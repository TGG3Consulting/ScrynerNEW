# GodsEye: протокол реального времени и клиентская логика

> Источник: полная HAR-запись Advanced mode (фильтр All), 230 запросов, 13 WebSocket-соединений,
> 69 JS-файлов приложения (2,5 МБ), 3 минуты работы. Дата: 2026-10-20.
> Дополняет `GODSEYE_ANALYSIS.md` (REST-часть).

---

## 1. Общая схема

```
браузер ──► /ws/all/v1          таблица TOP 50 (50 инструментов, патчи ~4 с)
        ──► /ws/all/alerts/v1   поток метрик для алертов (219+ инструментов, gzip, ~4 с)
        ──► /ws/tick            тики, ~90 событий/с, без подписки
        ──► /ws/density/feed    плотности (history + live)
        ──► /ws/active_twaps    TWAP Hyperliquid (snapshot/patch ~1 с)
        ──► /ws/graph_charts/v1 бары 1m по подписке (closed + partial, ~0.4 с)
        ──► /ws/summary_bots    боты (текстовые карточки)
        ──► /ws/summary_news    новости (Telegram-каналы)
        ──► /ws/summary_onchain ончейн (Telegram-каналы)
        ──► /ws/summary_listings листинги
        ──► /ws/stock/reference-prices/v1  котировки акций (TradingView)
        ──► /ws/v3              summary (Simple mode)
        ──► /ws/team-signals    командные сигналы
```

Общие правила:
- Все WS на том же хосте, аутентификация cookie сессии.
- Клиент шлёт `{"ping":1}` (или строку `ping`) каждые ~25 с, сервер отвечает `pong`.
- Каналы состояния используют **snapshot/patch с seq**:
  ```json
  {"type":"snapshot","version":3,"seq":17254,"data":{"<key>":{...}}}
  {"type":"patch","version":3,"base_seq":17254,"seq":17255,
   "updates":{"<key>":{поля-дельты}},"unset":{"<key>":["поле"]},"delete":["<key>"]}
  ```
  Клиент применяет патч только если `base_seq` равен текущему `seq`, иначе переподключается.
- Ленты (feeds) используют `hello` → `history` → отдельные события.
- Канал алертов может слать бинарные gzip-фреймы (`frame_encoding=gzip`).

Параметры фильтрации передаются в query при подключении:
```
market_scope=futures|spot|all   exclude_stocks=1
source_futures=apex,aster,binance,binance_alpha,bitget,bybit,gateio,htx,hyperliquid,kraken,kucoin,lighter,mexc,okx
source_spot=...                 route=all_futures
```

---

## 2. `/ws/all/v1` — TOP 50

Query: `limit=50&sort=totalVolume&dir=desc` + фильтры источников.
После подключения клиент шлёт `{"type":"subscribe","pinned_keys":[]}` (закреплённые пары
всегда входят в выдачу).

Запись инструмента (все поля присутствуют всегда):

| Поле | Тип | Смысл |
|---|---|---|
| `exchange`, `pair`, `market_type` | str | ключ `exchange:pair:market_type` |
| `display_symbol`, `display_pair`, `display_name` | str/null | отображение (для акций и Alpha) |
| `asset_class` | `crypto` / `stock` | класс актива |
| `stock_scope` | bool | токенизированная акция |
| `chain_name`, `contract_address`, `base_asset_id` | null/str | для DEX-токенов |
| `source_route_id`, `source_title`, `source_port` | str, str, int | источник: `binance_futures`, «Binance Futures», порт внутреннего сервиса (8700) |
| `last_price`, `last_price_ts`, `fair_price` | float | последняя цена, mark-price |
| `buy_sum`, `sell_sum` | float | объём покупок/продаж в quote **с последнего сброса** (`reset_interval` = 5 мин) |
| `buy_sum_5m`, `sell_sum_5m` | float | скользящие 5 минут |
| `buy_volume_1s`, `sell_volume_1s` | float | последняя секунда |
| `tpm` | int | сделок за минуту |
| `big_trade_side`, `big_trade_volume` | str, float | крупнейшая сделка в окне |
| `price_change_pct` | float | изменение цены за окно сброса (5 мин) |
| `price_change_24h_pct` | float | за сутки |
| `range5_pct` | float | (high-low)/low за 5 мин |
| `natr5` | float | NATR(14) по 5m-свечам, % |
| `spike_ratio` | float | объём 5m / средний объём завершённых 5m за час |
| `dvol5_pct` | float | объём 5m / объём 24h × 100 |
| `dtpl5_pct` | float | **сделок за 5 мин / сделок за 24 ч × 100** (из i18n: «DTPL5 — сделки за 5 минут в отношении сделок за 24 часа») |
| `oi_percentage` | float | изменение OI за `reset_interval` (5 мин), % |
| `oi2_percentage`, `oi2_reset_interval` | float, 1440 | изменение OI за сутки («OI1d %») |
| `oi_vol` | str | OI в quote, сокращённо («92.7m») |
| `volume_24h`, `tick_volume_24h`, `tick_volume_currency`, `tick_volume_updated_ts` | | суточный объём |
| `tick_threshold`, `tick_threshold_base`, `tick_threshold_multiplier`, `tick_threshold_source` | float, float, float, `local_minute_history` | порог тика для инструмента (см. §4) |
| `tick_threshold_error*`, `trade_contract_error*` | | диагностика источника |
| `quote_currency`, `volume_currency`, `contract_multiplier` | | нормализация контрактов |
| `graph_metrics[28]` | | 4 окна × 7: `[range, natr, volume, oi_change, dvol, dtpl, tpm]` для 1m/5m/15m/1h |
| `graph_activity[24]` | int/null | 4 окна × 6 (без natr): счётчики активности по метрикам, окна 1m/5m/15m/1h; слоты `[range, -, volume, -, -, tpm]`… фактически заполнены 14 из 24 |
| `simple_24h_metrics[7]` | | `[range24h, -, volume24h, oi_change24h, -, -, -]` |
| `coi_oi`, `coi_oi_ts`, `coi_inverse` | float, ts, bool | OI инструмента в базовом активе для расчёта COI; `coi_inverse` — инверсный контракт |

