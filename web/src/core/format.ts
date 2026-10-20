// Number / time formatting used across widgets.

export function shortNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  const f = (x: number) => x.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  if (a >= 1e9) return `${sign}${f(a / 1e9)}b`;
  if (a >= 1e6) return `${sign}${f(a / 1e6)}m`;
  if (a >= 1e3) return `${sign}${f(a / 1e3)}k`;
  if (a >= 100) return `${sign}${a.toFixed(0)}`;
  return `${sign}${f(a)}`;
}

export function usd(v: number | null | undefined): string {
  const s = shortNum(v);
  return s === "—" ? s : `$${s}`;
}

export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  if (v >= 1000) return v.toFixed(1);
  if (v >= 1) return v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return v.toPrecision(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function hhmmss(ts: number, offsetMin = 0): string {
  const d = new Date((ts + offsetMin * 60) * 1000);
  return d.toISOString().slice(11, 19);
}

export function mmss(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(14, 19);
}

export function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d${Math.floor((s % 86400) / 3600)}h`;
}

export function duration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d${h}h`;
  if (h) return `${h}h${m}m`;
  return `${m}m${s % 60}s`;
}

export const EXCHANGE_CODES: { [k: string]: string } = {
  binance: "BN", binance_alpha: "BA", bybit: "BB", okx: "OKX", bitget: "BG", gateio: "GT", mexc: "MX", kucoin: "KC", htx: "HTX",
  kraken: "KR", coinbase: "CB", upbit: "UP", hyperliquid: "HL", aster: "AST", apex: "APX", lighter: "LT",
};

export function exchangeBadge(exchange: string, marketType: string): string {
  return `${EXCHANGE_CODES[exchange] ?? exchange.slice(0, 3).toUpperCase()} ${marketType === "spot" ? "S" : "F"}`;
}

export function tvSymbol(exchange: string, pair: string, marketType: string): string {
  const suffix = marketType === "spot" ? (exchange === "binance_alpha" ? ".A" : "") : ".P";
  return `${exchange.replace("_alpha", "").toUpperCase()}:${pair.replace(/[\/_-]/g, "")}${suffix}`;
}
