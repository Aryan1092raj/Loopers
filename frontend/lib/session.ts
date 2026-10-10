"use client";
import { useEffect, useState } from "react";
import type { QuizItem } from "./types";

const rid = (prefix: string) => `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

function stored(key: string, make: () => string): string {
  try {
    const v = localStorage.getItem(key);
    if (v) return v;
    const n = make();
    localStorage.setItem(key, n);
    return n;
  } catch {
    return make(); // storage blocked: ids last for this page load only
  }
}

/** Student and chat-session ids live in localStorage; there is no auth (TRD §11). null until mounted. */
export function useIds() {
  const [ids, setIds] = useState<{ studentId: string; sessionId: string } | null>(null);
  useEffect(() => {
    setIds({ studentId: stored("loopers.student", () => rid("stu")), sessionId: stored("loopers.session", () => rid("s")) });
  }, []);
  return ids;
}

export function newSession() {
  try { localStorage.removeItem("loopers.session"); } catch { /* nothing to clear */ }
}

export const newQuizId = () => rid("quiz");

/** A quiz is a fixed list of questions ("Quiz 1"); each run through it is an attempt with its own server quiz_id.
 *  The API has no "list quizzes" call, so this lives in the browser. */
export interface QuizAttempt { id: string; at: string }
export interface QuizSet { n: number; items: QuizItem[]; attempts: QuizAttempt[] }
const QUIZZES = "loopers.quizsets";

export function readQuizSets(): QuizSet[] {
  try { return JSON.parse(localStorage.getItem(QUIZZES) ?? "[]") as QuizSet[]; } catch { return []; }
}

/** Call after each submitted answer. `retryOf` is the quiz number being replayed, or null for a new quiz.
 *  A new quiz is created on its first answer and gains the questions answered in its first attempt;
 *  a retry only adds an attempt, so the question list never changes. answer_json is never stored. */
export function recordAnswer(attemptId: string, item: QuizItem, retryOf: number | null): void {
  try {
    const list = readQuizSets();
    const at = new Date().toISOString();
    let set = list.find((s) => s.attempts.some((a) => a.id === attemptId));
    if (!set && retryOf !== null) {
      set = list.find((s) => s.n === retryOf);
      set?.attempts.push({ id: attemptId, at });
    } else if (!set) {
      set = { n: list.reduce((m, s) => Math.max(m, s.n), 0) + 1, items: [], attempts: [{ id: attemptId, at }] };
      list.push(set);
    }
    if (set && set.attempts[0].id === attemptId && !set.items.some((i) => i.item_id === item.item_id)) {
      const { item_id, concept_id, topic_id, type, bloom, stem, options_json } = item;
      set.items.push({ item_id, concept_id, topic_id, type, bloom, stem, options_json });
    }
    localStorage.setItem(QUIZZES, JSON.stringify(list));
  } catch { /* storage blocked: reports still open by id, only the quiz list is lost */ }
}
