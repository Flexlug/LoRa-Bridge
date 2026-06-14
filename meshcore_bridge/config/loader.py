"""Загрузка YAML-конфига + подстановка ${ENV_VAR} (§12)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .schema import AppConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """Рекурсивно подставить ${VAR} из окружения в строковых значениях."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var = m.group(1)
            try:
                return os.environ[var]
            except KeyError as exc:  # noqa: PERF203
                raise KeyError(f"Не задана переменная окружения: {var}") from exc
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | Path) -> AppConfig:
    """Прочитать YAML, подставить env, провалидировать в ``AppConfig``."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig.model_validate(_expand_env(raw))
