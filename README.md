# LoRa-Bridge

[![CI](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/ci.yml)
[![Docs](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docs.yml/badge.svg)](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docs.yml)
[![Docker Publish: GHCR](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docker-publish-ghcr.yml/badge.svg)](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docker-publish-ghcr.yml)
[![Docker Publish: Yandex Cloud](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docker-publish-yandex.yml/badge.svg)](https://github.com/Flexlug/LoRa-Bridge/actions/workflows/docker-publish-yandex.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Двунаправленный мост между **LoRa**-сетями (MeshCore, …) и мессенджерами (Telegram, …):
сообщения из мессенджера уходят в эфир, а принятое из эфира зеркалится подписчикам.
Поддерживает несколько физических LoRa-нод, каждую со своими каналами.

## Быстрый старт

Готовые образы публикуются в GHCR и в Yandex Cloud Container Registry

`config.example.yaml` — рабочий конфиг с заглушками. В `config.yaml` заполнить:

- `connection.device_id` (или `port`/`host`/`address` — зависит от способа подключения)
- `endpoints.*.channel_name`
- `chat_id` в `rooms[].subscribers`

Telegram token — отдельно, через переменную окружения (см. ниже). Где взять значения
для LoRa — **[Подключение MeshCore](docs/install/meshcore.md)**.

### Вариант 1: одной командой (`docker run`)

```bash
curl -o config.yaml https://raw.githubusercontent.com/Flexlug/LoRa-Bridge/main/config.example.yaml
```

Откройте `config.yaml`: перепишите секцию `lora:` под своё устройство (см. выше) и впишите
`chat_id`. Telegram token в файле трогать не нужно — он уже ссылается на `${TG_BOT_TOKEN}`
и подставится из `-e` ниже:

```bash
docker run -d --name lora-bridge --restart unless-stopped \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -v lora-bridge-data:/data \
  -e TG_BOT_TOKEN=123456:AAABBB... \
  ghcr.io/flexlug/lora-bridge:latest
```

Если `ghcr.io` недоступен, используйте тот же образ из Yandex Cloud —
достаточно заменить последнюю строку:

```bash
  cr.yandex/crpdjbirs0ru9gdbo2l8/flexlug/lora-bridge:latest
```

Для проброса LoRa-устройства добавьте `--device /dev/ttyUSB0:/dev/ttyUSB0` (путь — из
`connection.port` конфига).

### Вариант 2: `docker compose`

```bash
curl -o config.yaml https://raw.githubusercontent.com/Flexlug/LoRa-Bridge/main/config.example.yaml
```

Откройте `config.yaml`: перепишите секцию `lora:` под своё устройство (см. выше) и впишите
`chat_id`. Telegram token в файле трогать не нужно — он уже ссылается на `${TG_BOT_TOKEN}`,
а прокинет его `env_file` ниже:

```bash
echo "TG_BOT_TOKEN=123456:AAABBB..." > .env
```

```yaml
# docker-compose.yml
services:
  lora-bridge:
    image: ghcr.io/flexlug/lora-bridge:latest
    # если ghcr.io заблокирован — переключитесь на второй registry:
    # image: cr.yandex/crpdjbirs0ru9gdbo2l8/flexlug/lora-bridge:latest
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - lora-data:/data
    # devices:                        # проброс LoRa-устройства
    #   - "/dev/ttyUSB0:/dev/ttyUSB0"
    stop_grace_period: 10s

volumes:
  lora-data:
```

```bash
docker compose up -d
```

Собрать образ из исходников самостоятельно —
в **[гиде для разработчиков](docs/contributing/index.md)**.

## Документация

Полная документация — в [docs/](docs/index.md) (или собирается как сайт через `uv run mkdocs serve`):

- **[Установка и быстрый старт](docs/install/index.md)** — от чистой системы до запущенного моста
- **[Подключение MeshCore](docs/install/meshcore.md)** — USB / serial / TCP / BLE, каналы
- **[Настройка Telegram-бота](docs/install/telegram.md)** — BotFather, chat_id, темы
- **[Конфигурация](docs/config/index.md)** — справочник всех полей `config.yaml`
- **[Для разработчиков](docs/contributing/index.md)** — архитектура, тесты, линт
- **[Архитектура](docs/ARCHITECTURE.md)** — порты и абстракции, доменная модель, диаграммы
