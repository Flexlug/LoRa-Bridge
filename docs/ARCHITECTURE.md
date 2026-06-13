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

**Чего сознательно НЕ делаем в MVP:**

- Не передаём вложения (фото/голос/файлы) в LoRa — только текстовые
  плейсхолдеры (`[photo]`, ссылка и т.п.).
- Не гарантируем доставку *получателю* в LoRa (канальные сообщения MeshCore
  ACK не имеют — см. §2, AD-5).
- Без веб-UI/админки — только конфиг-файл.

**Ключевые ограничения предметной области (определяют всю архитектуру):**

| Сторона | Ограничение | Следствие |
|---|---|---|
| LoRa | Узкий канал: десятки байт на пакет, строгий **duty cycle** (EU868 1%) | rate-limit + усечение + admission control |
| LoRa | Канальные сообщения без ACK, без backfill после простоя | at-most-once; commit = факт передачи в эфир (TX-done) |
| LoRa | Возможны дубликаты пакетов (mesh-ретрансляция) | обязательная дедупликация |
| LoRa | Радио half-duplex, один узел = одна очередь TX | сериализованный egress-воркер в ядре |
| LoRa | Нет тем/threads, имена узлов короткие | темы только в мессенджере; маркировка отправителя |
| Telegram | Высокий объём, темы, вложения, длинные сообщения (4096) | источник «потопа» для LoRa; асимметрия |
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
| AD-5 | **Commit = TX-done** (узел подтвердил передачу в эфир) | Сильнейший правдивый сигнал: delivery-ACK у канальных сообщений нет. |
| AD-6 | **Сериализованная commit-очередь** + один egress-воркер с таймаутом TX-done | Один радиоузел физически сериализует TX; синхронный gated fan-out. |
| AD-7 | **Статус-фидбек в мессенджеры** (реакция-индикатор) | Дуплекс = поток сообщений + поток статусов; пользователь видит судьбу сообщения. |
| AD-8 | **Egress rate-limit + drop + уведомление** на стороне LoRa | Защита эфира/duty cycle; см. §7. |
| AD-9 | **Loop-guard + dedup** обязательны | Мультикаст + RX-эхо собственных TX создают петли/дубли. |

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
      +Optional~str~ topic
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
      +datetime timestamp
      +tuple~Attachment~ attachments
      +Optional~str~ reply_to
      +Optional~str~ origin_tag
    }
    class OutboundMessage {
      +str text
      +Optional~str~ sender_label
      +tuple~Attachment~ attachments
      +str origin_tag
    }
    class DeliveryStatus {
      <<enum>>
      PENDING
      TRANSMITTING
      SENT
      MIRRORED
      REJECTED
      FAILED
    }
    Message --> ChannelRef : source
    Message --> Identity : sender
```

```python
# domain/models.py  (ключевые типы)
from enum import Enum
from dataclasses import dataclass
from typing import Optional
import datetime as dt


@dataclass(frozen=True)
class ChannelRef:
    transport_id: str
    channel: str
    topic: Optional[str] = None      # message_thread_id; None для LoRa


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
    timestamp: dt.datetime           # UTC, tz-aware (проставляет ядро на ingress)
    attachments: tuple = ()
    reply_to: Optional[str] = None
    origin_tag: Optional[str] = None  # loop-guard


@dataclass(frozen=True)
class OutboundMessage:
    text: str
    sender_label: Optional[str] = None   # префикс "[TG:Alex]" для LoRa
    attachments: tuple = ()
    origin_tag: str = ""


class DeliveryStatus(Enum):
    PENDING = "pending"            # принято в commit-очередь
    TRANSMITTING = "transmitting"  # взято воркером, отдано узлу
    SENT = "sent"                  # TX-done подтверждён узлом (COMMIT)
    MIRRORED = "mirrored"          # разослано остальным подписчикам
    REJECTED = "rejected"          # admission: очередь полна / rate-limit / TTL
    FAILED = "failed"              # нет TX-done в таймаут / ошибка узла


@dataclass(frozen=True)
class RateSpec:
    msgs_per_window: int
    window_seconds: float
    burst: int = 1


