import { useCallback, useRef, useState } from "react";
import { api } from "../../api/client";

interface VoiceEnrollmentStatus {
  person_id: string;
  voice_samples: number;
  required_samples: number;
  complete: boolean;
}

interface VoiceCaptureProps {
  personId: string;
  personName: string;
  onComplete: () => void;
  onSkip: () => void;
}

export default function VoiceCapture({
  personId,
  personName,
  onComplete,
  onSkip,
}: VoiceCaptureProps) {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [status, setStatus] = useState<VoiceEnrollmentStatus | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const prompts = [
    "Please say: 'Hello Palintir, my name is " + personName + "'",
    "Please say: 'The quick brown fox jumps over the lazy dog'",
    "Please say: 'Today is a great day to learn something new'",
    "Please say: 'Can you tell me what time the class ends?'",
    "Please say any sentence naturally for a few seconds",
  ];

  const currentPrompt = prompts[Math.min(status?.voice_samples ?? 0, prompts.length - 1)];

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: 16000, channelCount: 1 },
      });

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: "audio/webm;codecs=opus",
      });
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });

        // Convert to PCM int16 via AudioContext
        setProcessing(true);
        try {
          const arrayBuffer = await blob.arrayBuffer();
          const audioCtx = new AudioContext({ sampleRate: 16000 });
          const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
          const pcmFloat = audioBuffer.getChannelData(0);

          // Convert float32 to int16
          const pcmInt16 = new Int16Array(pcmFloat.length);
          for (let i = 0; i < pcmFloat.length; i++) {
            const s = Math.max(-1, Math.min(1, pcmFloat[i]));
            pcmInt16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }

          // Base64 encode
          const bytes = new Uint8Array(pcmInt16.buffer);
          let binary = "";
          for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          const base64 = btoa(binary);

          const result = await api.post<VoiceEnrollmentStatus>(
            `/enrollment/persons/${personId}/voice`,
            { audio_base64: base64, sample_rate: 16000 },
          );
          setStatus(result);

          if (result.complete) {
            onComplete();
          }

          await audioCtx.close();
        } catch (err: unknown) {
          const message =
            err instanceof Error ? err.message : "Voice capture failed";
          alert(message);
        } finally {
          setProcessing(false);
        }
      };

      mediaRecorder.start();
      setRecording(true);

      // Auto-stop after 5 seconds
      setTimeout(() => {
        if (mediaRecorder.state === "recording") {
          mediaRecorder.stop();
          setRecording(false);
        }
      }, 5000);
    } catch {
      alert("Could not access microphone. Please grant permission.");
    }
  }, [personId, onComplete]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
      setRecording(false);
    }
  }, []);

  const samples = status?.voice_samples ?? 0;
  const required = status?.required_samples ?? 5;

  return (
    <div className="max-w-md mx-auto">
      <h2 className="text-xl font-bold mb-2">
        Voice Enrollment - {personName}
      </h2>
      <p className="text-gray-500 text-sm mb-4">
        {samples}/{required} voice samples captured.
      </p>

      {/* Progress */}
      <div className="w-full bg-gray-200 rounded-full h-2 mb-6">
        <div
          className="bg-indigo-600 h-2 rounded-full transition-all"
          style={{ width: `${(samples / required) * 100}%` }}
        />
      </div>

      {/* Prompt */}
      <div className="bg-indigo-50 rounded-lg p-4 mb-6 text-center">
        <p className="text-indigo-800 font-medium">{currentPrompt}</p>
      </div>

      {/* Recording indicator */}
      {recording && (
        <div className="flex items-center justify-center gap-2 mb-4">
          <div className="w-3 h-3 rounded-full bg-red-500 animate-pulse" />
          <span className="text-sm text-red-600 font-medium">
            Recording...
          </span>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button
          onClick={onSkip}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm"
        >
          Skip Voice
        </button>
        {recording ? (
          <button
            onClick={stopRecording}
            className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700"
          >
            Stop Recording
          </button>
        ) : (
          <button
            onClick={startRecording}
            disabled={processing}
            className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {processing
              ? "Processing..."
              : `Record Sample (${samples}/${required})`}
          </button>
        )}
      </div>
    </div>
  );
}
