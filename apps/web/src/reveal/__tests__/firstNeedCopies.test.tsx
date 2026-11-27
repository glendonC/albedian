// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PLAYBACK_GATE_REFUSED, type MediaVariant, type UploadJob } from '@albedian/contracts';
import { NO_PLAYBACK_NOTE, readRestorations, type StoredRestoration } from '../../services/apiClient.js';
import { COPY_STOPPED_LINE, NO_COPY_LINE, NOT_COPYABLE_LINE, RestoreVersions } from '../RestoreParts.js';
import { needsFirstCopy, useFirstNeedCopies, type ListedVersion } from '../firstNeedCopies.js';

// playback-firstneed (REQ-JOB-10; the lead's 22:19 ruling on 24 Nov; UIUX-versionwait-READY.md, its additions of
// 22:20, 02:22 and 04:47; the lead's answer of 08:10 on 25 Nov): a listed restored clip with no playback copy and no
// note asks its route for one at once; while its job runs its row is the pending row (the still now dot in the
// check's place, the seconds passed in the size's), it is never drawn, and its copy is drawn once its route serves it;
// a gate's refusal is the no-copy row, any other failure the failed x with its line and a press that tries again, and
// a clip the route cannot copy keeps its row with no check, not pressable, and no second line.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const sha = 'a'.repeat(64);
const clipNoCopy = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.mimeType === 'video/mp4' && v.playbackCopy === undefined)!;
const clipWithCopy = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.playbackCopy !== undefined)!;
const still = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.mimeType === 'image/png')!;
const VID = 'var_00000000000000d1';
const copyUri = `/api/media/${sha}/variants/${VID}/playback`;
const listed = (template: Record<string, unknown>, id = VID, extra: Partial<StoredRestoration> = {}): StoredRestoration => ({
  id,
  createdAtUTC: '2026-11-25T08:00:00Z',
  byteSize: 14_000_000,
  orientation: { quarterTurnsClockwise: 0, mirrored: false },
  variant: { ...template, id, sourceSha256: sha } as unknown as MediaVariant,
  playbackUrl: null,
  noCopy: false,
  ...extra,
});
const copyJob = (state: UploadJob['state'], error?: string): UploadJob => {
  const job = structuredClone(jobs.find((j) => j.kind === 'playback_copy' && j.state === state)!);
  return { ...job, request: { kind: 'playback_copy', variantId: VID }, ...(job.variantId !== undefined ? { variantId: VID } : {}), ...(error !== undefined ? { error } : {}) } as UploadJob;
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** The route's answers by method and path; each POST to the playback route is counted. */
function serve(answers: { start: () => Response | Promise<Response>; polls?: UploadJob[]; variant?: Record<string, unknown> }) {
  const posts: string[] = [];
  let poll = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === 'POST' && url === copyUri) {
      posts.push(url);
      return answers.start();
    }
    if (url.startsWith('/api/ingest/jobs/')) return json(answers.polls![Math.min(poll++, answers.polls!.length - 1)]);
    if (url === copyUri) return new Response('x', { status: 206, headers: { 'content-type': 'video/mp4' } });
    if (url === `/api/media/${sha}/variants/${VID}`) {
      return json({ variant: answers.variant ?? { ...clipWithCopy, id: VID, playbackCopy: { ...(clipWithCopy.playbackCopy as object), uri: copyUri } } });
    }
    return json({ detail: 'not served in this test' }, 404);
  });
  return posts;
}

let container: HTMLDivElement;
let root: Root;
let seen: ListedVersion[] = [];
let retryNow: (id: string) => void = () => {};
const shown: string[] = [];
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'Date'] });
  vi.setSystemTime(new Date('2026-11-26T12:00:00Z'));
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  seen = [];
  shown.length = 0;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const Harness: React.FC<{ list: StoredRestoration[] }> = ({ list }) => {
  const { versions, retry } = useFirstNeedCopies(sha, list);
  const [pressed, setPressed] = useState<string | null>(null);
  seen = versions;
  retryNow = retry;
  return createElement(RestoreVersions, {
    versions,
    shownId: VID,
    pressedId: pressed,
    onShow: (id: string) => {
      shown.push(id);
      setPressed(id);
    },
    onRetry: retry,
  });
};
async function draw(list: StoredRestoration[]) {
  await act(async () => root.render(createElement(Harness, { list })));
  await act(async () => await vi.advanceTimersByTimeAsync(0));
}
const rowEl = () => container.querySelector<HTMLButtonElement>('.restore-versions__row');
const lines = () => [...container.querySelectorAll('.restore-versions__older')].map((s) => s.textContent);
const tick = async (ms: number) => act(async () => await vi.advanceTimersByTimeAsync(ms));

