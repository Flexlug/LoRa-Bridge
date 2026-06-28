"""Журнал намерений (write-ahead) + recovery (§11.1).

Нужен НЕ для передоставки payload (это нарушило бы at-most-once, AD-5), а чтобы
знать per-message статус — починить «зависшую» реакцию после рестарта. SQLite:
in-process, ACID, ноль операционки. Persist-before-act: статус пишем ДО side-effect.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import aiosqlite

from ..domain.models import DeliveryStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound_journal (
    msg_key          TEXT PRIMARY KEY,   -- source_transport + native id (корреляция со статусом)
    origin_transport TEXT NOT NULL,      -- источник (для re-enqueue/статуса)
    origin_chat      TEXT NOT NULL,      -- куда вернуть реакцию (ChannelRef.channel источника)
    origin_msg_id    TEXT NOT NULL,
    target_node      TEXT NOT NULL,      -- целевая LoRa-нода (для re-enqueue)
    target_endpoint  TEXT NOT NULL,
    status           TEXT NOT NULL,      -- PENDING | TRANSMITTING | <terminal>
    enqueued_at      REAL NOT NULL,
    tx_started_at    REAL,
    payload          TEXT NOT NULL       -- собранная строка [тип:ник]+текст
);
"""


@dataclass(frozen=True)
class JournalEntry:
    msg_key: str
    origin_transport: str
    origin_chat: str
    origin_msg_id: str
    target_node: str
    target_endpoint: str
    status: DeliveryStatus
    enqueued_at: float
    tx_started_at: Optional[float]
    payload: str


class OutboundJournal(Protocol):
    async def record_pending(self, entry: JournalEntry) -> None: ...
    async def mark_transmitting(self, msg_key: str) -> None: ...  # ДО node.send()!
    async def mark_terminal(self, msg_key: str, status: DeliveryStatus) -> None: ...
    async def prune(self, msg_key: str) -> None: ...
    async def recover(self) -> list[JournalEntry]: ...  # нетерминальные сироты


class SqliteJournal:
    """SQLite-реализация ``OutboundJournal`` (aiosqlite)."""

    def __init__(self, db_path: str, *, _clock: Callable[[], float] = time.monotonic) -> None:
        self._db_path = db_path
        self._clock = _clock
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def stop(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("journal не запущен: вызови start()")
        return self._db

    async def record_pending(self, entry: JournalEntry) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO outbound_journal "
            "(msg_key, origin_transport, origin_chat, origin_msg_id, target_node, "
            " target_endpoint, status, enqueued_at, tx_started_at, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                entry.msg_key,
                entry.origin_transport,
                entry.origin_chat,
                entry.origin_msg_id,
                entry.target_node,
                entry.target_endpoint,
                DeliveryStatus.PENDING.value,
                entry.enqueued_at,
                None,
                entry.payload,
            ),
        )
        await self.conn.commit()

    async def mark_transmitting(self, msg_key: str) -> None:
        await self.conn.execute(
            "UPDATE outbound_journal SET status=?, tx_started_at=? WHERE msg_key=?",
            (DeliveryStatus.TRANSMITTING.value, self._clock(), msg_key),
        )
        await self.conn.commit()

    async def mark_terminal(self, msg_key: str, status: DeliveryStatus) -> None:
        await self.conn.execute(
            "UPDATE outbound_journal SET status=? WHERE msg_key=?",
            (status.value, msg_key),
        )
        await self.conn.commit()

    async def prune(self, msg_key: str) -> None:
        await self.conn.execute("DELETE FROM outbound_journal WHERE msg_key=?", (msg_key,))
        await self.conn.commit()

    async def recover(self) -> list[JournalEntry]:
        cur = await self.conn.execute(
            "SELECT msg_key, origin_transport, origin_chat, origin_msg_id, target_node, "
            "target_endpoint, status, enqueued_at, tx_started_at, payload "
            "FROM outbound_journal WHERE status IN (?, ?)",
            (DeliveryStatus.PENDING.value, DeliveryStatus.TRANSMITTING.value),
        )
        rows = await cur.fetchall()
        return [
            JournalEntry(
                msg_key=r[0],
                origin_transport=r[1],
                origin_chat=r[2],
                origin_msg_id=r[3],
                target_node=r[4],
                target_endpoint=r[5],
                status=DeliveryStatus(r[6]),
                enqueued_at=r[7],
                tx_started_at=r[8],
                payload=r[9],
            )
            for r in rows
        ]
