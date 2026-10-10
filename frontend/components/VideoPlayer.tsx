"use client";
import { useEffect, useRef } from "react";

export default function VideoPlayer({ src, t, n }: { src: string; t: number; n: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    const seek = () => { v.currentTime = t; v.play().catch(() => {}); };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [src, t, n]);
  return <video ref={ref} src={src} controls preload="metadata" className="w-full rounded bg-black" />;
}