describe('needsFirstCopy', () => {
  it('names only a listed restored clip with no copy served and no note', () => {
    expect(needsFirstCopy(listed(clipNoCopy))).toBe(true);
    expect(needsFirstCopy(listed(clipNoCopy, VID, { playbackUrl: copyUri }))).toBe(false);
    expect(needsFirstCopy(listed(clipNoCopy, VID, { noCopy: true }))).toBe(false);
    expect(needsFirstCopy(listed(still))).toBe(false);
    expect(needsFirstCopy({ ...listed(clipNoCopy), variant: undefined })).toBe(false);
  });
});

describe('a restored clip listed with no playback copy (playback-firstneed)', () => {
  it('asks its route once, is the pending row timed from the listing, and is drawn from its copy once served', async () => {
    const posts = serve({ start: () => json(copyJob('running'), 202), polls: [copyJob('running'), copyJob('running'), copyJob('succeeded')] });
    await draw([listed(clipNoCopy)]);
    expect(posts).toEqual([copyUri]);
    const row = rowEl()!;
    expect(row.getAttribute('aria-busy')).toBe('true');
    expect(row.querySelector('.restore-versions__mark .restore-versions__dot')).not.toBeNull();
    expect(row.querySelector('.restore-versions__mark svg')).toBeNull();
    expect(row.querySelector('.restore-versions__size')!.textContent).toBe('0 s');
    expect(lines()).toEqual([]);
    expect(seen[0]!.copy).toBe('making');
    await tick(1000);
    expect(rowEl()!.querySelector('.restore-versions__size')!.textContent).toBe('1 s');
    await tick(1000);
    expect(rowEl()!.querySelector('.restore-versions__size')!.textContent).toBe('2 s');
    // A press while the copy is made picks the row and asks nothing new: the same pending row, its time from the listing
    await act(async () => rowEl()!.click());
    expect(shown).toEqual([VID]);
    expect([rowEl()!.getAttribute('aria-pressed'), rowEl()!.getAttribute('aria-busy')]).toEqual(['true', 'true']);
    expect(rowEl()!.querySelector('.restore-versions__size')!.textContent).toBe('2 s');
    expect(posts).toHaveLength(1);
    // Asking again is only for a copy that stopped: one being made is not asked for twice
    await act(async () => retryNow(VID));
    expect(posts).toHaveLength(1);
    await tick(1000);
    // Served: an ordinary version again, drawn from its copy; alone, the list goes (D36)
    expect(seen[0]!.copy).toBeUndefined();
    expect(seen[0]!.playbackUrl).toBe(copyUri);
    expect(rowEl()).toBeNull();
    expect(posts).toHaveLength(1);
  });

  it("takes a gate's refusal, stored or new, as the no-copy row and never asks again", async () => {
    const posts = serve({ start: () => json(copyJob('failed', PLAYBACK_GATE_REFUSED), 200) });
    await draw([listed(clipNoCopy)]);
    expect(seen[0]!.noCopy).toBe(true);
    expect(rowEl()!.getAttribute('aria-disabled')).toBe('true');
    expect(rowEl()!.getAttribute('aria-busy')).toBeNull();
    expect(lines()).toEqual([NO_COPY_LINE]);
    act(() => rowEl()!.click());
    await tick(3000);
    expect(posts).toHaveLength(1);
    expect(shown).toEqual([]);
  });

  it('shows any other failure with the failed x and its line, and a press tries again as the pending row', async () => {
    const posts = serve({ start: () => json(copyJob('running'), 202), polls: [copyJob('failed', 'The playback copy stopped on an internal error.')] });
    await draw([listed(clipNoCopy)]);
    await tick(1000);
    expect(seen[0]!.copy).toBe('stopped');
    const row = rowEl()!;
    expect(row.getAttribute('aria-disabled')).toBeNull();
    expect(row.getAttribute('aria-busy')).toBeNull();
    expect(row.querySelector('.restore-versions__mark svg')).not.toBeNull();
    expect(row.querySelector('.restore-versions__size')!.textContent).toBe('14.0 MB');
    expect(lines()).toEqual([COPY_STOPPED_LINE]);
    await act(async () => rowEl()!.click());
    expect(posts).toHaveLength(2);
    expect(shown).toEqual([VID]);
    expect(rowEl()!.getAttribute('aria-busy')).toBe('true');
    expect(rowEl()!.getAttribute('aria-pressed')).toBe('true');
    expect(rowEl()!.querySelector('.restore-versions__size')!.textContent).toBe('0 s');
  });

  it('takes a start that does not reach a job (the server stopping) as a failure that a press tries again', async () => {
    const posts = serve({ start: () => json({ detail: 'The server stopped before this job finished.' }, 503) });
    await draw([listed(clipNoCopy)]);
    expect(seen[0]!.copy).toBe('stopped');
    expect(lines()).toEqual([COPY_STOPPED_LINE]);
    await act(async () => rowEl()!.click());
    expect(posts).toHaveLength(2);
  });

  it("keeps a clip the route cannot copy as a row with no check, not pressable, and UIUX's line from the route's sentence (lead 00:23)", async () => {
    const posts = serve({ start: () => json({ detail: 'No playback copy is made for a clip stored this way yet.' }, 422) });
    await draw([listed(clipNoCopy)]);
    expect(seen[0]!.copy).toBe('not_copyable');
    const row = rowEl()!;
    expect([row.getAttribute('aria-disabled'), row.getAttribute('aria-pressed'), row.getAttribute('aria-busy')]).toEqual(['true', 'false', null]);
    expect(row.querySelector('.restore-versions__mark')!.childElementCount).toBe(0);
    expect(row.querySelector('.restore-versions__size')!.textContent).toBe('14.0 MB');
    expect(lines()).toEqual([NOT_COPYABLE_LINE]);
    expect(NOT_COPYABLE_LINE).toBe('No copy that plays here is made for a clip stored this way, so it is not shown; Download saves it.');
    act(() => row.click());
    await tick(3000);
    expect(posts).toHaveLength(1);
    expect(shown).toEqual([]);
  });

  it('reads the copy a 409 says is in place and draws it', async () => {
    serve({ start: () => json({ detail: 'that variant has a playback copy' }, 409) });
    await draw([listed(clipNoCopy)]);
    expect(seen[0]!.copy).toBeUndefined();
    expect(seen[0]!.playbackUrl).toBe(copyUri);
  });

  it('says nothing of a copy its record names but its route does not serve: no check, not pressable, no line', async () => {
    // Its own variant: a copy served once is kept as served for the page's life (playbackCopyUrl)
    const id = 'var_00000000000000d5';
    const uri = `/api/media/${sha}/variants/${id}/playback`;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST' && url === uri) return json({ detail: 'that variant has a playback copy' }, 409);
      if (url === `/api/media/${sha}/variants/${id}`) return json({ variant: { ...clipWithCopy, id, playbackCopy: { ...(clipWithCopy.playbackCopy as object), uri } } });
      return json({ detail: 'no playback copy is served for that variant' }, 404);
    });
    await draw([listed(clipNoCopy, id)]);
    expect(seen[0]!.copy).toBe('unserved');
    const row = rowEl()!;
    expect([row.getAttribute('aria-disabled'), row.getAttribute('aria-busy')]).toEqual(['true', null]);
    expect(row.querySelector('.restore-versions__mark')!.childElementCount).toBe(0);
    expect(lines()).toEqual([]);
  });

  it('never asks for a still, a clip whose copy is served, or one whose record notes no copy', async () => {
    const posts = serve({ start: () => json(copyJob('running'), 202) });
    await draw([
      listed(still, 'var_00000000000000d4'),
      listed(clipWithCopy, 'var_00000000000000d3', { playbackUrl: copyUri }),
      listed(clipNoCopy, 'var_00000000000000d2', { noCopy: true }),
    ]);
    await tick(3000);
    expect(posts).toEqual([]);
    expect(seen.map((v) => v.copy)).toEqual([undefined, undefined, undefined]);
  });

  it('lists a version only as readRestorations reads it: a clip with no copy and no note is one to ask for', async () => {
    const bare: Record<string, unknown> = { ...clipNoCopy, id: VID, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${VID}/file` };
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ variants: [bare], attempts: [{ id: bare.attemptId as string, measured: { notes: [] } }] }));
    const [version] = await readRestorations(sha);
    expect(needsFirstCopy(version!)).toBe(true);
    vi.restoreAllMocks();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ variants: [bare], attempts: [{ id: bare.attemptId as string, measured: { notes: [NO_PLAYBACK_NOTE] } }] }));
    const [noted] = await readRestorations(sha);
    expect(needsFirstCopy(noted!)).toBe(false);
  });
});
