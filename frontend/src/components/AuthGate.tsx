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
 * Boot-screen login with a second "sign-in" sequence that plays after the
 * operator submits their token: extra boot lines decode one-by-one, the
 * form CRT-collapses, and a quick amber flash hands off to Layout (which
 * then plays its own power-on animation). All animations respect
 * prefers-reduced-motion.
 */

const BOOT_LINES = [
  "> palantir-observatory boot v0.1",
  "> link: local // encrypted",
  "> signal: nominal",
  "> awaiting operator credential...",
];

const SIGNIN_LINES = [
  "> token received",
  "> handshake ... ok",
  "> decrypting feeds ...",
  "> credential accepted [OK]",
  "> observatory online",
];

const SIGNIN_LINE_MS = 220;
const COLLAPSE_MS = 520;

type Phase = "idle" | "authenticating" | "collapsing";

/**
 * Sessionstorage flag read by Layout to trigger the crt-on-lg animation
 * on first mount after a successful sign-in. Layout clears it after reading.
 */
export const JUST_AUTHED_FLAG = "palantir.justAuthed";

export default function AuthGate({ children }: { children: ReactNode }) {
  const [hasToken, setHasToken] = useState<boolean>(() => !!getAuthToken());
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [reveal, setReveal] = useState(false);
  const [bootStep, setBootStep] = useState(0);
  const [phase, setPhase] = useState<Phase>("idle");
  const [signinStep, setSigninStep] = useState(0);

  useEffect(() => {
    return onAuthFail(() => {
      clearAuthToken();
      setHasToken(false);
      setPhase("idle");
      setSigninStep(0);
      setError("Credential rejected — verify token and retry.");
    });
  }, []);

  // Animate initial boot lines
  useEffect(() => {
    if (hasToken) return;
    if (phase !== "idle") return;
    if (bootStep >= BOOT_LINES.length) return;
    const id = setTimeout(() => setBootStep((s) => s + 1), 280);
    return () => clearTimeout(id);
  }, [bootStep, hasToken, phase]);

  // Animate sign-in lines after submit
  useEffect(() => {
    if (phase !== "authenticating") return;
    if (signinStep >= SIGNIN_LINES.length) {
      // All lines shown — hold briefly then collapse
      const id = setTimeout(() => setPhase("collapsing"), 260);
      return () => clearTimeout(id);
    }
    const id = setTimeout(
      () => setSigninStep((s) => s + 1),
      SIGNIN_LINE_MS,
    );
    return () => clearTimeout(id);
  }, [phase, signinStep]);

  // Handle collapse → hand off (Layout's power-flash-in covers the seam)
  useEffect(() => {
    if (phase !== "collapsing") return;
    const doneId = setTimeout(() => {
      try {
        sessionStorage.setItem(JUST_AUTHED_FLAG, "1");
      } catch {
        // sessionStorage can throw in strict privacy modes — fall through
      }
      setHasToken(true);
    }, COLLAPSE_MS);
    return () => clearTimeout(doneId);
  }, [phase]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (phase !== "idle") return;
    const trimmed = input.trim();
    if (!trimmed) {
      setError("Credential cannot be empty.");
      return;
    }
    setAuthToken(trimmed);
    setError(null);
    setInput("");
    resetWebSocket();
    setPhase("authenticating");
    setSigninStep(0);
  };

  if (hasToken) {
    return <>{children}</>;
  }

  const busy = phase !== "idle";
  const allBootShown = bootStep >= BOOT_LINES.length;

  return (
    <div className="min-h-dvh flex items-center justify-center px-4 py-10 bg-grid bg-[#05080f]/40 relative">
      <div className="w-full max-w-lg">
        {/* Brand mark — during auth, ring pulses harder and eye flickers */}
        <div className="flex flex-col items-center mb-8">
          <div className="relative">
            <div
              className={[
                "w-20 h-20 border rounded-full flex items-center justify-center transition-colors",
                busy ? "border-amber-400" : "border-amber-500/40",
              ].join(" ")}
            >
              <div
                className={[
                  "w-14 h-14 border rounded-full flex items-center justify-center transition-colors",
                  busy ? "border-amber-400 bg-amber-500/10" : "border-amber-500/60",
                ].join(" ")}
              >
                <Eye
                  className={[
                    "w-7 h-7 text-amber-500",
                    busy ? "flicker" : "breathe",
                  ].join(" ")}
                />
              </div>
            </div>
            <div
              className={[
                "absolute inset-0 rounded-full border animate-ping",
                busy ? "border-amber-400/60" : "border-amber-500/20",
              ].join(" ")}
            />
            {busy && (
              <div className="absolute -inset-2 rounded-full border border-amber-500/25 animate-ping [animation-duration:1.2s]" />
            )}
          </div>
          <div className="mt-4 text-center">
            <div
              className={[
                "font-data text-[10px] uppercase tracking-[0.34em]",
                busy ? "text-amber-400 glitch" : "text-amber-500",
              ].join(" ")}
            >
              // PALANTIR
            </div>
            <h1 className="text-2xl font-semibold text-gray-100 mt-1 tracking-tight">
              Observatory
            </h1>
          </div>
        </div>

        {/* Boot readout — initial boot, then sign-in decode after submit */}
        <div className="mb-4 font-data text-[11px] text-gray-500 space-y-0.5 min-h-[72px]">
          {BOOT_LINES.slice(0, bootStep).map((line, i) => (
            <div
              key={`boot-${i}`}
              className={
                i === bootStep - 1 && !busy
                  ? "text-amber-400"
                  : i === BOOT_LINES.length - 1 && allBootShown && !busy
                    ? "text-amber-400"
                    : ""
              }
            >
              {line}
              {i === bootStep - 1 && !allBootShown && (
                <span className="inline-block w-2 h-3 bg-amber-400 ml-1 animate-pulse align-middle" />
              )}
            </div>
          ))}
          {busy &&
            SIGNIN_LINES.slice(0, signinStep).map((line, i) => {
              const isLast = i === signinStep - 1;
              const tone =
                i >= 3 ? "text-emerald-300" : isLast ? "text-amber-300" : "text-gray-400";
              return (
                <div key={`sig-${i}`} className={tone}>
                  {line}
                  {isLast && signinStep < SIGNIN_LINES.length && (
                    <span className="inline-block w-2 h-3 bg-amber-400 ml-1 animate-pulse align-middle" />
                  )}
                </div>
              );
            })}
        </div>

        {/* Form card — applies crt-off when collapsing */}
        <form
          onSubmit={submit}
          className={[
            "relative bg-[#0a0f1c]/90 border border-[#1c2540] bracket-corners",
            phase === "collapsing" ? "crt-off" : "crt-on",
          ].join(" ")}
          aria-busy={busy}
        >
          <header className="flex items-center justify-between px-4 py-2.5 border-b border-[#1c2540]">
            <div className="flex items-center gap-2 font-data text-[10px] uppercase tracking-[0.2em] text-amber-500">
              <Lock className="w-3 h-3" />
              // CREDENTIAL PROMPT
            </div>
            <div className="flex items-center gap-1.5 font-data text-[10px]">
              <span
                className={[
                  "w-1.5 h-1.5 rounded-full pulse-dot",
                  busy ? "bg-amber-400" : "bg-red-500",
                ].join(" ")}
              />
              <span className={busy ? "text-amber-300" : "text-gray-500"}>
                {busy ? "AUTHORIZING" : "LOCKED"}
              </span>
            </div>
          </header>

          <div className="p-5 space-y-4">
            <label className="block">
              <span className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-400 mb-1.5 flex items-center justify-between">
                <span>// AUTHORIZATION TOKEN</span>
                <button
                  type="button"
                  onClick={() => setReveal((r) => !r)}
                  disabled={busy}
                  className="text-gray-500 hover:text-amber-400 normal-case tracking-normal text-[11px] disabled:opacity-40"
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
                  disabled={busy}
                  className="w-full h-11 pl-8 pr-3 bg-[#05080f] border border-[#1c2540] text-gray-100 font-data text-sm tracking-wider focus:border-amber-500 focus:outline-none placeholder:text-gray-600 disabled:opacity-60"
                />
              </div>
            </label>

            {error && !busy && (
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
              loading={busy}
              disabled={busy}
              iconLeft={<KeyRound className="w-4 h-4" />}
            >
              {busy ? "AUTHORIZING..." : "AUTHORIZE"}
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
