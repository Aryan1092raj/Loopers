"use client";
import { useEffect, useState } from "react";
import { del } from "@/lib/api";
import { useGet } from "@/lib/useGet";
import type { SourceRow } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";

export default function SourcesTable({ refresh, onRemoved }: { refresh: number; onRemoved?: () => void }) {
  const { data, error, loading, reload } = useGet<SourceRow[]>("/api/sources");
  const [removeError, setRemoveError] = useState<string | null>(null);
  useEffect(() => { if (refresh > 0) reload(); }, [refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  async function remove(s: SourceRow) {
    if (!window.confirm(`Remove "${s.title}"? It leaves the tutor, quizzes and the concept map.`)) return;
    try {
      await del(`/api/sources/${s.source_id}`);
      setRemoveError(null);
      reload();
      onRemoved?.();
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <ErrorNote message={error} />;
  if (loading && !data) return <p className="text-sm text-neutral-500">Loading sources…</p>;
  if (!data?.length) return <Empty>No sources yet. Drop a file above to build the knowledge base.</Empty>;
  return (
    <div className="overflow-x-auto rounded border bg-white">
      {removeError && <ErrorNote message={removeError} />}
      <table className="w-full text-left text-sm">
        <thead className="border-b bg-neutral-100">
          <tr><th className="p-2">Title</th><th className="p-2">Kind</th><th className="p-2">Lecture</th><th className="p-2">Status</th><th className="p-2">Units</th><th className="p-2"><span className="sr-only">Actions</span></th></tr>
        </thead>
        <tbody>
          {data.map((s) => (
            <tr key={s.source_id} className="border-b last:border-0">
              <td className="p-2">{s.title}</td>
              <td className="p-2">{s.kind}</td>
              <td className="p-2">{s.lecture_no ?? "–"}</td>
              <td className="p-2">{s.status}</td>
              <td className="p-2">{s.unit_count}</td>
              <td className="p-2 text-right">
                <button type="button" onClick={() => remove(s)} aria-label={`Remove ${s.title}`} className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-600">
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
