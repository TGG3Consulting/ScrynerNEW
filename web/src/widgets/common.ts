// Small DOM helpers and the widget frame.

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, html?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

export interface WidgetFrame {
  root: HTMLElement;
  body: HTMLElement;
  head: HTMLElement;
  tools: HTMLElement;
}

export function widgetFrame(title: string, opts: { help?: string; sound?: boolean; onSound?: (on: boolean) => void; onHelp?: () => void; extra?: HTMLElement[] } = {}): WidgetFrame {
  const root = el("section", "widget");
  const head = el("div", "widget-head");
  const h = el("span", "widget-title", `${opts.help ? `<button class="help-btn" title="${esc(opts.help)}">?</button>` : ""}${esc(title)}`);
  const tools = el("span", "widget-tools");
  if (opts.sound) {
    const b = el("button", "tool-btn sound-btn active", "ЗВУК");
    b.onclick = () => {
      b.classList.toggle("active");
      opts.onSound?.(b.classList.contains("active"));
    };
    tools.appendChild(b);
  }
  for (const x of opts.extra ?? []) tools.appendChild(x);
  const expand = el("button", "tool-btn", "⤢");
  expand.onclick = () => root.classList.toggle("expanded");
  tools.appendChild(expand);
  head.append(h, tools);
  const body = el("div", "widget-body");
  root.append(head, body);
  h.querySelector(".help-btn")?.addEventListener("click", () => opts.onHelp?.());
  return { root, body, head, tools };
}

export function modal(title: string, content: HTMLElement, opts: { wide?: boolean } = {}): HTMLElement {
  const overlay = el("div", "modal-overlay");
  const box = el("div", `modal${opts.wide ? " wide" : ""}`);
  const head = el("div", "modal-head", `<span>${esc(title)}</span><button class="modal-close">×</button>`);
  box.append(head, content);
  overlay.appendChild(box);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });
  head.querySelector(".modal-close")!.addEventListener("click", () => overlay.remove());
  document.body.appendChild(overlay);
  return overlay;
}

export function toast(text: string, kind: "ok" | "err" = "ok"): void {
  const t = el("div", `toast ${kind}`, esc(text));
  document.body.appendChild(t);
  window.setTimeout(() => t.classList.add("show"), 10);
  window.setTimeout(() => {
    t.classList.remove("show");
    window.setTimeout(() => t.remove(), 300);
  }, 2500);
}

export function field(label: string, input: HTMLElement, hint?: string): HTMLElement {
  const f = el("label", "field");
  f.appendChild(el("span", "field-label", esc(label)));
  f.appendChild(input);
  if (hint) f.appendChild(el("span", "field-hint", esc(hint)));
  return f;
}

export function numInput(value: number, opts: { min?: number; step?: number; placeholder?: string } = {}): HTMLInputElement {
  const i = el("input");
  i.type = "number";
  i.value = String(value);
  if (opts.min !== undefined) i.min = String(opts.min);
  i.step = String(opts.step ?? "any");
  if (opts.placeholder) i.placeholder = opts.placeholder;
  return i;
}

export function checkbox(label: string, checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const l = el("label", "check");
  const i = el("input");
  i.type = "checkbox";
  i.checked = checked;
  i.onchange = () => onChange(i.checked);
  l.append(i, el("span", "", esc(label)));
  return l;
}

export function toggle(checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const l = el("label", "switch");
  const i = el("input");
  i.type = "checkbox";
  i.checked = checked;
  i.onchange = () => onChange(i.checked);
  l.append(i, el("span", "slider"));
  return l;
}

export function button(text: string, onClick: () => void, cls = "btn"): HTMLButtonElement {
  const b = el("button", cls, esc(text));
  b.onclick = onClick;
  return b;
}
