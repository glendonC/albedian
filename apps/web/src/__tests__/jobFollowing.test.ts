import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { INTERRUPTED_SENTENCE, type IngestReceipt, type ObservationSolution, type UploadJob } from '@albedian/contracts';
import { cancelJob, JOB_POLL_MS, readPredictedSky, solveStoredUpload, type UploadEvent } from '../services/apiClient';

// Following a solve job (upload jobs design 3.9, against PLATFORM's runner routes): the solve starts with
// `wait=false`, the job record is read once a second until it ends, and only a succeeded job's solution is read; a
// failed, interrupted or canceled job says why in its own words. Cancel is one POST.

const fixtures = path.resolve(__dirname, '../../../../tests/fixtures');
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const sample = (state: UploadJob['state'], extra: (job: UploadJob) => boolean = () => true) =>
  structuredClone(jobs.find((job) => job.kind === 'solve' && job.state === state && extra(job))!);
const sha256 = sample('running').sourceAssetSha256;
const assetId = `src_${sha256.slice(0, 16)}`;
const receipt: IngestReceipt = {
  sourceAsset: {
    id: assetId,
    title: 'night.png',
    mediaKind: 'image',
    sha256,
    uri: `/api/ingest/${sha256}/original`,
    byteSize: 12,
    mimeType: 'image/png',
    width: 960,
    height: 540,
    durationMs: 0,
    videoCodec: 'png',
    audioStreams: [],
    privacyClass: 'private',
    immutable: true,
  },
  hints: {},
  alreadyStored: true,
};
const solutions = JSON.parse(fs.readFileSync(path.join(fixtures, 'python_contract_solutions.json'), 'utf8')) as Record<string, ObservationSolution>;
const succeededJob = sample('succeeded');
const solutionId = succeededJob.solutionId!;
const base = solutions['plate_calibrated']!;
const solution: ObservationSolution = {
  ...base,
  id: solutionId,
  sourceAssetId: assetId,
  sourceSha256: sha256,
  findings: base.findings.map((f) => ({
    ...f,
    observationSolutionId: solutionId,
    sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId } : undefined,
    evidence: { ...f.evidence, sourceAssetSha256: sha256, wcsSolutionId: solutionId },
  })),
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** A server that starts the job with the first record and answers each later read with the next record. */
function server(records: unknown[], solveStatus = 202) {
  const calls: string[] = [];
  const queue = [...records];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (url === `/api/ingest/${sha256}/solve?wait=false`) return json(queue.shift(), solveStatus);
    if (url.startsWith('/api/ingest/jobs/') && url.endsWith('/cancel')) return json({ ...sample('running'), cancelRequestedAtUTC: sample('running').startedAtUTC }, 202);
    if (url.startsWith('/api/ingest/jobs/')) return json(queue.shift());
    if (url === `/api/ingest/${sha256}/solution`) return json(solution);
    return new Response('Not found', { status: 404 });
  });
  return calls;
}
async function follow(records: unknown[], solveStatus = 202) {
  const calls = server(records, solveStatus);
  const events: UploadEvent[] = [];
  const session = await solveStoredUpload(receipt, { onEvent: (event) => events.push(event), pollMs: 0 });
  return { calls, events, session };
}
const running = sample('running');
const jobUrl = `GET /api/ingest/jobs/${running.id}`;

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('following a solve job until it ends', () => {
  it('starts without waiting, reads the job until it succeeds, reporting every record, then reads the solution', async () => {
    const counted = { ...running, stages: [...running.stages] };
    const { calls, events, session } = await follow([sample('queued'), counted, succeededJob]);
    expect(calls).toEqual([`POST /api/ingest/${sha256}/solve?wait=false`, `GET /api/ingest/jobs/${sample('queued').id}`, jobUrl, `GET /api/ingest/${sha256}/solution`]);
    expect(events.map((e) => (e.kind === 'job' || e.kind === 'solved' ? `${e.kind}:${e.job.state}` : e.kind))).toEqual([
      'job:queued',
      'job:running',
      'job:succeeded',
      'solved:succeeded',
    ]);
    expect(session.observationSolution?.id).toBe(solutionId);
  });

  it('waits a second between reads', async () => {
    expect(JOB_POLL_MS).toBe(1000);
    vi.useFakeTimers();
    const calls = server([running, succeededJob]);
    const done = solveStoredUpload(receipt, {});
    await vi.advanceTimersByTimeAsync(0);
    expect(calls.filter((c) => c.startsWith('GET /api/ingest/jobs/'))).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(999);
    expect(calls.filter((c) => c.startsWith('GET /api/ingest/jobs/'))).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls.filter((c) => c.startsWith('GET /api/ingest/jobs/'))).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(0);
    await done;
  });

  it("ends a failed or interrupted job with the job's own sentence, and reads no solution", async () => {
    for (const ended of [sample('failed'), sample('interrupted')]) {
      vi.restoreAllMocks();
      const { calls, events } = await follow([running, ended]);
      expect(calls.at(-1)).toBe(jobUrl);
      const last = events.at(-1)!;
      expect(last.kind).toBe('failed');
      expect(last.kind === 'failed' && last.sentence).toBe(ended.error);
    }
    expect(sample('interrupted').error).toBe(INTERRUPTED_SENTENCE);
  });

  it('ends a canceled job with the state\'s own name, "Canceled", and reads no solution', async () => {
    const { calls, events } = await follow([running, sample('canceled', (job) => job.cancelRequestedAtUTC !== undefined)]);
    expect(calls.at(-1)).toBe(jobUrl);
    const last = events.at(-1)!;
    expect(last.kind === 'failed' && last.sentence).toBe('Canceled');
  });

  it("follows nothing when the server answers with a job that already ended (today's waiting server)", async () => {
    const { calls } = await follow([succeededJob], 201);
    expect(calls).toEqual([`POST /api/ingest/${sha256}/solve?wait=false`, `GET /api/ingest/${sha256}/solution`]);
  });

  it('stops following, saying the solve stopped, when a job read fails or answers with a job of another kind', async () => {
    const media = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'running')!);
    for (const bad of [media, { nonsense: true }]) {
      vi.restoreAllMocks();
      const { events } = await follow([running, bad]);
      const last = events.at(-1)!;
      expect(last.kind).toBe('failed');
      expect(last.kind === 'failed' && last.job?.state).toBe('running');
    }
  });
});

