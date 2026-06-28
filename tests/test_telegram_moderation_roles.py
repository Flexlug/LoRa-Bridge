import pytest

from lora_bridge.transports.telegram.moderation.roles import Role, can_grant, can_revoke


def test_role_ordering() -> None:
    assert Role.USER < Role.MODERATOR < Role.ADMIN < Role.OWNER


# can_grant и can_revoke имеют одинаковую семантику «строго выше цели» (actor > target),
# поэтому проверяем обе одним набором кейсов.
@pytest.mark.parametrize("check", [can_grant, can_revoke], ids=["grant", "revoke"])
@pytest.mark.parametrize(
    "actor,target,allowed",
    [
        (Role.ADMIN, Role.MODERATOR, True),    # строго ниже — можно
        (Role.ADMIN, Role.ADMIN, False),       # равный — нельзя
        (Role.ADMIN, Role.OWNER, False),       # выше актора — нельзя
        (Role.OWNER, Role.ADMIN, True),        # owner может всё, что ниже себя
        (Role.OWNER, Role.MODERATOR, True),
        (Role.MODERATOR, Role.ADMIN, False),   # ниже не трогает выше
        (Role.USER, Role.USER, False),         # user не может вообще
        (Role.USER, Role.MODERATOR, False),
    ],
)
def test_strictly_higher_role_required(check, actor: Role, target: Role, allowed: bool) -> None:
    assert check(actor, target) is allowed
