"""Composition root: собрать граф объектов из конфига и запустить мост (§13).

Здесь — и ТОЛЬКО здесь — связываются слои: читаем конфиг, инстанцируем транспорты
и ядро, отдаём управление ``Bridge.run``.
"""
from __future__ import annotations

import os

import anyio

from .config.loader import load_config
from .config.schema import AppConfig


async def run(config: AppConfig) -> None:
    """Собрать и запустить мост.

    TODO(§13): на КАЖДУЮ ноду из config.lora инстанцировать транспорт по node.type
    (meshcore → MeshCoreTransport; meshtastic → будущий адаптер) + его NodeRuntime
    (CommitQueue/TtlDedup/LoopGuard/LabelFormat из node.policies); инстанцировать
    TelegramTransport'ы; собрать StatusDispatcher/DropNotifier/SqliteJournal;
    построить RoomRegistry, recovery журнала (§11.1), затем Bridge(...).run().
    """
    raise NotImplementedError("TODO(§13): composition root")


def main() -> None:
    """CLI-точка входа (см. [project.scripts] в pyproject.toml)."""
    config_path = os.environ.get("LORA_BRIDGE_CONFIG", "config.yaml")
    config = load_config(config_path)
    anyio.run(run, config)


if __name__ == "__main__":
    main()
