# ScrynerNEW

Мультибиржевой скринер крипто-фьючерсов в реальном времени: бары 1s/1m, метрики по окнам,
алерты по порогам, детекторы тиков, плотностей, TWAP и ботов, доставка в браузер, Telegram
и торговый терминал.

- Разбор референсного сервиса: `docs/analysis/GODSEYE_ANALYSIS.md`
- Архитектура: `docs/architecture/SCRYNER_ARCHITECTURE.md`
- Бэклог: `tasks/BACKLOG.md`
- Состояние проекта: `PROJECT_STATE.md`

## Запуск (dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config/scryner.example.yaml config/scryner.yaml
python -m scryner
```
