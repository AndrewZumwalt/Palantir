import { Cctv, EyeOff, Maximize2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, getAuthToken } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

// Mirror of palantir.models.BoundingBox.
interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface DetectedFace {
  person_id?: string | null;
  name?: string | null;
  confidence?: number;
  bbox: BBox;
}

interface DetectedObject {
  label: string;
  confidence: number;
  bbox: BBox;
  location_description?: string | null;
}

interface PersonEngagement {
  person_id: string;
  name?: string | null;
  state:
    | "working"
    | "collaborating"
    | "phone"
    | "sleeping"
    | "disengaged"
    | "unknown";
  confidence: number;
}

// Colour-per-engagement so the overlay reads at a glance.
const ENGAGEMENT_TONE: Record<PersonEngagement["state"], string> = {
  working: "#34d399", // emerald-400
  collaborating: "#60a5fa", // blue-400
  phone: "#f87171", // red-400
  sleeping: "#9ca3af", // gray-400
  disengaged: "#fbbf24", // amber-400
  unknown: "#475569", // slate-600
};

interface ConfigCamera {
  camera: { width: number; height: number; fps: number };
}

export default function CameraPage() {
  const { subscribe } = useWebSocket();
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [streamErrored, setStreamErrored] = useState(false);
  const [showOverlays, setShowOverlays] = useState(true);
  const [privacyMode, setPrivacyMode] = useState(false);
  // Native resolution -- used as the SVG viewBox so bounding boxes scale
  // 1:1 to whatever size the image renders at.
  const [size, setSize] = useState<{ width: number; height: number }>({
    width: 1920,
    height: 1080,
  });

  const [faces, setFaces] = useState<DetectedFace[]>([]);
  const [objects, setObjects] = useState<DetectedObject[]>([]);
  const [engagement, setEngagement] = useState<PersonEngagement[]>([]);
  const lastSeenRef = useRef<{ faces: number; objects: number; eng: number }>({
    faces: 0,
    objects: 0,
    eng: 0,
  });

  // Engagement is keyed by person_id; faces also carry person_id when
  // recognised.  Build a quick map so we can colour-code face boxes by
  // engagement state.
  const engagementByPerson = useMemo(() => {
    const m = new Map<string, PersonEngagement>();
    for (const e of engagement) m.set(e.person_id, e);
    return m;
  }, [engagement]);

  useEffect(() => {
    // Fetch native resolution + privacy state on mount.
    Promise.all([
      api.get<ConfigCamera>("/settings/config").catch(() => null),
      api
        .get<{ privacy_mode: boolean }>("/settings/privacy")
        .catch(() => null),
    ]).then(([cfg, priv]) => {
      if (cfg?.camera?.width && cfg.camera.height) {
        setSize({ width: cfg.camera.width, height: cfg.camera.height });
      }
      if (priv) setPrivacyMode(priv.privacy_mode);
    });
  }, []);

  // Build the MJPEG URL with the bearer token in the query string -- an
  // <img> tag can't carry an Authorization header.  Add a cache-busting
  // suffix so a privacy-toggle / network blip resets the connection.
  useEffect(() => {
    const token = getAuthToken();
    const base = "/api/vision/stream";
    setStreamErrored(false);
    setStreamUrl(token ? `${base}?token=${encodeURIComponent(token)}` : base);
  }, []);

  // Stop the MJPEG when privacy is on -- the backend would just send the
  // last cached frame, which is misleading.
  useEffect(() => {
    if (privacyMode) {
      setStreamUrl(null);
      return;
    }
    const token = getAuthToken();
    const base = "/api/vision/stream";
    setStreamUrl(token ? `${base}?token=${encodeURIComponent(token)}` : base);
  }, [privacyMode]);

  // Detection bridges via the existing WebSocket.
  useEffect(() => {
    const offFaces = subscribe("vision:faces", (data) => {
      const list = (data.faces as DetectedFace[]) ?? [];
      setFaces(list);
      lastSeenRef.current.faces = Date.now();
    });
    const offObjects = subscribe("vision:objects", (data) => {
      const list = (data.objects as DetectedObject[]) ?? [];
      setObjects(list);
      lastSeenRef.current.objects = Date.now();
    });
    const offEng = subscribe("vision:engagement", (data) => {
      const list = (data.engagements as PersonEngagement[]) ?? [];
      setEngagement(list);
      lastSeenRef.current.eng = Date.now();
    });
    const offPriv = subscribe("system:privacy", (data) => {
      if (typeof data.enabled === "boolean") setPrivacyMode(data.enabled);
    });
    return () => {
      offFaces();
      offObjects();
      offEng();
      offPriv();
    };
  }, [subscribe]);

  // After a few seconds with no new detections, age them out -- otherwise
  // a frozen overlay can mislead the operator into thinking nothing's wrong.
  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      if (now - lastSeenRef.current.faces > 3000) setFaces([]);
      if (now - lastSeenRef.current.objects > 5000) setObjects([]);
      if (now - lastSeenRef.current.eng > 5000) setEngagement([]);
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const aspect = `${size.width} / ${size.height}`;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // CAMERA FEED
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Live observation
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            Raw video at {size.width}×{size.height} with detection overlays
            from the vision pipeline. Use this to verify what the AI
            actually sees -- bounding boxes lag the source by 50-200ms.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill
            tone={faces.length ? "green" : "gray"}
            size="xs"
            pulse={faces.length > 0}
          >
            {faces.length} FACE{faces.length === 1 ? "" : "S"}
          </StatusPill>
          <StatusPill
            tone={objects.length ? "cyan" : "gray"}
            size="xs"
            pulse={objects.length > 0}
          >
            {objects.length} OBJ
          </StatusPill>
          <button
            onClick={() => setShowOverlays((v) => !v)}
            className={[
              "font-data text-[11px] uppercase tracking-[0.18em] px-3 py-1.5 border",
              showOverlays
                ? "border-amber-700/60 text-amber-300 bg-amber-500/10"
                : "border-[#1c2540] text-gray-400 hover:text-gray-200",
            ].join(" ")}
          >
            {showOverlays ? "Overlays ON" : "Overlays OFF"}
          </button>
        </div>
      </div>

      <Panel
        label="OPTICAL"
        title="Primary sensor"
        tone={privacyMode ? "danger" : "amber"}
        brackets
        meta={
          privacyMode ? (
            <StatusPill tone="red" size="xs">
              VEIL ENGAGED
            </StatusPill>
          ) : streamErrored ? (
            <StatusPill tone="red" size="xs">
              STREAM ERROR
            </StatusPill>
          ) : (
            <StatusPill tone="amber" size="xs" pulse>
              LIVE
            </StatusPill>
          )
        }
      >
        {privacyMode ? (
          <div
            className="flex items-center justify-center bg-[#05080f] border border-red-700/40 text-red-300 font-data uppercase tracking-[0.2em] text-sm"
            style={{ aspectRatio: aspect }}
          >
            <div className="flex flex-col items-center gap-2">
              <EyeOff className="w-10 h-10" />
              <span>Privacy veil engaged</span>
              <span className="text-[11px] text-red-400/70 normal-case tracking-normal">
                Stream paused. Disengage from the Protocols page.
              </span>
            </div>
          </div>
        ) : (
          <div
            className="relative bg-[#05080f] border border-[#1c2540] overflow-hidden"
            style={{ aspectRatio: aspect }}
          >
            {streamUrl ? (
              <img
                src={streamUrl}
                alt="Live camera feed"
                onError={() => setStreamErrored(true)}
                onLoad={() => setStreamErrored(false)}
                className="absolute inset-0 w-full h-full object-contain"
              />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-gray-500 font-data text-xs">
                <Cctv className="w-8 h-8 mr-2" />
                connecting...
              </div>
            )}

            {showOverlays && (
              <svg
                viewBox={`0 0 ${size.width} ${size.height}`}
                preserveAspectRatio="xMidYMid meet"
                className="absolute inset-0 w-full h-full pointer-events-none"
              >
                {/* Object boxes -- drawn below faces so faces stay on top */}
                {objects.map((o, i) => (
                  <g key={`obj-${i}`}>
                    <rect
                      x={o.bbox.x}
                      y={o.bbox.y}
                      width={o.bbox.width}
                      height={o.bbox.height}
                      fill="none"
                      stroke="#22d3ee"
                      strokeWidth={Math.max(2, size.width / 600)}
                      strokeDasharray="6 4"
                      opacity={0.7}
                    />
                    <text
                      x={o.bbox.x + 4}
                      y={Math.max(o.bbox.y - 6, 12)}
                      fontSize={Math.max(11, size.width / 110)}
                      fontFamily="ui-monospace, monospace"
                      fill="#22d3ee"
                    >
                      {o.label}
                      {o.confidence > 0
                        ? ` ${(o.confidence * 100).toFixed(0)}%`
                        : ""}
                    </text>
                  </g>
                ))}

                {/* Face boxes coloured by current engagement state */}
                {faces.map((f, i) => {
                  const eng = f.person_id
                    ? engagementByPerson.get(f.person_id)
                    : undefined;
                  const colour = eng
                    ? ENGAGEMENT_TONE[eng.state]
                    : "#fbbf24"; // default amber
                  const label = [
                    f.name ?? (f.person_id ? "id:" + f.person_id.slice(0, 6) : "?"),
                    eng?.state ? `· ${eng.state}` : null,
                    f.confidence ? `(${(f.confidence * 100).toFixed(0)}%)` : null,
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <g key={`face-${i}`}>
                      <rect
                        x={f.bbox.x}
                        y={f.bbox.y}
                        width={f.bbox.width}
                        height={f.bbox.height}
                        fill="none"
                        stroke={colour}
                        strokeWidth={Math.max(3, size.width / 400)}
                      />
                      <text
                        x={f.bbox.x + 4}
                        y={Math.max(f.bbox.y - 6, 12)}
                        fontSize={Math.max(12, size.width / 100)}
                        fontFamily="ui-monospace, monospace"
                        fill={colour}
                      >
                        {label}
                      </text>
                    </g>
                  );
                })}
              </svg>
            )}

            {/* Bottom-left HUD with native resolution */}
            <div className="absolute bottom-2 left-2 font-data text-[10px] text-amber-400/70 uppercase tracking-[0.18em] bg-[#05080f]/70 px-2 py-1">
              <Maximize2 className="w-3 h-3 inline mr-1" />
              {size.width}×{size.height}
            </div>
          </div>
        )}
      </Panel>

      {/* Live engagement table -- handy when several people are in frame */}
      <Panel label="CLASSIFICATION" title="Live engagement readout">
        {engagement.length ? (
          <ul className="divide-y divide-[#141d35]">
            {engagement.map((e) => (
              <li
                key={e.person_id}
                className="py-2 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{ background: ENGAGEMENT_TONE[e.state] }}
                  />
                  <span className="text-sm text-gray-100">
                    {e.name ?? `id:${e.person_id.slice(0, 8)}`}
                  </span>
                  <span className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-500">
                    {e.state}
                  </span>
                </div>
                <span className="font-data text-[11px] text-gray-400 tabular-nums">
                  {(e.confidence * 100).toFixed(0)}%
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="py-6 text-center text-gray-500 font-data text-[11px] uppercase tracking-[0.18em]">
            // no engagement signal -- enable [ml] extras + ensure a person is in frame
          </div>
        )}
      </Panel>
    </div>
  );
}
