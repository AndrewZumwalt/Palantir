import type { ReactNode } from "react";

type Tone = "amber" | "cyan" | "green" | "red" | "gray";

const VALUE_COLORS: Record<Tone, string> = {
  amber: "text-amber-400 text-glow",
  cyan: "text-cyan-300 text-glow-cyan",
  green: "text-emerald-300",
  red: "text-red-400",
  gray: "text-gray-200",
};

export interface MetricKPIProps {
  label: string;
  value: string | number;
  unit?: string;
  tone?: Tone;
  trend?: "up" | "down" | "flat";
  delta?: string;
  foot?: ReactNode;
  className?: string;
}

/**
 * MetricKPI — big glowing readout.
 *
 *   // SUBJECTS IN FRAME
 *   12    persons
 *   +2    in last 60s
 *
 * Use for hero numbers on dashboards.  The glow is subtle; it's there to
 * make the number feel active, not to scream.
 */
export function MetricKPI({
  label,
  value,
  unit,
  tone = "amber",
  trend,
  delta,
  foot,
  className = "",
}: MetricKPIProps) {
  const trendColor =
    trend === "up"
      ? "text-emerald-400"
      : trend === "down"
        ? "text-red-400"
        : "text-gray-500";
  const trendGlyph = trend === "up" ? "▲" : trend === "down" ? "▼" : "—";

  return (
    <div className={["flex flex-col gap-1.5", className].join(" ")}>
      <div className="font-data text-[10px] uppercase tracking-[0.22em] text-gray-500">
        // {label}
      </div>
      <div className="flex items-baseline gap-2">
        <span
          className={[
            "font-data font-semibold tabular-nums text-4xl leading-none",
            VALUE_COLORS[tone],
          ].join(" ")}
        >
          {value}
        </span>
        {unit && (
          <span className="text-xs text-gray-500 font-data uppercase tracking-wider">
            {unit}
          </span>
        )}
      </div>
      {(delta || foot) && (
        <div className="flex items-center gap-2 text-[11px] font-data">
          {delta && (
            <span className={[trendColor, "tabular-nums"].join(" ")}>
              {trendGlyph} {delta}
            </span>
          )}
          {foot && <span className="text-gray-500">{foot}</span>}
        </div>
      )}
    </div>
  );
}
