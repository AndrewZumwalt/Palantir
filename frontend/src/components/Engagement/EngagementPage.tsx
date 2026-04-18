import { Activity, Grid3x3, TableProperties } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { EmptyState, LoadingLines } from "../ui/EmptyState";
import { LiveIndicator } from "../ui/LiveIndicator";
import { Panel, SectionHeader } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";
import EngagementHeatmap from "./EngagementHeatmap";

interface EngagementScore {
  person_id: string;
  name: string;
  score: number;
  total_samples: number;
  breakdown: Record<string, number>;
}

interface SessionInfo {
  session_id: string | null;
  scores: EngagementScore[];
}

interface LiveEngagement {
  person_id: string;
  name: string | null;
  state: string;
  confidence: number;
}

type StateKey =
  | "working"
  | "collaborating"
  | "phone"
  | "sleeping"
  | "disengaged"
  | "unknown";

const STATE_TONE: Record<string, "green" | "cyan" | "red" | "gray" | "amber" | "violet"> = {
  working: "green",
  collaborating: "cyan",
  phone: "red",
  sleeping: "gray",
  disengaged: "amber",
  unknown: "gray",
};

const STATE_DOT: Record<string, string> = {
  working: "bg-emerald-400",
  collaborating: "bg-cyan-400",
  phone: "bg-red-400",
  sleeping: "bg-gray-500",
  disengaged: "bg-amber-400",
  unknown: "bg-gray-600",
};

type Tab = "live" | "scores" | "heatmap";

const TABS: { key: Tab; label: string; code: string; icon: typeof Activity }[] = [
  { key: "live", label: "Live feed", code: "01", icon: Activity },
  { key: "scores", label: "Session scores", code: "02", icon: TableProperties },
  { key: "heatmap", label: "Heatmap", code: "03", icon: Grid3x3 },
];

function scoreBand(score: number): "green" | "amber" | "red" {
  if (score < 40) return "red";
  if (score < 70) return "amber";
  return "green";
}

