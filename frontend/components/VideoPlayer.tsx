"use client";
import { useEffect, useRef, useState } from "react";

const clock = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export default function VideoPlayer({ src, t, n }: { src: string; t: number; n: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [blocked, setBlocked] = useState(false); // the browser refused autoplay; the student starts it
  const [failed, setFailed] = useState(false);   // the browser cannot load or decode the file

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    setBlocked(false);
    const seek = () => {
      v.currentTime = t;
      v.play().then(() => setBlocked(false), () => setBlocked(true));
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [src, t, n]);

  if (failed) {
    return (
      <div className="space-y-2 rounded bg-slate-900 p-4 text-sm text-slate-100">
        <p>This browser could not play the lecture video (H.264). Open it in Chrome, Edge or Firefox, or download it and jump to {clock(t)}.</p>
        <a href={`${src}#t=${t}`} target="_blank" rel="noopener noreferrer" className="inline-block rounded border border-slate-500 px-3 py-1 underline">Open the video file</a>
      </div>
    );
  }

  return (
    <div className="relative">
      <video
        ref={ref}
        src={src}
        controls
        playsInline
        preload="metadata"
        onPlay={() => setBlocked(false)}
        onError={() => setFailed(true)}
        className="w-full rounded bg-black"
      />
      {blocked && (
        <button
          type="button"
          onClick={() => { const v = ref.current; if (v) { v.currentTime = t; v.play().catch(() => {}); } }}
          className="absolute inset-0 m-auto h-14 w-fit rounded-full bg-indigo-600/90 px-5 text-sm font-medium text-white shadow-lg hover:bg-indigo-500"
        >
          ▶ Play from {clock(t)}
        </button>
      )}
    </div>
  );
}
