// Settings menu and all its panels.

import { apiGet, apiPost, apiDelete } from "../core/api";
import { store, HARD_DEFAULT_PAIR_SETTINGS, ALL_EXCHANGES, FUTURES_SOURCES, SPOT_SOURCES, type PairSettings, DEFAULT_COLUMNS } from "../core/store";
import { t, getLang, setLang } from "../core/i18n";
import { checkTerminal, TERMINALS, type TerminalName } from "../core/terminals";
import type { Record } from "../core/types";
import { button, checkbox, el, esc, field, modal, numInput, toast, toggle } from "./common";
import { COLUMNS } from "./top";

const ROUTES: Array<[string, string]> = [["all_futures", "All Futures"], ["all_spots", "All Spot"], ["all_stocks", "All Stocks"], ["all_in_one", "All in one"]];

function badge(text: string, cls = "affect"): string {
  return `<span class="affect-badge ${cls}">${esc(text)}</span>`;
}

function menuItem(title: string, sub: string, affects: string[], onClick: () => void, right?: string): HTMLElement {
  const it = el("div", "menu-item");
  it.innerHTML = `<div class="menu-item-title">${esc(title)}${right ? `<span class="menu-right">${right}</span>` : ""}</div><div class="menu-item-sub">${esc(sub)}</div>${affects.length ? `<div class="menu-affects">${esc(t("affects"))} ${affects.map((a) => badge(a, a === t("global") ? "global" : a === "TICK MODE" ? "tick" : "affect")).join(" ")}</div>` : ""}`;
  it.onclick = onClick;
  return it;
}

export function openMenu(ctx: { openCabinet: () => void; reportBug: () => void }): void {
  const box = el("div", "menu");
  const adv = "ADVANCED MODE";
  const glob = t("global");
  box.append(
    menuItem(t("menu.cabinet"), t("menu.cabinet.sub"), [], ctx.openCabinet, "ID"),
    el("div", "menu-section", t("menu.settings")),
    menuItem(t("menu.alerts"), t("menu.alerts.sub"), [adv], openAlertThresholds),
    menuItem(t("menu.telegram"), t("menu.telegram.sub"), [glob], openTelegram),
    menuItem(t("menu.autoopen"), t("menu.autoopen.sub"), [adv], openAutoOpen),
    menuItem(t("menu.ticks"), t("menu.ticks.sub"), [adv, "TICK MODE"], openTickSettings),
    menuItem(t("menu.customticks"), "", [], openCustomTicks),
    menuItem(t("menu.bots"), t("menu.bots.sub"), [adv], openBotSettings),
    menuItem(t("menu.density"), t("menu.density.sub"), [adv], openDensitySettings),
    menuItem(t("menu.infoblock"), t("menu.infoblock.sub"), [adv], openInfoblock),
    menuItem(t("menu.columns"), t("menu.columns.sub"), [adv], openColumns),
    menuItem(t("menu.config"), t("menu.config.sub"), [glob], openConfigs),
    menuItem(t("menu.bans"), t("menu.bans.sub"), [glob], openBans),
    el("div", "menu-section", t("menu.interface")),
    menuItem(t("menu.markers"), t("menu.markers.sub"), [], openMarkers, `${Object.values(store.profile.prefs.markers).filter(Boolean).length}/${Object.keys(store.profile.prefs.markers).length}`),
    menuItem(t("menu.lang"), getLang() === "ru" ? "RU / EN" : "EN / RU", [], () => {
      setLang(getLang() === "ru" ? "en" : "ru");
      location.reload();
    }),
    menuItem(t("menu.font"), "", [], () => {
      const v = prompt(t("menu.font"), String(store.profile.prefs.font_scale));
      if (v) store.update((p) => (p.prefs.font_scale = Math.max(70, Math.min(160, Number(v)))));
      applyUiPrefs();
    }, `${store.profile.prefs.font_scale}%`),
    menuItem(t("menu.tz"), t("menu.tz.sub"), [], openTimezone, `UTC${store.profile.prefs.utc_offset_min >= 0 ? "+" : "-"}${String(Math.abs(store.profile.prefs.utc_offset_min / 60)).padStart(2, "0")}:00`),
    menuItem(t("menu.hotkeys"), t("menu.hotkeys.sub"), [], openHotkeys),
    menuItem(t("menu.report"), t("menu.report.sub"), [], ctx.reportBug),
    el("div", "menu-section", t("menu.connections")),
  );
  for (const name of Object.keys(TERMINALS) as TerminalName[]) box.appendChild(connectionItem(name));
  const overlay = modal(t("menu.title"), box);
  overlay.classList.add("menu-overlay");
}

