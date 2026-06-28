"""Команды модерации — фабрика с замыканием над store и cfg."""
from __future__ import annotations

import html
import math
import time
from typing import Optional, TYPE_CHECKING

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message as TgMessage,
)

from .framework import CallbackSpec, CommandMeta, CommandSpec
from ..moderation.roles import Role

if TYPE_CHECKING:
    from ..moderation.store import ModerationStore

# Статические метаданные без хендлеров — для документации и /help.
MODERATION_COMMAND_METAS: list[CommandMeta] = [
    CommandMeta("set_alias",     "задать себе alias (или другому — для мод+)",  Role.USER),
    CommandMeta("set_transliter", "включить/выключить транслитерацию",          Role.USER),
    CommandMeta("ban",           "запретить пользователю бриджинг TG→LoRa",    Role.MODERATOR),
    CommandMeta("unban",         "снять бан",                                   Role.MODERATOR),
    CommandMeta("banlist",       "список забаненных пользователей",             Role.MODERATOR),
    CommandMeta("audit",         "журнал действий модерации",                  Role.MODERATOR),
    CommandMeta("role",          "управление ролями (grant/revoke)",            Role.ADMIN),
]

_PAGE_SIZE = 10


async def resolve_target(message: TgMessage) -> Optional[tuple[int, Optional[str]]]:
    """Reply или числовой аргумент → (tg_id, display_name|None). None = неопределимо."""
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        return u.id, u.full_name
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip().split()[0]
    if message.entities:
        for e in message.entities:
            if e.type == "text_mention" and e.user:
                return e.user.id, e.user.full_name
    if arg.lstrip("-").isdigit():
        return int(arg), None
    return None


def _mention(tg_id: int, name: Optional[str]) -> str:
    label = html.escape(name) if name else str(tg_id)
    return f'<a href="tg://user?id={tg_id}">{label}</a>'


async def _audit_text_and_kb(
    page: int, store: "ModerationStore"
) -> tuple[str, InlineKeyboardMarkup]:
    import datetime as _dt
    total = await store.count_audit_entries()
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = max(1, min(page, total_pages))
    entries = await store.get_audit_page(page=page, page_size=_PAGE_SIZE)

    lines = []
    for e in entries:
        ts_str = _dt.datetime.utcfromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M")
        actor = _mention(e.actor_id, e.actor_name)
        target = _mention(e.target_id, e.target_name) if e.target_id else ""
        detail = f"  [{html.escape(e.detail)}]" if e.detail else ""
        arrow = f"  →  {target}" if target else ""
        lines.append(f"{ts_str}  {actor}  {html.escape(e.action)}{arrow}{detail}")

    text = f"Журнал действий (стр. {page}/{total_pages}):\n\n" + "\n".join(lines)

    prev_btn = InlineKeyboardButton(
        text="←" if page > 1 else " ",
        callback_data=f"audit:page:{page - 1}" if page > 1 else "audit:noop",
    )
    info_btn = InlineKeyboardButton(text=f"{page} / {total_pages}", callback_data="audit:noop")
    next_btn = InlineKeyboardButton(
        text="→" if page < total_pages else " ",
        callback_data=f"audit:page:{page + 1}" if page < total_pages else "audit:noop",
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[prev_btn, info_btn, next_btn]])
    return text, kb


async def _send_audit_page_edit(query: CallbackQuery, page: int, store: "ModerationStore") -> None:
    text, kb = await _audit_text_and_kb(page, store)
    msg = query.message
    if isinstance(msg, TgMessage):
        await msg.edit_text(text, parse_mode="HTML", reply_markup=kb)