export default function EngagementPage() {
  const [sessionData, setSessionData] = useState<SessionInfo | null>(null);
  const [liveStates, setLiveStates] = useState<LiveEngagement[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("live");
  const { subscribe } = useWebSocket();

  useEffect(() => {
    api
      .get<SessionInfo>("/engagement/current")
      .then(setSessionData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const unsub = subscribe("vision:engagement", (data) => {
      const engagements = data.engagements as LiveEngagement[];
      if (engagements) setLiveStates(engagements);
    });
    return unsub;
  }, [subscribe]);

  useEffect(() => {
    const interval = setInterval(() => {
      api
        .get<SessionInfo>("/engagement/current")
        .then(setSessionData)
        .catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  const visible = liveStates.filter((e) => !e.person_id.startsWith("unknown_"));

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // BEHAVIORAL INDEX
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Engagement telemetry
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Pose-derived classification of subject focus states. Inferred
            locally from the vision pipeline; aggregate scoring is
            per-session only.
          </p>
        </div>
        <LiveIndicator label="STREAMING" tone="amber" />
      </div>

      {/* Tab strip */}
      <div className="flex gap-1 border-b border-[#1c2540]">
        {TABS.map((tab) => {
          const active = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={[
                "relative -mb-px px-4 py-2.5 inline-flex items-center gap-2 border-b-2 font-data text-xs uppercase tracking-[0.16em] transition-colors",
                active
                  ? "border-amber-500 text-amber-400"
                  : "border-transparent text-gray-500 hover:text-gray-200",
              ].join(" ")}
            >
              <tab.icon className="w-3.5 h-3.5" />
              <span className="text-gray-600 mr-1">0{TABS.indexOf(tab) + 1}.</span>
              {tab.label}
            </button>
          );
        })}
      </div>

      {loading ? (
        <Panel label="LOADING" title="Fetching index">
          <LoadingLines rows={4} />
        </Panel>
      ) : activeTab === "live" ? (
        <div className="space-y-4">
          <LegendRow />
          {visible.length === 0 ? (
            <Panel label="LIVE" title="Active subjects">
              <EmptyState
                icon={<Activity className="w-5 h-5" />}
                title="NO BEHAVIORAL DATA"
                description="The vision service needs identified subjects with pose estimation running before states can be classified."
              />
            </Panel>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {visible.map((eng) => (
                <SubjectTile key={eng.person_id} eng={eng} />
              ))}
            </div>
          )}
        </div>
      ) : activeTab === "scores" ? (
        <Panel
          label="SESSION"
          title="Aggregate scores"
          meta={
            <span>
              {sessionData?.scores.length ?? 0} tracked
            </span>
          }
        >
          {!sessionData?.scores.length ? (
            <EmptyState
              icon={<TableProperties className="w-5 h-5" />}
              title="NO SCORES YET"
              description="Session engagement scores aggregate once enough samples are collected."
            />
          ) : (
            <div className="overflow-x-auto -mx-4">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[#1c2540] font-data text-[10px] uppercase tracking-[0.18em] text-gray-500">
                    <th className="px-4 py-2.5 text-left">Subject</th>
                    <th className="px-4 py-2.5 text-left">Score</th>
                    <th className="px-4 py-2.5 text-left">Samples</th>
                    <th className="px-4 py-2.5 text-left">Breakdown</th>
                  </tr>
                </thead>
                <tbody>
                  {sessionData.scores.map((s) => (
                    <tr
                      key={s.person_id}
                      className="border-b border-[#141d35] hover:bg-[#0f1629]"
                    >
                      <td className="px-4 py-3 text-gray-200">{s.name}</td>
                      <td className="px-4 py-3">
                        <ScoreBar score={s.score} />
                      </td>
                      <td className="px-4 py-3 font-data text-gray-400 tabular-nums">
                        {s.total_samples}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          {Object.entries(s.breakdown).map(([state, count]) => (
                            <StatusPill
                              key={state}
                              tone={STATE_TONE[state] || "gray"}
                              size="xs"
                              brackets={false}
                              icon={
                                <span
                                  className={[
                                    "w-1.5 h-1.5 rounded-full",
                                    STATE_DOT[state] || STATE_DOT.unknown,
                                  ].join(" ")}
                                />
                              }
                            >
                              {state} {count as number}
                            </StatusPill>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      ) : (
        <div>
          <SectionHeader label="HEATMAP" title="Time-by-subject matrix" />
          <EngagementHeatmap sessionId={sessionData?.session_id || null} />
        </div>
      )}
    </div>
  );
}

function LegendRow() {
  return (
    <div className="flex flex-wrap gap-2 font-data text-[10px] uppercase tracking-[0.14em]">
      {(["working", "collaborating", "disengaged", "phone", "sleeping", "unknown"] as StateKey[]).map(
        (state) => (
          <span
            key={state}
            className="inline-flex items-center gap-1.5 px-2 py-1 bg-[#0a0f1c] border border-[#1c2540]"
          >
            <span className={["w-2 h-2 rounded-full", STATE_DOT[state]].join(" ")} />
            <span className="text-gray-400">{state}</span>
          </span>
        )
      )}
    </div>
  );
}

function SubjectTile({ eng }: { eng: LiveEngagement }) {
  const tone = STATE_TONE[eng.state] || "gray";
  const pct = Math.round(eng.confidence * 100);
  return (
    <div className="bg-[#0a0f1c] border border-[#1c2540] p-4 hover:border-amber-700/50 transition-colors">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 border border-[#2a3658] bg-[#05080f] flex items-center justify-center">
          <span className="font-data text-sm text-amber-400">
            {(eng.name || "?")[0].toUpperCase()}
          </span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-gray-100 truncate">
            {eng.name || eng.person_id}
          </div>
          <div className="font-data text-[10px] text-gray-500 uppercase tracking-[0.16em]">
            {eng.person_id.slice(0, 14)}
          </div>
        </div>
        <StatusPill tone={tone} size="xs">
          {eng.state}
        </StatusPill>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <div className="flex-1 h-1 bg-[#05080f] border border-[#1c2540] overflow-hidden">
          <div
            className={[
              "h-full",
              tone === "green"
                ? "bg-emerald-400"
                : tone === "cyan"
                  ? "bg-cyan-400"
                  : tone === "red"
                    ? "bg-red-400"
                    : tone === "amber"
                      ? "bg-amber-400"
                      : "bg-gray-500",
            ].join(" ")}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-data text-[10px] text-gray-400 w-10 text-right tabular-nums">
          {pct}%
        </span>
      </div>
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const band = scoreBand(score);
  const color =
    band === "green" ? "bg-emerald-400" : band === "amber" ? "bg-amber-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2 min-w-[140px]">
      <div className="flex-1 h-2 bg-[#05080f] border border-[#1c2540]">
        <div className={["h-full", color].join(" ")} style={{ width: `${score}%` }} />
      </div>
      <span className="font-data text-xs text-gray-300 tabular-nums w-12 text-right">
        {score}%
      </span>
    </div>
  );
}
