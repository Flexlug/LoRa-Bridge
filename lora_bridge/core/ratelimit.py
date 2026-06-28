"""Token-bucket для egress-лимитера ① (§7). Наш единственный «добрый» предел."""

from __future__ import annotations

import time
from typing import Callable

from ..domain.models import RateSpec


class TokenBucket:
    """Классический token-bucket: ``capacity`` токенов, долив ``capacity/window`` в сек."""

    def __init__(self, spec: RateSpec, *, _clock: Callable[[], float] = time.monotonic) -> None:
        self._capacity = float(max(spec.burst, spec.msgs_per_window))
        self._refill_per_sec = spec.msgs_per_window / spec.window_seconds
        self._tokens = self._capacity
        self._clock = _clock
        self._last = _clock()

    def try_consume(self, n: float = 1.0) -> bool:
        self.top_up()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def top_up(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_sec)
            self._last = now
