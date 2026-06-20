"""Супервизор фоновых задач (§8, M4).

Регистрирует именованные корутины и запускает их в единой task group anyio.
Единая точка старта упрощает диагностику: каждая задача видна по имени в логах.
При падении любой задачи anyio отменяет остальные — Bridge падает целиком,
что соответствует политике «нет частичной работы».
"""

from __future__ import annotations

import logging
from typing import Callable, Coroutine, Any

import anyio

log = logging.getLogger(__name__)


class Supervisor:
    """Запускает именованные фоновые задачи в единой anyio task group."""

    def __init__(self) -> None:
        self._tasks: list[tuple[str, Callable[[], Coroutine[Any, Any, None]]]] = []

    def register(self, name: str, coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Зарегистрировать задачу. Порядок регистрации = порядок запуска."""
        self._tasks.append((name, coro_fn))

    async def run(self) -> None:
        """Запустить все задачи. Возвращается только при отмене или падении одной из них."""
        async with anyio.create_task_group() as task_group:
            for name, coro_fn in self._tasks:
                log.debug("supervisor: старт задачи '%s'", name)
                task_group.start_soon(coro_fn)
