import {
  Activity,
  Bell,
  Cctv,
  ChevronsLeft,
  ChevronsRight,
  GaugeCircle,
  LogOut,
  Menu,
  Power,
  ScrollText,
  Settings,
  ShieldCheck,
  UserPlus,
  Users,
  Workflow,
  Eye,
} from "lucide-react";
import type { ComponentType } from "react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, clearAuthToken } from "../api/client";
import { JUST_AUTHED_FLAG } from "./AuthGate";
import { ReloadOverlay } from "./ReloadOverlay";
import { LiveIndicator } from "./ui/LiveIndicator";
import { StatusPill } from "./ui/StatusPill";

interface ActiveReload {
  reload_id: string;
  services: string[];
}

interface NavItem {
  to: string;
  label: string;
  code: string;
  icon: ComponentType<{ className?: string }>;
  desc: string;
}

const NAV: NavItem[] = [
  {
    to: "/",
    label: "Observatory",
    code: "OBS-00",
    icon: Eye,
    desc: "Primary overview",
  },
  {
    to: "/attendance",
    label: "Subject Registry",
    code: "REG-01",
    icon: Users,
    desc: "Present / departed",
  },
  {
    to: "/engagement",
    label: "Behavioral Index",
    code: "BHI-02",
    icon: GaugeCircle,
    desc: "Engagement telemetry",
  },
  {
    to: "/enrollment",
    label: "Subject Intake",
    code: "INK-03",
    icon: UserPlus,
    desc: "Enrollment wizard",
  },
  {
    to: "/automation",
    label: "Directives",
    code: "DIR-04",
    icon: Workflow,
    desc: "Automation rules",
  },
  {
    to: "/events",
    label: "Transmissions",
    code: "TX-05",
    icon: ScrollText,
    desc: "Event log stream",
  },
  {
    to: "/system",
    label: "Diagnostics",
    code: "DGN-06",
    icon: Activity,
    desc: "Service health",
  },
  {
    to: "/settings",
    label: "Protocols",
    code: "PRT-07",
    icon: Settings,
    desc: "Privacy & retention",
  },
];

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function formatTime(d: Date) {
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatDate(d: Date) {
  const y = d.getFullYear();
  const m = (d.getMonth() + 1).toString().padStart(2, "0");
  const day = d.getDate().toString().padStart(2, "0");
  return `${y}.${m}.${day}`;
}

export default function Layout() {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  // Power cycle: when true we're inside a CRT-off → CRT-on reboot.
  // On initial mount, if AuthGate set the just-authed flag, we play the
  // same sequence so the login-to-app handoff feels like "system online".
  const [booting, setBooting] = useState<boolean>(() => {
    try {
      const f = sessionStorage.getItem(JUST_AUTHED_FLAG);
      if (f) {
        sessionStorage.removeItem(JUST_AUTHED_FLAG);
        return true;
      }
    } catch {
      // ignore
    }
    return false;
  });
  const [powerCycling, setPowerCycling] = useState(false);
  // `flashHandoff` starts at peak opacity (matches AuthGate's collapse
  // brightness). `flashRamp` is the ramp-up/ramp-down flash used for the
  // in-app power-cycle easter egg.
  const [flashHandoff, setFlashHandoff] = useState(false);
  const [flashRamp, setFlashRamp] = useState(false);
  // When a power-cycle request is in flight, this holds the server-issued
  // reload_id + target services so <ReloadOverlay/> can show live progress.
  const [activeReload, setActiveReload] = useState<ActiveReload | null>(null);
  const now = useClock();

  const active = NAV.find(
    (n) => n.to === location.pathname || (n.to !== "/" && location.pathname.startsWith(n.to))
  );

  const handleLogout = () => {
    if (confirm("Sign out of Palantir?")) {
      clearAuthToken();
      window.location.reload();
    }
  };

  // Close mobile nav on route change
  useEffect(() => setMobileOpen(false), [location.pathname]);

  // When we first mount after a sign-in, fire the handoff flash (starts
  // at peak, fades over the first beat of the CRT sweep) and hold the
  // crt-on-lg animation for its full 1.6s duration.
  useEffect(() => {
    if (!booting) return;
    setFlashHandoff(true);
    const offFlash = setTimeout(() => setFlashHandoff(false), 720);
    const offBoot = setTimeout(() => setBooting(false), 1650);
    return () => {
      clearTimeout(offFlash);
      clearTimeout(offBoot);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Power cycle: kick off a server-side soft-reload of the backend services
  // (vision/audio/brain/tts/eventlog) and play the CRT animation at the same
  // time so the visual matches the real thing happening on the Pi. The
  // ReloadOverlay shows per-service progress as each one reports in via
  // WebSocket. If the API call fails we still run the CRT animation so the
  // button doesn't feel broken when offline — but we surface the error.
  const powerCycle = async () => {
    if (powerCycling || booting || activeReload) return;
    setPowerCycling(true);
    // Fire-and-wait the reload request before starting the CRT-off so the
    // overlay appears right as the screen comes back on.
    let reload: ActiveReload | null = null;
    try {
      reload = await api.post<ActiveReload>("/system/reload", { services: [] });
    } catch (err) {
      console.error("reload_request_failed", err);
    }
    setTimeout(() => {
      setFlashRamp(true);
      setPowerCycling(false);
      setBooting(true);
      if (reload) setActiveReload(reload);
      setTimeout(() => setFlashRamp(false), 700);
      setTimeout(() => setBooting(false), 1650);
    }, 520);
  };

  const rootAnim = powerCycling
    ? "crt-off"
    : booting
      ? "crt-on-lg"
      : "";

  return (
    <div className={["min-h-dvh flex", rootAnim].filter(Boolean).join(" ")}>
      {flashHandoff && <div className="power-flash-in" aria-hidden="true" />}
      {flashRamp && <div className="power-flash" aria-hidden="true" />}
      {activeReload && (
        <ReloadOverlay
          reloadId={activeReload.reload_id}
          services={activeReload.services}
          onFinished={() => setActiveReload(null)}
        />
      )}

      {/* ===== SIDEBAR ===== */}
      <aside
        className={[
          "hidden md:flex flex-col shrink-0 border-r border-[#1c2540] bg-[#05080f] transition-all duration-200",
          collapsed ? "w-16" : "w-64",
        ].join(" ")}
      >
        {/* Brand */}
        <div className="h-16 flex items-center gap-3 px-4 border-b border-[#1c2540]">
          <div className="relative shrink-0">
            <div className="w-8 h-8 border border-amber-500/60 rounded-full flex items-center justify-center">
              <div className="w-3 h-3 bg-amber-500 rounded-full breathe" />
            </div>
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="font-data text-[10px] uppercase tracking-[0.28em] text-amber-500 glitch">
                // PALANTIR
              </div>
              <div className="text-sm font-semibold text-gray-100">
                Observatory
              </div>
            </div>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto py-3 px-2">
          {!collapsed && (
            <div className="font-data text-[10px] uppercase tracking-[0.22em] text-gray-600 px-2 mb-2">
              // SURVEILLANCE
            </div>
          )}
          <ul className="space-y-0.5">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  title={collapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    [
                      "group flex items-center gap-3 px-2.5 py-2 border border-transparent rounded-sm text-sm transition-colors",
                      isActive
                        ? "bg-amber-500/10 border-amber-600/40 text-amber-200"
                        : "text-gray-400 hover:bg-[#0f1629] hover:text-gray-100 hover:border-[#1c2540]",
                    ].join(" ")
                  }
                >
                  {({ isActive }) => (
                    <>
                      <item.icon
                        className={[
                          "w-4 h-4 shrink-0",
                          isActive ? "text-amber-400" : "text-gray-500 group-hover:text-gray-300",
                        ].join(" ")}
                      />
                      {!collapsed && (
                        <>
                          <span className="flex-1 min-w-0 truncate">{item.label}</span>
                          <span
                            className={[
                              "font-data text-[9px] tracking-[0.14em]",
                              isActive ? "text-amber-500/70" : "text-gray-600",
                            ].join(" ")}
                          >
                            {item.code}
                          </span>
                        </>
                      )}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Footer */}
        <div className="border-t border-[#1c2540] p-3 space-y-2">
          {!collapsed && (
            <div className="flex items-center justify-between font-data text-[10px] uppercase tracking-[0.18em] text-gray-500">
              <span>CLR LVL</span>
              <StatusPill tone="amber" size="xs">OPERATOR</StatusPill>
            </div>
          )}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setCollapsed((c) => !c)}
              className="flex-1 h-8 inline-flex items-center justify-center border border-[#1c2540] text-gray-500 hover:text-amber-400 hover:border-amber-600/50"
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              {collapsed ? (
                <ChevronsRight className="w-4 h-4" />
              ) : (
                <ChevronsLeft className="w-4 h-4" />
              )}
            </button>
            <button
              onClick={handleLogout}
              title="Sign out"
              className="w-8 h-8 inline-flex items-center justify-center border border-[#1c2540] text-gray-500 hover:text-red-400 hover:border-red-700/60"
              aria-label="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* ===== MOBILE SIDEBAR OVERLAY ===== */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 z-50">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/80 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-[#05080f] border-r border-[#1c2540] flex flex-col crt-on">
            <div className="h-14 flex items-center justify-between px-4 border-b border-[#1c2540]">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 bg-amber-500 rounded-full breathe" />
                <span className="font-data text-xs uppercase tracking-[0.24em] text-amber-500">
                  PALANTIR
                </span>
              </div>
              <button
                onClick={() => setMobileOpen(false)}
                className="w-8 h-8 inline-flex items-center justify-center border border-[#1c2540] text-gray-400"
                aria-label="Close menu"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto py-3 px-2">
              <ul className="space-y-0.5">
                {NAV.map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === "/"}
                      className={({ isActive }) =>
                        [
                          "flex items-center gap-3 px-2.5 py-2.5 border border-transparent rounded-sm text-sm",
                          isActive
                            ? "bg-amber-500/10 border-amber-600/40 text-amber-200"
                            : "text-gray-400 hover:bg-[#0f1629] hover:text-gray-100",
                        ].join(" ")
                      }
                    >
                      <item.icon className="w-4 h-4 shrink-0" />
                      <span className="flex-1">{item.label}</span>
                      <span className="font-data text-[9px] text-gray-600">
                        {item.code}
                      </span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </nav>
          </aside>
        </div>
      )}

      {/* ===== MAIN COLUMN ===== */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Command bar */}
        <header className="sticky top-0 z-40 bg-[#05080f]/90 backdrop-blur border-b border-[#1c2540]">
          <div className="flex items-center gap-3 h-16 px-4 md:px-6">
            <button
              onClick={() => setMobileOpen(true)}
              className="md:hidden w-9 h-9 inline-flex items-center justify-center border border-[#1c2540] text-gray-300"
              aria-label="Open menu"
            >
              <Menu className="w-4 h-4" />
            </button>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 font-data text-[10px] uppercase tracking-[0.2em] text-gray-500">
                <Cctv className="w-3 h-3 text-amber-500" />
                <span className="text-amber-500">{active?.code ?? "---"}</span>
                <span>//</span>
                <span>{active?.desc ?? "secure channel"}</span>
              </div>
              <h1 className="text-base md:text-lg font-semibold text-gray-100 truncate">
                {active?.label ?? "Palantir Observatory"}
              </h1>
            </div>

            {/* Right: status cluster */}
            <div className="hidden sm:flex items-center gap-3 font-data text-[11px]">
              <LiveIndicator label="LINK" tone="amber" />
              <div className="h-5 w-px bg-[#1c2540]" />
              <div className="text-right leading-tight">
                <div className="text-gray-200 tabular-nums">
                  {formatTime(now)}
                </div>
                <div className="text-gray-500 text-[10px] uppercase tracking-[0.18em]">
                  {formatDate(now)} UTC
                  {(-now.getTimezoneOffset() / 60).toString()}
                </div>
              </div>
              <div className="h-5 w-px bg-[#1c2540]" />
              <div className="inline-flex items-center gap-1.5 text-gray-400">
                <ShieldCheck className="w-3.5 h-3.5 text-amber-500" />
                <span className="uppercase tracking-[0.18em]">SECURE</span>
              </div>
              <button
                className="w-9 h-9 inline-flex items-center justify-center border border-[#1c2540] text-gray-400 hover:border-amber-600/50 hover:text-amber-400"
                aria-label="Alerts"
              >
                <Bell className="w-4 h-4" />
              </button>
            </div>
          </div>
          {/* Hair-line amber accent that sweeps full-width when idle */}
          <div className="relative h-px bg-[#1c2540] overflow-hidden">
            <div className="absolute inset-0 scan-line" />
          </div>
        </header>

        <main
          key={location.pathname}
          className="flex-1 px-4 md:px-6 py-6 max-w-[1600px] w-full mx-auto page-in"
        >
          <Outlet />
        </main>

        <footer className="border-t border-[#1c2540] px-4 md:px-6 py-3 font-data text-[10px] uppercase tracking-[0.18em] text-gray-600 flex items-center justify-between gap-3">
          <span className="truncate">// PALANTIR OBSERVATORY v0.1 — LOCAL NODE</span>
          <div className="flex items-center gap-3">
            <span className="hidden sm:inline">
              FEEDS ENCRYPTED · ON-DEVICE PROCESSING · NO CLOUD EGRESS
            </span>
            <button
              onClick={powerCycle}
              disabled={powerCycling || booting || activeReload !== null}
              title="Power cycle: reload vision/audio/brain/tts/eventlog"
              aria-label="Power cycle: reload backend services"
              className="inline-flex items-center gap-1 text-gray-600 hover:text-amber-400 disabled:opacity-40 transition-colors"
            >
              <Power className="w-3 h-3" />
              <span>CYCLE</span>
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
