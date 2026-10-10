"use client";
import { useRef, useState } from "react";
import { post } from "@/lib/api";

export default function VoiceButton({ onText }: { onText: (text: string) => void }) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);

  async function toggle() {
    if (recording) { rec.current?.stop(); return; }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const r = new MediaRecorder(stream);
      r.ondataavailable = (e) => chunks.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const fd = new FormData();
        fd.append("audio", new Blob(chunks, { type: r.mimeType }), "question.webm");
        try {
          const out = await post<{ text?: string }>("/api/voice/stt", fd);
          if (out.text) onText(out.text);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      };
      rec.current = r;
      r.start();
      setRecording(true);
    } catch {
      setError("Microphone is not available.");
    }
  }

  return (
    <span className="flex items-center gap-2">
      <button type="button" onClick={toggle} aria-pressed={recording} className={`rounded border px-2 py-1 text-sm ${recording ? "border-red-500 bg-red-50 text-red-700" : ""}`}>
        {recording ? "Stop" : "Speak"}
      </button>
      {error && <span role="alert" className="text-xs text-red-700">{error}</span>}
    </span>
  );
}
