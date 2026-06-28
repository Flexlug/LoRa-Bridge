from __future__ import annotations
from enum import IntEnum


class Role(IntEnum):
    USER = 0
    MODERATOR = 1
    ADMIN = 2
    OWNER = 3


def can_grant(actor: Role, target: Role) -> bool:
    return actor > target


def can_revoke(actor: Role, target: Role) -> bool:
    return actor > target