Патчи приходят каждые ~4 с и содержат только изменившиеся поля; `delete` — выпавшие из топа.

Колонки TOP 50 (из `columns` в main.js): Exchange, Pin, Coins, 1d Vol, TPM, Spike, Dvol%,
Natr5, L/S, Long, Short, OI5m %, OI1d %, OI VOL, Price, Price Change, Settings.

Внутренние порты источников (`source_port`): bitget 8100, bybit 8300, gateio 8500,
binance 8700, hyperliquid 9200, okx 9300, aster 10000, mexc 10200, apex 10600, lighter 10800.
То есть на бэкенде **один процесс на биржу**, API-шлюз собирает их по портам.

---

## 3. `/ws/all/alerts/v1` — поток для алертов

Query: `limit=20000&min_vol=50000&min_oi1=2&min_dvol5=5&frame_encoding=gzip` + источники.
`min_*` = минимум из порогов пользователя по всем парам (клиент вызывает `takeMin("min_oi1",
s.oi_threshold)` и т. д.), чтобы сервер отсекал заведомо не проходящие инструменты.

Запись (22 поля): `exchange, pair, display_*, source_route_id, market_type, quote_currency,
buy_sum_5m, sell_sum_5m, buy_sum, sell_sum, oi_percentage, oi2_percentage, oi_vol,
spike_ratio, dvol5_pct, natr5, price_change_pct, tpm, volume_24h, stock_scope`.

### 3.1 Логика алерта — ВЫПОЛНЯЕТСЯ НА КЛИЕНТЕ (`evaluateAlertState` в main.js)

```
buy  = buy_sum  (с последнего сброса, 5 мин)   sell = sell_sum
oi1  = oi_percentage (null для spot)           tpm, spike = spike_ratio, dvol = dvol5_pct, natr5

condBuy   = buy_threshold  > 0 && buy  >= buy_threshold
condSell  = sell_threshold > 0 && sell >= sell_threshold
condBs    = condBuy || condSell
condOi    = !spot && oi_threshold > 0 && oi1 >= oi_threshold
condTpm   = tpm   >= tpm_threshold
condSpike = spike >= spike_threshold
condDvol  = dvol  >= dvol_threshold
condNatr5 = natr5 >= natr5_threshold

requires = [cond_i for i in включённых чекбоксов «проверять …»]
trigger  = all(requires)            если есть хотя бы один чекбокс (AND)
         = any(активных порогов>0) иначе (OR по заданным порогам)
```
Причины (`reasons`) собираются для текста: `Long`, `Short`, `OI5m`, `TPM`, `Spike`, `Dvol%`,
`Natr5` с значением и порогом.

Настройки по паре (`HARD_DEFAULT_PAIR_SETTINGS`): `buy_threshold, sell_threshold,
oi_threshold, tpm_threshold, spike_threshold, dvol_threshold, natr5_threshold, pinned,
hidden, hidden_until, alert_cooldown_minutes, require_buy_or_sell, require_oi1, require_tpm,
require_spike, require_dvol, require_natr5`. Пороги задаются на рынок (route) и могут
переопределяться на пару.

