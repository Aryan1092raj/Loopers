"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Bars, Pie, Stat, asPct } from "@/components/Charts";
import { ErrorNote } from "@/components/Note";
import ReportView from "@/components/ReportView";
import { readQuizSets, useIds, type QuizSet } from "@/lib/session";
import { get } from "@/lib/api";
import { useStaggerIn } from "@/lib/motion";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import type { Graph, QuizReport } from "@/lib/types";

const reportPath = (quiz: string, studentId: string) => `/api/quiz/report/${encodeURIComponent(quiz)}?student_id=${encodeURIComponent(studentId)}`;

function PastAttempt({ id, number, at, studentId, names }: { id: string; number: number; at: string; studentId: string; names: Map<string, string> }) {
  const { data, error } = useGet<QuizReport>(reportPath(id, studentId));
  return (
    <details className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
      <summary className="cursor-pointer text-sm font-medium">
        Attempt {number} · {new Date(at).toLocaleString()}{data ? ` · ${Math.round(data.score * 100)}%` : ""}
      </summary>
      <div className="mt-3 space-y-2">
        <ErrorNote message={error} />
        {data && <ReportView report={data} names={names} />}
      </div>
    </details>
  );
}

/** Every attempt in this browser at a glance: totals, a right/wrong pie, score per attempt and mastery per topic. */
function Overview({ sets, studentId, graph }: { sets: QuizSet[]; studentId: string; graph: Graph | null }) {
  const attempts = sets.flatMap((s) => s.attempts.map((a, i) => ({ ...a, quiz: s.n, number: i + 1 }))).sort((a, b) => a.at.localeCompare(b.at));
  const [reports, setReports] = useState<Map<string, QuizReport> | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useStaggerIn(root, reports !== null);
  useEffect(() => {
    // ponytail: one request per attempt, newest 30; add a server-side summary endpoint if students keep hundreds
    const recent = attempts.slice(-30);
    Promise.all(recent.map((a) => get<QuizReport>(reportPath(a.id, studentId)).then((r) => [a.id, r] as const, () => null)))
      .then((rs) => setReports(new Map(rs.filter((r): r is readonly [string, QuizReport] => r !== null))));
  }, [sets.length, studentId]); // eslint-disable-line react-hooks/exhaustive-deps -- attempts derive from sets

  if (attempts.length === 0) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-2xl border border-indigo-200 bg-white/70 px-6 py-12 text-center shadow-sm">
        <span aria-hidden className="grid h-12 w-12 place-items-center rounded-2xl bg-indigo-100 text-xl text-indigo-700">✦</span>
        <h2 className="text-lg font-semibold">No quizzes yet</h2>
        <p className="text-base text-slate-700">Take a quiz and your report appears here: a score for every attempt, how you did by topic, and what to practise next.</p>
        <Link href="/quiz" className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white">Take a quiz</Link>
      </div>
    );
  }
  if (!reports) return <p className="text-sm text-slate-500">Building your report…</p>;

  const done = attempts.filter((a) => reports.get(a.id)?.raw_metrics);
  const total = done.reduce((s, a) => s + reports.get(a.id)!.raw_metrics!.total_items, 0);
  const right = done.reduce((s, a) => s + reports.get(a.id)!.raw_metrics!.correct_items, 0);
  const topics = graph ? topicsOf(graph).filter((t) => t.concepts.length) : [];
  const latest = [...attempts].reverse().find((a) => reports.has(a.id));

  return (
    <div ref={root} className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Quizzes" value={sets.length} />
        <Stat label="Attempts" value={attempts.length} />
        <Stat label="Questions answered" value={total} />
        <Stat label="Overall accuracy" value={total ? right / total : 0} format={asPct} />
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-xl font-semibold">All answers</h2>
          <Pie parts={[{ label: "Correct", value: right, color: "#059669" }, { label: "Wrong", value: total - right, color: "#dc2626" }]} />
        </section>
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-xl font-semibold">Score per attempt</h2>
          <Bars rows={done.map((a) => ({ label: `Quiz ${a.quiz} · attempt ${a.number}`, value: reports.get(a.id)!.score, hint: new Date(a.at).toLocaleDateString() }))} />
        </section>
      </div>
      {topics.length > 0 && (
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-xl font-semibold">Mastery by topic</h2>
          <Bars rows={[...topics].sort((a, b) => a.mastery - b.mastery).map((t) => ({ label: t.node.name, value: t.mastery, hint: `${t.concepts.length} concepts` }))} />
        </section>
      )}
      {latest && (
        <section className="space-y-3">
          <div className="flex items-baseline justify-between">
            <h2 className="text-xl font-semibold text-[#f6eeff]">Latest attempt</h2>
            <Link href={`/report?quiz=${encodeURIComponent(latest.id)}`} className="text-sm text-[#d6b8f5] underline">Open full report</Link>
          </div>
          <ReportView report={reports.get(latest.id)!} names={new Map((graph?.nodes ?? []).map((n) => [n.id, n.name]))} />
        </section>
      )}
    </div>
  );
}

