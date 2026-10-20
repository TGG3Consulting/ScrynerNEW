// Data layer: all WebSocket channels for the current route, state maps and event emitters.

import { ReconnectingWs, PatchState } from "./ws";
import { store } from "./store";
import type { AlertRecord, BotItem, DensityItem, FeedMessage, Record, Tick, TwapItem } from "./types";

type Handler<T> = (x: T) => void;

class Emitter<T> {
  private hs: Handler<T>[] = [];
  on(h: Handler<T>): () => void {
    this.hs.push(h);
    return () => {
      this.hs = this.hs.filter((x) => x !== h);
    };
  }
  emit(x: T): void {
    for (const h of this.hs) h(x);
  }
}

export class DataLayer {
  route = "all_futures";
  top = new PatchState<Record>();
  alerts = new PatchState<AlertRecord>();
  twap = new PatchState<any>();
  ticks: Tick[] = [];
  densities: DensityItem[] = [];
  bots: BotItem[] = [];
  news: FeedMessage[] = [];
  onchain: FeedMessage[] = [];
  listings: FeedMessage[] = [];

  onTop = new Emitter<{ changed: string[]; deleted: string[]; full: boolean }>();
  onAlerts = new Emitter<{ changed: string[]; full: boolean }>();
  onTick = new Emitter<Tick>();
  onDensity = new Emitter<DensityItem>();
  onDensityUpdate = new Emitter<DensityItem>();
  onBot = new Emitter<BotItem>();
  onTwap = new Emitter<void>();
  onFeed = new Emitter<{ kind: "news" | "onchain" | "listings"; item: FeedMessage }>();
  onChart = new Emitter<{ key: string; timeframe: string; closed: any; partial: any }>();
  onStatus = new Emitter<void>();

  private sockets: { [name: string]: ReconnectingWs } = {};
  private chartKeys = new Set<string>();
  private chartSocket: ReconnectingWs | null = null;
  topLimit = 50;

  private sourceQuery(): string {
    const p = store.profile.prefs;
    return `source_futures=${encodeURIComponent(p.sources_futures.join(","))}&source_spot=${encodeURIComponent(p.sources_spot.join(","))}`;
  }

  private scope(): string {
    const r = this.route;
    if (r === "all_futures") return "market_scope=futures&exclude_stocks=1";
    if (r === "all_spots") return "market_scope=spot&exclude_stocks=1";
    if (r === "all_stocks") return "market_scope=all";
    return "market_scope=all";
  }

  full = false;

