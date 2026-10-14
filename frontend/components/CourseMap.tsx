"use client";
import { useSearchParams } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
      {open && graph && createPortal(<Overlay graph={graph} names={names} onReload={onReload} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

function Overlay({ graph, names, onReload, onClose }: { graph: Graph; names: Map<string, string>; onReload: () => void; onClose: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const picked = useRef(selected);
  picked.current = selected;
  usePopIn(root);
  // leave the site navbar visible: the map starts right below it
  const [top, setTop] = useState(0);
  useLayoutEffect(() => {
    const nav = document.querySelector("body > header");
    setTop(nav ? Math.round(nav.getBoundingClientRect().bottom) : 0);
  }, []);

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
    <div ref={root} role="dialog" aria-modal="true" aria-label="Course map" style={{ top }} className="fixed inset-x-0 bottom-0 z-50 flex flex-col bg-slate-50">
      <div className="relative flex min-h-0 flex-1 gap-4 p-4">
        {hasNodes
          ? <ConceptGraph graph={graph} onSelect={setSelected} onReload={onReload} onClose={onClose} className="h-full min-w-0 flex-1" />
          : <Empty>No concepts yet. Add material and let the concepts stage finish.</Empty>}
        {selected && (
          // phones: the concept drawer covers the map instead of squeezing it to nothing next to a 24 rem column
          <div className="absolute inset-0 z-10 overflow-y-auto bg-slate-50 p-4 md:static md:inset-auto md:w-96 md:shrink-0 md:bg-transparent md:p-0">
            <ConceptDrawer key={selected} id={selected} names={names} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
