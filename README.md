# LoRa-Bridge

Двунаправленный мост между **LoRa**-сетями (MeshCore, …) и мессенджерами (Telegram, …):
сообщения из мессенджера уходят в эфир, а принятое из эфира зеркалится подписчикам.
Поддерживает несколько физических LoRa-нод (секция `lora` — массив), каждая со своим
`type` (`meshcore` | `meshtastic`*) — фундамент под LoRa↔LoRa-мостинг.

## Структура проекта

```
lora_bridge/
├── domain/      # модели + порт Transport (ни от кого не зависит)
├── core/        # commit-очередь, фан-аут, dedup/loop-guard, статусы, журнал
├── transports/  # адаптеры: meshcore (LoRa), telegram (meshtastic — точка расширения)
├── config/      # pydantic-схема + загрузчик YAML (${ENV})
└── app.py       # composition root
```

Текущий статус: **ядро реализовано и покрыто тестами** (28 тестов) — маршрутизация,
commit-очередь + egress, статусы, dedup/loop-guard, журнал (SQLite, §11.1),
кросс-валидация конфига, LoRa↔LoRa relay. Адаптеры **MeshCore** (`meshcore_py`) и
**Telegram** (`aiogram`) написаны против API библиотек, но **на живом узле/токене
ещё не проверялись** — места вызовов помечены `# verify`.

## Запуск

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml      # заполнить секреты/маршруты
pytest -q                               # тесты чистого слоя
LORA_BRIDGE_CONFIG=config.yaml lora-bridge   # (после реализации оркестрации)
```

## Документация

- [Архитектура](docs/ARCHITECTURE.md) — порты и абстракции, доменная модель,
  ядро (Router/Bridge), реактивные потоки, конвейер обработки, mermaid-диаграммы
  и подробная «прожарка» корнер-кейсов.
