import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PLAYBACK_GATE_REFUSED, type UploadJob } from '@albedian/contracts';
import { followJob, readVariantShown, startPlaybackCopy, type StoredRestoration } from '../services/apiClient.js';

/**
 * playback-firstneed (REQ-JOB-10; the lead's ruling of 22:19 on 24 Nov: a restored clip with no playback copy is never
 * drawn as restored, and its copy is made when the version is first listed or opened). A listed restored clip whose
 * record names no copy that its route serves and carries no note that none met its gate asks
 * `POST /api/media/{sha256}/variants/{variantId}/playback` once, and its row says what follows
 * (UIUX-versionwait-READY.md, its additions of 22:20, 02:22 and 04:47; the lead's answer of 08:10 on 25 Nov):
 * - `making`: its job queued or running, the pending row, timed from when this page first listed it;
 * - its copy served: an ordinary version, drawn from the copy;
 * - a gate's refusal (a new job's or a stored one under the copy's revision): the no-copy row (`noCopy`);
 * - any other end (a time limit, an internal error, a result not stored, the clip changed, the server stopping, a start
 *   that reached no job): `stopped`, the failed x, and a press asks again;
 * - a clip the route cannot copy (422), or a copy its route names but does not serve: `not_copyable`, its row with no
 *   check, not pressable, and no second line; nothing is said that is not known.
 */
export type CopyWait = 'making' | 'stopped' | 'not_copyable';

/** A listed version with what its copy at first need shows, when it has one to wait for. */
export interface ListedVersion extends StoredRestoration {
  copy?: CopyWait;
  /** For `making`: when this page first listed the version or asked again (ms since the epoch, this page's clock). */
  copySince?: number;
}

type CopyState =
  | { kind: 'making'; since: number }
  | { kind: 'stopped' }
  | { kind: 'not_copyable' }
  | { kind: 'refused' }
  | { kind: 'ready'; playbackUrl: string };

/** Whether a listed version is a restored clip with no copy served and no note: the one that asks for its copy. */
export function needsFirstCopy(version: StoredRestoration): boolean {
  return version.variant?.mimeType.startsWith('video/') === true && !version.playbackUrl && version.noCopy !== true;
}

/** A version that is not drawn here: listed with its row even when it is the only one (D36's list stands for it). */
export function standsAlone(version: ListedVersion): boolean {
  return version.noCopy === true || version.copy !== undefined;
}

/** A version the compare can draw: a still, or a clip whose copy is served. */
export function drawableVersion(version: ListedVersion): boolean {
  return version.noCopy !== true && version.copy === undefined;
}

async function copyServed(sha256: string, variantId: string): Promise<CopyState> {
  const shown = await readVariantShown(sha256, variantId);
  return shown.playbackUrl !== null ? { kind: 'ready', playbackUrl: shown.playbackUrl } : { kind: 'not_copyable' };
}

async function jobEnded(sha256: string, variantId: string, job: UploadJob): Promise<CopyState> {
  if (job.state === 'succeeded') return copyServed(sha256, variantId);
  if (job.state === 'failed' && job.error === PLAYBACK_GATE_REFUSED) return { kind: 'refused' };
  return { kind: 'stopped' };
}

/**
 * The listed versions with their copies at first need asked for and followed, and `retry` for a row whose copy
 * stopped. Every job is followed until it ends or the upload changes; a version is asked for once per page, unless a
 * press asks again.
 */
export function useFirstNeedCopies(
  sha256: string | null,
  listed: readonly StoredRestoration[]
): { versions: ListedVersion[]; retry: (variantId: string) => void } {
  const [states, setStates] = useState<{ sha256: string | null; byId: Readonly<Record<string, CopyState>> }>({ sha256, byId: {} });
  const byId = states.sha256 === sha256 ? states.byId : {};
  // When this page first listed each version that needs a copy: the pending row's time counts from there
  const firstSeen = useRef<{ sha256: string | null; at: Map<string, number> }>({ sha256, at: new Map() });
  if (firstSeen.current.sha256 !== sha256) firstSeen.current = { sha256, at: new Map() };
  const following = useRef(new Map<string, AbortController>());

  const settle = useCallback((sha: string, id: string, state: CopyState) => {
    setStates((now) => (now.sha256 === sha ? { sha256: sha, byId: { ...now.byId, [id]: state } } : { sha256: sha, byId: { [id]: state } }));
  }, []);

  const ask = useCallback(
    (sha: string, id: string, since: number) => {
      following.current.get(id)?.abort();
      const stop = new AbortController();
      following.current.set(id, stop);
      settle(sha, id, { kind: 'making', since });
      void (async () => {
        try {
          const answer = await startPlaybackCopy(sha, id);
          if (stop.signal.aborted) return;
          let state: CopyState;
          if (answer.kind === 'exists') state = await copyServed(sha, id);
          else if (answer.kind === 'not_copyable') state = { kind: 'not_copyable' };
          else {
            const job = answer.job;
            const ended = job.state === 'queued' || job.state === 'running' ? await followJob(job, () => {}, { signal: stop.signal }) : job;
            if (stop.signal.aborted) return;
            state = await jobEnded(sha, id, ended);
          }
          if (!stop.signal.aborted) settle(sha, id, state);
        } catch {
          if (!stop.signal.aborted) settle(sha, id, { kind: 'stopped' });
        }
      })();
    },
    [settle]
  );

  // Every job followed for one upload stops being followed when the upload changes or the screen goes
  useEffect(() => {
    const live = following.current;
    return () => {
      for (const stop of live.values()) stop.abort();
      live.clear();
    };
  }, [sha256]);

  const needing = listed.filter(needsFirstCopy).map((v) => v.id);
  for (const id of needing) if (!firstSeen.current.at.has(id)) firstSeen.current.at.set(id, Date.now());
  const needingKey = needing.join(' ');
  useEffect(() => {
    if (sha256 === null) return;
    for (const id of needingKey === '' ? [] : needingKey.split(' ')) {
      if (byId[id] === undefined && !following.current.has(id)) ask(sha256, id, firstSeen.current.at.get(id) ?? Date.now());
    }
  }, [sha256, needingKey, byId, ask]);

  const retry = useCallback(
    (id: string) => {
      if (sha256 === null || byId[id]?.kind !== 'stopped') return;
      const since = Date.now();
      firstSeen.current.at.set(id, since);
      ask(sha256, id, since);
    },
    [sha256, byId, ask]
  );

  const versions = useMemo(
    () =>
      listed.map((version): ListedVersion => {
        if (!needsFirstCopy(version)) return version;
        const state = byId[version.id];
        if (state === undefined) return { ...version, copy: 'making', copySince: firstSeen.current.at.get(version.id) };
        switch (state.kind) {
          case 'ready':
            return { ...version, playbackUrl: state.playbackUrl };
          case 'refused':
            return { ...version, noCopy: true };
          case 'making':
            return { ...version, copy: 'making', copySince: state.since };
          default:
            return { ...version, copy: state.kind };
        }
      }),
    [listed, byId]
  );
  return { versions, retry };
}
