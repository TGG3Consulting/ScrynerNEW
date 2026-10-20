// RU / EN strings.

type Dict = { [k: string]: string };

const RU: Dict = {
  "menu.title": "Меню", "menu.cabinet": "Кабинет", "menu.cabinet.sub": "Доступ и рефералы", "menu.settings": "НАСТРОЙКИ", "menu.interface": "ИНТЕРФЕЙС", "menu.connections": "ПОДКЛЮЧЕНИЯ",
  "menu.alerts": "Алерты", "menu.alerts.sub": "Настройка порогов срабатывания алертов.", "menu.telegram": "Telegram и Android", "menu.telegram.sub": "Независимая отправка в Telegram и на телефон.",
  "menu.autoopen": "Автооткрытие стаканов", "menu.autoopen.sub": "Цели выбранного терминала для алертов, тиков, ботов, TWAP и плотностей",
  "menu.ticks": "Настройки тиков", "menu.ticks.sub": "Звук, фильтры и монеты", "menu.customticks": "Кастомные тики", "menu.bots": "Настройки ботов", "menu.bots.sub": "Паттерны, пороги тиков и биржи",
  "menu.density": "Настройки плотностей", "menu.density.sub": "Биржи и минимальный размер плотности", "menu.infoblock": "Инфоблок", "menu.infoblock.sub": "Новости, ончейн и листинги",
  "menu.columns": "Отображение Алертов и Топ 50", "menu.columns.sub": "Видимые колонки", "menu.config": "Конфиг", "menu.config.sub": "Сохранение и восстановление настроек",
  "menu.bans": "Бан-лист", "menu.bans.sub": "Вечные и часовые баны по разделам", "menu.markers": "Маркеры и Кнопки и Виджеты", "menu.markers.sub": "Выбрать маркеры, кнопки и виджеты графика",
  "menu.lang": "Язык", "menu.font": "Размер шрифта", "menu.tz": "Время графиков", "menu.tz.sub": "Смещение UTC на всех графиках", "menu.hotkeys": "Клавиши и иконки", "menu.hotkeys.sub": "Горячие клавиши и обозначения",
  "menu.report": "Сообщить об ошибке", "menu.report.sub": "Создать отчёт со скриншотами и журналом событий", "affects": "Влияет на:", "global": "ГЛОБАЛЬНО",
  "mode.advanced": "РЕЖИМ ADVANCED", "mode.simple": "SIMPLE", "mode.graph": "GRAPH", "mode.tick": "TICK", "mode.favorites": "FAVORITES", "mode.label": "Режим работы:", "market.label": "Рынок:",
  "w.alerts": "ИСТОРИЯ АЛЕРТОВ", "w.ticks": "ТИКИ", "w.bots": "БОТЫ", "w.density": "ПЛОТНОСТИ", "w.twap": "TWAP", "w.onchain": "ONCHAIN", "w.news": "НОВОСТИ", "w.listings": "ЛИСТИНГИ", "w.top": "TOP 50",
  "col.exchange": "Биржа", "col.pin": "Пин", "col.symbol": "Символ", "col.tpm": "TPM", "col.dvol": "Dvol%", "col.ls": "L/S", "col.oi5m": "OI5m %", "col.oi1d": "OI1d %", "col.settings": "Настройки",
  "col.vol": "VOL", "col.dvolShort": "DVOL", "col.spike": "Всплеск", "col.natr5": "Natr5", "col.price": "Цена", "col.change": "Изменение цены", "col.vol1d": "Объем 1д", "col.oivol": "OI Vol", "col.long": "Long", "col.short": "Short",
  "sound": "ЗВУК", "grid": "СЕТКА", "reset": "СБРОС", "collapse": "Collapse", "search": "Поиск символа",
  "alerts.title": "ПОРОГИ АЛЕРТОВ", "alerts.market": "ПРИМЕНИТЬ К РЫНКУ", "alerts.conditions": "УСЛОВИЯ, КОТОРЫЕ ДОЛЖНЫ СОВПАСТЬ", "alerts.thresholds": "ПОРОГИ",
  "alerts.req.bs": "Проверять лонг или шорт", "alerts.req.oi": "Проверять OI5m", "alerts.req.tpm": "Проверять TPM", "alerts.req.spike": "Проверять всплеск", "alerts.req.dvol": "Проверять Dvol%", "alerts.req.natr": "Проверять Natr5",
  "alerts.tpm": "TPM алерт", "alerts.tpm.h": "Сделок в минуту.", "alerts.buy": "Лонг алерт", "alerts.buy.h": "Объём покупок в USDT / USDC.", "alerts.sell": "Шорт алерт", "alerts.sell.h": "Объём продаж в USDT / USDC.",
  "alerts.spike": "Алерт всплеска", "alerts.spike.h": "Во сколько раз объём за последние 5 минут выше среднего объёма завершённых пятиминуток за предыдущий час.",
  "alerts.dvol": "Dvol% алерт", "alerts.dvol.h": "Процентное отношение объёма за последние 5 минут к суточному объёму.", "alerts.natr": "Natr5 алерт", "alerts.natr.h": "NATR(14) по 5-минутным свечам — текущая волатильность цены, %.",
  "alerts.oi": "OI5m алерт", "alerts.oi.h": "Изменение открытого интереса за 5 минут в %. Только фьючерсы.", "alerts.cooldown": "Кулдаун, мин", "alerts.cooldown.h": "Минимальная пауза между повторными алертами по одной паре, в минутах.",
  "apply": "Применить настройки", "zeros": "Вписать нули", "save": "Сохранить", "close": "Закрыть", "delete": "Удалить", "add": "Добавить",
  "tg.title": "TELEGRAM И ANDROID", "tg.bot": "Bot token", "tg.chat": "Chat ID", "tg.note": "Заметка", "tg.sources": "Отправлять события из", "tg.alerts": "Алерты", "tg.bots": "Боты", "tg.density": "Плотности", "tg.twap": "TWAP", "tg.ticks": "Тики", "tg.team": "Командные алерты",
  "tg.hint": "Один бот и chat ID для всех рынков и типов уведомлений в этом браузере. Уведомления отправляются, только пока открыта хотя бы одна вкладка. Общая очередь объединяет до 8 событий и отправляет не чаще одного сообщения в 2 секунды; события старше 60 секунд удаляются.",
  "android.title": "ПРИЛОЖЕНИЕ ANDROID", "android.send": "Отправлять на телефон", "android.code": "Получить код", "android.devices": "Устройства / доставка", "android.codeLabel": "Код для телефона", "android.codeHint": "8 цифр, без пробелов и дефисов. Действует 3 минуты.", "android.charts": "Алерты с графиками",
  "ticks.title": "НАСТРОЙКИ ТИКОВ", "ticks.mult": "Множитель порога", "ticks.alwaysVol": "Всегда играть, если объем >=", "ticks.alwaysVola": "Всегда играть, если волатильность >=", "ticks.exchanges": "Биржи", "ticks.sound": "Звук",
  "bots.title": "НАСТРОЙКИ БОТОВ", "bots.patterns": "Паттерны", "bots.P1": "P1 · Точный таймер", "bots.C1": "C1 · Микродавление", "bots.S1": "S1 · Спредер", "bots.minVol": "Минимальный объём каждого тика", "bots.minMove": "Минимальное движение каждого тика", "bots.hideR0": "Скрывать ботов R0",
  "density.title": "НАСТРОЙКИ ПЛОТНОСТЕЙ", "density.note": "Включите нужные биржи и задайте для них минимальный размер плотности. Пусто или 0 — любой размер.", "density.min": "Минимальный размер",
  "info.title": "ИНФОБЛОК", "info.news": "Новости", "info.onchain": "Ончейн", "info.listings": "Листинги",
  "bans.title": "БАН-ЛИСТ", "bans.forever": "навсегда", "bans.24h": "24 ч", "bans.1h": "1 ч", "bans.empty": "Бан-лист пуст",
  "cfg.title": "КОНФИГ", "cfg.save": "Сохранить текущие настройки", "cfg.restore": "Восстановить", "cfg.export": "Экспорт", "cfg.import": "Импорт",
  "conn.settings": "Настройки и журнал", "conn.nolink": "Нет связи", "conn.unchecked": "Не проверено", "conn.ok": "Связь есть", "conn.port": "Порт", "conn.check": "Проверить",
  "cabinet.title": "ЛИЧНЫЙ КАБИНЕТ", "cabinet.sub": "Статус доступа, реферальная программа и управление аккаунтом", "cabinet.access": "ДОСТУП К SCRYNER", "cabinet.status": "СТАТУС АККАУНТА", "cabinet.active": "Активен", "cabinet.inactive": "Истёк",
  "cabinet.plan": "ТАРИФ / ПЛАН", "cabinet.until": "ДЕЙСТВУЕТ ДО", "cabinet.modes": "ДОСТУПНЫЕ РЕЖИМЫ", "cabinet.open": "ОТКРЫТЬ SCRYNER", "cabinet.profile": "ПРОФИЛЬ", "cabinet.role": "РОЛЬ", "cabinet.since": "С нами с",
  "cabinet.nick": "ПУБЛИЧНЫЙ НИК", "cabinet.nickSave": "СОХРАНИТЬ НИК", "cabinet.nickHint": "3–24 символа: латиница, цифры, _ и -. Ник можно менять в любое время. Ник виден участникам команд; Telegram не показывается.",
  "cabinet.ref": "РЕФЕРАЛЬНАЯ ПРОГРАММА", "cabinet.refCode": "ВАШ РЕФЕРАЛЬНЫЙ КОД", "cabinet.refLink": "КОРОТКАЯ РЕФЕРАЛЬНАЯ ССЫЛКА", "copy": "КОПИРОВАТЬ", "cabinet.invited": "ПРИГЛАШЕНО", "cabinet.paid": "ОПЛАТИЛИ", "cabinet.payments": "ОПЛАТ", "cabinet.bonus": "ДОСТУПНО БОНУСОВ",
  "cabinet.linkStats": "СТАТИСТИКА ССЫЛКИ", "cabinet.allTime": "ВСЁ ВРЕМЯ", "cabinet.day": "ЗА 1 ДЕНЬ", "cabinet.clicks": "Переходы", "cabinet.regs": "Регистрации", "cabinet.quick": "БЫСТРЫЕ ДЕЙСТВИЯ", "cabinet.tickets": "ВАШИ ТИКЕТЫ", "cabinet.noTickets": "Вы ещё не создавали тикеты.",
  "cabinet.teams": "КОМАНДЫ", "cabinet.teamCreate": "Создать команду", "cabinet.teamJoin": "Вступить по коду", "cabinet.logout": "Выйти",
  "simple.title": "SIMPLE MODE", "graph.title": "GRAPH MODE", "tick.title": "TICK MODE", "fav.title": "ИЗБРАННОЕ", "fav.empty": "Избранное пусто. Shift+F на графике или ★ в таблице.",
  "tf": "ТФ", "impact": "Возможное влияние на цену:", "good": "good", "weak": "weak",
  "quickstart": "Быстрый старт", "teams": "Команды", "noData": "нет данных", "connecting": "подключение…", "ws": "WS",
};

