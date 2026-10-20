# BACKLOG

Нумерация: `S<поток>-<номер>`. Статусы: `[ ]` не начато, `[~]` в работе, `[x]` готово.

> Решение оператора 2026-10-20: без MVP, без заглушек и моков, все биржи включая DEX.
> «Этапы» ниже — параллельные потоки работ, а не очередность релизов.

## Этап 0 — Каркас
- [ ] S0-01 Пакет `src/scryner` с точкой входа `python -m scryner` (asyncio.gather сервисов)
- [ ] S0-02 Конфигурация: pydantic-settings + `config/scryner.yaml`, пример в репозитории
- [ ] S0-03 Логирование structlog JSON, уровни по сервисам
- [ ] S0-04 Шина событий: интерфейс + in-memory реализация (dev) + Redis Streams (prod)
- [ ] S0-05 Хранилище: интерфейс репозиториев + SQLite (dev), миграции
- [ ] S0-06 pytest + pytest-asyncio, фикстуры, первый тест на конфиг
- [ ] S0-07 CI (GitHub Actions): ruff, mypy, pytest
- [ ] S0-08 Dockerfile + docker-compose (redis, timescaledb, api)

## Этап 1 — Агрегатор и метрики (ядро данных)
- [ ] S1-01 Единый каталог инструментов (key, tick_size, step_size, multiplier, quote, coi_base, stock_scope, asset_class)
- [ ] S1-02 Базовый класс коллектора: подписки пачками, reconnect, контроль лага, нормализация
- [ ] S1-03 OI-поллеры с rate-limit на биржу
- [ ] S1-04 Агрегатор 1s-баров (кольцо 30 мин) с buy/sell quote, trades_count, oi_close
- [ ] S1-05 Агрегатор 1m-баров с OI OHLC, запись в БД, retention 9 дней
- [ ] S1-06 Окна 5m/15m/1h/24h из 1m, метрики range/volume/oi_change/tpm/dvol/natr5/spike
- [ ] S1-07 Упаковка метрик в массив `[vol24h, chg24h, dvol5, tpm, natr5, graph[28], day[7]]`
- [ ] S1-08 `GET /api/catalog` с `catalog_version` и дельтой
- [ ] S1-09 `POST /api/history_batch` (1s/1m, limit, include_partial, до 64 ключей)
- [ ] S1-10 `WS /ws/metrics` — дельты метрик раз в секунду
- [ ] S1-11 tick_threshold по суточному объёму (clamp(coef·vol^0.56, 100, 25000), coef по рынку), пересчёт раз в минуту
- [ ] S1-12 Сверка метрик с UI бирж на 10 инструментах (тест на расхождение ≤ 1%)
- [ ] S1-13 Кластеры 1h по шагу цены из потока сделок

## Этап 2 — Алерты
- [ ] S2-01 Клиентская модель порогов по рынку и паре (HARD_DEFAULT_PAIR_SETTINGS), AND/OR-логика evaluateAlertState, кулдаун
- [ ] S2-02 Сервер: `/ws/all/alerts/v1` с min_* фильтрами и gzip-фреймами
- [ ] S2-03 История алертов на клиенте (IndexedDB), экспорт
- [ ] S2-04 Серверные лимиты chart-alerts (30 / 5 на ключ), `/api/chart-alerts/v1`
- [ ] S2-05 Telegram-уведомления: свой бот-токен + chat_id, очередь до 8 событий / 2 с, TTL 60 с
- [ ] S2-06 Клиент: виджет «История алертов», звук, форма порогов
- [ ] S2-07 Лимиты алертов с графика: 30 активных / 5 на инструмент

## Этап 3 — События и графики
- [ ] S3-01 Коллектор стакана `depth@100ms` (локальная книга, снапшот + дельты)
- [ ] S3-02 Детектор плотностей: min_size, kратность, время жизни, расстояние %, влияние 5m/15m
- [ ] S3-03 Детектор тиков по 1s-барам: Δprice %, объём; кастомные тики
- [ ] S3-04 Детектор TWAP: равные ордера через равные интервалы, счётчик серий, сумма $, длительность
- [ ] S3-05 Детектор ботов: конфиг паттернов, первые 3 паттерна
- [ ] S3-06 Кластеры 1h по шагу цены, `POST /api/clusters/snapshot_batch`
- [ ] S3-07 Клиент: сетка виджетов 5×2, панели графиков (Lightweight Charts), маркеры событий, кластеры
- [ ] S3-08 Клиент: Graph mode (`/api/candidates`), Tick mode (1s-графики)
- [ ] S3-09 Автооткрытие стакана: HTTP на localhost терминала (MetaScalp :17845), ping статуса

## Этап 4 — Коллекторы всех бирж
- [ ] S4-01 Binance futures + spot + Alpha (aggTrade, depth, OI; живая проверка из RU/EU-региона)
- [ ] S4-02 Bybit v5 futures + spot (publicTrade, orderbook, tickers OI)
- [ ] S4-03 OKX futures + spot
- [ ] S4-04 Bitget futures + spot
- [ ] S4-05 Gate.io futures + spot
- [ ] S4-06 MEXC futures + spot
- [ ] S4-07 KuCoin futures + spot
- [ ] S4-08 HTX futures
- [ ] S4-09 Kraken futures + spot
- [ ] S4-10 Coinbase spot, Upbit spot
- [ ] S4-11 Hyperliquid perp + spot (DEX)
- [ ] S4-12 Aster futures + spot (DEX)
- [ ] S4-13 ApeX futures (DEX)
- [ ] S4-14 Lighter futures (DEX)
- [ ] S4-15 COI по `coi_base`: дельты OI по окнам и число источников
- [ ] S4-16 `route`: all_futures / all_spots / all_stocks / all_in_one / all_ticks / per-source

## Этап 5 — Пользователи
- [ ] S5-01 Telegram-логин: deep-link `start=login_<token>`, `/api/auth/pending/{token}`, cookie-сессия
- [ ] S5-02 Присутствие (`/api/presence`), ограничение окон, `client_release` с принудительным reload
- [ ] S5-03 Trial 3 дня, тарифы, режимы по тарифу (SIMPLE/ADVANCED/GRAPH/TICK/TRADES)
- [ ] S5-04 Рефералка: код, `/r/<code>`, статистика переходов/регистраций/оплат, бонусы
- [ ] S5-05 Команды: командные алерты, публичный ник
- [ ] S5-06 Бан-лист (вечный / 24 ч, по разделам), серверные конфиги (до 20)
- [ ] S5-07 Android/FCM: привязка 8-значным кодом (3 мин), push с графиками
- [ ] S5-08 Голосовые уведомления (SpeechSynthesis), `speech_text` с сервера
- [ ] S5-09 Кабинет, тикеты поддержки, документация (CMS-блоки справки, кнопка «?» у виджета)

## Этап 6 — Инфоблок
- [ ] S6-01 Новости (Telegram-каналы / RSS)
- [ ] S6-02 Ончейн-события (источники уточнить)
- [ ] S6-03 Листинги: анонсы бирж (Binance, Bybit, OKX, Bitget, Gate, MEXC, Upbit, Coinbase)