@dataclass(frozen=True)
class Capabilities:
    supports_topics: bool
    max_text_bytes: int
    egress_rate: Optional[RateSpec] = None
    supports_attachments: bool = False
    supports_status_feedback: bool = False   # умеет показать статус (реакция)
    emits_tx_done: bool = False              # узел отдаёт TX-done (commit)
```

---

## 5. Порты и абстракции

И LoRa-клиент, и мессенджер реализуют один протокол `Transport`. Дуплекс теперь
включает **обратный канал статусов** (`report_status`) — для отрисовки реакции.

```python
# domain/ports.py
from typing import AsyncIterator, Protocol, runtime_checkable
from .models import (Capabilities, ChannelRef, Message,
                     OutboundMessage, DeliveryStatus)


@runtime_checkable
class Transport(Protocol):
    id: str
    capabilities: Capabilities

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    # Исходящая сторона. Для LoRa send() РЕЗОЛВИТСЯ по TX-done узла,
    # а не по факту записи в линк (см. AD-5/AD-6).
    async def send(self, target: ChannelRef, msg: OutboundMessage) -> "SendResult": ...

    # Горячий мультикаст-поток входящих (см. §8).
    def subscribe(self) -> AsyncIterator[Message]: ...

    # Обратный канал статусов. No-op, если supports_status_feedback=False.
    async def report_status(self, origin: ChannelRef, message_id: str,
                            status: DeliveryStatus) -> None: ...
```

Адаптеры (примеры `capabilities`):

```python
# MeshCore: медленный коммит-источник, без тем, отдаёт TX-done, без статусов
MeshCoreTransport.capabilities = Capabilities(
    supports_topics=False, max_text_bytes=160,
    egress_rate=RateSpec(6, 60),          # консервативно под duty cycle
    supports_attachments=False,
    supports_status_feedback=False, emits_tx_done=True)

# Telegram: быстрый, темы, вложения, умеет реакции-статусы
TelegramTransport.capabilities = Capabilities(
    supports_topics=True, max_text_bytes=4096,
    egress_rate=RateSpec(20, 60, burst=20),
    supports_attachments=True,
    supports_status_feedback=True, emits_tx_done=False)
