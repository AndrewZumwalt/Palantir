import {
  Activity,
  Brain,
  Camera,
  Database,
  Globe,
  Mic,
  RefreshCw,
  Volume2,
} from "lucide-react";
import type { ComponentType } from "react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { Button } from "../ui/Button";
import { LoadingLines } from "../ui/EmptyState";
import { Select } from "../ui/Field";
import { MetricKPI } from "../ui/MetricKPI";
import { Panel, SectionHeader } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

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

interface ServiceMeta {
  icon: ComponentType<{ className?: string }>;
  description: string;
  label: string;
}

const SERVICE_META: Record<string, ServiceMeta> = {
  audio: {
    icon: Mic,
    description: "Microphone, wake-word, STT, speaker identification",
    label: "AUD-01",
  },
  vision: {
    icon: Camera,
    description: "Camera, face detection, object detection, engagement",
    label: "VIS-02",
  },
  brain: {
    icon: Brain,
    description: "LLM reasoning, context builder, automation engine",
    label: "BRN-03",
  },
  tts: {
    icon: Volume2,
    description: "Text-to-speech synthesis and playback",
    label: "TTS-04",
  },
  eventlog: {
    icon: Database,
    description: "Event persistence, attendance, score aggregation",
    label: "LOG-05",
  },
  web: {
    icon: Globe,
    description: "HTTP API, WebSocket bridge, frontend serving",
    label: "WEB-06",
  },
};

function formatUptime(seconds?: number): string {
  if (seconds === undefined || seconds === null) return "--";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function ServiceCard({ service }: { service: ServiceStatus }) {
  const meta = SERVICE_META[service.name] ?? {
    icon: Activity,
    description: "",
    label: service.name.toUpperCase(),
  };
  const Icon = meta.icon;

  const tone = service.stale ? "gray" : service.healthy ? "green" : "red";
  const statusLabel = service.stale
    ? "NO HEARTBEAT"
    : service.healthy
      ? "HEALTHY"
      : "DEGRADED";

  return (
    <Panel
      label={meta.label}
      title={`palantir-${service.name}`}
      meta={
        <StatusPill tone={tone} size="xs" pulse={service.healthy && !service.stale}>
          {statusLabel}
        </StatusPill>
      }
    >
      <div className="flex items-start gap-3 mb-3">
        <div className="w-10 h-10 border border-[#2a3658] bg-[#05080f] flex items-center justify-center shrink-0">
          <Icon
            className={[
              "w-4 h-4",
              service.stale
                ? "text-gray-600"
                : service.healthy
                  ? "text-amber-400"
                  : "text-red-400",
            ].join(" ")}
          />
        </div>
        <p className="text-xs text-gray-500 leading-relaxed">
          {meta.description}
        </p>
      </div>

      <dl className="space-y-1 font-data text-[11px]">
        <div className="flex justify-between border-t border-[#141d35] pt-1.5">
          <dt className="text-gray-500 uppercase tracking-[0.14em]">uptime</dt>
          <dd className="text-gray-200 tabular-nums">
            {formatUptime(service.uptime_seconds)}
          </dd>
        </div>
        {service.details &&
          Object.entries(service.details).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <dt className="text-gray-500 uppercase tracking-[0.14em]">
                {k.replace(/_/g, " ")}
              </dt>
              <dd className="text-gray-300 text-right truncate ml-3">
                {typeof v === "boolean"
                  ? v
                    ? "YES"
                    : "NO"
                  : String(v)}
              </dd>
            </div>
          ))}
      </dl>
    </Panel>
  );
}

interface CameraScanningResponse {
  scanning: boolean;
  mode: string;
  device: number;
}

interface CameraDevice {
  index: number;
  name: string;
  available: boolean;
  width: number;
  height: number;
  fps: number;
  selected: boolean;
}

interface CameraDevicesResponse {
  selected_device: number;
  devices: CameraDevice[];
}

