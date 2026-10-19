# Анализ сервиса GodsEye (oculusdei.pro)

> Дата разбора: 2026-10-19
> Источники: HAR-запись сессии (113 запросов к API, режимы Advanced / Simple / Graph / Tick),
> скриншоты интерфейса, публичные статические файлы сайта (страница входа, analytics.js, manifest).
> Версия клиента на момент разбора: `20261017-mexc-stocks-pro1`.
>
> Что НЕ попало в разбор (нужна вторая HAR-запись с фильтром All): WebSocket/SSE-поток
> событий (тики, боты, плотности, TWAP, история алертов), JS-код приложения, точные формулы
> детекторов. Всё, что помечено «гипотеза», выведено из структуры данных, а не из кода.

---

## 1. Что это за продукт

**GodsEye** («5m Market Watcher» в манифесте) — веб-скринер криптовалютных рынков в реальном
времени. Домены: `oculusdei.pro`, `oculusdei.ru`, `godseye.top`, `godseye.life`.

Ключевая идея: не «таблица с фильтрами», а **рабочее место скальпера**: сетка виджетов,
которые ловят события на ~1000+ инструментах 15 бирж и по клику открывают инструмент
в торговом терминале пользователя (MetaScalp, Colibri, Vataga, Tiger, Scalpee).

Целевая аудитория: скальперы на крипто-фьючерсах (RU-сегмент, интерфейс RU/EN).

### 1.1 Биржи (15)

Binance (futures, spot, Alpha), Bybit, Bitget, Gate.io, KuCoin, MEXC, Hyperliquid, OKX,
Kraken (futures `PF_*`, spot), Upbit, Coinbase, Aster, ApeX, HTX, Lighter.

Внутри ключ инструмента: `exchange:PAIR:market_type`, где `market_type ∈ {futures, spot}`,
а источник: `source_route_id = exchange_market` (`binance_futures`, `binance_alpha`,
`kraken_futures`, `gateio_spot`, …). Примеры из записи:
`kraken:PF_TAOUSD:futures`, `hyperliquid:ZEC/USDC:futures`, `binance_alpha:CT/USDT:spot`,
`mexc:DVNSTOCK_USDT:futures`, `lighter:XPL:futures`.

Отдельный признак `stock_scope: true` — токенизированные акции (`AAPLUSDT`, `AMZNUSDT`,
`DVNSTOCK_USDT`, …). На Binance Futures из 783 инструментов 224 помечены как stock.

Кросс-биржевая группировка по базовому активу: `coi_base = "crypto::BTC"` / `"stock::AAPL"`.

### 1.2 Рынки (параметр `route`)

`route=all_futures`, `route=all_in_one`, `route=all_ticks` (видны в записи).
Селектор «Рынок» в шапке: «All Futures» и другие наборы. Все API-запросы несут `route`,
то есть серверные наборы инструментов и настройки привязаны к рынку.

---

## 2. Режимы работы и страницы

Из `analytics.js` (карта страниц) и из тела `POST /api/auth/ping` (section/path):

| Путь | Раздел | Назначение |
|---|---|---|
| `/advanced` (`/advanced-next`) | Advanced mode | Основной режим: сетка виджетов + графики + TOP 50 |
| `/simple` | Simple mode | Таблица одного рынка по всем парам (783 на Binance Futures) |
| `/graph` | Graph mode | Подборка «кандидатов» по метрике + мини-графики |
| `/spike` | Tick mode | Секундные тики (1s-бары) |
| `/favorites` | Favorites mode | Избранное |
| `/chart` | Chart | Отдельный график |
| `/stakan` | DOM | Стакан |
| `/bot-feed`, `/density-feed`, `/tick-feed`, `/active-twaps` | Ленты | Боты, Liquidity Walls (плотности), тики, активные TWAP |
| `/settings`, `/settings-panel` | Settings | Панель настроек |
| `/help/natr5-dvol-spike` | Help | Справка по метрикам NATR5 / DVOL / Spike |
| `/tools/voice-lab`, `/voice-samples` | Voice Lab | Голосовые уведомления |
| `/admin/access`, `/admin/feedback` | Admin | Админка доступа и фидбэка |
| `/repair/cache`, `/repair/storage` | Repair | Сброс кэша / localStorage |
| `/diag/browser` | Diagnostics | Диагностика браузера |

В кабинете перечислены доступные режимы тарифа: **SIMPLE, ADVANCED, GRAPH, TICK, TRADES**.

---

## 3. Advanced mode: раскладка (по скриншоту)

