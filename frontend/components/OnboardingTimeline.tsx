"use client";
import { stagger } from "animejs";
import { createTimeline } from "animejs/timeline";
import { useLayoutEffect, useRef } from "react";
import { reducedMotion } from "@/lib/motion";
import type { PlanDay } from "@/lib/plan";
import { Empty } from "./Note";

const dayLabel = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
};

/** The dated study plan as a vertical timeline. The days slide in once, the first time a plan appears. */
export default function OnboardingTimeline({ days, left, empty = "Nothing fits yet. Raise the minutes per day or move the exam date later." }: { days: PlanDay[]; left: number; empty?: string }) {
  const root = useRef<HTMLOListElement>(null);
  const has = days.length > 0;

  useLayoutEffect(() => {
    const el = root.current;
    if (!has || !el || reducedMotion()) return;
    const items = Array.from(el.querySelectorAll("[data-tl=day]"));
    const tl = createTimeline({ defaults: { ease: "out(3)", duration: 420 } });
    tl.add(items, { opacity: [0, 1], x: [-16, 0], delay: stagger(110) });
    return () => { tl.revert(); };
  }, [has]);

  if (!has) return <Empty>{empty}</Empty>;
  return (
    <div className="space-y-3">
      <ol ref={root} className="relative space-y-4 border-l-2 border-slate-200 pl-6">
        {days.map((d) => (
          <li key={d.date} data-tl="day" className="relative">
            <span aria-hidden className="absolute -left-[31px] top-1.5 h-3 w-3 rounded-full border-2 border-white bg-indigo-500 ring-2 ring-indigo-200" />
            <h3 className="text-sm font-semibold text-slate-800">{dayLabel(d.date)} <span className="font-normal text-slate-500">· {d.tasks.reduce((s, t) => s + t.minutes, 0)} min</span></h3>
            <ul className="mt-1.5 space-y-1.5">
              {d.tasks.map((t) => (
                <li key={t.concept.id} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm">
                  <span className="font-medium text-slate-800">{t.concept.name}</span>
                  <span className="text-slate-500"> · {t.minutes} min · {t.reason}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      {left > 0 && (
        <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {left} {left === 1 ? "concept doesn't" : "concepts don't"} fit before your exam. Raise the minutes per day or move the exam date later.
        </p>
      )}
    </div>
  );
}