export function applyUiPrefs(): void {
  document.documentElement.style.fontSize = `${(store.profile.prefs.font_scale / 100) * 12}px`;
  document.documentElement.dataset.theme = store.profile.prefs.theme;
}

// ------------------------------------------------------------------ connections
function connectionItem(name: TerminalName): HTMLElement {
  const tcfg = store.profile.terminals[name];
  const it = el("div", "menu-item conn");
  const status = el("span", "conn-status", t("conn.unchecked"));
  it.innerHTML = `<div class="menu-item-title">${esc(TERMINALS[name].title)}</div><div class="menu-item-sub">${esc(t("conn.settings"))}</div>`;
  it.querySelector(".menu-item-title")!.appendChild(status);
  it.querySelector(".menu-item-title")!.appendChild(
    toggle(tcfg.enabled, (v) => {
      store.update((p) => (p.terminals[name].enabled = v));
      if (v) void checkTerminal(name).then((ok) => (status.textContent = ok ? t("conn.ok") : t("conn.nolink")));
    }),
  );
  it.querySelector(".menu-item-sub")!.addEventListener("click", () => openConnection(name));
  if (tcfg.enabled) void checkTerminal(name).then((ok) => (status.textContent = ok ? t("conn.ok") : t("conn.nolink")));
  return it;
}

function openConnection(name: TerminalName): void {
  const tcfg = store.profile.terminals[name];
  const box = el("div", "panel");
  const port = numInput(tcfg.port, { min: 1 });
  const targets = el("textarea");
  targets.value = tcfg.targets.join("\n");
  targets.rows = 4;
  const log = el("pre", "log", esc(tcfg.log.slice(-50).join("\n")));
  box.append(field(t("conn.port"), port), field("Link groups / bindings (по одному в строке)", targets), button(t("conn.check"), async () => {
    store.update((p) => {
      p.terminals[name].port = Number(port.value);
      p.terminals[name].targets = targets.value.split("\n").map((x) => x.trim()).filter(Boolean);
    });
    const ok = await checkTerminal(name);
    toast(ok ? t("conn.ok") : t("conn.nolink"), ok ? "ok" : "err");
    log.textContent = store.profile.terminals[name].log.slice(-50).join("\n");
  }), el("div", "field-label", "Журнал"), log);
  modal(TERMINALS[name].title, box);
}

