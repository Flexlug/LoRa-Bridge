"""Горячий мультикаст RX-потока (§8).

Один физический коннект к узлу → N подписчиков (мессенджеры + метрики). Каждый
подписчик получает собственный bounded-буфер; медленный подписчик роняет старейшее
(B7), а не тормозит остальных.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import AsyncIterator, Iterator

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from ..domain.models import Message


class Hub:
    def __init__(self, buffer_size: int = 256) -> None:
        self._buffer_size = buffer_size
        self._subscribers: set[MemoryObjectSendStream[Message]] = set()

    async def publish(self, msg: Message) -> None:
        """Разослать сообщение всем подписчикам (drop-oldest при переполнении — B7)."""
        for send in list(self._subscribers):
            try:
                send.send_nowait(msg)
            except anyio.WouldBlock:
                # TODO(§8/B7): дропнуть старейшее + инкремент метрики backpressure.
                pass
            except anyio.BrokenResourceError:
                self._subscribers.discard(send)

    @contextmanager
    def slot(self) -> Iterator[MemoryObjectReceiveStream[Message]]:
        send, receive = anyio.create_memory_object_stream[Message](self._buffer_size)
        self._subscribers.add(send)
        try:
            yield receive
        finally:
            self._subscribers.discard(send)
            send.close()

    async def subscribe(self) -> AsyncIterator[Message]:
        with self.slot() as receive:
            async for msg in receive:
                yield msg
