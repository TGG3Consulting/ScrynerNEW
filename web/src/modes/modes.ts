// Mode layouts: Advanced, Simple, Graph, Tick, Favorites.

import { apiGet } from "../core/api";
import { ChartPanel } from "../core/charts";
import { data } from "../core/data";
import { store } from "../core/store";
import { t } from "../core/i18n";
import { exchangeBadge, pct, shortNum } from "../core/format";
import { keyOf, type Tick } from "../core/types";
import { el, esc } from "../widgets/common";
import { alertsWidget, botsWidget, densityWidget, feedWidget, ticksWidget, twapWidget, type WidgetCtx } from "../widgets/feeds";
import { topWidget } from "../widgets/top";

export interface ModeHandle {
  destroy: () => void;
  openChart: (key: string) => void;
}

// ------------------------------------------------------------------ Advanced
export function advancedMode(root: HTMLElement, ctx: WidgetCtx): ModeHandle {
  root.innerHTML = "";
  root.className = "mode advanced";
  const prefs = store.profile.prefs;
  const topbar = el("div", "layout-bar");
  const cfgSel = el("select", "sel");
  cfgSel.innerHTML = `<option>Main</option>`;
  const gridLabel = el("span", "grid-label", `${esc(t("grid"))} <b>${prefs.grid.cols}</b> × <b>${prefs.grid.rows}</b>`);
  const reset = el("button", "tool-btn", t("reset"));
  reset.onclick = () => {
    if (confirm(t("reset") + "?")) store.reset();
  };
  topbar.append(cfgSel, gridLabel, reset);
  const widgetsRow = el("div", "widgets-row");
  widgetsRow.style.gridTemplateColumns = `repeat(${prefs.layout.length}, minmax(0, 1fr))`;
  const chartsRow = el("div", "charts-row");
  chartsRow.style.gridTemplateColumns = `repeat(${prefs.grid.cols}, minmax(0, 1fr))`;
  const bottom = el("div", "bottom-row");
  const right = el("div", "right-col");
  const main = el("div", "main-col");
  main.append(topbar, widgetsRow, chartsRow, bottom);
  root.append(main, right);

  const charts: ChartPanel[] = [];
  let chartIdx = 0;
  const openChart = (key: string) => {
    const panel = charts[chartIdx % charts.length];
    chartIdx++;
    void panel.load(key);
    panel.root.classList.add("flash");
    window.setTimeout(() => panel.root.classList.remove("flash"), 600);
  };
  const localCtx: WidgetCtx = { ...ctx, openChart };

  const factories: { [k: string]: () => HTMLElement } = {
    alerts: () => alertsWidget(localCtx).root,
    ticks: () => ticksWidget(localCtx).root,
    bots: () => botsWidget(localCtx).root,
    density: () => densityWidget(localCtx).root,
    twap: () => twapWidget(localCtx).root,
  };
  for (const w of prefs.layout) widgetsRow.appendChild(factories[w]?.() ?? el("div"));
  for (let i = 0; i < prefs.grid.cols; i++) {
    const host = el("div", "chart-host");
    chartsRow.appendChild(host);
    charts.push(new ChartPanel(host, { onFavorite: (key) => store.update((p) => (p.favorites = p.favorites.includes(key) ? p.favorites.filter((k) => k !== key) : [...p.favorites, key])) }));
  }
  bottom.appendChild(topWidget(localCtx).root);
  if (prefs.infoblock.onchain) right.appendChild(feedWidget("onchain").root);
  if (prefs.infoblock.news) right.appendChild(feedWidget("news").root);
  if (prefs.infoblock.listings) right.appendChild(feedWidget("listings").root);

  // chart markers from events (как у оригинала: боты, плотности LW и алерты; тики — только в ленте, без стрелок на свечах)
  const offs = [
    data.onDensity.on((d) => {
      const key = `${d.exchange}:${d.pair}:${d.market_type}`;
      for (const c of charts) if (c.key === key) c.addMarker({ ts: d.captured_at, kind: "density", text: "LW", side: d.side === "bid" ? "buy" : "sell", key: `d:${d.id}` });
    }),
    data.onBot.on((b) => {
      if (!b?.key) return;
      for (const c of charts) if (c.key === b.key) c.addMarker({ ts: Date.parse(b.captured_at) / 1000, kind: "bot", text: `Bot ${b.pattern ?? ""} R${b.rating ?? 0}`, side: b.side as any, key: `b:${b.id ?? b.captured_at}` });
    }),
  ];
  ctx.alertEngine.listeners.push((a) => {
    for (const c of charts) if (c.key === a.key) c.addMarker({ ts: a.timestamp / 1000, kind: "alert", text: a.reasons.map((r) => r.label).join(",") });
  });
  // initial charts: first pinned/top keys
  window.setTimeout(() => {
    const keys = [...data.top.data.keys()].slice(0, charts.length);
    keys.forEach((k, i) => void charts[i].load(k));
  }, 1500);
  return {
    destroy: () => {
      offs.forEach((o) => o());
      charts.forEach((c) => c.destroy());
    },
    openChart,
  };
}

// ------------------------------------------------------------------ Simple
export function simpleMode(root: HTMLElement, ctx: WidgetCtx): ModeHandle {
  root.innerHTML = "";
  root.className = "mode simple";
  const left = el("div", "simple-table");
  const rightC = el("div", "simple-chart");
  root.append(left, rightC);
  const chart = new ChartPanel(rightC);
  const openChart = (key: string) => void chart.load(key);
  const localCtx: WidgetCtx = { ...ctx, openChart };
  left.appendChild(topWidget(localCtx, { full: true, title: t("simple.title") }).root);
  return { destroy: () => chart.destroy(), openChart };
}

