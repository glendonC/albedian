import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { NO_PLAYBACK_NOTE, followJob, playbackCopyUrl, startPlaybackCopy, readRestorations, readVariantOrientation, readVariantShown, restoredSource, startRestoration, variantExportUrl, variantFileUrl } from '../services/apiClient';

// The restoration's routes (restore-2b, restoration design sections 9 and 11): a restoration job started, joined or
// returned at POST /api/media/{sha256}/restore, followed at GET /api/ingest/jobs/{id} once a second like a solve,
// and a succeeded job's variant served at GET /api/media/{sha256}/variants/{variantId}/file.

const fixtures = path.resolve(__dirname, '../../../../tests/fixtures');
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const restoration = (state: UploadJob['state']) => structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === state)!);
const succeeded = restoration('succeeded');
const sha256 = succeeded.sourceAssetSha256;
// A running restoration, from the succeeded record with its result and finish taken away and two stages reported
const running: UploadJob = (() => {
  const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = succeeded;
  return { ...rest, state: 'running', stages: succeeded.stages.slice(0, 2) } as UploadJob;
})();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.restoreAllMocks());

describe('startRestoration', () => {
  it('asks for a restoration of the stored file with the contract\'s request, and hands back the job', async () => {
    const seen: Array<{ url: string; init?: RequestInit }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      seen.push({ url: String(input), init });
      return json(running, 202);
    });
    const job = await startRestoration(sha256);
    expect(seen).toHaveLength(1);
    expect(seen[0]!.url).toBe(`/api/media/${sha256}/restore`);
    expect(seen[0]!.init?.method).toBe('POST');
    expect(JSON.parse(String(seen[0]!.init?.body))).toEqual({ kind: 'restoration' });
    expect(new Headers(seen[0]!.init?.headers).get('content-type')).toBe('application/json');
    expect(job.state).toBe('running');
  });

  it("throws the server's reason when it refuses (a clip, for now), and refuses a job of another kind", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ detail: 'Restoring a clip is not built yet.' }, 501));
    await expect(startRestoration(sha256)).rejects.toThrow('Restoring a clip is not built yet.');
    const solve = structuredClone(jobs.find((job) => job.kind === 'solve' && job.state === 'running')!);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json(solve, 200));
    await expect(startRestoration(sha256)).rejects.toThrow();
  });
});

describe('followJob', () => {
  it('reads the job once per wait while it is queued or running, telling each record, and ends with the last', async () => {
    const calls: string[] = [];
    const answers = [running, succeeded];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      calls.push(String(input));
      return json(answers.shift());
    });
    const told: string[] = [];
    const ended = await followJob(running, (job) => told.push(job.state), { pollMs: 0 });
    expect(calls).toEqual([`/api/ingest/jobs/${running.id}`, `/api/ingest/jobs/${running.id}`]);
    expect(told).toEqual(['running', 'succeeded']);
    expect(ended.variantId).toBe(succeeded.variantId);
  });

  it('reads nothing for a job that already ended, and stops when told to stop', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(running));
    expect((await followJob(succeeded, () => {}, { pollMs: 0 })).state).toBe('succeeded');
    expect(fetcher).not.toHaveBeenCalled();
    const stop = new AbortController();
    const following = followJob(running, () => stop.abort(), { pollMs: 0, signal: stop.signal });
    await expect(following).rejects.toThrow('stopped following');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("throws the server's reason when a read is refused", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({ detail: 'The stored job record could not be read.' }, 409));
    await expect(followJob(running, () => {}, { pollMs: 0 })).rejects.toThrow('The stored job record could not be read.');
  });
});

describe('readVariantOrientation', () => {
  it("reads how the stored variant is turned from its record (a PNG carries no orientation tag of its own)", async () => {
    const seen: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      seen.push(String(input));
      return json({ variant: { id: 'var_7e6f5a4b3c2d1e0f', displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } }, graph: {}, attempt: {} });
    });
    expect(await readVariantOrientation(sha256, 'var_7e6f5a4b3c2d1e0f')).toEqual({ quarterTurnsClockwise: 1, mirrored: false });
    expect(seen).toEqual([`/api/media/${sha256}/variants/var_7e6f5a4b3c2d1e0f`]);
  });

  it('reads an upright variant (no orientation in its record) as unturned, and throws a refusal', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ variant: { id: 'var_x' }, graph: {}, attempt: {} }));
    expect(await readVariantOrientation(sha256, 'var_x')).toEqual({ quarterTurnsClockwise: 0, mirrored: false });
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ detail: 'no variant with that id is stored for that file' }, 404));
    await expect(readVariantOrientation(sha256, 'var_x')).rejects.toThrow('no variant with that id is stored for that file');
  });

  it('starts following from a queued job too (the verdict on unit-restore-1, note a)', async () => {
    const queued = { ...running, state: 'queued' } as UploadJob;
    const answers = [succeeded];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(answers.shift()));
    expect((await followJob(queued, () => {}, { pollMs: 0 })).state).toBe('succeeded');
  });
});