**Шапка:** логотип, ссылки (соцсети, Telegram, YouTube, «Быстрый старт», «Команды»),
громкость, тема, аватар, колокольчик алертов, «Режим работы: ADVANCED», «Меню».

**Панель раскладки:** селектор конфига («Main», + / карандаш), «СЕТКА 5 × 2», «СБРОС».
Сетка = 5 колонок × 2 ряда виджетов, под ними 5 панелей графиков, привязанных к виджетам.

**Ряд виджетов (слева направо):**

1. **История алертов** — фильтр рынка (All Futures), звук. Колонки: Биржа (бейдж `KR F`,
   `GT F`, `BG F` = код биржи + F/S), Символ, VOL, DVOL. Используется как настраиваемый
   скринер.
2. **Тики** — строки: бейдж биржи, символ, % изменения, объём (`2m`, `13k`), иконки
   (бан, график). Секундные события: резкое движение цены или крупный объём.
3. **Боты** — карточки паттернов: `P1 | ALP S` (паттерн, биржа, рынок), символ, время,
   серия сделок `$8.3k @ 40:22` с изменением цены `-0.01%`. Повторяющиеся алгоритмические
   действия.
4. **Плотности** — карточки: биржа/символ, `250k @0.004514 0.12% LONG`, блок
   «Возможное влияние на цену: 5m: good +0.29%, 15m: weak -0.0112%».
5. **TWAP** — таблица: символ, `128/3…` (исполнено/серий), `2.4m$`, длительность `6d9h`,
   иконки. Переключатель SUM.

**Ряд графиков:** `KRAKEN:ARBUSD.P`, `BINANCE:USDCUSDT`, `BINANCE:DGAIUSDT.A`,
`ASTER:PUMPUSDT.P`, `HYPERLIQUID:ZECUSDC.P` (суффикс `.P` = perpetual, `.A` = Alpha).
На графике: свечи, объём, линия OI, маркеры ботов (`Bot R1`, `Bot R2`…), маркеры плотностей
(`LW`), таймфреймы `1s 1m 5m 15m 1h`, кнопки «ЗВУК», настройки, развернуть.

**Низ: TOP 50** — колонки: Биржа, Пин, Символ, TPM, Dvol%, L/S (два числа: объём лонг |
шорт), OI5m %, Настройки. Фильтр рынка, поиск символа, Top 50 / Collapse.

**Правая колонка:** ONCHAIN (лента CryptoAttack), НОВОСТИ (MarketTwits), ЛИСТИНГИ
(анонсы бирж).

Горячие клавиши на графике: `Shift` — линейка, `Shift+F` — в избранное. Клик по тикеру
копирует его и открывает в подключённом терминале. Клик по названию биржи открывает график
в последнем привязанном окне.

---

## 4. Меню настроек (полный список)

### Кабинет
Статус аккаунта, тариф (TRIAL, 3 дня), действует до, доступные режимы, профиль
(`Trader_<hex>`, `ID GEU-<32 hex>`, роль, публичный ник 3–24 символа, виден команде),
реферальная программа (код, короткая ссылка `/r/<CODE>`, приглашено / оплатили / оплат /
бонусы USDT, статистика переходов и регистраций), быстрые действия (платежи и продление,
поддержка, документация), тикеты.

### Настройки (с пометкой, на какой режим влияет)

| Пункт | Влияет на | Содержимое |
|---|---|---|
| **Алерты** | ADVANCED | Пороги срабатывания (см. §5) |
| **Telegram и Android** | Глобально | Свой бот-токен + chat ID; какие события слать: Алерты, Боты, Плотности, TWAP, Тики, Командные алерты. Очередь: до 8 событий в одном сообщении, не чаще 1 сообщения / 2 с, события старше 60 с отбрасываются; нужна открытая вкладка. Android-приложение: push через FCM, привязка 8-значным кодом (3 минуты), отдельный тумблер «Алерты с графиками» |
| **Автооткрытие стаканов** | ADVANCED | Цели выбранного терминала для алертов, тиков, ботов, TWAP и плотностей |
| **Настройки тиков** + **Кастомные тики** | ADVANCED, TICK | Звук, фильтры, монеты |
| **Настройки ботов** | ADVANCED | Паттерны, пороги тиков и биржи |
| **Настройки плотностей** | ADVANCED | Биржи и минимальный размер плотности |
| **Инфоблок** | ADVANCED | Новости, ончейн и листинги |
| **Отображение Алертов и Топ 50** | ADVANCED | Видимые колонки |
| **Конфиг** | Глобально | Сохранение/восстановление настроек (серверно: до 20 конфигов) |
| **Бан-лист** | Глобально | Вечные и часовые (24 ч) баны монет по разделам |