  start(route: string, opts: { top?: boolean; alerts?: boolean; ticks?: boolean; feeds?: boolean; twap?: boolean; full?: boolean; minFilters?: { [k: string]: number } } = {}): void {
    this.stop();
    this.route = route;
    const o = { top: true, alerts: true, ticks: true, feeds: true, twap: true, full: false, ...opts };
    this.full = o.full;
    const src = this.sourceQuery();
    if (o.top) {
      const topUrl = o.full ? `/ws/v3?${this.scope()}&${src}&sort=volume24h&dir=desc&route=${route}` : `/ws/all/v1?${this.scope()}&${src}&limit=${this.topLimit}&sort=totalVolume&dir=desc&route=${route}`;
      this.sockets.top = new ReconnectingWs(topUrl, {
        pingEvery: 25000,
        onOpen: (ws) => ws.send({ type: "subscribe", pinned_keys: Object.entries(store.profile.pairs).filter(([, s]) => s.pinned).map(([k]) => k) }),
        onMessage: (m) => {
          if (!this.top.apply(m)) this.sockets.top.stop(), this.sockets.top.start();
          this.onStatus.emit();
        },
      });
      this.top.listeners = [(changed, deleted, full) => this.onTop.emit({ changed, deleted, full })];
      this.sockets.top.start();
    }
    if (o.alerts) {
      const mins = Object.entries(o.minFilters ?? {}).map(([k, v]) => `&${k}=${v}`).join("");
      this.sockets.alerts = new ReconnectingWs(`/ws/all/alerts/v1?${this.scope()}&${src}&limit=20000${mins}&frame_encoding=gzip&route=${route}`, {
        pingEvery: 25000,
        onMessage: (m) => {
          if (!this.alerts.apply(m)) this.sockets.alerts.stop(), this.sockets.alerts.start();
        },
      });
      this.alerts.listeners = [(changed, _d, full) => this.onAlerts.emit({ changed, full })];
      this.sockets.alerts.start();
    }
    if (o.ticks) {
      this.sockets.tick = new ReconnectingWs(`/ws/tick?${src}&route=${route}`, {
        pingEvery: 25000,
        onMessage: (m) => {
          if (m && m.pair) {
            this.ticks.push(m as Tick);
            if (this.ticks.length > 5000) this.ticks.splice(0, 1000);
            this.onTick.emit(m as Tick);
          }
        },
      });
      this.sockets.tick.start();
    }
    if (o.twap) {
      this.sockets.twap = new ReconnectingWs(`/ws/active_twaps?route=${route}`, {
        pingEvery: 25000,
        onMessage: (m) => {
          if (!this.twap.apply(m)) this.sockets.twap.stop(), this.sockets.twap.start();
          this.onTwap.emit();
        },
      });
      this.sockets.twap.start();
    }
    if (o.feeds) {
      this.sockets.density = new ReconnectingWs(`/ws/density/feed?route=${route}`, {
        pingEvery: 25000,
        onMessage: (m) => {
          if (m.type === "history") {
            this.densities = m.items as DensityItem[];
            this.onDensityUpdate.emit(null as any);
          } else if (m.event === "density.message") {
            this.densities.push(m as DensityItem);
            if (this.densities.length > 400) this.densities.shift();
            this.onDensity.emit(m as DensityItem);
          } else if (m.event === "density.update") {
            const i = this.densities.findIndex((d) => d.id === m.id);
            if (i >= 0) this.densities[i] = { ...this.densities[i], ...m, event: "density.message" };
            this.onDensityUpdate.emit(m as DensityItem);
          }
        },
      });
      this.sockets.density.start();
      this.sockets.bots = new ReconnectingWs(`/ws/summary_bots?route=${route}`, {
        pingEvery: 25000,
        onMessage: (m) => {
          if (m.type === "history") {
            this.bots = m.items as BotItem[];
            this.onBot.emit(null as any);
          } else if (m.text) {
            this.bots.push(m as BotItem);
            if (this.bots.length > 400) this.bots.shift();
            this.onBot.emit(m as BotItem);
          }
        },
      });
      this.sockets.bots.start();
      for (const kind of ["news", "onchain", "listings"] as const) {
        this.sockets[kind] = new ReconnectingWs(`/ws/summary_${kind}?route=${route}`, {
          pingEvery: 25000,
          onMessage: (m) => {
            if (m.type === "history") {
              this[kind] = m.items as FeedMessage[];
              this.onFeed.emit({ kind, item: null as any });
            } else if (m.event === "telegram.message" || m.text) {
              this[kind].push(m as FeedMessage);
              if (this[kind].length > 300) this[kind].shift();
              this.onFeed.emit({ kind, item: m as FeedMessage });
            }
          },
        });
        this.sockets[kind].start();
      }
    }
  }

  stop(): void {
    for (const s of Object.values(this.sockets)) s.stop();
    this.sockets = {};
    this.chartSocket?.stop();
    this.chartSocket = null;
    this.chartKeys.clear();
  }

  connected(): { [name: string]: boolean } {
    return Object.fromEntries(Object.entries(this.sockets).map(([k, v]) => [k, v.connected]));
  }

  // ------------------------------------------------------------- live charts
  subscribeChart(key: string): void {
    this.chartKeys.add(key);
    this.syncCharts();
  }

  unsubscribeChart(key: string): void {
    this.chartKeys.delete(key);
    this.syncCharts();
  }

  private syncCharts(): void {
    if (!this.chartSocket) {
      this.chartSocket = new ReconnectingWs(`/ws/graph_charts/v1?route=${this.route}`, {
        pingEvery: 25000,
        pingPayload: "ping",
        onOpen: () => this.sendChartSubscribe(),
        onMessage: (m) => {
          if (m.type === "chart") this.onChart.emit(m);
        },
      });
      this.chartSocket.start();
    } else {
      this.sendChartSubscribe();
    }
  }

  private sendChartSubscribe(): void {
    const items = [...this.chartKeys].map((key) => {
      const [exchange, pair, market_type] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1, key.lastIndexOf(":")), key.slice(key.lastIndexOf(":") + 1)];
      const rec = this.top.data.get(key) ?? this.alerts.data.get(key);
      return { key, timeframe: "1m", exchange, pair, market_type, source_route_id: rec?.source_route_id ?? `${exchange}_${market_type}` };
    });
    this.chartSocket?.send({ type: "subscribe", items });
  }
}

export const data = new DataLayer();
