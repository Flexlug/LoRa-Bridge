"""Тесты команд модерации: парсинг аргументов и проверка прав."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from collections.abc import AsyncGenerator

import pytest
from aiogram.types import Chat, Message, User

from lora_bridge.transports.telegram.commands.moderation import (
    MODERATION_COMMAND_METAS,
    make_audit_callbacks,
    make_moderation_commands,
    resolve_target,
)
from lora_bridge.transports.telegram.moderation.roles import Role
from lora_bridge.transports.telegram.moderation.store import ModerationStore


@pytest.fixture
async def store() -> AsyncGenerator[ModerationStore, None]:
    s = ModerationStore(":memory:")
    await s.start()
    yield s
    await s.stop()


def _msg(text: str, user_id: int = 10, reply_user_id: int | None = None) -> Message:
    reply = None
    if reply_user_id is not None:
        reply = Message(
            message_id=5,
            date=dt.datetime(2024, 1, 1),
            chat=Chat(id=1, type="group"),
            from_user=User(id=reply_user_id, is_bot=False, first_name="Target"),
            text="some text",
        )
    return Message(
        message_id=10,
        date=dt.datetime(2024, 1, 1),
        chat=Chat(id=1, type="group"),
        from_user=User(id=user_id, is_bot=False, first_name="Actor"),
        text=text,
        reply_to_message=reply,
    )


def test_moderation_command_metas_complete() -> None:
    names = {m.name for m in MODERATION_COMMAND_METAS}
    assert {"ban", "unban", "banlist", "set_alias", "set_transliter", "role", "audit"} == names


def test_ban_requires_moderator_role() -> None:
    specs = {s.name: s for s in make_moderation_commands(
        ModerationStore(":memory:"), SimpleNamespace(owner_id=1, alias_max_chars=16)
    )}
    assert specs["ban"].min_role == Role.MODERATOR


def test_role_requires_admin_role() -> None:
    specs = {s.name: s for s in make_moderation_commands(
        ModerationStore(":memory:"), SimpleNamespace(owner_id=1, alias_max_chars=16)
    )}
    assert specs["role"].min_role == Role.ADMIN


async def test_resolve_target_from_reply() -> None:
    msg = _msg("/ban", reply_user_id=99)
    result = await resolve_target(msg)
    assert result is not None
    assert result[0] == 99
    assert result[1] == "Target"


async def test_resolve_target_from_numeric_arg() -> None:
    msg = _msg("/ban 12345")
    result = await resolve_target(msg)
    assert result is not None
    assert result[0] == 12345
    assert result[1] is None


async def test_resolve_target_none_when_no_arg() -> None:
    msg = _msg("/ban")
    result = await resolve_target(msg)
    assert result is None


async def test_ban_bans_user(store: ModerationStore) -> None:
    cmds = {s.name: s for s in make_moderation_commands(
        store, SimpleNamespace(owner_id=1, alias_max_chars=16)
    )}
    msg = _msg("/ban 555", user_id=1)
    mock_answer = AsyncMock()
    with patch.object(type(msg), "answer", mock_answer):
        await cmds["ban"].handler(msg)
    assert await store.is_disabled(555) is True


async def test_unban_unbans_user(store: ModerationStore) -> None:
    await store.ban_user(555, "X")
    cmds = {s.name: s for s in make_moderation_commands(
        store, SimpleNamespace(owner_id=1, alias_max_chars=16)
    )}
    msg = _msg("/unban 555", user_id=1)
    mock_answer = AsyncMock()
    with patch.object(type(msg), "answer", mock_answer):
        await cmds["unban"].handler(msg)
    assert await store.is_disabled(555) is False


async def test_set_alias_enforces_length(store: ModerationStore) -> None:
    cmds = {s.name: s for s in make_moderation_commands(
        store, SimpleNamespace(owner_id=1, alias_max_chars=5)
    )}
    msg = _msg("/set_alias TooLongAlias", user_id=10)
    mock_answer = AsyncMock()
    with patch.object(type(msg), "answer", mock_answer):
        await cmds["set_alias"].handler(msg)
    s = await store.get_user_settings(10)
    assert s.alias is None
    mock_answer.assert_awaited_once()
    assert "5" in mock_answer.await_args.args[0]


async def test_audit_noop_callback_answers_immediately(store: ModerationStore) -> None:
    """Нажатие на нефункциональные кнопки /audit не должно оставлять лоадер."""
    callbacks = {cb.prefix: cb for cb in make_audit_callbacks(store)}
    cb = callbacks["audit:"]
    answered = False

    async def fake_answer(text: str = "") -> None:
        nonlocal answered
        answered = True

    from unittest.mock import MagicMock
    query = MagicMock()
    query.data = "audit:noop"
    query.answer = fake_answer
    query.from_user = None
    await cb.handler(query)
    assert answered, "query.answer() не вызван — лоадер будет висеть вечно"


async def test_set_alias_sets_for_self(store: ModerationStore) -> None:
    cmds = {s.name: s for s in make_moderation_commands(
        store, SimpleNamespace(owner_id=1, alias_max_chars=16)
    )}
    msg = _msg("/set_alias Вася", user_id=10)
    mock_answer = AsyncMock()
    with patch.object(type(msg), "answer", mock_answer):
        await cmds["set_alias"].handler(msg)
    s = await store.get_user_settings(10)
    assert s.alias == "Вася"
