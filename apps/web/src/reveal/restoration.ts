import { useCallback, useEffect, useRef, useState } from 'react';
import type { UploadJob } from '@albedian/contracts';
import { CANCELED_WORD, cancelJob, failureReason, followJob, readRefusal, startRestoration, variantFileUrl } from '../services/apiClient.js';
import { megabytes } from './arrival.js';

/**
 * What the restoration shows (UIUX-restoration-READY.md, D10), from its job's own record: nothing asked yet, the job's
 * steps while it works, the compare once it succeeded, or why it stopped. Canceled and interrupted say what D2 says
 * (INTERIM); a refused restoration has no words in any record yet, so it stops with none.
 */
export type Restoration =
  | { phase: 'idle' }
  | { phase: 'asking' }
  | { phase: 'working'; job: UploadJob }
  | { phase: 'done'; job: UploadJob; variantUrl: string }
  | { phase: 'stopped'; job: UploadJob | null; sentence: string | null; subline: string | null; canTryAgain: boolean };

/** Under a refusal: the photo itself was never touched (UIUX-restore-stop-QUESTION.md, decided by the lead 06:11). */
export const UNCHANGED = 'Your photo is unchanged.';

/**
 * One plain sentence per refusal reason (UIUX-restore-stop-QUESTION.md, decided by the lead 06:11). A reason it does
 * not know, or a count of points it lacks, gives none: a reason code is never shown as words.
 */
export function refusalSentence(reason: string, unconfirmedPoints: number | null): string | null {
  switch (reason) {
    case 'unconfirmed_points':
      if (unconfirmedPoints === null) return null;
      return unconfirmedPoints === 1
        ? 'The restoration showed 1 point of light that is not in your photo, so it was not kept.'
        : `The restoration showed ${unconfirmedPoints} points of light that are not in your photo, so it was not kept.`;
    case 'star_flux_changed':
      return 'The restoration changed how bright a star is, so it was not kept.';
    case 'frame_times_differ':
      return "The restored clip's frame times did not match your clip's, so it was not kept.";
    case 'audio_differs':
      return "The restored clip's sound did not match your clip's, so it was not kept.";
    case 'copy_differs':
      return "The copy did not match your photo's pixels exactly, so it was not kept.";
    default:
      return null;
  }
}

const stopped = (job: UploadJob | null, sentence: string | null): Restoration => ({ phase: 'stopped', job, sentence, subline: null, canTryAgain: true });

export function restorationOf(job: UploadJob, sha256: string): Restoration {
  switch (job.state) {
    case 'queued':
    case 'running':
      return { phase: 'working', job };
    case 'succeeded':
      return job.variantId !== undefined
        ? { phase: 'done', job, variantUrl: variantFileUrl(sha256, job.variantId) }
        : stopped(job, null);
    case 'canceled':
      return stopped(job, CANCELED_WORD);
    case 'interrupted':
    case 'failed':
      return stopped(job, job.error ?? null);
    case 'rejected':
      // Deterministic: the same photo would be refused the same way, so no Try again and no Restore again
      return { phase: 'stopped', job, sentence: null, subline: UNCHANGED, canTryAgain: false };
  }
}

/**
 * The restoration of the photo on screen: \`start\` asks for it (or joins the one running, or returns the stored one),
 * follows its job once a second, and \`cancel\` asks the server to stop it. A new photo forgets it all and stops
 * following.
 */
export function useRestoration(sha256: string | null, options: { pollMs?: number } = {}) {
  const [restoration, setRestoration] = useState<Restoration>({ phase: 'idle' });
  const following = useRef<AbortController | null>(null);
  const pollMs = options.pollMs;

  useEffect(() => {
    setRestoration({ phase: 'idle' });
    return () => {
      following.current?.abort();
      following.current = null;
    };
  }, [sha256]);

  const start = useCallback(() => {
    if (!sha256) return;
    following.current?.abort();
    const stop = new AbortController();
    following.current = stop;
    setRestoration({ phase: 'asking' });
    void (async () => {
      try {
        const job = await startRestoration(sha256);
        if (stop.signal.aborted) return;
        setRestoration(restorationOf(job, sha256));
        const ended = await followJob(job, (record) => !stop.signal.aborted && setRestoration(restorationOf(record, sha256)), {
          pollMs,
          signal: stop.signal,
        });
        if (stop.signal.aborted) return;
        setRestoration(restorationOf(ended, sha256));
        if (ended.state === 'rejected' && ended.attemptId !== undefined) {
          const refusal = await readRefusal(sha256, ended.attemptId).catch(() => null);
          const sentence = refusal ? refusalSentence(refusal.rejection, refusal.unconfirmedPointCount) : null;
          if (!stop.signal.aborted && sentence !== null) setRestoration({ phase: 'stopped', job: ended, sentence, subline: UNCHANGED, canTryAgain: false });
        }
      } catch (err) {
        if (!stop.signal.aborted) setRestoration(stopped(null, failureReason(err)));
      }
    })();
  }, [sha256, pollMs]);

  const cancel = useCallback(async () => {
    if (restoration.phase !== 'working') return;
    await cancelJob(restoration.job.id);
  }, [restoration]);

  return { restoration, start, cancel };
}

