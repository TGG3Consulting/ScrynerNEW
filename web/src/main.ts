// App shell: top bar, routing between modes, data layer lifecycle.

import { apiGet, apiPost } from "./core/api";
import { AlertEngine } from "./core/alerts";
import { data } from "./core/data";
import { t } from "./core/i18n";
import { store } from "./core/store";
import { renderCabinet } from "./modes/cabinet";
import { advancedMode, favoritesMode, graphMode, simpleMode, tickMode, type ModeHandle } from "./modes/modes";
import { el, esc, modal, toast } from "./widgets/common";
import type { WidgetCtx } from "./widgets/feeds";
import { applyUiPrefs, openMenu, openSourcesPicker } from "./widgets/settings";

type Mode = "advanced" | "simple" | "graph" | "tick" | "favorites" | "cabinet";
const MODE_PATHS: { [k in Mode]: string } = { advanced: "/advanced", simple: "/simple", graph: "/graph", tick: "/spike", favorites: "/favorites", cabinet: "/cabinet" };

class App {
  root = document.getElementById("app")!;
  bar = el("header", "topbar");
  view = el("main", "view");
  mode: Mode = "advanced";
  handle: ModeHandle | null = null;
  alertEngine!: AlertEngine;
  status = el("span", "ws-status");
  version = "";

  async init(): Promise<void> {
    store.load();
    applyUiPrefs();
    await store.syncFromServer();
    this.root.append(this.bar, this.view);
    this.alertEngine = new AlertEngine(store.profile.prefs.market_route);
    await this.alertEngine.open();
    this.renderBar();
    this.route(location.pathname, new URLSearchParams(location.search).get("route") ?? store.profile.prefs.market_route);
    window.addEventListener("popstate", () => this.route(location.pathname, new URLSearchParams(location.search).get("route") ?? data.route));
    this.startDataLoop();
    this.presence();
    this.releaseWatch();
    window.setInterval(() => this.renderStatus(), 2000);
  }

  renderBar(): void {
    this.bar.innerHTML = "";
    const logo = el("a", "logo", "◉ SCRYNER");
    logo.href = "/advanced";
    const links = el("nav", "links");
    links.innerHTML = `<a href="/docs" target="_blank">${esc(t("quickstart"))}</a><a href="/cabinet" data-mode="cabinet">${esc(t("teams"))}</a>`;
    const right = el("div", "bar-right");
    const sound = el("button", "tool-btn", store.profile.prefs.sound_volume > 0 ? "🔊" : "🔇");
    sound.onclick = () => {
      store.update((p) => (p.prefs.sound_volume = p.prefs.sound_volume > 0 ? 0 : 0.6));
      sound.textContent = store.profile.prefs.sound_volume > 0 ? "🔊" : "🔇";
    };
    const theme = el("button", "tool-btn", "◐");
    theme.onclick = () => {
      store.update((p) => (p.prefs.theme = p.prefs.theme === "dark" ? "light" : "dark"));
      applyUiPrefs();
    };
    const sources = el("button", "tool-btn", "⚙ sources");
    sources.onclick = openSourcesPicker;
    const modeSel = el("select", "sel mode-sel");
    for (const m of ["advanced", "simple", "graph", "tick", "favorites"] as Mode[]) {
      const o = el("option", "", t(`mode.${m}`));
      o.value = m;
      modeSel.appendChild(o);
    }
    modeSel.value = this.mode === "cabinet" ? "advanced" : this.mode;
    modeSel.onchange = () => this.navigate(modeSel.value as Mode);
    const marketSel = el("select", "sel");
    for (const [v, l] of [["all_futures", "All Futures"], ["all_spots", "All Spot"], ["all_stocks", "All Stocks"], ["all_in_one", "All in one"]]) {
      const o = el("option", "", l);
      o.value = v;
      marketSel.appendChild(o);
    }
    marketSel.value = data.route || store.profile.prefs.market_route;
    marketSel.onchange = () => {
      store.update((p) => (p.prefs.market_route = marketSel.value));
      this.navigate(this.mode, marketSel.value);
    };
    const menu = el("button", "tool-btn menu-btn", `${esc(t("menu.title")).toUpperCase()} ☰`);
    menu.onclick = () => openMenu({ openCabinet: () => this.navigate("cabinet"), reportBug: () => this.reportBug() });
    right.append(this.status, sound, theme, sources, el("span", "lbl", t("mode.label")), modeSel, el("span", "lbl", t("market.label")), marketSel, menu);
    this.bar.append(logo, links, right);
    this.bar.querySelector('[data-mode="cabinet"]')!.addEventListener("click", (e) => {
      e.preventDefault();
      this.navigate("cabinet");
    });
  }

