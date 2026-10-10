"use client";
import { useRef, useState } from "react";

const ACCEPT = ".pdf,.pptx,.docx,.md,.txt,.mp4,.mkv,.webm,.mov,.mp3,.wav,.m4a";

export default function UploadZone({ onFile, disabled }: { onFile: (f: File) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files[0];
        if (f && !disabled) onFile(f);
      }}
      className={`rounded border-2 border-dashed p-8 text-center ${over ? "border-blue-500 bg-blue-50" : "border-neutral-300 bg-white"}`}
    >
      <p className="mb-3">Drop a lecture video, slide deck or textbook here</p>
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        Choose file
      </button>
      <input
        ref={input}
        type="file"
        hidden
        accept={ACCEPT}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}
