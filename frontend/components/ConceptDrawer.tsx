"use client";
import Link from "next/link";
import { chipLabel } from "@/lib/citation";
import { useGet } from "@/lib/useGet";
import type { ConceptDetail } from "@/lib/types";
import { ErrorNote } from "./Note";

export default function ConceptDrawer({ id, names, onClose }: { id: string; names: Map<string, string>; onClose: () => void }) {
  const { data, error, loading } = useGet<ConceptDetail>(`/api/concepts/${encodeURIComponent(id)}`);
  const label = (cid: string) => names.get(cid) ?? cid;
  return (
    <aside aria-label="Concept details" className="space-y-3 rounded border bg-white p-4 text-sm">
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-base font-semibold">{data?.concept.name ?? label(id)}</h2>
        <button type="button" onClick={onClose} className="rounded border px-2 py-0.5">Close</button>
      </div>
      <ErrorNote message={error} />
      {loading && !data && <p className="text-neutral-500">Loading…</p>}
      {data && (
        <>
          {data.concept.description && <p>{data.concept.description}</p>}
          {data.concept.aliases.length > 0 && <p className="text-neutral-600">Also called: {data.concept.aliases.join(", ")}</p>}
          <p><strong>Prerequisites:</strong> {data.prerequisites.length ? data.prerequisites.map(label).join(", ") : "none"}</p>
          <p><strong>Leads to:</strong> {data.dependents.length ? data.dependents.map(label).join(", ") : "none"}</p>
          <div>
            <strong>Sources</strong>
            {data.units.length === 0 && <p className="text-neutral-500">No units are tagged with this concept yet.</p>}
            <ul className="mt-1 space-y-2">
              {data.units.map((u) => (
                <li key={u.unit_id} className="rounded border p-2">
                  <Link href={`/?cite=${encodeURIComponent(u.unit_id)}`} className="font-medium underline">{chipLabel(u)}</Link>
                  <p className="mt-1 text-neutral-600">{u.excerpt.slice(0, 160)}</p>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </aside>
  );
}
