// Alert evaluation (client-side, same semantics as the reference `evaluateAlertState`),
// cooldown bans and persistent alert history (IndexedDB).

import type { AlertRecord } from "./types";
import { store, type PairSettings } from "./store";
import { shortNum, pct } from "./format";

export interface AlertReason {
  code: string;
  label: string;
  value: number | null;
  threshold: number;
  suffix: string;
}

export interface AlertItem {
  id: string;
  key: string;
  timestamp: number;
  exchange: string;
  pair: string;
  display_pair: string;
  market_type: string;
  reasons: AlertReason[];
  buy_sum: number;
  sell_sum: number;
  oi_percentage: number | null;
  tpm: number;
  volume_24h: number | null;
  dvol5_pct: number | null;
  natr5: number | null;
  spike_ratio: number | null;
  attention?: boolean;
}

const thresholdExceeded = (thr: number, v: number | null): boolean => thr > 0 && v !== null && v !== undefined && v >= thr;

export function evaluateAlertState(key: string, stats: AlertRecord, settings?: PairSettings): { trigger: boolean; reasons: AlertReason[]; settings: PairSettings } {
  const s = settings ?? store.pairSettings(key);
  const buy = Number(stats.buy_sum || 0);
  const sell = Number(stats.sell_sum || 0);
  const tpm = Number(stats.tpm || 0);
  const isSpot = stats.market_type === "spot";
  const oi1 = isSpot ? null : Number(stats.oi_percentage ?? 0);
  const spike = stats.spike_ratio == null ? null : Number(stats.spike_ratio);
  const dvol = stats.dvol5_pct == null ? null : Number(stats.dvol5_pct);
  const natr5 = stats.natr5 == null ? null : Number(stats.natr5);

  const condBuy = thresholdExceeded(s.buy_threshold, buy);
  const condSell = thresholdExceeded(s.sell_threshold, sell);
  const condBs = condBuy || condSell;
  const condOi = !isSpot && thresholdExceeded(s.oi_threshold, oi1);
  const condTpm = thresholdExceeded(s.tpm_threshold, tpm);
  const condSpike = spike != null && thresholdExceeded(s.spike_threshold, spike);
  const condDvol = dvol != null && thresholdExceeded(s.dvol_threshold, dvol);
  const condNatr5 = natr5 != null && thresholdExceeded(s.natr5_threshold, natr5);

  const requires: boolean[] = [];
  if (s.require_buy_or_sell) requires.push(condBs);
  if (s.require_oi1) requires.push(condOi);
  if (s.require_tpm) requires.push(condTpm);
  if (s.require_spike) requires.push(condSpike);
  if (s.require_dvol) requires.push(condDvol);
  if (s.require_natr5) requires.push(condNatr5);

  let trigger = false;
  if (requires.length) trigger = requires.every(Boolean);
  else {
    const active: boolean[] = [];
    if (s.buy_threshold > 0 || s.sell_threshold > 0) active.push(condBs);
    if (s.oi_threshold > 0) active.push(condOi);
    if (s.tpm_threshold > 0) active.push(condTpm);
    if (s.spike_threshold > 0) active.push(condSpike);
    if (s.dvol_threshold > 0) active.push(condDvol);
    if (s.natr5_threshold > 0) active.push(condNatr5);
    trigger = active.some(Boolean);
  }

  const reasons: AlertReason[] = [];
  if (condBuy) reasons.push({ code: "buy", label: "Long", value: buy, threshold: s.buy_threshold, suffix: "" });
  if (condSell) reasons.push({ code: "sell", label: "Short", value: sell, threshold: s.sell_threshold, suffix: "" });
  if (condOi) reasons.push({ code: "oi1", label: "OI5m", value: oi1, threshold: s.oi_threshold, suffix: "%" });
  if (condTpm) reasons.push({ code: "tpm", label: "TPM", value: tpm, threshold: s.tpm_threshold, suffix: "" });
  if (condSpike) reasons.push({ code: "spike", label: "Spike", value: spike, threshold: s.spike_threshold, suffix: "x" });
  if (condDvol) reasons.push({ code: "dvol", label: "Dvol%", value: dvol, threshold: s.dvol_threshold, suffix: "%" });
  if (condNatr5) reasons.push({ code: "natr5", label: "Natr5", value: natr5, threshold: s.natr5_threshold, suffix: "%" });
  return { trigger, reasons, settings: s };
}

export function reasonSummary(reasons: AlertReason[], detailed = false): string {
  if (!reasons.length) return "No reasons";
  return reasons.map((r) => (detailed ? `${r.label} ${formatReasonValue(r.value, r.suffix)} (≥${r.threshold}${r.suffix})` : r.label)).join(", ");
}

