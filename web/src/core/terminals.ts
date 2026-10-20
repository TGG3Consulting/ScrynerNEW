// Local terminal bridges (auto-open DOM): MetaScalp, Colibri, Vataga, Tiger, Scalpee.
// Each terminal exposes an HTTP API on localhost; the browser calls it directly.

import { store } from "./store";

export type TerminalName = "metascalp" | "colibri" | "vataga" | "tiger" | "scalpee";

export const TERMINALS: { [k in TerminalName]: { title: string; portStart: number; portRange: number } } = {
  metascalp: { title: "MetaScalp", portStart: 17845, portRange: 10 },
  colibri: { title: "Colibri", portStart: 18845, portRange: 10 },
  vataga: { title: "Vataga", portStart: 5005, portRange: 1 },
  tiger: { title: "Tiger", portStart: 7819, portRange: 1 },
  scalpee: { title: "Scalpee", portStart: 7356, portRange: 1 },
};

export interface OpenTarget {
  exchange: string;
  pair: string;
  market_type: string;
}

function log(name: TerminalName, line: string): void {
  store.update((p) => {
    const t = p.terminals[name];
    t.log.push(`${new Date().toISOString().slice(11, 19)} ${line}`);
    if (t.log.length > 200) t.log.splice(0, t.log.length - 200);
  });
}

async function ping(port: number, path = "/ping"): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const to = window.setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(`http://127.0.0.1:${port}${path}`, { mode: "cors", signal: ctrl.signal });
    window.clearTimeout(to);
    return r.ok;
  } catch {
    return false;
  }
}

export async function checkTerminal(name: TerminalName): Promise<boolean> {
  const t = store.profile.terminals[name];
  const spec = TERMINALS[name];
  for (let p = t.port || spec.portStart; p < (t.port || spec.portStart) + spec.portRange; p++) {
    if (await ping(p, name === "vataga" ? "/" : "/ping")) {
      if (p !== t.port) store.update((pr) => (pr.terminals[name].port = p));
      log(name, `ping ok :${p}`);
      return true;
    }
  }
  log(name, "ping failed");
  return false;
}

const SYMBOL_FORMAT: { [k in TerminalName]: (t: OpenTarget) => string } = {
  metascalp: (t) => `${t.exchange.toUpperCase()}:${t.pair.replace(/[\/_-]/g, "")}${t.market_type === "futures" ? ".P" : ""}`,
  colibri: (t) => `${t.exchange}:${t.pair.replace(/[\/_-]/g, "")}`,
  vataga: (t) => t.pair.replace(/[\/_-]/g, ""),
  tiger: (t) => `${t.exchange.toUpperCase()}_${t.market_type === "futures" ? "F" : "S"}_${t.pair.replace(/[\/_-]/g, "")}`,
  scalpee: (t) => `${t.exchange}:${t.market_type}:${t.pair.replace(/[\/_-]/g, "")}`,
};

export async function openInTerminal(name: TerminalName, target: OpenTarget, group?: string): Promise<boolean> {
  const t = store.profile.terminals[name];
  if (!t.enabled) return false;
  const port = t.port || TERMINALS[name].portStart;
  const symbol = SYMBOL_FORMAT[name](target);
  try {
    let r: Response;
    if (name === "vataga") {
      const partner = t.targets[0] || "scryner";
      r = await fetch(`http://127.0.0.1:${port}/api/open/${encodeURIComponent(partner)}/${encodeURIComponent(target.exchange)}/${target.market_type}/${encodeURIComponent(symbol)}`);
    } else if (name === "metascalp" || name === "colibri") {
      r = await fetch(`http://127.0.0.1:${port}/api/change-ticker`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker: symbol, exchange: target.exchange, market: target.market_type, binding: group ?? t.targets[0] ?? "" }) });
    } else {
      r = await fetch(`http://127.0.0.1:${port}/open`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol, exchange: target.exchange, market: target.market_type, group: group ?? t.targets[0] ?? "" }) });
    }
    log(name, `open ${symbol} → ${r.status}`);
    return r.ok;
  } catch (e) {
    log(name, `open ${symbol} failed: ${(e as Error).message}`);
    return false;
  }
}

/** Round-robin over configured link groups of every enabled terminal. */
export async function autoOpen(target: OpenTarget, source: string): Promise<void> {
  const seq = store.profile.uiState.alert_rotation_sequence;
  let opened = false;
  for (const name of Object.keys(TERMINALS) as TerminalName[]) {
    const t = store.profile.terminals[name];
    if (!t.enabled) continue;
    const group = t.targets.length ? t.targets[seq % t.targets.length] : undefined;
    if (await openInTerminal(name, target, group)) opened = true;
  }
  if (opened) store.update((p) => (p.uiState.alert_rotation_sequence = seq + 1));
  void source;
}

export async function copyTicker(target: OpenTarget): Promise<void> {
  try {
    await navigator.clipboard.writeText(target.pair.replace(/[\/_-]/g, ""));
  } catch {
    /* clipboard denied */
  }
}
