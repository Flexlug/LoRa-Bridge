"""Человекочитаемое форматирование ``pydantic.ValidationError`` для конфига.

Идея: по полю ``loc`` каждой ошибки пройти типовое дерево ``AppConfig`` и для
типовых случаев (missing / extra_forbidden / union_tag_invalid / *_type /
value_error) показать пользователю:
  * путь в терминах YAML (``lora[0].connection.host``);
  * что именно ожидалось (тип, варианты дискриминатора);
  * какие ещё поля допустимы в этой секции (для extra/missing);
  * что было получено.

Smart-union (``Subscriber``) даёт ошибки сразу для всех вариантов — оставляем
только тот вариант, который «ближе» к интенту пользователя (минимум ошибок).
"""

from __future__ import annotations

import typing
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from .schema import AppConfig

__all__ = ["format_validation_error"]


# --- публичный API ---------------------------------------------------------


def format_validation_error(exc: ValidationError) -> str:
    raw: list[dict[str, Any]] = [dict(e) for e in exc.errors()]
    errors = _collapse_smart_unions(raw)
    n = len(errors)
    header = f"Конфиг не прошёл валидацию ({n} {_plural_errors(n)}):"
    blocks = [header, ""]
    for i, err in enumerate(errors, 1):
        blocks.extend(_format_one(err, i))
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def _plural_errors(n: int) -> str:
    """1 ошибка / 2-4 ошибки / 5+ ошибок (с учётом исключений 11-14)."""
    last_two = n % 100
    last = n % 10
    if 11 <= last_two <= 14:
        return "ошибок"
    if last == 1:
        return "ошибка"
    if 2 <= last <= 4:
        return "ошибки"
    return "ошибок"


# --- группировка smart-union ----------------------------------------------