export function formatReasonValue(v: number | null, suffix = ""): string {
  if (v === null || v === undefined) return "n/a";
  if (suffix === "%") return pct(v);
  if (suffix === "x") return `${v.toFixed(2)}x`;
  return shortNum(v);
}

export function alertTelegramText(item: AlertItem): string {
  const prefix = item.attention ? "Attention\n" : "";
  return `${prefix}[ALERT] ${item.exchange.toUpperCase()} ${item.market_type === "spot" ? "S" : "F"} ${item.display_pair}\n${reasonSummary(item.reasons, true)}\nLong ${shortNum(item.buy_sum)} | Short ${shortNum(item.sell_sum)} | OI5m ${item.oi_percentage == null ? "n/a" : pct(item.oi_percentage)} | TPM ${item.tpm}`;
}

/** Cooldown bans + IndexedDB-backed history. */
export class AlertEngine {
  private cooldownUntil = new Map<string, number>();
  history: AlertItem[] = [];
  listeners: Array<(item: AlertItem) => void> = [];
  private db: IDBDatabase | null = null;
  private route: string;

  constructor(route: string) {
    this.route = route;
  }

  async open(): Promise<void> {
    await new Promise<void>((resolve) => {
      const req = indexedDB.open("scryner-alerts", 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("alerts")) db.createObjectStore("alerts", { keyPath: "id" }).createIndex("ts", "timestamp");
      };
      req.onsuccess = () => {
        this.db = req.result;
        resolve();
      };
      req.onerror = () => resolve();
    });
    await this.loadHistory();
  }

  private async loadHistory(): Promise<void> {
    if (!this.db) return;
    const weekAgo = Date.now() - 7 * 86400_000;
    await new Promise<void>((resolve) => {
      const tx = this.db!.transaction("alerts", "readonly");
      const idx = tx.objectStore("alerts").index("ts");
      const items: AlertItem[] = [];
      idx.openCursor(IDBKeyRange.lowerBound(weekAgo)).onsuccess = (ev) => {
        const cur = (ev.target as IDBRequest<IDBCursorWithValue>).result;
        if (cur) {
          items.push(cur.value as AlertItem);
          cur.continue();
        } else {
          this.history = items.filter((i) => i.id.startsWith(`${this.route}|`)).slice(-2000);
          resolve();
        }
      };
      tx.onerror = () => resolve();
    });
  }

  private persist(item: AlertItem): void {
    if (!this.db) return;
    try {
      this.db.transaction("alerts", "readwrite").objectStore("alerts").put(item);
    } catch {
      /* ignore */
    }
  }

  clear(): void {
    this.history = [];
    if (this.db) this.db.transaction("alerts", "readwrite").objectStore("alerts").clear();
  }

  cooldownMs(key: string, settings: PairSettings): number {
    const pair = settings.alert_cooldown_minutes > 0 ? settings.alert_cooldown_minutes : store.profile.prefs.alert_cooldown_minutes;
    return Math.max(1, Math.min(1440, pair)) * 60_000;
  }

  /** Evaluate one record; returns the alert item if it fired. */
  consider(key: string, rec: AlertRecord): AlertItem | null {
    if (store.isBanned("alerts", key)) return null;
    const s = store.pairSettings(key);
    if (s.hidden && (s.hidden_until === 0 || s.hidden_until > Date.now() / 1000)) return null;
    const ev = evaluateAlertState(key, rec, s);
    if (!ev.trigger) return null;
    const now = Date.now();
    if ((this.cooldownUntil.get(key) ?? 0) > now) return null;
    this.cooldownUntil.set(key, now + this.cooldownMs(key, s));
    const item: AlertItem = {
      id: `${this.route}|${now}|${key}`, key, timestamp: now, exchange: rec.exchange, pair: rec.pair, display_pair: rec.display_pair || rec.pair,
      market_type: rec.market_type, reasons: ev.reasons, buy_sum: rec.buy_sum, sell_sum: rec.sell_sum, oi_percentage: rec.oi_percentage, tpm: rec.tpm,
      volume_24h: rec.volume_24h, dvol5_pct: rec.dvol5_pct, natr5: rec.natr5, spike_ratio: rec.spike_ratio,
      attention: ev.reasons.length >= 3,
    };
    this.history.push(item);
    if (this.history.length > 2000) this.history.shift();
    this.persist(item);
    for (const l of this.listeners) l(item);
    return item;
  }
}
