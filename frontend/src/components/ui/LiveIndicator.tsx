import { Radio } from "lucide-react";

/**
 * LiveIndicator — tiny pulsing red dot + LIVE text.  Use on panels whose
 * content updates in real-time (engagement feed, event stream).
 */
export function LiveIndicator({
  label = "LIVE",
  tone = "red",
  className = "",
}: {
  label?: string;
  tone?: "red" | "amber" | "cyan";
  className?: string;
}) {
  const color =
    tone === "red"
      ? "text-red-400"
      : tone === "amber"
        ? "text-amber-400"
        : "text-cyan-300";
  const dot =
    tone === "red"
      ? "bg-red-500"
      : tone === "amber"
        ? "bg-amber-500"
        : "bg-cyan-400";
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 font-data text-[10px] uppercase tracking-[0.22em]",
        color,
        className,
      ].join(" ")}
    >
      <span className={["w-1.5 h-1.5 rounded-full pulse-dot", dot].join(" ")} />
      <span>{label}</span>
    </span>
  );
}

/** Same thing but bigger, with a broadcast icon.  Used in headers. */
export function BroadcastPulse({ label = "TRANSMITTING" }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 font-data text-xs uppercase tracking-[0.2em] text-amber-400">
      <Radio className="w-3.5 h-3.5 breathe" />
      {label}
    </span>
  );
}
