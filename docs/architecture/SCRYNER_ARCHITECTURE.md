# Scryner: архитектура собственного скринера

> Цель: сервис уровня GodsEye (см. `docs/analysis/GODSEYE_ANALYSIS.md`), построенный на
> нашем стеке: Python 3.11+, asyncio, aiohttp, websockets, pydantic-settings, structlog.
> Документ — первая версия, уточняется после получения WebSocket-потока оригинала.

---

## 1. Границы системы

```
┌──────────────┐   WS/REST   ┌────────────┐   bars/OI/book   ┌────────────┐
│ Биржи (15)   │ ──────────► │ Collectors │ ───────────────► │ Aggregator │
└──────────────┘             └────────────┘                  └─────┬──────┘
                                                                    │ metrics, bars, clusters
                      ┌─────────────────────────────────────────────┼──────────────┐
                      ▼                         ▼                   ▼              ▼
               ┌────────────┐          ┌──────────────┐     ┌────────────┐  ┌───────────┐
               │ Detectors  │          │ Alert engine │     │  Storage   │  │ Catalog   │
               │ tick/wall/ │          │ thresholds + │     │ Redis +    │  │ symbols,  │
               │ twap/bot   │          │ cooldown     │     │ TimescaleDB│  │ coi_base  │
               └─────┬──────┘          └──────┬───────┘     └─────┬──────┘  └─────┬─────┘
                     └──────────┬─────────────┴───────────────────┴───────────────┘
                                ▼
                        ┌───────────────┐      ┌──────────────┐     ┌────────────────┐
                        │ API (FastAPI) │ ───► │ Web-клиент   │     │ Notifier:      │
                        │ REST + WS     │      │ (виджеты,    │     │ Telegram, FCM, │
                        └───────────────┘      │ графики)     │     │ voice, terminal│
                                               └──────────────┘     └────────────────┘
```

Процессы (каждый — отдельный asyncio-сервис, общение через Redis Streams):

| Процесс | Роль |
|---|---|
| `collector-<exchange>` | Подписка на trades / depth / OI / funding, нормализация в единый формат |
| `aggregator` | 1s и 1m бары, окна метрик, COI по базовому активу |
| `detectors` | Тики, плотности, TWAP, боты |
| `alerts` | Пороговый движок (пороги пользователя × метрики) с кулдауном |
| `api` | FastAPI: REST + WebSocket для клиентов |
| `notifier` | Очереди в Telegram / FCM / терминал |

Dev-режим: все процессы в одном `python -m scryner` с asyncio.gather, Redis заменяется
in-memory шиной, TimescaleDB заменяется SQLite. Prod: docker-compose.

---

## 2. Модель данных

### 2.1 Инструмент
```
key            = "{exchange}:{pair}:{market_type}"      # binance:BTCUSDT:futures
source_route   = "{exchange}_{market}"                  # binance_futures
coi_base       = "crypto::BTC" | "stock::AAPL"          # кросс-биржевая группа
stock_scope    = bool                                   # токенизированная акция
quote_currency, volume_currency, contract_multiplier, tick_size, step_size
```
Каталог версионируется хэшем (`catalog_version`), клиент получает дельту.

### 2.2 Бары
```
bar_1s: ts, o, h, l, c, buy_quote, sell_quote, total_quote, trades_count,
        oi_close, oi_value, had_trade                       # retention 72 ч, в памяти 30 мин
bar_1m: ts, o, h, l, c, buy_quote, sell_quote, total_quote, trades_count,
        oi_open, oi_high, oi_low, oi_close                  # retention 9 дней, в БД
```
5m / 15m / 1h / 24h агрегируются из 1m на лету (кольцевые буферы в памяти).

### 2.3 Метрики на инструмент (пересчёт каждую секунду для активных, каждые 5 с для остальных)