### Интерфейс
Маркеры/кнопки/виджеты графика (16/16), язык EN/RU, размер шрифта, смещение UTC для графиков,
горячие клавиши и иконки, «Сообщить об ошибке» (отчёт со скриншотами и журналом событий).

### Подключения (терминалы)
MetaScalp, Colibri, Vataga, Tiger, Scalpee — у каждого «Настройки и журнал», статус
(«Нет связи» / «Не проверено»), тумблер. Связь с терминалом идёт **из браузера на
localhost**: в записи видны `GET http://127.0.0.1:17845/ping` каждые ~15 с (MetaScalp включён,
терминал не запущен → ошибка). То есть автооткрытие стакана = локальный HTTP-API терминала.

---

## 5. Метрики и пороги алертов (точные определения из UI)

Окно настроек «Пороги алертов»:

- **Применить к рынку**: All Futures / …
- **Условия, которые должны совпасть** (чекбоксы): проверять лонг или шорт; OI5m; TPM;
  всплеск; Dvol%; Natr5.
- **Пороги:**

| Поле | Определение (цитата из UI) |
|---|---|
| TPM алерт | Сделок в минуту |
| Лонг алерт | Объём покупок в USDT / USDC |
| Шорт алерт | Объём продаж в USDT / USDC |
| Алерт всплеска | Во сколько раз объём за последние 5 минут выше среднего объёма завершённых пятиминуток за предыдущий час |
| Dvol% алерт | Процентное отношение объёма за последние 5 минут к суточному объёму |
| Natr5 алерт | NATR(14) по 5-минутным свечам — текущая волатильность цены, % |
| OI5m алерт | Изменение открытого интереса за 5 минут в %. Только фьючерсы |
| Кулдаун, мин | Минимальная пауза между повторными алертами по одной паре (по умолчанию 15) |

Кнопки: «Применить настройки», «Вписать нули».

Серверные лимиты алертов с графиков (`/api/chart-alerts/v1`): `max_active: 30`,
`max_active_per_key: 5`.

---

## 6. REST API (полный контракт из записи)

Все ответы `application/json`, gzip, `cache-control: no-store`. Фронт — nginx/1.26.3.
Ошибки в стиле FastAPI: `{"detail":"auth required"}` (401), `{"detail":"Not Found"}` (404).
Каждый запрос несёт `?route=<market>`.

### 6.1 Аутентификация

```
GET  /advanced?route=all_futures      → 303 → /auth/login?next=<url>
GET  /auth/login                       → HTML, кнопка «Войти через Telegram»
                                         data-auth-status-url="/auth/pending/<TOKEN32>"
                                         href="https://t.me/oracle_wg_bot?start=login_<TOKEN32>"
GET  /auth/pending/<TOKEN32>           → {"status":"pending","poll_after_ms":2000}
                                         | {"status":"authenticated","redirect_url":...}
                                         | 410 expired | 409 completed | 404 not found
```
Клиент опрашивает каждые 2 с. Пароля нет, только Telegram-бот. Сессия — cookie.

```
POST /api/auth/ping
  body: {"section":"advanced","path":"/advanced",
         "visible_sections":[{"section":"advanced","path":"/advanced"}],
         "active":true,"reason":"load|visible|hidden|focus|interval"}
  resp: {"ok":true,"tracked_at":1792413300,"bypass":false,"welcome_notice":null}
```
Присутствие/активность пользователя: при загрузке, смене видимости вкладки, фокусе и по
интервалу (~60 с). Используется для учёта сессий и, вероятно, ограничения одновременных окон.

```
GET /api/auth/settings-configs → {"max_items":20,"count":0,"items":[]}
GET /api/teams?route=all_futures
  → {"teams":[],"preferences":{"voice":0,"mobile":0,"signal_mode":"simple"},
     "history":[],"user_id":"GEU-..."}
GET /api/client_release?route=…&_=<ts>&cv=<client_version>
  → {"version":"20261017-mexc-stocks-pro1","poll_ms":15000,"speech_text":""}
```
`client_release` опрашивается каждые 15 с; при несовпадении версии клиент перезагружается.
`speech_text` — серверное голосовое объявление.

### 6.2 Каталог и метрики (Simple mode)

