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
    // A network error (not an HTTP error) usually means the API is restarting: try once more after 3 s.
    const once = () => get<T>(path);
    once().catch((e) => (e instanceof TypeError ? new Promise<T>((ok, no) => setTimeout(() => once().then(ok, no), 3000)) : Promise.reject(e))).then(
      (d) => { if (!stale) { setData(d); setError(null); setLoading(false); } },
      (e) => { if (!stale) { setError(e instanceof Error ? e.message : String(e)); setLoading(false); } },
    );
    return () => { stale = true; };
  }, [path, tick]);

  return { data, error, loading, reload: () => setTick((n) => n + 1) };
}
