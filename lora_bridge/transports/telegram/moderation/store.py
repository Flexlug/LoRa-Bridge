from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiosqlite

from .roles import Role

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    tg_id        INTEGER PRIMARY KEY,
    role         TEXT NOT NULL,
    last_chat_id INTEGER
);
CREATE TABLE IF NOT EXISTS user_settings (
    tg_id       INTEGER PRIMARY KEY,
    alias       TEXT,
    transliter  INTEGER DEFAULT 0,
    disabled    INTEGER DEFAULT 0,
    banned_name TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    actor_id    INTEGER NOT NULL,
    actor_name  TEXT,
    action      TEXT NOT NULL,
    target_id   INTEGER,
    target_name TEXT,
    detail      TEXT
);
"""


@dataclass(frozen=True)
class UserSettings:
    alias: Optional[str] = None
    transliter: bool = False
    disabled: bool = False
    banned_name: Optional[str] = None


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: int
    actor_id: int
    actor_name: Optional[str]
    action: str
    target_id: Optional[int]
    target_name: Optional[str]
    detail: Optional[str]


class ModerationStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def stop(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ModerationStore не запущен: вызови start()")
        return self._db

    # --- роли ---

    async def get_role(self, owner_id: int, tg_id: int) -> Role:
        if tg_id == owner_id:
            return Role.OWNER
        cur = await self._conn.execute("SELECT role FROM roles WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        if row is None:
            return Role.USER
        return Role.ADMIN if row[0] == "admin" else Role.MODERATOR

    async def set_role(self, tg_id: int, role: str, chat_id: Optional[int] = None) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO roles (tg_id, role, last_chat_id) VALUES (?,?,?)",
            (tg_id, role, chat_id),
        )
        await self._conn.commit()

    async def remove_role(self, tg_id: int) -> None:
        await self._conn.execute("DELETE FROM roles WHERE tg_id=?", (tg_id,))
        await self._conn.commit()

    async def get_all_privileged(self) -> list[tuple[int, str, Optional[int]]]:
        cur = await self._conn.execute("SELECT tg_id, role, last_chat_id FROM roles")
        rows = await cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # --- user settings ---

    async def is_disabled(self, tg_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT disabled FROM user_settings WHERE tg_id=?", (tg_id,)
        )
        row = await cur.fetchone()
        return bool(row[0]) if row else False

    async def get_user_settings(self, tg_id: int) -> UserSettings:
        cur = await self._conn.execute(
            "SELECT alias, transliter, disabled, banned_name FROM user_settings WHERE tg_id=?",
            (tg_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return UserSettings()
        return UserSettings(
            alias=row[0],
            transliter=bool(row[1]),
            disabled=bool(row[2]),
            banned_name=row[3],
        )

    async def ban_user(self, tg_id: int, banned_name: Optional[str]) -> None:
        await self._conn.execute(
            "INSERT INTO user_settings (tg_id, disabled, banned_name) VALUES (?,1,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET disabled=1, banned_name=excluded.banned_name",
            (tg_id, banned_name),
        )
        await self._conn.commit()

    async def unban_user(self, tg_id: int) -> None:
        await self._conn.execute(
            "UPDATE user_settings SET disabled=0 WHERE tg_id=?", (tg_id,)
        )
        await self._conn.commit()

    async def get_banned_users(self) -> list[tuple[int, Optional[str], Optional[str]]]:
        cur = await self._conn.execute(
            "SELECT tg_id, banned_name, alias FROM user_settings WHERE disabled=1"
        )
        rows = await cur.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    async def set_alias(self, tg_id: int, alias: Optional[str]) -> None:
        await self._conn.execute(
            "INSERT INTO user_settings (tg_id, alias) VALUES (?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET alias=excluded.alias",
            (tg_id, alias),
        )
        await self._conn.commit()

    async def toggle_transliter(self, tg_id: int) -> bool:
        cur = await self._conn.execute(
            "SELECT transliter FROM user_settings WHERE tg_id=?", (tg_id,)
        )
        row = await cur.fetchone()
        new_val = 0 if (row and row[0]) else 1
        await self._conn.execute(
            "INSERT INTO user_settings (tg_id, transliter) VALUES (?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET transliter=excluded.transliter",
            (tg_id, new_val),
        )
        await self._conn.commit()
        return bool(new_val)

    # --- audit ---

    async def log_action(
        self,
        ts: int,
        actor_id: int,
        actor_name: Optional[str],
        action: str,
        target_id: Optional[int] = None,
        target_name: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO audit_log "
            "(ts, actor_id, actor_name, action, target_id, target_name, detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, actor_id, actor_name, action, target_id, target_name, detail),
        )
        await self._conn.commit()

    async def count_audit_entries(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) FROM audit_log")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_audit_page(self, page: int, page_size: int = 10) -> list[AuditEntry]:
        offset = (page - 1) * page_size
        cur = await self._conn.execute(
            "SELECT id, ts, actor_id, actor_name, action, target_id, target_name, detail "
            "FROM audit_log ORDER BY ts DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        )
        rows = await cur.fetchall()
        return [
            AuditEntry(
                id=r[0], ts=r[1], actor_id=r[2], actor_name=r[3],
                action=r[4], target_id=r[5], target_name=r[6], detail=r[7],
            )
            for r in rows
        ]
