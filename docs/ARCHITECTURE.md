# MeshCore-Bridge — Архитектура

> Дуплексный мост между LoRa-mesh сетью (MeshCore / Meshtastic / Reticulum) и
> мессенджерами (MVP — Telegram). Документ предназначен как рабочее ТЗ: по нему
> можно реализовать систему «с нуля».

## Содержание

1. [Цели и границы](#1-цели-и-границы)
2. [Ключевые архитектурные решения](#2-ключевые-архитектурные-решения)
3. [Топология: клиент-сервер](#3-топология-клиент-сервер)
4. [Доменная модель](#4-доменная-модель)
5. [Порты и абстракции](#5-порты-и-абстракции)
6. [Ядро: commit-очередь и фан-аут](#6-ядро-commit-очередь-и-фан-аут)
7. [Где живёт rate limit](#7-где-живёт-rate-limit)
8. [Реактивные потоки в asyncio](#8-реактивные-потоки-в-asyncio)
9. [Конвейер обработки сообщения](#9-конвейер-обработки-сообщения)
10. [Статусы доставки и фидбек](#10-статусы-доставки-и-фидбек)
11. [Жизненный цикл соединения](#11-жизненный-цикл-соединения)
12. [Конфигурация](#12-конфигурация)
13. [Структура пакета](#13-структура-пакета)
14. [🔥 Прожарка: косяки и корнер-кейсы](#14-прожарка-косяки-и-корнер-кейсы)
15. [Этапы реализации](#15-этапы-реализации)

---

## 1. Цели и границы

**Что делаем (MVP):**

- Принимаем поток сообщений из заданного **хештег-канала LoRa** (MeshCore) и
  публикуем их в **каналы/темы мессенджеров** (Telegram), один-ко-многим.
- Принимаем сообщения из мессенджеров (с поддержкой тем/threads), отправляем их
  в LoRa-канал и зеркалим остальным подписчикам — **дуплекс**.
- LoRa-узел **ровно один** и выступает единым источником правды (commit-log).

**Чего сознательно НЕ делаем (вообще, не только в MVP):**

- **Работаем исключительно с текстом.** Вложения (фото/голос/файлы) не
  поддерживаем — ни в MVP, ни дальше: медиа есть только в Reticulum, что вне
  нашего скоупа. Сервисные события чатов (вступления, пины, правки) игнорируем.
- Не гарантируем доставку *получателю* в LoRa (канальные сообщения MeshCore
  ACK не имеют — см. §2, AD-5).
- **Модерацию не делаем** — оставляем модераторам Telegram-канала (в крайнем
  случае кикают участника). Allow-list/анти-абьюз — PostMVP (см. §14, E1).
- Без веб-UI/админки — только конфиг-файл.

**Ключевые ограничения предметной области (определяют всю архитектуру):**

| Сторона | Ограничение | Следствие |
|---|---|---|
| LoRa | Узкий канал: десятки байт на пакет, строгий **duty cycle** (EU868 1%) | rate-limit + проверка размера (reject, без усечения) + admission control |
| LoRa | Канальные сообщения без ACK, без backfill после простоя | at-most-once; commit = принятие узлом / ACK якоря (§5.1), не факт эфира |
| LoRa | Возможны дубликаты пакетов (mesh-ретрансляция) | обязательная дедупликация |
| LoRa | Радио half-duplex, один узел = одна очередь TX | сериализованный egress-воркер в ядре |
| LoRa | Нет тем/threads, имена узлов короткие | темы — на стороне мессенджера (конфиг адаптера); маркировка отправителя |
| Telegram | Высокий объём, темы, длинные сообщения (4096) | источник «потопа» для LoRa; асимметрия |
| Telegram | Бот по умолчанию не видит сообщения группы (privacy mode) | требуется отключить privacy mode |
| Оба | Соединение нестабильно (BLE/Serial/TCP, long-polling) | resilience: reconnect, resume |

---

## 2. Ключевые архитектурные решения

| # | Решение | Обоснование |
|---|---|---|
| AD-1 | **Гексагональная архитектура (ports & adapters)** | Ядро не знает про MeshCore/Telegram. Транспорт = адаптер к порту `Transport`. |
| AD-2 | **Единый порт `Transport`** для LoRa и мессенджеров; различия — через `Capabilities` | Не плодим параллельные иерархии; ядро не ветвится по типу транспорта. |
| AD-3 | **Клиент-сервер**: сервер = ядро, клиенты = транспорты; **LoRa-клиент один** | Ядро — шина и арбитр; естественно даёт 1-ко-многим. |
| AD-4 | **LoRa как commit-log**: сообщение из мессенджера сначала уходит в эфир, и только по **TX-done** зеркалится остальным | Единый источник правды и порядок; мессенджеры — зеркала канала, а не отдельные чаты. |
| AD-5 | **Commit зависит от типа эндпоинта** (§5.1): `public`/`private` — по `MSG_OK` (узел принял) + airtime-pace; `room_server` — по delivery-ACK (`0x82`) | MeshCore **не даёт TX-done для каналов**; реальное подтверждение есть только у direct (room server) |
| AD-6 | **Сериализованная commit-очередь** + один egress-воркер с таймаутом TX-done | Один радиоузел физически сериализует TX; синхронный gated fan-out. |
| AD-7 | **Статус-фидбек в мессенджеры** (реакция-индикатор) | Дуплекс = поток сообщений + поток статусов; пользователь видит судьбу сообщения. |
| AD-8 | **Egress rate-limit + drop + уведомление** на стороне LoRa | Защита эфира/duty cycle; см. §7. |
| AD-9 | **Loop-guard + dedup** обязательны | Мультикаст + RX-эхо собственных TX создают петли/дубли. |
| AD-10 | **Обязательный префикс `[тип:ник]`** в каждом сообщении из мессенджера в LoRa; тип опускается, если в комнату пишет ровно один мессенджер | Получатель в эфире должен видеть автора и источник; ник — не опционален. |
| AD-11 | **Размер — all-or-nothing: НЕ усекаем.** Если `префикс+текст` не влезает в `max_text_bytes` — сразу `REJECTED(TOO_LONG)` обратно в мессенджер | Молчаливая потеря части слов пользователя хуже явной ошибки; усечение искажает смысл. |

> **Соглашение о термине «TX-done».** В тексте ядра ниже «TX-done» используется как
> абстрактное имя **события commit** (момент, когда `lora.send()` резолвится). Его
> конкретная реализация в MeshCore — **не** факт передачи в эфир, а `MSG_OK`
> (public/private) либо delivery-ACK room server'а (room_server); детали — §5.1.
> Ядру механизм безразличен: оно ждёт «commit ok / fail».

---

## 3. Топология: клиент-сервер

```mermaid
flowchart TB
    subgraph Clients["Клиенты"]
      L["LoRa-клиент\n(РОВНО один)"]
      T1["Telegram-клиент"]
      T2["Discord-клиент (future)"]
    end

    subgraph Server["СЕРВЕР = ЯДРО"]
      direction TB
      HUB["Шина (ingress fan-in)"]
      Q["Commit-очередь\n(сериализованная)"]
      W["Egress-воркер\n(один, gated на TX-done)"]
      FO["Fan-out + статусы"]
    end

    L  <-->|"#channel msgs + TX-done"| HUB
    T1 <-->|"msgs + статусы"| HUB
    T2 <--> HUB
    HUB --> Q --> W --> FO
    FO -. "mirror" .-> T1
    FO -. "mirror" .-> T2
    W  -. "TX" .-> L
```

Двусторонние стрелки = дуплекс на каждом ребре. Один LoRa-клиент обслуживает все
мессенджеры (мультикаст RX в ядре, §8). «1-ко-многим» — свойство топологии:
к одному LoRa-каналу подписано N мессенджер-эндпоинтов.

**Два пути сообщения (асимметрия источников):**

- **Из мессенджера** → ядро → **commit-очередь** → TX в узел → ждём TX-done →
  фан-аут остальным мессенджерам + статус источнику.
- **Из LoRa** → RX → dedup + loop-guard → фан-аут **всем** мессенджерам
  **в обход очереди** (сообщение уже в эфире, коммитить нечего).

---

## 4. Доменная модель

```mermaid
classDiagram
    class ChannelRef {
      +str transport_id
      +str channel
    }
    class Identity {
      +str display_name
      +str transport_uid
    }
    class Message {
      +str id
      +ChannelRef source
      +Identity sender
      +str text
      +Optional~datetime~ timestamp
      +Optional~str~ origin_tag
    }
    class DeliveryStatus {
      <<enum>>
      PENDING
      TRANSMITTING
      SENT
      REJECTED
      FAILED
    }
    Message --> ChannelRef : source
    Message --> Identity : sender
```

Одной модели `Message` хватает на оба направления: при зеркалировании в другие
каналы у неё просто другой `source`. Отдельный `OutboundMessage` не нужен —
адаптер сам решает, как отрисовать `sender + text` (мессенджер) или превратить в
плоскую строку (LoRa, см. ниже).

```python
# domain/models.py  (ключевые типы)
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import datetime as dt


@dataclass(frozen=True)
class ChannelRef:
    transport_id: str
    channel: str                     # opaque id эндпоинта; топик — забота адаптера


@dataclass(frozen=True)
class Identity:
    display_name: str
    transport_uid: str


@dataclass(frozen=True)
class Message:
    id: str                          # стабильный id транспорта (для dedup)
    source: ChannelRef
    sender: Identity
    text: str
    # Время источника, если извлекается. Для LoRa часто None — не выдумываем:
    # при зеркалировании в другие каналы исходный timestamp всё равно не передать.
    timestamp: Optional[dt.datetime] = None
    origin_tag: Optional[str] = None  # loop-guard (только LoRa-путь)


class DeliveryStatus(Enum):
    PENDING = "pending"            # принято в commit-очередь
    TRANSMITTING = "transmitting"  # взято воркером, отдано узлу
    SENT = "sent"                  # TX-done подтверждён узлом (COMMIT, терминальный успех)
    REJECTED = "rejected"          # admission отклонил (см. RejectReason)
    FAILED = "failed"              # нет TX-done в таймаут / ошибка узла
    UNKNOWN = "unknown"            # рестарт во время TRANSMITTING — ушло ли, неизвестно (§11.1)


class RejectReason(Enum):
    TOO_LONG = "too_long"          # префикс+текст > max_text_bytes (НЕ усекаем)
    RATE_LIMIT = "rate_limit"      # эфир перегружен (token-bucket / очередь полна)
    TTL_EXPIRED = "ttl_expired"    # протухло в очереди до отправки


@dataclass(frozen=True)
class RateSpec:
    msgs_per_window: int
    window_seconds: float
    burst: int = 1


@dataclass(frozen=True)
class Capabilities:
    max_text_bytes: int
    egress_rate: Optional[RateSpec] = None
    supports_status_feedback: bool = False   # умеет показать статус (реакция)
    emits_tx_done: bool = False              # узел отдаёт TX-done (commit)
```

> **Где живёт топик.** Ядро топиками не оперирует. Нужный топик задаётся в
> **конфиге** мессенджер-адаптера: адаптер по нему фильтрует входящий поток
> (берёт только сообщения этого топика) и в него же шлёт исходящие. `channel` в
> `ChannelRef` — opaque-идентификатор эндпоинта; ядро его не разбирает.

### Сборка LoRa-нагрузки (префикс)

Сообщение из мессенджера разворачивается в LoRa в плоскую строку `<префикс><текст>`,
где префикс несёт **автора** и (условно) **тип мессенджера**:

```
несколько мессенджеров пишут в комнату → "[TG:Alex] привет"
только один мессенджер пишет в комнату → "[Alex] привет"
```

```python
def build_lora_text(msg: Message, room: Room, tag: str, fmt: LabelFormat) -> str:
    nick = clip_utf8(msg.sender.display_name, fmt.max_nick_bytes)  # ник усекаем — ок
    # тип опускаем, если в комнату пишет ровно один мессенджер (AD-10)
    if room.writable_messenger_count > 1 and fmt.include_type:
        label = f"[{tag}:{nick}] "      # "[TG:Alex] "
    else:
        label = f"[{nick}] "            # "[Alex] "
    return label + msg.text             # ТЕКСТ не трогаем (AD-11); байты считает вызывающий
```

- **Префикс ест бюджет** `max_text_bytes` (он часть полезной нагрузки): бюджет
  текста = `max_text_bytes − len(label_bytes)`.
- **Ник усекаем** до `max_nick_bytes` (это метаданные); **текст пользователя —
  никогда**. Если даже усечённый префикс не влезает — `REJECTED(TOO_LONG)`.
- **Тег типа** (`TG`/`DC`/…) берётся из конфига транспорта-источника
  (`messengers[].tag`/`kind`), а не из текста пользователя (защита от подмены, D5).
- Решение «один/несколько мессенджеров» — на уровне **комнаты**
  (`writable_messenger_count`).

---

## 5. Порты и абстракции

И LoRa-клиент, и мессенджер реализуют один протокол `Transport`. Дуплекс теперь
включает **обратный канал статусов** (`report_status`) — для отрисовки реакции.

```python
# domain/ports.py
from typing import AsyncIterator, Optional, Protocol, runtime_checkable
from .models import (Capabilities, ChannelRef, Message,
                     DeliveryStatus, RejectReason)


@runtime_checkable
class Transport(Protocol):
    id: str
    capabilities: Capabilities

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    # Исходящая сторона: одна модель Message на оба направления.
    # LoRa-адаптер транслирует msg.text как есть (ядро уже собрало префикс);
    # мессенджер-адаптер сам форматирует sender + text.
    # Для LoRa send() РЕЗОЛВИТСЯ по TX-done узла, а не по записи в линк (AD-5/AD-6).
    async def send(self, target: ChannelRef, msg: Message) -> "SendResult": ...

    # Горячий мультикаст-поток входящих (см. §8).
    def subscribe(self) -> AsyncIterator[Message]: ...

    # Обратный канал статусов. No-op, если supports_status_feedback=False.
    # reason заполняется только для REJECTED (см. RejectReason).
    async def report_status(self, origin: ChannelRef, message_id: str,
                            status: DeliveryStatus,
                            reason: "Optional[RejectReason]" = None) -> None: ...
```

Адаптеры (примеры `capabilities`):

```python
# MeshCore: медленный коммит-источник; TX-done НЕТ (см. §5.1)
MeshCoreTransport.capabilities = Capabilities(
    max_text_bytes=150,                   # реальный байтовый бюджет узла (~133 симв / 163 Б)
    egress_rate=RateSpec(6, 60),          # консервативно под duty cycle + airtime-pace
    supports_status_feedback=False, emits_tx_done=False)

# Telegram: быстрый, умеет реакции-статусы
TelegramTransport.capabilities = Capabilities(
    max_text_bytes=4096,
    egress_rate=RateSpec(20, 60, burst=20),
    supports_status_feedback=True, emits_tx_done=False)
```

> **Что значит `send()` для MeshCore.** TX-done у канальных сообщений в MeshCore
> **не существует** (`send_chan_msg` возвращает лишь `MSG_OK` = узел принял к
> передаче; delivery-ACK есть только у direct-сообщений). Поэтому `send()`
> резолвится **по режиму доставки** — см. §5.1.

### 5.1. Контракт MeshCore-адаптера

Адаптер реализует порт `Transport` поверх библиотеки `meshcore` (companion-протокол
по Serial/BLE/TCP). Прожарка контракта против реального протокола вскрыла ряд
жёстких ограничений — ниже они и то, как адаптер их закрывает.

**Главное: commit зависит от ТИПА эндпоинта, и типов три.** MeshCore различает
по механике каналы (flood, без ACK) и room server (direct+login, с ACK). Чтобы это
не было «секретным знанием в конфиге» (наличие `pubkey` ⇒ room server), тип задаётся
**явным дискриминатором** `type`. Вся MeshCore-специфика (индексы слотов, деривация
PSK, login-команда, ACK-фрейм, `CONTACT` vs `CHANNEL` на приёме) спрятана в адаптере.
Имена типов взяты из словаря MeshCore-приложения, чтобы настраивал и непогружённый:
`public` (Public-канал) · `private` (Channel со своим secret) · `room_server` (Room Server).

| `type` | MeshCore | адресация | вход | отправка | **commit / ✅** | RX-событие | backfill |
|---|---|---|---|---|---|---|---|
| `public` | Public channel | слот-индекс, PSK по умолч. | — | `send_chan_msg` | `MSG_OK` = «узел принял к передаче» | `CHANNEL_MSG_RECV` | нет |
| `private` | Channel (+secret) | слот-индекс + свой PSK | знать PSK (`secret`) | `send_chan_msg` | `MSG_OK` (то же) | `CHANNEL_MSG_RECV` | нет |
| `room_server` | Room Server | **pubkey** контакта | `send_login(pubkey, pwd)` | `send_msg_with_retry` | **`ACK 0x82`** = «room server подтвердил приём» | `CONTACT_MSG_RECV` | **есть** (store-and-forward) |

```mermaid
flowchart TB
    M["Сообщение в LoRa-эндпоинт"] --> Q{"endpoint.type?"}
    Q -- public / private --> B["send_chan_msg(idx, text)\ncommit = MSG_OK + airtime-pace\n✅ = «принято узлом» (доставка не гарантирована)"]
    Q -- room_server --> A["(login при старте) → send_msg_with_retry(pubkey, text)\ncommit = ACK 0x82 в таймаут\n✅ = «room server подтвердил», timeout → FAILED"]
```

- **public / private**: flood fire-and-forget (оба — MeshCore-каналы: слот+PSK,
  различие лишь в ключе). `✅` честно означает лишь «узел принял к передаче»; что
  дальше в эфире — забота владельца ноды. Доставки/ACK у flood нет в принципе
  ([FAQ](https://docs.meshcore.io/faq/)).
- **room_server**: direct-сообщение залогиненному room server'у. Реальный ACK = commit
  **и** store-and-forward (закрывает F2: участники вне зоны получат backfill). Цена:
  доходит до room server и его участников, а не до произвольных слушателей; нужен
  его advert/`pubkey` в контактах и guest-пароль (`password`; пусто → read-only).

**Корнер-кейсы контракта (R-список):**

| # | Реальность MeshCore | Как закрываем |
|---|---|---|
| R1 | TX-done нет; commit = `MSG_OK` (public/private) или `ACK 0x82` (room_server) | по типу эндпоинта (см. выше); `emits_tx_done=False` |
| R2 | RX — pull-дренаж: `MESSAGES_WAITING` → надо вычитывать; есть баг [#1232](https://github.com/meshcore-dev/MeshCore/issues/1232) (`CHANNEL_MSG_RECV` не фаерится) | включаем `start_auto_message_fetching()`; страховочный `get_msg()` + health-check; иначе RX-буфер узла переполнится (backpressure на приём) |
| R3 | Канал = **слот-индекс 0–7** с PSK, не имя; room_server = контакт по **pubkey** | при `start()`: для public/private resolve `name→index` (или провизион); для room_server — найти контакт по pubkey (advert) + `send_login`. Public = индекс 0 |
| R4 | Очередь узла полна → `ERR_CODE_TABLE_FULL (3)` | адаптер → `SendResult(busy/retry)`, **не** `FAILED`; egress-лимитер ① держим ниже ёмкости |
| R5 | Лимит ~133 симв / `MAX_CHANNEL_DATA=163 Б`; потолок **байтовый** | `max_text_bytes` из реального бюджета (~150), TOO_LONG-чек по байтам (валидирует D2: кириллица!) |
| R6 | timestamp в send/RX зависит от часов узла | `set-time` при `start()`; RX-timestamp `Optional`/untrusted |
| R7 | Версии фреймов: `CHANNEL_MSG_RECV 0x08` vs `_V3 0x11` | фиксируем версию протокола из device-info, логируем; полагаемся на абстракцию либы |
| R8 | Self-echo: узел свой TX обычно не отдаёт на RX (+ node-dedup); room server может эхнуть наш пост назад | риск низкий; core loop-guard/dedup — страховка (особенно для room-эха) |
| R9 | `auto_reconnect`, но `max_reconnect_attempts` **конечно** | для долгоживущего моста — ∞ (или свой supervisor); после reconnect: re-drain, re-set-time, re-resolve index, **re-login в room_server** |
| R10 | BLE: PIN-пейринг, ОС-бондинг, нестабильность | для прода рекомендуем **USB/TCP**; BLE — опция |

**Жизненный цикл адаптера (per endpoint):**
- `start()`: connect (usb/serial/tcp) → `set-time`; для `public`/`private` — resolve `name→index` (или провизион); для `room_server` — найти контакт по `pubkey` + `send_login(pubkey, password)` → `start_auto_message_fetching()`.
- `subscribe()`: нормализует RX в единый `InboundMessage` (с автором): public/private ← `CHANNEL_MSG_RECV` нужного индекса, room_server ← `CONTACT_MSG_RECV` от pubkey room server'а; фильтрует `ADVERTISEMENT`/`ACK`/чужие контакты; дренаж-страховка против R2.
- `send()`: public/private → `send_chan_msg` (≈5с таймаут → `FAILED`; `TABLE_FULL` → busy) + airtime-pace; room_server → `send_msg_with_retry` + ждём `ACK 0x82` в таймаут.

> Источники: [Companion Protocol (docs)](https://docs.meshcore.io/companion_protocol/),
> [companion_protocol.md](https://github.com/meshcore-dev/MeshCore/blob/main/docs/companion_protocol.md),
> [FAQ](https://docs.meshcore.io/faq/), [Room Server guide](https://nodakmesh.org/meshcore/room-server),
> [meshcore_py](https://github.com/meshcore-dev/meshcore_py).

---

## 6. Ядро: commit-очередь и фан-аут

Ядро — сервер. Сообщения из мессенджеров проходят через **одну сериализованную
очередь** и **один egress-воркер**, потому что радиоузел физически передаёт по
одному пакету за раз.

```mermaid
sequenceDiagram
    participant U as User (Telegram-A)
    participant TGc as Telegram-клиент
    participant Q as Ядро: commit-очередь
    participant W as Ядро: egress-воркер (один)
    participant N as LoRa-узел
    participant Oth as Другие мессенджеры

    U->>TGc: пишет сообщение
    TGc->>Q: ingest(msg)
    Note over Q: сборка [тип:ник]+текст; размер? rate-limit? очередь?
    alt префикс+текст > лимита
        Q-->>TGc: REJECTED(TOO_LONG) 📏 (сразу, НЕ усекаем)
    else rate-limit / очередь полна
        Q-->>TGc: REJECTED ❌ (+ агрег. уведомление)
    else принято
        Q-->>TGc: PENDING 🕐
        W->>Q: pull (FIFO, по одному)
        W-->>TGc: TRANSMITTING 📤
        W->>N: send(out) — собранная строка [тип:ник]+текст
        alt TX-done в таймаут
            N-->>W: TX-done
            W-->>TGc: SENT ✅ (терминальный успех для пользователя)
            W->>Oth: mirror(msg) — best-effort, без статуса
        else таймаут / ошибка узла
            N--xW: нет TX-done
            W-->>TGc: FAILED ⚠️
        end
    end
```

> **Почему нет статуса MIRRORED.** Пользователю в TG не важно, долетело ли
> сообщение до Discord, а если в этот момент потеряно соединение с Discord —
> статус был бы «не ошибка, но и не успех» (лишняя неопределённость). Поэтому
> терминальный успех — `SENT` (ушло в эфир), а зеркалирование остальным —
> best-effort фоновая операция без отдельной реакции.

Путь из LoRa (в обход очереди):

```mermaid
sequenceDiagram
    participant N as LoRa-узел
    participant Lc as LoRa-клиент
    participant Core as Ядро
    participant M as Все мессенджеры
    N->>Lc: RX channel message
    Lc->>Core: ingest(msg, origin=LoRa)
    Core->>Core: dedup + loop-guard (не наш ли это TX-эхо?)
    alt новое чужое сообщение
        Core->>M: fan-out
    else дубль / собственное эхо
        Core->>Core: drop
    end
```

Скелет:

```python
# core/bridge.py
class Bridge:
    async def run(self):
        async with anyio.create_task_group() as tg:
            for t in self.transports.values():
                await t.start()
                tg.start_soon(self._consume, t)
            tg.start_soon(self._egress_worker)        # ОДИН воркер на узел

    async def _consume(self, t: Transport):
        async for msg in t.subscribe():
            if self._is_lora_origin(msg):
                # dedup и loop-guard — ТОЛЬКО для LoRa-пути (mesh-дубли, TX-эхо).
                # Для мессенджеров не нужно: дубли после рестарта ловит offset
                # в самом адаптере, «своё эхо» — фильтр по собственному bot-id.
                if not self.dedup.accept(msg):    continue
                if self.loop_guard.is_echo(msg):  continue
                await self._fanout_to_messengers(msg, exclude=None)
            else:
                await self._admit(msg)            # мессенджер → commit-очередь

    async def _admit(self, msg):
        room = self.rooms.for_source(msg.source)
        tag = self.transports[msg.source.transport_id].tag
        text = build_lora_text(msg, room, tag, self.label_fmt)   # префикс [тип:ник]

        # AD-11: НЕ усекаем текст. Не влезло — сразу разворачиваем обратно с ошибкой.
        over = len(text.encode("utf-8")) - self.lora.capabilities.max_text_bytes
        if over > 0:
            await self._reject(msg, RejectReason.TOO_LONG, detail=f"+{over} Б")
            return

        out = replace(msg, text=text)             # та же модель Message, готовый payload
        if not self.queue.offer(msg, out):        # bounded + rate-limit → RATE_LIMIT
            await self._reject(msg, RejectReason.RATE_LIMIT)
            return
        await self._set_status(msg, DeliveryStatus.PENDING)

    async def _reject(self, msg, reason, detail=""):
        await self._set_status(msg, DeliveryStatus.REJECTED, reason=reason)
        await self.notifier.note_reject(msg.source, reason, detail)   # debounce

    async def _egress_worker(self):
        async for msg, out in self.queue:                  # FIFO, payload уже собран
            if self.queue.is_stale(msg):                   # протух по TTL в очереди
                await self._reject(msg, RejectReason.TTL_EXPIRED)
                continue
            await self._set_status(msg, DeliveryStatus.TRANSMITTING)
            res = await with_timeout(self.lora.send(self.lora_target, out), self.tx_timeout)
            if res.ok:                                     # TX-done
                await self._set_status(msg, DeliveryStatus.SENT)
                # зеркалим остальным мессенджерам — best-effort, без статуса
                await self._fanout_to_messengers(msg, exclude=msg.source.transport_id)
            else:
                await self._set_status(msg, DeliveryStatus.FAILED)
```

---

## 7. Где живёт rate limit

Rate limit — **не одна точка, а вложенная лестница**. Наш прикладной лимитер —
самый верхний и единственный «добрый» (даёт обратную связь). Убери его — давление
проваливается вниз и проявляется **молча**.

```mermaid
flowchart TB
    A["Мессенджер → ядро\n(бурстовый вход; лимиты самого TG: ~20/мин, 429)"] --> B
    B["① Прикладной egress-лимитер + commit-очередь\n(НАШ контроль, даёт feedback)"] --> C
    C["② Companion-линк bridge↔узел\n(BLE/Serial/TCP: буфер, flow-control)"] --> D
    D["③ TX-очередь прошивки узла\n(малая глубина; переполнение → busy/drop)"] --> E
    E["④ Регуляторика: DUTY CYCLE (EU868 1%)\n+ dwell time (US915)"] --> F
    F["⑤ Радио: half-duplex + LBT/CAD\n(TX глушит RX; ждём свободный эфир)"] --> G
    G["⑥ Mesh: репитеры ретранслируют каждый пакет\n→ общий airtime-бюджет сети, коллизии"]
```

- **① Наш лимитер/очередь** — единственное место, где мы *хотим* упереться:
  только здесь можно вернуть статус `REJECTED` ❌ и уведомить пользователя.
- **③ TX-очередь узла** — мала (единицы–десятки пакетов); при переполнении либо
  `busy` (если есть feedback), либо **тихий drop**. Поэтому для `room_server`-эндпоинтов
  коммитим по ACK room server'а, а не по «приняли в очередь» (§5.1).
- **④ Duty cycle** — физический потолок. При дальнобойных пресетах time-on-air
  одного пакета ~**0.5–2 с**; при 1% это ≈ **1 пакет на ~100 с** устойчиво на
  под-диапазоне (бёрст до выжигания бюджета, потом тишина). US915 — без duty
  cycle, но dwell-time 400 мс.
- **⑤ Half-duplex** — пока узел разгребает backlog, он **глух к RX** → перегруз
  в сторону LoRa убивает обратное направление.
- **⑥ Mesh** — каждый пакет ретранслируют репитеры; перегруз бьёт по **всей
  сети**, не только по нам.

**Как это выглядит со стороны LoRa (наблюдатель на канале):** сообщения капают по
одному с растущей задержкой, частично не по порядку, **часть исчезает молча** — в
канале нет сигнала «rate limited». Именно поэтому статус и уведомление о дропе
отдаём на стороне мессенджера. Вывод: лимитер ① настраиваем **строго
консервативнее** реального бюджета узла — давление не должно доходить до ③–⑥.

---

## 8. Реактивные потоки в asyncio

«Реактивный поток» = `AsyncIterator[Message]`. RX-поток LoRa-клиента обязан быть
**горячим (multicast)**: один физический коннект к узлу, N подписчиков
(мессенджеры + метрики).

```mermaid
flowchart LR
    NODE["LoRa-узел\n(один физ. коннект)"] --> SRC["raw RX events"]
    SRC --> FANOUT{"Hub / broadcast"}
    FANOUT --> S1["subscribe() → ядро/фан-аут"]
    FANOUT --> S2["subscribe() → метрики/лог"]
```

- **Bounded buffer на подписчика** + drop-oldest + счётчик потерь (метрика).
- Cold→Hot: hub стартует при `start()`, а не при первом `subscribe()`.
- Egress (commit-очередь) — наоборот, **single-consumer**: одна сериализованная
  очередь, один воркер (§6), потому что радио одно.
- Без RxPY: async-генераторы + `anyio.create_memory_object_stream` дают
  merge/filter/buffer без тяжёлой зависимости; `Hub` инкапсулирует переход, если
  позже понадобятся сложные операторы.

---

## 9. Конвейер обработки сообщения

```mermaid
flowchart TB
    IN([Входящее Message]) --> ORI{"Источник?"}

    ORI -- "LoRa" --> DUP{"Dedup: новый?"}
    DUP -- нет --> D1[["drop (duplicate)"]]
    DUP -- да --> LG{"Loop-guard: наше TX-эхо?"}
    LG -- да --> D2[["drop (echo)"]]
    LG -- нет --> FOL["fan-out ВСЕМ мессенджерам\n(без очереди)"]

    ORI -- "мессенджер" --> CMP["Сборка LoRa-строки:\n• префикс [тип:ник] (тип опц.)\n• ник усекаем, текст — нет\n• считаем в байтах UTF-8"]
    CMP --> SZ{"префикс+текст ≤\nmax_text_bytes?"}
    SZ -- нет --> RJL["REJECTED(TOO_LONG) 📏\nразвернуть обратно с ошибкой\n(НЕ усекаем текст!)"]
    SZ -- да --> ADM{"Admission:\nrate-limit / очередь ok?"}
    ADM -- нет --> REJ["REJECTED(RATE_LIMIT) 🐢\n+ агрег. уведомление"]
    ADM -- да --> PEND["status=PENDING 🕐\n→ commit-очередь"]
    PEND --> WRK["egress-воркер: TRANSMITTING 📤\n(TTL-протухшее → REJECTED)"]
    WRK --> TX{"TX-done в таймаут?"}
    TX -- да --> SENT["status=SENT ✅\n+ зеркалим остальным (best-effort)"]
    TX -- нет --> FAIL["status=FAILED ⚠️"]
```

Детали стадий:

- **Dedup / loop-guard — только на LoRa-ветке** (это сознательно НЕ общий слой
  транспорта: делать «dedup по TX-эхо» для Telegram бессмысленно). Дубли TG после
  рестарта ловит offset в самом TG-адаптере, «своё эхо» — фильтр по bot-id.
- **Dedup** — TTL-LRU по `f"{transport_id}:{msg.id}"`; для LoRa без надёжного id
  ключ = `sha1(sender_uid + text)` с опорой на TTL-окно (timestamp не используем —
  его может не быть).
- **Loop-guard** — отбрасываем RX-сообщения, совпадающие с недавно
  оттранслированными нами (`origin_tag` + «recently-TX» множество с TTL).
- **Сборка строки** (синхронно, до очереди) — префикс `[тип:ник]` (тип опускаем
  при единственном мессенджере в комнате), считаем **в байтах UTF-8**.
- **Проверка размера** — `префикс+текст > max_text_bytes` ⇒ **`REJECTED(TOO_LONG)`
  немедленно** обратно в мессенджер (AD-11: текст НЕ усекаем; сообщение даже не
  входит в airtime-путь). В ошибке указываем превышение в байтах.
- **Admission** — bounded-очередь + token-bucket из `capabilities.egress_rate` +
  TTL (протухшее в очереди не отправляем, чтобы не тратить airtime на устаревшее).
- **Drop-notifier** — коалесцирует отказы за окно (напр. 60 с) → одно
  уведомление с числом отброшенных (не спамим).

---

## 10. Статусы доставки и фидбек

Дуплекс несёт два потока: сообщения и **статусы**. Originating-мессенджер
обновляет индикатор (реакцию) по мере прохождения commit-пайплайна.

```mermaid
stateDiagram-v2
    [*] --> REJECTED: TOO_LONG (до очереди)
    [*] --> PENDING: принято в очередь
    PENDING --> REJECTED: rate-limit / TTL
    PENDING --> TRANSMITTING: взято воркером
    TRANSMITTING --> SENT: TX-done (COMMIT, терминальный успех)
    TRANSMITTING --> FAILED: таймаут / ошибка узла
    REJECTED --> [*]
    FAILED --> [*]
    SENT --> [*]
```

`SENT` — терминальный успех для пользователя. Зеркалирование остальным
мессенджерам происходит после `SENT` фоном и **отдельного статуса не порождает**
(см. врезку в §6).

Маппинг на реакцию (Telegram `setMessageReaction`):

| Статус | Реакция | Смысл |
|---|---|---|
| PENDING | 🕐 | в очереди на эфир |
| TRANSMITTING | 📤 | передаётся узлом |
| SENT | ✅ | ушло в эфир (commit) |
| FAILED | ⚠️ | нет подтверждения от узла |
| UNKNOWN | ❓ | рестарт во время передачи — статус неизвестен (§11.1) |
| REJECTED | зависит от `reason` ↓ | не отправлено |

`REJECTED` несёт `RejectReason` — мессенджер показывает **конкретную** ошибку
(реакция + текстовый ответ), а не общий «отказ»:

| RejectReason | Реакция | Ответ пользователю |
|---|---|---|
| TOO_LONG | 📏 | «Сообщение длиннее лимита LoRa на N Б — сократите» |
| RATE_LIMIT | 🐢 | «Превышен лимит эфира, M сообщений отброшено» (агрег.) |
| TTL_EXPIRED | ⌛ | «Не успели отправить вовремя» |

Реализация: `report_status(origin, message_id, status, reason)` зовётся ядром на
каждом переходе; Telegram-адаптер мапит на реакцию исходного сообщения и (для
`REJECTED`/`FAILED`) на текстовый ответ-реплай. `message_id` ядро берёт из
входящего `Message.id` (корреляция). LoRa-адаптер `report_status` — no-op
(`supports_status_feedback=False`). `TOO_LONG` отдаётся **синхронно** в момент
приёма — пользователь видит ошибку мгновенно, сообщение в эфир не попадает.

---

## 11. Жизненный цикл соединения

```mermaid
stateDiagram-v2
    [*] --> Disconnected
    Disconnected --> Connecting: start()
    Connecting --> Connected: handshake ok
    Connecting --> Reconnecting: fail
    Connected --> Degraded: ошибки/таймауты
    Degraded --> Connected: восстановление
    Connected --> Reconnecting: разрыв
    Degraded --> Reconnecting: разрыв
    Reconnecting --> Connecting: backoff истёк
    Connected --> Disconnected: stop()
    Reconnecting --> Disconnected: stop()
```

| Транспорт | Backfill пропущенного |
|---|---|
| Telegram | Да — `getUpdates(offset)`; offset **персистится** на диск (иначе дубли/пропуски после рестарта) |
| LoRa | Нет — пришедшее в эфир во время оффлайна потеряно (норма для mesh) |

В состоянии `Reconnecting` LoRa-узла egress **встаёт целиком** (commit-очередь
gated) — это видно пользователям как зависший 🕐; по TTL протухшие сообщения
переходят в `REJECTED(TTL_EXPIRED)` ⌛. Backoff — экспоненциальный с джиттером.

### 11.1. Персистентность и восстановление

Процесс эфемерный: при рестарте in-memory commit-очередь теряется, и у
пользователя навсегда зависает реакция 🕐 — за неё больше некому отвечать.
Решаем **журналом намерений** (write-ahead), а не брокером: нам нужно не
«передоставить payload» (это противоречило бы at-most-once, AD-5 — повторный TX
дублирует в эфире и жжёт duty cycle), а **знать per-message статус**, чтобы
починить реакцию и применить политику. Состояние — **SQLite** (in-process, ACID,
ноль операционки; туда же кладём Telegram `getUpdates` offset). Брокер избыточен
при одном узле / одном инстансе и at-most-once — оставляем как точку расширения,
если позже разнесём транспорты по процессам.

> **Ключевое свойство модели:** egress-воркер сериализован (§6), поэтому в
> статусе `TRANSMITTING` в любой момент **не более ОДНОГО** сообщения. Значит
> по-настоящему неоднозначных сирот при рестарте — **0 или 1**.

```
outbound_journal(
  msg_key       PRIMARY KEY,   -- transport_id + native id (корреляция со статусом)
  origin_chat, origin_msg_id,  -- чтобы обновить реакцию на исходном сообщении
  status,                      -- PENDING | TRANSMITTING | <terminal>
  enqueued_at, tx_started_at,
  payload                      -- собранная строка [тип:ник]+текст (для ре-энкью)
)
```

**Persist-before-act** — журналируем намерение строго ДО побочного эффекта:

```mermaid
flowchart LR
    A["журнал: PENDING"] --> B["реакция 🕐"]
    B --> C["журнал: TRANSMITTING<br/>(ДО node.send!)"]
    C --> D["node.send()"]
    D --> E["журнал: SENT<br/>(ДО реакции/фан-аута)"]
    E --> F["реакция ✅ + mirror"]
    F --> G["прунинг записи"]
```

`TRANSMITTING` обязан писаться **до** `node.send()`: иначе падение сразу после
send потеряет сам факт попытки. На старте сканируем нетерминальные записи:

| Статус-сирота | Знаем | Действие | UX |
|---|---|---|---|
| `PENDING`, свежее TTL | не ушло | **ре-энкью** сейчас | 🕐 → ✅/⚠️ |
| `PENDING`, протухло | не ушло, устарело | `REJECTED(TTL_EXPIRED)` | 🕐 → ⌛ |
| `TRANSMITTING` (≤1) | **неизвестно** | `UNKNOWN`, **без авто-ретрая** | 🕐/📤 → ❓ «не подтвердили после перезапуска, проверьте/повторите вручную» |
| `SENT`, реакция не обновилась | ушло | до-применяем реакцию | → ✅ |

Все recovery-действия **идемпотентны**: `setMessageReaction` к тому же эмодзи —
no-op; ре-энкью защищён `msg_key`; редкий двойной mirror (падение между `SENT` и
fan-out) гасится дедупом на стороне мессенджера. Итог: **ни одна 🕐 не висит
вечно**, тихих дублей в эфире нет, единственное неоднозначное сообщение честно
отдаётся человеку.

> **Оговорка про durability.** Журналу нужен персистентный том; в полностью
> эфемерном контейнере без него recovery деградирует до «всё нетерминальное на
> старте → ❓» (это ортогонально выбору SQLite-vs-брокер).

---

## 12. Конфигурация

Один YAML. Секреты — через `${ENV}`, не в репозитории. Модель — «комнаты»: один
LoRa-эндпоинт + его мессенджер-подписчики. Секция `lora` — **массив нод**: можно
подключить несколько физических радиоузлов; у каждой ноды явный `type`
(`meshcore` | `meshtastic`*) — это фундамент под будущий **LoRa↔LoRa**-мостинг.
Политики **радио-специфичны** и потому живут **внутри ноды** (`policies`).

```yaml
lora:                                # МАССИВ физических LoRa-нод
  - id: meshcore-1
    type: meshcore                   # тип ноды/прошивки (meshcore | meshtastic*) — фундамент под LoRa↔LoRa
    # connection: usb(VID:PID) | serial(port) | tcp(host:port) | ble(address)
    connection:
      type: usb
      device_id: "0333:0303"
    # endpoints — MAP: ключ = имя эндпоинта (на него ссылается rooms[].lora.endpoint).
    # type — термин из MeshCore-приложения; механика (слоты, PSK, login, ACK) — в адаптере, §5.1.
    endpoints:
      general:                                       # Public-канал MeshCore (общий PSK), flood без ACK
        type: public
      emergency:                                     # Channel со своим secret (приватный), flood без ACK
        type: private
        secret: ${MC_EMERGENCY_SECRET}
      ops:                                           # Room Server: direct + login, реальный ACK + backfill
        type: room_server
        pubkey: "a1b2c3d4…"
        password: ${MC_OPS_PW}                       # guest-пароль (пусто → read-only)
    policies:                        # политики САМОЙ ноды (радио-специфичны)
      egress_rate:                                   # под duty cycle
        msgs_per_window: 6
        window_seconds: 60
      queue_ttl_seconds: 45                          # admission TTL
      commit_timeout_seconds: 30                     # таймаут ACK (room_server) / send (public/private)
      reconnect_backoff:
        base: 2
        max: 60
        jitter: true
      dedup_ttl_seconds: 300
      drop_notice_window_seconds: 60
      label:
        include_type: auto           # auto | always | never (auto: тег если >1 мессенджера в комнате)
        format: "[{type}:{nick}] "   # шаблон префикса; {type} опускается по include_type
        max_nick_bytes: 24           # ник усекаем при необходимости; ТЕКСТ — никогда (AD-11)
        on_oversize: reject          # reject (по умолчанию) — НЕ truncate

messengers:
  - id: telegram-main
    kind: telegram        # kind → тег источника в префиксе LoRa: "TG"
    tag: "TG"             # переопределение тега (по умолчанию из kind)
    token: ${TG_BOT_TOKEN}
    # privacy mode у бота ДОЛЖЕН быть отключён (BotFather /setprivacy → Disable)

rooms:
  - lora:                                # node-qualified (имена эндпоинтов уникальны лишь в ноде)
      node: meshcore-1
      endpoint: emergency
    subscribers:
      # topic указан → берём/шлём ТОЛЬКО эту тему
      - transport: telegram-main
        chat: "-1001234567890"
        topic: "42"
      # topic опущен → работаем с General (и только с ним, не со всеми темами)
      - transport: telegram-main
        chat: "-1009999999999"
```

> `*meshtastic` — пока только заложенная точка расширения: `type` ноды задаёт
> адаптер и схему её эндпоинтов (discriminated union). Сейчас реализован `meshcore`.
>
> Каждая нода — **одно радио**: своя commit-очередь и **один egress-воркер на ноду**
> (§6), свои dedup/loop-guard и `label` (несколько эндпоинтов внутри ноды делят это
> радио — очередь не на эндпоинт, а на ноду).

### 12.1. LoRa↔LoRa: комната из двух LoRa-эндпоинтов

LoRa-эндпоинт может быть **подписчиком** другого — так мост соединяет две LoRa-сети
(разные пресеты/частоты/регионы, или meshcore↔meshtastic в будущем). В конфиге
подписчик — это union: либо мессенджер (поля `transport`, `chat`, опциональный
`topic`), либо LoRa (поле `lora` со вложенными `node` и `endpoint`).

**Жёсткий инвариант на форму комнаты** (валидируется в схеме, §12):

| Форма | LoRa-эндпоинтов | Мессенджеров |
|---|---|---|
| 1 LoRa + N мессенджеров | 1 (первичный) | ≥ 1 |
| 2 LoRa (LoRa↔LoRa) | 2 (первичный + 1 подписчик) | **0** |

```mermaid
flowchart LR
    subgraph V1["1 LoRa + N мессенджеров"]
        L1["LoRa A"] <--> TG1["Telegram #1"]
        L1 <--> TG2["Telegram #2"]
    end
    subgraph V2["2 LoRa (LoRa↔LoRa)"]
        LA["LoRa A\n(node-1/general)"] <--> LB["LoRa B\n(node-2/relay)"]
    end
```

Смешивать второй LoRa с мессенджерами в одной комнате **запрещено**: семантика
префикса `[тип:ник]` и статусов-реакций стала бы неоднозначной. Инвариант держим
простым — две взаимоисключающие формы.

**Relay-семантика** (LoRa↔LoRa):

- RX на эндпоинте A → текст кладётся в **commit-очередь** ноды B и уходит её
  **egress-воркером** — тот же airtime-контроль (§7), что и для сообщений из
  мессенджера. Связь симметрична: оба эндпоинта — и источник, и приёмник.
- **Атрибуция автора:** входящий канальный/room-server `Message` несёт автора в
  `sender.display_name` (маппер вынес его из текста: `split_author` для каналов,
  резолв `signature`→`adv_name` для room-server). Relay восстанавливает его в
  wire-текст как `[ник] текст` (`transform.relay_lora_text`) — иначе автор
  потерялся бы. Скобочный формат, а не `ник: текст`: тогда обратный `split_author`
  его не распарсит и loop-guard эха не сломается. Если имя неизвестно
  (`display_name` пуст) — текст форвардится как есть, без обёртки.
- **Бюджет цели:** не влезло в `max_text_bytes` ноды B — **drop + лог** (мессенджера,
  которому вернуть `TOO_LONG`, тут нет). Актуально для разных прошивок/лимитов.
- **Петли:** per-node dedup (A3) + loop-guard `mark_sent` на цели (R8) гасят эхо;
  мостим **разные** сети, поэтому B обычно не слышен на A.
- **Множественное членство запрещено схемой:** один и тот же LoRa-эндпоинт не
  может состоять в нескольких комнатах (валидируется в `AppConfig`, см. §12.2);
  self-loop (подписчик == первичный) запрещён формой комнаты. Цепочки A→B→C
  допустимы как **разные** комнаты, но кольцевую топологию (A→B→C→A) собрать
  нельзя — каждый эндпоинт состоит максимум в одной комнате.

### 12.2. Ограничение топологии: один `ChannelRef` — одна комната

**Что запрещено.** Любой `ChannelRef` (LoRa-эндпоинт **или** мессенджер-канал
`(transport, chat, topic)`) может состоять не более чем в **одной** комнате.
Валидируется в `AppConfig.validate_unique_membership`; нарушение — `ValidationError`
со ссылкой на конкретные `rooms[i]`/`rooms[j]`.

Практически это означает, что сценарий «**одна TG-группа как зеркало для двух
независимых LoRa-сетей**» (например, MeshCore-нода + Meshtastic-нода, оба
мирят сообщения в один и тот же Telegram-чат) — **в текущей итерации не
поддержан**, даже двумя отдельными `Room`-записями.

**Почему сейчас так.** Модель маршрутизации построена как биекция «ChannelRef →
комната», и от этого инварианта зависят сразу несколько подсистем:

| Подсистема | Где живёт допущение | Что сломается без правки |
|---|---|---|
| `RoomRegistry._by_ref` | `core/routing.py` | `dict[ChannelRef, RoomRoute]`, не мультимап — приложение упадёт на старте |
| Admission из мессенджера | `Bridge.admit` → `targets[0]` | подразумевает ровно один LoRa-таргет на сообщение из мессенджера; нет политики выбора/бродкаста |
| Журнал намерений | `outbound_journal.msg_key TEXT PRIMARY KEY` (`transport:msg_id`) | при бродкасте в N нод `INSERT OR REPLACE` молча затрёт N−1 запись — потеря следа доставки |
| Статус-фидбек | `StatusDispatcher` — один статус на `(source, msg_id)` | гонка между нодами; финальный статус — последняя запись, не агрегат |
| AD-4 «LoRa как commit-log» | `Bridge.on_committed` | при бродкасте у одного сообщения из мессенджера становится **N** коммитов в N разных radio-сетях — понятие «единого источника правды» размывается |

То есть это не точечная проверка, а целый класс семантик: атомарность доставки,
идентичность сообщения, схема журнала, фидбек пользователю.

**Что нужно для снятия ограничения** (если/когда сценарий станет приоритетным):

1. **`RoomRegistry`** — мультимап: `dict[ChannelRef, list[RoomRoute]]`; пересмотр
   `for_source` под возврат списка либо отдельную dispatch-политику.
2. **Admission policy** — явный выбор между:
   - бродкаст-в-все (с признанием неатомарности доставки),
   - роутинг по топику/префиксу/конфигу (один TG-чат → одна mesh-сеть по правилу).
3. **`msg_key`** — расширить до `transport:msg_id:target_node`, чтобы N намерений
   на одно сообщение не затирали друг друга.
4. **`StatusDispatcher`** — статус как агрегат по target_node (или явная схема
   «OK если хотя бы одна нода приняла» / «FAILED если все»). Документировать в
   AD: что именно показывает реакция-индикатор при N таргетах.
5. **Атрибуция в мессенджере** — mesh-tag в миррор-направлении (отдельно от
   `LabelFormat`, который про LoRa-направление), иначе читатель TG не отличает
   источник.

До тех пор инвариант держим жёстко: лучше явный отказ конфига на старте, чем
тихая потеря записей в журнале или гонки в статусах. Корнер-кейс зафиксирован
в §14.G5.

---

## 13. Структура пакета

```
lora_bridge/
├── domain/
│   ├── models.py        # Message, ChannelRef, DeliveryStatus, Capabilities, LabelFormat …
│   └── ports.py         # Protocol Transport (+ report_status)
├── core/
│   ├── bridge.py        # ingress fan-in, маршрутизация LoRa/мессенджер; NodeRuntime (per-node)
│   ├── routing.py       # RoomRoute (комната = набор участников) + RoomRegistry + LoraMember/MessengerMember (§12.1)
│   ├── queue.py         # bounded commit-очередь (QueueItem) + admission (rate-limit + TTL)
│   ├── ratelimit.py     # token-bucket egress-лимитера ①
│   ├── egress.py        # egress-воркер (один НА НОДУ), gated на commit
│   ├── dedup.py         # TTL-LRU
│   ├── loopguard.py     # TX-эхо / origin_tag
│   ├── transform.py     # build_lora_text: префикс [тип:ник] + clip ника + size-check
│   ├── status.py        # диспетчер статусов → report_status
│   ├── journal.py       # OutboundJournal (интерфейс) + SQLite-реализация, recovery (§11.1)
│   └── notifier.py      # debounced drop-notice
├── transports/
│   ├── hub.py           # горячий мультикаст RX-поток (общий для адаптеров)
│   ├── meshcore/transport.py    # адаптер поверх lib `meshcore` (commit: §5.1)
│   ├── meshtastic/              # *точка расширения (node.type=meshtastic), пока не реализован
│   └── telegram/transport.py    # адаптер поверх `aiogram` (+ реакции-статусы)
├── config/{schema.py, loader.py}
└── app.py               # composition root (инстанцирует транспорт по node.type)
```

Зависимости направлены внутрь: `transports`/`config` → `domain`; `core` →
`domain`; `domain` ни от кого. (`hub` — утилита транспортного слоя, поэтому живёт
в `transports/`, а не в `core/`.)

---

## 14. 🔥 Прожарка: косяки и корнер-кейсы

### A. Петли и дубликаты

| # | Риск | Митигация |
|---|---|---|
| A1 | **Эхо-петля.** Наш TX в канал возвращается узлом как RX → повторный фан-аут | loop-guard: «recently-TX» множество с TTL + `origin_tag`; обычно узел свой TX не отдаёт на RX, но страхуемся |
| A2 | **Петля 1-ко-многим.** Фан-аут уходит обратно источнику | при mirror всегда `exclude=источник`; источник получает только статус, не копию |
| A3 | **Дубликаты mesh-пакетов** (только LoRa-путь) | dedup TTL-set; без id — хеш `(sender,text)` + опора на TTL-окно (timestamp может отсутствовать) |
| A4 | **Дубли после рестарта** (TG переотдаёт апдейты) | персист offset в самом TG-адаптере (это не общий core-dedup) |

### B. Commit-очередь и пропускная способность ⭐ (следствие выбранной модели)

| # | Риск | Митигация |
|---|---|---|
| B1 | **Head-of-line blocking.** Duty-cycle стол блокирует ВСЮ очередь, включая другие мессенджеры | admission TTL (выкидываем протухшее до TX), `REJECTED` ⌛, честность по источникам |
| B2 | **TX-done не приходит** (узел завис/линк отвалился) → воркер встаёт навсегда | таймаут TX-done → `FAILED` ⚠️, освобождаем воркер |
| B3 | **Несправедливость.** Спамер в одном мессенджере забивает общую очередь, остальные голодают | per-source квота/round-robin на admission |
| B4 | **Потоп из TG в LoRa** превышает duty cycle | token-bucket + bounded-очередь → `REJECTED(RATE_LIMIT)` 🐢 + агрег. уведомление |
| B5 | **Спам уведомлениями о дропе** | debounce: одно агрегированное за окно |
| B6 | **Telegram 429** (сам TG лимитирует) | egress-лимитер и для TG; уважать `retry_after`, backoff |
| B7 | **Рост памяти** при медленном подписчике hub | bounded buffer + drop-oldest + метрика |

### C. Commit-семантика

| # | Риск | Митигация |
|---|---|---|
| C1 | **«SENT ✅» вводит в заблуждение** для public/private: `MSG_OK` ≠ доставлено получателю | смысл ✅ зависит от типа (§5.1): public/private = «принято узлом», room_server = «room server подтвердил приём» |
| C2 | **TX-done у каналов нет** (норма, не edge-case) | три типа эндпоинтов §5.1: public/private = `MSG_OK`+pace (✅ слабее), room_server = delivery-ACK (✅ настоящий) |
| C3 | **Рестарт с in-flight сообщением** → у пользователя навсегда завис 🕐 | журнал намерений (SQLite, persist-before-act) + recovery на старте — см. §11.1. Серийный воркер ⇒ осиротевших `TRANSMITTING` ≤ 1 |

### D. Размер и формат

| # | Риск | Митигация |
|---|---|---|
| D1 | Сообщение `префикс+текст` длиннее LoRa-пакета | **НЕ усекаем** (AD-11): синхронный `REJECTED(TOO_LONG)` 📏 обратно с указанием превышения в байтах |
| D2 | **Эмодзи/кириллица:** лимит в байтах, не символах | считать байты UTF-8, не `len(str)` — и для текста, и для ника, и для префикса |
| D3 | **Вложение/медиа из TG** (фото, гс, стикер) | мы текст-онли: у сообщения нет текста → не пересылаем (или короткий маркер `[вложение]` по конфигу); не падаем |
| D4 | Markdown/entities Telegram | в LoRa — plain; обратно — экранирование MarkdownV2 |
| D5 | **Подмена отправителя/типа** текстом `[TG:Admin]…` | префикс и тег типа формирует только мост из доверенного `Identity`/конфига; польз. текст санитизируется |
| D6 | **Префикс ест бюджет.** Длинный ник может почти не оставить места тексту, а то и сам превысить лимит | ник усекаем (`max_nick_bytes`), **текст — никогда**; если даже усечённый префикс не влезает — TOO_LONG с понятной причиной |
| D7 | **Условный тег типа.** При >1 мессенджере в комнате нужен `[TG:…]`, при одном — лишний; смена числа подписчиков меняет длину префикса (граница TOO_LONG «плавает») | `include_type: auto` считает `writable_messenger_count` по комнате; для предсказуемости можно зафиксировать `always`/`never` |

### E. Идентичность, безопасность, право

| # | Риск | Митигация |
|---|---|---|
| E1 | Кто угодно из TG вещает в RF (абьюз, нелегальный контент) | **PostMVP.** В MVP модерацию делегируем модераторам TG-канала (кик участника). Позже — allow-list/per-user rate-limit |
| E2 | **Telegram bot privacy mode** — бот не видит сообщения группы | BotFather → `/setprivacy` → Disable |
| E3 | Утечка секретов (token, channel secret) | только ENV/secret-store; не логировать |
| E4 | Регуляторика ISM/duty cycle | жёсткий airtime-лимит (B4) + предупреждение в доках |

### F. Соединение и доставка

| # | Риск | Митигация |
|---|---|---|
| F1 | Разрыв BLE/Serial/TCP / истёкший long-poll | автомат §11, backoff + джиттер |
| F2 | Потеря в оффлайне LoRa (нет backfill) | принимаем как данность; TG-сторона — bounded-очередь с TTL |
| F3 | Конфликт ретраев и duty cycle | ретраи только для сетевых FAILED, не для REJECTED; учёт в лимитере |

### G. Маршрутизация и темы

| # | Риск | Митигация |
|---|---|---|
| G1 | Группа TG мигрирует в супергруппу (`chat_id` меняется) | обрабатывать `migrate_to_chat_id`, обновлять комнату в рантайме |
| G2 | Тема удалена/переименована | матчинг по **id темы** (не имени); если темы нет — drop + предупреждение в лог (НЕ откатываемся в General, см. G3) |
| G3 | **Семантика топика из конфига.** Топик опущен → берём/шлём только General; топик указан → только эта тема. Из General при заданном топике ничего не берём | адаптер фильтрует входящий поток строго по сконфигурированному `thread_id` (или его отсутствию = General) |
| G4 | **Split-brain: два экземпляра моста** на одном канале → двойной TX в эфир | операционный вопрос деплоя: запускать ровно один инстанс. Документируем как требование к развёртыванию |
| G5 | **Один мессенджер-чат как зеркало для нескольких LoRa-сетей** (например, общий TG-канал для MeshCore + Meshtastic) | не поддержано в текущей итерации: модель маршрутизации — биекция `ChannelRef → Room`. Валидируется на старте (`AppConfig.validate_unique_membership`); путь снятия ограничения — §12.2 |

### H. Порядок, время, наблюдаемость

| # | Риск | Митигация |
|---|---|---|
| H1 | Out-of-order | commit-очередь даёт глобальный порядок = порядок выхода в эфир (бонус модели) |
| H2 | У LoRa-узла нет RTC / timestamp не извлекается | `Message.timestamp = None` (не выдумываем); при зеркалировании исходное время всё равно не передать |
| H3 | Сообщения «пропадают» молча | метрики на каждый drop-узел: `dropped_total{reason}`, dead-letter-лог |
| H4 | Отладка петель/дублей | структурный лог с `origin_tag`, `msg.id`, комнатой; сквозной трейс |

---

## 15. Этапы реализации

```mermaid
flowchart LR
    M0["M0: каркас\ndomain + ports\n+ конфиг (комнаты)"] --> M1
    M1["M1: транспорты\nMeshCore RX/TX (+TX-done)\nTelegram RX/TX"] --> M2
    M2["M2: ядро\nhub + commit-очередь\n+ egress-воркер\n+ dedup + loopguard"] --> M3
    M3["M3: безопасность эфира\nadmission(rate-limit+TTL)\n+ статусы-реакции\n+ notifier + transform"] --> M4
    M4["M4: resilience\nреконнект + SQLite-журнал\n+ recovery + offset\n+ метрики + fairness"] --> M5
    M5["M5: 2-й мессенджер\n(Discord)\n— проверка абстракции"]
```

- **M0–M3 = MVP**: один LoRa-узел ↔ N Telegram-подписчиков, дуплекс, commit-log
  по TX-done, статусы-реакции, защита эфира.
- **M4** — продакшн-готовность (переживает разрывы/рестарты, честность очереди).
- **M5** — валидация абстрактности порта: Discord не должен трогать `core/` и
  `domain/`.

---

### Резюме одним абзацем

Финальная модель — **LoRa-узел как commit-log**: сообщения из мессенджеров
проходят через одну **сериализованную очередь** и **один egress-воркер**,
коммитятся по **TX-done** (терминальный успех `SENT`), и только после этого
best-effort зеркалятся остальным; путь до `SENT` отражается **реакцией-статусом**
в исходном мессенджере. Главная плата за единый источник правды —
**head-of-line blocking**: пропускная способность всей системы равна пропускной
способности LoRa, а duty-cycle стол виден пользователям как зависший 🕐 → ⌛ по TTL. Поэтому критичны три вещи: **admission control** (bounded
+ rate-limit + TTL) на входе в очередь, **таймаут TX-done** (чтобы воркер не
вставал навсегда) и **честность по источникам** (чтобы один спамер не заморозил
очередь для всех).
