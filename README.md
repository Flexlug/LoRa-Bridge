# LoRa-Bridge

Двунаправленный мост между **LoRa**-сетями (MeshCore, …) и мессенджерами (Telegram, …):
сообщения из мессенджера уходят в эфир, а принятое из эфира зеркалится подписчикам.
Поддерживает несколько физических LoRa-нод, каждую со своими каналами, а также
LoRa↔LoRa мостинг без мессенджеров.

## Быстрый старт

```bash
git clone https://github.com/Flexlug/LoRa-Bridge.git
cd LoRa-Bridge
uv sync
cp config.example.yaml config.yaml      # заполнить секреты и маршруты
LORA_BRIDGE_CONFIG=config.yaml lora-bridge
```

Переменные окружения приложения:

| Переменная           | По умолчанию         | Назначение                           |
|----------------------|----------------------|--------------------------------------|
| `LORA_BRIDGE_CONFIG` | `config.yaml`        | Путь к файлу конфига                 |
| `LORA_BRIDGE_DB`     | `lora_bridge.sqlite` | Путь к SQLite-журналу намерений      |
| `LORA_BRIDGE_LOG`    | `INFO`               | Уровень логов: `DEBUG`/`INFO`/`WARNING` |

## Документация

Полная документация — в [docs/](docs/index.md) (или собирается как сайт через `uv run mkdocs serve`):

- **[Установка и быстрый старт](docs/install/index.md)** — от чистой системы до запущенного моста
- **[Подключение MeshCore](docs/install/meshcore.md)** — USB / serial / TCP / BLE, каналы
- **[Настройка Telegram-бота](docs/install/telegram.md)** — BotFather, chat_id, темы
- **[Конфигурация](docs/config/index.md)** — справочник всех полей `config.yaml`
- **[Для разработчиков](docs/contributing/index.md)** — архитектура, тесты, линт
- **[Архитектура](docs/ARCHITECTURE.md)** — порты и абстракции, доменная модель, диаграммы

## Структура проекта

```
lora_bridge/
├── domain/      # модели + порт Transport (ни от кого не зависит)
├── core/        # commit-очередь, фан-аут, dedup/loop-guard, статусы, журнал
├── transports/  # адаптеры: meshcore (LoRa), telegram
├── config/      # pydantic-схема + загрузчик YAML (${ENV})
└── app.py       # composition root
```
