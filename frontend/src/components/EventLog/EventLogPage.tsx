import { useEffect, useState, useCallback } from "react";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";

interface Event {
  id: number;
  type: string;
  person_id: string | null;
  person_name: string | null;
  data: Record<string, unknown> | string | null;
  created_at: string;
}

interface EventsResponse {
  events: Event[];
  total: number;
  limit: number;
  offset: number;
}

const EVENT_TYPE_COLORS: Record<string, string> = {
  person_entered: "bg-emerald-100 text-emerald-700",
  person_exited: "bg-gray-100 text-gray-600",
  utterance: "bg-blue-100 text-blue-700",
  response: "bg-indigo-100 text-indigo-700",
  object_detected: "bg-purple-100 text-purple-700",
  engagement_change: "bg-amber-100 text-amber-700",
  automation_triggered: "bg-rose-100 text-rose-700",
  privacy_toggled: "bg-red-100 text-red-700",
  system_error: "bg-red-100 text-red-700",
};

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}

function EventDataPreview({ data }: { data: Event["data"] }) {
  if (!data) return null;
  if (typeof data === "string") {
    return <span className="text-gray-500 text-xs">{data}</span>;
  }

  const entries = Object.entries(data).filter(
    ([, v]) => typeof v !== "object" || v === null,
  );
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {entries.slice(0, 4).map(([k, v]) => (
        <span
          key={k}
          className="text-xs text-gray-500 bg-gray-50 rounded px-1.5 py-0.5 max-w-md truncate"
        >
          <span className="font-medium text-gray-600">{k}:</span>{" "}
          {String(v).slice(0, 80)}
        </span>
      ))}
    </div>
  );
}

export default function EventLogPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { subscribe } = useWebSocket();

  const limit = 50;

  const load = useCallback(
    async (resetOffset = false) => {
      setLoading(true);
      const nextOffset = resetOffset ? 0 : offset;
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(nextOffset),
      });
      if (selectedTypes.size > 0) {
        params.set("type", Array.from(selectedTypes).join(","));
      }

      try {
        const res = await api.get<EventsResponse>(
          `/events?${params.toString()}`,
        );
        setEvents(res.events);
        setTotal(res.total);
        if (resetOffset) setOffset(0);
      } catch {
        // Ignore
      } finally {
        setLoading(false);
      }
    },
    [selectedTypes, offset],
  );

  useEffect(() => {
    api
      .get<{ types: string[] }>("/events/types")
      .then((d) => setTypes(d.types))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load(true);
  }, [selectedTypes]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    load(false);
  }, [offset]); // eslint-disable-line react-hooks/exhaustive-deps

  // Real-time: prepend new events as they arrive
  useEffect(() => {
    if (!autoRefresh) return;
    const unsub = subscribe("events:log", () => {
      // Simplest approach: reload first page if currently on first page
      if (offset === 0) load(true);
    });
    return unsub;
  }, [subscribe, autoRefresh, offset, load]);

  const toggleType = (type: string) => {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const clearFilters = () => setSelectedTypes(new Set());

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Event Log</h1>
          <p className="text-sm text-gray-500 mt-1">
            {total.toLocaleString()} events recorded
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          />
          Auto-refresh
        </label>
      </div>

      {/* Type filters */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-700">
            Filter by type
          </span>
          {selectedTypes.size > 0 && (
            <button
              onClick={clearFilters}
              className="text-xs text-indigo-600 hover:text-indigo-800"
            >
              Clear
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {types.map((type) => (
            <button
              key={type}
              onClick={() => toggleType(type)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                selectedTypes.has(type)
                  ? EVENT_TYPE_COLORS[type] ||
                    "bg-indigo-100 text-indigo-700"
                  : "bg-gray-50 text-gray-600 hover:bg-gray-100"
              }`}
            >
              {type.replace(/_/g, " ")}
            </button>
          ))}
          {types.length === 0 && (
            <span className="text-sm text-gray-400">No events yet</span>
          )}
        </div>
      </div>

      {/* Event list */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading && events.length === 0 ? (
          <div className="p-12 text-center text-gray-400">Loading events...</div>
        ) : events.length === 0 ? (
          <div className="p-12 text-center text-gray-400">
            No events match the current filters.
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {events.map((event) => (
              <div key={event.id} className="px-5 py-3 hover:bg-gray-50">
                <div className="flex items-center gap-3">
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs font-medium whitespace-nowrap ${
                      EVENT_TYPE_COLORS[event.type] || "bg-gray-100 text-gray-700"
                    }`}
                  >
                    {event.type.replace(/_/g, " ")}
                  </span>
                  {event.person_name && (
                    <span className="text-sm font-medium text-gray-900">
                      {event.person_name}
                    </span>
                  )}
                  <span className="text-xs text-gray-400 ml-auto font-mono">
                    {formatTimestamp(event.created_at)}
                  </span>
                </div>
                <EventDataPreview data={event.data} />
              </div>
            ))}
          </div>
        )}

        {/* Pagination */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-gray-100 bg-gray-50">
          <span className="text-xs text-gray-500">
            Showing {offset + 1} – {Math.min(offset + limit, total)} of{" "}
            {total.toLocaleString()}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
              className="px-3 py-1 text-sm rounded-md border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <button
              onClick={() => setOffset(offset + limit)}
              disabled={offset + limit >= total}
              className="px-3 py-1 text-sm rounded-md border border-gray-200 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
