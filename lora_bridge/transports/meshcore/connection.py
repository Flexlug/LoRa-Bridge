"""Установка соединения с MeshCore-узлом и общие команды устройства (§5.1).

Не зависит от типа эндпоинта — это транспортный уровень одного радио: выбор
канала связи (TCP/serial/USB/BLE), поиск порта по VID:PID, синхронизация времени.
Точные вызовы `meshcore_py` помечены ``# verify``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, assert_never

from meshcore import MeshCore
from serial.tools import list_ports

from ...config.schema import (
    BleConnection,
    SerialConnection,
    TcpConnection,
    UsbConnection,
)

log = logging.getLogger(__name__)

# Тип объединения соединений; берём из конкретных моделей конфига.
Connection = TcpConnection | SerialConnection | UsbConnection | BleConnection


async def connect_mc(coro: Awaitable[Any], label: str, node_id: str) -> Any:
    try:
        mc = await coro
    except Exception as exc:
        raise RuntimeError(f"нода '{node_id}': не удалось подключиться {label}: {exc}") from exc
    if mc is None:
        raise RuntimeError(f"нода '{node_id}': {label} — нет ответа от устройства")
    log.info("нода '%s' подключена: %s", node_id, label)
    return mc


async def connect(connection: Connection, node_id: str) -> Any:
    match connection:
        case TcpConnection(host=host, port=port):
            return await connect_mc(
                MeshCore.create_tcp(host, port), f"TCP {host}:{port}", node_id  # verify
            )
        case SerialConnection(port=port):
            return await connect_mc(
                MeshCore.create_serial(port), f"serial {port}", node_id  # verify
            )
        case UsbConnection(device_id=device_id):
            serial_port = port_by_vidpid(device_id)
            return await connect_mc(
                MeshCore.create_serial(serial_port), f"USB {device_id} ({serial_port})", node_id
            )
        case BleConnection(address=address):
            return await connect_mc(
                MeshCore.create_ble(address), f"BLE {address}", node_id
            )
        case _ as unreachable:
            assert_never(unreachable)


def port_by_vidpid(device_id: str) -> str:
    """Найти serial-порт по VID:PID (usb-соединение)."""
    vid, pid = (int(x, 16) for x in device_id.split(":"))
    for p in list_ports.comports():
        if p.vid == vid and p.pid == pid:
            return p.device
    raise RuntimeError(f"USB-устройство {device_id} не найдено")


async def set_time(mc: Any) -> None:
    await mc.commands.set_time(int(time.time()))  # verify