Кулдаун: `alert_cooldown_minutes` пары, иначе глобальный `prefs.alert_cooldown_minutes`
(по умолчанию 15, с ограничением min/max), хранится как «local cooldown bans» в localStorage.

Текст Telegram-уведомления:
```
[ALERT] BINANCE F BTCUSDT
Long, OI5m
Long 2.7m | Short 1.9m | OI5m 0.31% | TPM 271
```

---

## 4. `/ws/tick` — тики

Без подписки, сервер шлёт все тики по включённым источникам, ~90 сообщений/с в записи
(17 342 за 193 с). Одно сообщение = одна секунда одного инструмента, в которой объём
превысил порог:

```json
{"exchange":"kucoin","pair":"OKLOUSDTM","market_type":"futures","display_pair":"OKLOUSDTM",
 "buy":0.0,"sell":819.21,"timestamp":1792488379.0,"window_sec":1.0,
 "price":26.67,"price_open":26.68,"price_high":26.68,"price_low":26.6,"price_ts":1792488379.407,
 "price_move_pct":-0.0375,"price_range_pct":0.3008,"volatility_oc":-0.0375,"volatility_hl":0.3008,
 "tick_threshold":117.03,"tick_threshold_base":117.03,"tick_threshold_multiplier":1.0,
 "volume_24h":397254.81,"volume_currency":"USDT",
 "stock_scope":true,"stock_symbol":"OKLO","display_symbol":"OKLO","display_name":"Oklo"}
```

`price_move_pct = (close-open)/open·100`, `price_range_pct = (high-low)/low·100` внутри секунды.

### 4.1 Порог тика (`tick_threshold_source: local_minute_history`)

Считается на сервере по суточному объёму инструмента. Точная формула (подобрана по 2 400
уникальным точкам с нулевым остатком, отдельно по рынкам):
```
threshold_base = coef · volume_24h^0.56   (USD)
coef: futures 3/35 = 0.085714, spot 1.8/35 = 0.051429 (0.6 × futures), Aster 1/35 = 0.028571
clamp: min 100 (HTX: 50), max 25 000
```
Примеры: 2.3k$/сутки → 100; 1.757M futures → 269; 1.74M spot → 161; 5.0B → 23 128; ≥7B → 25 000.
`tick_threshold = base × multiplier` (множитель задаёт пользователь в настройках тиков).
Клиент дополнительно фильтрует: «Always play if volume ≥», «if volatility ≥», кастомные правила
на пару (до 200 на биржу и рынок), бан-лист (1 ч / 24 ч / навсегда).

Распределение источников тиков в записи: binance 32%, bybit 12%, gateio 11%, okx 10%,
bitget 8%, mexc 7%, hyperliquid 5%, kucoin 4%, kraken 3%, htx 3%, binance_alpha 2%, aster 2%,
lighter 1%, apex <1%. Futures 64%, spot 36%.

---

## 5. `/ws/density/feed` — плотности (Liquidity Walls)

```json
{"type":"history","status":"live","configured":true,"client_count":16,"items":[...80]}
```
Элемент:
```json
{"event":"density.message","id":"42700","captured_at":1792477253.65,
 "exchange":"bitget","exchange_label":"Bitget Futures","source_route_id":"bitget_futures",
 "market_type":"futures","pair":"SQQQUSDT","display_pair":"SQQQUSDT","display_symbol":"SQQQ",
 "quote_currency":"USDT","base":"SQQQ","quote":"USDT","meta_symbol":"SQQQUSDT","native_symbol":"",
 "side":"bid","price":37.77,"size_usd":204382.16,"distance_pct":0.171799,"is_iceberg":false,
 "title":"","metric_line":"🟩 204k @ 37.77 | 0.1718%",
 "text":"Bitget Futures\nSQQQUSDT\n🟩 204k @ 37.77 | 0.1718%\n| Price impact:\n| 5m: weak -0.1198%\n| 15m: weak -0.5068%",
 "source":"","source_type":"density_alert"}
```
`side`: `bid` (🟩, поддержка) / `ask` (🟥). `distance_pct` — расстояние от цены.
«Price impact» — изменение цены за 5m/15m после появления плотности с оценкой `good` / `weak`
(знак совпадает со стороной → good). Живые события приходят тем же форматом без обёртки.
Настройки: включённые биржи + минимальный размер в USD на биржу.

---

## 6. `/ws/active_twaps` — TWAP Hyperliquid

