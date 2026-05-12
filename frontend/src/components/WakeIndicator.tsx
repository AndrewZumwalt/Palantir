/**
 * Global voice pipeline indicator.
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
import { Ear, Mic, Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { useWebSocket } from "../hooks/useWebSocket";

interface WakePulse {
  id: number;
  confidence: number;
  phase: "wake" | "listening" | "transcribing" | "heard" | "empty" | "error";
  message?: string;
  level: number;
  // Wall-clock time so the auto-dismiss timer is robust to React
  // double-mounts in dev mode.
  triggeredAt: number;
  expiresAt: number | null;
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
      setPulse({
        id: counter,
        confidence,
        phase: "wake",
        level: 0,
        triggeredAt: Date.now(),
        expiresAt: Date.now() + FLASH_MS,
      });
    });
    return unsub;
  }, [subscribe]);

  useEffect(() => {
    const offState = subscribe("audio:state", (data) => {
      const state = typeof data.state === "string" ? data.state : "idle";
      if (state === "idle") {
        setPulse((current) => {
          if (!current || current.phase === "wake") return current;
          if (
            current.phase === "heard" ||
            current.phase === "empty" ||
            current.phase === "error"
          ) {
            return current;
          }
          return { ...current, expiresAt: Date.now() + 800 };
        });
        return;
      }
      if (
        state !== "listening" &&
        state !== "transcribing" &&
        state !== "heard" &&
        state !== "empty" &&
        state !== "error"
      ) {
        return;
      }
      setPulse((current) => ({
        id: (current?.id ?? 0) + 1,
        confidence: current?.confidence ?? 0,
        phase: state,
        message:
          typeof data.text === "string"
            ? data.text
            : typeof data.message === "string"
              ? data.message
              : undefined,
        level:
          typeof data.level === "number"
            ? Math.max(0, Math.min(1, data.level))
            : current?.level ?? 0,
        triggeredAt: current?.triggeredAt ?? Date.now(),
        expiresAt:
          state === "listening" || state === "transcribing"
            ? null
            : Date.now() + FLASH_MS,
      }));
    });
    const offLevel = subscribe("audio:level", (data) => {
      const level =
        typeof data.rms === "number"
          ? data.rms
          : typeof data.level === "number"
            ? data.level
            : 0;
      setPulse((current) =>
        current
          ? { ...current, level: Math.max(0, Math.min(1, level)) }
          : current,
      );
    });
    return () => {
      offState();
      offLevel();
    };
  }, [subscribe]);

  // Auto-dismiss after FLASH_MS.  Re-runs whenever a fresh pulse lands,
  // so back-to-back wake events extend the visible window naturally.
  useEffect(() => {
    if (!pulse || pulse.expiresAt === null) return;
    const remaining = Math.max(0, pulse.expiresAt - Date.now());
    const t = setTimeout(() => setPulse(null), remaining);
    return () => clearTimeout(t);
  }, [pulse]);

  if (!pulse) return null;

  const confidencePct = Math.round(pulse.confidence * 100);
  const Icon = pulse.phase === "listening" ? Mic : pulse.phase === "transcribing" ? Radio : Ear;
  const label =
    pulse.phase === "listening"
      ? "LISTENING"
      : pulse.phase === "transcribing"
        ? "TRANSCRIBING"
        : pulse.phase === "heard"
          ? "HEARD"
          : pulse.phase === "empty"
            ? "NO SPEECH"
            : pulse.phase === "error"
              ? "AUDIO ERROR"
              : "WAKE WORD HEARD";

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
        <Icon className="w-4 h-4 text-amber-400" />
        <span>{label}</span>
        {pulse.phase === "listening" ? (
          <span className="text-cyan-300/90 tabular-nums">
            {Math.round(pulse.level * 100)}%
          </span>
        ) : (
          <span className="text-amber-400/80 tabular-nums">
            {confidencePct}%
          </span>
        )}
      </div>
    </div>
  );
}
