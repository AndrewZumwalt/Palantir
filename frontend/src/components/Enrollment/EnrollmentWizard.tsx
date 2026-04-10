import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";

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

type Step = "list" | "create" | "consent" | "capture" | "done";

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
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const loadPersons = useCallback(async () => {
    const data = await api.get<{ persons: Person[] }>("/enrollment/persons");
    setPersons(data.persons);
  }, []);

  useEffect(() => {
    loadPersons();
  }, [loadPersons]);

  const startCamera = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
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
      { name, role },
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
        { image_base64: base64 },
      );
      setStatus(result);

      if (result.complete) {
        stopCamera();
        setStep("done");
      }
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Capture failed";
      alert(message);
    } finally {
      setCapturing(false);
    }
  }, [currentPerson, capturing, stopCamera]);

  const handleDelete = useCallback(
    async (personId: string) => {
      if (!confirm("Remove this person and all their data?")) return;
      await api.delete(`/enrollment/persons/${personId}`);
      loadPersons();
    },
    [loadPersons],
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

  // List view
  if (step === "list") {
    return (
      <div>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Enrollment</h1>
            <p className="text-gray-500 mt-1">
              Manage enrolled people and register new faces
            </p>
          </div>
          <button
            onClick={() => setStep("create")}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
          >
            Enroll New Person
          </button>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="divide-y divide-gray-50">
            {persons.length ? (
              persons.map((p) => (
                <div
                  key={p.id}
                  className="px-5 py-4 flex items-center justify-between"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center">
                      <span className="text-indigo-700 font-semibold">
                        {p.name.charAt(0).toUpperCase()}
                      </span>
                    </div>
                    <div>
                      <p className="font-medium">{p.name}</p>
                      <p className="text-xs text-gray-500 capitalize">
                        {p.role}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`text-xs px-2 py-1 rounded-full ${p.has_face ? "bg-green-50 text-green-700" : "bg-gray-100 text-gray-500"}`}
                    >
                      {p.has_face ? "Face enrolled" : "No face"}
                    </span>
                    <button
                      onClick={() => handleDelete(p.id)}
                      className="text-xs text-red-500 hover:text-red-700"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div className="px-5 py-12 text-center text-gray-400">
                No one enrolled yet. Click "Enroll New Person" to start.
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Create person
  if (step === "create") {
    return (
      <div className="max-w-md mx-auto">
        <h2 className="text-xl font-bold mb-6">New Person</h2>
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              placeholder="Full name"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Role
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
            >
              <option value="student">Student</option>
              <option value="teacher">Teacher</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={resetWizard}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={!name.trim()}
              className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Consent
  if (step === "consent") {
    return (
      <div className="max-w-md mx-auto">
        <h2 className="text-xl font-bold mb-4">Consent</h2>
        <div className="bg-gray-50 rounded-lg p-4 mb-6 text-sm text-gray-700 leading-relaxed">
          <p className="mb-2">
            By proceeding, <strong>{currentPerson?.name}</strong> consents to:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li>Collection of facial data for identification</li>
            <li>Use of this data for classroom attendance tracking</li>
            <li>Storage of facial embeddings (not raw photos) on the local device</li>
          </ul>
          <p className="mt-2">
            Data can be deleted at any time via the enrollment settings.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={resetWizard}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm"
          >
            Cancel
          </button>
          <button
            onClick={handleConsent}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
          >
            I Consent - Start Camera
          </button>
        </div>
      </div>
    );
  }

  // Face capture
  if (step === "capture") {
    const samples = status?.face_samples ?? 0;
    const required = status?.required_samples ?? 10;

    return (
      <div className="max-w-lg mx-auto">
        <h2 className="text-xl font-bold mb-2">
          Capture Face - {currentPerson?.name}
        </h2>
        <p className="text-gray-500 text-sm mb-4">
          {samples}/{required} photos captured. Look at the camera from different
          angles.
        </p>

        {/* Progress bar */}
        <div className="w-full bg-gray-200 rounded-full h-2 mb-4">
          <div
            className="bg-indigo-600 h-2 rounded-full transition-all"
            style={{ width: `${(samples / required) * 100}%` }}
          />
        </div>

        <div className="relative rounded-xl overflow-hidden bg-black mb-4">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full"
          />
          {/* Crosshair overlay */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-48 h-48 border-2 border-white/30 rounded-full" />
          </div>
        </div>
        <canvas ref={canvasRef} className="hidden" />

        <div className="flex gap-3">
          <button
            onClick={resetWizard}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm"
          >
            Cancel
          </button>
          <button
            onClick={capturePhoto}
            disabled={capturing}
            className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {capturing ? "Processing..." : `Capture Photo (${samples}/${required})`}
          </button>
        </div>
      </div>
    );
  }

  // Done
  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
        <svg
          className="w-8 h-8 text-green-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M5 13l4 4L19 7"
          />
        </svg>
      </div>
      <h2 className="text-xl font-bold mb-2">Enrollment Complete</h2>
      <p className="text-gray-500 mb-6">
        {currentPerson?.name} has been successfully enrolled with facial
        recognition.
      </p>
      <button
        onClick={resetWizard}
        className="px-6 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
      >
        Back to List
      </button>
    </div>
  );
}
