import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const LABEL_CLS =
  "block font-data text-[10px] uppercase tracking-[0.18em] text-gray-400 mb-1.5";

const INPUT_CLS =
  "w-full h-10 px-3 bg-[#0a0f1c] border border-[#1c2540] text-gray-100 text-sm font-sans rounded-none focus:border-amber-500 focus:bg-[#05080f] focus:outline-none placeholder:text-gray-600";

const HELP_CLS = "mt-1.5 text-[11px] font-data text-gray-500";
const ERR_CLS = "mt-1.5 text-[11px] font-data text-red-400";

export function Field({
  label,
  required,
  help,
  error,
  children,
  className = "",
}: {
  label?: string;
  required?: boolean;
  help?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      {label && (
        <label className={LABEL_CLS}>
          // {label}
          {required && <span className="text-amber-500 ml-1">*</span>}
        </label>
      )}
      {children}
      {error ? (
        <div className={ERR_CLS} role="alert">
          ! {error}
        </div>
      ) : help ? (
        <div className={HELP_CLS}>{help}</div>
      ) : null}
    </div>
  );
}

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  help?: ReactNode;
  error?: ReactNode;
  wrapperClassName?: string;
}

export function TextInput({
  label,
  help,
  error,
  required,
  wrapperClassName,
  className = "",
  ...props
}: TextInputProps) {
  return (
    <Field
      label={label}
      required={required}
      help={help}
      error={error}
      className={wrapperClassName}
    >
      <input
        {...props}
        required={required}
        className={[INPUT_CLS, className].join(" ")}
      />
    </Field>
  );
}

export interface SelectProps
  extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  help?: ReactNode;
  error?: ReactNode;
  wrapperClassName?: string;
}

export function Select({
  label,
  help,
  error,
  required,
  wrapperClassName,
  className = "",
  children,
  ...props
}: SelectProps) {
  return (
    <Field
      label={label}
      required={required}
      help={help}
      error={error}
      className={wrapperClassName}
    >
      <select
        {...props}
        required={required}
        className={[INPUT_CLS, "appearance-none cursor-pointer", className].join(
          " "
        )}
      >
        {children}
      </select>
    </Field>
  );
}

export interface TextAreaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  help?: ReactNode;
  error?: ReactNode;
  wrapperClassName?: string;
}

export function TextArea({
  label,
  help,
  error,
  required,
  wrapperClassName,
  className = "",
  ...props
}: TextAreaProps) {
  return (
    <Field
      label={label}
      required={required}
      help={help}
      error={error}
      className={wrapperClassName}
    >
      <textarea
        {...props}
        required={required}
        className={[INPUT_CLS, "h-auto min-h-20 py-2", className].join(" ")}
      />
    </Field>
  );
}

/** Terminal-style toggle — OFF ▌ ON */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={[
        "inline-flex items-center gap-2 h-7 px-2 border font-data text-[11px] uppercase tracking-[0.16em] disabled:opacity-40",
        checked
          ? "bg-amber-500/15 text-amber-300 border-amber-600/60"
          : "bg-[#0a0f1c] text-gray-500 border-[#1c2540] hover:text-gray-300",
      ].join(" ")}
    >
      <span
        className={[
          "w-6 h-3 border relative transition-colors",
          checked
            ? "bg-amber-500 border-amber-400"
            : "bg-[#0a0f1c] border-[#2a3658]",
        ].join(" ")}
      >
        <span
          className={[
            "absolute top-0 w-2 h-full bg-gray-950 transition-all",
            checked ? "left-[calc(100%-0.5rem)]" : "left-0",
          ].join(" ")}
        />
      </span>
      {label && <span>{label}</span>}
    </button>
  );
}
