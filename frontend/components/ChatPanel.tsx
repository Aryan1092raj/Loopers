"use client";
import { useEffect, useRef, useState } from "react";
import { animate } from "animejs";
import { post } from "@/lib/api";
import { reducedMotion } from "@/lib/motion";
import type { Citation, TutorResponse } from "@/lib/types";
import { usePersisted } from "@/lib/usePersisted";
import AnswerView from "./AnswerView";
import { ErrorNote } from "./Note";
import VoiceButton from "./VoiceButton";

interface Turn { q: string; r?: TutorResponse; error?: string }

const MAX_BOX_PX = 200;

export interface ChatScope { topic_ids: string[]; concept_ids: string[] }
const ALL: ChatScope = { topic_ids: [], concept_ids: [] };

export default function ChatPanel({ studentId, sessionId, onCite, name, scope = ALL, toolbar }: {
  studentId: string; sessionId: string; onCite: (c: Citation) => void; name?: string | null;
  scope?: ChatScope; toolbar?: React.ReactNode; // toolbar renders in the top-right of the chat
}) {
  const [turns, setTurns] = usePersisted<Turn[]>("loopers.study.turns", []);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const last = useRef<HTMLDivElement>(null);
  // the newest turn rises in; older turns stay put
  useEffect(() => {
    if (!last.current || reducedMotion()) return;
    const a = animate(Array.from(last.current.children), { opacity: [0, 1], y: [10, 0], delay: (_: unknown, i: number) => i * 90, duration: 360, ease: "out(3)" });
    return () => { a.revert(); };
  }, [turns.length]);

  // keep the newest turn in view
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns, busy]);

  // grow the composer with its content, up to MAX_BOX_PX, then scroll inside it
  useEffect(() => {
    const t = box.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, MAX_BOX_PX)}px`;
    t.style.overflowY = t.scrollHeight > MAX_BOX_PX ? "auto" : "hidden";
  }, [input]);

  // "/" focuses the composer from anywhere except another field
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || el.closest("input, textarea, select, [contenteditable]")) return;
      e.preventDefault();
      box.current?.focus();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);

  async function ask(message: string, allowOutside = false) {
    if (!message.trim() || busy) return;
    setBusy(true);
    try {
      const r = await post<TutorResponse>("/api/chat", {
        session_id: sessionId, student_id: studentId, message,
        scope, lang: "en", allow_outside: allowOutside,
      });
      setTurns((t) => [...t, { q: message, r }]);
    } catch (e) {
      setTurns((t) => [...t, { q: message, error: e instanceof Error ? e.message : String(e) }]);
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  }

  return (
    <section aria-label="Tutor chat" className="flex h-full min-h-[32rem] flex-col">
      {toolbar && <div className="flex justify-end pb-2">{toolbar}</div>}
      <div className="flex-1 overflow-y-auto" aria-live="polite">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-2 py-6">
          {turns.length === 0 && (
            <div className="space-y-2 pt-[12vh] text-center">
              <h1 className="font-serif text-3xl text-slate-800">{name ? `What are we studying, ${name}?` : "What are we studying today?"}</h1>
              <p className="text-sm text-slate-500">Ask about your course material. Answers cite the page or video moment they came from.</p>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} ref={i === turns.length - 1 ? last : undefined} className="space-y-3">
              <p className="ml-auto w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl bg-slate-200/70 px-4 py-2 text-slate-900">{t.q}</p>
              {t.r && <AnswerView r={t.r} onCite={onCite} onOutside={() => ask(t.q, true)} />}
              <ErrorNote message={t.error ?? null} />
            </div>
          ))}
          {busy && <p className="text-sm text-slate-500">Thinking… the first answer after a restart can take a minute.</p>}
          <div ref={end} />
        </div>
      </div>
      <form
        onSubmit={(e) => { e.preventDefault(); const m = input; setInput(""); ask(m); }}
        className="mx-auto w-full max-w-3xl pb-1"
      >
        <div className="flex items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm transition focus-within:border-indigo-400 focus-within:shadow-md">
          <label className="sr-only" htmlFor="chat-input">Your question</label>
          <textarea
            id="chat-input"
            ref={box}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter adds a line; ignore Enter while an IME is composing
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); }
            }}
            placeholder="Ask about a concept…"
            className="flex-1 resize-none bg-transparent px-2 py-1.5 outline-none"
          />
          <VoiceButton onText={setInput} />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            aria-label="Send"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-indigo-600 text-white transition hover:bg-indigo-500 disabled:opacity-40"
          >
            ↑
          </button>
        </div>
        <p className="mt-1 text-center text-xs text-slate-400">Enter to send · Shift+Enter for a new line · / to focus</p>
      </form>
    </section>
  );
}
