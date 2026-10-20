# Changelog

Все значимые изменения в проекте документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- **[WEB]** Веб-клиент: режимы Advanced/Simple/Graph/Tick/Favorites, кабинет, меню настроек,
  клиентские алерты (порт evaluateAlertState), Telegram-очередь, звук/TTS, терминальные мосты.
- **[COLLECTOR]** Бэкфилл 1m-свечей для всех бирж; исключены обратные контракты MEXC/KuCoin.
- **[INFRA]** Dockerfile, docker-compose, nginx, GitHub Actions CI.
- **[API]** Шлюз FastAPI: все REST-маршруты контракта и 13 WS-каналов (snapshot/patch/seq,
  gzip-фреймы), авторизация через Telegram deep-link, сессии, trial, рефералка, команды,
  конфиги, chart-alerts, mobile/FCM, help-content, админка.
- **[FEEDS]** Telegram-бот, читатель Telegram-каналов (Telethon), TWAP-индексатор Hyperliquid
  (explorerTxs + bootstrap hypurrscan), котировки акций TradingView, FCM push.
- **[CORE]** Хранилище SQLite/TimescaleDB, воркер источника, детекторы плотностей и ботов.
- **[COLLECTOR]** Коллекторы 26 источников на 15 биржах, живые проверки.
- **[METRICS]** Движок метрик, кольцевые буферы баров, кластеры, COI, формула tick_threshold.
- **[DOCS]** `docs/analysis/GODSEYE_REALTIME_PROTOCOL.md` — протокол реального времени по
  полной HAR-записи: 13 WS-каналов, snapshot/patch/seq, поля записей, формула tick_threshold,
  клиентская логика алертов, боты, TWAP, терминалы, остальные REST-вызовы.
- **[DOCS]** Архитектура и бэклог переведены на полную сборку без MVP (решение оператора).
- **[DOCS]** `docs/analysis/GODSEYE_ANALYSIS.md` — полный разбор сервиса GodsEye
  (oculusdei.pro) по HAR-записи и скриншотам: продукт, режимы, виджеты, настройки, пороги
  алертов, контракт REST API, раскладка упакованных метрик, кластеры, инфраструктура.
- **[DOCS]** `docs/architecture/SCRYNER_ARCHITECTURE.md` — архитектура собственного
  скринера: процессы, модель данных, формулы метрик, детекторы, API, клиент, хранилище, этапы.
- **[DOCS]** `tasks/BACKLOG.md` — бэклог по этапам 0–6.
- **[INIT]** CLAUDE.md, PROJECT_STATE.md, README.md, pyproject.toml, .gitignore,
  `config/scryner.example.yaml`, каркас пакета `src/scryner`.