function Report() {
  const ids = useIds();
  const quiz = useSearchParams().get("quiz");
  const [sets, setSets] = useState<QuizSet[]>([]);
  useEffect(() => {
    const local = readQuizSets();
    if (local.length > 0) {
      setSets(local);
    } else if (ids?.studentId) {
      get<Array<{ quiz_id: string; created_at: string }>>(`/api/students/${encodeURIComponent(ids.studentId)}/quizzes`)
        .then((serverQuizzes) => {
          if (serverQuizzes && serverQuizzes.length > 0) {
            setSets(serverQuizzes.map((sq, i) => ({
              n: i + 1,
              items: [],
              attempts: [{ id: sq.quiz_id, at: sq.created_at }]
            })));
          }
        })
        .catch(() => {});
    }
  }, [ids?.studentId]);
  const report = useGet<QuizReport>(ids && quiz ? reportPath(quiz, ids.studentId) : null);
  const graph = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const names = new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name]));
  const set = sets.find((s) => s.attempts.some((a) => a.id === quiz));
  const current = set ? set.attempts.findIndex((a) => a.id === quiz) + 1 : 0;
  const others = set ? set.attempts.map((a, i) => ({ ...a, number: i + 1 })).filter((a) => a.id !== quiz).reverse() : [];

  if (!quiz) {
    return (
      <div className="space-y-5">
        <header className="space-y-0.5"><h1 className="text-2xl font-semibold tracking-tight">Your report</h1><p className="text-base text-slate-700">Every quiz attempt, how you scored, and where to focus next.</p></header>
        <ErrorNote message={graph.error} />
        {ids && <Overview sets={sets} studentId={ids.studentId} graph={graph.data} />}
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">{set ? `Quiz ${set.n} · attempt ${current}` : "Quiz report"}</h1>
      <ErrorNote message={report.error} />
      {report.loading && !report.data && <p className="text-sm text-[#cdb3ea]">Building your report…</p>}
      {report.data && <ReportView report={report.data} names={names} />}
      {ids && others.length > 0 && (
        <section aria-label="Other attempts" className="space-y-2">
          <h2 className="text-xl font-semibold text-[#f6eeff]">Other attempts of Quiz {set?.n}</h2>
          {others.map((a) => <PastAttempt key={a.id} id={a.id} number={a.number} at={a.at} studentId={ids.studentId} names={names} />)}
        </section>
      )}
      <div className="flex gap-4 text-sm text-[#d6b8f5]">
        <Link href="/quiz" className="underline">Back to quizzes</Link>
        <Link href="/report" className="underline">All quizzes</Link>
        <Link href="/dashboard" className="underline">Go to dashboard</Link>
      </div>
    </div>
  );
}

export default function ReportPage() {
  return <Suspense fallback={null}><Report /></Suspense>;
}
