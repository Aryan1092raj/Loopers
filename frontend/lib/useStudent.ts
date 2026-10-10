"use client";
import { useCallback, useEffect } from "react";
import { put } from "./api";
import { useIds } from "./session";
import type { Student } from "./types";
import { useGet } from "./useGet";

const CHANGED = "loopers:student-changed";

/** The current browser's student: id from localStorage, name from the API. `save` creates or renames, and every
 *  component using this hook refetches (the header menu and the profile page stay in step). */
export function useStudent() {
  const ids = useIds();
  const sid = ids ? encodeURIComponent(ids.studentId) : null;
  const { data, error, reload } = useGet<Student>(sid ? `/api/students/${sid}` : null);

  useEffect(() => {
    window.addEventListener(CHANGED, reload);
    return () => window.removeEventListener(CHANGED, reload);
  }, [reload]);

  const save = useCallback(async (name: string) => {
    if (!sid) return;
    await put(`/api/students/${sid}`, { name });
    window.dispatchEvent(new Event(CHANGED));
  }, [sid]);

  // a 404 (not created yet) arrives as `error` with no data: the student simply has no name yet
  return { ids, student: data, name: data?.name ?? null, loadError: data ? null : error, save };
}
