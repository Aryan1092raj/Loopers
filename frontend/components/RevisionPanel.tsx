"use client";
import Link from "next/link";
import { useState } from "react";
import { post } from "@/lib/api";
import { chipLabel } from "@/lib/citation";
import { speak } from "@/lib/speak";
import type { Brief, Citation, Flashcard } from "@/lib/types";
import FlashcardDeck from "./FlashcardDeck";
import { ErrorNote } from "./Note";

const cite = (c: Citation) => (
  <Link key={c.unit_id} href={`/?cite=${encodeURIComponent(c.unit_id)}`} className="rounded bg-indigo-50 px-1.5 py-0.5 text-xs text-indigo-700 hover:bg-indigo-100">
    {chipLabel(c)}
  </Link>
);

/** Revision aids for the student's weakest concepts: cited flashcards and a short spoken brief. */
export default function RevisionPanel({ studentId }: { studentId: string }) {
  const [cards, setCards] = useState<Flashcard[] | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [busy, setBusy] = useState<"cards" | "brief" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load<T>(which: "cards" | "brief", fn: () => Promise<T>, set: (v: T) => void) {
    setBusy(which);
    setError(null);
    try { set(await fn()); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-sm">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => load("cards", () => post<{ cards: Flashcard[] }>("/api/revision/flashcards", { student_id: studentId, n: 6 }), (r) => setCards(r.cards))}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-white transition hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy === "cards" ? "Making cards…" : "Flashcards for weak spots"}
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => load("brief", () => post<Brief>("/api/revision/brief", { student_id: studentId }), setBrief)}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 transition hover:border-indigo-300 disabled:opacity-50"
        >
          {busy === "brief" ? "Writing brief…" : "30-second brief"}
        </button>
      </div>
      <ErrorNote message={error} />
      {cards && (cards.length === 0
        ? <p className="text-sm text-slate-500">No cards yet: upload material and let the concepts stage finish.</p>
        : <FlashcardDeck key={cards.map((c) => c.front).join("|")} cards={cards} />)}
      {brief && (brief.text
        ? (
          <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3 text-sm shadow-sm">
            <p>{brief.text}</p>
            <div className="flex flex-wrap items-center gap-1">
              <button type="button" onClick={() => speak(brief.text)} className="mr-2 rounded-lg border border-slate-200 px-2 py-1 hover:border-indigo-300">Listen</button>
              {brief.citations.map(cite)}
            </div>
          </div>
        )
        : <p className="text-sm text-slate-500">No brief yet: upload material and let the concepts stage finish.</p>)}
    </div>
  );
}
