"use client";
import { useEffect, useRef, useState } from "react";
import { animate } from "animejs";
import { post } from "@/lib/api";
import { reducedMotion } from "@/lib/motion";
import type { Citation, TutorResponse } from "@/lib/types";
import { usePersisted } from "@/lib/usePersisted";
import { readFaq, saveFaq, type FaqItem } from "@/lib/faq";
import AnswerView from "./AnswerView";
import { BorderBeamCard } from "./ui/border-beam-card";
import { HaloCard } from "./ui/halo-card";
import { ErrorNote } from "./Note";
import VoiceButton from "./VoiceButton";

interface Turn { q: string; r?: TutorResponse; error?: string }

const MAX_BOX_PX = 200;

export interface ChatScope { topic_ids: string[]; concept_ids: string[] }
const ALL: ChatScope = { topic_ids: [], concept_ids: [] };

export default function ChatPanel({ studentId, sessionId, onCite, name, scope = ALL, toolbar, chatKey = "all", topicName = "All topics" }: {
  studentId: string; sessionId: string; onCite: (c: Citation) => void; name?: string | null;
  scope?: ChatScope; toolbar?: React.ReactNode; // toolbar renders in the top-right of the chat
  chatKey?: string; topicName?: string; // the parent remounts this panel (key={chatKey}) when the topic changes
}) {
  const turnsKey = chatKey === "all" ? "loopers.study.turns" : `loopers.study.turns.${chatKey}`;
  const genKey = `loopers.study.gen.${chatKey}`;
  const [turns, setTurns] = usePersisted<Turn[]>(turnsKey, []);
  // clearing a chat bumps `gen`, so the tutor's server-side memory of the old chat is left behind too
  const [gen, setGen] = usePersisted<number>(genKey, 0);
  const tutorSession = `${sessionId}.${chatKey}.${gen}`;
  const [faq, setFaq] = useState<FaqItem[]>([]);
  const [faqOpen, setFaqOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);
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

  // Switching topic unmounts this panel: the chat's doubts go into the FAQ and the chat itself is cleared, so coming
  // back starts empty. A page refresh only saves to the FAQ; the chat survives it. A panel that never held any turns
  // (including React's dev-mode mount/unmount probe) has nothing to save or clear.
  const latest = useRef(turns);
  latest.current = turns;
  useEffect(() => {
    const onHide = () => saveFaq(chatKey, latest.current);
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      if (latest.current.length === 0) return;
      saveFaq(chatKey, latest.current);
      try {
        sessionStorage.removeItem(turnsKey);
        sessionStorage.setItem(genKey, JSON.stringify(Number(sessionStorage.getItem(genKey) ?? 0) + 1));
      } catch { /* storage blocked: the old chat may reappear */ }
    };
  }, [chatKey, turnsKey, genKey]);

  useEffect(() => {
    if (!faqOpen) return;
    setFaq(readFaq(chatKey));
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setFaqOpen(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [faqOpen, chatKey]);

  function clearChat() {
    saveFaq(chatKey, turns);
    setTurns([]);
    setGen((g) => g + 1);
    if (faqOpen) setFaq(readFaq(chatKey));
  }

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
        session_id: tutorSession, student_id: studentId, message,
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
    <HaloCard
      aria-label="Tutor chat" padding="sm" alwaysGlow className="h-full min-h-[32rem]"
      fill="linear-gradient(180deg, rgba(255,255,255,0.94) 0%, rgba(240,224,250,0.84) 45%, rgba(224,196,245,0.72) 100%)" // white into light purple: a calm wash, text stays readable
    >
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-end gap-2 pb-2">
        <button
          type="button"
          onClick={clearChat}
          disabled={turns.length === 0}
          className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:border-indigo-300 disabled:opacity-40"
        >
          Clear chat
        </button>
        {toolbar}
      </div>
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
        {/* the beam runs while the box has focus and while an answer is on its way */}
        <BorderBeamCard colorVariant="ocean" theme="light" strength={0.75} hueRange={10} active={focused || busy} cardClassName="gap-0 border-slate-200 py-0">
        <div className="flex items-end gap-2 p-2">
          <label className="sr-only" htmlFor="chat-input">Your question</label>
          <textarea
            id="chat-input"
            ref={box}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
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
        </BorderBeamCard>
      </form>
      <div className="relative mt-1 flex items-center justify-center">
        <p className="px-16 text-center text-xs text-slate-400">Enter to send · Shift+Enter for a new line · / to focus</p>
        <button
          type="button"
          onClick={() => setFaqOpen((o) => !o)}
          aria-expanded={faqOpen}
          aria-controls="chat-faq"
          className="absolute right-1 rounded-full border border-indigo-200 bg-white px-3 py-1 text-xs font-medium text-indigo-700 hover:border-indigo-400"
        >
          FAQ
        </button>
      </div>
      {faqOpen && (
        <section id="chat-faq" aria-label={`FAQ for ${topicName}`} className="absolute bottom-10 right-1 z-10 flex max-h-[60%] w-[min(26rem,100%)] flex-col rounded-2xl border border-indigo-200 bg-white shadow-lg">
          <header className="flex items-center justify-between gap-2 border-b border-indigo-100 px-4 py-2">
            <h2 className="truncate text-sm font-semibold">FAQ · {topicName}</h2>
            <button type="button" onClick={() => setFaqOpen(false)} aria-label="Close FAQ" className="text-slate-500 hover:text-slate-800">✕</button>
          </header>
          <div className="space-y-2 overflow-y-auto p-3 text-sm">
            {faq.length === 0 ? (
              <p className="text-slate-500">Nothing yet. Your questions and answers from this topic are saved here when you switch topic or clear the chat.</p>
            ) : (
              [...faq].reverse().map((f) => (
                <details key={f.q} className="rounded-lg border border-slate-200 px-3 py-2">
                  <summary className="cursor-pointer font-medium">{f.q}</summary>
                  <p className="mt-1.5 text-slate-700">{f.a}</p>
                </details>
              ))
            )}
          </div>
        </section>
      )}
    </div>
    </HaloCard>
  );
}
