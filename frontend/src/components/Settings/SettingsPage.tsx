import {
  AlertTriangle,
  Brain,
  Camera,
  EyeOff,
  Gauge,
  KeyRound,
  Play,
  Trash2,
  Workflow,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { Button } from "../ui/Button";
import { LoadingLines } from "../ui/EmptyState";
import { Panel, SectionHeader } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

interface ConfigData {
  retention_days: number;
  auto_delete_on_unenroll: boolean;
  auth_configured: boolean;
  anthropic_configured: boolean;
  groq_configured: boolean;
  /** "db" if set via the dashboard; "env" if from PALANTIR_*; null if unset. */
  anthropic_source: "db" | "env" | null;
  groq_source: "db" | "env" | null;
  /** Last-4 fingerprint, never the full key. */
  anthropic_hint: string | null;
  groq_hint: string | null;
  llm_provider: "anthropic" | "groq" | "none";
  automation_enabled: boolean;
  allow_shell_commands: boolean;
  camera: { width: number; height: number; fps: number };
  engagement: { smoothing_window_seconds: number };
}

interface RetentionResult {
  retention_days: number;
  events_deleted: number;
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 font-data text-[11px]">
      <span className="text-gray-500 uppercase tracking-[0.14em]">{label}</span>
      <span className="text-gray-200 tabular-nums">{children}</span>
    </div>
  );
}

