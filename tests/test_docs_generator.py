"""Тест-страховка генератора авто-доки.

Запускает ``docs/gen_pages.py`` в подменённой mkdocs-gen-files-сессии и
проверяет, что:

* Генератор не падает при обходе схемы.
* Для каждой YAML-секции эмитится соответствующий файл.
* На странице ``lora`` присутствуют все варианты дискриминированных union'ов
  (connection + endpoint), а не голый ``Union[...]``.
* Cross-refs c id-полей уходят на ``types.md``.

Сам mkdocs-gen-files в проде живёт внутри плагина mkdocs; здесь мы импортим
его как обычный модуль и подменяем ``open`` на in-memory словарь, чтобы
не писать файлы на диск.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def emitted(monkeypatch) -> dict[str, str]:
    """Запустить генератор, перехватив все ``mkdocs_gen_files.open``."""
    captured: dict[str, str] = {}

    class _Buffer(io.StringIO):
        def __init__(self, sink: dict[str, str], path: str) -> None:
            super().__init__()
            self._sink = sink
            self._path = path

        def close(self) -> None:
            self._sink[self._path] = self.getvalue()
            super().close()

    fake_module = types.ModuleType("mkdocs_gen_files")

    def fake_open(path: str, mode: str = "r") -> Any:
        assert mode == "w", f"генератор пытается открыть на чтение: {path}"
        return _Buffer(captured, path)

    fake_module.open = fake_open  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mkdocs_gen_files", fake_module)
    # очищаем кеш модуля, чтобы main() выполнился заново
    sys.modules.pop("gen_pages", None)

    gen_path = Path(__file__).resolve().parent.parent / "docs" / "gen_pages.py"
    spec = importlib.util.spec_from_file_location("gen_pages", gen_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # выполнит main() внизу файла
    return captured


# ---------------------------------------------------------------------------


_EXPECTED_PAGES = [
    "config/index.md",
    "config/lora.md",
    "config/messengers.md",
    "config/rooms.md",
    "config/types.md",
]


@pytest.mark.parametrize("page", _EXPECTED_PAGES)
def test_generator_emits_expected_page(emitted, page):
    assert page in emitted, f"страница не сгенерирована: {page}; есть: {sorted(emitted)}"
    assert emitted[page].strip(), f"страница {page} пустая"


def test_lora_page_expands_all_connection_variants(emitted):
    lora = emitted["config/lora.md"]
    for variant in ("UsbConnection", "SerialConnection", "TcpConnection", "BleConnection"):
        assert f"`{variant}`" in lora, f"вариант connection не отрендерен: {variant}"


def test_lora_page_expands_all_endpoint_variants(emitted):
    lora = emitted["config/lora.md"]
    for variant in ("PublicEndpoint", "PrivateEndpoint", "RoomServerEndpoint"):
        assert f"`{variant}`" in lora, f"вариант endpoint не отрендерен: {variant}"


def test_discriminator_tag_value_visible_for_each_variant(emitted):
    """В таблице у поля connection видно «type: 'usb'» и т.п. — не голый Union[…]."""
    lora = emitted["config/lora.md"]
    for tag in ("'usb'", "'serial'", "'tcp'", "'ble'"):
        assert f"type: {tag}" in lora, f"тег дискриминатора не показан: {tag}"


def test_rooms_page_expands_smart_union_variants(emitted):
    rooms = emitted["config/rooms.md"]
    for variant in ("MessengerSubscriber", "LoraSubscriber"):
        assert f"`{variant}`" in rooms


def test_id_fields_link_to_types_page(emitted):
    """Поля с NewType должны указывать на types.md, а не на текущую страницу."""
    lora = emitted["config/lora.md"]
    # `id: NodeId` → ссылка вида types.md#NodeId
    assert "types.md#NodeId" in lora
    assert "types.md#EndpointName" in lora
    rooms = emitted["config/rooms.md"]
    assert "types.md#MessengerId" in rooms


def test_types_page_lists_all_newtype_aliases(emitted):
    types_page = emitted["config/types.md"]
    for name in ("NodeId", "EndpointName", "MessengerId"):
        # якорь должен быть, чтобы cross-refs резолвились
        assert f"#{name}" in types_page
        # и сам заголовок
        assert f"`{name}`" in types_page


def test_descriptions_make_it_into_rendered_tables(emitted):
    """Хотя бы одно описание из Field(description=...) должно попасть в вывод."""
    lora = emitted["config/lora.md"]
    # из MeshCoreNode.id
    assert "rooms[].lora.node" in lora
    # из EgressRate.msgs_per_window
    assert "за одно окно" in lora


def test_no_unresolved_python_typing_repr(emitted):
    """В готовом markdown не должно быть сырого typing.Union/Annotated в виде repr."""
    for path, content in emitted.items():
        assert "typing.Union" not in content, f"в {path} утёк typing.Union"
        assert "lora_bridge.config.schema" not in content, (
            f"в {path} утёк fully-qualified путь к модели"
        )
