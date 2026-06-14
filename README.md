# MeshCore-Bridge

Двунаправленный мост между сетью **MeshCore** (LoRa) и мессенджерами (Telegram, …):
сообщения из мессенджера уходят в эфир, а принятое из эфира зеркалится подписчикам.

## Структура проекта

```
meshcore_bridge/
├── domain/      # модели + порт Transport (ни от кого не зависит)
├── core/        # commit-очередь, фан-аут, dedup/loop-guard, статусы, журнал
├── transports/  # адаптеры: meshcore (LoRa), telegram
├── config/      # pydantic-схема + загрузчик YAML (${ENV})
└── app.py       # composition root
```

Текущий статус — **скелет**: доменный слой и чистые функции (`transform`, `dedup`,
`loopguard`, `hub`) реализованы и покрыты тестами; async-оркестрация и адаптеры —
типизированные заглушки с `TODO(§…)`, ссылающимися на разделы архитектуры.

## Запуск

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml      # заполнить секреты/маршруты
pytest -q                               # тесты чистого слоя
MESHCORE_BRIDGE_CONFIG=config.yaml meshcore-bridge   # (после реализации оркестрации)
```

## Документация

- [Архитектура](docs/ARCHITECTURE.md) — порты и абстракции, доменная модель,
  ядро (Router/Bridge), реактивные потоки, конвейер обработки, mermaid-диаграммы
  и подробная «прожарка» корнер-кейсов.
