"""Генератор страниц авто-доки конфига.

Запускается плагином ``mkdocs-gen-files`` при каждой сборке сайта. Обходит
дерево pydantic-моделей конфига и эмитит набор Markdown-страниц,
организованных «по YAML-секциям» (lora / messengers / rooms), а не
«по классам».

Источник правды — ``Field(description=...)`` и docstring'и моделей.
Discriminated union'ы спецкейсятся: показываются как «один из тегов»
с ссылками на варианты; smart-union без дискриминатора — как «один из
типов» по форме.

Чтобы добавить в авто-доку новую секцию: дописать вызов ``emit_section_page``
в ``main()``.
"""

from __future__ import annotations

import textwrap
import typing
from typing import Any, Union, get_args, get_origin

import mkdocs_gen_files
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from lora_bridge.config import schema
from lora_bridge.config.schema.ids import EndpointName, MessengerId, NodeId


# ---------------------------------------------------------------------------
# Описания секций (то, что не выводится из типов)
# ---------------------------------------------------------------------------


LORA_INTRO = """
Секция `lora:` — **массив** конфигов физических LoRa-нод. Каждая нода — это
один радиоузел плюс один или несколько каналов (эндпоинтов), которые
обслуживаются через это радио. Поля нижеперечислены ниже.
""".strip()

LORA_EXAMPLE = """
lora:
  - id: meshcore-1
    type: meshcore
    connection: { type: usb, device_id: "0403:6015" }
    endpoints:
      general:
        type: public
        channel_name: "General"
      ops:
        type: private
        channel_name: "Ops"
        secret: ${MC_OPS_SECRET}
    policies:
      egress_rate: { msgs_per_window: 6, window_seconds: 60 }
""".strip()


MESSENGERS_INTRO = """
Секция `messengers:` — **массив** конфигов мессенджер-транспортов. Каждый
элемент — отдельный бот/токен, на который ссылается `rooms[].subscribers`.
""".strip()

MESSENGERS_EXAMPLE = """
messengers:
  - id: telegram-main
    kind: telegram
    token: ${TG_BOT_TOKEN}
    tag: "TG"   # необязательно; по умолчанию — заглавные первых двух букв kind
""".strip()


ROOMS_INTRO = """
Секция `rooms:` — **массив** логических комнат. Комната связывает один
LoRa-эндпоинт (`lora` поле) с набором подписчиков (`subscribers`), между
которыми зеркалятся сообщения. Подписчиком может быть чат мессенджера или
другой LoRa-эндпоинт (LoRa↔LoRa relay).

Допустимые формы:

* **1 LoRa + N мессенджеров** — стандартный режим моста.
* **2 LoRa + 0 мессенджеров** — рилей между двумя радиосетями.
""".strip()

ROOMS_EXAMPLE = """
rooms:
  - lora: { node: meshcore-1, endpoint: general }
    subscribers:
      - { transport: telegram-main, chat: "-1001234567890" }
      - { transport: telegram-main, chat: "-1001234567890", topic: "42" }
""".strip()


CONFIG_OVERVIEW = """
# Конфиг

`config.yaml` описывает три верхнеуровневых секции — `lora`, `messengers`,
`rooms`, — которые ссылаются друг на друга по строковым id.

```yaml
lora: [...]          # массив физических LoRa-нод
messengers: [...]    # массив мессенджер-транспортов
rooms: [...]         # массив логических комнат
```

| Секция | Что описывает | Подробнее |
|--------|---------------|-----------|
| `lora` | Физические LoRa-ноды и их каналы | [lora](lora.md) |
| `messengers` | Мессенджер-транспорты | [messengers](messengers.md) |
| `rooms` | Привязка каналов к подписчикам | [rooms](rooms.md) |

Секции ссылаются друг на друга строковыми идентификаторами — все они
[документированы отдельно](types.md), чтобы было видно, кто на что
указывает.

!!! tip "Подстановка переменных окружения"
    В YAML работает синтаксис `${ENV_VAR}` через `envyaml`. Секреты
    держите в переменных окружения, не в файле — это рекомендуемый шаблон.
""".strip()