def _collapse_smart_unions(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Smart union без дискриминатора порождает ошибки сразу для всех веток.

    Группируем по точке union'а и выбираем вариант с наименьшим числом ошибок —
    он ближе всего к тому, что пользователь имел в виду.
    """
    groups: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = {}
    standalone: list[dict[str, Any]] = []
    for err in errors:
        loc = err.get("loc", ())
        variant_idx = _smart_union_variant_index(loc)
        if variant_idx is None:
            standalone.append(err)
            continue
        prefix = tuple(loc[:variant_idx])
        variant = str(loc[variant_idx])
        groups.setdefault(prefix, {}).setdefault(variant, []).append(err)

    out = list(standalone)
    for variants in groups.values():
        best_variant = min(variants.items(), key=lambda kv: len(kv[1]))[0]
        out.extend(variants[best_variant])
    return out


def _smart_union_variant_index(loc: tuple[Any, ...]) -> int | None:
    """Индекс шага в ``loc``, который выглядит как имя класса smart-union ветки.

    Имена полей в наших моделях — snake_case, имена классов — PascalCase, так
    что эвристика «начинается с заглавной» точно отделяет варианты от полей.
    """
    for i, step in enumerate(loc):
        if isinstance(step, str) and step[:1].isupper():
            return i
    return None


# --- форматирование одной ошибки ------------------------------------------


_TYPE_NAMES_RU = {
    "string_type": "строка",
    "int_type": "целое число",
    "int_parsing": "целое число",
    "float_type": "число (float)",
    "float_parsing": "число (float)",
    "bool_type": "булево (true/false)",
    "bool_parsing": "булево (true/false)",
    "list_type": "список",
    "dict_type": "словарь",
    "model_type": "объект",
}

# Имена примитивов Python → русское название (для ``_pretty_type``).
_PY_TYPE_NAMES_RU = {
    str: "строка",
    int: "целое число",
    float: "число (float)",
    bool: "булево (true/false)",
    list: "список",
    dict: "словарь",
}


def _format_one(err: dict[str, Any], n: int) -> list[str]:
    loc = err.get("loc", ())
    kind = err.get("type", "")
    inp = err.get("input")
    ctx = err.get("ctx") or {}
    msg = err.get("msg", "")
    display_loc = _format_loc(loc)
    header = f"{n}. {display_loc}"

    if kind == "missing":
        return _format_missing(header, loc)
    if kind == "extra_forbidden":
        return _format_extra(header, loc)
    if kind == "union_tag_invalid":
        return _format_union_tag_invalid(header, ctx)
    if kind == "union_tag_not_found":
        return _format_union_tag_not_found(header, ctx)
    if kind == "literal_error":
        return _format_literal(header, ctx, inp)
    if kind in _TYPE_NAMES_RU:
        return _format_type_error(header, kind, inp)
    if kind == "value_error":
        clean = msg.removeprefix("Value error, ")
        return [header, f"   — {clean}"]
    # fallback — то, что не классифицировали
    out = [header, f"   — {msg}"]
    if inp is not None:
        out.append(f"   — получено: {_format_input(inp)}")
    return out


def _format_missing(header: str, loc: tuple[Any, ...]) -> list[str]:
    parent_models = _resolve_models(loc[:-1])
    field_name = loc[-1] if loc else "?"
    field_str = f"'{field_name}'" if isinstance(field_name, str) else str(field_name)
    out = [header, f"   — отсутствует обязательное поле {field_str}"]

    owner = _model_with_field(parent_models, field_name) if parent_models else None
    if owner is not None:
        ann = owner.model_fields[field_name].annotation
        out.append(f"   — ожидается тип: {_pretty_type(ann)}")
        out.append(f"   — поля {_humanize_model(owner)}: {_list_fields(owner)}")
    elif parent_models:
        # обязательное поле, но точную модель не вычислили — перечислим всё
        all_fields = _union_fields(parent_models)
        if all_fields:
            out.append(f"   — допустимые поля: {all_fields}")
    return out


def _format_extra(header: str, loc: tuple[Any, ...]) -> list[str]:
    parent_models = _resolve_models(loc[:-1])
    extra = loc[-1] if loc else "?"
    out = [header, f"   — поле '{extra}' не допускается в этой секции"]
    if parent_models:
        owner = parent_models[0] if len(parent_models) == 1 else None
        if owner is not None:
            out.append(f"   — допустимые поля {_humanize_model(owner)}: {_list_fields(owner)}")
        else:
            out.append(f"   — допустимые поля: {_union_fields(parent_models)}")
    return out


def _format_union_tag_invalid(header: str, ctx: dict[str, Any]) -> list[str]:
    disc = ctx.get("discriminator", "").strip("'\"") or "type"
    tag = ctx.get("tag", "")
    expected = ctx.get("expected_tags", "")
    return [
        header,
        f"   — неизвестное значение поля-дискриминатора '{disc}': {tag!r}",
        f"   — допустимые значения: {expected}",
    ]


def _format_union_tag_not_found(header: str, ctx: dict[str, Any]) -> list[str]:
    disc = ctx.get("discriminator", "").strip("'\"") or "type"
    return [
        header,
        f"   — не указано поле-дискриминатор '{disc}'",
        f"   — добавьте {disc}: <одно из ожидаемых значений>",
    ]


def _format_literal(header: str, ctx: dict[str, Any], inp: Any) -> list[str]:
    expected = ctx.get("expected", "")
    return [
        header,
        f"   — значение {_format_input(inp)} недопустимо",
        f"   — ожидается одно из: {expected}",
    ]


def _format_type_error(header: str, kind: str, inp: Any) -> list[str]:
    expected = _TYPE_NAMES_RU.get(kind, kind)
    return [
        header,
        f"   — ожидается тип: {expected}",
        f"   — получено: {_format_input(inp)}",
    ]


# --- утилиты форматирования ------------------------------------------------


def _format_loc(loc: tuple[Any, ...]) -> str:
    """``('lora', 0, 'connection', 'tcp', 'host')`` → ``lora[0].connection.host``.

    Имена вариантов union'ов (``'tcp'``, ``'MessengerSubscriber'``) скрываем —
    в YAML пользователя их нет, они только сбивают с толку.
    """
    parts: list[str] = []
    skip_next_dot = False
    for step in loc:
        if isinstance(step, int):
            parts.append(f"[{step}]")
            skip_next_dot = False
        elif isinstance(step, str) and _looks_like_variant_name(step):
            # discriminator-тег или smart-union класс — пропускаем
            continue
        else:
            if parts and not skip_next_dot:
                parts.append(".")
            parts.append(str(step))
            skip_next_dot = False
    return "".join(parts) or "(корень конфига)"


def _looks_like_variant_name(s: str) -> bool:
    return s[:1].isupper() or s in _ALL_DISCRIMINATOR_TAGS


def _format_input(inp: Any) -> str:
    if inp is None:
        return "null"
    s = f"'{inp}'" if isinstance(inp, str) else repr(inp)
    return s if len(s) <= 80 else s[:77] + "..."


def _list_fields(model: type[BaseModel]) -> str:
    parts = []
    for name, fi in model.model_fields.items():
        marker = "" if fi.is_required() else "?"
        parts.append(f"{name}{marker}")
    return ", ".join(parts)


def _union_fields(models: list[type[BaseModel]]) -> str:
    seen: dict[str, None] = {}
    for m in models:
        for name in m.model_fields:
            seen.setdefault(name, None)
    return ", ".join(seen)


def _humanize_model(model: type[BaseModel]) -> str:
    return f"({model.__name__})"


def _pretty_type(t: Any) -> str:
    t = _strip_annotated(t)
    if t is type(None):
        return "null"
    # NewType — показываем «NodeId (строка)»: семантика + базовый тип
    supertype = getattr(t, "__supertype__", None)
    if supertype is not None:
        return f"{t.__name__} ({_pretty_type(supertype)})"
    if isinstance(t, type):
        return _PY_TYPE_NAMES_RU.get(t, t.__name__)
    origin = get_origin(t)
    if origin is list:
        return f"список {_pretty_type(get_args(t)[0])}"
    if origin is dict:
        k, v = get_args(t)
        return f"словарь {_pretty_type(k)} → {_pretty_type(v)}"
    if origin in (Union, typing.Union):
        inner = [_pretty_type(a) for a in get_args(t) if a is not type(None)]
        return " | ".join(inner)
    if origin is typing.Literal:
        return "одно из: " + ", ".join(repr(a) for a in get_args(t))
    return str(t)


# --- резолвер: loc → набор кандидатов BaseModel ----------------------------


def _strip_annotated(t: Any) -> Any:
    while hasattr(t, "__metadata__"):
        t = t.__origin__
    return t


def _resolve_models(loc: tuple[Any, ...]) -> list[type[BaseModel]]:
    """Идём по ``loc`` в типовом дереве ``AppConfig``; возвращаем модели на конце пути.

    На каждом шаге ``Union`` ветвимся: пробуем все варианты, оставляем те, где
    шаг разрешается. Для шагов-строк, которые совпадают с тегом дискриминатора
    или именем класса варианта, сужаем Union до конкретного варианта.
    """
    candidates: list[Any] = [AppConfig]
    for step in loc:
        nxt: list[Any] = []
        for c in candidates:
            nxt.extend(_step(c, step))
        if not nxt:
            return []
        candidates = nxt
    expanded: list[Any] = []
    for c in candidates:
        expanded.extend(_expand_union(c))
    return [c for c in expanded if isinstance(c, type) and issubclass(c, BaseModel)]


def _model_with_field(models: list[type[BaseModel]], field: Any) -> type[BaseModel] | None:
    if not isinstance(field, str):
        return None
    for m in models:
        if field in m.model_fields:
            return m
    return None


def _step(node: Any, step: Any) -> list[Any]:
    node = _strip_annotated(node)
    origin = get_origin(node)

    if isinstance(node, type) and issubclass(node, BaseModel):
        if isinstance(step, str) and step in node.model_fields:
            return [_strip_annotated(node.model_fields[step].annotation)]
        # smart-union: loc содержит имя класса варианта прямо здесь
        if isinstance(step, str) and step == node.__name__:
            return [node]
        return []

    if origin is list and isinstance(step, int):
        return [_strip_annotated(get_args(node)[0])]

    if origin is dict and isinstance(step, str):
        return [_strip_annotated(get_args(node)[1])]

    if origin in (Union, typing.Union):
        # discriminator-тег: сузим Union до варианта, у которого Literal[step]
        if isinstance(step, str):
            for arg in get_args(node):
                arg_t = _strip_annotated(arg)
                if not (isinstance(arg_t, type) and issubclass(arg_t, BaseModel)):
                    continue
                if arg_t.__name__ == step:
                    return [arg_t]
                if _variant_matches_tag(arg_t, step):
                    return [arg_t]
            # ничего не подошло — ветвимся как обычно
        results: list[Any] = []
        for arg in get_args(node):
            if arg is type(None):
                continue
            results.extend(_step(arg, step))
        return results

    return []


def _expand_union(t: Any) -> list[Any]:
    t = _strip_annotated(t)
    if get_origin(t) in (Union, typing.Union):
        out: list[Any] = []
        for arg in get_args(t):
            if arg is type(None):
                continue
            out.extend(_expand_union(arg))
        return out
    return [t]


def _variant_matches_tag(variant: type[BaseModel], tag: str) -> bool:
    for fi in variant.model_fields.values():
        ann = _strip_annotated(fi.annotation)
        if get_origin(ann) is typing.Literal and tag in get_args(ann):
            return True
    return False


def _collect_discriminator_tags(root: type[BaseModel] | None = None) -> set[str]:
    """Все теги дискриминаторов — чтобы скрыть их при печати ``loc``."""
    out: set[str] = set()
    seen: set[Any] = set()

    def visit(t: Any) -> None:
        t = _strip_annotated(t)
        if t in seen:
            return
        seen.add(t)
        if isinstance(t, type) and issubclass(t, BaseModel):
            for fi in t.model_fields.values():
                visit(fi.annotation)
            return
        origin = get_origin(t)
        if origin in (Union, typing.Union):
            for arg in get_args(t):
                arg_t = _strip_annotated(arg)
                if isinstance(arg_t, type) and issubclass(arg_t, BaseModel):
                    for fi in arg_t.model_fields.values():
                        ann = _strip_annotated(fi.annotation)
                        if get_origin(ann) is typing.Literal:
                            for v in get_args(ann):
                                if isinstance(v, str):
                                    out.add(v)
                    visit(arg_t)
                else:
                    visit(arg_t)
            return
        if origin in (list, dict):
            for arg in get_args(t):
                visit(arg)

    visit(root or AppConfig)
    return out


_ALL_DISCRIMINATOR_TAGS: set[str] = _collect_discriminator_tags()
