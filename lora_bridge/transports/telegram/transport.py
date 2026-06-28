"""Telegram-адаптер: порт ``Transport`` поверх ``aiogram``.

Тонкий оркестратор поверх одного бота: жизненный цикл (polling), нормализация
входящих в доменный ``Message`` и отправка. Кодирование канала (``chat`` /
``chat#topic``) живёт в ``channels``, статус-фидбэк реакциями — в ``reactions``.

Без живого токена адаптер не проверялся — места вызовов API помечены ``# verify``.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import AsyncIterator, Optional, TYPE_CHECKING

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message as TgMessage

from .commands import (
    ALL_COMMAND_METAS,
    build_command_router,
    command_menu,
    make_audit_callbacks,
    make_basic_commands,
    make_moderation_commands,
)
from .moderation.roles import Role
from .moderation.store import ModerationStore, UserSettings
from .moderation.transliterate import transliterate
from .reactions import ReactionFeedback
from ..hub import Hub
from ...domain.ports import Transport
from ...domain.models import (
    BRIDGE_TRANSPORT_UID,
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Identity,
    Message,
    RateSpec,
    RejectReason,
    SendResult,
    messenger_channel,
)

if TYPE_CHECKING:
    from ...config.schema import TelegramMessengerConfig

log = logging.getLogger(__name__)


def split_channel(channel: str) -> tuple[int, Optional[int]]:
    """``"chat"`` / ``"chat#topic"`` → ``(chat_id, thread_id|None)``. Инверсия ``messenger_channel``.

    Декод нужен только на send-стороне адаптера (chat_id/thread_id для ``bot.send_message``),
    поэтому живёт здесь, а не в (generic) домене рядом с ``messenger_channel``. Round-trip
    с энкодером закреплён guard-тестом ``tests/test_telegram_channels.py``.
    """
    if "#" in channel:
        chat, topic = channel.split("#", 1)
        return int(chat), int(topic)
    return int(channel), None


class TelegramTransport(Transport):
    capabilities = Capabilities(
        max_text_bytes=4096,
        egress_rate=RateSpec(20, 60, burst=20),
        supports_status_feedback=True,
        emits_tx_done=False,
    )

    _poll_task: asyncio.Task[None] | None = None

    def __init__(
        self,
        transport_id: str,
        config: "TelegramMessengerConfig",
        *,
        _store: Optional[ModerationStore] = None,
    ) -> None:
        self.id = transport_id
        self._hub = Hub()
        self._bot = Bot(config.token)
        self._dp = Dispatcher()
        self._store: Optional[ModerationStore] = None
        self._owner_id: int = 0
        # (tg_id, chat_id) — уже обновлённые scope; избегаем лишних API-вызовов
        self._cmd_scope_done: set[tuple[int, int]] = set()

        if config.commands is not None:
            owner_id = config.commands.owner_id
            self._owner_id = owner_id
            if _store is not None:
                self._store = _store
            else:
                from ...settings import Settings
                db_path = Settings.from_env().db_path
                self._store = ModerationStore(db_path)

            async def _on_role_changed(tg_id: int, chat_id: int) -> None:
                """Вызывается после /role grant|revoke — сразу обновляет меню в ЛС и группе."""
                if self._store is None:
                    return
                role = await self._store.get_role(self._owner_id, tg_id)
                self._cmd_scope_done = {p for p in self._cmd_scope_done if p[0] != tg_id}
                await self._set_user_commands(tg_id, role)
                if chat_id != tg_id:  # chat_id != tg_id означает групповой чат
                    await self._clear_user_group_commands(tg_id, chat_id)

            all_specs = (
                make_basic_commands(self._store, owner_id, ALL_COMMAND_METAS)
                + make_moderation_commands(
                    self._store, config.commands, on_role_changed=_on_role_changed
                )
            )
            callbacks = make_audit_callbacks(self._store)
            self._dp.include_router(
                build_command_router(
                    self.id, all_specs, self._store, owner_id, callbacks,
                    private_only=True,
                )
            )
        else:
            # только catchall — команды не утекают в pipeline даже без блока commands:
            self._dp.include_router(build_command_router(self.id, []))

        # Порядок включения = порядок диспетча: команды перехватываются ДО bridge-хэндлера,
        # поэтому транспорт-локальные команды не доходят до on_message → не текут в pipeline.
        bridge = Router(name=f"telegram-bridge:{self.id}")
        bridge.message.register(self.on_message, F.text)  # verify: фильтр текстовых
        self._dp.include_router(bridge)
        self._reactions = ReactionFeedback(self._bot)

    async def _set_user_commands(self, tg_id: int, role: Role) -> None:
        """Выставить меню команд в ЛС пользователя (chat_id ЛС = user_id)."""
        from aiogram.types import BotCommandScopeChat
        menu = command_menu(ALL_COMMAND_METAS, role)
        try:
            await self._bot.set_my_commands(menu, scope=BotCommandScopeChat(chat_id=tg_id))
            self._cmd_scope_done.add((tg_id, tg_id))
        except Exception:
            log.debug("Не удалось обновить меню команд user=%d", tg_id)

    async def _clear_user_group_commands(self, tg_id: int, group_chat_id: int) -> None:
        """Явно скрыть команды для конкретного пользователя в конкретной группе."""
        from aiogram.types import BotCommandScopeChatMember
        try:
            await self._bot.set_my_commands(
                [], scope=BotCommandScopeChatMember(chat_id=group_chat_id, user_id=tg_id)
            )
        except Exception:
            log.debug("Не удалось скрыть меню в группе user=%d chat=%d", tg_id, group_chat_id)

    async def start(self) -> None:
        me = await self._bot.get_me()  # verify: бот доступен (sanity)
        log.info("Telegram-транспорт '%s': бот @%s (id=%d) подключён", self.id, me.username, me.id)

        if self._store is not None:
            await self._store.start()
            from aiogram.types import BotCommandScopeAllGroupChats
            # глобальный дефолт — USER для всех (для ЛС)
            user_menu = command_menu(ALL_COMMAND_METAS, Role.USER)
            await self._bot.set_my_commands(user_menu)  # verify
            # в группах подсказки команд скрыты — все команды только в ЛС
            await self._bot.set_my_commands([], scope=BotCommandScopeAllGroupChats())
            # per-user меню только в ЛС; в группах — явно пусто
            privileged = await self._store.get_all_privileged()
            for tg_id, role_str, last_chat_id in privileged:
                role = Role.ADMIN if role_str == "admin" else Role.MODERATOR
                await self._set_user_commands(tg_id, role)
                if last_chat_id is not None:
                    await self._clear_user_group_commands(tg_id, last_chat_id)

        self._poll_task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False)  # verify
        )

    async def stop(self) -> None:
        await self._dp.stop_polling()  # verify
        if self._poll_task is not None:
            self._poll_task.cancel()
        if self._store is not None:
            await self._store.stop()
        await self._bot.close()  # verify

    async def on_message(self, message: TgMessage) -> None:
        user_id = message.from_user.id if message.from_user else None
        if user_id is not None and self._store is not None:
            if await self._store.is_disabled(user_id):
                await self._reactions.report_disabled(message)
                return
            settings: Optional[UserSettings] = await self._store.get_user_settings(user_id)
        else:
            settings = None
        await self._hub.publish(self.normalize(message, settings))

    def normalize(
        self, message: TgMessage, settings: Optional[UserSettings] = None
    ) -> Message:
        thread = message.message_thread_id
        chat_id = str(message.chat.id)
        user = message.from_user
        display_name = user.full_name if user else "unknown"
        text = message.text or ""

        if settings is not None:
            if settings.alias:
                display_name = settings.alias
            if settings.transliter:
                text = transliterate(text)
                display_name = transliterate(display_name)

        return Message(
            id=str(message.message_id),
            source=ChannelRef(self.id, messenger_channel(chat_id, str(thread) if thread else None)),
            sender=Identity(
                display_name=display_name,
                transport_uid=str(user.id) if user else "0",
            ),
            text=text,
        )

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        chat_id, thread_id = split_channel(target.channel)
        # Единое правило: bridge-уведомления — как есть; всё с известным именем
        # (TG-юзер ИЛИ резолвнутый автор room-server поста) — жирным префиксом;
        # каналы (display_name пуст, ник уже в тексте) — passthrough.
        if msg.sender.transport_uid == BRIDGE_TRANSPORT_UID or not msg.sender.display_name:
            # markup не добавляем → шлём plain без parse_mode, иначе спецсимволы
            # тела (<, &, незакрытый тег) из эфира уронили бы HTML-парсер Telegram.
            text = msg.text
            parse_mode = None
        else:
            # имя/тело экранируем — иначе те же спецсимволы ломают отправку.
            text = f"<b>{html.escape(msg.sender.display_name)}</b>: {html.escape(msg.text)}"
            parse_mode = "HTML"
        try:
            await self._bot.send_message(  # verify
                chat_id, text, message_thread_id=thread_id, parse_mode=parse_mode
            )
            return SendResult.success()
        except Exception as exc:  # noqa: BLE001
            log.exception("Telegram send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        chat_id, _ = split_channel(origin.channel)
        await self._reactions.report(chat_id, message_id, status, reason)
