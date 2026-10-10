"use client";
import { useEffect, useRef, useState } from "react";
import { post } from "@/lib/api";
import type { Citation, TutorResponse } from "@/lib/types";
import { usePersisted } from "@/lib/usePersisted";
import AnswerView from "./AnswerView";
import { ErrorNote } from "./Note";
import VoiceButton from "./VoiceButton";

interface Turn { q: string; r?: TutorResponse; error?: string }

export default function ChatPanel({ studentId, sessionId, onCite }: { studentId: string; sessionId: string; onCite: (c: Citation) => void }) {
  const [turns, setTurns] = usePersisted<Turn[]>("loopers.study.turns", []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns, busy]);

  async function ask(message: string, allowOutside = false) {
    if (!message.trim() || busy) return;
    setBusy(true);
    try {
      const r = await post<TutorResponse>("/api/chat", {
        session_id: sessionId, student_id: studentId, message,
        scope: { topic_ids: [], concept_ids: [] }, lang: "en", allow_outside: allowOutside,
      });
      setTurns((t) => [...t, { q: message, r }]);
    } catch (e) {
      setTurns((t) => [...t, { q: message, error: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Tutor chat" className="flex h-[75vh] flex-col rounded border bg-white">
      <div className="flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
        {turns.length === 0 && <p className="text-sm text-neutral-500">Ask a question about your course material.</p>}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <p className="ml-auto w-fit max-w-[85%] rounded bg-neutral-900 px-3 py-1 text-white">{t.q}</p>
            {t.r && <AnswerView r={t.r} onCite={onCite} onOutside={() => ask(t.q, true)} />}
            <ErrorNote message={t.error ?? null} />
          </div>
        ))}
        {busy && <p className="text-sm text-neutral-500">Thinking… the first answer after a restart can take a minute.</p>}
        <div ref={end} />
      </div>
      <form
        onSubmit={(e) => { e.preventDefault(); const m = input; setInput(""); ask(m); }}
        className="flex gap-2 border-t p-2"
      >
        <label className="sr-only" htmlFor="chat-input">Your question</label>
        <input
          id="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a concept…"
          className="flex-1 rounded border px-2 py-1"
        />
        <VoiceButton onText={setInput} />
        <button type="submit" disabled={busy || !input.trim()} className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Ask</button>
      </form>
    </section>
  );
}
