// Shared types mirroring the server records.

export type MarketType = "futures" | "spot";

export interface Record {
  exchange: string;
  pair: string;
  display_symbol: string | null;
  display_pair: string;
  display_name: string | null;
  asset_class: "crypto" | "stock";
  stock_scope: boolean;
  market_type: MarketType;
  buy_sum: number;
  sell_sum: number;
  tpm: number;
  oi_percentage: number | null;
  oi2_percentage: number | null;
  big_trade_side: "buy" | "sell" | null;
  big_trade_volume: number;
  price_change_pct: number;
  oi_vol: string;
  last_price: number | null;
  fair_price: number | null;
  last_price_ts: number | null;
  spike_ratio: number | null;
  buy_volume_1s: number;
  sell_volume_1s: number;
  quote_currency: string;
  volume_currency: string;
  tick_threshold: number;
  buy_sum_5m: number;
  sell_sum_5m: number;
  dvol5_pct: number | null;
  dtpl5_pct: number | null;
  price_change_24h_pct: number | null;
  range5_pct: number | null;
  natr5: number | null;
  graph_metrics: (number | null)[];
  graph_activity: (number | null)[];
  simple_24h_metrics: (number | null)[];
  source_route_id: string;
  source_title: string;
  volume_24h: number | null;
}

export type AlertRecord = Pick<Record, "exchange" | "pair" | "display_pair" | "display_symbol" | "display_name" | "source_route_id" | "market_type" | "quote_currency" | "buy_sum_5m" | "sell_sum_5m" | "buy_sum" | "sell_sum" | "oi_percentage" | "oi2_percentage" | "oi_vol" | "spike_ratio" | "dvol5_pct" | "natr5" | "price_change_pct" | "tpm" | "volume_24h" | "stock_scope">;

export interface Tick {
  exchange: string;
  pair: string;
  display_pair: string;
  market_type: MarketType;
  buy: number;
  sell: number;
  timestamp: number;
  price: number;
  price_open: number;
  price_high: number;
  price_low: number;
  price_ts: number;
  price_move_pct: number;
  price_range_pct: number;
  volatility_oc: number;
  volatility_hl: number;
  window_sec: number;
  tick_threshold: number;
  tick_threshold_base: number;
  tick_threshold_multiplier: number;
  volume_24h: number | null;
  volume_currency: string;
  stock_scope?: boolean;
  stock_symbol?: string;
  display_symbol?: string;
  display_name?: string;
}

export interface Bar {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
  buy_quote: number;
  sell_quote: number;
  total_quote: number;
  trades_count: number;
  oi_close: number | null;
  oi_open?: number | null;
  oi_high?: number | null;
  oi_low?: number | null;
}

export interface DensityItem {
  event: string;
  id: string;
  captured_at: number;
  exchange: string;
  exchange_label: string;
  source_route_id: string;
  market_type: MarketType;
  pair: string;
  display_pair: string;
  display_symbol: string;
  side: "bid" | "ask";
  price: number;
  size_usd: number;
  distance_pct: number;
  is_iceberg: boolean;
  text: string;
  metric_line: string;
  impact?: { [window: string]: { label: string; move_pct: number } };
  removed_at?: number | null;
}

export interface BotItem {
  captured_at: string;
  text: string;
  id?: string;
  key?: string;
  pattern?: string;
  side?: string;
  rating?: number;
  exchange?: string;
  market_type?: MarketType;
}

export interface TwapItem {
  kind: "twap";
  id: string;
  hash: string;
  time_ms: number;
  expires_ms: number;
  duration_min: number;
  user: string;
  market: "spot" | "perp";
  side: "BUY" | "SELL";
  token: string;
  display_pair: string;
  chart_key: string;
  amount: number;
  reduce_only: boolean;
  randomize: boolean;
  exchange: string;
  market_type: MarketType;
}

export interface FeedMessage {
  event: string;
  captured_at: string;
  message: { id: number; date: string | null; text: string; raw_text: string; views: number; media: { has_media: boolean; visual: boolean } };
  chat: { id: number | null; title: string | null; username: string | null };
  sender: { name: string | null; username: string | null };
  text?: string;
}

export function keyOf(r: { exchange: string; pair: string; market_type: MarketType }): string {
  return `${r.exchange}:${r.pair}:${r.market_type}`;
}

export function splitKey(key: string): { exchange: string; pair: string; market_type: MarketType } {
  const i = key.indexOf(":");
  const j = key.lastIndexOf(":");
  return { exchange: key.slice(0, i), pair: key.slice(i + 1, j), market_type: key.slice(j + 1) as MarketType };
}
