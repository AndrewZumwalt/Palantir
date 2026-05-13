import { Activity, MessageSquare, Mic, Radio, Send, Trash2, User } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { LiveIndicator } from "../ui/LiveIndicator";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

interface ChatTurn {
  id: number;
  created_at: string;
  user_text: string;
  assistant_text: string;
  person_id: string | null;
  person_name: string | null;
}

interface HistoryResponse {
  turns: ChatTurn[];
}

interface LiveTurn {
  text: string;
  source: string;
  created_at: string;
  assistant_text?: string;
}

type VoicePhase = "idle" | "listening" | "transcribing" | "heard" | "empty" | "error";

const POLL_INTERVAL_MS = 1500;

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    const pad = (n: number) => n.toString().padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch {
    return ts;
  }
}

export default function ChatPage() {
  const { connected, subscribe } = useWebSocket();
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voicePhase, setVoicePhase] = useState<VoicePhase>("idle");
  const [micLevel, setMicLevel] = useState(0);
  const [lastHeard, setLastHeard] = useState<string | null>(null);
  const [voiceMessage, setVoiceMessage] = useState<string | null>(null);
  const [liveTurn, setLiveTurn] = useState<LiveTurn | null>(null);
  // When we send a message we expect a new turn to appear; track the last
  // seen id so we can show a subtle "waiting for reply" hint until it does.
  const [pendingSince, setPendingSince] = useState<number | null>(null);
  const lastIdRef = useRef<number>(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Reverse the API order (newest first) into chronological order for display.
  const ordered = useMemo(() => [...turns].reverse(), [turns]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await api.get<HistoryResponse>("/chat/history?limit=100");
      const list = data.turns ?? [];
      const top = list[0]?.id ?? 0;
      if (top !== lastIdRef.current) {
        lastIdRef.current = top;
        setTurns(list);
        // The reply we were waiting for has landed.
        if (pendingSince !== null && top > pendingSince) {
          setPendingSince(null);
        }
      }
      setLiveTurn((current) => {
        if (!current) return current;
        const landed = list.some((turn) => turn.user_text === current.text);
        return landed ? null : current;
      });
      setError(null);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Failed to load history";
      setError(message);
    }
  }, [pendingSince]);

  useEffect(() => {
    const offState = subscribe("audio:state", (data) => {
      const state = typeof data.state === "string" ? data.state : "idle";
      if (
        state === "listening" ||
        state === "transcribing" ||
        state === "heard" ||
        state === "empty" ||
        state === "error" ||
        state === "idle"
      ) {
        setVoicePhase(state);
      }
      if (typeof data.text === "string" && data.text.trim()) {
        setLastHeard(data.text);
      }
      if (typeof data.message === "string") {
        setVoiceMessage(data.message);
      } else if (state === "idle") {
        setVoiceMessage(null);
      }
    });
    const offLevel = subscribe("audio:level", (data) => {
      const level =
        typeof data.rms === "number"
          ? data.rms
          : typeof data.level === "number"
            ? data.level
            : 0;
      setMicLevel(Math.max(0, Math.min(1, level)));
      if (data.listening === true) {
        setVoicePhase((current) =>
          current === "idle" ? "listening" : current,
        );
      }
    });
    const offUtterance = subscribe("audio:utterance", (data) => {
      const text = typeof data.text === "string" ? data.text.trim() : "";
      if (!text) return;
      const source = typeof data.source === "string" ? data.source : "voice";
      const created_at =
        typeof data.timestamp === "string"
          ? data.timestamp
          : new Date().toISOString();
      setLastHeard(text);
      setLiveTurn({ text, source, created_at });
      setPendingSince(lastIdRef.current);
      loadHistory();
    });
    const offResponse = subscribe("brain:response", (data) => {
      const text = typeof data.text === "string" ? data.text.trim() : "";
      if (!text) return;
      setLiveTurn((current) =>
        current ? { ...current, assistant_text: text } : current,
      );
      setPendingSince(null);
      loadHistory();
    });
    return () => {
      offState();
      offLevel();
      offUtterance();
      offResponse();
    };
  }, [loadHistory, subscribe]);

  // Initial load + polling.
  useEffect(() => {
    loadHistory();
    const id = setInterval(loadHistory, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [loadHistory]);

  // Auto-scroll to the newest message when the list grows.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [ordered.length, pendingSince]);

  const handleSend = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const text = draft.trim();
      if (!text || sending) return;
      setSending(true);
      setError(null);
      try {
        // Snapshot the highest id BEFORE sending so we know what counts as
        // the new reply when it arrives.
        setPendingSince(lastIdRef.current);
        await api.post("/chat/message", { text });
        setDraft("");
        // Kick a poll right away so the new turn shows up faster than
        // the next interval would deliver.
        loadHistory();
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to send message";
        setError(message);
        setPendingSince(null);
      } finally {
        setSending(false);
      }
    },
    [draft, sending, loadHistory],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter sends, Shift+Enter inserts a newline (consistent with Slack/etc).
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const form = (e.target as HTMLElement).closest("form");
        form?.requestSubmit();
      }
    },
    [],
  );

  const handleClear = useCallback(async () => {
    if (clearing || !window.confirm("Clear the conversation log?")) return;
    setClearing(true);
    setError(null);
    try {
      await api.delete<{ cleared: boolean; deleted: number }>("/chat/history");
      lastIdRef.current = 0;
      setTurns([]);
      setLiveTurn(null);
      setPendingSince(null);
      setLastHeard(null);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to clear chat history";
      setError(message);
    } finally {
      setClearing(false);
    }
  }, [clearing]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 font-data text-[10px] uppercase tracking-[0.22em] text-amber-500">
            <MessageSquare className="w-3 h-3" />
            // CONVERSATION LOG
          </div>
          <h2 className="text-xl font-semibold text-gray-100">
            Chat with Palantir
          </h2>
        </div>
        <LiveIndicator label="STREAM" tone="cyan" />
      </div>

      <Panel
        title="Voice pipeline"
        label="MIC"
        meta={
          <StatusPill
            tone={connected ? (voicePhase === "error" ? "red" : "cyan") : "gray"}
            size="xs"
            pulse={voicePhase === "listening" || voicePhase === "transcribing"}
          >
            {connected ? voicePhase.toUpperCase() : "OFFLINE"}
          </StatusPill>
        }
      >
        <div className="grid gap-3 md:grid-cols-[220px_1fr] md:items-center">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 border border-cyan-700/50 bg-cyan-500/10 flex items-center justify-center">
              {voicePhase === "listening" ? (
                <Mic className="w-4 h-4 text-cyan-300" />
              ) : (
                <Radio className="w-4 h-4 text-cyan-300" />
              )}
            </div>
            <div className="min-w-0">
              <div className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-500">
                input level
              </div>
              <div className="font-data text-xs text-gray-200">
                {Math.round(micLevel * 100).toString().padStart(2, "0")}%
              </div>
            </div>
          </div>
          <div>
            <div className="h-2 bg-[#05080f] border border-[#1c2540] overflow-hidden">
              <div
                className="h-full bg-cyan-400 transition-all"
                style={{ width: `${Math.min(100, micLevel * 180)}%` }}
              />
            </div>
            <div className="mt-2 text-xs text-gray-400 min-h-5">
              {lastHeard ? (
                <>
                  <span className="font-data uppercase tracking-[0.14em] text-amber-400">
                    heard
                  </span>{" "}
                  {lastHeard}
                </>
              ) : voiceMessage ? (
                voiceMessage
              ) : (
                "Waiting for wake word or typed input."
              )}
            </div>
          </div>
        </div>
      </Panel>

      <Panel
        title="Transcript"
        label="I/O"
        meta={
          <>
            <StatusPill tone="cyan" size="xs">
              {ordered.length} TURN{ordered.length === 1 ? "" : "S"}
            </StatusPill>
            <button
              type="button"
              onClick={handleClear}
              disabled={clearing || (ordered.length === 0 && !liveTurn)}
              title="Clear conversation log"
              aria-label="Clear conversation log"
              className="h-7 w-7 inline-flex items-center justify-center border border-[#1c2540] text-gray-500 hover:text-red-300 hover:border-red-700/60 disabled:opacity-40"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </>
        }
      >
        <div
          ref={scrollRef}
          className="max-h-[60vh] min-h-[280px] overflow-y-auto px-4 py-4 space-y-4"
        >
          {ordered.length === 0 && !liveTurn ? (
            <EmptyState
              title="No conversation yet"
              description="Speak the wake word, or type a message below to start."
            />
          ) : (
            ordered.map((t) => (
              <div key={t.id} className="space-y-2">
                <Bubble
                  side="user"
                  who={t.person_name ?? "Unknown speaker"}
                  text={t.user_text}
                  timestamp={t.created_at}
                />
                <Bubble
                  side="assistant"
                  who="Palantir"
                  text={t.assistant_text}
                  timestamp={t.created_at}
                />
              </div>
            ))
          )}
          {liveTurn && (
            <div className="space-y-2">
              <Bubble
                side="user"
                who={liveTurn.source === "voice" ? "Mic input" : "Typed input"}
                text={liveTurn.text}
                timestamp={liveTurn.created_at}
              />
              {liveTurn.assistant_text && (
                <Bubble
                  side="assistant"
                  who="Palantir"
                  text={liveTurn.assistant_text}
                  timestamp={new Date().toISOString()}
                />
              )}
            </div>
          )}
          {pendingSince !== null && (
            <div className="flex items-center gap-2 text-xs font-data uppercase tracking-[0.18em] text-amber-500/70 pl-2">
              <Activity className="w-3 h-3" />
              // awaiting reply...
            </div>
          )}
        </div>

        {error && (
          <div className="px-4 pb-3 text-xs text-red-300">
            {error}
          </div>
        )}

        <form
          onSubmit={handleSend}
          className="border-t border-[#1c2540] p-3 flex items-end gap-2 bg-[#05080f]"
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message to Palantir..."
            rows={2}
            disabled={sending}
            className="flex-1 resize-none bg-[#0a1020] border border-[#1c2540] rounded-sm px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-amber-600/60"
          />
          <Button type="submit" disabled={sending || !draft.trim()}>
            <Send className="w-4 h-4" />
            <span className="ml-1.5">SEND</span>
          </Button>
        </form>
      </Panel>
    </div>
  );
}

function Bubble({
  side,
  who,
  text,
  timestamp,
}: {
  side: "user" | "assistant";
  who: string;
  text: string;
  timestamp: string;
}) {
  const isUser = side === "user";
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className={[
          "max-w-[80%] rounded-sm border px-3 py-2 text-sm",
          isUser
            ? "border-amber-600/40 bg-amber-500/10 text-amber-100"
            : "border-cyan-700/40 bg-cyan-500/5 text-cyan-100",
        ].join(" ")}
      >
        <div
          className={[
            "flex items-center gap-1.5 font-data text-[10px] uppercase tracking-[0.18em]",
            isUser ? "text-amber-400/80" : "text-cyan-400/80",
          ].join(" ")}
        >
          <User className="w-3 h-3" />
          <span>{who}</span>
          <span className="text-gray-600">·</span>
          <span className="text-gray-500">{formatTimestamp(timestamp)}</span>
        </div>
        <div className="mt-1 whitespace-pre-wrap">{text}</div>
      </div>
    </div>
  );
}
