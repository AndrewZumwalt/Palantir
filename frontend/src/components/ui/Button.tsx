import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "cyan";
type Size = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  iconLeft?: ReactNode;
  iconRight?: ReactNode;
  fullWidth?: boolean;
  loading?: boolean;
}

const BASE =
  "inline-flex items-center justify-center gap-2 font-data uppercase tracking-[0.14em] border transition-[transform,box-shadow,background-color,border-color,color] select-none disabled:opacity-40 disabled:cursor-not-allowed active:translate-y-px";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-amber-500 text-gray-950 border-amber-400 hover:bg-amber-400 hover:shadow-[0_0_18px_-2px_rgba(245,158,11,0.7)] focus-visible:outline-amber-300",
  secondary:
    "bg-[#0f1629] text-gray-200 border-[#2a3658] hover:border-amber-500/70 hover:text-amber-400",
  ghost:
    "bg-transparent text-gray-300 border-transparent hover:bg-[#0f1629] hover:text-amber-400",
  danger:
    "bg-red-600/90 text-white border-red-500 hover:bg-red-500 hover:shadow-[0_0_16px_-2px_rgba(239,68,68,0.7)]",
  cyan: "bg-cyan-500/10 text-cyan-300 border-cyan-600/50 hover:bg-cyan-500/20 hover:border-cyan-400",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 px-2.5 text-[11px]",
  md: "h-9 px-3.5 text-xs",
  lg: "h-11 px-5 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  iconLeft,
  iconRight,
  fullWidth,
  loading,
  className = "",
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={[
        BASE,
        VARIANTS[variant],
        SIZES[size],
        fullWidth ? "w-full" : "",
        className,
      ].join(" ")}
    >
      {loading ? (
        <span className="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
      ) : (
        iconLeft
      )}
      {children && <span className="leading-none">{children}</span>}
      {!loading && iconRight}
    </button>
  );
}