// ------------------------------------------------------------------ alert thresholds
export function openAlertThresholds(): void {
  const box = el("div", "panel");
  const routeSel = el("select", "sel");
  for (const [v, l] of ROUTES) {
    const o = el("option", "", l);
    o.value = v;
    routeSel.appendChild(o);
  }
  routeSel.value = store.profile.prefs.market_route;
  const cur = (): Partial<PairSettings> => store.profile.route_defaults[routeSel.value] ?? {};
  const s = { ...HARD_DEFAULT_PAIR_SETTINGS, alert_cooldown_minutes: store.profile.prefs.alert_cooldown_minutes, ...cur() };
  const reqs: Array<[keyof PairSettings, string]> = [["require_buy_or_sell", "alerts.req.bs"], ["require_oi1", "alerts.req.oi"], ["require_tpm", "alerts.req.tpm"], ["require_spike", "alerts.req.spike"], ["require_dvol", "alerts.req.dvol"], ["require_natr5", "alerts.req.natr"]];
  const thr: Array<[keyof PairSettings, string, string]> = [["tpm_threshold", "alerts.tpm", "alerts.tpm.h"], ["buy_threshold", "alerts.buy", "alerts.buy.h"], ["sell_threshold", "alerts.sell", "alerts.sell.h"], ["spike_threshold", "alerts.spike", "alerts.spike.h"], ["dvol_threshold", "alerts.dvol", "alerts.dvol.h"], ["natr5_threshold", "alerts.natr", "alerts.natr.h"], ["oi_threshold", "alerts.oi", "alerts.oi.h"], ["alert_cooldown_minutes", "alerts.cooldown", "alerts.cooldown.h"]];
  const inputs = new Map<string, HTMLInputElement>();
  const checks = new Map<string, HTMLInputElement>();
  const fill = () => {
    const c = { ...HARD_DEFAULT_PAIR_SETTINGS, alert_cooldown_minutes: store.profile.prefs.alert_cooldown_minutes, ...cur() };
    for (const [k, i] of inputs) i.value = String((c as any)[k]);
    for (const [k, i] of checks) i.checked = Boolean((c as any)[k]);
  };
  box.appendChild(el("div", "panel-title", `${esc(t("alerts.title"))}<div class="panel-sub">${esc(t("menu.alerts.sub"))}</div>`));
  box.appendChild(el("div", "section-title", t("alerts.market")));
  box.appendChild(routeSel);
  routeSel.onchange = fill;
  box.appendChild(el("div", "section-title", t("alerts.conditions")));
  for (const [k, label] of reqs) {
    const c = checkbox(t(label), Boolean((s as any)[k]), () => undefined);
    checks.set(k, c.querySelector("input")!);
    box.appendChild(c);
  }
  box.appendChild(el("div", "section-title", t("alerts.thresholds")));
  for (const [k, label, hint] of thr) {
    const i = numInput(Number((s as any)[k]), { min: 0 });
    inputs.set(k, i);
    box.appendChild(field(t(label), i, t(hint)));
  }
  const row = el("div", "btn-row");
  row.append(
    button(t("apply"), () => {
      const next: Partial<PairSettings> = {};
      for (const [k, i] of inputs) (next as any)[k] = Number(i.value) || 0;
      for (const [k, i] of checks) (next as any)[k] = i.checked;
      store.update((p) => {
        p.route_defaults[routeSel.value] = next;
        if (next.alert_cooldown_minutes) p.prefs.alert_cooldown_minutes = next.alert_cooldown_minutes;
      });
      toast(t("save"));
    }, "btn primary"),
    button(t("zeros"), () => {
      for (const i of inputs.values()) i.value = "0";
    }),
  );
  box.appendChild(row);
  modal(t("alerts.title"), box);
}

export function openPairSettings(key: string, rec?: Record): void {
  const s = store.pairSettings(key);
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(rec?.display_pair ?? key)));
  const inputs = new Map<string, HTMLInputElement>();
  for (const [k, label] of [["buy_threshold", "alerts.buy"], ["sell_threshold", "alerts.sell"], ["oi_threshold", "alerts.oi"], ["tpm_threshold", "alerts.tpm"], ["spike_threshold", "alerts.spike"], ["dvol_threshold", "alerts.dvol"], ["natr5_threshold", "alerts.natr"], ["alert_cooldown_minutes", "alerts.cooldown"]] as Array<[keyof PairSettings, string]>) {
    const i = numInput(Number(s[k]), { min: 0 });
    inputs.set(k, i);
    box.appendChild(field(t(label), i));
  }
  const pinned = checkbox(t("col.pin"), s.pinned, () => undefined);
  const hidden = checkbox("Hidden", s.hidden, () => undefined);
  box.append(pinned, hidden);
  box.appendChild(button(t("save"), () => {
    const next: Partial<PairSettings> = { pinned: pinned.querySelector("input")!.checked, hidden: hidden.querySelector("input")!.checked, hidden_until: 0 };
    for (const [k, i] of inputs) (next as any)[k] = Number(i.value) || 0;
    store.update((p) => (p.pairs[key] = next));
    toast(t("save"));
  }, "btn primary"));
  modal(t("col.settings"), box);
}