  renderStatus(): void {
    const c = data.connected();
    const n = Object.values(c).filter(Boolean).length;
    const total = Object.keys(c).length;
    this.status.textContent = `${t("ws")} ${n}/${total} · ${data.top.data.size}`;
    this.status.className = `ws-status ${n === total && total > 0 ? "ok" : "warn"}`;
  }

  navigate(mode: Mode, route?: string): void {
    const r = route ?? data.route ?? store.profile.prefs.market_route;
    history.pushState({}, "", `${MODE_PATHS[mode]}?route=${r}`);
    this.route(MODE_PATHS[mode], r);
  }

  route(path: string, route: string): void {
    const mode = (Object.entries(MODE_PATHS).find(([, p]) => path.startsWith(p))?.[0] as Mode | undefined) ?? "advanced";
    this.handle?.destroy();
    this.handle = null;
    this.mode = mode;
    this.renderBar();
    const full = mode === "simple";
    if (data.route !== route || data.full !== full || Object.keys(data.connected()).length === 0) {
      data.start(route, { minFilters: this.minFilters(), full });
      this.alertEngine = new AlertEngine(route);
      void this.alertEngine.open();
    }
    const ctx: WidgetCtx = { openChart: (k) => this.handle?.openChart(k), alertEngine: this.alertEngine };
    this.view.className = "view";
    if (mode === "cabinet") {
      void renderCabinet(this.view, () => this.navigate("advanced"));
      return;
    }
    const factory = { advanced: advancedMode, simple: simpleMode, graph: graphMode, tick: tickMode, favorites: favoritesMode }[mode];
    this.handle = factory(this.view, ctx);
    void apiPost("/api/auth/ping", { section: mode, path, visible_sections: [{ section: mode, path }], active: document.hasFocus(), reason: "load" }).catch(() => undefined);
  }

  /** Server-side pre-filter: minimum of all user thresholds (so no alert can be missed). */
  minFilters(): { [k: string]: number } {
    const all = [...Object.values(store.profile.route_defaults), ...Object.values(store.profile.pairs)];
    const take = (field: string): number => {
      const vals = all.map((s: any) => Number(s?.[field] ?? 0)).filter((v) => v > 0);
      return vals.length ? Math.min(...vals) : 0;
    };
    const out: { [k: string]: number } = {};
    const oi = take("oi_threshold");
    const dvol = take("dvol_threshold");
    const tpm = take("tpm_threshold");
    const spike = take("spike_threshold");
    const natr = take("natr5_threshold");
    if (oi) out.min_oi1 = oi;
    if (dvol) out.min_dvol5 = dvol;
    if (tpm) out.min_tpm = tpm;
    if (spike) out.min_spike = spike;
    if (natr) out.min_natr5 = natr;
    return out;
  }

  startDataLoop(): void {
    // evaluate alerts on every alerts patch
    data.onAlerts.on(({ changed }) => {
      for (const k of changed) {
        const rec = data.alerts.data.get(k);
        if (rec) this.alertEngine.consider(k, rec);
      }
    });
  }

  presence(): void {
    const send = (reason: string) => void apiPost("/api/auth/ping", { section: this.mode, path: location.pathname, visible_sections: [{ section: this.mode, path: location.pathname }], active: document.hasFocus() && !document.hidden, reason }).catch(() => undefined);
    document.addEventListener("visibilitychange", () => send(document.hidden ? "hidden" : "visible"));
    window.addEventListener("focus", () => send("focus"));
    window.setInterval(() => send("interval"), 60_000);
  }

  releaseWatch(): void {
    const check = async () => {
      try {
        const r = await apiGet<{ version: string; poll_ms: number; speech_text: string }>("/api/client_release", { route: data.route, cv: this.version });
        if (!this.version) this.version = r.version;
        else if (r.version !== this.version) {
          toast("New version, reloading…");
          window.setTimeout(() => location.reload(), 1500);
        }
        if (r.speech_text) {
          const { speak } = await import("./core/notify");
          speak(r.speech_text);
        }
        window.setTimeout(check, r.poll_ms || 15000);
      } catch {
        window.setTimeout(check, 15000);
      }
    };
    void check();
  }

  reportBug(): void {
    const box = el("div", "panel");
    const ta = el("textarea");
    ta.rows = 6;
    ta.placeholder = "Что случилось?";
    box.append(ta);
    const btn = el("button", "btn primary", "Отправить");
    btn.onclick = async () => {
      await apiPost("/api/v1/feedback/reports", { kind: "bug", text: ta.value, mode: this.mode, route: data.route, ws: data.connected(), ua: navigator.userAgent, profile: store.profile.prefs });
      toast("Отправлено");
      overlay.remove();
    };
    box.append(btn);
    const overlay = modal(t("menu.report"), box);
  }
}

void new App().init();
