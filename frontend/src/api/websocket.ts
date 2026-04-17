// Module-level WebSocket singleton.
//
// Previously every component that called `useWebSocket()` opened its own
// connection, and the auto-reconnect-on-close (combined with StrictMode's
// double-mount) meant each navigation leaked connections — the dashboard
// showed "3 clients flashing" because new sockets were racing old ones.
//
// Now the whole app shares one socket.  The hook in hooks/useWebSocket.ts
// is a thin subscription to state exposed here.

import { getAuthToken, onAuthFail } from "./client";
import type { WebSocketMessage } from "./types";

type MessageHandler = (data: Record<string, unknown>) => void;
type ConnectionListener = (connected: boolean) => void;

const messageHandlers = new Map<string, Set<MessageHandler>>();
const connectionListeners = new Set<ConnectionListener>();

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let connected = false;
let started = false;
// Stop reconnecting once we've seen a 401-style close (auth rejection).
// AuthGate will call `resetWebSocket()` after the user supplies a new token.
let authBlocked = false;

function setConnected(next: boolean): void {
  if (connected === next) return;
  connected = next;
  connectionListeners.forEach((l) => l(next));
}

function scheduleReconnect(): void {
  if (authBlocked) return;
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, 3000);
}

function openSocket(): void {
  if (authBlocked) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const token = getAuthToken();
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${window.location.host}/ws${token ? `?token=${token}` : ""}`;

  const next = new WebSocket(url);
  ws = next;

  next.onopen = () => {
    setConnected(true);
  };

  next.onmessage = (event) => {
    try {
      const message: WebSocketMessage = JSON.parse(event.data);
      const handlers = messageHandlers.get(message.channel);
      if (handlers) {
        handlers.forEach((h) => h(message.data));
      }
    } catch {
      // ignore malformed frames
    }
  };

  next.onclose = (event) => {
    setConnected(false);
    // Backend closes with 4001 when the bearer token is wrong / missing.
    // Stop reconnecting until AuthGate clears the block.
    if (event.code === 4001) {
      authBlocked = true;
      return;
    }
    scheduleReconnect();
  };

  next.onerror = () => {
    // onclose follows; reconnect handled there.
  };
}

/**
 * Start the singleton socket.  Safe to call many times — only the first
 * call actually opens a connection.
 */
export function startWebSocket(): void {
  if (started) return;
  started = true;
  openSocket();

  // Close on navigation so the server decrements its client count
  // immediately instead of waiting on TCP timeout (otherwise a quick
  // reload shows "2 clients" for several seconds).
  window.addEventListener("beforeunload", () => {
    if (ws) {
      try {
        ws.close(1000, "page unload");
      } catch {
        // ignore
      }
    }
  });

  // If the REST client detects auth failure (401), the WS almost certainly
  // has the same problem — tear it down so AuthGate can restart cleanly.
  onAuthFail(() => {
    authBlocked = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      try {
        ws.close();
      } catch {
        // ignore
      }
      ws = null;
    }
    setConnected(false);
  });
}

/**
 * Drop the auth block and reconnect.  Call this after the user provides
 * a fresh auth token via AuthGate.
 */
export function resetWebSocket(): void {
  authBlocked = false;
  if (ws) {
    try {
      ws.close();
    } catch {
      // ignore
    }
    ws = null;
  }
  openSocket();
}

export function subscribeChannel(
  channel: string,
  handler: MessageHandler,
): () => void {
  let set = messageHandlers.get(channel);
  if (!set) {
    set = new Set();
    messageHandlers.set(channel, set);
  }
  set.add(handler);
  return () => {
    messageHandlers.get(channel)?.delete(handler);
  };
}

export function subscribeConnection(listener: ConnectionListener): () => void {
  connectionListeners.add(listener);
  // Fire immediately with current state so consumers don't flicker on mount.
  listener(connected);
  return () => {
    connectionListeners.delete(listener);
  };
}

export function isConnected(): boolean {
  return connected;
}
