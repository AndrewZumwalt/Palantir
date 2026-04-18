import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export function Modal({
  open,
  onClose,
  label,
  title,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  label?: string;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  const sizeCls = {
    sm: "max-w-md",
    md: "max-w-xl",
    lg: "max-w-3xl",
    xl: "max-w-5xl",
  }[size];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 crt-on"
      role="dialog"
      aria-modal="true"
      aria-label={title || label}
    >
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close dialog"
        onClick={onClose}
        className="absolute inset-0 bg-black/80 backdrop-blur-sm cursor-default"
      />
      {/* Panel */}
      <div
        className={[
          "relative w-full bg-[#0a0f1c] border border-[#2a3658] shadow-[0_0_80px_-10px_rgba(245,158,11,0.35)] bracket-corners",
          sizeCls,
        ].join(" ")}
      >
        <header className="flex items-center justify-between gap-3 px-5 py-3 border-b border-[#1c2540]">
          <div className="flex items-baseline gap-3 min-w-0">
            {label && (
              <span className="font-data text-[10px] uppercase tracking-[0.2em] text-amber-500">
                // {label}
              </span>
            )}
            {title && (
              <h2 className="text-base font-semibold text-gray-100 truncate">
                {title}
              </h2>
            )}
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 inline-flex items-center justify-center border border-[#1c2540] text-gray-400 hover:border-amber-500 hover:text-amber-400"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </header>
        <div className="p-5 max-h-[calc(100vh-180px)] overflow-y-auto">
          {children}
        </div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[#1c2540] bg-[#05080f]/60">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}
