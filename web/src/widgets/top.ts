// TOP 50 table (Advanced) and the full Simple-mode table.

import { data } from "../core/data";
import { store, DEFAULT_COLUMNS } from "../core/store";
import { t } from "../core/i18n";
import { exchangeBadge, pct, price, shortNum } from "../core/format";
import type { Record } from "../core/types";
import { el, esc, widgetFrame, type WidgetFrame } from "./common";
import type { WidgetCtx } from "./feeds";
import { openPairSettings } from "./settings";

interface Col {
  id: string;
  label: string;
  value: (r: Record) => number | string | null;
  render?: (r: Record) => string;
  cls?: (r: Record) => string;
}

export const COLUMNS: Col[] = [
  { id: "exchange", label: "col.exchange", value: (r) => r.exchange, render: (r) => `<span class="badge">${esc(exchangeBadge(r.exchange, r.market_type))}</span>` },
  { id: "pinned", label: "col.pin", value: () => "", render: () => `<span class="pin">☆</span>` },
  { id: "pair", label: "col.symbol", value: (r) => r.display_pair, render: (r) => `<span class="sym">${esc(r.display_pair)}</span>` },
  { id: "volume24h", label: "col.vol1d", value: (r) => r.volume_24h, render: (r) => shortNum(r.volume_24h) },
  { id: "tpm", label: "col.tpm", value: (r) => r.tpm, render: (r) => String(r.tpm) },
  { id: "spike", label: "col.spike", value: (r) => r.spike_ratio, render: (r) => (r.spike_ratio == null ? "—" : `${r.spike_ratio.toFixed(2)}x`), cls: (r) => ((r.spike_ratio ?? 0) >= 3 ? "hot" : "") },
  { id: "dvol", label: "col.dvol", value: (r) => r.dvol5_pct, render: (r) => pct(r.dvol5_pct, 2, false), cls: (r) => ((r.dvol5_pct ?? 0) >= 5 ? "hot" : "") },
  { id: "natr5", label: "col.natr5", value: (r) => r.natr5, render: (r) => pct(r.natr5, 2, false) },
  { id: "lsSum", label: "col.ls", value: (r) => r.buy_sum + r.sell_sum, render: (r) => `<span class="up">${shortNum(r.buy_sum)}</span>|<span class="dn">${shortNum(r.sell_sum)}</span>` },
  { id: "buySum", label: "col.long", value: (r) => r.buy_sum, render: (r) => shortNum(r.buy_sum) },
  { id: "sellSum", label: "col.short", value: (r) => r.sell_sum, render: (r) => shortNum(r.sell_sum) },
  { id: "oi", label: "col.oi5m", value: (r) => r.oi_percentage, render: (r) => pct(r.oi_percentage), cls: (r) => ((r.oi_percentage ?? 0) > 0 ? "up" : (r.oi_percentage ?? 0) < 0 ? "dn" : "") },
  { id: "oi2", label: "col.oi1d", value: (r) => r.oi2_percentage, render: (r) => pct(r.oi2_percentage) },
  { id: "oi_vol", label: "col.oivol", value: (r) => r.oi_vol, render: (r) => esc(r.oi_vol) },
  { id: "price", label: "col.price", value: (r) => r.last_price, render: (r) => price(r.last_price) },
  { id: "priceChange", label: "col.change", value: (r) => r.price_change_pct, render: (r) => pct(r.price_change_pct), cls: (r) => (r.price_change_pct > 0 ? "up" : r.price_change_pct < 0 ? "dn" : "") },
  { id: "settings", label: "col.settings", value: () => "", render: () => `<button class="mini gear">⚙</button>` },
];

export function topWidget(ctx: WidgetCtx, opts: { full?: boolean; title?: string } = {}): WidgetFrame {
  const search = el("input", "search");
  search.placeholder = t("search");
  const collapse = el("button", "tool-btn", opts.full ? "" : t("collapse"));
  let collapsed = false;
  collapse.onclick = () => {
    collapsed = !collapsed;
    render();
  };
  const f = widgetFrame(opts.title ?? t("w.top"), { extra: opts.full ? [search] : [search, collapse] });
  const table = el("table", "tbl top");
  f.body.appendChild(table);
  let sortId = opts.full ? "volume24h" : "lsSum";
  let sortDesc = true;
  const visible = () => COLUMNS.filter((c) => (store.profile.prefs.columns[c.id] ?? DEFAULT_COLUMNS[c.id]) !== false);

  const render = () => {
    const cols = visible();
    const q = search.value.trim().toUpperCase();
    const col = COLUMNS.find((c) => c.id === sortId)!;
    let rows = [...data.top.data.entries()].filter(([, r]) => !q || r.display_pair.toUpperCase().includes(q));
    rows = rows.filter(([k]) => !store.pairSettings(k).hidden);
    rows.sort((a, b) => {
      const pa = store.pairSettings(a[0]).pinned ? 1 : 0;
      const pb = store.pairSettings(b[0]).pinned ? 1 : 0;
      if (pa !== pb) return pb - pa;
      const va = Number(col.value(a[1]) ?? -Infinity);
      const vb = Number(col.value(b[1]) ?? -Infinity);
      return sortDesc ? vb - va : va - vb;
    });
    if (collapsed) rows = rows.slice(0, 10);
    table.innerHTML = `<thead><tr>${cols.map((c) => `<th data-col="${c.id}" class="${c.id === sortId ? (sortDesc ? "sort-desc" : "sort-asc") : ""}">${esc(t(c.label))}</th>`).join("")}</tr></thead><tbody>${rows
      .map(([k, r]) => `<tr data-key="${esc(k)}" class="${store.pairSettings(k).pinned ? "pinned" : ""}">${cols.map((c) => `<td class="${c.cls?.(r) ?? ""}">${c.render ? c.render(r) : esc(c.value(r))}</td>`).join("")}</tr>`)
      .join("")}</tbody>`;
  };
  table.addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    const th = target.closest("th");
    if (th) {
      const id = th.dataset.col!;
      if (id === sortId) sortDesc = !sortDesc;
      else {
        sortId = id;
        sortDesc = true;
      }
      render();
      return;
    }
    const tr = target.closest("tr");
    const key = tr?.dataset.key;
    if (!key) return;
    if (target.classList.contains("pin")) {
      store.update((p) => (p.pairs[key] = { ...(p.pairs[key] ?? {}), pinned: !store.pairSettings(key).pinned }));
      return;
    }
    if (target.classList.contains("gear")) {
      openPairSettings(key, data.top.data.get(key));
      return;
    }
    ctx.openChart(key);
  });
  search.oninput = render;
  let timer: number | null = null;
  const schedule = () => {
    if (timer) return;
    timer = window.setTimeout(() => {
      timer = null;
      render();
    }, 250);
  };
  data.onTop.on(schedule);
  store.subscribe(schedule);
  render();
  return f;
}
