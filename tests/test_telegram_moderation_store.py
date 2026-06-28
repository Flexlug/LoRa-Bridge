# tests/test_telegram_moderation_store.py
import pytest
from lora_bridge.transports.telegram.moderation.roles import Role
from lora_bridge.transports.telegram.moderation.store import ModerationStore, UserSettings

@pytest.fixture
async def store() -> ModerationStore:
    s = ModerationStore(":memory:")
    await s.start()
    return s

async def test_default_role_is_user(store: ModerationStore) -> None:
    role = await store.get_role(owner_id=1, tg_id=999)
    assert role == Role.USER

async def test_owner_id_returns_owner(store: ModerationStore) -> None:
    role = await store.get_role(owner_id=42, tg_id=42)
    assert role == Role.OWNER

async def test_set_and_get_role(store: ModerationStore) -> None:
    await store.set_role(tg_id=10, role="admin", chat_id=100)
    assert await store.get_role(owner_id=1, tg_id=10) == Role.ADMIN

async def test_remove_role_reverts_to_user(store: ModerationStore) -> None:
    await store.set_role(tg_id=10, role="moderator", chat_id=100)
    await store.remove_role(tg_id=10)
    assert await store.get_role(owner_id=1, tg_id=10) == Role.USER

async def test_ban_and_is_disabled(store: ModerationStore) -> None:
    assert await store.is_disabled(tg_id=5) is False
    await store.ban_user(tg_id=5, banned_name="Vasya")
    assert await store.is_disabled(tg_id=5) is True

async def test_unban(store: ModerationStore) -> None:
    await store.ban_user(tg_id=5, banned_name=None)
    await store.unban_user(tg_id=5)
    assert await store.is_disabled(tg_id=5) is False

async def test_set_alias(store: ModerationStore) -> None:
    await store.set_alias(tg_id=7, alias="Вася")
    s = await store.get_user_settings(tg_id=7)
    assert s.alias == "Вася"

async def test_reset_alias(store: ModerationStore) -> None:
    await store.set_alias(tg_id=7, alias="Вася")
    await store.set_alias(tg_id=7, alias=None)
    s = await store.get_user_settings(tg_id=7)
    assert s.alias is None

async def test_toggle_transliter(store: ModerationStore) -> None:
    result = await store.toggle_transliter(tg_id=7)
    assert result is True
    result = await store.toggle_transliter(tg_id=7)
    assert result is False

async def test_get_banned_users(store: ModerationStore) -> None:
    await store.ban_user(tg_id=1, banned_name="Alice")
    await store.ban_user(tg_id=2, banned_name=None)
    bans = await store.get_banned_users()
    ids = [b[0] for b in bans]
    assert 1 in ids and 2 in ids

async def test_audit_log(store: ModerationStore) -> None:
    await store.log_action(ts=1000, actor_id=1, actor_name="Admin",
                           action="ban", target_id=2, target_name="User")
    count = await store.count_audit_entries()
    assert count == 1
    page = await store.get_audit_page(page=1, page_size=10)
    assert len(page) == 1
    assert page[0].action == "ban"
    assert page[0].actor_id == 1

async def test_audit_pagination(store: ModerationStore) -> None:
    for i in range(25):
        await store.log_action(ts=i, actor_id=1, actor_name="A", action="ban")
    assert await store.count_audit_entries() == 25
    p1 = await store.get_audit_page(page=1, page_size=10)
    p3 = await store.get_audit_page(page=3, page_size=10)
    assert len(p1) == 10
    assert len(p3) == 5

async def test_get_all_privileged(store: ModerationStore) -> None:
    await store.set_role(tg_id=10, role="admin", chat_id=100)
    await store.set_role(tg_id=20, role="moderator", chat_id=200)
    priv = await store.get_all_privileged()
    assert len(priv) == 2
    tg_ids = {p[0] for p in priv}
    assert {10, 20} == tg_ids
