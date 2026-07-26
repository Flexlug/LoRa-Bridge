"""Примитивы интроспекции типового дерева конфиг-схемы.

Общие для рендера конфиг-ошибок (``config/errors.py``) и генератора
справочника конфига (``docs/gen_pages.py``).
"""

from __future__ import annotations

import types
import typing
from typing import Any


def is_union_origin(origin: object) -> bool:
    """Union в обеих формах: ``typing.Union[X, Y]`` и PEP 604 ``X | Y`` (types.UnionType)."""
    return origin is typing.Union or origin is types.UnionType


def strip_annotated(t: Any) -> Any:
    """Снять слои ``Annotated[...]``, добравшись до базового типа."""
    while hasattr(t, "__metadata__"):
        t = t.__origin__
    return t