| Метрика | Окна | Формула |
|---|---|---|
| `range_pct` | 1m, 5m, 15m, 1h, 24h | `(high - low) / low * 100` по окну |
| `natr5` | 5m | NATR(14) по 5-минутным свечам: `ATR(14) / close * 100` |
| `volume` | 1m, 5m, 15m, 1h, 24h | сумма `total_quote` |
| `oi_change_pct` | 1m, 5m, 15m, 1h, 24h | `(oi_now - oi_window_start) / oi_window_start * 100`, только futures |
| `dvol_pct` | 5m | `volume_5m / volume_24h * 100` |
| `dtpl_pct` | 5m | гипотеза: `(buy - sell) / total * 100` за 5m, уточнить по JS оригинала |
| `tpm` | 1m | число сделок за последнюю минуту |
| `spike` | 5m | `volume_5m / mean(volume завершённых 5m за предыдущий час)` |
| `long_volume`, `short_volume` | 5m | `buy_quote`, `sell_quote` |
| `coi` | 1m, 5m, 15m, 1h, 24h | сумма дельт OI в quote по всем биржам для `coi_base` + число источников |

Метрики хранятся в Redis hash `metrics:{route}` упакованными массивами (как у оригинала:
`[vol24h, chg24h, dvol5, tpm, natr5, graph[28], day[7]]`), чтобы отдавать 800 инструментов
одним ответом ~200 КБ gzip.

### 2.4 Кластеры (footprint)
По каждому инструменту за каждый час: `{price_level: ask_size, bid_size, trade_count}` с шагом
`price_step` (на запрос) поверх базового шага `tick_size × zoom`. Строятся из потока сделок,
хранятся в Redis на 24 ч, в БД — часовые срезы.

### 2.5 События
```
event: id, ts, type ∈ {tick, wall, twap, bot, alert}, key, exchange, pair, market_type,
       payload(json), score
```
Хранятся 7 дней (История алертов), стримятся клиентам по WS.

---

## 3. Детекторы (первая версия правил)

- **Тик (tick):** за 1 с `|Δprice| ≥ X%` или `total_quote ≥ Y` (пороги пользователя по
  рынку); выдаётся с направлением, объёмом, % и ссылкой на 1s-график.
- **Плотность (wall):** уровень в стакане с объёмом `≥ min_size` (USDT) и
  `≥ k × средний уровень`, живущий `≥ T` секунд; расстояние до цены в %; сторона LONG/SHORT;
  «влияние на цену» = доля объёма плотности от объёма сделок за 5m/15m (good / weak).
  Снимается при исчезновении уровня.
- **TWAP:** серия ордеров одинакового размера с регулярным интервалом (±20%) ≥ N штук;
  копится «исполнено/серий», сумма в $, длительность.
- **Бот (pattern):** повторяющиеся серии сделок одного размера/шага (`P1 | ALP S` и т. п.);
  правила описываются конфигом паттернов, первый набор: «лесенка», «айсберг», «пинг-понг».
- **Алерт:** конъюнкция включённых условий (long/short, OI5m, TPM, spike, Dvol%, Natr5) по
  порогам пользователя и рынка; кулдаун на пару (по умолчанию 15 мин); лимит активных
  алертов с графиков 30 / 5 на инструмент.

---

## 4. API (совместимо по форме с оригиналом, чтобы переиспользовать его UX)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/catalog?route=&catalog_version=` | каталог + метрики + coi (дельта по версии) |
| GET | `/api/candidates?route=&sort=&limit=&metric_filters=` | отбор по метрикам |
| POST | `/api/history_batch?route=` | бары 1s/1m до 64 инструментов, с partial |
| POST | `/api/clusters/snapshot_batch?route=` | кластеры за час по шагу цены |
| GET/POST | `/api/alerts/thresholds` | пороги пользователя по рынку |
| GET | `/api/events?types=&since=` | история событий |
| WS | `/ws/events?route=` | поток событий (tick/wall/twap/bot/alert) + heartbeat |
| WS | `/ws/metrics?route=` | дельты метрик для TOP 50 (раз в секунду) |
| POST | `/api/auth/telegram/start` → `/api/auth/pending/{token}` | вход через Telegram-бота |
| POST | `/api/presence` | аналог `auth/ping` |
| GET | `/api/client_release` | версия клиента, принудительный reload |
| GET/PUT | `/api/user/configs` | до 20 сохранённых раскладок |
| GET/PUT | `/api/user/bans` | бан-лист (вечный / 24 ч, по разделам) |
| GET/PUT | `/api/user/notify` | Telegram-бот, chat_id, FCM-устройства, источники событий |

