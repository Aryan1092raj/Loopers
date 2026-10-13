"use client";
import { Suspense, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { ErrorNote } from "@/components/Note";
import QuizRunner from "@/components/QuizRunner";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useStaggerIn } from "@/lib/motion";
import { useGet } from "@/lib/useGet";
import type { Graph } from "@/lib/types";

function Quiz() {
  const ids = useIds();
  const topic = useSearchParams().get("topic");
  const { data, error, loading } = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const concepts = (data?.nodes ?? []).filter((n) => n.level !== "topic");
  const picked = data && topic ? (topicsOf(data).find((t) => t.node.id === topic)?.concepts ?? []).map((c) => c.id) : [];
  const root = useRef<HTMLDivElement>(null);
  useStaggerIn(root, !!data);
  return (
    <div ref={root} className="space-y-5">
      <header className="space-y-0.5">
        <h1 className="text-2xl font-semibold tracking-tight">Quiz</h1>
        <p className="text-sm text-slate-600">Pick what to practise, choose how many questions, and every answer comes back with its source.</p>
      </header>
      <ErrorNote message={error} />
      {(loading || !ids) && !data && <p className="text-sm text-neutral-500">Loading…</p>}
      {ids && data && (
        <QuizRunner key={topic ?? ""} studentId={ids.studentId} concepts={concepts} topics={data ? topicsOf(data) : []} names={new Map(data.nodes.map((n) => [n.id, n.name]))} initialPicked={picked} />
      )}
    </div>
  );
}

export default function QuizPage() {
  return <Suspense fallback={null}><Quiz /></Suspense>;
}
