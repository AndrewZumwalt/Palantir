import {
  ChevronLeft,
  ChevronRight,
  Filter,
  Radio,
  ScrollText,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { Button } from "../ui/Button";
import { EmptyState, LoadingLines } from "../ui/EmptyState";
import { Toggle } from "../ui/Field";
import { LiveIndicator } from "../ui/LiveIndicator";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

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

type Tone = "green" | "gray" | "cyan" | "violet" | "amber" | "red";

const EVENT_TYPE_TONE: Record<string, Tone> = {
  person_entered: "green",
  person_exited: "gray",
  utterance: "cyan",
  response: "violet",
  object_detected: "violet",
  engagement_change: "amber",
  automation_triggered: "amber",
  privacy_toggled: "red",
  system_error: "red",
};

const ACTIVE_CHIP: Record<Tone, string> = {
  green: "text-emerald-200 border-emerald-600/70 bg-emerald-500/15",
  gray: "text-gray-200 border-gray-600 bg-gray-500/10",
  cyan: "text-cyan-200 border-cyan-600/70 bg-cyan-500/15",
  violet: "text-violet-200 border-violet-600/70 bg-violet-500/15",
  amber: "text-amber-200 border-amber-600/70 bg-amber-500/15",
  red: "text-red-200 border-red-600/70 bg-red-500/15",
};

const INACTIVE_CHIP =
  "text-gray-500 border-[#1c2540] bg-[#05080f] hover:text-gray-200 hover:border-[#2a3658]";

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    const pad = (n: number) => n.toString().padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return ts;
  }
}

function formatDate(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

function EventDataPreview({ data }: { data: Event["data"] }) {
  if (!data) return null;
  if (typeof data === "string") {
    return (
      <span className="font-data text-[11px] text-gray-500 truncate">
        {data}
      </span>
    );
  }
  const entries = Object.entries(data).filter(
    ([, v]) => typeof v !== "object" || v === null
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.slice(0, 4).map(([k, v]) => (
        <span
          key={k}
          className="inline-flex items-center gap-1 font-data text-[10px] px-1.5 py-0.5 bg-[#05080f] border border-[#1c2540] text-gray-400 max-w-[22rem] truncate"
        >
          <span className="text-gray-600">{k}=</span>
          <span className="text-gray-300">{String(v).slice(0, 60)}</span>
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
          `/events?${params.toString()}`
        );
        setEvents(res.events);
        setTotal(res.total);
        if (resetOffset) setOffset(0);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    },
    [selectedTypes, offset]
  );

  useEffect(() => {
    api
      .get<{ types: string[] }>("/events/types")
      .then((d) => setTypes(d.types))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTypes]);

  useEffect(() => {
    load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset]);

  useEffect(() => {
    if (!autoRefresh) return;
    const unsub = subscribe("events:log", () => {
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
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // TRANSMISSIONS
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Event log stream
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Append-only record of everything the node has observed.{" "}
            <span className="font-data text-amber-400">
              {total.toLocaleString()}
            </span>{" "}
            transmissions on file.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {autoRefresh && <LiveIndicator label="TAILING" tone="amber" />}
          <div className="inline-flex items-center gap-2">
            <span className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-500">
              AUTO-TAIL
            </span>
            <Toggle checked={autoRefresh} onChange={setAutoRefresh} />
          </div>
        </div>
      </div>

      {/* Filter panel */}
      <Panel
        label="FILTER"
        title="Channel selection"
        meta={
          selectedTypes.size > 0 ? (
            <button
              onClick={clearFilters}
              className="inline-flex items-center gap-1 text-amber-400 hover:text-amber-300"
            >
              <X className="w-3 h-3" /> clear ({selectedTypes.size})
            </button>
          ) : (
            <span className="inline-flex items-center gap-1 text-gray-500">
              <Filter className="w-3 h-3" /> all channels
            </span>
          )
        }
      >
        <div className="flex flex-wrap gap-1.5">
          {types.length === 0 ? (
            <span className="font-data text-xs text-gray-600">
              &gt; NO CHANNELS DISCOVERED
            </span>
          ) : (
            types.map((type) => {
              const active = selectedTypes.has(type);
              const tone = EVENT_TYPE_TONE[type] || "gray";
              return (
                <button
                  key={type}
                  onClick={() => toggleType(type)}
                  className={[
                    "inline-flex items-center h-7 px-2.5 border font-data text-[10px] uppercase tracking-[0.14em] transition-colors",
                    active ? ACTIVE_CHIP[tone] : INACTIVE_CHIP,
                  ].join(" ")}
                >
                  {type.replace(/_/g, " ")}
                </button>
              );
            })
          )}
        </div>
      </Panel>

      {/* Terminal-style feed */}
      <Panel
        label="FEED"
        title="Live transmissions"
        meta={
          <span className="inline-flex items-center gap-1.5">
            <Radio className="w-3 h-3 text-amber-500 breathe" />
            {Math.min(offset + limit, total)} / {total.toLocaleString()}
          </span>
        }
        bodyClassName="p-0"
      >
        {loading && events.length === 0 ? (
          <div className="p-4">
            <LoadingLines rows={6} />
          </div>
        ) : events.length === 0 ? (
          <EmptyState
            icon={<ScrollText className="w-5 h-5" />}
            title="NO TRANSMISSIONS"
            description="No events match the current channel filter."
          />
        ) : (
          <ul className="divide-y divide-[#141d35] font-data text-xs">
            {events.map((event) => {
              const tone = EVENT_TYPE_TONE[event.type] || "gray";
              return (
                <li
                  key={event.id}
                  className="px-4 py-2.5 hover:bg-[#0f1629] transition-colors"
                >
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-gray-600 tabular-nums shrink-0">
                      {formatTimestamp(event.created_at)}
                    </span>
                    <span className="text-gray-700 shrink-0">|</span>
                    <StatusPill tone={tone} size="xs" brackets={false}>
                      {event.type.replace(/_/g, " ")}
                    </StatusPill>
                    {event.person_name && (
                      <>
                        <span className="text-gray-700 shrink-0">›</span>
                        <span className="text-gray-200">
                          {event.person_name}
                        </span>
                      </>
                    )}
                    <span className="text-gray-700 ml-auto shrink-0 text-[10px] uppercase tracking-[0.12em]">
                      #{event.id} · {formatDate(event.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 pl-[4.25rem]">
                    <EventDataPreview data={event.data} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex items-center justify-between px-4 py-3 border-t border-[#1c2540] bg-[#05080f]/60">
          <span className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-500 tabular-nums">
            &gt; {offset + 1}–{Math.min(offset + limit, total)} of{" "}
            {total.toLocaleString()}
          </span>
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
              iconLeft={<ChevronLeft className="w-3 h-3" />}
            >
              PREV
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOffset(offset + limit)}
              disabled={offset + limit >= total}
              iconRight={<ChevronRight className="w-3 h-3" />}
            >
              NEXT
            </Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
