"use client";
import { splitAnswer } from "@/lib/answer";
import type { Citation, TutorResponse } from "@/lib/types";
import CitationChip from "./CitationChip";
import RefusalCard from "./RefusalCard";

export default function AnswerView({ r, onCite, onOutside }: { r: TutorResponse; onCite: (c: Citation) => void; onOutside: () => void }) {
  if (r.mode === "refused") {
    return <RefusalCard message={r.refusal?.message ?? "I could not find this in your course material."} offerOutside={r.refusal?.offer_outside ?? true} onOutside={onOutside} />;
  }
  if (r.mode === "outside_syllabus") {
    return (
      <div className="space-y-1">
        <span className="inline-block rounded bg-purple-600 px-2 py-0.5 text-xs font-semibold tracking-wide text-white">OUTSIDE SYLLABUS</span>
        <p>{r.segments.map((s) => s.text).join(". ")}.</p>
        <Listen text={r.answer.replace(/\[E\d+\]/g, "")} />
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <p>
        {splitAnswer(r.answer, r.citations).map((p, i) =>
          p.kind === "text" ? <span key={i}>{p.text}</span> : <CitationChip key={i} citation={p.citation} onCite={onCite} />,
        )}
      </p>
      <Listen text={r.answer.replace(/\[E\d+\]/g, "")} />
      {r.mode === "partially_grounded" && (
        <p className="text-xs text-amber-800">Some claims the course material does not support were left out.</p>
      )}
    </div>
  );
}

function Listen({ text }: { text: string }) {
  if (typeof speechSynthesis === "undefined") return null;
  return (
    <button
      type="button"
      onClick={() => { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); }}
      className="text-xs underline"
    >
      Listen
    </button>
  );
}
