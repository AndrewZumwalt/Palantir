import { EyeOff, Radio, Users, Wifi } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AttendanceData, HealthStatus } from "../../api/types";
import { useWebSocket } from "../../hooks/useWebSocket";
import { Button } from "../ui/Button";
import { LoadingLines } from "../ui/EmptyState";
import { MetricKPI } from "../ui/MetricKPI";
import { Panel, SectionHeader } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";
import AttendancePanel from "./AttendancePanel";

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [attendance, setAttendance] = useState<AttendanceData | null>(null);
  const [privacyMode, setPrivacyMode] = useState(false);
  const { connected, subscribe } = useWebSocket();

  useEffect(() => {
    api.get<HealthStatus>("/health").then(setHealth).catch(() => {});
    api
      .get<AttendanceData>("/dashboard/attendance")
      .then(setAttendance)
      .catch(() => {});
    api
      .get<{ privacy_mode: boolean }>("/settings/privacy")
      .then((data) => setPrivacyMode(data.privacy_mode))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const unsub = subscribe("system:privacy", (data) => {
      if (typeof data.enabled === "boolean") {
        setPrivacyMode(data.enabled);
      }
    });
    return unsub;
  }, [subscribe]);

  const togglePrivacy = useCallback(async () => {
    const newState = !privacyMode;
    await api.post(`/settings/privacy?enabled=${newState}`);
    setPrivacyMode(newState);
  }, [privacyMode]);

  const healthy = health?.status === "ok";

  return (
    <div className="space-y-6 stagger-in">
      {/* ============== HEADLINE ============== */}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // REAL-TIME OVERVIEW
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Classroom Observatory
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Live subject presence, behavioral index, and system integrity.
            All processing is performed on-device; no data leaves this node.
          </p>
        </div>

        <Button
          variant={privacyMode ? "danger" : "secondary"}
          size="md"
          iconLeft={<EyeOff className="w-4 h-4" />}
          onClick={togglePrivacy}
        >
          PRIVACY VEIL // {privacyMode ? "ENGAGED" : "OPEN"}
        </Button>
      </div>

      {/* ============== HERO KPIs ============== */}
      <Panel
        label="TELEMETRY"
        title="Primary readings"
        meta={<StatusPill tone="amber" pulse size="xs">LIVE</StatusPill>}
        tone="amber"
        brackets
      >
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
          <MetricKPI
            label="Subjects in frame"
            value={attendance?.count ?? 0}
            unit="persons"
            tone="amber"
            foot={
              <span className="inline-flex items-center gap-1">
                <Users className="w-3 h-3" /> currently present
              </span>
            }
          />
          <MetricKPI
            label="Node uptime"
            value={
              health ? formatUptime(health.uptime_seconds) : "--"
            }
            tone={healthy ? "cyan" : "red"}
            foot={healthy ? "all services nominal" : "service degraded"}
          />
          <MetricKPI
            label="Link integrity"
            value={connected ? "OK" : "DOWN"}
            tone={connected ? "green" : "red"}
            foot={
              health
                ? `${health.ws_clients} peer${health.ws_clients === 1 ? "" : "s"} attached`
                : "--"
            }
          />
          <MetricKPI
            label="Privacy veil"
            value={privacyMode ? "ON" : "OFF"}
            tone={privacyMode ? "red" : "gray"}
            foot={privacyMode ? "capture suspended" : "capture active"}
          />
        </div>
      </Panel>

      {/* ============== STATUS STRIP ============== */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 font-data text-[11px]">
        <SystemStat
          icon={<Wifi className="w-3.5 h-3.5" />}
          label="WEBSOCKET"
          value={connected ? "attached" : "reconnecting"}
          ok={connected}
        />
        <SystemStat
          icon={<Radio className="w-3.5 h-3.5" />}
          label="BUS"
          value={healthy ? "streaming" : "silent"}
          ok={!!healthy}
        />
        <SystemStat
          icon={<Users className="w-3.5 h-3.5" />}
          label="REGISTRY"
          value={`${attendance?.count ?? 0} active`}
          ok={(attendance?.count ?? 0) > 0}
        />
        <SystemStat
          icon={<EyeOff className="w-3.5 h-3.5" />}
          label="VEIL"
          value={privacyMode ? "engaged" : "open"}
          ok={!privacyMode}
        />
      </div>

      {/* ============== SESSION FEED ============== */}
      <div>
        <SectionHeader label="SESSION FEED" title="Present subjects" />
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <div className="lg:col-span-3">
            <Panel
              label="REGISTRY"
              title="Detected persons"
              meta={
                <span className="text-amber-400 tabular-nums">
                  {attendance?.count ?? 0} present
                </span>
              }
            >
              {attendance?.present.length ? (
                <ul className="divide-y divide-[#141d35]">
                  {attendance.present.map((person, i) => (
                    <li
                      key={person.id}
                      className="py-2.5 flex items-center gap-3 hover:bg-[#0f1629] px-2 -mx-2"
                    >
                      <span className="font-data text-[10px] text-gray-600 tabular-nums w-8">
                        {(i + 1).toString().padStart(3, "0")}
                      </span>
                      <div className="w-8 h-8 border border-[#2a3658] flex items-center justify-center bg-[#0a0f1c]">
                        <span className="font-data text-xs text-amber-400">
                          {person.name.charAt(0).toUpperCase()}
                        </span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-gray-200 truncate">
                          {person.name}
                        </div>
                        <div className="font-data text-[10px] text-gray-500 uppercase tracking-[0.12em]">
                          id: {person.id.slice(0, 12)}
                        </div>
                      </div>
                      <StatusPill
                        tone={person.role === "teacher" ? "cyan" : "gray"}
                        size="xs"
                      >
                        {person.role}
                      </StatusPill>
                    </li>
                  ))}
                </ul>
              ) : health ? (
                <div className="py-10 text-center font-data text-xs text-gray-500">
                  &gt; NO SUBJECTS IN FRAME
                </div>
              ) : (
                <LoadingLines rows={4} />
              )}
            </Panel>
          </div>
          <div className="lg:col-span-2">
            <AttendancePanel />
          </div>
        </div>
      </div>
    </div>
  );
}

function SystemStat({
  icon,
  label,
  value,
  ok,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <div
      className={[
        "flex items-center gap-2 px-3 py-2 border bg-[#0a0f1c]",
        ok ? "border-[#1c2540]" : "border-red-800/50",
      ].join(" ")}
    >
      <span className={ok ? "text-amber-500" : "text-red-400"}>{icon}</span>
      <span className="text-gray-500 uppercase tracking-[0.16em]">{label}</span>
      <span className="flex-1 text-right text-gray-200 uppercase">{value}</span>
    </div>
  );
}
