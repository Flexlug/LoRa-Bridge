"""Настройки приложения из переменных окружения.

Единственное место, где читается os.environ. Вся инфраструктурная конфигурация
(не бизнес-логика, которая живёт в YAML) — здесь.
"""

from __future__ import annotations

import dataclasses
import os


@dataclasses.dataclass(frozen=True)
class Settings:
    db_path: str
    log_level: str
    config_path: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=os.environ.get("LORA_BRIDGE_DB", "lora_bridge.sqlite"),
            log_level=os.environ.get("LORA_BRIDGE_LOG", "INFO"),
            config_path=os.environ.get("LORA_BRIDGE_CONFIG", "config.yaml"),
        )
