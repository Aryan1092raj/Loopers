"use client";
import { useEffect } from "react";
import { useGet } from "@/lib/useGet";
import type { SourceRow } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";

export default function SourcesTable({ refresh }: { refresh: number }) {
  const { data, error, loading, reload } = useGet<SourceRow[]>("/api/sources");
  useEffect(() => { if (refresh > 0) reload(); }, [refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <ErrorNote message={error} />;
  if (loading && !data) return <p className="text-sm text-neutral-500">Loading sources…</p>;
  if (!data?.length) return <Empty>No sources yet. Drop a file above to build the knowledge base.</Empty>;
  return (
    <div className="overflow-x-auto rounded border bg-white">
      <table className="w-full text-left text-sm">
        <thead className="border-b bg-neutral-100">
          <tr><th className="p-2">Title</th><th className="p-2">Kind</th><th className="p-2">Lecture</th><th className="p-2">Status</th><th className="p-2">Units</th></tr>
        </thead>
        <tbody>
          {data.map((s) => (
            <tr key={s.source_id} className="border-b last:border-0">
              <td className="p-2">{s.title}</td>
              <td className="p-2">{s.kind}</td>
              <td className="p-2">{s.lecture_no ?? "–"}</td>
              <td className="p-2">{s.status}</td>
              <td className="p-2">{s.unit_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
