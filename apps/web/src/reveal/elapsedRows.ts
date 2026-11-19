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

/** The rows with the time passed on the row being worked when it has no count; a step is timed from when it is first seen. */
export function useElapsedRows<R extends { stage: string; state: string; value: string | null; fraction: number | null }>(rows: readonly R[]): R[] {
  const timed = rows.find((row) => row.state === 'now' && row.value === null && row.fraction === null)?.stage ?? null;
  const since = useRef<{ stage: string; at: number } | null>(null);
  if (timed === null) since.current = null;
  else if (since.current?.stage !== timed) since.current = { stage: timed, at: Date.now() };
  const [, setTick] = useState(0);
  useEffect(() => {
    if (timed === null) return undefined;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [timed]);
  const start = since.current;
  // Only the timed row, the one being worked with no count, takes the time
  return rows.map((row) => (start !== null && row.stage === start.stage ? { ...row, value: elapsedWords((Date.now() - start.at) / 1000) } : row));
}
