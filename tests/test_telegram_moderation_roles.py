from lora_bridge.transports.telegram.moderation.roles import Role, can_grant, can_revoke


def test_role_ordering() -> None:
    assert Role.USER < Role.MODERATOR < Role.ADMIN < Role.OWNER


def test_can_grant_strictly_below() -> None:
    assert can_grant(Role.ADMIN, Role.MODERATOR) is True
    assert can_grant(Role.ADMIN, Role.ADMIN) is False
    assert can_grant(Role.ADMIN, Role.OWNER) is False


def test_owner_can_grant_all() -> None:
    assert can_grant(Role.OWNER, Role.ADMIN) is True
    assert can_grant(Role.OWNER, Role.MODERATOR) is True


def test_user_cannot_grant() -> None:
    assert can_grant(Role.USER, Role.USER) is False


def test_can_revoke_strictly_below() -> None:
    assert can_revoke(Role.ADMIN, Role.MODERATOR) is True
    assert can_revoke(Role.ADMIN, Role.ADMIN) is False
    assert can_revoke(Role.MODERATOR, Role.ADMIN) is False


def test_can_revoke_self_always_false() -> None:
    assert can_revoke(Role.OWNER, Role.OWNER) is False
    assert can_revoke(Role.ADMIN, Role.ADMIN) is False