export default function SystemPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [scanning, setScanning] = useState<boolean | null>(null);
  const [cameraDevice, setCameraDevice] = useState<number | null>(null);
  const [cameraDevices, setCameraDevices] = useState<CameraDevice[]>([]);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [scanningBusy, setScanningBusy] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  const loadCameraDevices = useCallback(async () => {
    setCameraBusy(true);
    try {
      const data = await api.get<CameraDevicesResponse>(
        "/system/camera/devices?max_index=8",
      );
      setCameraDevices(data.devices ?? []);
      setCameraDevice(data.selected_device);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to scan cameras";
      setScanError(message);
    } finally {
      setCameraBusy(false);
    }
  }, []);

  useEffect(() => {
    const load = () => {
      api.get<SystemStatus>("/system/status").then(setStatus).catch(() => {});
      api.get<SystemStats>("/system/stats").then(setStats).catch(() => {});
      api
        .get<CameraScanningResponse>("/system/camera/scanning")
        .then((r) => {
          setScanning(r.scanning);
          setCameraDevice(r.device);
        })
        .catch(() => {});
    };
    load();
    loadCameraDevices();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [loadCameraDevices]);

  const toggleScanning = useCallback(async () => {
    if (scanning === null || scanningBusy) return;
    const next = !scanning;
    setScanningBusy(true);
    setScanError(null);
    // Optimistic UI: flip immediately so the operator gets feedback before
    // the vision service has actually swapped its capture.
    setScanning(next);
    try {
      const r = await api.post<CameraScanningResponse>(
        "/system/camera/scanning",
        { enabled: next, device: cameraDevice },
      );
      setScanning(r.scanning);
      setCameraDevice(r.device);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to toggle camera scanning";
      setScanError(message);
      // Revert the optimistic flip.
      setScanning(!next);
    } finally {
      setScanningBusy(false);
    }
  }, [cameraDevice, scanning, scanningBusy]);

  const selectCameraDevice = useCallback(
    async (value: string) => {
      const next = Number(value);
      if (!Number.isFinite(next) || cameraBusy) return;
      setCameraBusy(true);
      setScanError(null);
      setCameraDevice(next);
      try {
        const r = await api.post<CameraScanningResponse>("/system/camera/device", {
          device: next,
        });
        setCameraDevice(r.device);
        setScanning(r.scanning);
        await loadCameraDevices();
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to select camera";
        setScanError(message);
      } finally {
        setCameraBusy(false);
      }
    },
    [cameraBusy, loadCameraDevices],
  );

  const healthyCount = status?.services.filter(
    (s) => s.healthy && !s.stale
  ).length;
  const total = status?.services.length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // DIAGNOSTICS
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Service integrity
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Live health of the six Palantir microservices. Refresh is
            continuous; stale services indicate a missing heartbeat.
          </p>
        </div>
        <StatusPill
          tone={healthyCount === total && total > 0 ? "green" : "amber"}
          size="sm"
          pulse
        >
          {healthyCount ?? "--"} / {total || "--"} OPERATIONAL
        </StatusPill>
      </div>

      {/* Stats bar */}
      {stats ? (
        <Panel label="LEDGER" title="Archive counters">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-5">
            <MetricKPI label="Persons" value={stats.persons} tone="amber" />
            <MetricKPI label="Sessions" value={stats.sessions} tone="cyan" />
            <MetricKPI label="Events" value={stats.events.toLocaleString()} tone="amber" />
            <MetricKPI
              label="Engagement samples"
              value={stats.engagement_samples.toLocaleString()}
              tone="cyan"
            />
            <MetricKPI
              label="Directives"
              value={stats.automation_rules}
              tone="amber"
            />
            <MetricKPI
              label="Conversations"
              value={stats.conversations}
              tone="cyan"
            />
          </div>
        </Panel>
      ) : (
        <Panel label="LEDGER" title="Archive counters">
          <LoadingLines rows={2} />
        </Panel>
      )}

      {/* Camera scanning toggle */}
      <Panel
        label="OPTICS"
        title="Room scanning"
        meta={
          <StatusPill tone={scanning ? "green" : "gray"} size="xs" pulse={!!scanning}>
            {scanning === null ? "..." : scanning ? "ACTIVE" : "STANDBY"}
          </StatusPill>
        }
      >
        <div className="grid gap-4 lg:grid-cols-[1fr_360px] lg:items-end">
          <div className="text-sm text-gray-400">
            {scanning
              ? "Vision service has the selected local camera open and is detecting faces / engagement live. Browser enrollment may need a different camera or standby mode on Windows."
              : "Vision service is idle (relay mode). Pick a camera, then start scanning when you want the system tracking people in the room."}
          </div>
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <Select
              aria-label="Camera device"
              value={cameraDevice ?? ""}
              onChange={(e) => selectCameraDevice(e.target.value)}
              disabled={cameraBusy}
            >
              {cameraDevices.length === 0 && (
                <option value={cameraDevice ?? 0}>Camera {cameraDevice ?? 0}</option>
              )}
              {cameraDevices.map((device) => (
                <option key={device.index} value={device.index}>
                  {device.name}
                  {device.available
                    ? ` (${device.width}x${device.height})`
                    : " (busy/offline)"}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              onClick={loadCameraDevices}
              loading={cameraBusy}
              disabled={cameraBusy}
              aria-label="Search connected cameras"
              title="Search connected cameras"
            >
              <RefreshCw className="w-4 h-4" />
            </Button>
            <Button
              onClick={toggleScanning}
              disabled={scanning === null || scanningBusy}
              variant={scanning ? "secondary" : "primary"}
              className="col-span-2"
            >
              <Camera className="w-4 h-4" />
              <span className="ml-1.5">
                {scanning === null
                  ? "..."
                  : scanning
                    ? "STOP SCANNING"
                    : "START SCANNING"}
              </span>
            </Button>
          </div>
        </div>
        {scanError && (
          <div className="mt-2 text-xs text-red-300">{scanError}</div>
        )}
      </Panel>

      {/* Services */}
      <div>
        <SectionHeader label="SERVICES" title="Microservice health" />
        {status ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {status.services.map((svc) => (
              <ServiceCard key={svc.name} service={svc} />
            ))}
          </div>
        ) : (
          <Panel label="LOADING" title="Polling services">
            <LoadingLines rows={4} />
          </Panel>
        )}
      </div>
    </div>
  );
}
