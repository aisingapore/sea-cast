// Reconnecting WebSocket client for the audience captions channel.
//
// Plain WebSocket — deliberately NOT Pipecat/RTVI (those carry the speaker's
// media session; audience delivery is small JSON text frames). Behaviors:
//   - exponential backoff with jitter, capped at 8s
//   - 45s stale timer: no message (server pings every ≤20s) → assume a dead
//     connection and force a reconnect
//   - close code 4001 (token invalid/expired) → re-join for a fresh token,
//     then reconnect
//   - room_status "ended" → terminal: stop reconnecting for good
//   - a 404 while re-joining → the room is gone → also terminal

import { ApiError } from "./api";
import { CLOSE_TOKEN_INVALID, type CaptionSocketMessage } from "./captionTypes";

// Must stay above twice the server's PING_INTERVAL_SECS (20s, in
// backend/app/rooms.py), so a healthy socket survives one missed ping.
const STALE_TIMEOUT_MS = 45_000;
export const MAX_BACKOFF_MS = 8_000;
export const BASE_BACKOFF_MS = 500;

// Delay before reconnect attempt N (0-based): exponential growth capped at
// 8s, with jitter in [cap/2, cap] so a room full of phones that lost the same
// network doesn't stampede the server in lockstep.
export function reconnectDelayMs(
  attempt: number,
  random: () => number = Math.random,
): number {
  const cap = Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
  return cap / 2 + random() * (cap / 2);
}

export type CaptionsSocketStatus =
  | "connecting"
  | "open"
  | "reconnecting"
  | "ended" // room over / gone — terminal
  | "closed"; // closed by us (unmount)

// Return a join token; called with forceRefresh after a 4001 close. Throwing
// ApiError(404) signals "room gone" and ends the socket.
export type GetToken = (opts?: { forceRefresh?: boolean }) => Promise<string>;

export type CaptionsSocketOptions = {
  // Build the wss:// URL for a token (see api.captionsSocketUrl).
  socketUrl: (token: string) => string;
  getToken: GetToken;
  onMessage: (msg: CaptionSocketMessage) => void;
  onStatus?: (status: CaptionsSocketStatus) => void;
  // Test seam; defaults to `new WebSocket(url)`.
  createWebSocket?: (url: string) => WebSocket;
};

export class CaptionsSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private status: CaptionsSocketStatus = "closed";
  private staleTimer: ReturnType<typeof setTimeout> | undefined;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly opts: CaptionsSocketOptions) {}

  connect(): void {
    if (this.status !== "closed") return;
    void this.open(false);
  }

  // Stop for good (component unmount / user navigated away).
  close(): void {
    this.shutdown("closed");
  }

  getStatus(): CaptionsSocketStatus {
    return this.status;
  }

  private async open(forceToken: boolean): Promise<void> {
    if (this.isTerminal()) return;
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let token: string;
    try {
      token = await this.opts.getToken({ forceRefresh: forceToken });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        this.shutdown("ended"); // room expired or was never real
      } else {
        this.scheduleReconnect(forceToken); // transient join failure — retry
      }
      return;
    }
    // Bail if we were shut down while awaiting the token — either terminal
    // ("ended") or closed by us (unmount). Missing the "closed" case leaks a
    // live socket the owner no longer holds, which the server counts as a
    // second listener (StrictMode's mount→cleanup→mount makes this routine).
    if (this.isTerminal() || this.status === "closed") return;

    const create = this.opts.createWebSocket ?? ((url) => new WebSocket(url));
    const ws = create(this.opts.socketUrl(token));
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
      this.bumpStaleTimer();
    };
    ws.onmessage = (event) => {
      this.bumpStaleTimer();
      let msg: CaptionSocketMessage;
      try {
        msg = JSON.parse(String(event.data)) as CaptionSocketMessage;
      } catch {
        return; // not ours; ignore
      }
      this.opts.onMessage(msg);
      if (msg.type === "room_status" && msg.status === "ended") {
        this.shutdown("ended");
      }
    };
    ws.onclose = (event) => {
      this.clearStaleTimer();
      if (this.isTerminal() || this.status === "closed") return;
      this.scheduleReconnect(event.code === CLOSE_TOKEN_INVALID);
    };
    // Errors are always followed by close; onclose owns the retry.
    ws.onerror = () => {};
  }

  private scheduleReconnect(forceToken: boolean): void {
    this.setStatus("reconnecting");
    const delay = reconnectDelayMs(this.attempt);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => void this.open(forceToken), delay);
  }

  // Any inbound frame proves the link is alive; silence past the server's
  // ping cadence means it isn't — close locally, which triggers reconnect.
  private bumpStaleTimer(): void {
    this.clearStaleTimer();
    this.staleTimer = setTimeout(() => this.ws?.close(), STALE_TIMEOUT_MS);
  }

  private clearStaleTimer(): void {
    if (this.staleTimer !== undefined) clearTimeout(this.staleTimer);
    this.staleTimer = undefined;
  }

  private isTerminal(): boolean {
    return this.status === "ended";
  }

  private shutdown(status: "closed" | "ended"): void {
    this.clearStaleTimer();
    if (this.reconnectTimer !== undefined) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = undefined;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onopen = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
    this.setStatus(status);
  }

  private setStatus(status: CaptionsSocketStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.opts.onStatus?.(status);
  }
}
