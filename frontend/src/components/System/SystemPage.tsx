import { useEffect, useState } from "react";
import { api } from "../../api/client";

interface ServiceStatus {
  name: string;
  healthy: boolean;
  uptime_seconds?: number;
  details?: Record<string, unknown>;
  last_seen?: string | null;
  stale?: boolean;
}

interface SystemStatus {
  services: ServiceStatus[];
  web_uptime_seconds: number;
}

interface SystemStats {
  persons: number;
  sessions: number;
  events: number;
  engagement_samples: number;
  automation_rules: number;
  conversations: number;
}

const SERVICE_DESCRIPTIONS: Record<string, string> = {
  audio: "Microphone capture, wake word, speech-to-text, speaker ID",
  vision: "Camera capture, face detection, object detection, engagement",
  brain: "LLM reasoning, context building, automation engine",
  tts: "Text-to-speech synthesis and playback",
  eventlog: "Event persistence, attendance tracking, score aggregation",
  web: "HTTP API, WebSocket bridge, frontend serving",
};

function formatUptime(seconds?: number): string {
  if (seconds === undefined || seconds === null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function ServiceCard({ service }: { service: ServiceStatus }) {
  const statusColor = service.stale
    ? "bg-gray-200"
    : service.healthy
      ? "bg-emerald-500"
      : "bg-red-500";
  const statusLabel = service.stale
    ? "No heartbeat"
    : service.healthy
      ? "Healthy"
      : "Degraded";

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${statusColor}`} />
          <h3 className="text-base font-semibold capitalize">
            palintir-{service.name}
          </h3>
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
          {statusLabel}
        </span>
      </div>

      <p className="text-xs text-gray-500 mb-3">
        {SERVICE_DESCRIPTIONS[service.name]}
      </p>

      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">Uptime</span>
          <span className="font-mono">{formatUptime(service.uptime_seconds)}</span>
        </div>
        {service.details &&
          Object.entries(service.details).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span className="text-gray-500 capitalize">
                {k.replace(/_/g, " ")}
              </span>
              <span className="font-mono text-gray-700">
                {typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="text-2xl font-bold tracking-tight">
        {value.toLocaleString()}
      </div>
      <div className="text-xs text-gray-500 mt-0.5 capitalize">
        {label.replace(/_/g, " ")}
      </div>
    </div>
  );
}

export default function SystemPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [stats, setStats] = useState<SystemStats | null>(null);

  useEffect(() => {
    const load = () => {
      api.get<SystemStatus>("/system/status").then(setStatus).catch(() => {});
      api.get<SystemStats>("/system/stats").then(setStats).catch(() => {});
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">System Status</h1>
        <p className="text-sm text-gray-500 mt-1">
          Live health of the six Palintir microservices
        </p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatTile label="Persons" value={stats.persons} />
          <StatTile label="Sessions" value={stats.sessions} />
          <StatTile label="Events" value={stats.events} />
          <StatTile
            label="Engagement samples"
            value={stats.engagement_samples}
          />
          <StatTile
            label="Automation rules"
            value={stats.automation_rules}
          />
          <StatTile label="Conversations" value={stats.conversations} />
        </div>
      )}

      {/* Service cards */}
      <div>
        <h2 className="text-base font-semibold text-gray-700 mb-3">Services</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {status?.services.map((svc) => (
            <ServiceCard key={svc.name} service={svc} />
          ))}
        </div>
      </div>

      {!status && (
        <div className="text-center py-12 text-gray-400 animate-pulse">
          Loading system status...
        </div>
      )}
    </div>
  );
}
