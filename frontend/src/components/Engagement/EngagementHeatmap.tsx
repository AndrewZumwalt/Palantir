import { Grid3x3 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { EmptyState, LoadingLines } from "../ui/EmptyState";
import { Panel } from "../ui/Panel";

interface HeatmapData {
  session_id: string;
  time_buckets: string[];
  students: {
    person_id: string;
    name: string;
    states: (string | null)[];
  }[];
}

const STATE_CELL_COLORS: Record<string, string> = {
  working: "bg-emerald-500/90",
  collaborating: "bg-cyan-500/90",
  phone: "bg-red-500/90",
  sleeping: "bg-gray-500/70",
  disengaged: "bg-amber-500/90",
  unknown: "bg-gray-700/50",
};

interface Props {
  sessionId: string | null;
}

export default function EngagementHeatmap({ sessionId }: Props) {
  const [data, setData] = useState<HeatmapData | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    api
      .get<HeatmapData>(`/engagement/heatmap/${sessionId}`)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sessionId]);

  if (!sessionId) {
    return (
      <Panel label="HEATMAP" title="Not available">
        <EmptyState
          icon={<Grid3x3 className="w-5 h-5" />}
          title="NO ACTIVE SESSION"
          description="Start a session to accumulate heatmap data."
        />
      </Panel>
    );
  }

  if (loading) {
    return (
      <Panel label="HEATMAP" title="Building matrix">
        <LoadingLines rows={4} />
      </Panel>
    );
  }

  if (!data || data.students.length === 0) {
    return (
      <Panel label="HEATMAP" title="Empty matrix">
        <EmptyState
          icon={<Grid3x3 className="w-5 h-5" />}
          title="NO DATA"
          description="No engagement observations have been recorded for this session."
        />
      </Panel>
    );
  }

  const timeLabels = data.time_buckets.map((t) => {
    const parts = t.split(" ");
    return parts.length > 1 ? parts[1] : t;
  });
  const labelInterval = Math.max(1, Math.ceil(timeLabels.length / 16));

  return (
    <Panel
      label="HEATMAP"
      title="Behavioral matrix"
      meta={
        <span>
          {data.students.length} subjects × {data.time_buckets.length} intervals
        </span>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2 font-data text-[10px] uppercase tracking-[0.14em]">
          {Object.entries(STATE_CELL_COLORS).map(([state, color]) => (
            <span
              key={state}
              className="inline-flex items-center gap-1.5 px-2 py-1 bg-[#05080f] border border-[#1c2540]"
            >
              <span className={["w-2 h-2", color].join(" ")} />
              <span className="text-gray-400">{state}</span>
            </span>
          ))}
        </div>

        <div className="overflow-x-auto">
          <div className="min-w-fit">
            <div className="flex ml-32 mb-1">
              {timeLabels.map((label, i) => (
                <div key={i} className="w-5 flex-shrink-0 text-center">
                  {i % labelInterval === 0 && (
                    <span className="text-[10px] text-gray-500 font-data -rotate-45 inline-block origin-bottom-left whitespace-nowrap">
                      {label}
                    </span>
                  )}
                </div>
              ))}
            </div>

            {data.students.map((student) => (
              <div key={student.person_id} className="flex items-center mb-px">
                <div className="w-32 flex-shrink-0 pr-3 text-right">
                  <span className="text-xs text-gray-300 truncate block">
                    {student.name}
                  </span>
                </div>
                <div className="flex border border-[#1c2540]">
                  {student.states.map((state, i) => (
                    <div
                      key={i}
                      className={[
                        "w-5 h-5 flex-shrink-0",
                        state
                          ? STATE_CELL_COLORS[state] || STATE_CELL_COLORS.unknown
                          : "bg-[#0a0f1c]",
                      ].join(" ")}
                      title={`${student.name} @ ${data.time_buckets[i]}: ${state || "no data"}`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <p className="font-data text-[10px] text-gray-500 uppercase tracking-[0.14em]">
          &gt; each cell = 1 minute; color = dominant state for interval
        </p>
      </div>
    </Panel>
  );
}
