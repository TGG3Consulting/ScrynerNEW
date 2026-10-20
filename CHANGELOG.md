# Changelog

Все значимые изменения в проекте документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
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
