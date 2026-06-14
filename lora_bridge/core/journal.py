"""Журнал намерений (write-ahead) + recovery (§11.1).

Нужен НЕ для передоставки payload (это нарушило бы at-most-once, AD-5), а чтобы
знать per-message статус — починить «зависшую» реакцию после рестарта. SQLite:
in-process, ACID, ноль операционки. Persist-before-act: статус пишем ДО side-effect.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ..domain.models import DeliveryStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound_journal (
    msg_key       TEXT PRIMARY KEY,   -- transport_id + native id (корреляция со статусом)
    origin_chat   TEXT NOT NULL,      -- куда вернуть реакцию
    origin_msg_id TEXT NOT NULL,
    status        TEXT NOT NULL,      -- PENDING | TRANSMITTING | <terminal>
    enqueued_at   REAL NOT NULL,
    tx_started_at REAL,
    payload       TEXT NOT NULL       -- собранная строка [тип:ник]+текст
);
"""


class OutboundJournal(Protocol):
    """Интерфейс журнала; реализация — SQLite (см. ``SqliteJournal``)."""

    async def record_pending(
        self, msg_key: str, origin_chat: str, origin_msg_id: str, payload: str
    ) -> None: ...
    async def mark_transmitting(self, msg_key: str) -> None: ...   # ДО node.send()!
    async def mark_terminal(self, msg_key: str, status: DeliveryStatus) -> None: ...
    async def prune(self, msg_key: str) -> None: ...
    async def recover(self) -> list["JournalEntry"]: ...           # нетерминальные сироты на старте


class JournalEntry:
    """Запись журнала, возвращаемая ``recover`` (см. таблицу действий §11.1)."""

    msg_key: str
    origin_chat: str
    origin_msg_id: str
    status: DeliveryStatus
    enqueued_at: float
    tx_started_at: Optional[float]
    payload: str


class SqliteJournal:
    """SQLite-реализация ``OutboundJournal`` (aiosqlite).

    TODO(§11.1): открыть БД, применить SCHEMA, реализовать persist-before-act и
    recovery-сканирование нетерминальных записей (PENDING/TRANSMITTING/SENT).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def start(self) -> None:
        raise NotImplementedError("TODO(§11.1): connect + apply SCHEMA")

    async def record_pending(
        self, msg_key: str, origin_chat: str, origin_msg_id: str, payload: str
    ) -> None:
        raise NotImplementedError("TODO(§11.1)")

    async def mark_transmitting(self, msg_key: str) -> None:
        raise NotImplementedError("TODO(§11.1): пишем ДО node.send()")

    async def mark_terminal(self, msg_key: str, status: DeliveryStatus) -> None:
        raise NotImplementedError("TODO(§11.1)")

    async def prune(self, msg_key: str) -> None:
        raise NotImplementedError("TODO(§11.1)")

    async def recover(self) -> list[JournalEntry]:
        raise NotImplementedError("TODO(§11.1): сироты → идемпотентные действия")
