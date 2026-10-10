"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ErrorNote } from "@/components/Note";
import QuizRunner from "@/components/QuizRunner";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import type { Graph } from "@/lib/types";

function Quiz() {
  const ids = useIds();
  const topic = useSearchParams().get("topic");
  const { data, error, loading } = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const concepts = (data?.nodes ?? []).filter((n) => n.level !== "topic");
  const picked = data && topic ? (topicsOf(data).find((t) => t.node.id === topic)?.concepts ?? []).map((c) => c.id) : [];
  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-xl font-semibold">Quiz</h1>
      <ErrorNote message={error} />
      {(loading || !ids) && !data && <p className="text-sm text-neutral-500">Loading…</p>}
      {ids && data && (
        <QuizRunner key={topic ?? ""} studentId={ids.studentId} concepts={concepts} names={new Map(data.nodes.map((n) => [n.id, n.name]))} initialPicked={picked} />
      )}
    </div>
  );
}

export default function QuizPage() {
  return <Suspense fallback={null}><Quiz /></Suspense>;
}
