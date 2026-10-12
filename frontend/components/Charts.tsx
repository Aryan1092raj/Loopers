"use client";
import { animate, createDrawable, stagger, utils } from "animejs";
import { useEffect, useRef } from "react";
import { reducedMotion } from "@/lib/motion";

/** A number that counts up from 0 when it first shows. `format` turns the running value into text. */
const whole = (v: number) => String(Math.round(v));

export function CountUp({ value, format = whole, className }: { value: number; format?: (v: number) => string; className?: string }) {
  const el = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const span = el.current;
    if (!span) return;
    if (reducedMotion()) { span.textContent = format(value); return; }
    const box = { v: 0 };
    const a = animate(box, { v: value, duration: 900, ease: "out(3)", onUpdate: () => { span.textContent = format(box.v); } });
    return () => { a.pause(); };
  }, [value, format]);
  return <span ref={el} className={className}>{format(reducedMotion() ? value : 0)}</span>;
}

/** Ring chart for one share in [0, 1]; the arc draws itself in. The label inside is the percentage. */
export function Ring({ value, label, color = "#4f46e5", size = 112 }: { value: number; label: string; color?: string; size?: number }) {
  const arc = useRef<SVGCircleElement>(null);
  const r = 42;
  const v = Math.min(1, Math.max(0, value));
  useEffect(() => {
    if (!arc.current || reducedMotion()) return;
    const a = animate(createDrawable(arc.current), { draw: ["0 0", `0 ${v}`], duration: 1000, ease: "out(3)" });
    return () => { a.revert(); };
  }, [v]);
  return (
    <figure className="flex flex-col items-center gap-2">
      <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={`${label}: ${Math.round(v * 100)}%`}>
        <circle cx="50" cy="50" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
        <circle
          ref={arc} cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          pathLength={1} strokeDasharray={`${v} 1`} transform="rotate(-90 50 50)"
        />
        <text x="50" y="55" textAnchor="middle" className="fill-slate-900 text-[18px] font-semibold tabular-nums">{Math.round(v * 100)}%</text>
      </svg>
      <figcaption className="max-w-32 text-center text-xs text-slate-600">{label}</figcaption>
    </figure>
  );
}

/** Pie of a few parts (e.g. correct vs wrong), with a legend so colour is not the only cue. */
export function Pie({ parts, size = 140 }: { parts: { label: string; value: number; color: string }[]; size?: number }) {
  const root = useRef<SVGSVGElement>(null);
  const total = parts.reduce((s, p) => s + p.value, 0) || 1;
  let start = 0;
  const arcs = parts.map((p) => { const a = { ...p, start, share: p.value / total }; start += a.share; return a; });
  useEffect(() => {
    const arcsEl = root.current?.querySelectorAll<SVGCircleElement>("circle[data-arc]");
    if (!arcsEl?.length || reducedMotion()) return;
    const a = animate(createDrawable(Array.from(arcsEl)), { draw: (el: unknown) => ["0 0", (el as SVGElement).dataset.arc!], delay: stagger(250), duration: 700, ease: "out(2)" });
    return () => { a.revert(); };
  }, [total]);
  return (
    <div className="flex items-center gap-4">
      <svg ref={root} viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
        <circle cx="50" cy="50" r="30" fill="none" stroke="#f1f5f9" strokeWidth="40" />
        {arcs.filter((a) => a.share > 0).map((a) => (
          <circle
            key={a.label} data-arc={`${a.start} ${a.start + a.share}`} cx="50" cy="50" r="30" fill="none" stroke={a.color} strokeWidth="40"
            pathLength={1} strokeDasharray={`0 ${a.start} ${a.share} 1`} transform="rotate(-90 50 50)"
          />
        ))}
      </svg>
      <ul className="space-y-1 text-sm">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-2">
            <span aria-hidden className="h-3 w-3 rounded-sm" style={{ background: p.color }} />
            {p.label} <span className="tabular-nums text-slate-500">{p.value} ({Math.round((p.value / total) * 100)}%)</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Horizontal bars, each in [0, 1]. An optional `before` shows a faint marker of the earlier value. */
export function Bars({ rows }: { rows: { label: string; value: number; before?: number; hint?: string }[] }) {
  const root = useRef<HTMLUListElement>(null);
  useEffect(() => {
    const fills = root.current?.querySelectorAll<HTMLElement>("[data-fill]");
    if (!fills?.length || reducedMotion()) return;
    const a = animate(Array.from(fills), { scaleX: [0, 1], delay: stagger(60), duration: 700, ease: "out(3)" });
    return () => { a.revert(); };
  }, [rows.length]);
  return (
    <ul ref={root} className="space-y-2">
      {rows.map((r) => {
        const pct = Math.round(utils.clamp(r.value, 0, 1) * 100);
        return (
          <li key={r.label} className="space-y-1">
            <div className="flex justify-between gap-2 text-sm">
              <span className="truncate text-slate-700">{r.label}</span>
              <span className="shrink-0 tabular-nums text-slate-500">
                {r.before !== undefined && <>{Math.round(r.before * 100)}% → </>}{pct}%{r.hint ? ` · ${r.hint}` : ""}
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-slate-100">
              <div data-fill className="h-full origin-left rounded-full" style={{ width: `${pct}%`, background: pct >= 75 ? "#059669" : pct >= 40 ? "#d97706" : "#dc2626" }} />
              {r.before !== undefined && <div aria-hidden className="absolute inset-y-0 w-0.5 bg-slate-500" style={{ left: `${Math.round(r.before * 100)}%` }} />}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Small stat tile used on the dashboard, report and evaluation pages. */
export function Stat({ label, value, format, sub }: { label: string; value: number; format?: (v: number) => string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <CountUp value={value} format={format} className="mt-1 block text-2xl font-semibold tabular-nums text-slate-900" />
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export const asPct = (v: number) => `${Math.round(v * 100)}%`;