```
GET /api/simple/v1/pairs?route=all_in_one&source=binance_futures&catalog_version=<v|пусто>
```
Ответ (~760 КБ без каталога ~210 КБ gzip), опрос каждые ~31 с:
```json
{
  "catalog_version": "v1-<sha256>",
  "catalog": { "<key>": {exchange, pair, market_type, display_symbol, display_pair,
               display_name, stock_scope, quote_currency, volume_currency,
               contract_multiplier, source_route_id, source_title, coi_base} } | null,
  "metrics": { "<key>": [vol24h, chg24h_pct, dvol5_pct, tpm, natr5,
                          [28 × graph_metrics], [7 × simple_24h_metrics]] },
  "coi":     { "<coi_base>": [[d1, d2, d3, d4, d5], [n1, n2, n3, n4, n5], n_total, ts] }
}
```
Если `catalog_version` клиента совпадает, `catalog` приходит `null` (дельта-обновление).

**Раскладка `graph_metrics` (28 = 4 окна × 7 слотов)**, окна `1m, 5m, 15m, 1h`,
слоты `[range_pct, natr, volume_quote, oi_change_pct, dvol_pct, dtpl_pct, tpm]`:

| Окно | Заполненные слоты |
|---|---|
| 1m | range, volume, oi_change, tpm |
| 5m | range, natr(14), volume, oi_change, dvol, dtpl |
| 15m | range, volume, oi_change |
| 1h | range, volume, oi_change |

`simple_24h_metrics` = `[range24h, -, volume24h, oi_change24h, -, -, -]`.
Проверено на всех 783 инструментах: `metrics[2] == graph[5m].dvol`, `metrics[4] == graph[5m].natr`.

`dtpl` — гипотеза: «delta trades per …» / доля дельты объёма; точное определение нужно из JS.

**`coi`** (гипотеза: combined open interest по базовому активу на всех биржах):
5 дельт OI по окнам (вероятно 1m/5m/15m/1h/24h), 5 счётчиков источников по окнам,
общее число источников, timestamp. Пример `crypto::BTC`: 15 источников.

### 6.3 Кандидаты (Graph mode)

```
GET /api/graph/v1/candidates?sort=range5m&limit=160
    &metric_filters={"volume5m":10000,"dvol5m":4,"tpm1m":20}&route=all_futures
```
```json
{"type":"graph_candidates","sort":"range5m","metric_filters":{…},"min_volume_5m":10000,
 "limit":96,"count":6,"total_matched":6,"updated_at":1792413438.4,
 "items":[{key, exchange, pair, market_type, display_*, quote_currency,
           contract_multiplier, source_route_id, volume_5m, range5_pct, natr5,
           dvol5_pct, dtpl5_pct, oi_percentage, tpm, sort_value,
           graph_metrics[28], simple_24h_metrics[7],
           metric_activity{range1m, volume1m, tpm1m, range5m, natr5m, volume5m, oi5m,
                           dtpl5m, range15m, volume15m, oi15m, range1h, volume1h, oi1h,
                           dvol5m}, stock_scope}]}
```
Сервер режет `limit` до 96. `metric_activity` — целые 0…~60 на метрику (гипотеза: ранг
или счётчик активности за последние N окон).

Доступные ключи сортировки/фильтров = имена в `metric_activity`.

### 6.4 История баров (графики)

```
POST /api/graph/v1/history_batch?route=all_futures
  body: {"items":[{"key":"kraken:PF_TAOUSD:futures","timeframe":"1m","exchange":"kraken",
                   "pair":"PF_TAOUSD","market_type":"futures",
                   "source_route_id":"kraken_futures","limit":360,"include_partial":1}],
         "include_partial":1,"feature":"advanced|graph"}
```
```json
{"type":"graph_history_batch","count":1,"received":1,"max_items":64,"accepted":1,
 "omitted":0,"truncated":false,"error_count":0,"limit_reason":"",
 "items":{"<key>|1m":{"key","interval_sec":60,"retention_hours":216,
    "bars":[{ts,o,h,l,c,buy_quote,sell_quote,total_quote,trades_count,
             oi_close,oi_open,oi_high,oi_low}, …360],
    "partial":{…текущий незакрытый бар…},"source_route_id"}},
 "errors":{}}
```
Таймфреймы в записи: `1m` (limit 360, retention 216 ч = 9 дней) и `1s` (limit 1800 =
30 мин, retention 72 ч; поля `oi_close, oi_value, had_trade`). До 64 инструментов за запрос.
Размер ответа на 16 инструментов × 360 баров: 1.29 МБ (225 КБ gzip), 167 мс.

Таймфреймы `5m/15m/1h` на графике, вероятно, агрегируются на клиенте из 1m.

### 6.5 Кластеры (footprint) — сервис `marketstore`

