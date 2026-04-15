import { useCallback, useEffect, useRef, useState } from "react";
import type { WebSocketMessage } from "../api/types";

type MessageHandler = (data: Record<string, unknown>) => void;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef<Map<string, Set<MessageHandler>>>(new Map());
  const [connected, setConnected] = useState(false);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const connect = useCallback(() => {
    const token = localStorage.getItem("palintir_auth_token");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws${token ? `?token=${token}` : ""}`;

    const ws = new WebSocket(url);

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onclose = () => {
      setConnected(false);
      // Reconnect after 3 seconds
      reconnectTimeoutRef.current = setTimeout(connect, 3000);
    };

    ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        const handlers = handlersRef.current.get(message.channel);
        if (handlers) {
          handlers.forEach((handler) => handler(message.data));
        }
      } catch {
        // Ignore malformed messages
      }
    };

    wsRef.current = ws;
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const subscribe = useCallback(
    (channel: string, handler: MessageHandler) => {
      if (!handlersRef.current.has(channel)) {
        handlersRef.current.set(channel, new Set());
      }
      handlersRef.current.get(channel)!.add(handler);

      // Return unsubscribe function
      return () => {
        handlersRef.current.get(channel)?.delete(handler);
      };
    },
    [],
  );

  return { connected, subscribe };
}
