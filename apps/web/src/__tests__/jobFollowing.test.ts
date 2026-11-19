import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { INTERRUPTED_SENTENCE, type IngestReceipt, type ObservationSolution, type UploadJob } from '@albedian/contracts';
import { cancelJob, JOB_POLL_MS, solveStoredUpload, type UploadEvent } from '../services/apiClient';

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
