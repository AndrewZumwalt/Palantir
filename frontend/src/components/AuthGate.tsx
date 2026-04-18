import { Eye, KeyRound, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  clearAuthToken,
  getAuthToken,
  onAuthFail,
  setAuthToken,
} from "../api/client";
import { resetWebSocket } from "../api/websocket";
import { Button } from "./ui/Button";

/**
 * Auth gate — surveillance-console styling.
 *
 * Drops the old indigo card in favor of a CRT-style boot screen: thin
 * monospace chrome, a slow "initializing" readout, and the token field
 * themed as a `> credential prompt`.  Functional behavior is identical
 * to the previous component: token in localStorage -> render children;
 * no token or a 401 -> show the form again.
 */

const BOOT_LINES = [
  "> palantir-observatory boot v0.1",
  "> link: local // encrypted",
  "> signal: nominal",
  "> awaiting operator credential...",
];

export default function AuthGate({ children }: { children: ReactNode }) {
  const [hasToken, setHasToken] = useState<boolean>(() => !!getAuthToken());
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [bootStep, setBootStep] = useState(0);

  useEffect(() => {
    return onAuthFail(() => {
      clearAuthToken();
      setHasToken(false);
      setError("Credential rejected — verify token and retry.");
    });
  }, []);

  // Animate boot lines
  useEffect(() => {
    if (hasToken) return;
    if (bootStep >= BOOT_LINES.length) return;
    const id = setTimeout(() => setBootStep((s) => s + 1), 280);
    return () => clearTimeout(id);
  }, [bootStep, hasToken]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) {
      setError("Credential cannot be empty.");
      return;
    }
    setAuthToken(trimmed);
    setError(null);
    setInput("");
    setHasToken(true);
    resetWebSocket();
  };

  if (hasToken) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-dvh flex items-center justify-center px-4 py-10 bg-grid bg-[#05080f]/40">
      <div className="w-full max-w-lg">
        {/* Brand mark */}
        <div className="flex flex-col items-center mb-8">
          <div className="relative">
            <div className="w-20 h-20 border border-amber-500/40 rounded-full flex items-center justify-center">
              <div className="w-14 h-14 border border-amber-500/60 rounded-full flex items-center justify-center">
                <Eye className="w-7 h-7 text-amber-500 breathe" />
              </div>
            </div>
            <div className="absolute inset-0 rounded-full border border-amber-500/20 animate-ping" />
          </div>
          <div className="mt-4 text-center">
            <div className="font-data text-[10px] uppercase tracking-[0.34em] text-amber-500">
              // PALANTIR
            </div>
            <h1 className="text-2xl font-semibold text-gray-100 mt-1 tracking-tight">
              Observatory
            </h1>
          </div>
        </div>

        {/* Boot readout */}
        <div className="mb-4 font-data text-[11px] text-gray-500 space-y-0.5 min-h-[72px]">
          {BOOT_LINES.slice(0, bootStep).map((line, i) => (
            <div
              key={i}
              className={
                i === bootStep - 1
                  ? "text-amber-400"
                  : i === BOOT_LINES.length - 1 && bootStep >= BOOT_LINES.length
                    ? "text-amber-400"
                    : ""
              }
            >
              {line}
              {i === bootStep - 1 && bootStep < BOOT_LINES.length && (
                <span className="inline-block w-2 h-3 bg-amber-400 ml-1 animate-pulse align-middle" />
              )}
            </div>
          ))}
        </div>

        {/* Form card */}
        <form
          onSubmit={submit}
          className="relative bg-[#0a0f1c]/90 border border-[#1c2540] bracket-corners crt-on"
        >
          <header className="flex items-center justify-between px-4 py-2.5 border-b border-[#1c2540]">
            <div className="flex items-center gap-2 font-data text-[10px] uppercase tracking-[0.2em] text-amber-500">
              <Lock className="w-3 h-3" />
              // CREDENTIAL PROMPT
            </div>
            <div className="flex items-center gap-1.5 font-data text-[10px] text-gray-500">
              <span className="w-1.5 h-1.5 bg-red-500 rounded-full pulse-dot" />
              <span>LOCKED</span>
            </div>
          </header>

          <div className="p-5 space-y-4">
            <label className="block">
              <span className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-400 mb-1.5 flex items-center justify-between">
                <span>// AUTHORIZATION TOKEN</span>
                <button
                  type="button"
                  onClick={() => setReveal((r) => !r)}
                  className="text-gray-500 hover:text-amber-400 normal-case tracking-normal text-[11px]"
                >
                  {reveal ? "hide" : "reveal"}
                </button>
              </span>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 font-data text-amber-500 text-sm pointer-events-none">
                  {"›"}
                </span>
                <input
                  type={reveal ? "text" : "password"}
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="palantir_auth_token"
                  aria-label="Authorization token"
                  className="w-full h-11 pl-8 pr-3 bg-[#05080f] border border-[#1c2540] text-gray-100 font-data text-sm tracking-wider focus:border-amber-500 focus:outline-none placeholder:text-gray-600"
                />
              </div>
            </label>

            {error && (
              <div
                role="alert"
                className="flex items-start gap-2 px-3 py-2 bg-red-500/10 border border-red-700/50 text-red-300 font-data text-[11px]"
              >
                <ShieldAlert className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              iconLeft={<KeyRound className="w-4 h-4" />}
            >
              AUTHORIZE
            </Button>
          </div>

          <footer className="flex items-center gap-2 px-4 py-2.5 border-t border-[#1c2540] font-data text-[10px] text-gray-600 uppercase tracking-[0.18em]">
            <ShieldCheck className="w-3 h-3" />
            TLS // BEARER AUTH // BROWSER LOCALSTORAGE
          </footer>
        </form>

        <p className="mt-5 text-[11px] text-gray-500 leading-relaxed font-data">
          &gt; token printed by{" "}
          <span className="text-amber-400">scripts/dev-up.sh</span> (Mac) or{" "}
          <span className="text-amber-400">scripts/install.sh</span> (Pi).
          stored per-host in browser localStorage; sent as bearer on every
          request.
        </p>
      </div>
    </div>
  );
}