describe('variantFileUrl', () => {
  it('names the stored variant\'s bytes under its upload', () => {
    expect(variantFileUrl(sha256, 'var_7e6f5a4b3c2d1e0f')).toBe(`/api/media/${sha256}/variants/var_7e6f5a4b3c2d1e0f/file`);
  });
});

describe('variantExportUrl (D22; the lead, 07:25: EXPORT\'s restore-8)', () => {
  it('names the one stored zip of the variant and its manifest', () => {
    expect(variantExportUrl(sha256, 'var_7e6f5a4b3c2d1e0f')).toBe(`/api/media/${sha256}/variants/var_7e6f5a4b3c2d1e0f/export`);
    expect(variantExportUrl(sha256, 'var/x y')).toBe(`/api/media/${sha256}/variants/var%2Fx%20y/export`);
  });
});

// clip-display-copy (DECIDED-BY-LEAD 21:57 and 22:05 on 24 Nov): a restored clip plays from its playback copy, H.264
// High, which every browser the app meets draws, when its record names one and its own route serves it; the route
// answers not found for a listed copy whose file is not the one its record names (AUDIT's note on playback-route-one).
describe('the playback copy', () => {
  const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
  const clip = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.playbackCopy !== undefined)!;
  const vid = (n: number) => `var_${String(n).padStart(16, '0')}`;
  const clipOf = (id: string, createdAtUTC: string) => ({
    ...clip,
    id,
    createdAtUTC,
    sourceSha256: sha256,
    sourceAssetId: `src_${sha256.slice(0, 16)}`,
    uri: `/api/media/${sha256}/variants/${id}/file`,
    playbackCopy: { ...(clip.playbackCopy as Record<string, unknown>), uri: `/api/media/${sha256}/variants/${id}/playback` },
  });
  const served = (status: number) => new Response(status === 206 ? 'x' : JSON.stringify({ detail: 'that variant has no playback copy' }), {
    status,
    headers: { 'content-type': status === 206 ? 'video/mp4' : 'application/json' },
  });

  it('plays a clip from its copy when the copy is served, asking only its first byte, and only once', async () => {
    const one = clipOf(vid(1), '2026-11-24T10:00:00Z');
    const seen: Array<{ url: string; range: string | null }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      seen.push({ url, range: new Headers(init?.headers).get('range') });
      return url.endsWith('/playback') ? served(206) : json({ variant: one, graph: {}, attempt: {} });
    });
    expect(await readVariantShown(sha256, vid(1))).toEqual({ orientation: (one as { displayOrientation?: unknown }).displayOrientation ?? { quarterTurnsClockwise: 0, mirrored: false }, playbackUrl: one.playbackCopy.uri, noCopy: false });
    expect(seen).toEqual([
      { url: `/api/media/${sha256}/variants/${vid(1)}`, range: null },
      { url: one.playbackCopy.uri, range: 'bytes=0-0' },
    ]);
    // A copy served once is the same file for the page's life
    expect(await playbackCopyUrl(one.playbackCopy)).toBe(one.playbackCopy.uri);
    expect(seen).toHaveLength(2);
  });

  it('plays the lossless file where there is no copy, a copy its route does not serve, or no answer', async () => {
    const none = { ...clipOf(vid(2), '2026-11-24T10:00:00Z') } as Record<string, unknown>;
    delete none.playbackCopy;
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ variant: none, graph: {}, attempt: {} }));
    expect((await readVariantShown(sha256, vid(2))).playbackUrl).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    const refused = clipOf(vid(3), '2026-11-24T10:00:00Z');
    fetcher.mockImplementation(async (input) => (String(input).endsWith('/playback') ? served(404) : json({ variant: refused, graph: {}, attempt: {} })));
    expect((await readVariantShown(sha256, vid(3))).playbackUrl).toBeNull();
    // Not served is asked again (a copy may be made later), and a JSON answer is not a copy
    fetcher.mockImplementation(async () => json({ variants: [] }));
    expect(await playbackCopyUrl(refused.playbackCopy)).toBeNull();
    fetcher.mockImplementation(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(await playbackCopyUrl(clipOf(vid(4), '2026-11-24T10:00:00Z').playbackCopy)).toBeNull();
    // A record that is no copy is never asked for
    fetcher.mockClear();
    expect(await playbackCopyUrl({ uri: '/elsewhere' })).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('lists each restored clip with its copy as served, and plays a version from it', async () => {
    const newer = clipOf(vid(6), '2026-11-24T12:00:00Z');
    const older = clipOf(vid(5), '2026-11-24T11:00:00Z');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/playback')) return served(url.includes(vid(6)) ? 206 : 404);
      return json({ variants: [older, newer], attempts: [] });
    });
    const listed = await readRestorations(sha256);
    expect(listed.map((r) => [r.id, r.playbackUrl])).toEqual([
      [vid(6), newer.playbackCopy.uri],
      [vid(5), null],
    ]);
    expect(restoredSource(sha256, listed[0]!)).toBe(newer.playbackCopy.uri);
    expect(restoredSource(sha256, listed[1]!)).toBe(variantFileUrl(sha256, vid(5)));
    // A still has no copy: its PNG, as before
    expect(restoredSource(sha256, { id: vid(7) })).toBe(variantFileUrl(sha256, vid(7)));
  });

  it("says which versions' records carry the no-copy note, which are never drawn here (UIUX 02:25)", async () => {
    expect(NO_PLAYBACK_NOTE).toBe('No playback copy could be made within its gate, so this restoration has none.');
    const kept = clipOf(vid(9), '2026-11-24T13:00:00Z') as Record<string, unknown>;
    delete kept.playbackCopy;
    const plain = { ...kept, id: vid(8), uri: `/api/media/${sha256}/variants/${vid(8)}/file`, createdAtUTC: '2026-11-24T12:30:00Z', attemptId: 'att_0000000000000008' };
    const noted = { ...kept, attemptId: 'att_0000000000000009' };
    const attempt = (id: string, notes: string[]) => ({ id, measured: { notes } });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).endsWith('/variants')
        ? json({ variants: [plain, noted], attempts: [attempt('att_0000000000000009', [NO_PLAYBACK_NOTE]), attempt('att_0000000000000008', [])] })
        : json({ variant: noted, graph: {}, attempt: attempt('att_0000000000000009', [NO_PLAYBACK_NOTE]) })
    );
    expect((await readRestorations(sha256)).map((r) => [r.id, r.noCopy])).toEqual([
      [vid(9), true],
      [vid(8), false],
    ]);
    expect((await readVariantShown(sha256, vid(9))).noCopy).toBe(true);
  });
});

