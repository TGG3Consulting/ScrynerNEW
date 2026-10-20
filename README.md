# ScrynerNEW

Мультибиржевой скринер крипто-фьючерсов в реальном времени: бары 1s/1m, метрики по окнам,
алерты по порогам, детекторы тиков, плотностей, TWAP и ботов, доставка в браузер, Telegram
и торговый терминал.

- Разбор референсного сервиса: `docs/analysis/GODSEYE_ANALYSIS.md`
- Архитектура: `docs/architecture/SCRYNER_ARCHITECTURE.md`
- Бэклог: `tasks/BACKLOG.md`
- Состояние проекта: `PROJECT_STATE.md`

## Запуск (dev, один процесс)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" telethon cryptography
cp config/scryner.example.yaml config/scryner.yaml      # включить dev_auto_login: true для работы без Telegram
cd web && npm install && npm run build && cd ..
python -m scryner all --sources hyperliquid_futures,gateio_futures   # или без --sources: все 26 источников
# http://localhost:8080/advanced?route=all_futures
```

## Запуск (prod, docker compose)

```bash
cp .env.example .env            # токен бота, api_id/api_hash, пароль БД, домен
cp config/scryner.example.yaml config/scryner.yaml
docker compose up -d --build    # redis, timescaledb, gateway, feeds, 26 воркеров, nginx
```

Режимы процесса: `python -m scryner all|worker <source>|gateway|feeds`. Проверка одного
источника: `python tools/smoke_collector.py bybit_futures 20 10 BTC,ETH`.
