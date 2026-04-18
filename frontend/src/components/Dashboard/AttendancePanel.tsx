import { Clock, DoorOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { EmptyState, LoadingLines } from "../ui/EmptyState";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

interface AttendanceRecord {
  person_id: string;
  name: string;
  role: string;
  entered_at: string;
  exited_at: string | null;
  duration_seconds: number | null;
}

interface SessionData {
  session: { id: string; name: string; started_at: string } | null;
  records: AttendanceRecord[];
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "PRESENT";
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export default function AttendancePanel() {
  const [data, setData] = useState<SessionData | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const result = await api.get<SessionData>("/attendance/current");
        setData(result);
      } catch {
        /* ignore */
      } finally {
        setLoaded(true);
      }
    };
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  if (!loaded) {
    return (
      <Panel label="SESSION" title="Attendance log">
        <LoadingLines rows={3} />
      </Panel>
    );
  }

  if (!data?.session) {
    return (
      <Panel label="SESSION" title="Attendance log">
        <EmptyState
          icon={<DoorOpen className="w-5 h-5" />}
          title="NO ACTIVE SESSION"
          description="Start a session from the Directives panel to begin recording."
        />
      </Panel>
    );
  }

  const present = data.records.filter((r) => !r.exited_at);
  const departed = data.records.filter((r) => r.exited_at);

  return (
    <Panel
      label="SESSION"
      title={data.session.name}
      meta={
        <>
          <Clock className="w-3 h-3 opacity-60" />
          <span>{formatTime(data.session.started_at)}</span>
        </>
      }
    >
      <div className="space-y-1">
        {present.map((r) => (
          <div
            key={r.person_id}
            className="flex items-center justify-between gap-3 py-1.5"
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot shrink-0" />
              <span className="text-sm text-gray-200 truncate">{r.name}</span>
            </div>
            <span className="font-data text-[10px] text-gray-500 shrink-0 uppercase tracking-[0.12em]">
              IN @ {formatTime(r.entered_at)}
            </span>
          </div>
        ))}

        {departed.length > 0 && (
          <>
            <div className="pt-3 mt-2 border-t border-[#141d35]">
              <div className="font-data text-[10px] uppercase tracking-[0.2em] text-gray-600 mb-2">
                // DEPARTED
              </div>
              {departed.map((r) => (
                <div
                  key={r.person_id + r.entered_at}
                  className="flex items-center justify-between gap-3 py-1.5 opacity-50"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-gray-600 shrink-0" />
                    <span className="text-sm text-gray-400 truncate">
                      {r.name}
                    </span>
                  </div>
                  <StatusPill tone="gray" size="xs">
                    {formatDuration(r.duration_seconds)}
                  </StatusPill>
                </div>
              ))}
            </div>
          </>
        )}

        {data.records.length === 0 && (
          <div className="py-8 text-center font-data text-xs text-gray-500">
            &gt; AWAITING FIRST DETECTION
          </div>
        )}
      </div>
    </Panel>
  );
}