def make_moderation_commands(
    store: "ModerationStore",
    cfg: object,
) -> list[CommandSpec]:
    """Фабрика команд модерации с замыканием над store и cfg."""
    owner_id: int = getattr(cfg, "owner_id", 0)
    alias_max: int = getattr(cfg, "alias_max_chars", 16)

    async def ban(message: TgMessage) -> None:
        target = await resolve_target(message)
        if target is None:
            await message.answer(
                "Укажите цель: ответьте на сообщение или передайте числовой ID."
            )
            return
        tg_id, display_name = target
        actor = message.from_user
        actor_id = actor.id if actor else 0
        actor_name = actor.full_name if actor else None
        await store.ban_user(tg_id, display_name)
        await store.log_action(
            ts=int(time.time()), actor_id=actor_id, actor_name=actor_name,
            action="ban", target_id=tg_id, target_name=display_name,
        )
        await message.answer(
            f"Пользователь {_mention(tg_id, display_name)} забанен.", parse_mode="HTML"
        )

    async def unban(message: TgMessage) -> None:
        target = await resolve_target(message)
        if target is None:
            await message.answer(
                "Укажите цель: ответьте на сообщение или передайте числовой ID."
            )
            return
        tg_id, display_name = target
        actor = message.from_user
        actor_id = actor.id if actor else 0
        actor_name = actor.full_name if actor else None
        await store.unban_user(tg_id)
        await store.log_action(
            ts=int(time.time()), actor_id=actor_id, actor_name=actor_name,
            action="unban", target_id=tg_id, target_name=display_name,
        )
        await message.answer(f"Бан снят: {_mention(tg_id, display_name)}.", parse_mode="HTML")

    async def banlist(message: TgMessage) -> None:
        bans = await store.get_banned_users()
        if not bans:
            await message.answer("Список банов пуст.")
            return
        lines = []
        for tg_id, banned_name, alias in bans:
            mention = _mention(tg_id, banned_name)
            suffix = f" (alias: {html.escape(alias)})" if alias else ""
            lines.append(f"• {mention}{suffix}")
        await message.answer(
            "Забаненные пользователи:\n" + "\n".join(lines), parse_mode="HTML"
        )

    async def set_alias(message: TgMessage) -> None:
        text = message.text or ""
        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        actor = message.from_user
        actor_id = actor.id if actor else 0

        target_id: int = actor_id
        target_name: Optional[str] = None
        new_alias: Optional[str] = None

        if args:
            if message.reply_to_message and message.reply_to_message.from_user:
                u = message.reply_to_message.from_user
                target_id, target_name = u.id, u.full_name
                new_alias = args or None
            else:
                arg_parts = args.split(maxsplit=1)
                first = arg_parts[0]
                is_id = first.lstrip("-").isdigit()
                is_mention = bool(message.entities and any(
                    e.type == "text_mention" and e.user for e in (message.entities or [])
                ))
                if is_id:
                    target_id = int(first)
                    new_alias = arg_parts[1] if len(arg_parts) > 1 else None
                elif is_mention:
                    for e in (message.entities or []):
                        if e.type == "text_mention" and e.user:
                            target_id = e.user.id
                            target_name = e.user.full_name
                    new_alias = arg_parts[1] if len(arg_parts) > 1 else args
                else:
                    new_alias = args

        if target_id != actor_id:
            role = await store.get_role(owner_id, actor_id)
            if role < Role.MODERATOR:
                await message.answer(
                    "Недостаточно прав для изменения alias другому пользователю."
                )
                return

        if new_alias and len(new_alias) > alias_max:
            await message.answer(f"Alias слишком длинный: максимум {alias_max} символов.")
            return

        await store.set_alias(target_id, new_alias)
        actor_name = actor.full_name if actor else None
        detail = f"alias: {new_alias}" if new_alias else "alias: сброшен"
        await store.log_action(
            ts=int(time.time()), actor_id=actor_id, actor_name=actor_name,
            action="set_alias", target_id=target_id, target_name=target_name, detail=detail,
        )
        if new_alias:
            await message.answer(f"Alias установлен: {html.escape(new_alias)}")
        else:
            await message.answer("Alias сброшен.")

    async def set_transliter(message: TgMessage) -> None:
        text = message.text or ""
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        actor = message.from_user
        actor_id = actor.id if actor else 0

        target_id = actor_id
        if arg:
            target = await resolve_target(message)
            if target is not None:
                target_id = target[0]
                if target_id != actor_id:
                    role = await store.get_role(owner_id, actor_id)
                    if role < Role.MODERATOR:
                        await message.answer("Недостаточно прав.")
                        return

        new_val = await store.toggle_transliter(target_id)
        state = "включена" if new_val else "выключена"
        await message.answer(f"Транслитерация {state}.")

    async def role_cmd(message: TgMessage) -> None:
        text = message.text or ""
        parts = text.split()
        if len(parts) < 4:
            await message.answer("Использование: /role grant|revoke admin|moderator <id>")
            return
        action_str, role_str, target_arg = parts[1], parts[2], parts[3]
        if action_str not in ("grant", "revoke"):
            await message.answer("Действие должно быть grant или revoke.")
            return
        if role_str not in ("admin", "moderator"):
            await message.answer("Роль должна быть admin или moderator.")
            return
        if not target_arg.lstrip("-").isdigit():
            await message.answer("Укажите числовой Telegram ID.")
            return
        target_id = int(target_arg)
        actor = message.from_user
        actor_id = actor.id if actor else 0
        actor_name = actor.full_name if actor else None

        actor_role = await store.get_role(owner_id, actor_id)
        target_role_map = {"admin": Role.ADMIN, "moderator": Role.MODERATOR}
        target_role = target_role_map[role_str]

        from ..moderation.roles import can_grant, can_revoke
        if action_str == "grant":
            if not can_grant(actor_role, target_role):
                await message.answer("Недостаточно прав для выдачи этой роли.")
                return
            await store.set_role(target_id, role_str, chat_id=message.chat.id)
        else:
            current_target_role = await store.get_role(owner_id, target_id)
            if not can_revoke(actor_role, current_target_role):
                await message.answer("Недостаточно прав для отзыва этой роли.")
                return
            await store.remove_role(target_id)

        await store.log_action(
            ts=int(time.time()), actor_id=actor_id, actor_name=actor_name,
            action=action_str, target_id=target_id, detail=f"role: {role_str}",
        )
        verb = "выдана" if action_str == "grant" else "отозвана"
        await message.answer(
            f"Роль {role_str} {verb} для {_mention(target_id, None)}.", parse_mode="HTML"
        )

    async def audit(message: TgMessage) -> None:
        text, kb = await _audit_text_and_kb(page=1, store=store)
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    return [
        CommandSpec("set_alias",     "задать себе alias (или другому — для мод+)",  Role.USER,      set_alias),
        CommandSpec("set_transliter", "включить/выключить транслитерацию",          Role.USER,      set_transliter),
        CommandSpec("ban",           "запретить пользователю бриджинг TG→LoRa",    Role.MODERATOR, ban),
        CommandSpec("unban",         "снять бан",                                   Role.MODERATOR, unban),
        CommandSpec("banlist",       "список забаненных пользователей",             Role.MODERATOR, banlist),
        CommandSpec("audit",         "журнал действий модерации",                  Role.MODERATOR, audit),
        CommandSpec("role",          "управление ролями (grant/revoke)",            Role.ADMIN,     role_cmd),
    ]


def make_audit_callbacks(store: "ModerationStore") -> list[CallbackSpec]:
    """Фабрика CallbackSpec для пагинации /audit."""
    async def audit_page(query: CallbackQuery) -> None:
        data = query.data or ""
        if data == "audit:noop":
            await query.answer()
            return
        try:
            page = int(data.split(":")[-1])
        except ValueError:
            await query.answer()
            return
        await _send_audit_page_edit(query, page, store)
        await query.answer()

    return [
        CallbackSpec(prefix="audit:page:", handler=audit_page, min_role=Role.MODERATOR),
    ]
