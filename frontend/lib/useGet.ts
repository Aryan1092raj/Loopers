"use client";
import { useEffect, useState } from "react";
import { get } from "./api";

/** GET a path on mount and whenever it changes. Pass null to skip. `reload()` refetches. */
export function useGet<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let stale = false;
    setLoading(true);
    get<T>(path).then(
      (d) => { if (!stale) { setData(d); setError(null); setLoading(false); } },
      (e) => { if (!stale) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } },
    );
    return () => { stale = true; };
  }, [path, tick]);

  return { data, error, loading, reload: () => setTick((n) => n + 1) };
}
