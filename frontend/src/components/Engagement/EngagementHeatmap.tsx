import { useEffect, useState } from "react";
import { api } from "../../api/client";

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
  working: "bg-emerald-400",
  collaborating: "bg-blue-400",
  phone: "bg-red-400",
  sleeping: "bg-gray-300",
  disengaged: "bg-amber-400",
  unknown: "bg-gray-200",
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
      <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
        <p className="text-gray-400 text-sm">No active session.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="animate-pulse text-gray-400">
          Building heatmap...
        </div>
      </div>
    );
  }

  if (!data || data.students.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
        <p className="text-gray-400 text-sm">
          No engagement data for this session yet.
        </p>
      </div>
    );
  }

  // Format time labels (show only minutes like "14:05")
  const timeLabels = data.time_buckets.map((t) => {
    const parts = t.split(" ");
    return parts.length > 1 ? parts[1] : t;
  });

  // Only show every Nth label to avoid crowding
  const labelInterval = Math.max(1, Math.ceil(timeLabels.length / 20));

  return (
    <div className="space-y-4">
      {/* Legend */}
      <div className="flex flex-wrap gap-3">
        {Object.entries(STATE_CELL_COLORS).map(([state, color]) => (
          <div key={state} className="flex items-center gap-1.5 text-xs">
            <span className={`w-3 h-3 rounded-sm ${color}`} />
            <span className="capitalize text-gray-600">{state}</span>
          </div>
        ))}
      </div>

      {/* Heatmap grid */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
        <div className="min-w-fit p-4">
          {/* Time axis labels */}
          <div className="flex ml-32 mb-1">
            {timeLabels.map((label, i) => (
              <div
                key={i}
                className="w-5 flex-shrink-0 text-center"
              >
                {i % labelInterval === 0 ? (
                  <span className="text-[10px] text-gray-400 -rotate-45 inline-block origin-bottom-left whitespace-nowrap">
                    {label}
                  </span>
                ) : null}
              </div>
            ))}
          </div>

          {/* Student rows */}
          {data.students.map((student) => (
            <div key={student.person_id} className="flex items-center mb-px">
              {/* Student name */}
              <div className="w-32 flex-shrink-0 pr-3 text-right">
                <span className="text-xs font-medium text-gray-700 truncate block">
                  {student.name}
                </span>
              </div>

              {/* State cells */}
              <div className="flex">
                {student.states.map((state, i) => (
                  <div
                    key={i}
                    className={`w-5 h-5 flex-shrink-0 ${
                      state
                        ? STATE_CELL_COLORS[state] || STATE_CELL_COLORS.unknown
                        : "bg-gray-50"
                    } ${i === 0 ? "rounded-l" : ""} ${i === student.states.length - 1 ? "rounded-r" : ""}`}
                    title={`${student.name} @ ${data.time_buckets[i]}: ${state || "no data"}`}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <p className="text-xs text-gray-400">
        Each cell represents one minute. Color indicates the dominant engagement
        state during that interval.
      </p>
    </div>
  );
}
