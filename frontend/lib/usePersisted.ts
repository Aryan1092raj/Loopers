"use client";
import { useEffect, useState } from "react";

/** useState that survives navigation and reloads within the tab (sessionStorage).
 *  Starts at `initial` so server and client HTML match, restores after mount, and
 *  saves only after the restore so a fresh mount never overwrites what was stored. */
export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw !== null) setValue(JSON.parse(raw) as T);
    } catch { /* storage blocked or bad JSON: keep the initial value */ }
    setRestored(true);
  }, [key]);

  useEffect(() => {
    if (!restored) return;
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked: memory lasts this page only */ }
  }, [key, value, restored]);

  return [value, setValue] as const;
}
