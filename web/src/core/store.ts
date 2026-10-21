// Settings store: localStorage + server sync (user settings per route), with change events.

import { apiGet, apiPut } from "./api";
import { splitKey } from "./types";

export interface PairSettings {
  buy_threshold: number;
  sell_threshold: number;
  oi_threshold: number;
  tpm_threshold: number;
  spike_threshold: number;
  dvol_threshold: number;
  natr5_threshold: number;
  alert_cooldown_minutes: number;
  pinned: boolean;
  hidden: boolean;
  hidden_until: number;
  require_buy_or_sell: boolean;
  require_oi1: boolean;
  require_tpm: boolean;
  require_spike: boolean;
  require_dvol: boolean;
  require_natr5: boolean;
}

export const HARD_DEFAULT_PAIR_SETTINGS: PairSettings = {
  buy_threshold: 0, sell_threshold: 0, oi_threshold: 0, tpm_threshold: 0, spike_threshold: 0, dvol_threshold: 0, natr5_threshold: 0,
  alert_cooldown_minutes: 0, pinned: false, hidden: false, hidden_until: 0,
  require_buy_or_sell: false, require_oi1: false, require_tpm: false, require_spike: false, require_dvol: false, require_natr5: false,
};

// Out-of-the-box alert thresholds per source route — reverse-engineered from the reference
// service's own defaults (defaultPairSettings_v1). Alerts resolve by the PAIR's exchange route
// (see pairSettings), so these make alerts fire immediately, exactly like the original.
export const DEFAULT_ROUTE_SETTINGS: { [route: string]: Partial<PairSettings> } = {
  aster_futures: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  aster_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  binance_alpha: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  binance_futures: { buy_threshold: 200000, sell_threshold: 200000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_oi1: true, require_dvol: true },
  binance_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  bitget_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  bitget_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  bybit_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_oi1: true, require_dvol: true },
  bybit_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  gateio_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  gateio_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  hyperliquid_futures: { buy_threshold: 100000, sell_threshold: 100000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  kraken_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  kraken_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  kucoin_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 5, require_buy_or_sell: true, require_oi1: true, require_dvol: true },
  kucoin_spot: { buy_threshold: 50000, sell_threshold: 50000, natr5_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  lighter_futures: { buy_threshold: 50000, sell_threshold: 50000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_oi1: true, require_dvol: true },
  mexc_futures: { buy_threshold: 200000, sell_threshold: 200000, oi_threshold: 2, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  mexc_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  okx_futures: { buy_threshold: 100000, sell_threshold: 100000, oi_threshold: 5, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
  okx_spot: { buy_threshold: 50000, sell_threshold: 50000, dvol_threshold: 6, require_buy_or_sell: true, require_dvol: true },
};

export interface TelegramSettings {
  bot_token: string;
  chat_id: string;
  note: string;
  sources: { alerts: boolean; bots: boolean; density: boolean; twap: boolean; ticks: boolean; team: boolean };
  mobile: { alerts: boolean; bots: boolean; density: boolean; twap: boolean; ticks: boolean; team: boolean; charts: boolean };
  auto_open_meta: boolean;
}

export interface TickSettings {
  multiplier: number;
  always_play_volume: number;
  always_play_volatility: number;
  sound: string;
  volume: number;
  tts: boolean;
  exchanges: { [exchange: string]: boolean };
  custom: Array<{ pair: string; exchange: string; market_type: string; min_usd: number; min_pct: number; sec: number }>;
}

export interface BotSettings {
  patterns: { P1: boolean; C1: boolean; S1: boolean };
  min_tick_volume: number;
  min_tick_move: number;
  hide_r0: boolean;
  exchanges: { [exchange: string]: boolean };
}

export interface DensitySettings {
  exchanges: { [exchange: string]: { enabled: boolean; min_usd: number } };
}

export interface Profile {
  prefs: {
    alert_cooldown_minutes: number;
    market_route: string;
    sources_futures: string[];
    sources_spot: string[];
    grid: { cols: number; rows: number };
    columns: { [col: string]: boolean };
    font_scale: number;
    utc_offset_min: number;
    sound_volume: number;
    theme: "dark" | "light";
    infoblock: { news: boolean; onchain: boolean; listings: boolean };
    markers: { [m: string]: boolean };
    layout: string[];
    chart_timeframe: string;
  };
  pairs: { [key: string]: Partial<PairSettings> };
  route_defaults: { [route: string]: Partial<PairSettings> };
  telegram: TelegramSettings;
  ticks: TickSettings;
  bots: BotSettings;
  density: DensitySettings;
  bans: { [section: string]: { [key: string]: number } }; // key → until ts (0 = forever)
  favorites: string[];
  terminals: { [name: string]: { enabled: boolean; port: number; targets: string[]; log: string[] } };
  uiState: { alert_rotation_sequence: number };
}

export const ALL_EXCHANGES = ["binance", "binance_alpha", "bybit", "okx", "bitget", "gateio", "mexc", "kucoin", "htx", "kraken", "coinbase", "upbit", "hyperliquid", "aster", "apex", "lighter"];
export const FUTURES_SOURCES = ["apex", "aster", "binance", "bitget", "bybit", "gateio", "htx", "hyperliquid", "kraken", "kucoin", "lighter", "mexc", "okx"];
export const SPOT_SOURCES = ["aster", "binance", "binance_alpha", "bitget", "bybit", "coinbase", "gateio", "hyperliquid", "kraken", "kucoin", "mexc", "okx", "upbit"];

export const DEFAULT_COLUMNS: { [c: string]: boolean } = {
  // defaults matched to the reference (column_visible::all_futures)
  exchange: true, pinned: true, pair: true, volume24h: false, tpm: true, spike: false, dvol: true, natr5: false, lsSum: true, buySum: true, sellSum: true,
  oi: true, oi2: false, oi_vol: false, price: false, priceChange: false, settings: true,
};

function defaultProfile(): Profile {
  const exAll = (): { [k: string]: boolean } => Object.fromEntries(ALL_EXCHANGES.map((e) => [e, true]));
  return {
    prefs: {
      alert_cooldown_minutes: 15, market_route: "all_futures", sources_futures: [...FUTURES_SOURCES], sources_spot: [...SPOT_SOURCES],
      grid: { cols: 5, rows: 2 }, columns: { ...DEFAULT_COLUMNS }, font_scale: 100, utc_offset_min: 0, sound_volume: 0.6, theme: "dark",
      infoblock: { news: true, onchain: true, listings: true }, markers: { bots: true, density: true, twap: true, alerts: true, ticks: true, oi: true, volume: true, clusters: true },
      layout: ["alerts", "ticks", "bots", "density", "twap"], chart_timeframe: "1m",
    },
    pairs: {},
    route_defaults: { ...DEFAULT_ROUTE_SETTINGS },
    telegram: { bot_token: "", chat_id: "", note: "", sources: { alerts: true, bots: false, density: false, twap: false, ticks: false, team: false },
      mobile: { alerts: false, bots: false, density: false, twap: false, ticks: false, team: false, charts: false }, auto_open_meta: true },
    ticks: { multiplier: 1, always_play_volume: 0, always_play_volatility: 0, sound: "bum", volume: 0.6, tts: false, exchanges: exAll(), custom: [] },
    bots: { patterns: { P1: true, C1: true, S1: true }, min_tick_volume: 0, min_tick_move: 0, hide_r0: false, exchanges: exAll() },
    density: { exchanges: Object.fromEntries(ALL_EXCHANGES.map((e) => [e, { enabled: true, min_usd: 0 }])) },
    bans: { alerts: {}, ticks: {}, bots: {}, density: {}, twap: {} },
    favorites: [],
    terminals: {
      metascalp: { enabled: false, port: 17845, targets: [], log: [] }, colibri: { enabled: false, port: 18845, targets: [], log: [] },
      vataga: { enabled: false, port: 5005, targets: [], log: [] }, tiger: { enabled: false, port: 7819, targets: [], log: [] }, scalpee: { enabled: false, port: 7356, targets: [], log: [] },
    },
    uiState: { alert_rotation_sequence: 0 },
  };
}

function deepMerge<T>(base: T, over: any): T {
  if (over === null || typeof over !== "object" || Array.isArray(over)) return (over ?? base) as T;
  const out: any = { ...(base as any) };
  for (const [k, v] of Object.entries(over)) out[k] = typeof v === "object" && v !== null && !Array.isArray(v) && typeof out[k] === "object" ? deepMerge(out[k], v) : v;
  return out as T;
}

type Listener = (p: Profile) => void;

class Store {
  profile: Profile = defaultProfile();
  private listeners: Listener[] = [];
  private saveTimer: number | null = null;
  private serverTimer: number | null = null;
  private readonly LS_KEY = "scryner.profile.v1";

  load(): void {
    try {
      const raw = localStorage.getItem(this.LS_KEY);
      if (raw) this.profile = deepMerge(defaultProfile(), JSON.parse(raw));
    } catch {
      this.profile = defaultProfile();
    }
  }

  async syncFromServer(): Promise<void> {
    try {
      const r = await apiGet<{ payload: Profile | null }>("/api/settings/profile");
      if (r.payload) {
        this.profile = deepMerge(defaultProfile(), r.payload);
        this.persistLocal();
        this.emit();
      }
    } catch {
      /* offline or unauthenticated */
    }
  }

  update(fn: (p: Profile) => void): void {
    fn(this.profile);
    this.persistLocal();
    this.emit();
    if (this.serverTimer) window.clearTimeout(this.serverTimer);
    this.serverTimer = window.setTimeout(() => void apiPut("/api/settings/profile", this.profile).catch(() => undefined), 1500);
  }

  replace(p: Profile): void {
    this.profile = deepMerge(defaultProfile(), p);
    this.persistLocal();
    this.emit();
    void apiPut("/api/settings/profile", this.profile).catch(() => undefined);
  }

  reset(): void {
    this.replace(defaultProfile());
  }

  subscribe(fn: Listener): () => void {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((x) => x !== fn);
    };
  }

  pairSettings(key: string, route?: string): PairSettings {
    if (route) return { ...HARD_DEFAULT_PAIR_SETTINGS, ...(this.profile.route_defaults[route] ?? {}), ...(this.profile.pairs[key] ?? {}) };
    // Resolve by the pair's own exchange route (e.g. binance_futures), like the reference's
    // profileRouteIdForKey, with the viewed market route as a lower-priority baseline.
    const { exchange, market_type } = splitKey(key);
    const exRoute = `${exchange}_${market_type}`;
    const mr = this.profile.prefs.market_route;
    return {
      ...HARD_DEFAULT_PAIR_SETTINGS,
      ...(this.profile.route_defaults[mr] ?? {}),
      ...(this.profile.route_defaults[exRoute] ?? {}),
      ...(this.profile.pairs[key] ?? {}),
    };
  }

  isBanned(section: string, key: string): boolean {
    const until = this.profile.bans[section]?.[key];
    if (until === undefined) return false;
    if (until === 0) return true;
    if (until > Date.now() / 1000) return true;
    delete this.profile.bans[section][key];
    return false;
  }

  ban(section: string, key: string, seconds: number | 0): void {
    this.update((p) => {
      p.bans[section] = p.bans[section] ?? {};
      p.bans[section][key] = seconds === 0 ? 0 : Date.now() / 1000 + seconds;
    });
  }

  private persistLocal(): void {
    if (this.saveTimer) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => {
      try {
        localStorage.setItem(this.LS_KEY, JSON.stringify(this.profile));
      } catch {
        /* quota */
      }
    }, 200);
  }

  private emit(): void {
    for (const l of this.listeners) l(this.profile);
  }
}

export const store = new Store();
