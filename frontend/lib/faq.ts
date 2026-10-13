import type { TutorResponse } from "./types";

/** One doubt the student had in a topic's chat, with the tutor's answer. */
export interface FaqItem { q: string; a: string }
interface TurnLike { q: string; r?: TutorResponse }

const KEY = "loopers.faq.";
const MAX_ITEMS = 40;
const MAX_ANSWER = 360;

const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ");

/** FAQ entries from a chat: answered questions only (refusals teach nothing), citation markers removed, long answers cut. */
export function faqFrom(turns: TurnLike[]): FaqItem[] {
  const out: FaqItem[] = [];
  for (const t of turns) {
    if (!t.r || t.r.mode === "refused" || !t.r.answer) continue;
    const a = t.r.answer.replace(/\[E\d+\]/g, "").replace(/\s+/g, " ").trim();
    if (a) out.push({ q: t.q.trim(), a: a.length > MAX_ANSWER ? `${a.slice(0, MAX_ANSWER).trimEnd()}…` : a });
  }
  return out;
}

/** Newer wins: asking the same question again replaces the old entry. Oldest entries drop past the cap. */
export function mergeFaq(old: FaqItem[], add: FaqItem[]): FaqItem[] {
  const seen = new Map<string, FaqItem>();
  for (const it of [...old, ...add]) {
    seen.delete(norm(it.q)); // re-insert so it moves to the end
    seen.set(norm(it.q), it);
  }
  return [...seen.values()].slice(-MAX_ITEMS);
}

export function readFaq(chatKey: string): FaqItem[] {
  try { return JSON.parse(localStorage.getItem(KEY + chatKey) ?? "[]") as FaqItem[]; } catch { return []; }
}

/** Fold a chat's answered questions into the topic's FAQ. Called when the chat closes. */
export function saveFaq(chatKey: string, turns: TurnLike[]): void {
  const add = faqFrom(turns);
  if (add.length === 0) return;
  try { localStorage.setItem(KEY + chatKey, JSON.stringify(mergeFaq(readFaq(chatKey), add))); } catch { /* storage blocked: the FAQ is skipped */ }
}
