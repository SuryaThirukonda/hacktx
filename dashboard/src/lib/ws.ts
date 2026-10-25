// Reconnecting WebSocket with jittered exponential backoff. Text messages are parsed as JSON.

export type SocketStatus = "connecting" | "open" | "closed";

export interface SocketOptions {
  binary?: boolean;
  onMessage: (data: unknown) => void;
  onStatus?: (s: SocketStatus) => void;
}

export class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private stopped = false;
  private timer: number | null = null;

  constructor(private url: string, private opts: SocketOptions) {
    this.connect();
  }

  private connect() {
    if (this.stopped) return;
    this.opts.onStatus?.("connecting");
    const ws = new WebSocket(this.url);
    if (this.opts.binary) ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      this.attempts = 0;
      this.opts.onStatus?.("open");
    };
    ws.onmessage = (ev) => {
      let data: unknown = ev.data;
      if (typeof ev.data === "string") {
        try {
          data = JSON.parse(ev.data);
        } catch {
          return; // malformed message
        }
      }
      try {
        this.opts.onMessage(data);
      } catch (err) {
        console.error("socket handler failed", this.url, err);
      }
    };
    ws.onclose = () => {
      this.opts.onStatus?.("closed");
      this.ws = null;
      if (this.stopped) return;
      const delay = Math.min(8000, 400 * 2 ** this.attempts) * (0.7 + Math.random() * 0.6);
      this.attempts++;
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
    ws.onerror = () => ws.close();
    this.ws = ws;
  }

  /** Drop the current connection; the normal backoff path reconnects (used to re-fetch a backlog). */
  restart() {
    this.attempts = 0;
    this.ws?.close();
  }

  send(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  close() {
    this.stopped = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.ws?.close();
  }
}
