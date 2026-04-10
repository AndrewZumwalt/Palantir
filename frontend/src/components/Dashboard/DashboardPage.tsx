import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AttendanceData, HealthStatus } from "../../api/types";
import { useWebSocket } from "../../hooks/useWebSocket";
import StatusCard from "./StatusCard";

export default function DashboardPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [attendance, setAttendance] = useState<AttendanceData | null>(null);
  const [privacyMode, setPrivacyMode] = useState(false);
  const { connected, subscribe } = useWebSocket();

  // Fetch initial data
  useEffect(() => {
    api.get<HealthStatus>("/health").then(setHealth).catch(() => {});
    api
      .get<AttendanceData>("/dashboard/attendance")
      .then(setAttendance)
      .catch(() => {});
    api
      .get<{ privacy_mode: boolean }>("/settings/privacy")
      .then((data) => setPrivacyMode(data.privacy_mode))
      .catch(() => {});
  }, []);

  // Subscribe to real-time updates
  useEffect(() => {
    const unsub = subscribe("system:privacy", (data) => {
      if (typeof data.enabled === "boolean") {
        setPrivacyMode(data.enabled);
      }
    });
    return unsub;
  }, [subscribe]);

  const togglePrivacy = useCallback(async () => {
    const newState = !privacyMode;
    await api.post(`/settings/privacy?enabled=${newState}`);
    setPrivacyMode(newState);
  }, [privacyMode]);

  const formatUptime = (seconds: number): string => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-gray-500 mt-1">
            Real-time classroom overview
          </p>
        </div>

        {/* Privacy mode toggle */}
        <button
          onClick={togglePrivacy}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            privacyMode
              ? "bg-red-100 text-red-700 hover:bg-red-200"
              : "bg-gray-100 text-gray-700 hover:bg-gray-200"
          }`}
        >
          {privacyMode ? "Privacy Mode ON" : "Privacy Mode OFF"}
        </button>
      </div>

      {/* Status cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatusCard
          title="System"
          value={health?.status === "ok" ? "Online" : "Offline"}
          subtitle={
            health ? `Uptime: ${formatUptime(health.uptime_seconds)}` : undefined
          }
          status={health?.status === "ok" ? "ok" : "error"}
        />
        <StatusCard
          title="Students Present"
          value={attendance?.count ?? 0}
          subtitle="Currently in classroom"
        />
        <StatusCard
          title="WebSocket"
          value={connected ? "Connected" : "Disconnected"}
          subtitle={
            health ? `${health.ws_clients} client(s)` : undefined
          }
          status={connected ? "ok" : "warning"}
        />
        <StatusCard
          title="Privacy"
          value={privacyMode ? "Active" : "Inactive"}
          subtitle={
            privacyMode ? "All capture paused" : "System recording"
          }
          status={privacyMode ? "warning" : "ok"}
        />
      </div>

      {/* Attendance list */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold">Present</h2>
        </div>
        <div className="divide-y divide-gray-50">
          {attendance?.present.length ? (
            attendance.present.map((person) => (
              <div
                key={person.id}
                className="px-5 py-3 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center">
                    <span className="text-indigo-700 font-medium text-sm">
                      {person.name.charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <span className="font-medium">{person.name}</span>
                </div>
                <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-600 capitalize">
                  {person.role}
                </span>
              </div>
            ))
          ) : (
            <div className="px-5 py-12 text-center text-gray-400">
              No one detected in the classroom
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