```
POST /api/marketstore/clusters/snapshot_batch?route=all_futures
  body: {"items":[{"request_id":"cluster-1","key":"…","timeframe":"1h","zoom":1,
                   "exchange","pair","market_type","source_route_id","price_step":0.5}]}
```
```json
{"type":"marketstore_cluster_batch","max_items":64,…,
 "items":{"cluster-1":{key, source:"marketstore_clusters", exchange, market, symbol,
   TimeFrame:"1h", timeframe, ZoomIndex, zoom, PriceIncrement, updated_at (RFC3339 ns),
   Columns:[{StartTime, EndTime, PriceIncrement, AsksSum, BidsSum,
             Items:[{Price, AskSize, BidSize, TradeCount}], Aggregated:true, PriceAnchor}],
   source_route_id}}}
```
Объём по ценовым уровням за час (кластерный/футпринт-график): покупки по ask, продажи по
bid, число сделок. `price_step` задаёт клиент. Обновляется каждые ~15 с для открытых графиков.
PascalCase-поля и наносекундный `updated_at` выдают отдельный сервис на Go.

### 6.6 Алерты и справка

```
GET /api/chart-alerts/v1?route=…   (каждые 15 с)
  → {"type":"chart_signal_alerts","max_active":30,"max_active_per_key":5,"alerts":[]}
GET /api/help-content/quick_start
  → {"key","html","updated_at","updated_by":"@maksim_f","is_default":false}
```
Справка редактируется в админке (CMS-подход: HTML-блоки с классами `summary-help-*`).

### 6.7 Частоты опроса (сводка)

| Эндпоинт | Интервал | Триггер |
|---|---|---|
| `client_release` | 15 с | таймер |
| `chart-alerts/v1` | 15 с | таймер |
| `auth/ping` | ~60 с + события видимости | таймер / visibility / focus |
| `simple/v1/pairs` | ~31 с | таймер (Simple mode) |
| `clusters/snapshot_batch` | ~15 с на открытый график | открытие графика / таймер |
| `graph/v1/history_batch` | при открытии графика / смене ТФ | событие |
| `graph/v1/candidates` | при входе в Graph mode | событие |
| `127.0.0.1:17845/ping` | ~15 с | тумблер терминала включён |

Реальное время (тики/боты/плотности/TWAP/алерты) в этой записи не видно → отдельный
WebSocket или SSE-канал. Нужна HAR-запись с фильтром All.

---

## 7. Наблюдения по инфраструктуре

- **Фронт:** nginx 1.26.3, HTTP/2, CSP, X-Frame-Options, строгий `permissions-policy`.
- **Backend API:** Python (стиль ошибок FastAPI/Starlette, 303-редирект на логин).
- **Сервис кластеров:** Go (`marketstore`, PascalCase JSON, RFC3339 с наносекундами).
- **Релизы:** версия вида `YYYYMMDD-<feature>-<tag>` в заголовке `x-godeye-client-release`
  и в query `?v=` статики; клиент сам перезагружается при смене версии.
- **Аналитика:** Google Tag Manager (`GTM-N5TTLLKT`), GA4 (`G-9H9V16DBNW`), Microsoft
  Clarity. В `analytics.js` жёсткая фильтрация PII (запрещены параметры с token/chat_id/…).
- **Локальное хранилище:** страницы `/repair/cache`, `/repair/storage` → настройки и кэш
  живут в localStorage/IndexedDB; «Конфиг» синхронизирует их на сервер (до 20 штук).
- **Авторизация:** только Telegram-бот `@oracle_wg_bot`; trial 3 дня; тарифы, рефералка.

---

## 8. Что именно копировать в своём сервисе (приоритеты)

1. **Ядро данных:** 1s/1m бары с buy/sell quote-объёмом, числом сделок и OI OHLC; окна
   1m/5m/15m/1h/24h; метрики range, NATR(14) 5m, volume, OI%, Dvol%, TPM, spike.
2. **Движок алертов** с порогами из §5 и кулдауном на пару.
3. **TOP 50 / История алертов** — таблица + лента.
4. **Детекторы событий:** тики (1s), плотности стакана (с оценкой влияния на цену 5m/15m),
   TWAP, боты (паттерны серий сделок).
5. **Графики** с маркерами событий и кластерами.
6. **Доставка:** Telegram-бот, звук/голос, автооткрытие в терминале через localhost.
7. **Обвязка:** Telegram-логин, trial/тарифы, рефералка, команды, бан-лист, конфиги.

Что НЕ копировать: бренд, иконки, тексты, стилистику «Рик и Морти» — это их контент.
