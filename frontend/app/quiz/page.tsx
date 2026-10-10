"use client";
import { ErrorNote } from "@/components/Note";
import QuizRunner from "@/components/QuizRunner";
import { useIds } from "@/lib/session";
import { useGet } from "@/lib/useGet";
import type { Graph } from "@/lib/types";

export default function QuizPage() {
  const ids = useIds();
  const { data, error, loading } = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const concepts = (data?.nodes ?? []).filter((n) => n.level !== "topic");
  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-xl font-semibold">Quiz</h1>
      <ErrorNote message={error} />
      {(loading || !ids) && !data && <p className="text-sm text-neutral-500">Loading…</p>}
      {ids && data && <QuizRunner studentId={ids.studentId} concepts={concepts} names={new Map(data.nodes.map((n) => [n.id, n.name]))} />}
    </div>
  );
}