```

> **Реализация TX-done для MeshCore.** `send()` LoRa-адаптера не возвращается по
> записи в companion-линк, а ждёт события **TX-done** от прошивки (с таймаутом).
> Так «успешно отправлено в узел» = «физически ушло в эфир», что и есть наш
> commit. Если прошивка не отдаёт TX-done — деградируем до queue-ACK с
> предупреждением (и помечаем коммит как «слабый»).

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
    Note over Q: admission: очередь полна / rate-limit / TTL?
    alt отклонено
        Q-->>TGc: REJECTED ❌ (+ агрег. уведомление)
    else принято
        Q-->>TGc: PENDING 🕐
        W->>Q: pull (FIFO, по одному)
        W-->>TGc: TRANSMITTING 📤
        W->>N: send_channel(text)
        alt TX-done в таймаут
            N-->>W: TX-done
            W-->>TGc: SENT ✅
            W->>Oth: mirror(text)
            W-->>TGc: MIRRORED 📡
        else таймаут / ошибка узла
            N--xW: нет TX-done
            W-->>TGc: FAILED ⚠️
        end
    end
```

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
            if not self.dedup.accept(msg):     continue   # дубль пакета
            if self.loop_guard.is_echo(msg):   continue   # наш TX вернулся как RX
            if self._is_lora_origin(msg):
                await self._fanout_to_messengers(msg, exclude=None)
            else:
                await self._admit(msg)                     # в commit-очередь

    async def _admit(self, msg):
        if not self.queue.offer(msg):          # bounded + rate-limit + TTL
            await self._set_status(msg, DeliveryStatus.REJECTED)
            await self.notifier.note_drop(msg.source)
            return
        await self._set_status(msg, DeliveryStatus.PENDING)

    async def _egress_worker(self):
        async for msg in self.queue:                       # FIFO, по одному
            await self._set_status(msg, DeliveryStatus.TRANSMITTING)
            res = await with_timeout(self.lora.send(...), self.tx_timeout)
            if res.ok:                                     # TX-done
                await self._set_status(msg, DeliveryStatus.SENT)
                await self._fanout_to_messengers(msg, exclude=msg.source.transport_id)
                await self._set_status(msg, DeliveryStatus.MIRRORED)
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
  `busy` (если есть feedback), либо **тихий drop**. Поэтому коммитим по TX-done,
  а не по «приняли в очередь».
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
    IN([Входящее Message]) --> DUP{"Dedup: новый?"}
    DUP -- нет --> D1[["drop (duplicate)"]]
    DUP -- да --> LG{"Loop-guard: наше TX-эхо?"}
    LG -- да --> D2[["drop (echo)"]]
    LG -- нет --> ORI{"Источник?"}
    ORI -- "LoRa" --> FOL["fan-out ВСЕМ мессенджерам\n(без очереди)"]
    ORI -- "мессенджер" --> ADM{"Admission:\nочередь/rate-limit/TTL ok?"}
    ADM -- нет --> REJ["status=REJECTED ❌\n+ агрег. уведомление"]
    ADM -- да --> PEND["status=PENDING 🕐\n→ commit-очередь"]
    PEND --> WRK["egress-воркер: TRANSMITTING 📤"]
    WRK --> XF["Transform под LoRa:\n• префикс sender_label\n• усечь до max_text_bytes (UTF-8!)\n• attachments → плейсхолдер\n• origin_tag"]
    XF --> TX{"TX-done в таймаут?"}
    TX -- да --> SENT["status=SENT ✅"]
    SENT --> MIR["fan-out ОСТАЛЬНЫМ\n(exclude=источник)\nstatus=MIRRORED 📡"]
    TX -- нет --> FAIL["status=FAILED ⚠️"]
```

Детали стадий:

- **Dedup** — TTL-LRU по `f"{transport_id}:{msg.id}"`; для LoRa без надёжного id
  ключ = `sha1(sender_uid + text + floor(ts, 5s))`.
- **Loop-guard** — отбрасываем RX-сообщения, совпадающие с недавно
  оттранслированными нами (`origin_tag` + «recently-TX» множество с TTL), и
  сообщения от собственной identity бота.
- **Transform** — только под нужды LoRa (приёмник медленный/узкий): префикс
  отправителя, усечение **по границе UTF-8** (эмодзи/кириллица считаем в
  **байтах**, не в символах), вложения → `summary`.
- **Admission** — bounded-очередь + token-bucket из `capabilities.egress_rate` +
  TTL (протухшее в очереди не отправляем, чтобы не тратить airtime на устаревшее).
- **Drop-notifier** — коалесцирует дропы за окно (напр. 60 с) → одно
  уведомление с числом отброшенных (не спамим).

---

## 10. Статусы доставки и фидбек

Дуплекс несёт два потока: сообщения и **статусы**. Originating-мессенджер
обновляет индикатор (реакцию) по мере прохождения commit-пайплайна.

```mermaid
stateDiagram-v2
    [*] --> PENDING: принято в очередь
    PENDING --> REJECTED: admission (очередь/rate-limit/TTL)
    PENDING --> TRANSMITTING: взято воркером
    TRANSMITTING --> SENT: TX-done (COMMIT)
    TRANSMITTING --> FAILED: таймаут / ошибка узла
    SENT --> MIRRORED: разослано остальным
    REJECTED --> [*]
    FAILED --> [*]
    MIRRORED --> [*]
