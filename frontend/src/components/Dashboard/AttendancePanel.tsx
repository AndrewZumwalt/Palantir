import { useEffect, useState } from "react";
import { api } from "../../api/client";

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

export default function AttendancePanel() {
  const [data, setData] = useState<SessionData | null>(null);

  useEffect(() => {
    const load = async () => {
      const result = await api.get<SessionData>("/attendance/current");
      setData(result);
    };
    load();
    const interval = setInterval(load, 15000); // Refresh every 15s
    return () => clearInterval(interval);
  }, []);

  const formatTime = (iso: string) => {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return "Present";
    const m = Math.floor(seconds / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  };

  if (!data?.session) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h2 className="text-lg font-semibold mb-2">Attendance</h2>
        <p className="text-gray-400 text-sm">No active session</p>
      </div>
    );
  }

  const present = data.records.filter((r) => !r.exited_at);
  const departed = data.records.filter((r) => r.exited_at);

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Attendance</h2>
          <p className="text-xs text-gray-500">
            {data.session.name} - Started{" "}
            {formatTime(data.session.started_at)}
          </p>
        </div>
        <span className="text-sm font-medium text-indigo-600">
          {present.length} present
        </span>
      </div>

      <div className="divide-y divide-gray-50">
        {present.map((r) => (
          <div
            key={r.person_id}
            className="px-5 py-3 flex items-center justify-between"
          >
            <div className="flex items-center gap-3">
              <div className="w-2 h-2 rounded-full bg-green-500" />
              <span className="font-medium text-sm">{r.name}</span>
            </div>
            <span className="text-xs text-gray-500">
              Arrived {formatTime(r.entered_at)}
            </span>
          </div>
        ))}

        {departed.length > 0 && (
          <>
            <div className="px-5 py-2 bg-gray-50">
              <span className="text-xs font-medium text-gray-400 uppercase">
                Left
              </span>
            </div>
            {departed.map((r) => (
              <div
                key={r.person_id + r.entered_at}
                className="px-5 py-3 flex items-center justify-between opacity-60"
              >
                <div className="flex items-center gap-3">
                  <div className="w-2 h-2 rounded-full bg-gray-300" />
                  <span className="font-medium text-sm">{r.name}</span>
                </div>
                <span className="text-xs text-gray-500">
                  {formatDuration(r.duration_seconds)}
                </span>
              </div>
            ))}
          </>
        )}

        {data.records.length === 0 && (
          <div className="px-5 py-8 text-center text-gray-400 text-sm">
            No one detected yet
          </div>
        )}
      </div>
    </div>
  );
}
