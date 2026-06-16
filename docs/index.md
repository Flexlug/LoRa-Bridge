# LoRa-Bridge

Двунаправленный мост между **LoRa**-сетями (MeshCore, …) и мессенджерами
(Telegram, …). Сообщения из мессенджера уходят в эфир, а принятое из эфира
зеркалится подписчикам. Поддерживается несколько физических LoRa-нод, каждая
со своим типом прошивки.

## Документация

- **[Конфиг](config/index.md)** — структура `config.yaml` и описание всех
  полей. Большая часть страниц этого раздела авто-генерируется при сборке
  из pydantic-схемы — `Field(description=...)` и docstring'и в коде ↔ ровно
  то, что вы здесь видите.
- [Архитектура](https://github.com/Flexlug/LoRa-Bridge/blob/main/docs/ARCHITECTURE.md)
  — порты и абстракции, доменная модель, ядро, разбор корнер-кейсов.

## Быстрый старт

```bash
uv sync
cp config.example.yaml config.yaml      # заполнить секреты/маршруты
export TG_BOT_TOKEN="..."
LORA_BRIDGE_CONFIG=config.yaml lora-bridge
```

Подробнее — в [README](https://github.com/Flexlug/LoRa-Bridge#readme).
