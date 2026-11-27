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
 * C19 item 1a (UIUX-walkasks-READY.md, DECIDED-BY-LEAD 12:28 on 27 Nov): the row being worked that reports a total,
 * once its count has not moved for 10 s, carries `since`, the seconds passed since its step was first seen worked in
 * this job, drawn beside the count for the rest of that step; every other row's `since` is null.
 */
export function useElapsedRows<R extends { stage: string; state: string; value: string | null; fraction: number | null }>(rows: readonly R[], job: string | null = null): Array<R & { since: string | null }> {
  const timed = rows.find((row) => row.state === 'now' && row.value === null && row.fraction === null)?.stage ?? null;
  // C19 item 1a: the row being worked that reports a total (its count and its bar)
  const counted = rows.find((row) => row.state === 'now' && row.value !== null && row.fraction !== null) ?? null;
  const seen = useRef<{ job: string | null; starts: Map<string, number>; began: Map<string, number>; moved: Map<string, { value: string; at: number }>; still: Set<string> }>({ job, starts: new Map(), began: new Map(), moved: new Map(), still: new Set() });
  if (seen.current.job !== job) seen.current = { job, starts: new Map(), began: new Map(), moved: new Map(), still: new Set() };
  const now = Date.now();
  const nowRow = rows.find((row) => row.state === 'now');
  if (nowRow && !seen.current.began.has(nowRow.stage)) seen.current.began.set(nowRow.stage, now);
  if (timed !== null && !seen.current.starts.has(timed)) seen.current.starts.set(timed, now);
  if (counted !== null) {
    const last = seen.current.moved.get(counted.stage);
    if (!last || last.value !== counted.value) seen.current.moved.set(counted.stage, { value: counted.value!, at: now });
    // A count unmoved for 10 s: from then on, for the rest of that step, the seconds since the step began stand beside it
    else if (now - last.at >= STILL_MS) seen.current.still.add(counted.stage);
  }
  const since = timed === null ? null : { stage: timed, at: seen.current.starts.get(timed)! };
  const [, setTick] = useState(0);
  const ticking = timed ?? counted?.stage ?? null;
  useEffect(() => {
    if (ticking === null) return undefined;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  const start = since;
  const beside = counted !== null && seen.current.still.has(counted.stage) ? { stage: counted.stage, words: elapsedWords((now - seen.current.began.get(counted.stage)!) / 1000) } : null;
  // Only the timed row, the one being worked with no count, takes the time in its count's place; only the counted row
  // whose count stood still takes it beside the count
  return rows.map((row) =>
    start !== null && row.stage === start.stage
      ? { ...row, value: elapsedWords((now - start.at) / 1000), since: null }
      : { ...row, since: beside !== null && row.stage === beside.stage ? beside.words : null }
  );
}

/** C19 item 1a: how long a count stands still before the seconds passed stand beside it (Nielsen's ten seconds). */
const STILL_MS = 10_000;
