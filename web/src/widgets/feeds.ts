// Feed widgets: alert history, ticks, bots, densities, TWAP, news/onchain/listings.

import { data } from "../core/data";
import { store } from "../core/store";
import { t } from "../core/i18n";
import { ago, exchangeBadge, hhmmss, mmss, pct, shortNum, usd, duration } from "../core/format";
import { AlertEngine, type AlertItem, alertTelegramText, reasonSummary } from "../core/alerts";
import { telegramQueue, playSound, speak, pushToPhone } from "../core/notify";
import { autoOpen, copyTicker } from "../core/terminals";
import type { BotItem, DensityItem, FeedMessage, Tick, TwapItem } from "../core/types";
import { keyOf } from "../core/types";
import { el, esc, widgetFrame, type WidgetFrame } from "./common";

export interface WidgetCtx {
  openChart: (key: string) => void;
  alertEngine: AlertEngine;
}

function rowActions(key: string, exchange: string, pair: string, market: string, section: string, ctx: WidgetCtx): HTMLElement {
  const box = el("span", "row-actions");
  const ban = el("button", "mini", "⊘");
  ban.title = "Ban 24h (shift: forever, alt: 1h)";
  ban.onclick = (e) => {
    e.stopPropagation();
    store.ban(section, key, e.shiftKey ? 0 : e.altKey ? 3600 : 86400);
  };
  const chart = el("button", "mini", "▤");
  chart.title = "Open chart";
  chart.onclick = (e) => {
    e.stopPropagation();
    ctx.openChart(key);
  };
  const copy = el("button", "mini", "⧉");
  copy.title = "Copy ticker / open in terminal";
  copy.onclick = (e) => {
    e.stopPropagation();
    void copyTicker({ exchange, pair, market_type: market });
    void autoOpen({ exchange, pair, market_type: market }, section);
  };
  box.append(chart, copy, ban);
  return box;
}

