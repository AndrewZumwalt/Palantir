import { CheckCircle2, Loader2, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { subscribeChannel } from "../api/websocket";

export type ReloadStatus = "pending" | "running" | "ok" | "error";

export interface ReloadProgress {
  reload_id: string;
  service: string;
  status: ReloadStatus;
  message: string;
}

/**
 * Overlay shown while a soft-reload request is in flight. Subscribes to the
 * `system:reload:progress` WebSocket channel and renders per-service status
 * as each backend service reports in.
 *
 * Rendered on top of the CRT power-cycle animation so the user sees both the
 * visual reboot vibe AND the actual thing the button did.
 */
export function ReloadOverlay({
  reloadId,
  services,
  onFinished,
}: {
  reloadId: string;
  services: string[];
  onFinished: () => void;
}) {
  const [byService, setByService] = useState<Record<string, ReloadProgress>>(
    () =>
      Object.fromEntries(
        services.map((s) => [
          s,
          {
            reload_id: reloadId,
            service: s,
            status: "pending" as ReloadStatus,
            message: "queued",
          },
        ]),
      ),
  );

  useEffect(() => {
    const unsub = subscribeChannel("system:reload:progress", (data) => {
      const p = data as unknown as ReloadProgress;
      if (p.reload_id !== reloadId) return;
      setByService((prev) => ({ ...prev, [p.service]: p }));
    });
    return unsub;
  }, [reloadId]);

  const allDone = services.every((s) => {
    const st = byService[s]?.status;
    return st === "ok" || st === "error";
  });

  // Auto-dismiss ~700ms after everything reports. Long enough for the user
  // to see the green check, short enough not to feel like a modal.
  useEffect(() => {
    if (!allDone) return;
    const id = setTimeout(onFinished, 700);
    return () => clearTimeout(id);
  }, [allDone, onFinished]);

  // Safety net: if a service never reports (e.g. it's down), don't leave the
  // overlay up forever.
  useEffect(() => {
    const id = setTimeout(onFinished, 12_000);
    return () => clearTimeout(id);
  }, [onFinished]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center pointer-events-none"
      aria-live="polite"
      aria-label="System reload in progress"
    >
      <div className="pointer-events-auto w-[min(92vw,440px)] border border-amber-600/40 bg-[#05080f]/95 backdrop-blur-sm shadow-[0_0_40px_rgba(245,158,11,0.15)]">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-[#1c2540] font-data text-[10px] uppercase tracking-[0.22em] text-amber-400">
          <Zap className="w-3 h-3" />
          <span>// POWER CYCLE</span>
          <span className="ml-auto text-gray-500">{reloadId.slice(0, 6)}</span>
        </div>
        <ul className="divide-y divide-[#1c2540]">
          {services.map((name) => {
            const p = byService[name];
            return (
              <li
                key={name}
                className="flex items-center gap-3 px-3 py-2 font-data text-[11px]"
              >
                <StatusIcon status={p?.status ?? "pending"} />
                <span className="uppercase tracking-[0.16em] text-gray-200 w-20">
                  {name}
                </span>
                <span
                  className={[
                    "flex-1 truncate text-[10px]",
                    p?.status === "error"
                      ? "text-red-400"
                      : p?.status === "ok"
                        ? "text-emerald-300"
                        : "text-gray-500",
                  ].join(" ")}
                >
                  {p?.message ?? "waiting"}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: ReloadStatus }) {
  if (status === "ok")
    return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />;
  if (status === "error")
    return <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />;
  if (status === "running")
    return (
      <Loader2 className="w-3.5 h-3.5 text-amber-400 shrink-0 animate-spin" />
    );
  return (
    <span className="w-3.5 h-3.5 shrink-0 flex items-center justify-center">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-600 pulse-dot" />
    </span>
  );
}
