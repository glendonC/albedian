import { useCallback, useEffect, useRef, useState } from 'react';
import type { UploadJob } from '@albedian/contracts';
import { CANCELED_WORD, cancelJob, failureReason, followJob, startRestoration, variantFileUrl } from '../services/apiClient.js';

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
  | { phase: 'stopped'; job: UploadJob | null; sentence: string | null };

export function restorationOf(job: UploadJob, sha256: string): Restoration {
  switch (job.state) {
    case 'queued':
    case 'running':
      return { phase: 'working', job };
    case 'succeeded':
      return job.variantId !== undefined
        ? { phase: 'done', job, variantUrl: variantFileUrl(sha256, job.variantId) }
        : { phase: 'stopped', job, sentence: null };
    case 'canceled':
      return { phase: 'stopped', job, sentence: CANCELED_WORD };
    case 'interrupted':
    case 'failed':
      return { phase: 'stopped', job, sentence: job.error ?? null };
    case 'rejected':
      return { phase: 'stopped', job, sentence: null };
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
        if (!stop.signal.aborted) setRestoration(restorationOf(ended, sha256));
      } catch (err) {
        if (!stop.signal.aborted) setRestoration({ phase: 'stopped', job: null, sentence: failureReason(err) });
      }
    })();
  }, [sha256, pollMs]);

  const cancel = useCallback(async () => {
    if (restoration.phase !== 'working') return;
    await cancelJob(restoration.job.id);
  }, [restoration]);

  return { restoration, start, cancel };
}
