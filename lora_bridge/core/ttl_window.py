from __future__ import annotations

import time
from collections import OrderedDict


class TtlWindow:
    """Скользящее TTL-окно: хранит строковые ключи, автоматически вытесняет устаревшие.

    Разделяемый примитив для TtlDedup и LoopGuard — концепции разные, механика одна.
    """

    def __init__(self, ttl_seconds: float, *, _clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = _clock
        self._store: OrderedDict[str, float] = OrderedDict()

    def add(self, key: str) -> None:
        now = self._clock()
        self.evict(now)
        self._store[key] = now
        self._store.move_to_end(key)

    def add_if_absent(self, key: str) -> bool:
        """Добавить ключ если его нет. Возвращает True — ключ новый, False — уже был."""
        now = self._clock()
        self.evict(now)
        if key in self._store:
            self._store.move_to_end(key)
            return False
        self._store[key] = now
        return True

    def __contains__(self, key: str) -> bool:
        now = self._clock()
        self.evict(now)
        return key in self._store

    def evict(self, now: float) -> None:
        deadline = now - self._ttl
        while self._store:
            ts = next(iter(self._store.values()))
            if ts >= deadline:
                break
            self._store.popitem(last=False)
