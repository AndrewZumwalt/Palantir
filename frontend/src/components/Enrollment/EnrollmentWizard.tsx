import {
  Camera,
  Check,
  ChevronRight,
  Fingerprint,
  Shield,
  Trash2,
  UserPlus,
  UserRound,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Select, TextInput } from "../ui/Field";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";
import VoiceCapture from "./VoiceCapture";

interface Person {
  id: string;
  name: string;
  role: string;
  enrolled_at: string;
  has_face: number;
  has_voice: number;
}

interface EnrollmentStatus {
  person_id: string;
  name: string;
  face_samples: number;
  required_samples: number;
  complete: boolean;
}

type Step = "list" | "create" | "consent" | "capture" | "voice" | "done";

const STEP_FLOW: { key: Step; label: string }[] = [
  { key: "create", label: "Identify" },
  { key: "consent", label: "Consent" },
  { key: "capture", label: "Face" },
  { key: "voice", label: "Voice" },
  { key: "done", label: "Archive" },
];

export default function EnrollmentWizard() {
  const [persons, setPersons] = useState<Person[]>([]);
  const [step, setStep] = useState<Step>("list");
  const [currentPerson, setCurrentPerson] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [status, setStatus] = useState<EnrollmentStatus | null>(null);
  const [name, setName] = useState("");
  const [role, setRole] = useState("student");
  const [capturing, setCapturing] = useState(false);
  const [faceDetectionAvailable, setFaceDetectionAvailable] = useState<
    boolean | null
  >(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const loadPersons = useCallback(async () => {
    const data = await api.get<{ persons: Person[] }>("/enrollment/persons");
    setPersons(data.persons);
  }, []);

  useEffect(() => {
    loadPersons();
    // Capability probe so we can warn the operator if the backend can't
    // actually run face detection (insightface not installed -- the face
    // endpoint would otherwise return 503 only at capture time).
    api
      .get<{ face_detection_available?: boolean }>("/settings/config")
      .then((cfg) =>
        setFaceDetectionAvailable(cfg.face_detection_available !== false),
      )
      .catch(() => setFaceDetectionAvailable(null));
  }, [loadPersons]);

  const startCamera = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
      });
      streamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
    } catch {
      alert("Could not access camera. Please grant permission.");
    }
  }, []);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  const handleCreate = useCallback(async () => {
    if (!name.trim()) return;
    const result = await api.post<{ person_id: string; name: string }>(
      "/enrollment/persons",
      { name, role }
    );
    setCurrentPerson({ id: result.person_id, name: result.name });
    setStep("consent");
  }, [name, role]);

  const handleConsent = useCallback(async () => {
    if (!currentPerson) return;
    await api.post(`/enrollment/persons/${currentPerson.id}/consent`, {
      consent_text:
        "I consent to the collection of my facial data for classroom attendance and identification purposes. I understand I can request deletion at any time.",
    });
    setStep("capture");
    startCamera();
  }, [currentPerson, startCamera]);

  const capturePhoto = useCallback(async () => {
    if (!videoRef.current || !canvasRef.current || !currentPerson || capturing)
      return;

    setCapturing(true);
    const canvas = canvasRef.current;
    const video = videoRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);

    const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
    const base64 = dataUrl.split(",")[1];

    try {
      const result = await api.post<EnrollmentStatus>(
        `/enrollment/persons/${currentPerson.id}/face`,
        { image_base64: base64 }
      );
      setStatus(result);
      if (result.complete) {
        stopCamera();
        setStep("voice");
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Capture failed";
      alert(message);
    } finally {
      setCapturing(false);
    }
  }, [currentPerson, capturing, stopCamera]);

  const handleDelete = useCallback(
    async (personId: string) => {
      if (!confirm("Purge subject and all associated data?")) return;
      await api.delete(`/enrollment/persons/${personId}`);
      loadPersons();
    },
    [loadPersons]
  );

  const resetWizard = useCallback(() => {
    stopCamera();
    setStep("list");
    setCurrentPerson(null);
    setStatus(null);
    setName("");
    setRole("student");
    loadPersons();
  }, [stopCamera, loadPersons]);

  // ---------- LIST ----------
  if (step === "list") {
    return (
      <div className="space-y-6">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
              // SUBJECT INTAKE
            </div>
            <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
              Enrollment roster
            </h1>
            <p className="text-sm text-gray-500 mt-1 max-w-2xl">
              Manage enrolled persons. Face and voice embeddings live on this
              node only; raw images are discarded post-embedding.
            </p>
          </div>
          <Button
            variant="primary"
            size="md"
            iconLeft={<UserPlus className="w-4 h-4" />}
            onClick={() => setStep("create")}
          >
            INTAKE NEW SUBJECT
          </Button>
        </div>

        <Panel
          label="ARCHIVE"
          title="Enrolled subjects"
          meta={
            <span className="tabular-nums">
              {persons.length} record{persons.length === 1 ? "" : "s"}
            </span>
          }
        >
          {persons.length ? (
            <ul className="divide-y divide-[#141d35]">
              {persons.map((p, i) => (
                <li
                  key={p.id}
                  className="py-3 flex items-center gap-4 group hover:bg-[#0f1629] px-2 -mx-2"
                >
                  <span className="font-data text-[10px] text-gray-600 tabular-nums w-8">
                    {(i + 1).toString().padStart(3, "0")}
                  </span>
                  <div className="w-10 h-10 border border-[#2a3658] bg-[#05080f] flex items-center justify-center">
                    <span className="font-data text-sm text-amber-400">
                      {p.name.charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-gray-100 truncate">
                      {p.name}
                    </div>
                    <div className="font-data text-[10px] text-gray-500 uppercase tracking-[0.14em]">
                      {p.role} · {p.id.slice(0, 14)}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusPill
                      tone={p.has_face ? "green" : "gray"}
                      size="xs"
                    >
                      {p.has_face ? "FACE ✓" : "NO FACE"}
                    </StatusPill>
                    <StatusPill
                      tone={p.has_voice ? "cyan" : "gray"}
                      size="xs"
                    >
                      {p.has_voice ? "VOICE ✓" : "NO VOICE"}
                    </StatusPill>
                  </div>
                  <button
                    onClick={() => handleDelete(p.id)}
                    title="Purge subject"
                    className="w-8 h-8 inline-flex items-center justify-center border border-[#1c2540] text-gray-500 hover:text-red-400 hover:border-red-700 opacity-0 group-hover:opacity-100"
                    aria-label="Purge subject"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<UserPlus className="w-5 h-5" />}
              title="NO SUBJECTS ON FILE"
              description="The archive is empty. Begin intake to enroll the first subject."
              action={
                <Button
                  variant="primary"
                  size="md"
                  iconLeft={<UserPlus className="w-4 h-4" />}
                  onClick={() => setStep("create")}
                >
                  BEGIN INTAKE
                </Button>
              }
            />
          )}
        </Panel>
      </div>
    );
  }

  // ---------- WIZARD SCAFFOLD ----------
  const renderWizardFrame = (
    title: string,
    subtitle: string,
    body: React.ReactNode
  ) => (
    <div className="max-w-2xl mx-auto space-y-5">
      <div>
        <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
          // SUBJECT INTAKE · {step.toUpperCase()}
        </div>
        <h1 className="text-2xl font-semibold text-gray-100 mt-1">{title}</h1>
        <p className="text-sm text-gray-500 mt-1">{subtitle}</p>
      </div>

      {/* Step rail */}
      <StepRail current={step} />

      {body}
    </div>
  );

  // ---------- CREATE ----------
  if (step === "create") {
    return renderWizardFrame(
      "Identify subject",
      "Create the record before biometric capture.",
      <Panel label="FORM" title="New subject record">
        <div className="space-y-4">
          <TextInput
            label="FULL NAME"
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Lastname, Firstname"
          />
          <Select
            label="ROLE"
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="student">Student</option>
            <option value="teacher">Teacher</option>
            <option value="admin">Admin</option>
            <option value="guest">Guest</option>
          </Select>
        </div>
        <div className="flex gap-2 mt-6 justify-end">
          <Button variant="ghost" onClick={resetWizard}>
            CANCEL
          </Button>
          <Button
            variant="primary"
            onClick={handleCreate}
            disabled={!name.trim()}
            iconRight={<ChevronRight className="w-4 h-4" />}
          >
            CONTINUE
          </Button>
        </div>
      </Panel>
    );
  }

  // ---------- CONSENT ----------
  if (step === "consent") {
    return renderWizardFrame(
      `Consent · ${currentPerson?.name}`,
      "Operator must confirm subject has acknowledged the terms.",
      <Panel label="DISCLOSURE" title="Data handling" tone="amber" brackets>
        <div className="text-sm text-gray-300 leading-relaxed space-y-3">
          <p>
            By proceeding,{" "}
            <strong className="text-amber-300">{currentPerson?.name}</strong>{" "}
            consents to:
          </p>
          <ul className="space-y-2 font-data text-[13px]">
            <li className="flex gap-2">
              <span className="text-amber-500">▸</span>
              <span className="text-gray-300">
                Collection of facial data for identification.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-amber-500">▸</span>
              <span className="text-gray-300">
                Use of this data for classroom attendance tracking.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="text-amber-500">▸</span>
              <span className="text-gray-300">
                Storage of <em>facial embeddings</em> (not raw photos) on the
                local device.
              </span>
            </li>
          </ul>
          <p className="text-gray-500 text-[13px] border-t border-[#1c2540] pt-3 mt-2">
            Data is purgeable at any time via the subject archive.
          </p>
        </div>
        <div className="flex gap-2 mt-6 justify-end">
          <Button variant="ghost" onClick={resetWizard}>
            CANCEL
          </Button>
          <Button
            variant="primary"
            onClick={handleConsent}
            iconLeft={<Shield className="w-4 h-4" />}
          >
            CONFIRM & START CAPTURE
          </Button>
        </div>
      </Panel>
    );
  }

  // ---------- FACE CAPTURE ----------
  if (step === "capture") {
    const samples = status?.face_samples ?? 0;
    const required = status?.required_samples ?? 10;
    const pct = Math.min(100, (samples / required) * 100);

    return renderWizardFrame(
      `Biometric // face · ${currentPerson?.name}`,
      `Capture ${required} angles. Slight head rotation between samples.`,
      <Panel label="CAPTURE" title="Face enrollment" tone="amber" brackets>
        {faceDetectionAvailable === false && (
          <div className="mb-4 px-3 py-2 bg-amber-500/10 border border-amber-600/60 font-data text-[11px] text-amber-200">
            &gt; FACE DETECTION OFFLINE -- backend has no insightface.
            Install ML extras (e.g. <strong>start-laptop.ps1 -WithMl</strong>{" "}
            on Windows after MSVC Build Tools) to enable enrollment.
            Photos will fail with HTTP 503 until then.
          </div>
        )}
        <div className="flex items-center justify-between mb-3">
          <span className="font-data text-[11px] text-gray-400 uppercase tracking-[0.18em]">
            &gt; {samples.toString().padStart(2, "0")} / {required} samples
          </span>
          <StatusPill tone="amber" size="xs" pulse>
            CAPTURING
          </StatusPill>
        </div>
        <div className="h-1.5 bg-[#05080f] border border-[#1c2540] mb-4">
          <div
            className="h-full bg-amber-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>

        <div className="relative bg-black border border-[#1c2540] overflow-hidden">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full block"
          />
          {/* Reticle */}
          <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
            <svg
              viewBox="0 0 200 200"
              className="w-56 h-56 text-amber-400/60"
            >
              <circle
                cx="100"
                cy="100"
                r="70"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.8"
              />
              <circle
                cx="100"
                cy="100"
                r="90"
                fill="none"
                stroke="currentColor"
                strokeWidth="0.4"
                strokeDasharray="2 4"
              />
              <line
                x1="100"
                y1="20"
                x2="100"
                y2="40"
                stroke="currentColor"
                strokeWidth="1"
              />
              <line
                x1="100"
                y1="160"
                x2="100"
                y2="180"
                stroke="currentColor"
                strokeWidth="1"
              />
              <line
                x1="20"
                y1="100"
                x2="40"
                y2="100"
                stroke="currentColor"
                strokeWidth="1"
              />
              <line
                x1="160"
                y1="100"
                x2="180"
                y2="100"
                stroke="currentColor"
                strokeWidth="1"
              />
            </svg>
          </div>
          {/* Corner tick marks */}
          <div className="absolute top-2 left-2 font-data text-[10px] text-amber-400/80 uppercase tracking-[0.18em]">
            FACE-01
          </div>
          <div className="absolute top-2 right-2 font-data text-[10px] text-amber-400/80 uppercase tracking-[0.18em]">
            {samples}/{required}
          </div>
        </div>
        <canvas ref={canvasRef} className="hidden" />

        <div className="flex gap-2 mt-6 justify-end">
          <Button variant="ghost" onClick={resetWizard} iconLeft={<X className="w-4 h-4" />}>
            ABORT
          </Button>
          <Button
            variant="primary"
            onClick={capturePhoto}
            loading={capturing}
            disabled={faceDetectionAvailable === false}
            iconLeft={<Camera className="w-4 h-4" />}
          >
            {capturing ? "PROCESSING" : "CAPTURE"}
          </Button>
        </div>
      </Panel>
    );
  }

  // ---------- VOICE ----------
  if (step === "voice" && currentPerson) {
    return renderWizardFrame(
      `Biometric // voice · ${currentPerson.name}`,
      "Five short samples. You may skip voice and finish with face only.",
      <VoiceCapture
        personId={currentPerson.id}
        personName={currentPerson.name}
        onComplete={() => setStep("done")}
        onSkip={() => setStep("done")}
      />
    );
  }

  // ---------- DONE ----------
  return renderWizardFrame(
    `Subject archived · ${currentPerson?.name}`,
    "Biometric record is live and indexed for detection.",
    <Panel label="OK" title="Enrollment complete" tone="amber" brackets>
      <div className="flex flex-col items-center py-4 text-center">
        <div className="relative w-16 h-16 border border-amber-500/60 rounded-full flex items-center justify-center mb-4">
          <Check className="w-7 h-7 text-amber-400" />
          <div className="absolute inset-0 border border-amber-500/30 rounded-full animate-ping" />
        </div>
        <h2 className="text-lg font-semibold text-gray-100">
          {currentPerson?.name}
        </h2>
        <p className="text-sm text-gray-500 mt-1 max-w-md">
          Face and voice embeddings stored. Subject will now be recognized by
          the vision and audio pipelines.
        </p>
        <div className="flex gap-2 mt-6">
          <Button
            variant="secondary"
            onClick={resetWizard}
            iconLeft={<UserRound className="w-4 h-4" />}
          >
            BACK TO ROSTER
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              setCurrentPerson(null);
              setStatus(null);
              setName("");
              setRole("student");
              setStep("create");
            }}
            iconLeft={<UserPlus className="w-4 h-4" />}
          >
            ENROLL ANOTHER
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function StepRail({ current }: { current: Step }) {
  const currentIdx = STEP_FLOW.findIndex((s) => s.key === current);
  return (
    <ol className="flex items-center gap-2 overflow-x-auto pb-1">
      {STEP_FLOW.map((s, i) => {
        const active = i === currentIdx;
        const done = i < currentIdx;
        return (
          <li key={s.key} className="flex items-center gap-2 shrink-0">
            <div
              className={[
                "flex items-center gap-2 px-2.5 py-1 border font-data text-[10px] uppercase tracking-[0.18em]",
                active
                  ? "bg-amber-500/10 border-amber-600/60 text-amber-300"
                  : done
                    ? "border-[#2a3658] text-gray-400"
                    : "border-[#1c2540] text-gray-600",
              ].join(" ")}
            >
              {done ? (
                <Check className="w-3 h-3 text-amber-400" />
              ) : (
                <Fingerprint className="w-3 h-3" />
              )}
              <span>{`${(i + 1).toString().padStart(2, "0")}. ${s.label}`}</span>
            </div>
            {i < STEP_FLOW.length - 1 && (
              <span className="text-gray-700 font-data text-sm">/</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
