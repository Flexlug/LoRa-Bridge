# Для разработчиков

Этот раздел — для тех, кто хочет добавить новый тип транспорта, разобраться в
устройстве проекта или внести изменения. Если ваша задача — просто запустить
бота, всё нужное в **[Установке](../install/index.md)** и **[Конфиге](../config/index.md)**.

## Локальный запуск

Основной способ запустить мост — Docker (см.
[README](https://github.com/Flexlug/LoRa-Bridge/blob/main/README.md)). Для
разработки удобнее собрать и запустить из исходников напрямую:

```bash
git clone https://github.com/Flexlug/LoRa-Bridge.git
cd LoRa-Bridge
uv sync --extra dev --extra docs
cp config.example.yaml config.yaml      # заменить device_id/channel_name и Telegram token/chat_id
lora-bridge
```

Переменные окружения (`LORA_BRIDGE_CONFIG`, а также `${ENV_VAR}`-подстановки вроде
`TG_BOT_TOKEN` в конфиге) не обязательны — по умолчанию `lora-bridge` уже ищет
`config.yaml` рядом с собой, а секреты можно просто вписать в файл текстом.
`${ENV_VAR}`-синтаксис нужен, только если вы сами хотите не держать секреты в
конфиге (например, в CI). Полный список переменных — в
**[Установке](../install/index.md#env-vars)**.

## Тесты

```bash
uv run pytest -q                              # все тесты
uv run pytest tests/test_pipeline.py -v       # один файл
uv run pytest tests/test_pipeline.py::test_x  # один тест
```

Все async-тесты подхватывают `pytest-anyio` с `anyio_mode = "auto"` — декоратор
не нужен.

## Линт и типы

```bash
uv run ruff check
uv run mypy lora_bridge
```

`pyproject.toml` фиксирует `ruff` line-length 100 и `mypy --strict` для пакета.

## Документация локально

```bash
uv run mkdocs serve   # http://127.0.0.1:8000
```

Большая часть страниц про конфиг **генерируется автоматически** при сборке —
скрипт `docs/gen_pages.py` обходит pydantic-схему и эмитит markdown. Подробности
о том, как это устроено и как добавить новую секцию, — в комментариях самого
скрипта.

## Структура репозитория

```
lora_bridge/
├── domain/      # модели + порт Transport (ни от кого не зависит)
├── core/        # commit-очередь, фан-аут, dedup/loop-guard, статусы, журнал
├── transports/  # адаптеры: meshcore (LoRa), telegram
├── config/      # pydantic-схема + загрузчик YAML (${ENV})
└── app.py       # composition root
```

## Архитектура

Развёрнутый разбор устройства проекта — портов и абстракций, доменной модели,
ядра (Router/Bridge), реактивных потоков, конвейера обработки и корнер-кейсов —
в **[архитектурном документе](https://github.com/Flexlug/LoRa-Bridge/blob/main/docs/ARCHITECTURE.md)**
(не входит в сайт документации).

В коде ссылки вида `§5`, `§6`, `AD-4` отсылают к этому документу.

## Статус проекта

* **Ядро** реализовано и покрыто тестами: маршрутизация, commit-очередь и
  egress, статусы, dedup/loop-guard, журнал (SQLite, §11.1), cross-валидация
  конфига.
* **Адаптер MeshCore** (`meshcore_py`) проверен на живом железе (Heltec v3, USB,
  два узла одновременно, public и private каналы). Части, относящиеся к
  `room_server`-эндпоинту, ещё не проверялись — соответствующие вызовы
  помечены `# verify`.
* **Адаптер Telegram** (`aiogram`) проверен с живым ботом и групповым чатом:
  отправка, зеркалирование из LoRa, реакции-статусы.

## Как принять участие

* Баг-репорт или фича-реквест — issues в репозитории.
* PR — приветствуются. Перед открытием убедитесь, что тесты и линт зелёные.
* Если работаете над адаптером — снимите `# verify`-метки только после
  проверки на реальном железе.
