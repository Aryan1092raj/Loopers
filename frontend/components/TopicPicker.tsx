"use client";
import { useEffect, useId, useRef, useState } from "react";
import { masteryColor } from "@/lib/mastery";
import type { Topic } from "@/lib/topics";

interface Option { id: string | null; name: string; mastery: number | null; sub: string }

/** Dropdown to scope the chat to one topic (or all). Listbox semantics: arrows move, Enter picks, Escape closes. */
export default function TopicPicker({ topics, value, onChange }: { topics: Topic[]; value: string | null; onChange: (id: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const uid = useId();

  const options: Option[] = [
    { id: null, name: "All topics", mastery: null, sub: `${topics.length} ${topics.length === 1 ? "topic" : "topics"}` },
    ...topics.map((t) => ({ id: t.node.id, name: t.node.name, mastery: t.mastery, sub: `${t.concepts.length} ${t.concepts.length === 1 ? "concept" : "concepts"}` })),
  ];
  const current = Math.max(0, options.findIndex((o) => o.id === value));

  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  function show() { setActive(current); setOpen(true); }
  function pick(i: number) { onChange(options[i].id); setOpen(false); }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) return show();
      setActive((a) => (a + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length);
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    }
  }

  const label = options[current].name;
  return (
    <div ref={root} className="relative">
      <button
        type="button"
        role="combobox"
        disabled={topics.length === 0}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${uid}-list`}
        aria-activedescendant={open ? `${uid}-${active}` : undefined}
        aria-label={`Topic: ${label}`}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKeyDown}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1.5 pl-3 pr-2.5 text-sm shadow-sm transition hover:border-indigo-300 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
      >
        <span className="text-slate-500">Topic</span>
        <span className="max-w-44 truncate font-medium">{topics.length ? label : "none yet"}</span>
        <span aria-hidden className={`text-xs text-slate-400 transition ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <ul
          id={`${uid}-list`}
          role="listbox"
          aria-label="Topics"
          className="absolute right-0 z-40 mt-2 max-h-80 w-72 overflow-y-auto rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-lg"
        >
          {options.map((o, i) => (
            <li
              key={o.id ?? "all"}
              id={`${uid}-${i}`}
              role="option"
              aria-selected={i === current}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(i)}
              ref={(el) => { if (el && i === active) el.scrollIntoView({ block: "nearest" }); }}
              className={`flex cursor-pointer items-center gap-3 px-4 py-2 ${i === active ? "bg-indigo-50" : ""} ${i === current ? "font-semibold text-indigo-700" : "text-slate-700"}`}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">{o.name}</span>
                <span className="block text-xs font-normal text-slate-500">{o.sub}</span>
              </span>
              {o.mastery !== null && (
                <span className="flex items-center gap-1.5 text-xs font-normal text-slate-500" role="img" aria-label={`Mastery ${Math.round(o.mastery * 100)}%`}>
                  <span className="h-1.5 w-12 overflow-hidden rounded-full bg-slate-200">
                    <span className="block h-full rounded-full" style={{ width: `${Math.round(o.mastery * 100)}%`, background: masteryColor(o.mastery) }} />
                  </span>
                  {Math.round(o.mastery * 100)}%
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
