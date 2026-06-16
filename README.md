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

Текущий статус: **ядро реализовано и покрыто тестами** (60 тестов) — маршрутизация,
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

## Первичная настройка (без Docker)

### Требования

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (рекомендуется) или pip
- Устройство с прошивкой MeshCore (USB, serial, TCP или BLE)
- Telegram-бот (создаётся через @BotFather)

### 1. Установка

```bash
uv sync
# или: pip install -e ".[dev]"
```

### 2. Настройка Telegram-бота

1. Найдите **@BotFather**, выполните `/newbot` — получите `token`.
2. **Обязательно** отключите privacy mode: `/setprivacy` → выберите бота → `Disable`.  
   Без этого бот не видит сообщения участников группы, только команды.
3. Добавьте бота в нужные чаты, назначьте права администратора.
4. Узнайте `chat_id`: перешлите любое сообщение из чата боту [@userinfobot](https://t.me/userinfobot),
   или после первого сообщения в чат вызовите `getUpdates` через Bot API.

### 3. Конфиг

```bash
cp config.example.yaml config.yaml
```

Конфиг поддерживает подстановку `${ENV_VAR}` — секреты держите в переменных окружения, не в файле:

```bash
export TG_BOT_TOKEN="123456:AAABBB..."
export MC_EMERGENCY_SECRET="ваш-psk"   # только если используете private-эндпоинт
export MC_OPS_PW="пароль"             # только если используете room_server
```

#### Подключение к MeshCore-ноде

Конфиг работает только с прошивкой **MeshCore** (`type: meshcore`). Выберите тип физического
подключения в поле `connection`:

| Тип | Поле | Как узнать значение |
|-----|------|---------------------|
| USB | `device_id: "VID:PID"` | `lsusb` на Linux; Device Manager → Properties → Details на Windows |
| Serial | `port: "/dev/ttyUSB0"` | `ls /dev/tty*` до и после подключения |
| TCP | `host` + `port` | IP-адрес и порт companion-сервера MeshCore |
| BLE | `address: "AA:BB:CC:DD:EE:FF"` | MAC-адрес устройства |

Примеры:

```yaml
# USB (наиболее распространённый вариант)
connection:
  type: usb
  device_id: "0403:6015"

# Serial
connection:
  type: serial
  port: "/dev/ttyUSB0"

# TCP (companion запущен как сервер)
connection:
  type: tcp
  host: "192.168.1.100"
  port: 5000

# BLE
connection:
  type: ble
  address: "AA:BB:CC:DD:EE:FF"
```

На Linux пользователь должен состоять в группе `dialout` для доступа к устройству:
```bash
sudo usermod -aG dialout $USER   # затем перелогиниться
```

#### Типы каналов MeshCore (`endpoints`)

Одна физическая нода может обслуживать несколько каналов одновременно — каждый объявляется
отдельной записью в словаре `endpoints`. Ключ (например `general`, `emergency`) — это имя,
на которое ссылается секция `rooms`.

---

**`public` — публичный канал**

Общий PSK, виден всем участникам сети. Сообщения передаются flood-способом без подтверждения (без ACK).
Подходит для общего чата.

```yaml
lora:
  - id: meshcore-1
    type: meshcore
    connection:
      type: usb
      device_id: "0403:6015"
    endpoints:
      general:
        type: public
        channel_name: "General"    # имя канала из вкладки Channels в приложении MeshCore
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
```

---

**`private` — приватный канал**

Канал с отдельным PSK (`secret`). Flood без ACK, но доступен только тем, кто знает секрет.
Подходит для закрытых рабочих групп внутри сети.

```yaml
lora:
  - id: meshcore-1
    type: meshcore
    connection:
      type: usb
      device_id: "0403:6015"
    endpoints:
      ops:
        type: private
        channel_name: "Emergency"  # имя канала из вкладки Channels в приложении MeshCore
        secret: ${MC_OPS_SECRET}   # PSK канала из настроек MeshCore
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
```

---

**`room_server` — Room Server**

Адресная доставка через Room Server: требует `pubkey` сервера и опционального гостевого пароля.
В отличие от public/private, здесь есть реальный ACK и backfill (история сообщений при
переподключении). Подходит, когда нужна гарантия доставки.

```yaml
lora:
  - id: meshcore-1
    type: meshcore
    connection:
      type: tcp
      host: "192.168.1.100"
      port: 5000
    endpoints:
      secure-room:
        type: room_server
        pubkey: "a1b2c3d4e5f6..."   # публичный ключ Room Server из приложения MeshCore
        password: ${MC_ROOM_PW}     # гостевой пароль; опустите поле, если вход без пароля
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
```

---

**Несколько каналов на одной ноде**

Эндпоинты не исключают друг друга — одна нода может одновременно работать с несколькими каналами:

```yaml
endpoints:
  general:
    type: public
    channel_name: "General"
  emergency:
    type: private
    channel_name: "Emergency"
    secret: ${MC_EMERGENCY_SECRET}
  ops:
    type: room_server
    pubkey: "a1b2c3..."
    password: ${MC_OPS_PW}
```

#### Комнаты (`rooms`)

Комната связывает один LoRa-эндпоинт с подписчиками. Допустимо два варианта:

**1 LoRa + мессенджеры** — сообщения из Telegram уходят в эфир, из эфира зеркалируются в чат:

```yaml
rooms:
  - lora:
      node: meshcore-1
      endpoint: general
    subscribers:
      # General чата
      - transport: telegram-main
        chat: "-1001234567890"
      # конкретный топик
      - transport: telegram-main
        chat: "-1001234567890"
        topic: "42"
```

**2 LoRa** — мостинг между двумя радиосетями без мессенджеров:

```yaml
rooms:
  - lora:
      node: meshcore-1
      endpoint: general
    subscribers:
      - lora:
          node: meshcore-2
          endpoint: relay
```

### 4. Запуск

```bash
LORA_BRIDGE_CONFIG=config.yaml lora-bridge
```

Все настройки приложения задаются переменными окружения:

| Переменная | По умолчанию | Назначение |
|-----------|--------------|-----------|
| `LORA_BRIDGE_CONFIG` | `config.yaml` | Путь к файлу конфига |
| `LORA_BRIDGE_DB` | `lora_bridge.sqlite` | Путь к SQLite-журналу намерений |
| `LORA_BRIDGE_LOG` | `INFO` | Уровень логов (`DEBUG` / `INFO` / `WARNING`) |

### 5. Проверка

При успешном старте в логах появятся примерно такие строки:

```
INFO:lora_bridge.wiring:транспорт ноды 'meshcore-1' создан (3 эндпоинтов)
INFO:lora_bridge.transports.meshcore.transport:нода 'meshcore-1' подключена: USB 0403:6015 (/dev/ttyACM0)
INFO:lora_bridge.transports.meshcore.transport:нода 'meshcore-1' запущена: 3 эндпоинтов активно
INFO:lora_bridge.transports.telegram.transport:Telegram-транспорт 'telegram-main': бот @YourBot (id=123456789) подключён
```

Отсутствие этих строк — первый сигнал для диагностики:

- **USB-устройство не найдено** — проверьте `device_id` через `lsusb`; на Linux пользователь
  должен быть в группе `dialout` (`sudo usermod -aG dialout $USER`, затем перелогиниться).
- **Telegram не отвечает** — проверьте `token` и доступность `api.telegram.org`.
- **Ошибка валидации конфига** — запустите с `LORA_BRIDGE_LOG=DEBUG` и сверьтесь с `config.example.yaml`.

---

## Первичная настройка (Docker)

> TODO

---

## Документация

- [Архитектура](docs/ARCHITECTURE.md) — порты и абстракции, доменная модель,
  ядро (Router/Bridge), реактивные потоки, конвейер обработки, mermaid-диаграммы
  и подробная «прожарка» корнер-кейсов.
