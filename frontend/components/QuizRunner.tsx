"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { animate } from "animejs";
import { post } from "@/lib/api";
import { reducedMotion } from "@/lib/motion";
import { parseOptions } from "@/lib/quiz";
import { newQuizId, readQuizSets, recordAnswer, type QuizSet } from "@/lib/session";
import { chipLabel } from "@/lib/citation";
import type { Topic } from "@/lib/topics";
import type { GraphNode, QuizFeedback, QuizItem } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";

type Mode = "practice" | "mock";
type Phase = "setup" | "loading" | "answering" | "feedback" | "done";

const MIXED_TYPES = ["mcq", "short", "numeric"] as const;

async function generate(concept: GraphNode, itemType: string): Promise<QuizItem | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const r = await post<{ status: string; item?: QuizItem }>("/api/quiz/generate", { concept_id: concept.id, topic_id: concept.parent_id ?? "", item_type: itemType });
    if (r.status === "success" && r.item) return r.item;
  }
  return null;
}

/** One topic as a selectable card: the card toggles the whole topic; "Pick concepts" opens per-concept chips. */
function TopicCard({ name, concepts, mastery, picked, setPicked }: {
  name: string; concepts: GraphNode[]; mastery: number | null; picked: Set<string>; setPicked: React.Dispatch<React.SetStateAction<Set<string>>>;
}) {
  const n = concepts.filter((c) => picked.has(c.id)).length;
  const [open, setOpen] = useState(n > 0 && n < concepts.length); // a deep link that picked part of a topic shows which part
  const all = n === concepts.length;
  const setAll = (on: boolean) => setPicked((s) => { const next = new Set(s); for (const c of concepts) { if (on) next.add(c.id); else next.delete(c.id); } return next; });
  const toggle = (id: string) => setPicked((s) => { const next = new Set(s); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  return (
    <div className={`rounded-2xl border bg-white p-3 shadow-sm transition ${n ? "border-indigo-400 ring-2 ring-indigo-100" : "border-slate-200 hover:border-indigo-300"}`}>
      <button type="button" aria-pressed={all} onClick={() => setAll(!all)} className="flex w-full items-start gap-3 text-left">
        <span aria-hidden className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md border text-xs ${all ? "border-indigo-600 bg-indigo-600 text-white" : n ? "border-indigo-600 bg-indigo-100 text-indigo-700" : "border-slate-300"}`}>
          {all ? "✓" : n ? "–" : ""}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium text-slate-900">{name}</span>
          <span className="block text-xs text-slate-500">{n ? `${n} of ${concepts.length} concepts` : `${concepts.length} concept${concepts.length === 1 ? "" : "s"}`}</span>
        </span>
        {mastery !== null && (
          <span className="flex flex-col items-end gap-1 text-[11px] text-slate-500" aria-label={`Mastery ${Math.round(mastery * 100)}%`}>
            {Math.round(mastery * 100)}%
            <span className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100"><span className="block h-full rounded-full bg-indigo-500" style={{ width: `${Math.round(mastery * 100)}%` }} /></span>
          </span>
        )}
      </button>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="mt-2 text-xs text-indigo-700 hover:underline">
        {open ? "Hide concepts" : "Pick concepts"}
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {concepts.map((c) => (
            <button
              key={c.id} type="button" aria-pressed={picked.has(c.id)} onClick={() => toggle(c.id)}
              className={`rounded-full border px-2.5 py-1 text-xs transition ${picked.has(c.id) ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-200 bg-slate-50 text-slate-700 hover:border-indigo-300"}`}
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Row of mutually exclusive buttons, used for the quiz settings. */
function Segmented<T extends string | number>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</legend>
      <div className="flex rounded-xl bg-slate-100 p-1">
        {options.map(([v, text]) => (
          <button
            key={String(v)} type="button" aria-pressed={v === value} onClick={() => onChange(v)}
            className={`flex-1 rounded-lg px-2 py-1.5 text-sm transition ${v === value ? "bg-white font-medium text-indigo-700 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
          >
            {text}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

export default function QuizRunner({ studentId, concepts, topics = [], names, initialPicked = [] }: { studentId: string; concepts: GraphNode[]; topics?: Topic[]; names: Map<string, string>; initialPicked?: string[] }) {
  const router = useRouter();
  // Refs, not state: load/advance/finish run async and must see the values of the current attempt, not of the render that started it.
  const quizId = useRef(newQuizId());
  const answered = useRef(0);
  const skippedRef = useRef(0);
  const totalRef = useRef(4);
  const retryRef = useRef<QuizSet | null>(null); // set while replaying a saved quiz
  const [phase, setPhase] = useState<Phase>("setup");
  const [picked, setPicked] = useState<Set<string>>(() => new Set(initialPicked));
  const [count, setCount] = useState(4);
  const [mode, setMode] = useState<Mode>("practice");
  const [mixed, setMixed] = useState(false);
  const [index, setIndex] = useState(0);
  const [item, setItem] = useState<QuizItem | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [correct, setCorrect] = useState<boolean | null>(null);
  const [feedback, setFeedback] = useState<QuizFeedback | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [answeredN, setAnsweredN] = useState(0);
  const [noQuestions, setNoQuestions] = useState(false);
  const [total, setTotal] = useState(4);
  const [done, setDone] = useState<{ answered: number; total: number; note: string | null } | null>(null);
  const [sets, setSets] = useState<QuizSet[]>([]);
  useEffect(() => setSets(readQuizSets()), []);
  const card = useRef<HTMLElement>(null);
  const verdict = useRef<HTMLDivElement>(null);
  // each new question slides in; the verdict pops in, and a wrong answer gives a small shake
  useEffect(() => {
    if (!card.current || reducedMotion() || phase !== "answering") return;
    animate(card.current, { opacity: [0, 1], x: [24, 0], duration: 320, ease: "out(3)" });
  }, [item?.item_id]); // eslint-disable-line react-hooks/exhaustive-deps -- only on a new question
  useEffect(() => {
    if (!verdict.current || reducedMotion()) return;
    animate(verdict.current, correct ? { scale: [0.96, 1], opacity: [0, 1], duration: 300, ease: "out(3)" } : { x: [0, -8, 8, -5, 5, 0], duration: 400, ease: "inOut(2)" });
  }, [correct]);

  const chosen = concepts.filter((c) => picked.has(c.id));
  // Concepts grouped under their topic; concepts outside every topic go last under "Other".
  const groups = useMemo(() => {
    const inTopic = new Set(topics.flatMap((t) => t.concepts.map((c) => c.id)));
    const other = concepts.filter((c) => !inTopic.has(c.id));
    return [
      ...topics.filter((t) => t.concepts.length).map((t) => ({ id: t.node.id, name: t.node.name, concepts: t.concepts, mastery: t.mastery as number | null })),
      ...(other.length ? [{ id: "other", name: "Other", concepts: other, mastery: null }] : []),
    ];
  }, [topics, concepts]);
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
      const next = retryRef.current ? retryRef.current.items[i] : await generate(chosen[i % chosen.length], mixed ? MIXED_TYPES[i % MIXED_TYPES.length] : "mcq");
      if (!next) {
        skippedRef.current += 1;
        setSkipped((n) => n + 1);
        return advance(i);
      }
      setItem(next);
      setChoice(null);
      setCorrect(null);
      setFeedback(null);
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
    if (!item || !choice?.trim()) return;
    setError(null);
    try {
      const r = await post<{ correct: boolean; feedback?: QuizFeedback }>(`/api/quiz/submit?student_id=${encodeURIComponent(studentId)}`, {
        quiz_id: quizId.current, item_id: item.item_id, student_answer: [choice.trim()],
      });
      setFeedback(r.feedback ?? null);
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
    return <Empty>No concepts to quiz on yet. <Link href="/" className="underline">Upload material</Link> first.</Empty>;
  }

  if (phase === "setup") {
    return (
      <form onSubmit={(e) => { e.preventDefault(); start(); }} className="space-y-8">
        <ErrorNote message={noQuestions ? "No new questions are available for these concepts yet. Pick other concepts or try again later." : null} />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <section aria-label="Scope" className="space-y-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="font-medium">Choose topics</h2>
              <div className="flex gap-3 text-xs">
                <button type="button" onClick={() => setPicked(new Set(concepts.map((c) => c.id)))} className="text-indigo-700 hover:underline">Select all</button>
                <button type="button" onClick={() => setPicked(new Set())} disabled={picked.size === 0} className="text-slate-500 hover:underline disabled:opacity-40">Clear</button>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {groups.map((g) => <TopicCard key={g.id} name={g.name} concepts={g.concepts} mastery={g.mastery} picked={picked} setPicked={setPicked} />)}
            </div>
          </section>

          <aside aria-label="Quiz settings" className="h-fit space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:sticky lg:top-4">
            <Segmented label="Questions" value={[4, 8, 12].includes(count) ? count : 4} options={[[4, "4"], [8, "8"], [12, "12"]]} onChange={setCount} />
            <Segmented label="Mode" value={mode} options={[["practice", "Practice"], ["mock", "Mock exam"]]} onChange={setMode} />
            <Segmented label="Question types" value={mixed ? "mixed" : "mcq"} options={[["mcq", "Multiple choice"], ["mixed", "Mixed"]]} onChange={(v) => setMixed(v === "mixed")} />
            <p className="text-xs text-slate-500">
              {mode === "mock" ? `Timed: ${Math.round((count * 90) / 60)} min, feedback at the end.` : "Feedback with sources after each answer."}
              {mixed ? " Mixes multiple choice, short answer and numeric." : ""}
            </p>
            <div className="rounded-xl bg-slate-50 p-3 text-sm">
              <p><strong>{picked.size}</strong> concept{picked.size === 1 ? "" : "s"} · <strong>{count}</strong> questions</p>
              <p className="text-xs text-slate-500">Every question is checked by a second model before you see it.</p>
            </div>
            <button type="submit" disabled={picked.size === 0} className="w-full rounded-xl bg-indigo-600 px-4 py-2.5 font-medium text-white shadow-sm transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40">
              {picked.size === 0 ? "Pick a topic to start" : "Start quiz"}
            </button>
            {noQuestions && picked.size > 0 && (
              <button type="button" onClick={start} className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm hover:border-indigo-300">Try again with the same concepts</button>
            )}
          </aside>
        </div>

        {sets.length > 0 && (
          <section aria-label="Your quizzes" className="space-y-3">
            <div className="flex items-baseline justify-between">
              <h2 className="font-medium">Your quizzes</h2>
              <Link href="/report" className="text-sm text-indigo-700 hover:underline">All reports</Link>
            </div>
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[...sets].reverse().map((s) => (
                <li key={s.n} className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex items-baseline justify-between">
                    <span className="font-semibold">Quiz {s.n}</span>
                    <span className="text-xs text-slate-500">{s.items.length} question{s.items.length === 1 ? "" : "s"} · {s.attempts.length} attempt{s.attempts.length === 1 ? "" : "s"}</span>
                  </div>
                  <p className="line-clamp-2 flex-1 text-xs text-slate-600">{covers(s)}</p>
                  <div className="flex gap-2 text-sm">
                    <button type="button" onClick={() => retry(s)} className="flex-1 rounded-lg border border-slate-200 px-2 py-1.5 hover:border-indigo-300">Retry</button>
                    <Link href={`/report?quiz=${encodeURIComponent(s.attempts[s.attempts.length - 1].id)}`} className="flex-1 rounded-lg bg-indigo-50 px-2 py-1.5 text-center text-indigo-700 hover:bg-indigo-100">Report</Link>
                  </div>
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
      <section aria-label="Quiz result" className="mx-auto max-w-2xl space-y-4 rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm">
        <p className="text-lg font-semibold text-emerald-700">Quiz complete</p>
        <p className="text-sm">You answered {done.answered} of {done.total} question{done.total === 1 ? "" : "s"}.</p>
        {done.note && <p className="text-sm text-neutral-600">{done.note}</p>}
        <div className="flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => router.push(`/report?quiz=${encodeURIComponent(quizId.current)}`)}
            className="rounded-xl bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500"
          >
            View report
          </button>
          <button
            type="button"
            onClick={() => { setSets(readQuizSets()); setDone(null); setPhase("setup"); }}
            className="rounded-xl border border-slate-200 px-4 py-2 hover:border-indigo-300"
          >
            Back to quizzes
          </button>
        </div>
      </section>
    );
  }

  const last = index + 1 >= total;
  return (
    <section ref={card} aria-label="Quiz question" className="mx-auto max-w-3xl space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden>
        <div className="h-full rounded-full bg-indigo-500 transition-all duration-500" style={{ width: `${((index + (phase === "feedback" ? 1 : 0)) / total) * 100}%` }} />
      </div>
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>Question {index + 1} of {total}{skipped > 0 ? ` · ${skipped} skipped` : ""}</span>
        {left !== null && <span role="timer" className={`rounded-full px-2 py-0.5 font-medium tabular-nums ${left < 60 ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-700"}`}>{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left</span>}
      </div>
      <ErrorNote message={error} />
      {phase === "loading" && <p className="text-sm text-neutral-500">Preparing a verified question…</p>}
      {item && phase !== "loading" && (
        <>
          <p className="text-xs font-medium uppercase tracking-wide text-indigo-600">{concepts.find((c) => c.id === item.concept_id)?.name ?? item.concept_id} · {item.type} · {item.bloom}</p>
          <p className="text-lg font-medium text-slate-900">{item.stem}</p>
          {parseOptions(item.options_json).length > 0 ? (
            <div className="grid gap-2" role="radiogroup" aria-label="Answer options">
              {parseOptions(item.options_json).map((o, i) => (
                <button
                  key={o}
                  type="button"
                  role="radio"
                  aria-checked={choice === o}
                  disabled={phase === "feedback"}
                  onClick={() => setChoice(o)}
                  className={`flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition ${choice === o ? "border-indigo-600 bg-indigo-50 ring-2 ring-indigo-100" : "border-slate-200 hover:border-indigo-300 hover:bg-slate-50"} disabled:cursor-default`}
                >
                  <span aria-hidden className={`grid h-7 w-7 shrink-0 place-items-center rounded-lg text-sm font-semibold ${choice === o ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600"}`}>{String.fromCharCode(65 + i)}</span>
                  {o}
                </button>
              ))}
            </div>
          ) : (
            <label className="flex flex-col gap-1 text-sm">
              {item.type === "numeric" ? "Your answer (a number)" : "Your answer (a sentence or two)"}
              {item.type === "numeric" ? (
                <input type="number" step="any" value={choice ?? ""} disabled={phase === "feedback"} onChange={(e) => setChoice(e.target.value)} className="w-48 rounded-xl border border-slate-300 px-3 py-2 disabled:opacity-60" />
              ) : (
                <textarea rows={3} value={choice ?? ""} disabled={phase === "feedback"} onChange={(e) => setChoice(e.target.value)} className="w-full rounded-xl border border-slate-300 px-3 py-2 disabled:opacity-60" />
              )}
            </label>
          )}
          {phase === "answering" && (
            <div className="flex flex-wrap items-center gap-3">
              <button type="button" onClick={submit} disabled={!choice?.trim()} className="rounded-xl bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500 disabled:opacity-40">Submit answer</button>
              {answeredN > 0 && (
                <button type="button" onClick={() => finish("You ended the quiz early.")} className="rounded-xl border border-slate-200 px-4 py-2 hover:border-indigo-300">Finish quiz</button>
              )}
            </div>
          )}
          {phase === "feedback" && (
            <div ref={verdict} className={`space-y-3 rounded-xl border p-4 ${correct ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}>
              <div role="status" aria-atomic="true">
                <span className={`text-sm font-semibold ${correct ? "text-emerald-800" : "text-red-800"}`}>{correct ? "✓ Correct" : "✗ Not quite"}</span>
              </div>
              {feedback && (
                <div className="space-y-2 text-sm">
                  {!correct && feedback.correct_answer.length > 0 && <p>Correct answer: <strong>{feedback.correct_answer.join(", ")}</strong></p>}
                  {feedback.explanation && <p className="text-neutral-700">{feedback.explanation}</p>}
                  {feedback.citations.length > 0 && (
                    <ul aria-label="Sources" className="space-y-1 rounded-lg bg-white/70 p-2 text-xs text-slate-600">
                      {feedback.citations.map((c) => (
                        <li key={c.id + c.unit_id}><span className="font-medium">{chipLabel(c)}</span>: {c.excerpt}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3">
                {!last && <button type="button" onClick={() => advance(index)} className="rounded-xl bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500">Next question</button>}
                <button
                  type="button"
                  onClick={() => (last ? finish() : finish("You ended the quiz early."))}
                  className={last ? "rounded-xl bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-500" : "rounded-xl border border-slate-200 bg-white px-4 py-2 hover:border-indigo-300"}
                >
                  Finish quiz
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
