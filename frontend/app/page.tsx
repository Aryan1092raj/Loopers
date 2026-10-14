"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import ChatPanel from "@/components/ChatPanel";
import CourseMap from "@/components/CourseMap";
import JobCard from "@/components/JobCard";
import { ErrorNote } from "@/components/Note";
import SourcePanel from "@/components/SourcePanel";
import SourcesTable from "@/components/SourcesTable";
import TopicPicker from "@/components/TopicPicker";
import UploadPanel from "@/components/UploadPanel";
import { get } from "@/lib/api";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";
import { useStudent } from "@/lib/useStudent";
import type { Citation, Graph } from "@/lib/types";

function Workspace() {
  const ids = useIds();
  const { name } = useStudent();
  const cite = useSearchParams().get("cite");
  const graph = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const [job, setJob] = usePersisted<{ id: string; title: string } | null>("loopers.upload.job", null);
  const [sourcesTick, setSourcesTick] = useState(0);
  const [active, setActive] = useState<{ c: Citation; n: number } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const open = (c: Citation) => setActive((a) => ({ c, n: (a?.n ?? 0) + 1 }));
  const names = useMemo(() => new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name])), [graph.data]);
  const topics = useMemo(() => (graph.data ? topicsOf(graph.data) : []), [graph.data]);
  const [topicId, setTopicId] = usePersisted<string | null>("loopers.chat.topic", null);
  const topic = topics.find((t) => t.node.id === topicId); // a topic that no longer exists falls back to all
  const scope = useMemo(
    () => (topic ? { topic_ids: [topic.node.id], concept_ids: topic.concepts.map((c) => c.id) } : { topic_ids: [], concept_ids: [] }),
    [topic],
  );

  useEffect(() => {
    if (!cite) return;
    get<Citation>(`/api/citations/${encodeURIComponent(cite)}`).then(open, () => setNote("That source is no longer available."));
  }, [cite]);

  // Escape closes the source drawer
  useEffect(() => {
    if (!active) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setActive(null); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [active]);

  if (!ids) return <p className="text-sm text-[#cdb3ea]">Loading…</p>;
  return (
    <div className="space-y-3">
      <ErrorNote message={note ?? graph.error} />
      {/* The page scrolls; the chat stays pinned beside the sidebar, so a long file list is never squeezed or hidden. */}
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-h-[32rem] lg:sticky lg:top-[4.75rem] lg:h-[calc(100vh-6.6rem)]">
        <ChatPanel
          key={topic?.node.id ?? "all"}
          chatKey={topic?.node.id ?? "all"}
          topicName={topic?.node.name ?? "All topics"}
          studentId={ids.studentId}
          sessionId={ids.sessionId}
          onCite={open}
          name={name}
          scope={scope}
          toolbar={<div className="flex items-center gap-2"><TopicPicker topics={topics} value={topic?.node.id ?? null} onChange={setTopicId} /><CourseMap graph={graph.data} names={names} onReload={graph.reload} /></div>}
        />
        </div>

        <aside aria-label="Course" className="flex min-w-0 flex-col gap-4">
          <section aria-label="Add course material" className="space-y-3 rounded-2xl border border-indigo-200 bg-white/70 p-4 shadow-sm">
            <div>
              <h2 className="font-semibold">Add course material</h2>
              <p className="text-xs text-slate-500">PDFs, slides, lecture videos or a YouTube link</p>
            </div>
            <UploadPanel onJob={(j) => { setJob(j); setSourcesTick((n) => n + 1); }} />
            {job && <JobCard key={job.id} jobId={job.id} title={job.title} onFinished={() => { setSourcesTick((n) => n + 1); graph.reload(); }} />}
          </section>
          <section aria-label="Uploaded files" className="space-y-3 rounded-2xl border border-indigo-200 bg-white/70 p-4 shadow-sm">
            <h2 className="font-semibold">Uploaded files</h2>
            <SourcesTable refresh={sourcesTick} onRemoved={graph.reload} />
          </section>
        </aside>
      </div>

      {active && (
        <aside aria-label="Source viewer" className="fixed inset-y-0 right-0 z-30 flex w-full max-w-xl flex-col gap-3 overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 shadow-2xl">
          <button type="button" onClick={() => setActive(null)} className="self-end rounded-lg border border-slate-200 bg-white px-3 py-1 text-sm hover:border-indigo-300">Close</button>
          <SourcePanel active={active} />
        </aside>
      )}
    </div>
  );
}

export default function HomePage() {
  return <Suspense fallback={null}><Workspace /></Suspense>;
}
