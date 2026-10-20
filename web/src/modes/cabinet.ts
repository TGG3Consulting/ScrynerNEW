// Personal cabinet page.

import { apiGet, apiPost } from "../core/api";
import { t } from "../core/i18n";
import { button, el, esc, field, toast } from "../widgets/common";

interface Me {
  user_id: string;
  nickname: string;
  role: string;
  plan: string;
  plan_until: number | null;
  plan_active: boolean;
  modes: string[];
  referral_code: string;
  referral_link: string;
  referral: { invited: number; paid: number; payments: number; bonus: number; clicks_all: number; clicks_day: number; registrations_all: number; registrations_day: number };
  created_at: number;
  telegram_username: string | null;
  bonus_usdt: number;
}

export async function renderCabinet(root: HTMLElement, goHome: () => void): Promise<void> {
  root.innerHTML = `<div class="cabinet"><h1>${esc(t("cabinet.title"))}</h1><p class="sub">${esc(t("cabinet.sub"))}</p><div class="cabinet-grid"></div></div>`;
  const grid = root.querySelector(".cabinet-grid")!;
  let me: Me;
  try {
    me = await apiGet<Me>("/api/auth/me");
  } catch (e) {
    grid.innerHTML = `<div class="card">${esc(String((e as Error).message))}</div>`;
    return;
  }
  const date = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleDateString() : "—");
  const access = el("div", "cab-card");
  access.innerHTML = `<h3>${esc(t("cabinet.access"))}</h3><div class="cab-row"><div class="shield ${me.plan_active ? "ok" : ""}">🛡</div><div>
  <div class="k">${esc(t("cabinet.status"))}</div><div class="v ${me.plan_active ? "up" : "dn"}">● ${esc(me.plan_active ? t("cabinet.active") : t("cabinet.inactive"))}</div>
  <div class="k">${esc(t("cabinet.plan"))}</div><div class="v">${esc(me.plan.toUpperCase())}</div>
  <div class="k">${esc(t("cabinet.until"))}</div><div class="v">${esc(date(me.plan_until))}</div></div>
  <div><div class="k">${esc(t("cabinet.modes"))}</div><div class="modes">${me.modes.map((m) => `<span class="mode-chip">${esc(m)}</span>`).join("")}</div></div></div>`;
  access.appendChild(button(t("cabinet.open") + " ›", goHome, "btn wide"));

  const profile = el("div", "cab-card");
  profile.innerHTML = `<h3>${esc(t("cabinet.profile"))}</h3><div class="profile-name">${esc(me.nickname)}</div><div class="k">ID ${esc(me.user_id)}</div><div class="k">${esc(t("cabinet.role"))}</div><div class="v">${esc(me.role)}</div><div class="k">${esc(t("cabinet.since"))} ${esc(date(me.created_at))}</div>`;
  const nick = el("input");
  nick.value = me.nickname;
  profile.appendChild(field(t("cabinet.nick"), nick, t("cabinet.nickHint")));
  profile.appendChild(button(t("cabinet.nickSave"), async () => {
    try {
      await apiPost("/api/auth/nickname", { nickname: nick.value });
      toast(t("save"));
    } catch (e) {
      toast(String((e as Error).message), "err");
    }
  }));

  const ref = el("div", "cab-card");
  const r = me.referral;
  ref.innerHTML = `<h3>${esc(t("cabinet.ref"))}</h3><div class="ref-row"><div><div class="k">${esc(t("cabinet.refCode"))}</div><div class="code">${esc(me.referral_code)}</div></div><div><div class="k">${esc(t("cabinet.refLink"))}</div><div class="code small">${esc(me.referral_link)}</div></div></div>
  <div class="stats"><div><div class="k">${esc(t("cabinet.invited"))}</div><div class="big">${r.invited}</div></div><div><div class="k">${esc(t("cabinet.paid"))}</div><div class="big">${r.paid}</div></div><div><div class="k">${esc(t("cabinet.payments"))}</div><div class="big">${r.payments}</div></div><div><div class="k">${esc(t("cabinet.bonus"))}</div><div class="big">${r.bonus.toFixed(0)} USDT</div></div></div>
  <table class="tbl"><thead><tr><th>${esc(t("cabinet.linkStats"))}</th><th>${esc(t("cabinet.allTime"))}</th><th>${esc(t("cabinet.day"))}</th></tr></thead><tbody><tr><td>${esc(t("cabinet.clicks"))}</td><td>${r.clicks_all}</td><td>${r.clicks_day}</td></tr><tr><td>${esc(t("cabinet.regs"))}</td><td>${r.registrations_all}</td><td>${r.registrations_day}</td></tr></tbody></table>`;
  const copyRow = el("div", "btn-row");
  copyRow.append(button(t("copy") + " code", () => void navigator.clipboard.writeText(me.referral_code)), button(t("copy") + " link", () => void navigator.clipboard.writeText(me.referral_link)));
  ref.appendChild(copyRow);

  const quick = el("div", "cab-card");
  quick.innerHTML = `<h3>${esc(t("cabinet.quick"))}</h3>`;
  quick.append(
    button("Платежи и продление ›", () => toast("Платежи: обратитесь в поддержку")),
    button("Поддержка ›", () => openTicket()),
    button("Документация ›", () => window.open("/docs", "_blank")),
    button(t("cabinet.logout"), async () => {
      await apiPost("/auth/logout", {});
      location.href = "/auth/login";
    }),
  );

  const teams = el("div", "cab-card");
  teams.innerHTML = `<h3>${esc(t("cabinet.teams"))}</h3><div class="team-list"></div>`;
  const teamList = teams.querySelector(".team-list")!;
  const loadTeams = async () => {
    const tr = await apiGet<{ teams: Array<{ id: string; title: string; invite_code: string | null }> }>("/api/teams", { route: "all_futures" });
    teamList.innerHTML = tr.teams.length ? tr.teams.map((x) => `<div class="list-row"><span>${esc(x.title)}${x.invite_code ? ` <small>invite: ${esc(x.invite_code)}</small>` : ""}</span></div>`).join("") : `<div class="hint">—</div>`;
  };
  void loadTeams();
  const trow = el("div", "btn-row");
  trow.append(
    button(t("cabinet.teamCreate"), async () => {
      const title = prompt("Team title");
      if (!title) return;
      await apiPost("/api/teams/actions/create", { title });
      await loadTeams();
    }),
    button(t("cabinet.teamJoin"), async () => {
      const code = prompt("Invite code");
      if (!code) return;
      try {
        await apiPost("/api/teams/actions/join", { invite_code: code });
        await loadTeams();
      } catch (e) {
        toast(String((e as Error).message), "err");
      }
    }),
  );
  teams.appendChild(trow);

  const tickets = el("div", "cab-card wide");
  tickets.innerHTML = `<h3>${esc(t("cabinet.tickets"))}</h3><div class="hint">${esc(t("cabinet.noTickets"))}</div>`;
  grid.append(access, profile, ref, quick, teams, tickets);
}

function openTicket(): void {
  const subject = prompt("Тема обращения");
  if (!subject) return;
  const body = prompt("Текст");
  if (!body) return;
  void apiPost("/api/v1/feedback/reports", { kind: "ticket", subject, body }).then(() => toast("Отправлено")).catch((e) => toast(String(e.message), "err"));
}
