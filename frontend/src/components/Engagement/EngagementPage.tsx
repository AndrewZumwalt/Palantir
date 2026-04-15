import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
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

const STATE_COLORS: Record<string, string> = {
  working: "bg-emerald-100 text-emerald-800",
  collaborating: "bg-blue-100 text-blue-800",
  phone: "bg-red-100 text-red-800",
  sleeping: "bg-gray-200 text-gray-700",
  disengaged: "bg-amber-100 text-amber-800",
  unknown: "bg-gray-100 text-gray-500",
};

const STATE_DOTS: Record<string, string> = {
  working: "bg-emerald-500",
  collaborating: "bg-blue-500",
  phone: "bg-red-500",
  sleeping: "bg-gray-400",
  disengaged: "bg-amber-500",
  unknown: "bg-gray-300",
};

function ScoreBadge({ score }: { score: number }) {
  let color = "bg-emerald-50 text-emerald-700 ring-emerald-600/20";
  if (score < 40) color = "bg-red-50 text-red-700 ring-red-600/20";
  else if (score < 70) color = "bg-amber-50 text-amber-700 ring-amber-600/20";

  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${color}`}
    >
      {score}%
    </span>
  );
}

export default function EngagementPage() {
  const [sessionData, setSessionData] = useState<SessionInfo | null>(null);
  const [liveStates, setLiveStates] = useState<LiveEngagement[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"live" | "scores" | "heatmap">(
    "live",
  );
  const { subscribe } = useWebSocket();

  useEffect(() => {
    api
      .get<SessionInfo>("/engagement/current")
      .then(setSessionData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Subscribe to real-time engagement updates
  useEffect(() => {
    const unsub = subscribe(
      "vision:engagement",
      (data: Record<string, unknown>) => {
        const engagements = data.engagements as LiveEngagement[];
        if (engagements) {
          setLiveStates(engagements);
        }
      },
    );
    return unsub;
  }, [subscribe]);

  // Refresh scores periodically
  useEffect(() => {
    const interval = setInterval(() => {
      api
        .get<SessionInfo>("/engagement/current")
        .then(setSessionData)
        .catch(() => {});
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-pulse text-gray-400">
          Loading engagement data...
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          Engagement Tracking
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Real-time student engagement classification and scoring
        </p>
      </div>

      {/* Tab navigation */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {(["live", "scores", "heatmap"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 text-sm font-medium border-b-2 transition-colors capitalize ${
                activeTab === tab
                  ? "border-indigo-500 text-indigo-600"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
              }`}
            >
              {tab === "live"
                ? "Live View"
                : tab === "scores"
                  ? "Session Scores"
                  : "Heatmap"}
            </button>
          ))}
        </nav>
      </div>

      {/* Live engagement states */}
      {activeTab === "live" && (
        <div className="space-y-4">
          {/* Legend */}
          <div className="flex flex-wrap gap-3">
            {Object.entries(STATE_DOTS).map(([state, dot]) => (
              <div key={state} className="flex items-center gap-1.5 text-xs">
                <span className={`w-2.5 h-2.5 rounded-full ${dot}`} />
                <span className="capitalize text-gray-600">{state}</span>
              </div>
            ))}
          </div>

          {liveStates.length === 0 ? (
            <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
              <p className="text-gray-400 text-sm">
                No engagement data yet. The vision service needs to detect
                identified students with pose estimation active.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {liveStates
                .filter((e) => !e.person_id.startsWith("unknown_"))
                .map((eng) => (
                  <div
                    key={eng.person_id}
                    className="bg-white rounded-xl border border-gray-200 p-4 flex items-center gap-4"
                  >
                    <div
                      className={`w-10 h-10 rounded-full flex items-center justify-center ${STATE_COLORS[eng.state] || STATE_COLORS.unknown}`}
                    >
                      <span className="text-sm font-semibold">
                        {(eng.name || "?")[0].toUpperCase()}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {eng.name || eng.person_id}
                      </p>
                      <p className="text-xs text-gray-500 capitalize">
                        {eng.state}
                      </p>
                    </div>
                    <div className="text-right">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${STATE_COLORS[eng.state] || STATE_COLORS.unknown}`}
                      >
                        {Math.round(eng.confidence * 100)}%
                      </span>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Session scores */}
      {activeTab === "scores" && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {!sessionData?.scores.length ? (
            <div className="p-12 text-center">
              <p className="text-gray-400 text-sm">
                No engagement scores computed for this session yet.
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Student
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Score
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Samples
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Breakdown
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {sessionData.scores.map((s) => (
                  <tr key={s.person_id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {s.name}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <ScoreBadge score={s.score} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {s.total_samples}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex gap-1.5 flex-wrap">
                        {Object.entries(s.breakdown).map(([state, count]) => (
                          <span
                            key={state}
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ${STATE_COLORS[state] || STATE_COLORS.unknown}`}
                          >
                            <span className="capitalize">{state}</span>
                            <span className="font-mono">
                              {count as number}
                            </span>
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Heatmap */}
      {activeTab === "heatmap" && (
        <EngagementHeatmap sessionId={sessionData?.session_id || null} />
      )}
    </div>
  );
}