// ------------------------------------------------------------------ Graph
export function graphMode(root: HTMLElement, ctx: WidgetCtx): ModeHandle {
  root.innerHTML = "";
  root.className = "mode graph";
  const bar = el("div", "layout-bar");
  const sortSel = el("select", "sel");
  for (const s of ["range5m", "volume5m", "natr5m", "dvol5m", "oi5m", "tpm1m", "range15m", "volume15m", "range1h", "volume1h", "oi1h"]) {
    const o = el("option", "", s);
    o.value = s;
    sortSel.appendChild(o);
  }
  const vol = el("input");
  vol.type = "number";
  vol.value = "10000";
  vol.title = "min volume5m";
  const dvol = el("input");
  dvol.type = "number";
  dvol.value = "4";
  dvol.title = "min dvol5m %";
  const tpm = el("input");
  tpm.type = "number";
  tpm.value = "20";
  tpm.title = "min tpm1m";
  const apply = el("button", "tool-btn active", "APPLY");
  bar.append(el("span", "grid-label", "SORT"), sortSel, el("span", "grid-label", "VOL5m ≥"), vol, el("span", "grid-label", "DVOL5m ≥"), dvol, el("span", "grid-label", "TPM ≥"), tpm, apply);
  const grid = el("div", "graph-grid");
  root.append(bar, grid);
  const charts: ChartPanel[] = [];
  const load = async () => {
    charts.forEach((c) => c.destroy());
    charts.length = 0;
    grid.innerHTML = "";
    const r = await apiGet("/api/graph/v1/candidates", { sort: sortSel.value, limit: 96, metric_filters: JSON.stringify({ volume5m: Number(vol.value), dvol5m: Number(dvol.value), tpm1m: Number(tpm.value) }), route: data.route });
    for (const it of r.items.slice(0, 24)) {
      const host = el("div", "graph-cell");
      const meta = el("div", "graph-meta", `<span class="badge">${esc(exchangeBadge(it.exchange, it.market_type))}</span> ${esc(it.display_pair)} · R5 ${pct(it.range5_pct, 2, false)} · NATR ${pct(it.natr5, 2, false)} · V5 ${shortNum(it.volume_5m)} · OI ${pct(it.oi_percentage)}`);
      const chost = el("div", "chart-host");
      host.append(meta, chost);
      grid.appendChild(host);
      const c = new ChartPanel(chost);
      charts.push(c);
      void c.load(it.key);
    }
  };
  apply.onclick = () => void load();
  void load();
  return { destroy: () => charts.forEach((c) => c.destroy()), openChart: () => undefined };
}

// ------------------------------------------------------------------ Tick
export function tickMode(root: HTMLElement, ctx: WidgetCtx): ModeHandle {
  root.innerHTML = "";
  root.className = "mode tick";
  const side = el("div", "tick-side");
  const grid = el("div", "tick-grid");
  root.append(side, grid);
  const charts = new Map<string, ChartPanel>();
  const openChart = (key: string) => {
    let c = charts.get(key);
    if (!c) {
      const host = el("div", "chart-host");
      grid.prepend(host);
      c = new ChartPanel(host);
      c.setTimeframe("1s");
      charts.set(key, c);
      void c.load(key);
      while (grid.children.length > 8) {
        const last = grid.lastElementChild as HTMLElement;
        for (const [k, p] of charts) if (p.root === last) {
          p.destroy();
          charts.delete(k);
        }
        last.remove();
      }
    } else grid.prepend(c.root);
  };
  const localCtx: WidgetCtx = { ...ctx, openChart };
  side.appendChild(ticksWidget(localCtx).root);
  side.appendChild(twapWidget(localCtx).root);
  const off = data.onTick.on((tk: Tick) => {
    const key = keyOf(tk);
    // тики не рисуем стрелками на свечах (как у оригинала); только авто-открываем график на крупном тике
    if (!charts.get(key) && charts.size < 4 && tk.buy + tk.sell >= tk.tick_threshold * 3) openChart(key);
  });
  return { destroy: () => {
    off();
    charts.forEach((c) => c.destroy());
  }, openChart };
}

// ------------------------------------------------------------------ Favorites
export function favoritesMode(root: HTMLElement, ctx: WidgetCtx): ModeHandle {
  root.innerHTML = "";
  root.className = "mode favorites";
  const favs = store.profile.favorites;
  if (!favs.length) {
    root.appendChild(el("div", "empty", esc(t("fav.empty"))));
    return { destroy: () => undefined, openChart: () => undefined };
  }
  const grid = el("div", "graph-grid");
  root.appendChild(grid);
  const charts: ChartPanel[] = [];
  for (const key of favs) {
    const host = el("div", "graph-cell");
    const chost = el("div", "chart-host");
    const meta = el("div", "graph-meta", esc(key));
    const rm = el("button", "mini", "✕");
    rm.onclick = () => store.update((p) => (p.favorites = p.favorites.filter((k) => k !== key)));
    meta.appendChild(rm);
    host.append(meta, chost);
    grid.appendChild(host);
    const c = new ChartPanel(chost);
    charts.push(c);
    void c.load(key);
  }
  void ctx;
  return { destroy: () => charts.forEach((c) => c.destroy()), openChart: () => undefined };
}
