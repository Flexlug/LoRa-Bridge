"""Тесты реконнекта MeshCoreTransport (M4).

Логика переподключения полностью изолирована от meshcore_py — используем
минимальный stub вместо реального железа.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anyio

from lora_bridge.transports.meshcore.transport import MeshCoreTransport, EV_DISCONNECTED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(node_id: str = "test") -> MagicMock:
    """Минимальная заглушка MeshCoreNode."""
    ep = MagicMock()
    ep.channel_name = "General"
    node = MagicMock()
    node.id = node_id
    node.endpoints = {}  # без эндпоинтов — упрощает резолв при старте
    node.connection = MagicMock()
    return node


def _make_transport(node_id: str = "test") -> MeshCoreTransport:
    return MeshCoreTransport(_make_node(node_id))


# ---------------------------------------------------------------------------
# _signal_disconnect / Event
# ---------------------------------------------------------------------------

async def test_signal_disconnect_sets_event():
    t = _make_transport()
    t._disconnect_ev = anyio.Event()
    t._signal_disconnect()
    assert t._disconnect_ev.is_set()


async def test_signal_disconnect_noop_before_start():
    """До start() _disconnect_ev is None — не должно падать."""
    t = _make_transport()
    assert t._disconnect_ev is None
    t._signal_disconnect()  # не должно выбросить


async def test_signal_disconnect_idempotent():
    t = _make_transport()
    t._disconnect_ev = anyio.Event()
    t._signal_disconnect()
    t._signal_disconnect()  # дважды — не должно падать


# ---------------------------------------------------------------------------
# send() при отсутствии соединения
# ---------------------------------------------------------------------------

async def test_send_returns_overloaded_when_mc_is_none():
    from lora_bridge.domain.models import ChannelRef, Message, Identity
    t = _make_transport()
    assert t._mc is None  # начальное состояние
    target = ChannelRef("test", "ep")
    msg = Message(
        id="1",
        source=target,
        sender=Identity(display_name="u", transport_uid="u"),
        text="hi",
    )
    result = await t.send(target, msg)
    assert result.busy and not result.ok


# ---------------------------------------------------------------------------
# on_event: DISCONNECTED → _signal_disconnect
# ---------------------------------------------------------------------------

async def test_on_event_disconnected_signals():
    t = _make_transport()
    t._disconnect_ev = anyio.Event()

    event = MagicMock()
    event.type = EV_DISCONNECTED
    await t.on_event(event)

    assert t._disconnect_ev.is_set()


async def test_on_event_disconnected_does_not_publish():
    """DISCONNECTED не должен уходить в hub как сообщение."""
    t = _make_transport()
    t._disconnect_ev = anyio.Event()

    published = []

    async def spy_publish(msg):
        published.append(msg)

    t._hub.publish = spy_publish

    event = MagicMock()
    event.type = EV_DISCONNECTED
    await t.on_event(event)

    assert published == []


# ---------------------------------------------------------------------------
# run(): переподключение после DISCONNECTED
# ---------------------------------------------------------------------------

async def test_run_calls_start_after_disconnect():
    """После сигнала DISCONNECTED run() должен вызвать start() заново."""
    t = _make_transport()

    start_calls = []
    teardown_calls = []

    async def fake_start():
        start_calls.append(1)
        t._disconnect_ev = anyio.Event()  # каждый start() создаёт новый event

    async def fake_teardown():
        teardown_calls.append(1)

    t.start = fake_start
    t._teardown = fake_teardown

    # Устанавливаем начальный event (имитируем что start() уже был вызван снаружи)
    t._disconnect_ev = anyio.Event()

    async def trigger_and_stop():
        await anyio.sleep(0.01)
        t._signal_disconnect()           # первый обрыв → run() переподключается
        await anyio.sleep(0.05)
        t._stopping = True               # просим run() остановиться
        t._signal_disconnect()           # будим его из _wait_for_disconnect

    async with anyio.create_task_group() as tg:
        tg.start_soon(t.run)
        tg.start_soon(trigger_and_stop)

    assert len(start_calls) >= 1
    assert len(teardown_calls) >= 1


async def test_run_stops_when_stopping_set():
    """stop() → run() должен выйти без повторного start()."""
    t = _make_transport()
    t._disconnect_ev = anyio.Event()

    start_calls = []

    async def fake_start():
        start_calls.append(1)
        t._disconnect_ev = anyio.Event()

    t.start = fake_start
    t._teardown = AsyncMock()

    async def stop_immediately():
        await anyio.sleep(0.01)
        t._stopping = True
        t._signal_disconnect()

    async with anyio.create_task_group() as tg:
        tg.start_soon(t.run)
        tg.start_soon(stop_immediately)

    assert start_calls == []  # остановились до реконнекта


async def test_run_backoff_doubles_on_failure():
    """При неудачном реконнекте задержка удваивается (нулевые задержки в тесте)."""
    t = _make_transport()
    t._disconnect_ev = anyio.Event()

    attempt = 0
    delays: list[float] = []

    async def fake_start():
        nonlocal attempt
        attempt += 1
        t._disconnect_ev = anyio.Event()
        if attempt <= 2:
            raise RuntimeError("connect failed")
        # 3-я попытка — успех; после start() ждём следующего disconnect,
        # но мы сразу останавливаемся
        t._stopping = True
        t._signal_disconnect()

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    t.start = fake_start
    t._teardown = AsyncMock()

    with patch("anyio.sleep", fake_sleep):
        t._signal_disconnect()  # инициируем первый обрыв
        await t.run()

    assert len(delays) >= 2, f"ожидали минимум 2 sleep-вызова, получили {delays}"
    assert delays[1] > delays[0], f"backoff не вырос: {delays}"


# ---------------------------------------------------------------------------
# ReactionDebouncer
# ---------------------------------------------------------------------------

async def test_debouncer_schedules_reaction_after_delay():
    """Реакция выставляется после задержки если SENT не пришёл."""
    from lora_bridge.transports.telegram.transport import ReactionDebouncer
    from aiogram.types import ReactionTypeEmoji
    from unittest.mock import AsyncMock

    calls: list = []
    bot = AsyncMock()
    bot.set_message_reaction = AsyncMock(side_effect=lambda *a, **kw: calls.append(kw.get("reaction", [])))

    debouncer = ReactionDebouncer(delay=0.05)
    debouncer.schedule((1, "42"), [ReactionTypeEmoji(emoji="👀")], bot)

    await anyio.sleep(0.1)
    assert len(calls) == 1
    assert calls[0][0].emoji == "👀"


async def test_debouncer_sent_before_debounce_no_api_calls():
    """SENT до истечения debounce: отменяет задачу БЕЗ API-вызова (реакция не была выставлена)."""
    from lora_bridge.transports.telegram.transport import ReactionDebouncer
    from aiogram.types import ReactionTypeEmoji
    from unittest.mock import AsyncMock

    calls: list = []
    bot = AsyncMock()
    bot.set_message_reaction = AsyncMock(side_effect=lambda *a, **kw: calls.append(kw.get("reaction", [])))

    debouncer = ReactionDebouncer(delay=0.2)
    debouncer.schedule((1, "42"), [ReactionTypeEmoji(emoji="👀")], bot)

    # SENT приходит раньше чем истечёт debounce
    await anyio.sleep(0.05)
    await debouncer.clear_now((1, "42"), bot)

    # Ждём дольше чем задержка — callback НЕ должен сработать
    await anyio.sleep(0.3)

    # Ноль вызовов: реакция не была выставлена → REACTION_EMPTY не возникает
    assert calls == []


async def test_debouncer_sent_after_debounce_clears():
    """SENT после срабатывания debounce: реакция была выставлена → делаем clear."""
    from lora_bridge.transports.telegram.transport import ReactionDebouncer
    from aiogram.types import ReactionTypeEmoji
    from unittest.mock import AsyncMock

    calls: list = []
    bot = AsyncMock()
    bot.set_message_reaction = AsyncMock(side_effect=lambda *a, **kw: calls.append(kw.get("reaction", [])))

    debouncer = ReactionDebouncer(delay=0.05)
    debouncer.schedule((1, "99"), [ReactionTypeEmoji(emoji="👀")], bot)

    # Ждём дольше debounce — 👀 выставится
    await anyio.sleep(0.1)
    assert len(calls) == 1 and calls[0][0].emoji == "👀"

    # Теперь SENT — должен вызвать clear (reaction=[])
    await debouncer.clear_now((1, "99"), bot)
    assert len(calls) == 2
    assert calls[1] == []


async def test_debouncer_race_sent_during_apply():
    """Generation-счётчик: SENT после пробуждения callback не даёт выставить реакцию."""
    from lora_bridge.transports.telegram.transport import ReactionDebouncer
    from aiogram.types import ReactionTypeEmoji
    from unittest.mock import AsyncMock

    order: list = []

    async def fake_react(*args, reaction, **kw):
        order.append(reaction)

    bot = AsyncMock()
    bot.set_message_reaction = AsyncMock(side_effect=fake_react)

    debouncer = ReactionDebouncer(delay=0.05)
    debouncer.schedule((1, "99"), [ReactionTypeEmoji(emoji="👀")], bot)

    # Имитируем SENT сразу после пробуждения (до API-вызова callback)
    await anyio.sleep(0.06)  # callback проснулся, но ещё не отправил
    await debouncer.clear_now((1, "99"), bot)

    await anyio.sleep(0.05)

    # Может быть 1 или 2 вызова в зависимости от timing,
    # но последний всегда должен быть clear ([])
    assert order[-1] == [], f"последний вызов должен быть clear, получили: {order}"
