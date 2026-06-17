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

## Serial

Если устройство уже видно как serial-порт (некоторые платы или USB-серийные
конвертеры):

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

Если companion-сервер MeshCore запущен по сети (на отдельной машине, в эмуляторе
или в контейнере):

```yaml
connection:
  type: tcp
  host: "192.168.1.100"
  port: 5000
```

`host:port` — адрес и порт companion-сервера. Если он у вас локально, используйте
`127.0.0.1`.

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

---

→ Дальше: **[Настройка Telegram-бота](telegram.md)**, **[Конфигурация](../config/index.md)**.