Источник `hypurrscan` (индексатор Hyperliquid). Snapshot/patch:
```json
"__meta__": {"kind":"meta","source":"hypurrscan","status":"live","count":239,"active_count":239,"last_success_ts":...}
"__bans__": {"kind":"bans","source":"file","tokens":["bnb","btc","eth","paxg"],"prefixes":["cash:","vntl:","xyz:"],
             "min_usd_per_30s_by_token_side":{"eth":{"sell":62000},...},
             "min_usd_per_minute_by_token":{"sol":352000,...}}
"t:<hash>": {"kind":"twap","id","hash","time_ms","expires_ms","duration_min":1440,"user":"0x...",
             "block","market_id":10334,"market":"spot|perp","market_label","side":"BUY","direction":"buy",
             "token":"KNTQ","pair":"@334/USDC","display_symbol","display_pair":"KNTQ/USDC",
             "chart_key":"hyperliquid:%40334/USDC:spot","amount":1201157.17,"amount_text",
             "reduce_only":false,"randomize":false,"exchange":"hyperliquid","market_type","source_route_id"}
"p:<hash>": {"kind":"presence","id":"<hash>"}      // живой TWAP
"m:<market_id>": {"kind":"market","market_id":10334,"price":35.95}
```
Появление TWAP = патч с `p:` и `t:`, завершение = `delete` обоих ключей. Виджет считает
`исполнено/серий`, сумму в $ и длительность. «TWAP только для futures» в Tick mode.

---

## 7. `/ws/graph_charts/v1` — живые бары

```json
→ {"type":"subscribe","items":[{"key","timeframe":"1m","exchange","pair","market_type","source_route_id"}, …]}
← {"type":"subscribed","count":4,"sources":3}
← {"type":"chart","key":"kraken:PF_ARBUSD:futures","timeframe":"1m",
    "closed":{ts,o,h,l,c,buy_quote,sell_quote,total_quote,trades_count,oi_close,oi_open,oi_high,oi_low},
    "partial":{…текущий бар…},"source_route_id":"kraken_futures"}
```
История грузится через `POST /api/graph/v1/history_batch`, далее обновления по WS
(~2 сообщения/с на 4 подписки). Повторный `subscribe` заменяет набор подписок.

---

## 8. `/ws/summary_bots` — боты

```json
{"type":"hello","connections":5,"history_size":38}
{"type":"history","items":[{"captured_at":"2026-10-20T06:44:19+00:00","text":"..."}]}
{"captured_at":"...","text":"..."}      // живое событие
```
Карточка — текст фиксированного формата:
```
P1 🟩 [BYBIT FUTURES]        ← паттерн, сторона (🟩 buy / 🟥 sell, ▼ давление вниз), источник

BTWUSDT R2                   ← пара, рейтинг R0…R5

🟨 ~3.5s                     ← средний интервал между сделками
C1 pressure: +1.79% over 300s | side vol 86%   ← только для C1

🟩 $3.6k @ 45:11 | +0%       ← сделки: сумма, mm:ss, движение цены
🟩 $3.3k @ 45:16 | +0.02%
```
Паттерны (i18n): **P1 · Точный таймер** (равные сделки через равные интервалы),
**C1 · Микродавление** (серия мелких сделок одной стороны, давление на цену за 300 с и доля
объёма стороны), **S1 · Спредер**. Настройки: паттерны, минимальный объём каждого тика (USD),
минимальное движение каждого тика (%), биржи, «скрывать ботов R0». В записи: P1 R0 — 47,
P1 R5 — 16, P1 R1 — 9, C1 R0 — 3.

---

## 9. `/ws/summary_news`, `/ws/summary_onchain`, `/ws/summary_listings`

Один формат: `hello` → `history` (80 элементов) → живые `telegram.message`:
```json
{"event":"telegram.message","captured_at":"...","source_type":"message",
 "message":{"id","date","edit_date","grouped_id","text","raw_text","post","post_author","views","forwards",
            "media":{"kind","has_media","visual","mime_type","size_bytes","data_url"}},
 "chat":{"id","title","username","kind":"channel|megagroup","verified","broadcast","megagroup","photo"},
 "sender":{"id","name","username","kind","verified","bot","photo"}}
```
Источники — публичные Telegram-каналы, читаются userbot-клиентом (Telethon/MTProto):
- Новости: `MarketTwits` (@markettwits), «КриптоАтака 24».
- Ончейн: «Onchain Intrigue | Powered by Cielo» (@OnChainIntrigue), CryptoAttack, Lookonchain.
- Листинги: сообщения без `chat` (внутренний источник / ручная лента), 37 элементов истории.

---

## 10. `/ws/stock/reference-prices/v1`