/** The restoration job's steps in order, with their approved words (UIUX-restoration-READY.md, D10). */
export const RESTORE_STEPS = ['decode', 'measure', 'restore', 'encode', 'write', 'verify'] as const;
export const RESTORE_STEP_WORDS: Readonly<Record<(typeof RESTORE_STEPS)[number], string>> = {
  decode: 'Opened',
  measure: 'Measured',
  restore: 'Restoring',
  encode: 'Encoding',
  write: 'Saving',
  verify: 'Checking',
};

export interface RestoreRow {
  stage: (typeof RESTORE_STEPS)[number];
  word: string;
  state: 'done' | 'now' | 'next' | 'failed';
  /** The row's count as written, or null for none: a still has one frame and nothing to count. */
  value: string | null;
  /** done over total for the bar of the row being worked or stopped at; null draws no bar. */
  fraction: number | null;
}

const STOPPED: ReadonlySet<string> = new Set(['failed', 'canceled', 'interrupted', 'rejected']);
const grouped = (n: number) => n.toLocaleString('en-US');
type Entry = UploadJob['stages'][number];
const full = (entry: Entry) => entry.total !== null && entry.done >= entry.total;

/**
 * One row per step, read from the job's record as the working screen reads a solve's (arrival.ts): done once a later
 * step reported or its own count is full; the row being worked only for a running job; a stopped job marks where it
 * stopped. Frames are counted as "done of total" only when there is more than one; Saving shows megabytes so far.
 */
export function restorationRows(job: UploadJob): RestoreRow[] {
  const entries = new Map<string, Entry>();
  for (const entry of job.stages) entries.set(entry.stage, entry);
  const succeeded = job.state === 'succeeded';
  const stopped = STOPPED.has(job.state);
  const working = job.state === 'running';
  let last = -1;
  RESTORE_STEPS.forEach((name, index) => {
    if (entries.has(name)) last = index;
  });
  const lastEntry = last >= 0 ? entries.get(RESTORE_STEPS[last]!)! : null;
  const lastFull = lastEntry !== null && full(lastEntry);
  const final = RESTORE_STEPS.length - 1;
  // A still has one frame and nothing to count: its rows carry no numbers, Saving included (D10)
  const still = job.stages.some((entry) => entry.unit === 'frames' && entry.total === 1);
  let at = -1;
  if (!succeeded && (stopped || working)) {
    if (!lastFull) at = Math.max(0, last);
    else if (last < final) at = last + 1;
    else at = stopped ? final : -1;
  }
  return RESTORE_STEPS.map((name, index): RestoreRow => {
    const entry = entries.get(name) ?? null;
    const row = (state: RestoreRow['state'], value: string | null = null, fraction: number | null = null): RestoreRow => ({
      stage: name,
      word: RESTORE_STEP_WORDS[name],
      state,
      value,
      fraction,
    });
    const done = succeeded || (at >= 0 ? index < at : index < last || (index === last && lastFull));
    if (still) return row(index === at ? (stopped ? 'failed' : 'now') : done ? 'done' : 'next');
    if (index === at) {
      if (entry !== null && entry.unit === 'bytes') return row(stopped ? 'failed' : 'now', megabytes(entry.done));
      const counted = entry !== null && entry.total !== null && entry.total > 1;
      return counted
        ? row(stopped ? 'failed' : 'now', `${grouped(entry.done)} of ${grouped(entry.total!)}`, entry.done / entry.total!)
        : row(stopped ? 'failed' : 'now');
    }
    if (!done) return row('next');
    if (entry === null) return row('done');
    if (entry.unit === 'bytes') return row('done', megabytes(entry.done));
    if (entry.total === null || entry.total <= 1) return row('done');
    if (entry.done < entry.total) return row('done', `${grouped(entry.done)} of ${grouped(entry.total)}`);
    return row('done', grouped(entry.total));
  });
}
