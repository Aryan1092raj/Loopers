"use client";
import { useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";

/* Monochrome line-art so every icon reads in the site's indigo/slate palette instead of mismatched emoji colours. */
const ICONS: Record<string, JSX.Element> = {
  sqrt: <path d="M2 13h2.2l2.3 7L9.5 4H21" />,
  pi: <path d="M3 7h18M7 7v13M17 7l-1 10a3 3 0 0 0 3 3" />,
  sigma: <path d="M19 4H6l7 8-7 8h13" />,
  infinity: <path d="M7 8a4 4 0 1 0 0 8 6 6 0 0 0 5-3 6 6 0 0 1 5-3 4 4 0 1 1 0 8 6 6 0 0 1-5-3 6 6 0 0 0-5-3" />,
  atom: (
    <>
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
      <ellipse cx="12" cy="12" rx="10" ry="4.2" />
      <ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(60 12 12)" />
      <ellipse cx="12" cy="12" rx="10" ry="4.2" transform="rotate(120 12 12)" />
    </>
  ),
  wave: <path d="M1 12c2 0 2-7 4-7s2 14 4 14 2-7 4-7 2-7 4-7 2 14 4 14 2-7 4-7" />,
  flask: <path d="M9 2h6M10 2v6.5L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 8.5V2M7.5 15h9" />,
  molecule: (
    <>
      <circle cx="6" cy="18" r="2.4" />
      <circle cx="18" cy="18" r="2.4" />
      <circle cx="12" cy="5" r="2.4" />
      <path d="M8 16.6 10.6 7M16 16.6 13.4 7M8.4 18h7.2" />
    </>
  ),
  dna: <path d="M6 2c0 6 12 14 12 20M18 2c0 6-12 14-12 20M7 7h10M6.5 12h11M7 17h10" />,
  leaf: <path d="M20 4C9 4 4 10 4 18c0 1.1.9 2 2 2 8 0 14-5 14-16ZM6 20 18 8" />,
};

const SUBJECTS: Record<keyof typeof ICONS, string> = {
  sqrt: "math", pi: "math", sigma: "math", infinity: "math",
  atom: "physics", wave: "physics",
  flask: "chemistry", molecule: "chemistry",
  dna: "biology", leaf: "biology",
};

type Sprite = {
  key: string;
  icon: keyof typeof ICONS;
  style: CSSProperties;
};

const ICON_KEYS = Object.keys(ICONS) as (keyof typeof ICONS)[];
const COUNT = 26;
/* One bright, distinct hue per subject so the four fields stay tellable apart against the dark background. */
const SUBJECT_COLORS: Record<string, string> = {
  math: "#fbbf24",
  physics: "#38bdf8",
  chemistry: "#34d399",
  biology: "#fb7185",
};

function rand(min: number, max: number) {
  return Math.random() * (max - min) + min;
}

function makeSprites(): Sprite[] {
  return Array.from({ length: COUNT }, (_, i) => {
    const icon = ICON_KEYS[i % ICON_KEYS.length];
    const size = rand(18, 36);
    return {
      key: `${icon}-${i}`,
      icon,
      style: {
        position: "absolute",
        top: `${rand(0, 96)}%`,
        left: `${rand(0, 96)}%`,
        width: size,
        height: size,
        color: SUBJECT_COLORS[SUBJECTS[icon]],
        opacity: rand(0.38, 0.68),
        "--dx1": `${rand(-40, 40)}px`,
        "--dy1": `${rand(-30, 30)}px`,
        "--dx2": `${rand(-50, 50)}px`,
        "--dy2": `${rand(-40, 40)}px`,
        "--r1": `${rand(-25, 25)}deg`,
        "--r2": `${rand(-25, 25)}deg`,
        animationDuration: `${rand(26, 50)}s`,
        animationDelay: `${rand(-30, 0)}s`,
      } as CSSProperties,
    };
  });
}

/** Small drifting science icons (math/physics/chemistry/biology) behind every page. Purely decorative. */
export default function ScienceBackground() {
  const [mounted, setMounted] = useState(false);
  // generated after mount so the random layout never disagrees with the server-rendered markup
  const sprites = useMemo(() => (mounted ? makeSprites() : []), [mounted]);

  useEffect(() => setMounted(true), []);

  return (
    <div aria-hidden className="science-bg pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {sprites.map((s) => (
        <svg
          key={s.key}
          className="science-bg-sprite"
          style={s.style}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.4}
          strokeLinecap="round"
          strokeLinejoin="round"
          data-subject={SUBJECTS[s.icon]}
        >
          {ICONS[s.icon]}
        </svg>
      ))}
    </div>
  );
}