```

Маппинг на реакцию (Telegram `setMessageReaction`):

| Статус | Реакция | Смысл |
|---|---|---|
| PENDING | 🕐 | в очереди на эфир |
| TRANSMITTING | 📤 | передаётся узлом |
| SENT | ✅ | ушло в эфир (commit) |
| MIRRORED | 📡 | отзеркалено остальным |
| REJECTED | ❌ | rate-limit / очередь полна |
| FAILED | ⚠️ | нет подтверждения от узла |

Реализация: `report_status(origin, message_id, status)` зовётся ядром на каждом
переходе; Telegram-адаптер мапит на реакцию исходного сообщения. `message_id`
ядро берёт из входящего `Message.id` (корреляция). LoRa-адаптер `report_status` —
no-op (`supports_status_feedback=False`).

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
переходят в ❌. Backoff — экспоненциальный с джиттером.

---

## 12. Конфигурация

Один YAML. Секреты — через `${ENV}`, не в репозитории. Модель — «комнаты»: один
LoRa-канал + его мессенджер-подписчики.

```yaml
lora:
  id: meshcore-1
  connection: { type: tcp, host: 127.0.0.1, port: 5000 }   # tcp|serial|ble
  channels:
    - { name: "emergency", secret: ${MC_EMERGENCY_SECRET} }
  tx_done_timeout_seconds: 30

messengers:
  - id: telegram-main
    kind: telegram
    token: ${TG_BOT_TOKEN}
    # privacy mode у бота ДОЛЖЕН быть отключён (BotFather /setprivacy → Disable)

rooms:
  - lora_channel: "emergency"
    subscribers:
      - { transport: telegram-main, chat: "-1001234567890", topic: "42" }
      - { transport: telegram-main, chat: "-1009999999999" }   # архив, без темы

policies:
  dedup_ttl_seconds: 300
  drop_notice_window_seconds: 60
  egress_rate: { msgs_per_window: 6, window_seconds: 60 }      # под duty cycle
  queue_ttl_seconds: 45                                        # admission TTL
  reconnect_backoff: { base: 2, max: 60, jitter: true }
