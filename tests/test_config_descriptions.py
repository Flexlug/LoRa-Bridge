"""Тесты-страховки рефактора: NewType + Field(description).

После миграции инлайн-комментариев на ``Field(description=…)`` и id-полей
на ``NodeId``/``EndpointName``/``MessengerId`` хотим зафиксировать:

* у всех полей конфиг-моделей описание есть и непустое — иначе будущий
  генератор доки выдаст «голые» строки;
* NewType работает прозрачно для pydantic (парсит как ``str``), но
  читается как семантический тип в аннотациях и в выводе форматтера ошибок.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from lora_bridge.config.errors import _pretty_type, format_validation_error
from lora_bridge.config.schema import (
    AppConfig,
    BleConnection,
    EgressRate,
    EndpointName,
    LabelPolicy,
    LoraRef,
    LoraSubscriber,
    MeshCoreNode,
    MessengerId,
    MessengerSubscriber,
    NodeId,
    NodePolicies,
    PrivateEndpoint,
    PublicEndpoint,
    ReconnectBackoff,
    RoomConfig,
    RoomServerEndpoint,
    SerialConnection,
    TcpConnection,
    TelegramMessengerConfig,
    UsbConnection,
)


# ---------------------------------------------------------------------------
# Покрытие описаниями
# ---------------------------------------------------------------------------

_DOCUMENTED_MODELS: list[type[BaseModel]] = [
    AppConfig,
    MeshCoreNode,
    NodePolicies,
    EgressRate,
    ReconnectBackoff,
    LabelPolicy,
    UsbConnection,
    SerialConnection,
    TcpConnection,
    BleConnection,
    PublicEndpoint,
    PrivateEndpoint,
    RoomServerEndpoint,
    TelegramMessengerConfig,
    RoomConfig,
    LoraRef,
    MessengerSubscriber,
    LoraSubscriber,
]


@pytest.mark.parametrize("model", _DOCUMENTED_MODELS, ids=lambda m: m.__name__)
def test_every_field_of_config_model_has_description(model):
    """Если упадёт — добавьте ``Field(description=…)`` новому полю."""
    missing = [
        name for name, fi in model.model_fields.items()
        if not (fi.description and fi.description.strip())
    ]
    assert not missing, (
        f"{model.__name__}: поля без description: {missing}. "
        "Добавьте ``Field(description=...)`` — оно попадёт в авто-доку."
    )


@pytest.mark.parametrize("model", _DOCUMENTED_MODELS, ids=lambda m: m.__name__)
def test_every_documented_model_has_class_docstring(model):
    """Класс-докстринг используется как описание модели в генераторе доки."""
    assert model.__doc__ and model.__doc__.strip(), (
        f"{model.__name__} без класс-докстринга — он рендерится в шапке секции доки."
    )


# ---------------------------------------------------------------------------
# NewType: прозрачен для pydantic, осмыслен для типов и доки
# ---------------------------------------------------------------------------


def test_newtype_ids_are_transparent_for_pydantic_validation():
    """NodeId/EndpointName/MessengerId парсятся ровно как ``str``."""
    cfg = MeshCoreNode.model_validate(
        {
            "id": "n1",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 1},
            "endpoints": {"ch": {"type": "public", "channel_name": "G"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        }
    )
    # значения сохраняются как обычные строки (NewType — runtime-noop)
    assert cfg.id == "n1"
    assert "ch" in cfg.endpoints


@pytest.mark.parametrize(
    "model,field,expected_type_name",
    [
        (MeshCoreNode, "id", "NodeId"),
        (LoraRef, "node", "NodeId"),
        (LoraRef, "endpoint", "EndpointName"),
        (MessengerSubscriber, "transport", "MessengerId"),
        (TelegramMessengerConfig, "id", "MessengerId"),
    ],
)
def test_id_fields_use_newtype_annotation(model, field, expected_type_name):
    """Аннотация поля должна быть NewType, а не голым ``str``."""
    annotation = model.model_fields[field].annotation
    assert getattr(annotation, "__name__", None) == expected_type_name


@pytest.mark.parametrize(
    "newtype,label",
    [
        (NodeId, "NodeId (строка)"),
        (EndpointName, "EndpointName (строка)"),
        (MessengerId, "MessengerId (строка)"),
    ],
)
def test_pretty_type_renders_newtype_with_supertype(newtype, label):
    """В авто-доке/ошибках NewType виден как «имя (базовый тип)»."""
    assert _pretty_type(newtype) == label


def test_missing_node_id_error_mentions_newtype_in_expected_type():
    """Регрессия: ошибка про пропущенный ``lora[0].id`` показывает NodeId, а не str."""
    try:
        AppConfig.model_validate(
            {
                "lora": [
                    {
                        "type": "meshcore",
                        "connection": {"type": "tcp", "host": "h", "port": 1},
                        "endpoints": {"ch": {"type": "public", "channel_name": "G"}},
                        "policies": {
                            "egress_rate": {"msgs_per_window": 6, "window_seconds": 60}
                        },
                    }
                ],
                "messengers": [],
                "rooms": [],
            }
        )
    except ValidationError as exc:
        msg = format_validation_error(exc)
        assert "lora[0].id" in msg
        assert "обязательное поле 'id'" in msg
        assert "NodeId" in msg
    else:
        pytest.fail("ожидалась ValidationError")
