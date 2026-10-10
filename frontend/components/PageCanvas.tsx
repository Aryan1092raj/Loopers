"use client";
import { useState } from "react";
import { bboxStyle } from "@/lib/citation";

export default function PageCanvas({ url, bbox, alt }: { url: string; bbox: number[] | null; alt: string }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const box = bbox && size ? bboxStyle(bbox, size.w, size.h) : null;
  return (
    <div className="relative inline-block max-w-full">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={alt}
        className="block max-w-full rounded border"
        onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
      />
      {box && <div aria-hidden style={box} className="pointer-events-none absolute border-2 border-yellow-400 bg-yellow-300/30" />}
    </div>
  );
}