TYPES_INTRO = """
# Типы-ссылки между секциями

Идентификаторы, по которым секции ссылаются друг на друга. На уровне типов
это `NewType` над `str` — pydantic парсит их как обычные строки, mypy ловит
передачу «не того id» между слоями, а в авто-доке видно, на какую секцию
ссылка.
""".strip()


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    with mkdocs_gen_files.open("config/index.md", "w") as f:
        f.write(CONFIG_OVERVIEW + "\n")

    emit_section_page(
        path="config/lora.md",
        title="Секция `lora:` — массив LoRa-нод",
        intro=LORA_INTRO,
        yaml_example=LORA_EXAMPLE,
        root_model=schema.MeshCoreNode,
    )
    emit_section_page(
        path="config/messengers.md",
        title="Секция `messengers:` — мессенджер-транспорты",
        intro=MESSENGERS_INTRO,
        yaml_example=MESSENGERS_EXAMPLE,
        root_model=schema.TelegramMessengerConfig,
    )
    emit_section_page(
        path="config/rooms.md",
        title="Секция `rooms:` — комнаты",
        intro=ROOMS_INTRO,
        yaml_example=ROOMS_EXAMPLE,
        root_model=schema.RoomConfig,
    )
    emit_types_page()


def emit_section_page(
    *,
    path: str,
    title: str,
    intro: str,
    yaml_example: str,
    root_model: type[BaseModel],
) -> None:
    models = _collect_models(root_model)
    parts = [
        f"# {title}",
        "",
        intro,
        "",
        "## Пример YAML",
        "",
        "```yaml",
        yaml_example,
        "```",
        "",
        "## Поля",
        "",
    ]
    for m in models:
        parts.append(_render_model(m))
        parts.append("")
    with mkdocs_gen_files.open(path, "w") as f:
        f.write("\n".join(parts))


def emit_types_page() -> None:
    parts = [TYPES_INTRO, ""]
    for nt in (NodeId, EndpointName, MessengerId):
        nt_any: Any = nt  # NewType — статически не «класс»; докторим через Any
        parts.append(f"## `{nt_any.__name__}` {{ #{nt_any.__name__} }}")
        parts.append("")
        doc = (nt_any.__doc__ or "").strip()
        if doc:
            parts.append(doc)
            parts.append("")
        parts.append(f"Базовый тип: `{nt_any.__supertype__.__name__}`.")
        parts.append("")
    with mkdocs_gen_files.open("config/types.md", "w") as f:
        f.write("\n".join(parts))


# ---------------------------------------------------------------------------
# Сбор моделей: BFS от корня по дереву аннотаций
# ---------------------------------------------------------------------------


def _collect_models(root: type[BaseModel]) -> list[type[BaseModel]]:
    """BFS от ``root`` по аннотациям полей; стабильный порядок появления."""
    ordered: list[type[BaseModel]] = []
    seen: set[type[BaseModel]] = set()
    queue: list[type[BaseModel]] = [root]
    while queue:
        m = queue.pop(0)
        if m in seen:
            continue
        seen.add(m)
        ordered.append(m)
        for fi in m.model_fields.values():
            for child in _models_in(fi.annotation):
                if child not in seen:
                    queue.append(child)
    return ordered


def _models_in(t: Any) -> list[type[BaseModel]]:
    """Все BaseModel-классы, до которых можно дотянуться, развернув ``t``."""
    t = _unwrap_annotated(t)
    if isinstance(t, type) and issubclass(t, BaseModel):
        return [t]
    origin = get_origin(t)
    if origin in (list, dict, tuple, set, frozenset):
        return [m for a in get_args(t) for m in _models_in(a)]
    if origin in (Union, typing.Union):
        return [m for a in get_args(t) for m in _models_in(a)]
    return []


# ---------------------------------------------------------------------------
# Рендер модели
# ---------------------------------------------------------------------------


