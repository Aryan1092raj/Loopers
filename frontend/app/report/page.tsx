"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Empty, ErrorNote } from "@/components/Note";
import ReportView from "@/components/ReportView";
import { readQuizSets, useIds, type QuizSet } from "@/lib/session";
import { useGet } from "@/lib/useGet";
import type { Graph, QuizReport } from "@/lib/types";

const reportPath = (quiz: string, studentId: string) => `/api/quiz/report/${encodeURIComponent(quiz)}?student_id=${encodeURIComponent(studentId)}`;

function PastAttempt({ id, number, at, studentId, names }: { id: string; number: number; at: string; studentId: string; names: Map<string, string> }) {
  const { data, error } = useGet<QuizReport>(reportPath(id, studentId));
  return (
    <details className="rounded border bg-white p-3">
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

function Report() {
  const ids = useIds();
  const quiz = useSearchParams().get("quiz");
  const [sets, setSets] = useState<QuizSet[]>([]);
  useEffect(() => setSets(readQuizSets()), []);
  const report = useGet<QuizReport>(ids && quiz ? reportPath(quiz, ids.studentId) : null);
  const graph = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const names = new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name]));
  const set = sets.find((s) => s.attempts.some((a) => a.id === quiz));
  const current = set ? set.attempts.findIndex((a) => a.id === quiz) + 1 : 0;
  const others = set ? set.attempts.map((a, i) => ({ ...a, number: i + 1 })).filter((a) => a.id !== quiz).reverse() : [];

  if (!quiz) return <Empty>No quiz selected. <Link href="/quiz" className="underline">Start a quiz</Link>.</Empty>;
  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-xl font-semibold">{set ? `Quiz ${set.n} · attempt ${current}` : "Quiz report"}</h1>
      <ErrorNote message={report.error} />
      {report.loading && !report.data && <p className="text-sm text-neutral-500">Building your report…</p>}
      {report.data && <ReportView report={report.data} names={names} />}
      {ids && others.length > 0 && (
        <section aria-label="Other attempts" className="space-y-2">
          <h2 className="font-medium">Other attempts of Quiz {set?.n}</h2>
          {others.map((a) => <PastAttempt key={a.id} id={a.id} number={a.number} at={a.at} studentId={ids.studentId} names={names} />)}
        </section>
      )}
      <div className="flex gap-4 text-sm">
        <Link href="/quiz" className="underline">Back to quizzes</Link>
        <Link href="/dashboard" className="underline">Go to dashboard</Link>
      </div>
    </div>
  );
}

export default function ReportPage() {
  return <Suspense fallback={null}><Report /></Suspense>;
}