// playback-firstneed (REQ-JOB-10): a stored restored clip's copy asked for at first need
describe('startPlaybackCopy', () => {
  const copyJob = (state: UploadJob['state']) => structuredClone(jobs.find((job) => job.kind === 'playback_copy' && job.state === state)!);
  it('posts the variant\'s own playback route and hands back its job', async () => {
    const seen: Array<{ url: string; method?: string }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      seen.push({ url: String(input), method: init?.method });
      return json(copyJob('running'), 202);
    });
    const vid = (copyJob('running').request as { variantId: string }).variantId;
    const answer = await startPlaybackCopy(sha256, vid);
    expect(seen).toEqual([{ url: `/api/media/${sha256}/variants/${vid}/playback`, method: 'POST' }]);
    expect(answer.kind === 'job' && answer.job.state).toBe('running');
  });

  it('says when the copy is in place or the clip cannot be copied, and refuses a job of another kind', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ detail: 'that variant has a playback copy' }, 409));
    expect(await startPlaybackCopy(sha256, 'var_0000000000000001')).toEqual({ kind: 'exists' });
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ detail: 'No playback copy is made for a clip stored this way yet.' }, 422));
    expect(await startPlaybackCopy(sha256, 'var_0000000000000001')).toEqual({ kind: 'not_copyable' });
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json(succeeded, 200));
    await expect(startPlaybackCopy(sha256, 'var_0000000000000001')).rejects.toThrow('a restoration job is not a playback copy');
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(json({ detail: 'no variant with that id is stored for that file' }, 404));
    await expect(startPlaybackCopy(sha256, 'var_0000000000000001')).rejects.toThrow('no variant with that id is stored');
  });
});