def _render_model(model: type[BaseModel]) -> str:
    out = [f"### `{model.__name__}` {{ #{model.__name__} }}", ""]
    doc = (model.__doc__ or "").strip()
    if doc:
        out.append(textwrap.dedent(doc).strip())
        out.append("")
    out.append("| Поле | Тип | Обязательно | Описание |")
    out.append("|------|-----|-------------|----------|")
    for name, fi in model.model_fields.items():
        type_str = _render_type(fi)
        required = "✓" if fi.is_required() else _render_default(fi)
        desc = _escape_cell(fi.description or "")
        out.append(f"| `{name}` | {type_str} | {required} | {desc} |")
    out.append("")
    return "\n".join(out)


def _render_type(fi: FieldInfo) -> str:
    """Тип поля для ячейки таблицы.

    Спецкейс: discriminator (если pydantic его выделил в ``fi.discriminator``) —
    показываем «один из тегов» с ссылками на варианты.
    """
    if fi.discriminator:
        return _render_discriminated(fi)
    return _pretty_type(fi.annotation)


def _render_discriminated(fi: FieldInfo) -> str:
    """Discriminated union → «один из (тегов)» с ссылками на варианты."""
    variants = [_unwrap_annotated(a) for a in get_args(_unwrap_annotated(fi.annotation))]
    discr_field = fi.discriminator if isinstance(fi.discriminator, str) else "type"
    parts: list[str] = []
    for v in variants:
        if not (isinstance(v, type) and issubclass(v, BaseModel)):
            continue
        tag = _discriminator_value(v, discr_field)
        link = f"[`{v.__name__}`](#{v.__name__})"
        if tag is not None:
            parts.append(f"`{discr_field}: {tag}` → {link}")
        else:
            parts.append(link)
    return "<br>".join(parts)


def _discriminator_value(model: type[BaseModel], field: str) -> str | None:
    fi = model.model_fields.get(field)
    if fi is None:
        return None
    ann = _unwrap_annotated(fi.annotation)
    if get_origin(ann) is typing.Literal:
        args = get_args(ann)
        return repr(args[0]) if args else None
    return None


def _pretty_type(t: Any) -> str:
    """Markdown-friendly рендер аннотации типа для ячейки таблицы."""
    t = _unwrap_annotated(t)
    sup = getattr(t, "__supertype__", None)
    if sup is not None:
        return f"[`{t.__name__}`](types.md#{t.__name__})"
    if t is type(None):
        return "`None`"
    if isinstance(t, type):
        if issubclass(t, BaseModel):
            return f"[`{t.__name__}`](#{t.__name__})"
        return f"`{t.__name__}`"

    origin = get_origin(t)
    args = get_args(t)

    if origin is list:
        return f"список {_pretty_type(args[0])}"
    if origin is dict:
        return f"словарь {_pretty_type(args[0])} → {_pretty_type(args[1])}"
    if origin is tuple:
        return "кортеж (" + ", ".join(_pretty_type(a) for a in args) + ")"
    if origin is typing.Literal:
        return " \\| ".join(f"`{a!r}`" for a in args)
    if origin in (Union, typing.Union):
        non_none = [a for a in args if a is not type(None)]
        rendered = " \\| ".join(_pretty_type(a) for a in non_none)
        if len(non_none) < len(args):
            return f"{rendered} (опционально)"
        return rendered

    return f"`{t!r}`"


def _render_default(fi: FieldInfo) -> str:
    """Текст в колонке «обязательно» для необязательного поля — показывает default."""
    if fi.default_factory is not None:
        try:
            v = fi.default_factory()  # type: ignore[call-arg]
        except Exception:
            return "—"
        return f"`{_repr_default(v)}`"
    if fi.default is None:
        return "`None`"
    if fi.default is Ellipsis:
        return "—"
    return f"`{_repr_default(fi.default)}`"


def _repr_default(v: Any) -> str:
    if isinstance(v, BaseModel):
        return f"{type(v).__name__}()"
    return repr(v)


def _escape_cell(text: str) -> str:
    """Markdown-табличная ячейка: экранируем разделитель и переносы."""
    return text.replace("|", r"\|").replace("\n", " ").strip()


def _unwrap_annotated(t: Any) -> Any:
    while hasattr(t, "__metadata__"):
        t = t.__origin__
    return t


main()
