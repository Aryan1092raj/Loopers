"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import ChatPanel from "@/components/ChatPanel";
import { ErrorNote } from "@/components/Note";
import SourcePanel from "@/components/SourcePanel";
import { get } from "@/lib/api";
import { useIds } from "@/lib/session";
import type { Citation } from "@/lib/types";
import { usePersisted } from "@/lib/usePersisted";

function Study() {
  const ids = useIds();
  const cite = useSearchParams().get("cite");
  const [active, setActive] = usePersisted<{ c: Citation; n: number } | null>("loopers.study.active", null);
  const [note, setNote] = useState<string | null>(null);
  const open = (c: Citation) => setActive((a) => ({ c, n: (a?.n ?? 0) + 1 }));

  useEffect(() => {
    if (!cite) return;
    get<Citation>(`/api/citations/${encodeURIComponent(cite)}`).then(
      (c) => setActive((a) => ({ c, n: (a?.n ?? 0) + 1 })),
      () => setNote("That source is no longer available."),
    );
  }, [cite, setActive]);

  if (!ids) return <p className="text-sm text-neutral-500">Loading…</p>;
  return (
    <div className="space-y-2">
      <ErrorNote message={note} />
      <div className="grid gap-4 lg:grid-cols-2">
        <ChatPanel studentId={ids.studentId} sessionId={ids.sessionId} onCite={open} />
        <SourcePanel active={active} />
      </div>
    </div>
  );
}

export default function StudyPage() {
  return <Suspense fallback={null}><Study /></Suspense>;
}
