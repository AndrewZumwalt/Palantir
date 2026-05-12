/**
 * Global wake-word flash indicator.
 *
 * Subscribes to the `audio:wake` WebSocket channel and shows a brief
 * pulsing badge in the top-right corner whenever the audio service
 * reports that the wake word was detected.  Without this, the only
 * feedback was the brain's reply 3-5 seconds later -- if STT or the
 * brain choked, the operator had no idea whether the wake word was
 * heard at all.
 *
 * Mounted in Layout so it's visible on every page.
 */
import { Ear } from "lucide-react";
import { useEffect, useState } from "react";
import { useWebSocket } from "../hooks/useWebSocket";

interface WakePulse {
  id: number;
  confidence: number;
  // Wall-clock time so the auto-dismiss timer is robust to React
  // double-mounts in dev mode.
  triggeredAt: number;
}

const FLASH_MS = 2500;

export function WakeIndicator() {
  const { subscribe } = useWebSocket();
  const [pulse, setPulse] = useState<WakePulse | null>(null);

  useEffect(() => {
    let counter = 0;
    const unsub = subscribe("audio:wake", (data) => {
      // The audio service publishes WakeWordEvent: { confidence, timestamp }.
      const confidence = typeof data.confidence === "number" ? data.confidence : 0;
      counter += 1;
      setPulse({ id: counter, confidence, triggeredAt: Date.now() });
    });
    return unsub;
  }, [subscribe]);

  // Auto-dismiss after FLASH_MS.  Re-runs whenever a fresh pulse lands,
  // so back-to-back wake events extend the visible window naturally.
  useEffect(() => {
    if (!pulse) return;
    const remaining = Math.max(
      0,
      FLASH_MS - (Date.now() - pulse.triggeredAt),
    );
    const t = setTimeout(() => setPulse(null), remaining);
    return () => clearTimeout(t);
  }, [pulse]);

  if (!pulse) return null;

  const confidencePct = Math.round(pulse.confidence * 100);

  return (
    <div
      aria-live="polite"
      className="fixed top-20 right-4 md:right-6 z-50 pointer-events-none"
    >
      <div
        key={pulse.id}
        className="wake-pulse flex items-center gap-2 px-3 py-2 border border-amber-500/80 bg-amber-500/15 text-amber-100 rounded-sm font-data text-xs uppercase tracking-[0.18em] backdrop-blur"
        style={{
          boxShadow: "0 0 12px rgba(245, 158, 11, 0.4)",
        }}
      >
        <Ear className="w-4 h-4 text-amber-400" />
        <span>WAKE WORD HEARD</span>
        <span className="text-amber-400/80 tabular-nums">
          {confidencePct}%
        </span>
      </div>
    </div>
  );
}
