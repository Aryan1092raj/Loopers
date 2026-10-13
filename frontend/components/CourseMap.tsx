"use client";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { usePopIn } from "@/lib/motion";
import type { Graph } from "@/lib/types";
import ConceptDrawer from "./ConceptDrawer";
import ConceptGraph from "./ConceptGraph";
import { Empty } from "./Note";

/** "Course map" button for the chat toolbar; opens the prerequisite graph full screen. */
export default function CourseMap({ graph, names, onReload }: { graph: Graph | null; names: Map<string, string>; onReload: () => void }) {
  const [open, setOpen] = useState(false);
  const cite = useSearchParams().get("cite");
  useEffect(() => setOpen(false), [cite]); // a source link in the drawer opens the source viewer behind the map
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={!graph}
        className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm transition hover:border-indigo-300 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
      >
        Course map
      </button>
      {open && graph && <Overlay graph={graph} names={names} onReload={onReload} onClose={() => setOpen(false)} />}
    </>
  );
}

function Overlay({ graph, names, onReload, onClose }: { graph: Graph; names: Map<string, string>; onReload: () => void; onClose: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const picked = useRef(selected);
  picked.current = selected;
  usePopIn(root);

  // Escape closes the concept first, then the map; the page behind does not scroll while open
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (picked.current) setSelected(null);
      else onClose();
    };
    document.addEventListener("keydown", esc);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", esc); document.body.style.overflow = overflow; };
  }, [onClose]);

  const hasNodes = graph.nodes.length > 0;
  return (
    <div ref={root} role="dialog" aria-modal="true" aria-label="Course map" className="fixed inset-0 z-50 flex flex-col bg-slate-50">
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-5 py-3">
        <div>
          <h2 className="font-semibold">Course map</h2>
          <p className="text-xs text-slate-500">Arrows run from a prerequisite to the concept that needs it. Swipe sideways on empty space, or press Next, to reveal the next concept. Swipe with two fingers to move the map. Click a concept for its sources.</p>
        </div>
        <button type="button" onClick={onClose} autoFocus className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-sm hover:border-indigo-300">Close</button>
      </header>
      <div className="flex min-h-0 flex-1 gap-4 p-4">
        {hasNodes
          ? <ConceptGraph graph={graph} onSelect={setSelected} onReload={onReload} className="h-full min-w-0 flex-1" />
          : <Empty>No concepts yet. Add material and let the concepts stage finish.</Empty>}
        {selected && (
          <div className="w-96 shrink-0 overflow-y-auto">
            <ConceptDrawer key={selected} id={selected} names={names} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
