// Browser-side delivery: user's own Telegram bot (batched queue), Android push via server, sound and voice.

import { store } from "./store";
import { apiPost } from "./api";

type FeedKind = "alerts" | "bots" | "density" | "twap" | "ticks" | "team";

interface Queued {
  kind: FeedKind;
  key: string;
  text: string;
  ts: number;
}

const MAX_BATCH = 8;
const MIN_INTERVAL_MS = 2000;
const TTL_MS = 60_000;

class TelegramQueue {
  private q: Queued[] = [];
  private seen = new Set<string>();
  private timer: number | null = null;
  private lastSent = 0;
  private isLeader = false;
  private channel = new BroadcastChannel("scryner-telegram");

  constructor() {
    // one tab sends: simple leader election by lowest start time
    const myId = `${Date.now()}-${Math.random()}`;
    const peers = new Map<string, number>();
    this.channel.onmessage = (ev) => {
      if (ev.data?.type === "hello") peers.set(ev.data.id, ev.data.started);
      if (ev.data?.type === "bye") peers.delete(ev.data.id);
      this.isLeader = [...peers.values()].every((s) => s >= Number(myId.split("-")[0]));
    };
    const started = Number(myId.split("-")[0]);
    this.channel.postMessage({ type: "hello", id: myId, started });
    window.setInterval(() => this.channel.postMessage({ type: "hello", id: myId, started }), 5000);
    window.addEventListener("beforeunload", () => this.channel.postMessage({ type: "bye", id: myId }));
    this.isLeader = true;
  }

  enqueue(kind: FeedKind, key: string, text: string, ts = Date.now()): void {
    const tg = store.profile.telegram;
    if (!tg.bot_token || !tg.chat_id || !tg.sources[kind]) return;
    const id = `${kind}|${key}`;
    if (this.seen.has(id)) return;
    this.seen.add(id);
    window.setTimeout(() => this.seen.delete(id), TTL_MS);
    this.q.push({ kind, key, text, ts });
    this.schedule();
  }

  private schedule(): void {
    if (this.timer) return;
    const wait = Math.max(0, MIN_INTERVAL_MS - (Date.now() - this.lastSent));
    this.timer = window.setTimeout(() => void this.flush(), wait);
  }

  private async flush(): Promise<void> {
    this.timer = null;
    const now = Date.now();
    this.q = this.q.filter((x) => now - x.ts <= TTL_MS);
    if (!this.q.length || !this.isLeader) return;
    const batch = this.q.splice(0, MAX_BATCH);
    const tg = store.profile.telegram;
    const text = batch.map((b) => b.text).join("\n\n");
    this.lastSent = Date.now();
    try {
      await fetch(`https://api.telegram.org/bot${tg.bot_token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: tg.chat_id, text: text.slice(0, 4000), disable_web_page_preview: true }),
      });
    } catch {
      /* network */
    }
    if (this.q.length) this.schedule();
  }
}

export const telegramQueue = new TelegramQueue();

export async function pushToPhone(kind: FeedKind, title: string, text: string, data: { [k: string]: string } = {}): Promise<void> {
  const m = store.profile.telegram.mobile;
  if (!(m as any)[kind]) return;
  try {
    await apiPost("/api/mobile-alerts/send", { title, text, data: { kind, ...data } });
  } catch {
    /* not paired */
  }
}

// ---------------------------------------------------------------- sound / voice
let audioCtx: AudioContext | null = null;
const buffers = new Map<string, AudioBuffer>();

export const SOUNDS: { [name: string]: () => AudioBuffer } = {};

function ctx(): AudioContext {
  if (!audioCtx) audioCtx = new AudioContext();
  return audioCtx;
}

function synth(name: string): AudioBuffer {
  const c = ctx();
  const dur = name === "bum" ? 0.25 : name === "ping" ? 0.15 : 0.4;
  const buf = c.createBuffer(1, Math.floor(c.sampleRate * dur), c.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < d.length; i++) {
    const tt = i / c.sampleRate;
    const env = Math.exp(-tt * (name === "bum" ? 18 : 10));
    const f = name === "bum" ? 160 - tt * 200 : name === "ping" ? 1200 : name === "alarm" ? 660 + Math.sin(tt * 40) * 120 : 440;
    d[i] = Math.sin(2 * Math.PI * f * tt) * env * 0.6;
  }
  return buf;
}

export function playSound(name = "bum", volume?: number): void {
  const vol = volume ?? store.profile.prefs.sound_volume;
  if (vol <= 0) return;
  try {
    const c = ctx();
    let b = buffers.get(name);
    if (!b) {
      b = synth(name);
      buffers.set(name, b);
    }
    const src = c.createBufferSource();
    src.buffer = b;
    const g = c.createGain();
    g.gain.value = vol;
    src.connect(g).connect(c.destination);
    src.start();
  } catch {
    /* autoplay policy */
  }
}

export function speak(text: string): void {
  if (store.profile.prefs.sound_volume <= 0 || !("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(text);
  u.volume = store.profile.prefs.sound_volume;
  u.rate = 1.1;
  speechSynthesis.speak(u);
}
