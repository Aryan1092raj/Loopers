"use client";
import Link from "next/link";
import { useMemo } from "react";
import ConceptDrawer from "@/components/ConceptDrawer";
import ConceptGraph from "@/components/ConceptGraph";
import { Empty, ErrorNote } from "@/components/Note";
import { useIds } from "@/lib/session";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";
import type { Graph } from "@/lib/types";

export default function GraphPage() {
  const ids = useIds();
  const { data, error, loading } = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const [selected, setSelected] = usePersisted<string | null>("loopers.graph.selected", null);
  const names = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n.name])), [data]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Course map</h1>
      <p className="text-sm text-neutral-600">Arrows run from a prerequisite to the concept that needs it. Colour shows your mastery, from red (0%) to green (100%).</p>
      <ErrorNote message={error} />
      {(loading || !ids) && !data && <p className="text-sm text-neutral-500">Loading…</p>}
      {data && data.nodes.length === 0 && (
        <Empty>No concepts yet. <Link href="/upload" className="underline">Upload material</Link> and let the concepts stage finish.</Empty>
      )}
      {data && data.nodes.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
          <ConceptGraph graph={data} onSelect={setSelected} />
          {selected && <ConceptDrawer key={selected} id={selected} names={names} onClose={() => setSelected(null)} />}
        </div>
      )}
    </div>
  );
}
