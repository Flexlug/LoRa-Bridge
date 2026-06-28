"""Точка входа: загрузить конфиг, собрать граф объектов, запустить мост (§13)."""

from __future__ import annotations

import logging
import signal
import sys

import anyio
from envyaml import EnvYAML
from pydantic import ValidationError

from .config.errors import format_validation_error
from .config.schema import AppConfig
from .core.bridge import Bridge, NodeRuntime
from .core.journal import SqliteJournal
from .core.queue import QueueItem
from .core.status import StatusDispatcher
from .domain.models import BRIDGE_TRANSPORT_UID, ChannelRef, DeliveryStatus, Identity, Message
from .settings import Settings
from .wiring import build_lora_nodes, build_messengers, build_notice_sink, build_rooms

log = logging.getLogger(__name__)

NOTICE_SENDER = Identity(display_name="bridge", transport_uid=BRIDGE_TRANSPORT_UID)


async def recover(
    journal: SqliteJournal, nodes: dict[str, NodeRuntime], status: StatusDispatcher, messenger_ids: set[str]
) -> None:
    """Починка сирот после рестарта (§11.1): TRANSMITTING→UNKNOWN, PENDING→re-enqueue."""
    for e in await journal.recover():
        origin = ChannelRef(e.origin_transport, e.origin_chat)
        if e.status == DeliveryStatus.TRANSMITTING:
            await status.set(origin, e.origin_msg_id, DeliveryStatus.UNKNOWN)
            await journal.mark_terminal(e.msg_key, DeliveryStatus.UNKNOWN)
            continue
        node = nodes.get(e.target_node)
        if node is None:
            continue
        payload = Message(id=e.origin_msg_id, source=origin, sender=NOTICE_SENDER, text=e.payload)
        node.queue.put_nowait(
            QueueItem(
                source=origin,
                source_msg_id=e.origin_msg_id,
                target=ChannelRef(e.target_node, e.target_endpoint),
                payload=payload,
                original=payload,
                from_messenger=e.origin_transport in messenger_ids,
            )
        )
        await status.set(origin, e.origin_msg_id, DeliveryStatus.PENDING)


async def run(config: AppConfig, settings: Settings) -> None:
    # Мессенджеры строятся первыми: notice_sink нужен при сборке нод (per-node нотификатор)
    messengers = build_messengers(config)
    notice_sink = build_notice_sink(messengers.transports, NOTICE_SENDER)
    lora = build_lora_nodes(config, notice_sink)

    status = StatusDispatcher({**lora.transports, **messengers.transports})

    journal = SqliteJournal(settings.db_path)
    await journal.start()
    await recover(journal, lora.runtimes, status, set(messengers.transports))

    bridge = Bridge(
        nodes=lora.runtimes,
        messengers=messengers.bindings,
        rooms=build_rooms(config),
        status=status,
        journal=journal,
    )
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_signals, tg.cancel_scope)
            await bridge.run()
            tg.cancel_scope.cancel()  # bridge.run вернулся сам → снять watcher
    finally:
        # journal.stop() под shield: при graceful-выходе scope уже отменён сигналом,
        # без shield первый await сбросил бы запись журнала (§11.1 persist-before-act).
        with anyio.CancelScope(shield=True):
            await journal.stop()


async def _watch_signals(scope: anyio.CancelScope) -> None:
    """Перехват SIGINT/SIGTERM → graceful-отмена (Ctrl+C и `docker stop`, §13).

    Без установленного обработчика дефолтный SIGTERM убил бы процесс мимо `finally`
    (журнал/соединения недозакрыты), а SIGINT всплыл бы трейсбэком. По первому
    сигналу отменяем scope и выходим из receiver — обработчики снимаются, поэтому
    повторный Ctrl+C завершает процесс немедленно без трейсбэка (страховка на случай,
    если graceful-очистка подвиснет).
    """
    with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
        async for signum in signals:
            name = signal.Signals(signum).name
            log.info("получен %s — завершаюсь (повтор Ctrl+C для немедленного выхода)", name)
            scope.cancel()
            return


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s.%(msecs)03d %(levelname)s:%(name)s:%(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger().setLevel(settings.log_level)  # override если basicConfig не сработал
    try:
        config = AppConfig.model_validate(dict(EnvYAML(settings.config_path)))
    except ValidationError as exc:
        sys.stderr.write(format_validation_error(exc))
        sys.exit(2)
    try:
        anyio.run(run, config, settings)
    except KeyboardInterrupt:
        # Ctrl+C в окне до установки receiver (сборка графа, journal.start, recover)
        # приходит дефолтным KeyboardInterrupt — выходим кодом 130 без трейсбэка.
        log.warning("прервано на старте — выход")
        sys.exit(130)


if __name__ == "__main__":
    main()
