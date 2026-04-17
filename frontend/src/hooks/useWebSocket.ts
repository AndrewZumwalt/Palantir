import { useCallback, useEffect, useState } from "react";
import {
  startWebSocket,
  subscribeChannel,
  subscribeConnection,
  isConnected,
} from "../api/websocket";

type MessageHandler = (data: Record<string, unknown>) => void;

/**
 * Subscribe to the app-wide singleton WebSocket.  Many components can call
 * this hook; they all share one underlying connection (see api/websocket.ts).
 */
export function useWebSocket() {
  const [connected, setConnected] = useState(isConnected);

  useEffect(() => {
    startWebSocket();
    const unsub = subscribeConnection(setConnected);
    return unsub;
  }, []);

  const subscribe = useCallback(
    (channel: string, handler: MessageHandler) => subscribeChannel(channel, handler),
    [],
  );

  return { connected, subscribe };
}
