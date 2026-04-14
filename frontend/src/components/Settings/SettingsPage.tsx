import { useEffect, useState } from "react";
import { api } from "../../api/client";

interface ConfigData {
  retention_days: number;
  auto_delete_on_unenroll: boolean;
  auth_configured: boolean;
  anthropic_configured: boolean;
  automation_enabled: boolean;
  allow_shell_commands: boolean;
  camera: {
    width: number;
    height: number;
    fps: number;
  };
  engagement: {
    smoothing_window_seconds: number;
  };
}

interface RetentionResult {
  retention_days: number;
  events_deleted: number;
}

function SettingCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <h3 className="text-base font-semibold text-gray-900">{title}</h3>
      {description && (
        <p className="text-sm text-gray-500 mt-1 mb-4">{description}</p>
      )}
      <div className={description ? "" : "mt-4"}>{children}</div>
    </div>
  );
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={`w-2 h-2 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`}
      />
      <span className="text-sm text-gray-700">{label}</span>
    </div>
  );
}

export default function SettingsPage() {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [privacyMode, setPrivacyMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<RetentionResult | null>(
    null,
  );

  useEffect(() => {
    Promise.all([
      api.get<ConfigData>("/settings/config"),
      api.get<{ privacy_mode: boolean }>("/settings/privacy"),
    ])
      .then(([cfg, priv]) => {
        setConfig(cfg);
        setPrivacyMode(priv.privacy_mode);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const togglePrivacy = async () => {
    const newState = !privacyMode;
    await api.post(`/settings/privacy?enabled=${newState}`);
    setPrivacyMode(newState);
  };

  const runCleanup = async () => {
    setCleanupRunning(true);
    setCleanupResult(null);
    try {
      const result = await api.post<RetentionResult>("/system/retention/cleanup");
      setCleanupResult(result);
    } catch {
      // Ignore
    } finally {
      setCleanupRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 animate-pulse">
        Loading settings...
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-gray-500 mt-1">
          Privacy, data retention, and system configuration
        </p>
      </div>

      {/* Privacy mode */}
      <SettingCard
        title="Privacy Mode"
        description="When enabled, cameras, microphones, and AI processing are paused. All identification is disabled until you turn it off."
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-900">
              Classroom recording
            </p>
            <p className="text-xs text-gray-500 mt-0.5">
              {privacyMode
                ? "Paused — no audio/video is being processed"
                : "Active — system is listening and watching"}
            </p>
          </div>
          <button
            onClick={togglePrivacy}
            className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
              privacyMode ? "bg-red-500" : "bg-emerald-500"
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                privacyMode ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>
      </SettingCard>

      {/* Data retention */}
      <SettingCard
        title="Data Retention"
        description="Events older than the retention period are automatically deleted. Engagement samples are kept for 30 days."
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-700">
              Event retention period
            </span>
            <span className="text-sm font-mono font-medium">
              {config?.retention_days} days
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-700">
              Delete data when unenrolling
            </span>
            <span className="text-sm font-mono font-medium">
              {config?.auto_delete_on_unenroll ? "Yes" : "No"}
            </span>
          </div>
          <div className="pt-3 border-t border-gray-100 flex items-center justify-between">
            <span className="text-xs text-gray-500">
              Run cleanup manually
            </span>
            <button
              onClick={runCleanup}
              disabled={cleanupRunning}
              className="px-3 py-1.5 text-sm rounded-md border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-50"
            >
              {cleanupRunning ? "Running..." : "Run Now"}
            </button>
          </div>
          {cleanupResult && (
            <div className="text-xs text-emerald-700 bg-emerald-50 rounded px-2 py-1.5">
              Deleted {cleanupResult.events_deleted} old events.
            </div>
          )}
        </div>
      </SettingCard>

      {/* System integrations */}
      <SettingCard
        title="System Integrations"
        description="External services and authentication configured via environment."
      >
        <div className="space-y-2">
          <StatusDot
            ok={!!config?.anthropic_configured}
            label={
              config?.anthropic_configured
                ? "Anthropic API configured"
                : "Anthropic API key missing — set ANTHROPIC_API_KEY"
            }
          />
          <StatusDot
            ok={!!config?.auth_configured}
            label={
              config?.auth_configured
                ? "Web authentication enabled"
                : "No auth token configured (development mode)"
            }
          />
          <StatusDot
            ok={!!config?.automation_enabled}
            label={
              config?.automation_enabled
                ? "Automation engine enabled"
                : "Automation engine disabled"
            }
          />
          {config?.allow_shell_commands && (
            <div className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1.5 mt-2">
              Warning: shell command automation is enabled. Rules can execute
              arbitrary shell commands.
            </div>
          )}
        </div>
      </SettingCard>

      {/* Hardware info */}
      <SettingCard
        title="Hardware Configuration"
        description="Read-only view of the current camera and engagement settings. Modify via config/environment.toml."
      >
        <div className="grid grid-cols-2 gap-y-2 text-sm">
          <span className="text-gray-600">Camera resolution</span>
          <span className="font-mono text-right">
            {config?.camera.width} × {config?.camera.height}
          </span>
          <span className="text-gray-600">Camera FPS</span>
          <span className="font-mono text-right">{config?.camera.fps}</span>
          <span className="text-gray-600">Engagement smoothing</span>
          <span className="font-mono text-right">
            {config?.engagement.smoothing_window_seconds}s
          </span>
        </div>
      </SettingCard>
    </div>
  );
}
