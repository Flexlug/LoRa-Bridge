"""Способ физического подключения к LoRa-узлу.

Дискриминированный union по полю ``type``: добавление нового типа подключения
сводится к новому классу и расширению ``Union`` в алиасе ``Connection``.
Все ветки автоматически попадают в exhaustiveness-чек у потребителей
(см. ``transports/meshcore/transport.py``).
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class UsbConnection(BaseModel):
    """Подключение по USB. Узел адресуется парой VID:PID."""

    type: Literal["usb"] = Field(description="Тег дискриминатора — должно быть ``usb``.")
    device_id: str = Field(
        description=(
            'Идентификатор устройства в формате "VID:PID", напр. "0403:6015". '
            "На Linux находится в выводе `lsusb`, на Windows — Device Manager → "
            "Properties → Details."
        )
    )


class SerialConnection(BaseModel):
    """Прямое подключение по serial-порту (виртуальному или физическому)."""

    type: Literal["serial"] = Field(description="Тег дискриминатора — должно быть ``serial``.")
    port: str = Field(
        description=(
            'Путь до устройства: "/dev/ttyUSB0" (Linux/macOS), "COM3" (Windows).'
        )
    )


class TcpConnection(BaseModel):
    """Подключение к companion-серверу MeshCore по TCP."""

    type: Literal["tcp"] = Field(description="Тег дискриминатора — должно быть ``tcp``.")
    host: str = Field(description="Хост или IP companion-сервера MeshCore.")
    port: int = Field(description="TCP-порт companion-сервера.")


class BleConnection(BaseModel):
    """Подключение по Bluetooth Low Energy."""

    type: Literal["ble"] = Field(description="Тег дискриминатора — должно быть ``ble``.")
    address: str = Field(
        description='MAC-адрес устройства, напр. "AA:BB:CC:DD:EE:FF".'
    )


Connection = Annotated[
    Union[UsbConnection, SerialConnection, TcpConnection, BleConnection],
    Field(discriminator="type"),
]
"""Способ физического подключения к LoRa-узлу.

Дискриминирован по ``type``: ``usb`` | ``serial`` | ``tcp`` | ``ble``.
"""
