"use client";
import { useEffect, useState } from "react";
import { get } from "./api";
import type { Job } from "./types";

/** Polls a job every 1.5 s until it is done or failed, or the component unmounts.
 *  A failed poll (for example the API reloading) is shown and retried after 3 s. */
export function useJob(jobId: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      let delay = 1500;
      try {
        const j = await get<Job>(`/api/jobs/${jobId}`);
        if (stop) return;
        setJob(j);
        setError(null);
        if (j.status === "done" || j.status === "failed") return;
      } catch (e) {
        if (stop) return;
        setError(e instanceof Error ? e.message : String(e));
        delay = 3000;
      }
      timer = setTimeout(tick, delay);
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [jobId]);

  return { job, error };
}