// ------------------------------------------------------------------ alerts history
export function alertsWidget(ctx: WidgetCtx): WidgetFrame {
  const marketSel = el("select", "sel");
  for (const r of ["all_futures", "all_spots", "all_in_one", "all_stocks"]) {
    const o = el("option", "", r === "all_futures" ? "All Futures" : r === "all_spots" ? "All Spot" : r === "all_stocks" ? "All Stocks" : "All in one");
    o.value = r;
    marketSel.appendChild(o);
  }
  marketSel.value = store.profile.prefs.market_route;
  marketSel.onchange = () => store.update((p) => (p.prefs.market_route = marketSel.value));
  let sound = true;
  const f = widgetFrame(t("w.alerts"), { help: "alerts", sound: true, onSound: (on) => (sound = on), extra: [marketSel] });
  const table = el("table", "tbl");
  table.innerHTML = `<thead><tr><th>${t("col.exchange")}</th><th>${t("col.symbol")}</th><th>${t("col.vol")}</th><th>${t("col.dvolShort")}</th><th></th></tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody")!;
  f.body.appendChild(table);

  const render = (item: AlertItem, prepend: boolean) => {
    const key = item.key;
    const tr = el("tr", `alert-row ${item.reasons.some((r) => r.code === "sell") && !item.reasons.some((r) => r.code === "buy") ? "sell" : "buy"}`);
    tr.title = `${hhmmss(item.timestamp / 1000)} ${reasonSummary(item.reasons, true)}`;
    tr.innerHTML = `<td class="badge-cell"><span class="badge">${esc(exchangeBadge(item.exchange, item.market_type))}</span></td><td class="sym">${esc(item.display_pair)}</td><td>${shortNum(item.buy_sum + item.sell_sum)}</td><td class="${(item.dvol5_pct ?? 0) > 5 ? "hot" : ""}">${pct(item.dvol5_pct, 2, false)}</td><td></td>`;
    tr.querySelector("td:last-child")!.appendChild(rowActions(key, item.exchange, item.pair, item.market_type, "alerts", ctx));
    tr.querySelector(".sym")!.addEventListener("click", () => ctx.openChart(key));
    tr.querySelector(".badge-cell")!.addEventListener("click", () => ctx.openChart(key));
    if (prepend) tbody.prepend(tr);
    else tbody.appendChild(tr);
    while (tbody.children.length > 300) tbody.lastElementChild!.remove();
  };
  for (const it of [...ctx.alertEngine.history].slice(-300)) render(it, true);
  ctx.alertEngine.listeners.push((item) => {
    render(item, true);
    if (sound) playSound("alarm");
    if (store.profile.ticks.tts) speak(`${item.display_pair} ${reasonSummary(item.reasons)}`);
    telegramQueue.enqueue("alerts", item.id, alertTelegramText(item), item.timestamp);
    void pushToPhone("alerts", `[ALERT] ${item.display_pair}`, reasonSummary(item.reasons, true));
    if (store.profile.telegram.auto_open_meta) void autoOpen({ exchange: item.exchange, pair: item.pair, market_type: item.market_type }, "alert");
  });
  return f;
}

// ------------------------------------------------------------------ ticks
export function ticksWidget(ctx: WidgetCtx): WidgetFrame {
  let sound = true;
  const f = widgetFrame(t("w.ticks"), { help: "ticks", sound: true, onSound: (on) => (sound = on) });
  const list = el("div", "tick-list");
  f.body.appendChild(list);
  const accept = (tk: Tick): boolean => {
    const s = store.profile.ticks;
    if (!s.exchanges[tk.exchange]) return false;
    const key = keyOf(tk);
    if (store.isBanned("ticks", key)) return false;
    const vol = tk.buy + tk.sell;
    const custom = s.custom.find((c) => c.pair === tk.pair && c.exchange === tk.exchange && c.market_type === tk.market_type);
    if (custom) return vol >= custom.min_usd && Math.abs(tk.price_move_pct) >= custom.min_pct;
    if (vol >= tk.tick_threshold_base * s.multiplier) return true;
    if (s.always_play_volume > 0 && vol >= s.always_play_volume) return true;
    return s.always_play_volatility > 0 && Math.abs(tk.price_range_pct) >= s.always_play_volatility;
  };
  data.onTick.on((tk) => {
    if (!accept(tk)) return;
    const key = keyOf(tk);
    const side = tk.buy >= tk.sell ? "buy" : "sell";
    const row = el("div", `tick-row ${side}`);
    row.innerHTML = `<span class="badge">${esc(exchangeBadge(tk.exchange, tk.market_type))}</span><span class="sym">${esc(tk.display_pair)}</span><span class="num ${tk.price_move_pct >= 0 ? "up" : "dn"}">${pct(tk.price_move_pct)}</span><span class="num">${shortNum(tk.buy + tk.sell)}</span><span class="time">${mmss(tk.timestamp)}</span>`;
    row.appendChild(rowActions(key, tk.exchange, tk.pair, tk.market_type, "ticks", ctx));
    row.querySelector(".sym")!.addEventListener("click", () => ctx.openChart(key));
    list.prepend(row);
    while (list.children.length > 200) list.lastElementChild!.remove();
    if (sound) playSound(store.profile.ticks.sound || "bum", store.profile.ticks.volume);
    telegramQueue.enqueue("ticks", `${key}|${tk.timestamp}`, `[TICK] ${tk.exchange.toUpperCase()} ${tk.market_type === "spot" ? "S" : "F"} ${tk.display_pair}\n${side === "buy" ? "🟩" : "🟥"} ${usd(tk.buy + tk.sell)} ${pct(tk.price_move_pct)} @ ${tk.price}`, tk.timestamp * 1000);
    void pushToPhone("ticks", `[TICK] ${tk.display_pair}`, `${usd(tk.buy + tk.sell)} ${pct(tk.price_move_pct)}`);
  });
  return f;
}

// ------------------------------------------------------------------ bots
export function botsWidget(ctx: WidgetCtx): WidgetFrame {
  let sound = false;
  const f = widgetFrame(t("w.bots"), { help: "bots", sound: true, onSound: (on) => (sound = on) });
  const list = el("div", "card-list");
  f.body.appendChild(list);
  const card = (b: BotItem, prepend: boolean) => {
    const s = store.profile.bots;
    const pattern = b.pattern ?? b.text.slice(0, 2);
    if (!(s.patterns as any)[pattern]) return;
    if (s.hide_r0 && (b.rating === 0 || /\bR0\b/.test(b.text))) return;
    if (b.exchange && !s.exchanges[b.exchange]) return;
    if (b.key && store.isBanned("bots", b.key)) return;
    const lines = b.text.split("\n");
    const head = lines[0] ?? "";
    const sym = (lines[2] ?? "").trim();
    const c = el("div", `card bot ${head.includes("🟩") ? "buy" : "sell"}`);
    c.innerHTML = `<div class="card-head"><span class="card-title">${esc(head)}</span><span class="time">${esc(b.captured_at.slice(11, 16))}</span></div><div class="card-sym">${esc(sym)}</div><pre class="card-body">${esc(lines.slice(3).join("\n"))}</pre>`;
    if (b.key) {
      const [exchange, pair, market] = [b.key.slice(0, b.key.indexOf(":")), b.key.slice(b.key.indexOf(":") + 1, b.key.lastIndexOf(":")), b.key.slice(b.key.lastIndexOf(":") + 1)];
      c.querySelector(".card-head")!.appendChild(rowActions(b.key, exchange, pair, market, "bots", ctx));
      c.querySelector(".card-sym")!.addEventListener("click", () => ctx.openChart(b.key!));
    }
    if (prepend) list.prepend(c);
    else list.appendChild(c);
    while (list.children.length > 120) list.lastElementChild!.remove();
  };
  data.onBot.on((b) => {
    if (b === null) {
      list.innerHTML = "";
      for (const x of data.bots) card(x, true);
      return;
    }
    card(b, true);
    if (sound) playSound("ping");
    telegramQueue.enqueue("bots", b.id ?? b.captured_at, b.text, Date.parse(b.captured_at));
    void pushToPhone("bots", "[BOT]", b.text.split("\n").slice(0, 3).join(" "));
  });
  for (const x of data.bots) card(x, true);
  return f;
}

// ------------------------------------------------------------------ densities
export function densityWidget(ctx: WidgetCtx): WidgetFrame {
  let sound = false;
  const f = widgetFrame(t("w.density"), { help: "density", sound: true, onSound: (on) => (sound = on) });
  const list = el("div", "card-list");
  f.body.appendChild(list);
  const nodes = new Map<string, HTMLElement>();
  const accept = (d: DensityItem): boolean => {
    const s = store.profile.density.exchanges[d.exchange];
    if (s && !s.enabled) return false;
    if (s && s.min_usd > 0 && d.size_usd < s.min_usd) return false;
    return !store.isBanned("density", `${d.exchange}:${d.pair}:${d.market_type}`);
  };
  const render = (d: DensityItem, prepend: boolean) => {
    if (!accept(d)) return;
    const key = `${d.exchange}:${d.pair}:${d.market_type}`;
    let c = nodes.get(d.id);
    if (!c) {
      c = el("div", `card density ${d.side === "bid" ? "buy" : "sell"}`);
      nodes.set(d.id, c);
      if (prepend) list.prepend(c);
      else list.appendChild(c);
    }
    const impact = Object.entries(d.impact ?? {}).sort((a, b) => Number(a[0]) - Number(b[0])).map(([w, i]) => `<div class="impact ${i.label}">${Number(w) / 60}m: ${i.label} ${pct(i.move_pct, 4)}</div>`).join("");
    c.innerHTML = `<div class="card-head"><span class="card-title">${esc(d.exchange_label)}</span><span class="time">${hhmmss(d.captured_at)}</span></div><div class="card-sym">${esc(d.display_pair)}</div><div class="metric-line">${esc(d.metric_line)}${d.is_iceberg ? ' <span class="tag">iceberg</span>' : ""}${d.removed_at ? ' <span class="tag off">removed</span>' : ""}</div><div class="impact-box"><div class="impact-title">${t("impact")}</div>${impact}</div>`;
    c.querySelector(".card-head")!.appendChild(rowActions(key, d.exchange, d.pair, d.market_type, "density", ctx));
    c.querySelector(".card-sym")!.addEventListener("click", () => ctx.openChart(key));
    while (list.children.length > 120) {
      const last = list.lastElementChild as HTMLElement;
      for (const [id, n] of nodes) if (n === last) nodes.delete(id);
      last.remove();
    }
  };
  data.onDensity.on((d) => {
    render(d, true);
    if (sound) playSound("ping");
    telegramQueue.enqueue("density", d.id, d.text, d.captured_at * 1000);
    void pushToPhone("density", `[WALL] ${d.display_pair}`, d.metric_line);
  });
  data.onDensityUpdate.on((d) => {
    if (d === null) {
      list.innerHTML = "";
      nodes.clear();
      for (const x of data.densities) render(x, true);
      return;
    }
    const full = data.densities.find((x) => x.id === d.id);
    if (full) render(full, false);
  });
  for (const x of data.densities) render(x, true);
  return f;
}

// ------------------------------------------------------------------ TWAP
export function twapWidget(ctx: WidgetCtx): WidgetFrame {
  let sum = false;
  const sumBtn = el("button", "tool-btn", "SUM");
  sumBtn.onclick = () => {
    sum = !sum;
    sumBtn.classList.toggle("active", sum);
    render();
  };
  const f = widgetFrame(t("w.twap"), { help: "twap", extra: [sumBtn] });
  const table = el("table", "tbl");
  table.innerHTML = `<thead><tr><th>Symbol</th><th>Side</th><th>$</th><th>Time</th><th></th></tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody")!;
  f.body.appendChild(table);
  const render = () => {
    const items = [...data.twap.data.values()].filter((v) => v?.kind === "twap") as TwapItem[];
    const now = Date.now();
    let rows: Array<{ sym: string; side: string; amount: number; left: number; n: number; key: string; item: TwapItem }> = [];
    if (sum) {
      const g = new Map<string, any>();
      for (const it of items) {
        const k = `${it.display_pair}|${it.side}`;
        const cur = g.get(k) ?? { sym: it.display_pair, side: it.side, amount: 0, left: 0, n: 0, key: it.chart_key, item: it };
        cur.amount += it.amount;
        cur.left = Math.max(cur.left, it.expires_ms - now);
        cur.n += 1;
        g.set(k, cur);
      }
      rows = [...g.values()];
    } else rows = items.map((it) => ({ sym: it.display_pair, side: it.side, amount: it.amount, left: it.expires_ms - now, n: 1, key: it.chart_key, item: it }));
    rows.sort((a, b) => b.amount - a.amount);
    tbody.innerHTML = "";
    for (const r of rows.slice(0, 150)) {
      const tr = el("tr", r.side === "BUY" ? "buy" : "sell");
      tr.innerHTML = `<td class="sym">${esc(r.sym)}</td><td>${r.side}${r.n > 1 ? ` ×${r.n}` : ""}</td><td>${usd(r.amount)}</td><td>${duration(r.left)}</td><td></td>`;
      tr.querySelector(".sym")!.addEventListener("click", () => ctx.openChart(r.key));
      tr.querySelector("td:last-child")!.appendChild(rowActions(r.key, "hyperliquid", r.item.display_pair, r.item.market_type, "twap", ctx));
      tbody.appendChild(tr);
    }
  };
  const seen = new Set<string>();
  data.onTwap.on(() => {
    render();
    for (const v of data.twap.data.values()) {
      if (v?.kind === "twap" && !seen.has(v.id)) {
        seen.add(v.id);
        if (seen.size > 5000) seen.clear();
        telegramQueue.enqueue("twap", v.id, `[TWAP] ${v.display_pair} ${v.side} ${usd(v.amount)} / ${v.duration_min}m`, v.time_ms);
      }
    }
  });
  window.setInterval(render, 5000);
  render();
  return f;
}

// ------------------------------------------------------------------ Telegram feeds
export function feedWidget(kind: "news" | "onchain" | "listings"): WidgetFrame {
  const f = widgetFrame(t(`w.${kind}`), {});
  const list = el("div", "feed-list");
  f.body.appendChild(list);
  const render = (m: FeedMessage, prepend: boolean) => {
    const text = m.message?.text ?? m.text ?? "";
    const title = m.chat?.title ?? m.sender?.name ?? "";
    const when = (m.message?.date ?? m.captured_at ?? "").slice(11, 16);
    const c = el("div", "feed-item");
    c.innerHTML = `<div class="feed-head"><b>${esc(title)}</b><span class="time">${esc(when)}</span></div><div class="feed-text">${esc(text).replace(/\n/g, "<br>")}</div>`;
    if (prepend) list.prepend(c);
    else list.appendChild(c);
    while (list.children.length > 150) list.lastElementChild!.remove();
  };
  data.onFeed.on((e) => {
    if (e.kind !== kind) return;
    if (e.item === null) {
      list.innerHTML = "";
      for (const m of data[kind]) render(m, true);
    } else render(e.item, true);
  });
  for (const m of data[kind]) render(m, true);
  void ago;
  return f;
}
