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
  return chipsAfterPunctuation(parts);
}

/** The model writes "claim [E1]." so the chip would sit before the full stop; move the punctuation in front of the chip(s). */
function chipsAfterPunctuation(parts: Part[]): Part[] {
  const out: Part[] = [];
  for (const p of parts) {
    const punct = p.kind === "text" ? /^[.,;:!?]+/.exec(p.text)?.[0] : undefined;
    let i = out.length;
    while (i > 0 && out[i - 1].kind === "cite") i--;
    if (p.kind === "text" && punct && i < out.length) {
      const prev = out[i - 1];
      if (prev?.kind === "text") out[i - 1] = { kind: "text", text: prev.text.replace(/\s+$/, "") + punct };
      else out.splice(i, 0, { kind: "text", text: punct });
      if (p.text.length > punct.length) out.push({ kind: "text", text: p.text.slice(punct.length) });
    } else {
      out.push(p);
    }
  }
  return out;
}
