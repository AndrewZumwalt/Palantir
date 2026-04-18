import type { ReactNode } from "react";

type Tone = "amber" | "cyan" | "green" | "red" | "gray" | "violet";

const TONES: Record<Tone, string> = {
  amber: "text-amber-400 border-amber-600/50 bg-amber-500/10",
  cyan: "text-cyan-300 border-cyan-600/50 bg-cyan-500/10",
  green: "text-emerald-300 border-emerald-600/50 bg-emerald-500/10",
  red: "text-red-300 border-red-600/50 bg-red-500/10",
  gray: "text-gray-400 border-gray-700/60 bg-gray-500/5",
  violet: "text-violet-300 border-violet-600/50 bg-violet-500/10",
};

const DOT_TONES: Record<Tone, string> = {
  amber: "bg-amber-400 text-amber-400",
  cyan: "bg-cyan-400 text-cyan-400",
  green: "bg-emerald-400 text-emerald-400",
  red: "bg-red-400 text-red-400",
  gray: "bg-gray-500 text-gray-500",
  violet: "bg-violet-400 text-violet-400",
};

export interface StatusPillProps {
  tone?: Tone;
  pulse?: boolean;
  icon?: ReactNode;
  children: ReactNode;
  size?: "xs" | "sm";
  brackets?: boolean;
  className?: string;
}

/**
 * StatusPill — small bracketed label.  Use to communicate state of a thing:
 * [ ONLINE ] [ CLASSIFIED ] [ IDLE ].  When `pulse` is set, shows a live dot.
 */
export function StatusPill({
  tone = "gray",
  pulse = false,
  icon,
  children,
  size = "xs",
  brackets = true,
  className = "",
}: StatusPillProps) {
  const sizeCls =
    size === "xs" ? "h-5 px-1.5 text-[10px]" : "h-6 px-2 text-[11px]";
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 font-data uppercase tracking-[0.14em] border rounded-sm whitespace-nowrap",
        TONES[tone],
        sizeCls,
        className,
      ].join(" ")}
    >
      {pulse && (
        <span
          className={["w-1.5 h-1.5 rounded-full pulse-dot", DOT_TONES[tone]].join(
            " "
          )}
        />
      )}
      {icon}
      {brackets && <span className="opacity-60">[</span>}
      <span>{children}</span>
      {brackets && <span className="opacity-60">]</span>}
    </span>
  );
}