describe('cancelJob', () => {
  it('asks the server to cancel that job with one POST and hands back its record', async () => {
    const calls = server([]);
    const job = await cancelJob(running.id);
    expect(calls).toEqual([`POST /api/ingest/jobs/${running.id}/cancel`]);
    expect(job.state).toBe('running');
    expect((job as UploadJob).cancelRequestedAtUTC).toBe(running.startedAtUTC);
  });

  it('throws when the server refuses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'no solve job with that id is stored' }), { status: 404 }));
    await expect(cancelJob(running.id)).rejects.toThrow('no solve job with that id is stored');
  });
});

describe('the sky a running solve predicted from an early pose (D25, platform-10a and 10b)', () => {
  const POSED = solutions['pose_moon_level_head']!;
  const early = { ...POSED, sourceSha256: sha256, sourceAssetId: assetId };
  const skyUrl = `GET /api/ingest/jobs/${running.id}/predicted-sky`;
  const named = { ...running, predictedSkyAtUTC: running.startedAtUTC };

  /** As server(), with the job's predicted sky route answering `sky` (a body, or a status alone). */
  function posedServer(records: unknown[], sky: { body?: unknown; status: number }) {
    const calls: string[] = [];
    const queue = [...records];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url === `/api/ingest/${sha256}/solve?wait=false`) return json(queue.shift(), 202);
      if (url.endsWith('/predicted-sky')) return sky.body === undefined ? new Response('{"detail":"no predicted sky is served for that job"}', { status: sky.status }) : json(sky.body, sky.status);
      if (url.startsWith('/api/ingest/jobs/')) return json(queue.shift());
      if (url === `/api/ingest/${sha256}/solution`) return json(solution);
      return new Response('Not found', { status: 404 });
    });
    return calls;
  }
  async function followPosed(records: unknown[], sky: { body?: unknown; status: number }) {
    const calls = posedServer(records, sky);
    const events: UploadEvent[] = [];
    await solveStoredUpload(receipt, { onEvent: (event) => events.push(event), pollMs: 0 });
    return { calls, events };
  }
  const kinds = (events: UploadEvent[]) => events.map((e) => (e.kind === 'job' || e.kind === 'solved' ? `${e.kind}:${e.job.state}` : e.kind));

  it('reads it once the record names it, tells it as early, and reads it only once', async () => {
    const { calls, events } = await followPosed([running, named, named, succeededJob], { body: early, status: 200 });
    expect(calls.filter((c) => c === skyUrl)).toHaveLength(1);
    expect(calls.indexOf(skyUrl)).toBe(calls.indexOf(jobUrl) + 1);
    expect(kinds(events)).toEqual(['job:running', 'job:running', 'early', 'job:running', 'job:succeeded', 'solved:succeeded']);
    const told = events.find((e) => e.kind === 'early');
    expect(told?.kind === 'early' && told.solution.poseEstimate?.method).toBe('moon_camera_profile_level_head');
    expect(told?.kind === 'early' && told.solution.status).toBe('approximate');
  });

  it('reads it from the record the start answers with, when that one names it already', async () => {
    const { calls, events } = await followPosed([named, succeededJob], { body: early, status: 200 });
    expect(calls.slice(0, 2)).toEqual([`POST /api/ingest/${sha256}/solve?wait=false`, skyUrl]);
    expect(kinds(events)).toEqual(['job:running', 'early', 'job:succeeded', 'solved:succeeded']);
  });

  it('never reads it for a job that has ended, though its record names it', async () => {
    const ended = { ...succeededJob, predictedSkyAtUTC: succeededJob.startedAtUTC };
    const { calls } = await followPosed([running, ended], { body: early, status: 200 });
    expect(calls.some((c) => c.endsWith('/predicted-sky'))).toBe(false);
  });

  it('puts the job id into the path encoded, whatever it holds', async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      calls.push(String(input));
      return new Response('{}', { status: 404 });
    });
    expect(await readPredictedSky('job/../x y?z')).toBeNull();
    expect(calls).toEqual(['/api/ingest/jobs/job%2F..%2Fx%20y%3Fz/predicted-sky']);
  });

  it('never reads it for a record that does not name it', async () => {
    const { calls } = await followPosed([running, running, succeededJob], { body: early, status: 200 });
    expect(calls.some((c) => c.endsWith('/predicted-sky'))).toBe(false);
  });

  it('tells nothing, and goes on following, when the route answers 404 or with a sky that is not an approximate one with its pose', async () => {
    const { poseEstimate: _pose, ...unposed } = early;
    for (const sky of [{ status: 404 }, { body: { nonsense: true }, status: 200 }, { body: solution, status: 200 }, { body: unposed, status: 200 }]) {
      vi.restoreAllMocks();
      const { events } = await followPosed([running, named, succeededJob], sky);
      expect(kinds(events)).toEqual(['job:running', 'job:running', 'job:succeeded', 'solved:succeeded']);
    }
  });

  it('tells nothing, and goes on following, when the read itself fails', async () => {
    const calls: string[] = [];
    const queue: unknown[] = [running, named, succeededJob];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push(url);
      if (url.endsWith('/predicted-sky')) throw new TypeError('Failed to fetch');
      if (url === `/api/ingest/${sha256}/solution`) return json(solution);
      return json(queue.shift(), url.endsWith('wait=false') ? 202 : 200);
    });
    const events: UploadEvent[] = [];
    await solveStoredUpload(receipt, { onEvent: (event) => events.push(event), pollMs: 0 });
    expect(kinds(events)).toEqual(['job:running', 'job:running', 'job:succeeded', 'solved:succeeded']);
  });
});
