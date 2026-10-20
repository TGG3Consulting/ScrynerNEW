// Reconnecting WebSocket with snapshot/patch state tracking and application pings.

export type Json = any;

export interface WsOptions {
  onOpen?: (ws: ReconnectingWs) => void;
  onMessage: (msg: Json) => void;
  pingEvery?: number;
  pingPayload?: string;
  binary?: "gzip" | "none";
}

export class ReconnectingWs {
  private ws: WebSocket | null = null;
  private stopped = false;
  private delay = 1000;
  private pingTimer: number | null = null;
  connected = false;
  messages = 0;

  constructor(public url: string, private opts: WsOptions) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.pingTimer) window.clearInterval(this.pingTimer);
    this.ws?.close();
    this.ws = null;
  }

  send(payload: Json): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
  }

  private connect(): void {
    if (this.stopped) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}${this.url}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this.delay = 1000;
      this.opts.onOpen?.(this);
      if (this.opts.pingEvery) {
        this.pingTimer = window.setInterval(() => this.send(this.opts.pingPayload ?? '{"ping":1}'), this.opts.pingEvery);
      }
    };
    ws.onmessage = async (ev) => {
      this.messages++;
      let text: string;
      if (ev.data instanceof ArrayBuffer) {
        const ds = new DecompressionStream("gzip");
        const stream = new Blob([ev.data]).stream().pipeThrough(ds);
        text = await new Response(stream).text();
      } else {
        text = ev.data as string;
      }
      if (text === "pong") return;
      try {
        this.opts.onMessage(JSON.parse(text));
      } catch {
        /* non-JSON frame */
      }
    };
    ws.onclose = () => {
      this.connected = false;
      if (this.pingTimer) window.clearInterval(this.pingTimer);
      this.pingTimer = null;
      if (!this.stopped) {
        window.setTimeout(() => this.connect(), this.delay + Math.random() * 500);
        this.delay = Math.min(30000, this.delay * 2);
      }
    };
    ws.onerror = () => ws.close();
  }
}

/** Keeps a `{key: record}` map in sync with snapshot/patch messages (seq checked). */
export class PatchState<T extends object> {
  data: Map<string, T> = new Map();
  seq = 0;
  listeners: Array<(changed: string[], deleted: string[], full: boolean) => void> = [];

  apply(msg: Json): boolean {
    if (msg.type === "snapshot") {
      this.data = new Map(Object.entries(msg.data as { [k: string]: T }));
      this.seq = msg.seq;
      this.emit([...this.data.keys()], [], true);
      return true;
    }
    if (msg.type === "patch") {
      if (msg.base_seq !== this.seq) return false; // out of sync → caller reconnects
      const changed: string[] = [];
      for (const [k, v] of Object.entries(msg.updates ?? {})) {
        const cur = this.data.get(k);
        this.data.set(k, cur ? ({ ...cur, ...(v as object) } as T) : (v as T));
        changed.push(k);
      }
      for (const [k, fields] of Object.entries(msg.unset ?? {})) {
        const cur = this.data.get(k) as any;
        if (cur) for (const f of fields as string[]) delete cur[f];
      }
      const deleted: string[] = msg.delete ?? [];
      for (const k of deleted) this.data.delete(k);
      this.seq = msg.seq;
      this.emit(changed, deleted, false);
      return true;
    }
    return true;
  }

  private emit(changed: string[], deleted: string[], full: boolean): void {
    for (const l of this.listeners) l(changed, deleted, full);
  }
}