```json
→ {"type":"subscribe","listings":["NASDAQ-SNDK"]}
← {"type":"snapshot","version":3,"seq":1,"data":{"NASDAQ-SNDK":{"status":"live","source":"tradingview_live_quote",
    "listing":"NASDAQ-SNDK","price":1332.8,"currency":"USD","session":"pre_market","quote_time_ms":...,
    "earnings":{"date":"2026-10-29","time":1,"publication_type":12}}}}
```
Референсная цена базовой акции для токенизированных акций (`stock_scope`), источник —
TradingView live quote. Используется для отклонения токена от биржи.

---

## 11. Терминалы (локальные мосты, из `*-local.js`)

| Терминал | Порт(ы) | Протокол |
|---|---|---|
| MetaScalp | 17845+ (перебор диапазона, кэш `metaScalpSelectedPort_v1`) | HTTP `GET /ping`, `POST /api/change-ticker`, `POST /api/combo`, `GET /api/ui/state`, `/api/connections`, `/api/connections/{id}/tickers` |
| Colibri | 18845+ | HTTP, `/api/change-ticker`, `/api/connections` |
| Vataga | 5005 | `GET /api/open/{partner_ID}/{exchange}/{type}/{symbol}` |
| Tiger | 7819 | HTTP/WS, `ping`, `open symbol` (link groups до 20) |
| Scalpee | 7356 | HTTP, `ping`, `open ticker` |

Разрешённые Origin в терминалах: oculusdei.pro/.ru/.tech, godseye.top/.life.
Ротация целей: `alert_rotation_sequence` по кругу между привязками (Meta binding,
Colibri slot, Tiger link group, Scalpee link group).

---

## 12. Остальные REST-вызовы из кода клиента

| Путь | Назначение |
|---|---|
| `GET /api/all/v1/snapshot` | снапшот TOP без WS |
| `GET /api/all/v1/keys?…` | список ключей по источникам |
| `GET /api/all/v1/summary_filter_metadata?window=24h&include_partial=1` | метаданные фильтров (диапазоны метрик) |
| `GET /api/v2/summary_filter_metadata`, `GET /api/v2/pairs` | версии для Simple |
| `GET /api/history/seconds/{key}`, `GET /api/history/{kind}/{key}` | история 1s / другие |
| `GET /api/marketstore/clusters/snapshot/{key}` | одиночный кластер |
| `DELETE /api/pairs/{key}`, `POST /api/pairs/{key}/reset` | админ: удалить / сбросить пару |
| `GET /api/v2/lighter/market-id/{symbol}` | маппинг символа Lighter → market id |
| `POST /api/mobile-alerts/{action}` | привязка Android (код, устройства) |
| `/api/teams`, `/api/teams/actions/{action}` | команды |
| `POST /api/v1/feedback/reports` | «Сообщить об ошибке» |
| `/api/auth/welcome-notice/{rev}/content`, `/ack` | приветственные уведомления (админ: `/api/admin/welcome-notices`) |
| `/api/gen_config` | генерация конфига MetaScalp |

---

## 13. Клиентские модули (что у них есть, что делать нам)

`main.js` (640 КБ, Advanced), `advanced-next.js`, `graph-native-core.js` (Graph mode),
`tick-feed-core.js`, `active-twap-feed-core.js`, `summary-feed-core.js` (боты/новости/ончейн),
`chart-*.js` (Lightweight Charts 5.2.0 + маркеры сигналов, кластеры, алерты с графика),
`global-audio.js` + `tts-voice-policy.js` (звук и голос), `telegram-notification-core.js`,
`mobile-notification-core.js` (FCM), `team-signals-*.js`, `terminal-auto-open-core.js` +
`*-local.js` (терминалы), `favorites-store.js`, `settings-backup-core.js` (конфиги),
`browser-storage.js` (IndexedDB), `exchange-switcher.js` (маршруты), `i18n.js` (RU/EN),
`help-modal.js` + `help-content-editor.js` (справка/CMS), `hotkeys-icons.js`,
`client-release.js` (автообновление), `auth-presence.js` (ping), `wake-lock.js`,
`discord-presence.js`.

Маршруты (`route`): `all_futures`, `all_spots`, `all_stocks`, `all_in_one`, `all_ticks`,
а также per-source: `binance_futures`, `binance_spot`, `binance_alpha`, `bybit_*`, `okx_*`,
`bitget_*`, `gateio_*`, `mexc_*`, `kucoin_*`, `htx_futures`, `kraken_*`, `coinbase_spot`,
`upbit_spot`, `hyperliquid_*`, `aster_*`, `apex_futures`, `lighter_futures`.
