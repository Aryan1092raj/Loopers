"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useStudent } from "@/lib/useStudent";

const ITEMS = [
  ["/profile", "Profile"],
  ["/dashboard", "Dashboard"],
  ["/onboarding", "Onboarding"],
] as const;

export default function UserMenu() {
  const { name } = useStudent();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);

  const label = name ?? "Set your name";
  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-3 text-sm shadow-sm transition hover:border-indigo-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
      >
        <span aria-hidden className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-xs font-semibold text-white">
          {name?.trim()[0]?.toUpperCase() ?? "?"}
        </span>
        <span className="max-w-32 truncate">{label}</span>
        <span aria-hidden className={`text-xs text-slate-400 transition ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <ul role="menu" className="absolute right-0 z-40 mt-2 w-48 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-lg">
          {ITEMS.map(([href, text]) => (
            <li key={href} role="none">
              <Link
                role="menuitem"
                href={href}
                aria-current={path === href ? "page" : undefined}
                className={`block px-4 py-2 hover:bg-indigo-50 focus-visible:bg-indigo-50 focus-visible:outline-none ${path === href ? "font-semibold text-indigo-700" : "text-slate-700"}`}
              >
                {text}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
