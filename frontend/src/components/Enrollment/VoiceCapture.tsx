import { Mic, MicOff, SkipForward, Square } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { api } from "../../api/client";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";

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
    `"Hello Palantir, my name is ${personName}."`,
    `"The quick brown fox jumps over the lazy dog."`,
    `"Today is a great day to learn something new."`,
    `"Can you tell me what time the class ends?"`,
    `"Any short sentence — speak naturally for a few seconds."`,
  ];

  const promptIdx = Math.min(status?.voice_samples ?? 0, prompts.length - 1);
  const currentPrompt = prompts[promptIdx];

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
        setProcessing(true);
        try {
          const arrayBuffer = await blob.arrayBuffer();
          const audioCtx = new AudioContext({ sampleRate: 16000 });
          const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);
          const pcmFloat = audioBuffer.getChannelData(0);

          const pcmInt16 = new Int16Array(pcmFloat.length);
          for (let i = 0; i < pcmFloat.length; i++) {
            const s = Math.max(-1, Math.min(1, pcmFloat[i]));
            pcmInt16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }

          const bytes = new Uint8Array(pcmInt16.buffer);
          let binary = "";
          for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          const base64 = btoa(binary);

          const result = await api.post<VoiceEnrollmentStatus>(
            `/enrollment/persons/${personId}/voice`,
            { audio_base64: base64, sample_rate: 16000 }
          );
          setStatus(result);
          if (result.complete) onComplete();

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
  const pct = Math.min(100, (samples / required) * 100);

  return (
    <Panel label="CAPTURE" title="Voice enrollment" tone="amber" brackets>
      <div className="flex items-center justify-between mb-3">
        <span className="font-data text-[11px] text-gray-400 uppercase tracking-[0.18em]">
          &gt; {samples.toString().padStart(2, "0")} / {required} samples
        </span>
        {recording ? (
          <StatusPill tone="red" size="xs" pulse>
            RECORDING
          </StatusPill>
        ) : processing ? (
          <StatusPill tone="cyan" size="xs" pulse>
            PROCESSING
          </StatusPill>
        ) : (
          <StatusPill tone="gray" size="xs">
            READY
          </StatusPill>
        )}
      </div>

      <div className="h-1.5 bg-[#05080f] border border-[#1c2540] mb-5">
        <div
          className="h-full bg-amber-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Prompt */}
      <div className="bg-[#05080f] border border-amber-600/30 px-4 py-5 text-center mb-5">
        <div className="font-data text-[10px] uppercase tracking-[0.22em] text-amber-500 mb-2">
          // PROMPT {promptIdx + 1}
        </div>
        <p className="text-gray-100 text-base leading-relaxed">
          {currentPrompt}
        </p>
      </div>

      {/* Waveform placeholder — animated bars */}
      <div className="flex items-end justify-center gap-1 h-12 mb-5">
        {Array.from({ length: 28 }).map((_, i) => (
          <span
            key={i}
            className={[
              "w-1 bg-amber-500/60",
              recording ? "breathe" : "",
            ].join(" ")}
            style={{
              height: recording
                ? `${20 + ((i * 37 + Date.now()) % 80)}%`
                : "20%",
              animationDelay: `${(i % 8) * 0.08}s`,
            }}
          />
        ))}
      </div>

      <div className="flex gap-2 justify-end">
        <Button
          variant="ghost"
          onClick={onSkip}
          iconLeft={<SkipForward className="w-4 h-4" />}
        >
          SKIP VOICE
        </Button>
        {recording ? (
          <Button
            variant="danger"
            onClick={stopRecording}
            iconLeft={<Square className="w-4 h-4 fill-current" />}
          >
            STOP
          </Button>
        ) : (
          <Button
            variant="primary"
            onClick={startRecording}
            disabled={processing}
            loading={processing}
            iconLeft={
              processing ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />
            }
          >
            {processing ? "PROCESSING" : "RECORD 5s"}
          </Button>
        )}
      </div>
    </Panel>
  );
}
