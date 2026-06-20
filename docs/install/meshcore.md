# Подключение MeshCore-устройства

Драйвер MeshCore поддерживает четыре варианта физического подключения. Выберите тот,
который соответствует вашему железу, и подставьте параметры в секцию `connection`
ноды.

## USB

Самый частый сценарий: устройство подключено напрямую кабелем USB.

```yaml
lora:
  - id: meshcore-1
    type: meshcore
    connection:
      type: usb
      device_id: "0403:6015"
    # ... endpoints и policies
```

**Где найти `device_id` (формат `VID:PID`)**:

* **Linux**: `lsusb` → найдите свою плату → формат `VID:PID`.
* **macOS**: System Information → USB → ищите Vendor ID / Product ID.
* **Windows**: Device Manager → ваше устройство → Properties → Details → Hardware IDs.

!!! warning "Linux: права на serial-устройство"
    На Linux пользователь должен быть в группе `dialout`, иначе порт не откроется:

    ```bash
    sudo usermod -aG dialout $USER
    # затем перелогиниться
    ```

    Если возможности добавить пользователя в группу нет, можно временно дать
    права прямо на handle устройства:

    ```bash
    sudo chmod 666 /dev/ttyUSB0     # путь подставьте свой
    ```

    Важно: это сбрасывается на каждом переподключении устройства — после
    каждого `unplug/plug` команду нужно повторять. Для постоянного
    решения используйте `dialout`.

## Serial

Используйте этот вариант, если адресуете устройство по конкретному serial-порту
(а не по `VID:PID`):

```yaml
connection:
  type: serial
  port: "/dev/ttyUSB0"
```

**Как узнать `port`**:

* **Linux**: `ls /dev/tty*` до и после подключения устройства — новый файл и есть ваш
  порт. Обычно это `/dev/ttyUSB0` или `/dev/ttyACM0`.
* **macOS**: то же самое, или загляните в `ls /dev/cu.*`.
* **Windows**: Device Manager → Ports (COM & LPT) → найдите запись типа `COM3`.

## TCP

Если ваше MeshCore-устройство (физическая нода с антенной) подключено к локальной
сети и доступно по IP — например, через Ethernet/Wi-Fi-модуль или внешний bridge:

```yaml
connection:
  type: tcp
  host: "192.168.1.100"
  port: 5000
```

`host:port` — адрес и порт ноды в сети.

## BLE

Bluetooth Low Energy:

```yaml
connection:
  type: ble
  address: "AA:BB:CC:DD:EE:FF"
```

`address` — MAC-адрес устройства, обычно есть на стикере или в приложении MeshCore.

## Несколько каналов на одной ноде

Одна нода обслуживает любое количество каналов — добавьте их в `endpoints` как
отдельные ключи:

```yaml
endpoints:
  general:           # имя — на него ссылается rooms[].lora.endpoint
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

Подробное описание полей всех типов эндпоинтов — в **[справочнике lora](../config/lora.md)**.

## Поведение при потере связи

Если устройство не отвечает при старте или отключается в процессе работы, мост
**не падает**: он переходит в режим ожидания и периодически пробует переподключиться
с экспоненциальным backoff (1 → 2 → 4 → … → 60 секунд). После восстановления связи
работа возобновляется автоматически.

В логах это выглядит так:

```
WARNING нода 'meshcore-1': serial /dev/ttyUSB0 — нет ответа от устройства — ожидаю reconnect
...
INFO    нода 'meshcore-1' переподключена
```

## Диагностика (log_raw_rx)

Если нужно посмотреть, какие пакеты устройство принимает из эфира, включите
подробное логирование входящих RF-событий:

```yaml
lora:
  - id: meshcore-1
    log_raw_rx: true   # по умолчанию false — производит очень много строк
    # ...
```

!!! warning
    `log_raw_rx: true` в активной сети генерирует сотни строк в минуту. Включайте
    только для разовой диагностики, затем возвращайте `false`.

---

→ Дальше: **[Настройка Telegram-бота](telegram.md)**, **[Конфигурация](../config/index.md)**.