// ------------------------------------------------------------------ telegram / android
export function openTelegram(): void {
  const tg = store.profile.telegram;
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("tg.title"))}<div class="panel-sub">${esc(t("menu.telegram.sub"))}</div>`));
  box.appendChild(el("div", "section-title", "TELEGRAM"));
  const token = el("input");
  token.type = "password";
  token.value = tg.bot_token;
  const chat = el("input");
  chat.value = tg.chat_id;
  const note = el("input");
  note.value = tg.note;
  box.append(field(t("tg.bot"), token), field(t("tg.chat"), chat), field(t("tg.note"), note));
  box.appendChild(el("div", "section-title", t("tg.sources")));
  const grid = el("div", "toggle-grid");
  for (const k of ["alerts", "bots", "density", "twap", "ticks", "team"] as const) {
    const row = el("div", "toggle-row", `<span>${esc(t(`tg.${k}`))}</span>`);
    row.prepend(toggle(tg.sources[k], (v) => store.update((p) => (p.telegram.sources[k] = v))));
    grid.appendChild(row);
  }
  box.appendChild(grid);
  box.appendChild(el("p", "hint", esc(t("tg.hint"))));
  box.appendChild(el("div", "section-title", t("android.title")));
  box.appendChild(el("div", "field-label", t("android.send")));
  const grid2 = el("div", "toggle-grid");
  for (const k of ["alerts", "bots", "density", "twap", "ticks", "team", "charts"] as const) {
    const row = el("div", "toggle-row", `<span>${esc(k === "charts" ? t("android.charts") : t(`tg.${k}`))}</span>`);
    row.prepend(toggle(tg.mobile[k], (v) => store.update((p) => (p.telegram.mobile[k] = v))));
    grid2.appendChild(row);
  }
  box.appendChild(grid2);
  const codeBox = el("div", "code-box", "12345678");
  const row = el("div", "btn-row");
  row.append(
    button(t("android.code"), async () => {
      try {
        const r = await apiPost<{ code: string }>("/api/mobile-alerts/code", {});
        codeBox.textContent = r.code;
      } catch (e) {
        toast(String((e as Error).message), "err");
      }
    }),
    button(t("android.devices"), async () => {
      const r = await apiPost<{ devices: Array<{ code: string; paired_at: number }> }>("/api/mobile-alerts/devices", {});
      toast(r.devices.length ? r.devices.map((d) => `${d.code} ✓`).join(", ") : "—");
    }),
  );
  box.append(row, el("div", "field-label", t("android.codeLabel")), codeBox, el("p", "hint", esc(t("android.codeHint"))));
  box.appendChild(button(t("save"), () => {
    store.update((p) => {
      p.telegram.bot_token = token.value.trim();
      p.telegram.chat_id = chat.value.trim();
      p.telegram.note = note.value;
    });
    toast(t("save"));
  }, "btn primary"));
  modal(t("tg.title"), box);
}

export function openAutoOpen(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("menu.autoopen"))}<div class="panel-sub">${esc(t("menu.autoopen.sub"))}</div>`));
  const row = el("div", "toggle-row", `<span>${esc(t("menu.autoopen"))}</span>`);
  row.prepend(toggle(store.profile.telegram.auto_open_meta, (v) => store.update((p) => (p.telegram.auto_open_meta = v))));
  box.appendChild(row);
  for (const name of Object.keys(TERMINALS) as TerminalName[]) {
    const tcfg = store.profile.terminals[name];
    const r = el("div", "toggle-row", `<span>${esc(TERMINALS[name].title)} :${tcfg.port} ${tcfg.targets.length ? `(${tcfg.targets.length} groups)` : ""}</span>`);
    r.prepend(toggle(tcfg.enabled, (v) => store.update((p) => (p.terminals[name].enabled = v))));
    box.appendChild(r);
  }
  modal(t("menu.autoopen"), box);
}

// ------------------------------------------------------------------ ticks / bots / density
function exchangeToggles(get: (ex: string) => boolean, set: (ex: string, v: boolean) => void, list = ALL_EXCHANGES): HTMLElement {
  const grid = el("div", "toggle-grid");
  for (const ex of list) {
    const row = el("div", "toggle-row", `<span>${esc(ex)}</span>`);
    row.prepend(toggle(get(ex), (v) => set(ex, v)));
    grid.appendChild(row);
  }
  return grid;
}