```

> Несколько каналов на одном узле **делят одно радио** → общая commit-очередь и
> один egress-воквер (а не очередь на канал).

---

## 13. Структура пакета

```
meshcore_bridge/
├── domain/
│   ├── models.py        # Message, ChannelRef, DeliveryStatus, Capabilities …
│   └── ports.py         # Protocol Transport (+ report_status)
├── core/
│   ├── bridge.py        # ingress fan-in, маршрутизация LoRa/мессенджер
│   ├── queue.py         # bounded commit-очередь + admission (rate-limit + TTL)
│   ├── egress.py        # single egress-воркер, gated на TX-done
│   ├── hub.py           # горячий мультикаст RX-поток
│   ├── dedup.py         # TTL-LRU
│   ├── loopguard.py     # TX-эхо / origin_tag
│   ├── transform.py     # label / truncate(UTF-8) / attachments
│   ├── status.py        # диспетчер статусов → report_status
│   └── notifier.py      # debounced drop-notice
├── transports/
│   ├── meshcore/transport.py   # адаптер поверх lib `meshcore` (TX-done!)
│   └── telegram/transport.py   # адаптер поверх `aiogram` (+ реакции-статусы)
├── config/{schema.py, loader.py}
└── app.py               # composition root
```

Зависимости направлены внутрь: `transports`/`config` → `domain`; `core` →
`domain`; `domain` ни от кого.

---

## 14. 🔥 Прожарка: косяки и корнер-кейсы

### A. Петли и дубликаты

| # | Риск | Митигация |
|---|---|---|
| A1 | **Эхо-петля.** Наш TX в канал возвращается узлом как RX → повторный фан-аут | loop-guard: «recently-TX» множество с TTL + `origin_tag`; обычно узел свой TX не отдаёт на RX, но страхуемся |
| A2 | **Петля 1-ко-многим.** Фан-аут уходит обратно источнику | при mirror всегда `exclude=источник`; источник получает только статус, не копию |
| A3 | **Дубликаты mesh-пакетов** | dedup TTL-set; без id — хеш `(sender,text,coarse_ts)` |
| A4 | **Дубли после рестарта** (TG переотдаёт апдейты) | персист offset; dedup опц. персист |

### B. Commit-очередь и пропускная способность ⭐ (следствие выбранной модели)

| # | Риск | Митигация |
|---|---|---|
| B1 | **Head-of-line blocking.** Duty-cycle стол блокирует ВСЮ очередь, включая другие мессенджеры | admission TTL (выкидываем протухшее до TX), статус ❌, честность по источникам |
| B2 | **TX-done не приходит** (узел завис/линк отвалился) → воркер встаёт навсегда | таймаут TX-done → `FAILED` ⚠️, освобождаем воркер |
| B3 | **Несправедливость.** Спамер в одном мессенджере забивает общую очередь, остальные голодают | per-source квота/round-robin на admission |
| B4 | **Потоп из TG в LoRa** превышает duty cycle | token-bucket + bounded-очередь → `REJECTED` ❌ + агрег. уведомление |
| B5 | **Спам уведомлениями о дропе** | debounce: одно агрегированное за окно |
| B6 | **Telegram 429** (сам TG лимитирует) | egress-лимитер и для TG; уважать `retry_after`, backoff |
| B7 | **Рост памяти** при медленном подписчике hub | bounded buffer + drop-oldest + метрика |

### C. Commit-семантика

| # | Риск | Митигация |
|---|---|---|
| C1 | **«SENT ✅» вводит в заблуждение:** TX-done ≠ доставлено получателю (у канальных нет ACK) | документировать смысл ✅ = «ушло в эфир»; для гарантий — отдельный режим direct+ACK (дорого) |
| C2 | **Прошивка не отдаёт TX-done** | деградация до queue-ACK с пометкой «слабый коммит» + предупреждение |
| C3 | **Рестарт с in-flight сообщением** (TRANSMITTING) → у пользователя завис 🕐 | на старте orphaned in-flight → статус ❓unknown |

### D. Размер и формат

| # | Риск | Митигация |
|---|---|---|
| D1 | Сообщение длиннее LoRa-пакета | усечение по `max_text_bytes` по границе UTF-8 + `…` (фрагментация — пост-MVP) |
| D2 | **Эмодзи/кириллица:** лимит в байтах, не символах | считать байты UTF-8, не `len(str)` |
| D3 | Вложения не лезут в LoRa | политика `placeholder`/`url`/`drop` |
| D4 | Markdown/entities Telegram | в LoRa — plain; обратно — экранирование MarkdownV2 |
| D5 | **Подмена отправителя** текстом `[TG:Admin]…` | префикс формирует только мост из доверенного `Identity`; польз. текст санитизируется |

### E. Идентичность, безопасность, право

| # | Риск | Митигация |
|---|---|---|
| E1 | Кто угодно из TG вещает в RF (абьюз, нелегальный контент) | allow-list на запись в LoRa; per-user rate-limit; модерация |
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
| G2 | Тема удалена/переименована | матчинг по **id темы**, не имени; fallback в general или drop + предупреждение |
| G3 | General topic (без thread_id) vs форум-темы | `topic=None` ≠ `topic="1"` — различать явно |
| G4 | **Split-brain: два экземпляра моста** на одном канале → двойная публикация и двойной TX в эфир | single-instance (lock-файл/лидер); как минимум — предупреждение в доках |
| G5 | Нет комнаты для входящего | тихий drop + метрика `unrouted_total` |

### H. Порядок, время, наблюдаемость

| # | Риск | Митигация |
|---|---|---|
| H1 | Out-of-order | commit-очередь даёт глобальный порядок = порядок выхода в эфир (бонус модели) |
| H2 | У LoRa-узла нет RTC | timestamp проставляет ядро на ingress (UTC) |
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
    M4["M4: resilience\nреконнект + offset\n+ метрики + fairness"] --> M5
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
коммитятся по **TX-done**, и только после этого зеркалятся остальным; каждый шаг
отражается **статусом-реакцией** в исходном мессенджере. Главная плата за единый
источник правды — **head-of-line blocking**: пропускная способность всей системы
равна пропускной способности LoRa, а duty-cycle стол виден пользователям как
зависший 🕐 → ❌ по TTL. Поэтому критичны три вещи: **admission control** (bounded
+ rate-limit + TTL) на входе в очередь, **таймаут TX-done** (чтобы воркер не
вставал навсегда) и **честность по источникам** (чтобы один спамер не заморозил
очередь для всех).
