import { useEffect, useRef, useState } from 'react';

/**
 * C19 item 1 (UIUX-walkasks-READY.md, decided by the lead 12:59; AGENTS.md rule 3): a working row whose step reports
 * no total shows, in its count's place, the time passed since the app saw that step begin, updated once a second: no
 * estimate of what is left and no bar. When the job reports a count, the count replaces the time at once.
 */

/** "12 s" under a minute, then "1 min 5 s". */
export function elapsedWords(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

/**
 * The rows with the time passed on the row being worked when it has no count; a step is timed from when it is first
 * seen in this job (`job`, its id), so a step that leaves the timed state for a record and comes back keeps counting
 * from its start, not from 0 (FIELD walk-0620: Matching read 1 s, then 0 s, between the pose try and the search).
 */
export function useElapsedRows<R extends { stage: string; state: string; value: string | null; fraction: number | null }>(rows: readonly R[], job: string | null = null): R[] {
  const timed = rows.find((row) => row.state === 'now' && row.value === null && row.fraction === null)?.stage ?? null;
  const seen = useRef<{ job: string | null; starts: Map<string, number> }>({ job, starts: new Map() });
  if (seen.current.job !== job) seen.current = { job, starts: new Map() };
  if (timed !== null && !seen.current.starts.has(timed)) seen.current.starts.set(timed, Date.now());
  const since = timed === null ? null : { stage: timed, at: seen.current.starts.get(timed)! };
  const [, setTick] = useState(0);
  useEffect(() => {
    if (timed === null) return undefined;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [timed]);
  const start = since;
  // Only the timed row, the one being worked with no count, takes the time
  return rows.map((row) => (start !== null && row.stage === start.stage ? { ...row, value: elapsedWords((Date.now() - start.at) / 1000) } : row));
}
