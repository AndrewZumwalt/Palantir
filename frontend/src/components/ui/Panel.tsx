import type { ReactNode } from "react";

/**
 * Panel — the workhorse surface.
 *
 * Renders a dark card with a mono `// LABEL` header and optional right-aligned
 * meta slot.  Corner brackets hint at a reticle / CRT frame without being
 * cartoony.  All panels share the same border + spacing so the dashboard
 * reads as one machine, not a pile of components.
 */
export interface PanelProps {
  label?: string;
  title?: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  tone?: "default" | "amber" | "cyan" | "danger";
  dense?: boolean;
  brackets?: boolean;
}

const TONE_BORDER: Record<NonNullable<PanelProps["tone"]>, string> = {
  default: "border-[#1c2540]",
  amber: "border-amber-700/60",
  cyan: "border-cyan-700/60",
  danger: "border-red-700/60",
};

const TONE_LABEL: Record<NonNullable<PanelProps["tone"]>, string> = {
  default: "text-gray-500",
  amber: "text-amber-500",
  cyan: "text-cyan-400",
  danger: "text-red-400",
};

export function Panel({
  label,
  title,
  meta,
  children,
  className = "",
  bodyClassName = "",
  tone = "default",
  dense = false,
  brackets = false,
}: PanelProps) {
  return (
    <section
      className={[
        "relative bg-[#0a0f1c]/80 border",
        TONE_BORDER[tone],
        brackets ? "bracket-corners" : "",
        className,
      ].join(" ")}
    >
      {(label || title || meta) && (
        <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-[#1c2540]">
          <div className="flex items-baseline gap-3 min-w-0">
            {label && (
              <span
                className={[
                  "font-data text-[10px] uppercase tracking-[0.18em]",
                  TONE_LABEL[tone],
                ].join(" ")}
              >
                // {label}
              </span>
            )}
            {title && (
              <h2 className="text-sm font-semibold text-gray-100 truncate">
                {title}
              </h2>
            )}
          </div>
          {meta && (
            <div className="flex items-center gap-2 text-xs text-gray-400 font-data">
              {meta}
            </div>
          )}
        </header>
      )}
      <div className={[dense ? "p-3" : "p-4", bodyClassName].join(" ")}>
        {children}
      </div>
    </section>
  );
}

/**
 * SectionHeader — free-standing label.  Use above groups of panels.
 */
export function SectionHeader({
  label,
  title,
  children,
}: {
  label: string;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-between gap-4 mb-3">
      <div>
        <div className="font-data text-[10px] uppercase tracking-[0.22em] text-amber-500">
          // {label}
        </div>
        {title && (
          <h2 className="text-xl font-semibold text-gray-100 mt-0.5">
            {title}
          </h2>
        )}
      </div>
      {children && <div>{children}</div>}
    </div>
  );
}
