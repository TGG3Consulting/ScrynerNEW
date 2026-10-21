// Chart panel: candles + volume + OI line + event markers (Lightweight Charts v5).

import { createChart, CandlestickSeries, HistogramSeries, LineSeries, createSeriesMarkers, type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type UTCTimestamp, type SeriesMarker, type Time } from "lightweight-charts";
import { apiPost } from "./api";
import { data } from "./data";
import { store } from "./store";
import type { Bar } from "./types";
import { tvSymbol, shortNum } from "./format";
import { splitKey } from "./types";

export type Timeframe = "1s" | "1m" | "5m" | "15m" | "1h";
const TF_SEC: { [k in Timeframe]: number } = { "1s": 1, "1m": 60, "5m": 300, "15m": 900, "1h": 3600 };

function fmtDuration(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

export interface ChartMarker {
  ts: number;
  kind: "bot" | "density" | "twap" | "alert" | "tick";
  text: string;
  side?: "buy" | "sell";
  key?: string; // stable identity (e.g. density wall id): repeats replace instead of stacking
}

export class ChartPanel {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  oi: ISeriesApi<"Line">;
  key = "";
  timeframe: Timeframe = "1m";
  private bars1m: Bar[] = [];
  private bars1s: Bar[] = [];
  private markers: ChartMarker[] = [];
  private markersApi: ISeriesMarkersPluginApi<Time> | null = null;
  private offChart: (() => void) | null = null;
  private title: HTMLElement;
  private tfBar: HTMLElement;
  private container: HTMLElement;
  private rulerBox: HTMLElement | null = null;
  private rulerLabel: HTMLElement | null = null;
  private rulerAnchor: { x: number; y: number; time: number; price: number } | null = null;

  constructor(public root: HTMLElement, opts: { onFavorite?: (key: string) => void } = {}) {
    root.classList.add("chart-panel");
    root.innerHTML = `<div class="chart-head"><span class="chart-title">—</span><span class="chart-tf"></span><span class="chart-badges"></span></div><div class="chart-body"></div>`;
    this.title = root.querySelector(".chart-title")!;
    this.tfBar = root.querySelector(".chart-tf")!;
    this.container = root.querySelector(".chart-body")!;
    for (const tf of ["1s", "1m", "5m", "15m", "1h"] as Timeframe[]) {
      const b = document.createElement("button");
      b.textContent = tf;
      b.className = "tf-btn" + (tf === this.timeframe ? " active" : "");
      b.onclick = () => this.setTimeframe(tf);
      this.tfBar.appendChild(b);
    }
    this.chart = createChart(this.container, {
      layout: { background: { color: "#101214" }, textColor: "#9aa3ad", fontSize: 10, attributionLogo: false },
      grid: { vertLines: { color: "#1a1d21" }, horzLines: { color: "#1a1d21" } },
      rightPriceScale: { borderColor: "#262a2f", scaleMargins: { top: 0.05, bottom: 0.3 } },
      timeScale: { borderColor: "#262a2f", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    this.candles = this.chart.addSeries(CandlestickSeries, { upColor: "#26a69a", downColor: "#ef5350", wickUpColor: "#26a69a", wickDownColor: "#ef5350", borderVisible: false });
    this.volume = this.chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol" });
    this.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.72, bottom: 0.12 } });
    this.oi = this.chart.addSeries(LineSeries, { color: "#f5c542", lineWidth: 1, priceScaleId: "oi", lastValueVisible: true, priceLineVisible: false });
    this.chart.priceScale("oi").applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } });
    root.addEventListener("keydown", (e) => {
      if (e.shiftKey && e.key.toLowerCase() === "f" && this.key) opts.onFavorite?.(this.key);
    });
    root.tabIndex = 0;
    this.setupRuler();
  }

  // Shift + движение мышью = линейка: прямоугольник замера + плашка (%, Δцена, объём, время, бары).
  // Используем нативный crosshairMove Lightweight Charts (всегда срабатывает над графиком) и
  // глобальное отслеживание Shift (не зависит от фокуса панели).
  private setupRuler(): void {
    this.container.style.position = "relative";
    this.rulerBox = document.createElement("div");
    this.rulerBox.className = "chart-ruler";
    this.rulerLabel = document.createElement("div");
    this.rulerLabel.className = "chart-ruler-label";
    this.rulerBox.style.display = this.rulerLabel.style.display = "none";
    this.container.append(this.rulerBox, this.rulerLabel);
    let shift = false;
    const onKey = (e: KeyboardEvent): void => {
      shift = e.shiftKey;
      if (!shift) this.clearRuler();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    this.chart.subscribeCrosshairMove((param) => {
      if (!shift || !param.point || param.time == null) {
        if (!shift) this.clearRuler();
        return;
      }
      const x = param.point.x;
      const y = param.point.y;
      const price = this.candles.coordinateToPrice(y);
      if (price == null) return;
      const time = Number(param.time);
      if (!this.rulerAnchor) {
        this.rulerAnchor = { x, y, time, price };
        return;
      }
      this.drawRuler(x, y, time, price);
    });
  }

  private clearRuler(): void {
    this.rulerAnchor = null;
    if (this.rulerBox) this.rulerBox.style.display = "none";
    if (this.rulerLabel) this.rulerLabel.style.display = "none";
  }

  private drawRuler(x: number, y: number, time: number, price: number): void {
    const a = this.rulerAnchor;
    if (!a || !this.rulerBox || !this.rulerLabel) return;
    const up = price >= a.price;
    const pctChange = a.price ? ((price - a.price) / a.price) * 100 : 0;
    const delta = price - a.price;
    const tfSec = TF_SEC[this.timeframe];
    const off = store.profile.prefs.utc_offset_min * 60;
    const lo = Math.min(a.time, time);
    const hi = Math.max(a.time, time);
    const bars = Math.max(1, Math.round((hi - lo) / tfSec));
    let vol = 0;
    for (const b of this.aggregated()) {
      const bt = b.ts + off;
      if (bt >= lo && bt <= hi) vol += b.total_quote;
    }
    const col = up ? "#26a69a" : "#ef5350";
    const left = Math.min(a.x, x);
    const top = Math.min(a.y, y);
    this.rulerBox.style.cssText = `display:block;position:absolute;left:${left}px;top:${top}px;width:${Math.abs(x - a.x)}px;height:${Math.abs(y - a.y)}px;background:${up ? "rgba(38,166,154,0.15)" : "rgba(239,83,80,0.15)"};border:1px solid ${col};pointer-events:none;z-index:10;`;
    this.rulerLabel.textContent = `${pctChange >= 0 ? "+" : ""}${pctChange.toFixed(2)}% d ${delta >= 0 ? "+" : ""}${Number(delta.toPrecision(5))} V ${shortNum(vol)} T ${fmtDuration(bars * tfSec)} ${bars} bars`;
    this.rulerLabel.style.cssText = `display:block;position:absolute;left:${x + 10}px;top:${y + 10}px;background:${col};color:#06110f;font:600 10px/1.4 system-ui,sans-serif;padding:1px 6px;border-radius:3px;white-space:nowrap;pointer-events:none;z-index:11;`;
  }

  setTimeframe(tf: Timeframe): void {
    this.timeframe = tf;
    for (const b of this.tfBar.querySelectorAll("button")) b.classList.toggle("active", b.textContent === tf);
    void this.reload();
  }

  async load(key: string): Promise<void> {
    if (this.key && this.key !== key) data.unsubscribeChart(this.key);
    this.key = key;
    const { exchange, pair, market_type } = splitKey(key);
    this.title.textContent = tvSymbol(exchange, pair, market_type);
    this.markers = [];
    await this.reload();
    data.subscribeChart(key);
    if (!this.offChart) {
      this.offChart = data.onChart.on((m) => {
        if (m.key !== this.key || m.timeframe !== "1m") return;
        if (m.closed) this.upsert1m(m.closed as Bar);
        if (m.partial) this.upsert1m(m.partial as Bar);
        this.render();
      });
    }
  }

  private async reload(): Promise<void> {
    if (!this.key) return;
    const { exchange, pair, market_type } = splitKey(this.key);
    const tf = this.timeframe === "1s" ? "1s" : "1m";
    const limit = tf === "1s" ? 1800 : 360;
    try {
      const r = await apiPost("/api/graph/v1/history_batch", { items: [{ key: this.key, timeframe: tf, exchange, pair, market_type, source_route_id: `${exchange}_${market_type}`, limit, include_partial: 1 }], include_partial: 1, feature: "advanced" }, { route: data.route });
      const item = r.items[`${this.key}|${tf}`];
      const bars: Bar[] = item ? [...item.bars, ...(item.partial ? [item.partial] : [])] : [];
      if (tf === "1s") this.bars1s = bars;
      else this.bars1m = bars;
    } catch {
      /* keep what we have */
    }
    this.render();
    this.chart.timeScale().scrollToRealTime();
  }

  private upsert1m(b: Bar): void {
    const arr = this.bars1m;
    if (arr.length && arr[arr.length - 1].ts === b.ts) arr[arr.length - 1] = b;
    else if (!arr.length || arr[arr.length - 1].ts < b.ts) {
      arr.push(b);
      if (arr.length > 2000) arr.shift();
    } else {
      const i = arr.findIndex((x) => x.ts === b.ts);
      if (i >= 0) arr[i] = b;
    }
  }

  addMarker(m: ChartMarker): void {
    const enabled = store.profile.prefs.markers;
    if (m.kind === "bot" && !enabled.bots) return;
    if (m.kind === "density" && !enabled.density) return;
    if (m.kind === "twap" && !enabled.twap) return;
    if (m.kind === "alert" && !enabled.alerts) return;
    if (m.kind === "tick" && !enabled.ticks) return;
    if (m.key) {
      // one marker per stable identity (density wall, bot signal): refresh, do not stack
      const i = this.markers.findIndex((x) => x.key === m.key);
      if (i >= 0) {
        this.markers[i] = m;
        this.render();
        return;
      }
    }
    this.markers.push(m);
    if (this.markers.length > 200) this.markers.shift();
    this.render();
  }

  private aggregated(): Bar[] {
    const sec = TF_SEC[this.timeframe];
    if (this.timeframe === "1s") return this.bars1s;
    if (sec === 60) return this.bars1m;
    const out: Bar[] = [];
    for (const b of this.bars1m) {
      const ts = Math.floor(b.ts / sec) * sec;
      const last = out[out.length - 1];
      if (last && last.ts === ts) {
        last.h = Math.max(last.h, b.h);
        last.l = Math.min(last.l, b.l);
        last.c = b.c;
        last.buy_quote += b.buy_quote;
        last.sell_quote += b.sell_quote;
        last.total_quote += b.total_quote;
        last.trades_count += b.trades_count;
        if (b.oi_close != null) last.oi_close = b.oi_close;
      } else out.push({ ...b, ts });
    }
    return out;
  }

  private render(): void {
    const off = store.profile.prefs.utc_offset_min * 60;
    const bars = this.aggregated();
    this.candles.setData(bars.map((b) => ({ time: (b.ts + off) as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    if (store.profile.prefs.markers.volume) {
      this.volume.setData(bars.map((b) => ({ time: (b.ts + off) as UTCTimestamp, value: b.total_quote, color: b.c >= b.o ? "rgba(38,166,154,0.5)" : "rgba(239,83,80,0.5)" })));
    } else this.volume.setData([]);
    if (store.profile.prefs.markers.oi) {
      this.oi.setData(bars.filter((b) => b.oi_close != null).map((b) => ({ time: (b.ts + off) as UTCTimestamp, value: b.oi_close as number })));
    } else this.oi.setData([]);
    const sec = TF_SEC[this.timeframe];
    const ms: SeriesMarker<Time>[] = this.markers.map((m) => ({
      time: (Math.floor(m.ts / sec) * sec + off) as UTCTimestamp,
      position: m.side === "sell" ? "aboveBar" : "belowBar",
      color: m.kind === "bot" ? "#4fc3f7" : m.kind === "density" ? "#ab47bc" : m.kind === "twap" ? "#ffa726" : m.kind === "alert" ? "#ffee58" : "#80cbc4",
      shape: m.side === "sell" ? "arrowDown" : "arrowUp",
      text: m.text,
    }));
    if (!this.markersApi) this.markersApi = createSeriesMarkers(this.candles, ms);
    else this.markersApi.setMarkers(ms);
    const last = bars[bars.length - 1];
    const badges = this.root.querySelector(".chart-badges")!;
    badges.textContent = last ? `VOL ${shortNum(last.total_quote)}${last.oi_close != null ? ` · OI ${shortNum(last.oi_close)}` : ""}` : "";
  }

  destroy(): void {
    if (this.key) data.unsubscribeChart(this.key);
    this.offChart?.();
    this.chart.remove();
  }
}
