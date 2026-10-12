"use client";
import Link from "next/link";
import { animate } from "animejs";
import { useEffect, useRef, useState } from "react";
import { chipLabel } from "@/lib/citation";
import { reducedMotion } from "@/lib/motion";
import type { Flashcard } from "@/lib/types";

/** A study deck: click or Space flips, arrows move, "Again" sends the card to the back of the deck. */
export default function FlashcardDeck({ cards }: { cards: Flashcard[] }) {
  const [queue, setQueue] = useState(() => cards.map((_, i) => i));
  const [known, setKnown] = useState(0);
  const [pos, setPos] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const inner = useRef<HTMLDivElement>(null);
  const shell = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);

  const done = queue.length === 0;
  const card = done ? null : cards[queue[pos % queue.length]];

  function flip() {
    if (!card) return;
    const next = !flipped;
    setFlipped(next);
    if (inner.current && !reducedMotion()) animate(inner.current, { rotateY: next ? 180 : 0, duration: 520, ease: "out(4)" });
    else if (inner.current) inner.current.style.transform = `rotateY(${next ? 180 : 0}deg)`;
  }

  // Slide the next card in from the side it came from, showing its front.
  function go(step: number, update?: () => void) {
    const run = () => {
      update?.();
      setPos((p) => p + step);
      setFlipped(false);
      if (inner.current) inner.current.style.transform = "rotateY(0deg)";
      if (shell.current && !reducedMotion()) animate(shell.current, { x: [step >= 0 ? 40 : -40, 0], opacity: [0, 1], duration: 300, ease: "out(3)" });
    };
    if (shell.current && !reducedMotion()) {
      animate(shell.current, { x: step >= 0 ? -40 : 40, opacity: 0, duration: 160, ease: "in(2)", onComplete: run });
    } else run();
  }

  function mark(gotIt: boolean) {
    if (!card) return;
    const i = pos % queue.length;
    go(0, () => {
      setQueue((q) => (gotIt ? q.filter((_, k) => k !== i) : [...q.filter((_, k) => k !== i), q[i]]));
      if (gotIt) setKnown((n) => n + 1);
    });
  }

  function restart() { setQueue(cards.map((_, i) => i)); setKnown(0); setPos(0); setFlipped(false); }

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (!root.current?.contains(document.activeElement) && document.activeElement !== document.body) return;
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === " ") { e.preventDefault(); flip(); }
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1 + queue.length); // backwards without going negative
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  if (done) {
    return (
      <div className="space-y-2 rounded-2xl border border-emerald-200 bg-emerald-50 p-6 text-center text-sm">
        <p className="font-medium text-emerald-800">Deck done: you knew all {cards.length} cards.</p>
        <button type="button" onClick={restart} className="rounded-lg border border-emerald-300 bg-white px-3 py-1.5 hover:border-emerald-500">Go again</button>
      </div>
    );
  }

  return (
    <div ref={root} className="max-w-xl space-y-3">
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>Card {(pos % queue.length) + 1} of {queue.length} left</span>
        <span>{known} known</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-indigo-500 transition-all" style={{ width: `${(known / cards.length) * 100}%` }} />
      </div>

      <div ref={shell} style={{ perspective: "1200px" }}>
        <div
          ref={inner}
          role="button"
          tabIndex={0}
          aria-label={flipped ? "Answer side. Press Space to see the question." : "Question side. Press Space to see the answer."}
          onClick={flip}
          className="relative h-56 cursor-pointer rounded-2xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-indigo-600"
          style={{ transformStyle: "preserve-3d" }}
        >
          <Face>
            <span className="text-xs font-medium uppercase tracking-wide text-indigo-600">Question</span>
            <p className="text-lg font-medium text-slate-900">{card!.front}</p>
            <span className="text-xs text-slate-400">Click or press Space to flip</span>
          </Face>
          <Face back>
            <span className="text-xs font-medium uppercase tracking-wide text-emerald-600">Answer</span>
            <p className="overflow-y-auto text-slate-800">{card!.back}</p>
            <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
              {card!.citations.map((c) => (
                <Link key={c.unit_id} href={`/?cite=${encodeURIComponent(c.unit_id)}`} className="rounded bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-700 hover:bg-indigo-100">
                  {chipLabel(c)}
                </Link>
              ))}
            </div>
          </Face>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button type="button" onClick={() => go(queue.length - 1)} aria-label="Previous card" className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 hover:border-indigo-300">←</button>
        <button type="button" onClick={() => go(1)} aria-label="Next card" className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 hover:border-indigo-300">→</button>
        <span className="flex-1" />
        <button type="button" disabled={!flipped} onClick={() => mark(false)} className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-amber-900 hover:border-amber-500 disabled:opacity-40">Again</button>
        <button type="button" disabled={!flipped} onClick={() => mark(true)} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-white hover:bg-emerald-500 disabled:opacity-40">Got it</button>
      </div>
    </div>
  );
}

function Face({ back = false, children }: { back?: boolean; children: React.ReactNode }) {
  return (
    <div
      className={`absolute inset-0 flex flex-col justify-between gap-3 rounded-2xl border p-5 shadow-md ${back ? "border-emerald-200 bg-gradient-to-br from-white to-emerald-50" : "border-indigo-200 bg-gradient-to-br from-white to-indigo-50"}`}
      style={{ backfaceVisibility: "hidden", WebkitBackfaceVisibility: "hidden", transform: back ? "rotateY(180deg)" : undefined }}
    >
      {children}
    </div>
  );
}
