import type { Citation } from "./types";

export type Part = { kind: "text"; text: string } | { kind: "cite"; citation: Citation };

const MARKER = /\[(E\d+)\]/g;

/** Splits an answer on [E#] markers. A marker with no matching citation stays inside the surrounding text. */
export function splitAnswer(answer: string, citations: Citation[]): Part[] {
  const byId = new Map(citations.map((c) => [c.id, c]));
  const parts: Part[] = [];
  let last = 0;
  for (const m of answer.matchAll(MARKER)) {
    const citation = byId.get(m[1]);
    if (!citation) continue;
    const at = m.index ?? 0;
    if (at > last) parts.push({ kind: "text", text: answer.slice(last, at) });
    parts.push({ kind: "cite", citation });
    last = at + m[0].length;
  }
  if (last < answer.length) parts.push({ kind: "text", text: answer.slice(last) });
  return parts;
}
