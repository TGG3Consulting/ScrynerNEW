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

## 3. Детекторы (по расшифрованному протоколу, см. `GODSEYE_REALTIME_PROTOCOL.md`)

- **Тик (tick):** серверный. Для каждого инструмента каждую секунду: если `buy + sell`
  за секунду `≥ tick_threshold`, публикуется событие с OHLC секунды, `price_move_pct`,
  `price_range_pct`, порогом и `volume_24h`. `tick_threshold_base = clamp(0.274 ·
  volume_24h^0.479, 100, 25000)` (HTX: min 50), пересчёт раз в минуту по суточному объёму.
  Пользовательские фильтры (множитель, «всегда играть если», кастомные правила, баны) —
  на клиенте.
- **Плотность (wall):** серверный, по локальной книге. Уровень с `size_usd ≥ min_size`
  (порог на биржу, настраивается) и заметно выше соседних; `distance_pct` от цены, сторона
  `bid/ask`, `is_iceberg` (восстанавливающийся объём). После публикации досчитывается
  «Price impact» 5m/15m: `good`, если цена пошла в сторону стены, иначе `weak`.
- **TWAP (Hyperliquid):** серверный индексатор TWAP-ордеров Hyperliquid (как hypurrscan):
  REST/WS Hyperliquid info → активные TWAP с `user, token, side, amount, duration_min,
  reduce_only, randomize`, цены рынков, баны токенов/префиксов, минимальные суммы в
  30 с/минуту по токену. Публикуется snapshot/patch.
- **Боты:** серверный, по потоку сделок. **P1 Strict timer** — ≥ N сделок одной стороны
  близкого размера через почти равный интервал (`~3.5s`); **C1 Micro-pressure** — серия
  мелких сделок одной стороны с измеримым давлением на цену за 300 с и долей объёма стороны;
  **S1 Spreader** — симметричные сделки по обеим сторонам. Рейтинг `R0…R5` по длине серии и
  регулярности; фильтры: мин. объём и мин. движение каждого тика, биржи, скрывать R0.
- **Алерт:** **клиентский**, как в оригинале: сервер стримит метрики (`/ws/all/alerts/v1`,
  отсекая по `min_*` из query), клиент применяет `evaluateAlertState` (AND по включённым
  чекбоксам, иначе OR по заданным порогам), кулдаун на пару (по умолчанию 15 мин, локально),
  историю алертов и отправку в Telegram/терминал. Серверная часть алертов — только лимиты
  алертов с графиков (30 активных / 5 на инструмент) и командные сигналы.

---

## 4. API — повторяем контракт оригинала один в один

Пути, query-параметры, форматы snapshot/patch/seq и записей берутся из
`GODSEYE_ANALYSIS.md` §6 и `GODSEYE_REALTIME_PROTOCOL.md`. Это позволяет переиспользовать
клиентскую логику оригинала как спецификацию UX.

REST: `/api/all/v1/{snapshot,keys,summary_filter_metadata}`, `/api/simple/v1/pairs`,
`/api/v2/pairs`, `/api/graph/v1/{candidates,history_batch}`, `/api/history/{kind}/{key}`,
`/api/marketstore/clusters/{snapshot/{key},snapshot_batch}`, `/api/chart-alerts/v1`,
`/api/client_release`, `/api/auth/{ping,settings-configs,welcome-notice/…}`, `/api/teams…`,
`/api/mobile-alerts/{action}`, `/api/help-content/{key}`, `/api/v1/feedback/reports`,
`/api/v2/lighter/market-id/{symbol}`, админ: `/api/pairs/{key}`, `/api/admin/welcome-notices`.

WS: `/ws/all/v1`, `/ws/all/alerts/v1` (gzip-фреймы), `/ws/tick`, `/ws/density/feed`,
`/ws/active_twaps`, `/ws/graph_charts/v1`, `/ws/summary_{bots,news,onchain,listings}`,
`/ws/stock/reference-prices/v1`, `/ws/v3`, `/ws/team-signals`.

Аутентификация: `/auth/login` (Telegram deep-link) + `/auth/pending/{token}`, cookie-сессия.

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

## 8. Порядок сборки (полная версия, без MVP и заглушек)

Решение оператора (2026-10-20): промежуточных MVP нет, моки и заглушки запрещены, биржи —
все доступные, включая DEX. Работа ведётся параллельными потоками, каждый поток доводится
до рабочего состояния с живыми данными:

| Поток | Содержимое |
|---|---|
| **Ядро** | конфигурация, логирование, модели, шина, хранилище, запуск сервисов |
| **Агрегатор** | бары 1s/1m, окна, все метрики §2.3 с тестами на синтетике и сверкой с биржами |
| **Коллекторы** | 15 бирж: Binance (futures/spot/Alpha), Bybit, OKX, Bitget, Gate.io, MEXC, KuCoin, HTX, Kraken, Coinbase, Upbit, Hyperliquid, Aster, ApeX, Lighter — каталог, сделки, стакан, OI |
| **Детекторы** | тики, плотности, TWAP Hyperliquid, боты P1/C1/S1 |
| **API** | REST + 13 WS-каналов по контракту оригинала |
| **Интеграции** | Telegram-бот, Telegram-каналы (новости/ончейн/листинги), FCM, TradingView-котировки, терминалы |
| **Пользователи** | логин, сессии, тарифы, рефералка, команды, баны, конфиги, кабинет, админка |
| **Клиент** | все режимы и виджеты |
| **Инфраструктура** | compose, nginx, CI, деплой |

Подробная разбивка — `tasks/BACKLOG.md`.

---

## 9. Закрытые вопросы (после второй HAR-записи)

1. Транспорт — WebSocket, 13 каналов, snapshot/patch с seq, gzip-фреймы для алертов.
2. `dtpl5_pct` = сделки за 5 мин / сделки за 24 ч × 100. `graph_activity[24]` — счётчики
   активности по 6 метрикам × 4 окна.
3. `coi`: на инструменте приходит `coi_oi` (OI в базовом активе) + `coi_inverse`; агрегация
   по `coi_base` делается на сервере (5 окон, счётчики источников).
4. Боты: P1 strict timer, C1 micro-pressure, S1 spreader, рейтинг R0–R5; TWAP — индексатор
   Hyperliquid.
5. Терминалы: MetaScalp 17845+, Colibri 18845+, Vataga 5005, Tiger 7819, Scalpee 7356.
6. Алерты по порогам считаются на клиенте.

Остаётся неизвестным: точная формула рейтинга R0–R5 и внутренние пороги детектора ботов,
алгоритм выделения плотности (кратность к соседним уровням, время жизни). Берём свои
значения и выносим в конфиг.