export function openTickSettings(): void {
  const s = store.profile.ticks;
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("ticks.title"))}<div class="panel-sub">${esc(t("menu.ticks.sub"))}</div>`));
  const mult = numInput(s.multiplier, { min: 0, step: 0.1 });
  const vol = numInput(s.always_play_volume, { min: 0 });
  const vola = numInput(s.always_play_volatility, { min: 0, step: 0.01 });
  const sound = el("select", "sel");
  for (const n of ["bum", "ping", "alarm", "none"]) {
    const o = el("option", "", n);
    o.value = n;
    sound.appendChild(o);
  }
  sound.value = s.sound;
  const volume = numInput(s.volume, { min: 0, step: 0.1 });
  const tts = checkbox("TTS (voice)", s.tts, (v) => store.update((p) => (p.ticks.tts = v)));
  box.append(field(t("ticks.mult"), mult), field(t("ticks.alwaysVol"), vol), field(t("ticks.alwaysVola"), vola), field(t("ticks.sound"), sound), field("Volume", volume), tts);
  box.appendChild(el("div", "section-title", t("ticks.exchanges")));
  box.appendChild(exchangeToggles((ex) => s.exchanges[ex] !== false, (ex, v) => store.update((p) => (p.ticks.exchanges[ex] = v))));
  box.appendChild(button(t("apply"), () => {
    store.update((p) => {
      p.ticks.multiplier = Number(mult.value) || 1;
      p.ticks.always_play_volume = Number(vol.value) || 0;
      p.ticks.always_play_volatility = Number(vola.value) || 0;
      p.ticks.sound = sound.value;
      p.ticks.volume = Number(volume.value) || 0;
    });
    toast(t("save"));
  }, "btn primary"));
  modal(t("ticks.title"), box);
}

export function openCustomTicks(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("menu.customticks"))}<div class="panel-sub">Individual settings for exact coins (max 200 per exchange and market)</div>`));
  const list = el("div", "list");
  const render = () => {
    list.innerHTML = "";
    store.profile.ticks.custom.forEach((c, i) => {
      const row = el("div", "list-row", `<span>${esc(c.exchange)} ${esc(c.market_type)} ${esc(c.pair)} ≥ $${c.min_usd} / ${c.min_pct}% / ${c.sec}s</span>`);
      row.appendChild(button(t("delete"), () => {
        store.update((p) => p.ticks.custom.splice(i, 1));
        render();
      }, "mini"));
      list.appendChild(row);
    });
  };
  render();
  const pair = el("input");
  pair.placeholder = "BTCUSDT";
  const ex = el("input");
  ex.placeholder = "binance";
  const mt = el("select", "sel");
  for (const m of ["futures", "spot"]) {
    const o = el("option", "", m);
    o.value = m;
    mt.appendChild(o);
  }
  const usd = numInput(1000, { min: 0 });
  const pctI = numInput(0.1, { min: 0, step: 0.01 });
  const sec = numInput(1, { min: 1 });
  box.append(list, field("Coin", pair), field("Exchange", ex), field("Market", mt), field("Exception $", usd), field("Exception %", pctI), field("Sec", sec));
  box.appendChild(button(t("add"), () => {
    if (!pair.value.trim() || !ex.value.trim()) return;
    store.update((p) => p.ticks.custom.push({ pair: pair.value.trim().toUpperCase(), exchange: ex.value.trim().toLowerCase(), market_type: mt.value, min_usd: Number(usd.value) || 0, min_pct: Number(pctI.value) || 0, sec: Number(sec.value) || 1 }));
    render();
  }, "btn primary"));
  modal(t("menu.customticks"), box);
}