function IntegrationRow({
  ok,
  label,
  description,
  icon: Icon,
  active,
  pillOverride,
}: {
  ok: boolean;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
  /** When true, the row is highlighted as the currently-selected provider. */
  active?: boolean;
  /** Override the default "OK / ATTENTION" pill (used for LLM provider rows). */
  pillOverride?: { tone: "green" | "amber" | "cyan" | "gray" | "red"; label: string };
}) {
  const highlight = active
    ? "border-cyan-700/60 bg-cyan-500/10"
    : ok
      ? "border-emerald-700/50 bg-emerald-500/5"
      : "border-amber-700/50 bg-amber-500/5";
  const iconColor = active
    ? "text-cyan-300"
    : ok
      ? "text-emerald-400"
      : "text-amber-400";
  return (
    <div className="flex items-center gap-3 py-2 border-b border-[#141d35] last:border-0">
      <div
        className={[
          "w-9 h-9 border flex items-center justify-center shrink-0",
          highlight,
        ].join(" ")}
      >
        <Icon className={`w-4 h-4 ${iconColor}`} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-gray-100">{label}</div>
        <div className="text-xs text-gray-500">{description}</div>
      </div>
      {pillOverride ? (
        <StatusPill tone={pillOverride.tone} size="xs" pulse={active}>
          {pillOverride.label}
        </StatusPill>
      ) : (
        <StatusPill tone={ok ? "green" : "amber"} size="xs">
          {ok ? "OK" : "ATTENTION"}
        </StatusPill>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [privacyMode, setPrivacyMode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<RetentionResult | null>(null);
  const [anthropicInput, setAnthropicInput] = useState("");
  const [groqInput, setGroqInput] = useState("");
  const [keysSaving, setKeysSaving] = useState(false);
  const [keysSaved, setKeysSaved] = useState<string | null>(null);
  const [keysError, setKeysError] = useState<string | null>(null);

  const refreshConfig = () =>
    api.get<ConfigData>("/settings/config").then(setConfig).catch(() => {});

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

  const saveApiKeys = async () => {
    setKeysSaving(true);
    setKeysSaved(null);
    setKeysError(null);
    try {
      const body: Record<string, string> = {};
      if (anthropicInput.trim() !== "") body.anthropic_api_key = anthropicInput;
      if (groqInput.trim() !== "") body.groq_api_key = groqInput;
      if (Object.keys(body).length === 0) {
        setKeysError("Enter at least one key (or use 'Clear' to delete one).");
        return;
      }
      const result = await api.post<{ updated: string[] }>(
        "/settings/api_keys",
        body,
      );
      setAnthropicInput("");
      setGroqInput("");
      setKeysSaved(`Saved: ${result.updated.join(", ")}`);
      await refreshConfig();
    } catch (e) {
      const detail = e instanceof Error ? e.message : "Server may be offline.";
      setKeysError(`Save failed -- ${detail}`);
    } finally {
      setKeysSaving(false);
    }
  };

  const clearApiKey = async (field: "anthropic_api_key" | "groq_api_key") => {
    setKeysSaving(true);
    setKeysSaved(null);
    setKeysError(null);
    try {
      await api.post("/settings/api_keys", { [field]: "" });
      setKeysSaved(`Cleared: ${field}`);
      await refreshConfig();
    } catch (e) {
      const detail = e instanceof Error ? e.message : "Server may be offline.";
      setKeysError(`Clear failed -- ${detail}`);
    } finally {
      setKeysSaving(false);
    }
  };

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
      // ignore
    } finally {
      setCleanupRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold text-gray-100">Protocols</h1>
        <Panel label="LOADING" title="Fetching protocols">
          <LoadingLines rows={5} />
        </Panel>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // PROTOCOLS
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Privacy &amp; configuration
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Global toggles for surveillance, data retention, and integrations.
            Hardware configuration is declared in{" "}
            <span className="font-data text-amber-400">
              config/environment.toml
            </span>
            .
          </p>
        </div>
      </div>

      {/* ============ PRIVACY HERO ============ */}
      <Panel
        label="PRIVACY VEIL"
        title="Master surveillance switch"
        tone={privacyMode ? "danger" : "amber"}
        brackets
      >
        <div className="flex items-center justify-between gap-6 flex-wrap">
          <div className="flex items-center gap-4">
            <div
              className={[
                "w-14 h-14 border flex items-center justify-center",
                privacyMode
                  ? "border-red-700/60 bg-red-500/10"
                  : "border-amber-700/50 bg-amber-500/5",
              ].join(" ")}
            >
              <EyeOff
                className={[
                  "w-6 h-6",
                  privacyMode ? "text-red-400" : "text-amber-400 breathe",
                ].join(" ")}
              />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-lg font-semibold text-gray-100">
                  {privacyMode ? "VEIL ENGAGED" : "CAPTURE ACTIVE"}
                </span>
                <StatusPill
                  tone={privacyMode ? "red" : "amber"}
                  size="xs"
                  pulse={!privacyMode}
                >
                  {privacyMode ? "LOCKED" : "LIVE"}
                </StatusPill>
              </div>
              <p className="text-sm text-gray-500 max-w-md mt-0.5">
                {privacyMode
                  ? "Cameras, microphones, and inference paused. Nothing is being observed."
                  : "Cameras, microphones, and inference are running. Events are being recorded."}
              </p>
            </div>
          </div>
          <Button
            variant={privacyMode ? "danger" : "primary"}
            size="lg"
            iconLeft={<EyeOff className="w-4 h-4" />}
            onClick={togglePrivacy}
          >
            {privacyMode ? "DISENGAGE VEIL" : "ENGAGE VEIL"}
          </Button>
        </div>
      </Panel>

      {/* ============ RETENTION ============ */}
      <div>
        <SectionHeader label="RETENTION" title="Data lifecycle" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Panel label="CONFIG" title="Auto-purge">
            <DetailRow label="event retention">
              {config?.retention_days} DAYS
            </DetailRow>
            <DetailRow label="purge on unenroll">
              {config?.auto_delete_on_unenroll ? "YES" : "NO"}
            </DetailRow>
            <DetailRow label="engagement samples">30 DAYS</DetailRow>
            <p className="mt-3 text-xs text-gray-500 leading-relaxed border-t border-[#141d35] pt-3">
              Events older than the retention period are deleted by a nightly
              timer. You can also run the purge manually.
            </p>
          </Panel>

          <Panel
            label="ACTION"
            title="Manual purge"
            meta={
              cleanupRunning ? (
                <StatusPill tone="amber" size="xs" pulse>
                  WORKING
                </StatusPill>
              ) : (
                <StatusPill tone="gray" size="xs">
                  IDLE
                </StatusPill>
              )
            }
          >
            <p className="text-sm text-gray-400 mb-4">
              Immediately delete all events older than{" "}
              <span className="font-data text-amber-400 tabular-nums">
                {config?.retention_days}
              </span>{" "}
              days. Cannot be undone.
            </p>
            <Button
              variant="secondary"
              onClick={runCleanup}
              loading={cleanupRunning}
              disabled={cleanupRunning}
              iconLeft={
                cleanupRunning ? (
                  <Trash2 className="w-4 h-4" />
                ) : (
                  <Play className="w-4 h-4" />
                )
              }
            >
              {cleanupRunning ? "PURGING..." : "EXECUTE PURGE"}
            </Button>
            {cleanupResult && (
              <div className="mt-4 px-3 py-2 bg-emerald-500/10 border border-emerald-700/40 font-data text-[11px] text-emerald-300">
                &gt; {cleanupResult.events_deleted} EVENTS PURGED FROM ARCHIVE
              </div>
            )}
          </Panel>
        </div>
      </div>

      {/* ============ INTEGRATIONS ============ */}
      <Panel label="INTEGRATIONS" title="External services &amp; auth">
        <div className="space-y-0">
          <IntegrationRow
            ok={!!config?.anthropic_configured}
            active={config?.llm_provider === "anthropic"}
            icon={Brain}
            label="Anthropic API"
            description={
              config?.anthropic_configured
                ? config?.llm_provider === "anthropic"
                  ? "Cognitive backend — live"
                  : "Configured but not selected"
                : "ANTHROPIC_API_KEY is not set"
            }
            pillOverride={
              config?.llm_provider === "anthropic"
                ? { tone: "cyan", label: "ACTIVE" }
                : config?.anthropic_configured
                  ? { tone: "gray", label: "STANDBY" }
                  : undefined
            }
          />
          <IntegrationRow
            ok={!!config?.groq_configured}
            active={config?.llm_provider === "groq"}
            icon={Zap}
            label="Groq API"
            description={
              config?.groq_configured
                ? config?.llm_provider === "groq"
                  ? "Cognitive backend — live (free-tier fallback)"
                  : "Configured (Anthropic takes priority)"
                : "GROQ_API_KEY is not set — free alternative to Anthropic"
            }
            pillOverride={
              config?.llm_provider === "groq"
                ? { tone: "cyan", label: "ACTIVE" }
                : config?.groq_configured
                  ? { tone: "gray", label: "STANDBY" }
                  : undefined
            }
          />
          <IntegrationRow
            ok={!!config?.auth_configured}
            icon={KeyRound}
            label="Web authentication"
            description={
              config?.auth_configured
                ? "Bearer token required for all requests"
                : "Auth disabled — development mode"
            }
          />
          <IntegrationRow
            ok={!!config?.automation_enabled}
            icon={Workflow}
            label="Directive engine"
            description={
              config?.automation_enabled
                ? "Rules will fire on matching triggers"
                : "Directives are disabled"
            }
          />
        </div>
        {config?.llm_provider === "none" && (
          <div className="mt-4 flex items-start gap-2 px-3 py-2 bg-amber-500/10 border border-amber-600/60 font-data text-[11px] text-amber-200">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
            <span>
              No cognitive backend configured. Set an API key below or via{" "}
              <strong>ANTHROPIC_API_KEY</strong> /{" "}
              <strong>GROQ_API_KEY</strong> in{" "}
              <span className="font-data text-amber-300">.env</span> to enable
              LLM responses. Without one, the assistant replies with a
              deterministic offline responder.
            </span>
          </div>
        )}
        {config?.allow_shell_commands && (
          <div className="mt-4 flex items-start gap-2 px-3 py-2 bg-amber-500/10 border border-amber-600/60 font-data text-[11px] text-amber-200">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
            <span>
              Shell-command directives are permitted. Rules may execute
              arbitrary commands on this host. Review{" "}
              <strong>allow_shell_commands</strong> before deployment.
            </span>
          </div>
        )}
      </Panel>

      {/* ============ API KEYS ============ */}
      <Panel
        label="API KEYS"
        title="Cognitive backend credentials"
        meta={
          keysSaving ? (
            <StatusPill tone="amber" size="xs" pulse>
              SAVING
            </StatusPill>
          ) : null
        }
      >
        <p className="text-sm text-gray-400 mb-4">
          Stored in the local SQLite settings table. The brain service
          reloads automatically -- no restart needed. Keys are never
          returned to this page; only a last-4 fingerprint is shown.
        </p>

        <div className="space-y-4">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="font-data text-[11px] uppercase tracking-[0.18em] text-gray-400">
                Anthropic API key
              </label>
              <span className="font-data text-[11px] text-gray-500">
                {config?.anthropic_configured
                  ? `${config.anthropic_hint ?? ""} (${config.anthropic_source})`
                  : "not set"}
              </span>
            </div>
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={anthropicInput}
                onChange={(e) => setAnthropicInput(e.target.value)}
                placeholder="sk-ant-..."
                className="flex-1 bg-[#0a0f1f] border border-[#1f2c4a] px-3 py-2 text-sm text-gray-200 font-data placeholder-gray-600 focus:outline-none focus:border-cyan-700"
              />
              {config?.anthropic_configured && (
                <Button
                  variant="danger"
                  size="md"
                  onClick={() => clearApiKey("anthropic_api_key")}
                  disabled={keysSaving}
                >
                  CLEAR
                </Button>
              )}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="font-data text-[11px] uppercase tracking-[0.18em] text-gray-400">
                Groq API key
              </label>
              <span className="font-data text-[11px] text-gray-500">
                {config?.groq_configured
                  ? `${config.groq_hint ?? ""} (${config.groq_source})`
                  : "not set"}
              </span>
            </div>
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={groqInput}
                onChange={(e) => setGroqInput(e.target.value)}
                placeholder="gsk_..."
                className="flex-1 bg-[#0a0f1f] border border-[#1f2c4a] px-3 py-2 text-sm text-gray-200 font-data placeholder-gray-600 focus:outline-none focus:border-cyan-700"
              />
              {config?.groq_configured && (
                <Button
                  variant="danger"
                  size="md"
                  onClick={() => clearApiKey("groq_api_key")}
                  disabled={keysSaving}
                >
                  CLEAR
                </Button>
              )}
            </div>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <Button
              variant="primary"
              onClick={saveApiKeys}
              loading={keysSaving}
              disabled={
                keysSaving ||
                (anthropicInput.trim() === "" && groqInput.trim() === "")
              }
              iconLeft={<KeyRound className="w-4 h-4" />}
            >
              SAVE KEYS
            </Button>
            {keysSaved && (
              <span className="font-data text-[11px] text-emerald-300">
                &gt; {keysSaved}
              </span>
            )}
            {keysError && (
              <span className="font-data text-[11px] text-red-300">
                &gt; {keysError}
              </span>
            )}
          </div>
        </div>

        <p className="mt-4 font-data text-[10px] text-gray-600 uppercase tracking-[0.14em]">
          // db-stored keys override env vars. clear to fall back to .env.
        </p>
      </Panel>

      {/* ============ HARDWARE ============ */}
      <Panel label="HARDWARE" title="Read-only configuration">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div>
            <div className="flex items-center gap-2 mb-2 text-xs text-gray-400 uppercase tracking-[0.18em]">
              <Camera className="w-3.5 h-3.5 text-amber-500" />
              Camera
            </div>
            <DetailRow label="resolution">
              {config?.camera.width} × {config?.camera.height}
            </DetailRow>
            <DetailRow label="framerate">{config?.camera.fps} FPS</DetailRow>
          </div>
          <div>
            <div className="flex items-center gap-2 mb-2 text-xs text-gray-400 uppercase tracking-[0.18em]">
              <Gauge className="w-3.5 h-3.5 text-amber-500" />
              Engagement
            </div>
            <DetailRow label="smoothing window">
              {config?.engagement.smoothing_window_seconds}s
            </DetailRow>
          </div>
        </div>
        <p className="mt-4 font-data text-[10px] text-gray-600 uppercase tracking-[0.14em]">
          // edit config/environment.toml and restart services to change these
        </p>
      </Panel>
    </div>
  );
}
