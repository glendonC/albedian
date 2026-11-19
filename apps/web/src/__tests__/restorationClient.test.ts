import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { followJob, readVariantOrientation, startRestoration, variantExportUrl, variantFileUrl } from '../services/apiClient';

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