export function openBotSettings(): void {
  const s = store.profile.bots;
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("bots.title"))}<div class="panel-sub">${esc(t("menu.bots.sub"))}</div>`));
  box.appendChild(el("div", "section-title", t("bots.patterns")));
  for (const pn of ["P1", "C1", "S1"] as const) box.appendChild(checkbox(t(`bots.${pn}`), s.patterns[pn], (v) => store.update((p) => (p.bots.patterns[pn] = v))));
  const minVol = numInput(s.min_tick_volume, { min: 0 });
  const minMove = numInput(s.min_tick_move, { min: 0, step: 0.01 });
  box.append(field(t("bots.minVol"), minVol), field(t("bots.minMove"), minMove), checkbox(t("bots.hideR0"), s.hide_r0, (v) => store.update((p) => (p.bots.hide_r0 = v))));
  box.appendChild(el("div", "section-title", t("ticks.exchanges")));
  box.appendChild(exchangeToggles((ex) => s.exchanges[ex] !== false, (ex, v) => store.update((p) => (p.bots.exchanges[ex] = v))));
  box.appendChild(button(t("apply"), () => {
    store.update((p) => {
      p.bots.min_tick_volume = Number(minVol.value) || 0;
      p.bots.min_tick_move = Number(minMove.value) || 0;
    });
    toast(t("save"));
  }, "btn primary"));
  modal(t("bots.title"), box);
}

export function openDensitySettings(): void {
  const s = store.profile.density;
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", `${esc(t("density.title"))}<div class="panel-sub">${esc(t("density.note"))}</div>`));
  const inputs = new Map<string, HTMLInputElement>();
  for (const ex of ALL_EXCHANGES) {
    const cfg = s.exchanges[ex] ?? { enabled: true, min_usd: 0 };
    const row = el("div", "toggle-row", `<span>${esc(ex)}</span>`);
    row.prepend(toggle(cfg.enabled, (v) => store.update((p) => (p.density.exchanges[ex] = { ...(p.density.exchanges[ex] ?? { min_usd: 0 }), enabled: v }))));
    const i = numInput(cfg.min_usd, { min: 0, placeholder: t("density.min") });
    inputs.set(ex, i);
    row.appendChild(i);
    box.appendChild(row);
  }
  box.appendChild(button(t("apply"), () => {
    store.update((p) => {
      for (const [ex, i] of inputs) p.density.exchanges[ex] = { ...(p.density.exchanges[ex] ?? { enabled: true }), min_usd: Number(i.value) || 0 };
    });
    toast(t("save"));
  }, "btn primary"));
  modal(t("density.title"), box);
}

export function openInfoblock(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(t("info.title"))));
  for (const k of ["news", "onchain", "listings"] as const) {
    const row = el("div", "toggle-row", `<span>${esc(t(`info.${k}`))}</span>`);
    row.prepend(toggle(store.profile.prefs.infoblock[k], (v) => store.update((p) => (p.prefs.infoblock[k] = v))));
    box.appendChild(row);
  }
  modal(t("info.title"), box);
}

export function openColumns(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(t("menu.columns"))));
  for (const c of COLUMNS) {
    const row = el("div", "toggle-row", `<span>${esc(t(c.label))}</span>`);
    row.prepend(toggle(store.profile.prefs.columns[c.id] ?? DEFAULT_COLUMNS[c.id], (v) => store.update((p) => (p.prefs.columns[c.id] = v))));
    box.appendChild(row);
  }
  modal(t("menu.columns"), box);
}

export function openMarkers(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(t("menu.markers"))));
  for (const k of Object.keys(store.profile.prefs.markers)) {
    const row = el("div", "toggle-row", `<span>${esc(k)}</span>`);
    row.prepend(toggle(store.profile.prefs.markers[k], (v) => store.update((p) => (p.prefs.markers[k] = v))));
    box.appendChild(row);
  }
  modal(t("menu.markers"), box);
}

export function openTimezone(): void {
  const box = el("div", "panel");
  const sel = el("select", "sel");
  for (let h = -12; h <= 14; h++) {
    const o = el("option", "", `UTC${h >= 0 ? "+" : "-"}${String(Math.abs(h)).padStart(2, "0")}:00`);
    o.value = String(h * 60);
    sel.appendChild(o);
  }
  sel.value = String(store.profile.prefs.utc_offset_min);
  sel.onchange = () => store.update((p) => (p.prefs.utc_offset_min = Number(sel.value)));
  box.append(el("div", "panel-title", esc(t("menu.tz"))), field(t("menu.tz.sub"), sel));
  modal(t("menu.tz"), box);
}

export function openHotkeys(): void {
  const box = el("div", "panel");
  box.innerHTML = `<div class="panel-title">${esc(t("menu.hotkeys"))}</div><table class="tbl"><tbody>
  <tr><td>Shift</td><td>Линейка на графике / Ruler</td></tr><tr><td>Shift + F</td><td>В избранное / Favorite</td></tr>
  <tr><td>Клик по тикеру</td><td>Копировать и открыть в терминале</td></tr><tr><td>Клик по бирже</td><td>Открыть график</td></tr>
  <tr><td>⊘</td><td>Бан 24 ч (Shift — навсегда, Alt — 1 ч)</td></tr><tr><td>▤</td><td>График</td></tr><tr><td>⧉</td><td>Тикер / терминал</td></tr>
  <tr><td>🟩 / 🟥</td><td>Покупка / продажа</td></tr><tr><td>KR F, GT S</td><td>Биржа + Futures / Spot</td></tr></tbody></table>`;
  modal(t("menu.hotkeys"), box);
}

export function openBans(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(t("bans.title"))));
  const render = () => {
    box.querySelectorAll(".list").forEach((x) => x.remove());
    const list = el("div", "list");
    let any = false;
    for (const [section, m] of Object.entries(store.profile.bans)) {
      for (const [key, until] of Object.entries(m)) {
        any = true;
        const row = el("div", "list-row", `<span><b>${esc(section)}</b> ${esc(key)} — ${until === 0 ? esc(t("bans.forever")) : new Date(until * 1000).toLocaleString()}</span>`);
        row.appendChild(button(t("delete"), () => {
          store.update((p) => delete p.bans[section][key]);
          render();
        }, "mini"));
        list.appendChild(row);
      }
    }
    if (!any) list.appendChild(el("div", "hint", esc(t("bans.empty"))));
    box.appendChild(list);
  };
  render();
  modal(t("bans.title"), box);
}

export function openConfigs(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", esc(t("cfg.title"))));
  const list = el("div", "list");
  const load = async () => {
    list.innerHTML = "";
    const r = await apiGet<{ items: Array<{ id: string; title: string; payload: any; updated_at: number }>; max_items: number }>("/api/auth/settings-configs");
    for (const it of r.items) {
      const row = el("div", "list-row", `<span>${esc(it.title)} <small>${new Date(it.updated_at * 1000).toLocaleString()}</small></span>`);
      row.append(button(t("cfg.restore"), () => {
        store.replace(it.payload);
        toast(t("cfg.restore"));
        location.reload();
      }, "mini"), button(t("delete"), async () => {
        await apiDelete(`/api/auth/settings-configs/${it.id}`);
        await load();
      }, "mini"));
      list.appendChild(row);
    }
    box.querySelector(".cfg-count")!.textContent = `${r.items.length}/${r.max_items}`;
  };
  box.appendChild(el("div", "hint cfg-count", ""));
  box.appendChild(list);
  const title = el("input");
  title.placeholder = "Main";
  box.appendChild(field("Title", title));
  const row = el("div", "btn-row");
  row.append(
    button(t("cfg.save"), async () => {
      try {
        await apiPost("/api/auth/settings-configs", { title: title.value || "config", payload: store.profile });
        await load();
        toast(t("save"));
      } catch (e) {
        toast(String((e as Error).message), "err");
      }
    }, "btn primary"),
    button(t("cfg.export"), () => {
      const blob = new Blob([JSON.stringify(store.profile, null, 2)], { type: "application/json" });
      const a = el("a");
      a.href = URL.createObjectURL(blob);
      a.download = "scryner-config.json";
      a.click();
    }),
    button(t("cfg.import"), () => {
      const i = el("input");
      i.type = "file";
      i.accept = "application/json";
      i.onchange = async () => {
        const f = i.files?.[0];
        if (!f) return;
        store.replace(JSON.parse(await f.text()));
        location.reload();
      };
      i.click();
    }),
  );
  box.appendChild(row);
  void load();
  modal(t("cfg.title"), box);
}

export function openSourcesPicker(): void {
  const box = el("div", "panel");
  box.appendChild(el("div", "panel-title", "Sources"));
  box.appendChild(el("div", "section-title", "Futures"));
  box.appendChild(exchangeToggles((ex) => store.profile.prefs.sources_futures.includes(ex), (ex, v) => store.update((p) => (p.prefs.sources_futures = v ? [...new Set([...p.prefs.sources_futures, ex])] : p.prefs.sources_futures.filter((x) => x !== ex))), FUTURES_SOURCES));
  box.appendChild(el("div", "section-title", "Spot"));
  box.appendChild(exchangeToggles((ex) => store.profile.prefs.sources_spot.includes(ex), (ex, v) => store.update((p) => (p.prefs.sources_spot = v ? [...new Set([...p.prefs.sources_spot, ex])] : p.prefs.sources_spot.filter((x) => x !== ex))), SPOT_SOURCES));
  box.appendChild(el("p", "hint", "Изменения применяются после переподключения (кнопка Применить)."));
  box.appendChild(button(t("apply"), () => location.reload(), "btn primary"));
  modal("Sources", box);
}