const EN: Dict = {
  "menu.title": "Menu", "menu.cabinet": "Cabinet", "menu.cabinet.sub": "Access and referrals", "menu.settings": "SETTINGS", "menu.interface": "INTERFACE", "menu.connections": "CONNECTIONS",
  "menu.alerts": "Alerts", "menu.alerts.sub": "Alert thresholds.", "menu.telegram": "Telegram & Android", "menu.telegram.sub": "Independent delivery to Telegram and phone.",
  "menu.autoopen": "Auto-open DOM", "menu.autoopen.sub": "Terminal targets for alerts, ticks, bots, TWAP and densities", "menu.ticks": "Tick Settings", "menu.ticks.sub": "Sound, filters and symbols", "menu.customticks": "Custom Ticks",
  "menu.bots": "Bot Settings", "menu.bots.sub": "Patterns, tick thresholds and exchanges", "menu.density": "Density Settings", "menu.density.sub": "Exchanges and minimum wall size", "menu.infoblock": "Info block", "menu.infoblock.sub": "News, onchain and listings",
  "menu.columns": "Alerts & Top 50 columns", "menu.columns.sub": "Visible columns", "menu.config": "Config", "menu.config.sub": "Save and restore settings", "menu.bans": "Ban list", "menu.bans.sub": "Permanent and hourly bans per section",
  "menu.markers": "Markers, Buttons & Widgets", "menu.markers.sub": "Chart markers, buttons and widgets", "menu.lang": "Language", "menu.font": "Font size", "menu.tz": "Chart time", "menu.tz.sub": "UTC offset on all charts", "menu.hotkeys": "Keys & icons", "menu.hotkeys.sub": "Hotkeys and legend",
  "menu.report": "Report a bug", "menu.report.sub": "Create a report with screenshots and event log", "affects": "Affects:", "global": "GLOBAL",
  "mode.advanced": "ADVANCED MODE", "mode.simple": "SIMPLE", "mode.graph": "GRAPH", "mode.tick": "TICK", "mode.favorites": "FAVORITES", "mode.label": "Mode:", "market.label": "Market:",
  "w.alerts": "ALERT HISTORY", "w.ticks": "TICKS", "w.bots": "BOTS", "w.density": "DENSITIES", "w.twap": "TWAP", "w.onchain": "ONCHAIN", "w.news": "NEWS", "w.listings": "LISTINGS", "w.top": "TOP 50",
  "col.exchange": "Exchange", "col.pin": "Pin", "col.symbol": "Symbol", "col.tpm": "TPM", "col.dvol": "Dvol%", "col.ls": "L/S", "col.oi5m": "OI5m %", "col.oi1d": "OI1d %", "col.settings": "Settings",
  "col.vol": "VOL", "col.dvolShort": "DVOL", "col.spike": "Spike", "col.natr5": "Natr5", "col.price": "Price", "col.change": "Price change", "col.vol1d": "1d Vol", "col.oivol": "OI Vol", "col.long": "Long", "col.short": "Short",
  "sound": "SOUND", "grid": "GRID", "reset": "RESET", "collapse": "Collapse", "search": "Search symbol",
  "alerts.title": "ALERT THRESHOLDS", "alerts.market": "APPLY TO MARKET", "alerts.conditions": "CONDITIONS THAT MUST MATCH", "alerts.thresholds": "THRESHOLDS",
  "alerts.req.bs": "Check long or short", "alerts.req.oi": "Check OI5m", "alerts.req.tpm": "Check TPM", "alerts.req.spike": "Check spike", "alerts.req.dvol": "Check Dvol%", "alerts.req.natr": "Check Natr5",
  "alerts.tpm": "TPM alert", "alerts.tpm.h": "Trades per minute.", "alerts.buy": "Long alert", "alerts.buy.h": "Buy volume in USDT / USDC.", "alerts.sell": "Short alert", "alerts.sell.h": "Sell volume in USDT / USDC.",
  "alerts.spike": "Spike alert", "alerts.spike.h": "Ratio of the last 5-minute volume to the average completed 5-minute volume of the previous hour.", "alerts.dvol": "Dvol% alert", "alerts.dvol.h": "Last 5-minute volume as a percentage of 24h volume.",
  "alerts.natr": "Natr5 alert", "alerts.natr.h": "NATR(14) on 5-minute candles — current price volatility, %.", "alerts.oi": "OI5m alert", "alerts.oi.h": "Open-interest change over 5 minutes, %. Futures only.", "alerts.cooldown": "Cooldown, min", "alerts.cooldown.h": "Minimum pause between repeated alerts for one pair, minutes.",
  "apply": "Apply settings", "zeros": "Fill zeros", "save": "Save", "close": "Close", "delete": "Delete", "add": "Add",
  "tg.title": "TELEGRAM & ANDROID", "tg.bot": "Bot token", "tg.chat": "Chat ID", "tg.note": "Note", "tg.sources": "Send events from", "tg.alerts": "Alerts", "tg.bots": "Bots", "tg.density": "Densities", "tg.twap": "TWAP", "tg.ticks": "Ticks", "tg.team": "Team alerts",
  "tg.hint": "One bot and chat ID for all markets and notification types in this browser. Notifications are sent only while at least one tab is open. A shared queue batches up to 8 events and sends at most one message per 2 seconds; events older than 60 seconds are dropped.",
  "android.title": "ANDROID APP", "android.send": "Send to phone", "android.code": "Get code", "android.devices": "Devices / delivery", "android.codeLabel": "Phone code", "android.codeHint": "8 digits, no spaces or dashes. Valid for 3 minutes.", "android.charts": "Alerts with charts",
  "ticks.title": "TICK SETTINGS", "ticks.mult": "Threshold multiplier", "ticks.alwaysVol": "Always play if volume >=", "ticks.alwaysVola": "Always play if volatility >=", "ticks.exchanges": "Exchanges", "ticks.sound": "Sound",
  "bots.title": "BOT SETTINGS", "bots.patterns": "Patterns", "bots.P1": "P1 · Strict timer", "bots.C1": "C1 · Micro-pressure", "bots.S1": "S1 · Spreader", "bots.minVol": "Minimum volume of every tick", "bots.minMove": "Minimum movement of every tick", "bots.hideR0": "Hide R0 bots",
  "density.title": "DENSITY SETTINGS", "density.note": "Enable exchanges and set their minimum density size. Empty or 0 means any size.", "density.min": "Minimum size",
  "info.title": "INFO BLOCK", "info.news": "News", "info.onchain": "Onchain", "info.listings": "Listings",
  "bans.title": "BAN LIST", "bans.forever": "forever", "bans.24h": "24h", "bans.1h": "1h", "bans.empty": "Ban list is empty",
  "cfg.title": "CONFIG", "cfg.save": "Save current settings", "cfg.restore": "Restore", "cfg.export": "Export", "cfg.import": "Import",
  "conn.settings": "Settings and log", "conn.nolink": "No link", "conn.unchecked": "Not checked", "conn.ok": "Connected", "conn.port": "Port", "conn.check": "Check",
  "cabinet.title": "CABINET", "cabinet.sub": "Access status, referral program and account", "cabinet.access": "ACCESS", "cabinet.status": "ACCOUNT STATUS", "cabinet.active": "Active", "cabinet.inactive": "Expired",
  "cabinet.plan": "PLAN", "cabinet.until": "VALID UNTIL", "cabinet.modes": "AVAILABLE MODES", "cabinet.open": "OPEN SCRYNER", "cabinet.profile": "PROFILE", "cabinet.role": "ROLE", "cabinet.since": "Since",
  "cabinet.nick": "PUBLIC NICKNAME", "cabinet.nickSave": "SAVE NICKNAME", "cabinet.nickHint": "3–24 chars: letters, digits, _ and -. Visible to team members; Telegram is never shown.",
  "cabinet.ref": "REFERRAL PROGRAM", "cabinet.refCode": "YOUR REFERRAL CODE", "cabinet.refLink": "SHORT REFERRAL LINK", "copy": "COPY", "cabinet.invited": "INVITED", "cabinet.paid": "PAID", "cabinet.payments": "PAYMENTS", "cabinet.bonus": "BONUS AVAILABLE",
  "cabinet.linkStats": "LINK STATISTICS", "cabinet.allTime": "ALL TIME", "cabinet.day": "LAST DAY", "cabinet.clicks": "Clicks", "cabinet.regs": "Registrations", "cabinet.quick": "QUICK ACTIONS", "cabinet.tickets": "YOUR TICKETS", "cabinet.noTickets": "No tickets yet.",
  "cabinet.teams": "TEAMS", "cabinet.teamCreate": "Create team", "cabinet.teamJoin": "Join by code", "cabinet.logout": "Log out",
  "simple.title": "SIMPLE MODE", "graph.title": "GRAPH MODE", "tick.title": "TICK MODE", "fav.title": "FAVORITES", "fav.empty": "Favorites are empty. Shift+F on a chart or ★ in the table.",
  "tf": "TF", "impact": "Price impact:", "good": "good", "weak": "weak",
  "quickstart": "Quick start", "teams": "Teams", "noData": "no data", "connecting": "connecting…", "ws": "WS",
};

let lang: "ru" | "en" = (localStorage.getItem("scryner.lang") as "ru" | "en") || "ru";

export function t(key: string): string {
  const d = lang === "ru" ? RU : EN;
  return d[key] ?? EN[key] ?? key;
}

export function getLang(): "ru" | "en" {
  return lang;
}

export function setLang(l: "ru" | "en"): void {
  lang = l;
  localStorage.setItem("scryner.lang", l);
  document.documentElement.lang = l;
}
