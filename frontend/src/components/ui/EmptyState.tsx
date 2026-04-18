import type { ReactNode } from "react";

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      {icon && (
        <div className="w-12 h-12 mb-4 flex items-center justify-center border border-[#1c2540] text-gray-600">
          {icon}
        </div>
      )}
      <div className="font-data text-xs uppercase tracking-[0.2em] text-gray-500 mb-1">
        // {title}
      </div>
      {description && (
        <p className="text-sm text-gray-500 max-w-sm">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Skeleton row — terminal-style loader */
export function LoadingLines({
  rows = 3,
  className = "",
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={["space-y-2 font-data text-xs", className].join(" ")}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-2 text-amber-500/70 breathe"
          style={{ animationDelay: `${i * 0.1}s` }}
        >
          <span className="text-[10px]">{">"}</span>
          <span className="h-3 bg-[#1c2540] flex-1" style={{ width: `${60 + ((i * 17) % 35)}%` }} />
        </div>
      ))}
    </div>
  );
}