Ответы: JSON, gzip, `cache-control: no-store`. Ошибки `{"detail": "..."}`.

---

## 5. Клиент

SPA без тяжёлого фреймворка на первом этапе: TypeScript + Lightweight Charts (TradingView
OSS) для графиков, собственная сетка виджетов (CSS grid, drag-n-drop), состояние в
localStorage + серверные конфиги. Виджеты: История алертов, Тики, Боты, Плотности, TWAP,
TOP 50, Новости/Ончейн/Листинги, панель графиков с маркерами событий и кластерами.
Звук/голос — Web Audio + SpeechSynthesis. Автооткрытие стакана — HTTP на `127.0.0.1:<port>`
терминала (MetaScalp 17845, остальные уточнить).

---

## 6. Коллекторы: с чего начать

| Биржа | Потоки | Примечание |
|---|---|---|
| Binance Futures | `aggTrade`, `depth@100ms`, `markPrice`, REST `openInterest` (OI) | есть клиент в TradeBot-MA (`src/exchange`), переиспользовать |
| Bybit | `publicTrade`, `orderbook.50`, `tickers` (OI) | v5 WS |
| OKX | `trades`, `books`, `open-interest` | |
| Bitget, Gate.io, MEXC, KuCoin, HTX | аналогично | |
| Hyperliquid, Aster, Lighter, ApeX | DEX-API, свои форматы | позже |
| Kraken, Coinbase, Upbit | spot + Kraken futures | позже |

Нагрузка Binance Futures: ~800 инструментов, пики 5–10 тыс. сообщений/с. Один процесс
на биржу, до 200 стримов на соединение, авто-reconnect, контроль лага.

---

## 7. Хранилище

- **Redis**: горячее состояние (последние бары, метрики, стаканы, кластеры, очереди событий).
- **TimescaleDB** (prod) / **SQLite** (dev): `bars_1m` (9 дней), `clusters_1h` (30 дней),
  `events` (7 дней), пользователи, настройки, подписки.
- 1s-бары только в памяти (кольцо 30 мин) + опциональный дамп последних 72 ч на диск.

Оценка: 800 × 1440 × 9 дней ≈ 10 млн строк 1m-баров на биржу — укладывается в TimescaleDB
без проблем; 1s-бары в БД не писать.

---

## 8. Этапы

| Этап | Содержимое | Критерий готовности |
|---|---|---|
| **0. Каркас** | репозиторий, конфиги, логирование, Redis/SQLite, CI, тесты | `pytest` зелёный, `python -m scryner` стартует |
| **1. MVP Binance Futures** | коллектор, 1s/1m бары, метрики §2.3, каталог, `/api/catalog`, `/api/history_batch`, таблица TOP 50 в браузере | таблица обновляется раз в секунду, метрики сходятся с Binance UI |
| **2. Алерты** | пороги §3, кулдаун, история алертов, Telegram-бот, звук | алерт приходит в Telegram ≤ 2 с от события |
| **3. События** | тики, плотности, TWAP, боты, кластеры, маркеры на графике | виджеты Advanced-раскладки заполнены |
| **4. Мультибиржа + COI** | Bybit, OKX, Bitget, Gate, MEXC; coi_base; stock_scope | ≥ 5 бирж, `route=all_futures` |
| **5. Пользователи** | Telegram-логин, trial, тарифы, рефералка, команды, бан-лист, конфиги, Android/FCM | кабинет как в оригинале |
| **6. Инфоблок** | новости, ончейн, листинги (RSS/Telegram-каналы/API бирж) | правая колонка |

Подробная разбивка — `tasks/BACKLOG.md`.

---

## 9. Открытые вопросы (закрыть после второй HAR-записи оригинала)

1. Транспорт реального времени у оригинала (WS или SSE), формат сообщений, частота.
2. Точные определения `dtpl` и `metric_activity`.
3. Окна массива `coi` (предположительно 1m/5m/15m/1h/24h).
4. Алгоритмы детекторов ботов и TWAP (видны только карточки в UI).
5. Порты и протоколы локальных API терминалов Colibri / Vataga / Tiger / Scalpee.
