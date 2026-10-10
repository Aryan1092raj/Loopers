"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { post } from "@/lib/api";
import { parseOptions } from "@/lib/quiz";
import { newQuizId, readQuizSets, recordAnswer, type QuizSet } from "@/lib/session";
import type { GraphNode, QuizItem } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";

type Mode = "practice" | "mock";
type Phase = "setup" | "loading" | "answering" | "feedback" | "done";

async function generate(concept: GraphNode): Promise<QuizItem | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await post<{ status: string; item?: QuizItem }>("/api/quiz/generate", { concept_id: concept.id, topic_id: concept.parent_id ?? "" });
    if (r.status === "success" && r.item) return r.item;
  }
  return null;
}

export default function QuizRunner({ studentId, concepts, names }: { studentId: string; concepts: GraphNode[]; names: Map<string, string> }) {
  const router = useRouter();
  // Refs, not state: load/advance/finish run async and must see the values of the current attempt, not of the render that started it.
  const quizId = useRef(newQuizId());
  const answered = useRef(0);
  const skippedRef = useRef(0);
  const totalRef = useRef(4);
  const retryRef = useRef<QuizSet | null>(null); // set while replaying a saved quiz
  const [phase, setPhase] = useState<Phase>("setup");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [count, setCount] = useState(4);
  const [mode, setMode] = useState<Mode>("practice");
  const [index, setIndex] = useState(0);
  const [item, setItem] = useState<QuizItem | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [correct, setCorrect] = useState<boolean | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [answeredN, setAnsweredN] = useState(0);
  const [noQuestions, setNoQuestions] = useState(false);
  const [total, setTotal] = useState(4);
  const [done, setDone] = useState<{ answered: number; total: number; note: string | null } | null>(null);
  const [sets, setSets] = useState<QuizSet[]>([]);
  useEffect(() => setSets(readQuizSets()), []);

  const chosen = concepts.filter((c) => picked.has(c.id));
  /** "Topic: X · Concepts: A, B" for a saved quiz, from the concept graph the page already loaded. */
  const covers = (s: QuizSet) => {
    const ids = [...new Set(s.items.map((i) => i.concept_id))];
    const topics = [...new Set(ids.map((id) => concepts.find((c) => c.id === id)?.parent_id).filter((t): t is string => !!t))];
    const label = (id: string) => names.get(id) ?? id;
    return `${topics.length ? `Topic: ${topics.map(label).join(", ")} · ` : ""}Concepts: ${ids.map(label).join(", ")}`;
  };

  function finish(note: string | null = null) {
    if (answered.current === 0) {
      // every slot was rejected or skipped: an empty report would only say "no answers", so go back to setup
      setLeft(null);
      setIndex(0);
      setSkipped(0);
      setNoQuestions(true);
      setPhase("setup");
      return;
    }
    setLeft(null);
    const missed = skippedRef.current;
    setDone({
      answered: answered.current,
      total: totalRef.current,
      note: note ?? (missed > 0 ? "No more new questions were available for the rest of this quiz." : null),
    });
    setPhase("done");
  }

  async function load(i: number): Promise<void> {
    setPhase("loading");
    setError(null);
    try {
      const next = retryRef.current ? retryRef.current.items[i] : await generate(chosen[i % chosen.length]);
      if (!next) {
        skippedRef.current += 1;
        setSkipped((n) => n + 1);
        return advance(i);
      }
      setItem(next);
      setChoice(null);
      setCorrect(null);
      setPhase("answering");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("answering");
    }
  }

  function advance(i: number): void | Promise<void> {
    if (i + 1 >= totalRef.current) return finish();
    setIndex(i + 1);
    return load(i + 1);
  }

  async function begin(n: number, retry: QuizSet | null) {
    answered.current = 0;
    skippedRef.current = 0;
    totalRef.current = n;
    quizId.current = newQuizId();
    retryRef.current = retry;
    setTotal(n);
    setAnsweredN(0);
    setDone(null);
    setNoQuestions(false);
    setSkipped(0);
    setIndex(0);
    if (mode === "mock") setLeft(n * 90);
    await load(0);
  }
  const start = () => begin(count, null);
  const retry = (set: QuizSet) => begin(set.items.length, set);

  async function submit() {
    if (!item || choice === null) return;
    setError(null);
    try {
      const r = await post<{ correct: boolean }>(`/api/quiz/submit?student_id=${encodeURIComponent(studentId)}`, {
        quiz_id: quizId.current, item_id: item.item_id, student_answer: [choice],
      });
      answered.current += 1;
      setAnsweredN(answered.current);
      recordAnswer(quizId.current, item, retryRef.current?.n ?? null);
      setCorrect(r.correct);
      if (mode === "mock") await advance(index);
      else setPhase("feedback");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const expired = left !== null && left <= 0;
  useEffect(() => {
    if (left === null || expired) return;
    const t = setTimeout(() => setLeft(left - 1), 1000);
    return () => clearTimeout(t);
  }, [left, expired]);
  useEffect(() => { if (expired) finish("Time is up."); }, [expired]);

  if (concepts.length === 0) {
    return <Empty>No concepts to quiz on yet. <Link href="/upload" className="underline">Upload material</Link> first.</Empty>;
  }

  if (phase === "setup") {
    return (
      <form onSubmit={(e) => { e.preventDefault(); start(); }} className="space-y-4 rounded border bg-white p-4">
        <ErrorNote message={noQuestions ? "No new questions are available for these concepts yet. Pick other concepts or try again later." : null} />
        {noQuestions && picked.size > 0 && (
          <button type="button" onClick={start} className="rounded border px-3 py-1 text-sm hover:bg-neutral-100">Try again with the same concepts</button>
        )}
        <fieldset className="space-y-1">
          <legend className="font-medium">Scope</legend>
          <div className="grid max-h-56 gap-1 overflow-y-auto sm:grid-cols-2">
            {concepts.map((c) => (
              <label key={c.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={picked.has(c.id)}
                  onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(c.id); else n.delete(c.id); return n; })}
                />
                {c.name}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex flex-wrap items-end gap-4 text-sm">
          <label className="flex flex-col gap-1">Questions
            <input type="number" min={1} max={20} value={count} onChange={(e) => setCount(Math.max(1, Number(e.target.value) || 1))} className="w-20 rounded border px-2 py-1" />
          </label>
          <label className="flex flex-col gap-1">Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} className="rounded border bg-white px-2 py-1">
              <option value="practice">Practice (feedback after each answer)</option>
              <option value="mock">Mock exam (timed, feedback at the end)</option>
            </select>
          </label>
        </div>
        <button type="submit" disabled={picked.size === 0} className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Start a new quiz</button>
        {sets.length > 0 && (
          <section aria-label="Your quizzes" className="space-y-2 border-t pt-3">
            <h2 className="font-medium">Your quizzes</h2>
            <p className="text-xs text-neutral-500">Retry replays the same questions, in the mode chosen above.</p>
            <ul className="space-y-2 text-sm">
              {[...sets].reverse().map((s) => (
                <li key={s.n} className="flex flex-wrap items-center gap-3 rounded border p-2">
                  <span className="font-medium">Quiz {s.n}</span>
                  <span className="text-neutral-600">{s.items.length} question{s.items.length === 1 ? "" : "s"} · {s.attempts.length} attempt{s.attempts.length === 1 ? "" : "s"}</span>
                  <button type="button" onClick={() => retry(s)} className="ml-auto rounded border px-2 py-0.5 hover:bg-neutral-100">Retry</button>
                  <Link href={`/report?quiz=${encodeURIComponent(s.attempts[s.attempts.length - 1].id)}`} className="underline">Latest report</Link>
                  <p className="w-full text-xs text-neutral-600">{covers(s)}</p>
                </li>
              ))}
            </ul>
          </section>
        )}
      </form>
    );
  }

  if (phase === "done" && done) {
    return (
      <section aria-label="Quiz result" className="space-y-3 rounded border bg-white p-4">
        <p className="font-medium text-green-800">Quiz completed successfully.</p>
        <p className="text-sm">You answered {done.answered} of {done.total} question{done.total === 1 ? "" : "s"}.</p>
        {done.note && <p className="text-sm text-neutral-600">{done.note}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => router.push(`/report?quiz=${encodeURIComponent(quizId.current)}`)}
            className="rounded bg-neutral-900 px-3 py-1 text-white"
          >
            View report
          </button>
          <button
            type="button"
            onClick={() => { setSets(readQuizSets()); setDone(null); setPhase("setup"); }}
            className="rounded border px-3 py-1 hover:bg-neutral-100"
          >
            Back to quizzes
          </button>
        </div>
      </section>
    );
  }

  const last = index + 1 >= total;
  return (
    <section aria-label="Quiz question" className="space-y-3 rounded border bg-white p-4">
      <div className="flex items-center justify-between text-xs text-neutral-600">
        <span>Question {index + 1} of {total}{skipped > 0 ? ` · ${skipped} skipped` : ""}</span>
        {left !== null && <span role="timer">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left</span>}
      </div>
      <ErrorNote message={error} />
      {phase === "loading" && <p className="text-sm text-neutral-500">Preparing a verified question…</p>}
      {item && phase !== "loading" && (
        <>
          <p className="text-xs text-neutral-500">{concepts.find((c) => c.id === item.concept_id)?.name ?? item.concept_id} · {item.type} · {item.bloom}</p>
          <p className="font-medium">{item.stem}</p>
          <div className="grid gap-2" role="radiogroup" aria-label="Answer options">
            {parseOptions(item.options_json).map((o) => (
              <button
                key={o}
                type="button"
                role="radio"
                aria-checked={choice === o}
                disabled={phase === "feedback"}
                onClick={() => setChoice(o)}
                className={`rounded border px-3 py-1 text-left ${choice === o ? "border-blue-600 bg-blue-50" : "hover:bg-neutral-100"}`}
              >
                {o}
              </button>
            ))}
          </div>
          {phase === "answering" && (
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={submit} disabled={choice === null} className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Submit answer</button>
              {answeredN > 0 && (
                <button type="button" onClick={() => finish("You ended the quiz early.")} className="rounded border px-3 py-1 hover:bg-neutral-100">Finish quiz</button>
              )}
            </div>
          )}
          {phase === "feedback" && (
            <div className="flex flex-wrap items-center gap-3">
              <span className={`rounded px-2 py-0.5 text-xs ${correct ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>{correct ? "Correct" : "Not quite"}</span>
              {!last && <button type="button" onClick={() => advance(index)} className="rounded bg-neutral-900 px-3 py-1 text-white">Next question</button>}
              <button
                type="button"
                onClick={() => (last ? finish() : finish("You ended the quiz early."))}
                className={last ? "rounded bg-neutral-900 px-3 py-1 text-white" : "rounded border px-3 py-1 hover:bg-neutral-100"}
              >
                Finish quiz
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
