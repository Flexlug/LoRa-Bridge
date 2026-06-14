from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class UsbConnection(BaseModel):
    type: Literal["usb"]
    device_id: str          # VID:PID, напр. "0333:0303"


class SerialConnection(BaseModel):
    type: Literal["serial"]
    port: str               # /dev/ttyUSB0 / COM3


class TcpConnection(BaseModel):
    type: Literal["tcp"]
    host: str
    port: int


class BleConnection(BaseModel):
    type: Literal["ble"]
    address: str


Connection = Annotated[
    Union[UsbConnection, SerialConnection, TcpConnection, BleConnection],
    Field(discriminator="type"),
]
