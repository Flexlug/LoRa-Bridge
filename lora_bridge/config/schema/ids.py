"""Семантические псевдонимы для id-ссылок между секциями конфига.

Каждая секция YAML ссылается на сущность из другой секции через строковый id:

* ``rooms[].lora.node``           → ``lora[].id``        (``NodeId``)
* ``rooms[].lora.endpoint``       → ``lora[].endpoints`` (``EndpointName``, ключ dict)
* ``rooms[].subscribers[].transport`` → ``messengers[].id`` (``MessengerId``)

``NewType`` даёт два эффекта:

* mypy ловит передачу id «не того типа» между слоями (NodeId vs MessengerId);
* в авто-доке тип поля виден как ``NodeId``/``EndpointName``/``MessengerId`` —
  читателю сразу ясно, на какую секцию ссылка, а не безликий ``str``.
"""

from __future__ import annotations

from typing import NewType

NodeId = NewType("NodeId", str)
"""Идентификатор LoRa-ноды (``lora[].id``)."""

EndpointName = NewType("EndpointName", str)
"""Имя эндпоинта внутри ноды (ключ ``lora[].endpoints``)."""

MessengerId = NewType("MessengerId", str)
"""Идентификатор транспорта мессенджера (``messengers[].id``)."""
