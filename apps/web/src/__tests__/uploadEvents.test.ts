import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution, SolveJob } from '@albedian/contracts';
import { solveStoredUpload, uploadAndSolveMedia, UploadRefusedError, type UploadEvent } from '../services/apiClient';
import { ingestAndSolveUploadedFile } from '../App';

// What the working screen is told about an upload (unit 1c): the receipt, each job record the server answers with, and
// how it ended. A failure carries the one sentence a person reads: the job's own error, word for word, or that the
// solve stopped when no job record says why.

const sha256 = 'c'.repeat(64);
const assetId = `src_${sha256.slice(0, 16)}`;
const solutionId = `sol_${sha256.slice(0, 16)}`;
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
  alreadyStored: false,
};
const STAGES_DONE = [
  { stage: 'store', unit: 'bytes', done: 12, total: 12 },
  { stage: 'decode', unit: 'frames', done: 1, total: 1 },
  { stage: 'detect', unit: 'frames', done: 1, total: 1 },
  { stage: 'solve', unit: 'candidates', done: 48, total: 220 },
  { stage: 'verify', unit: 'steps', done: 1, total: 1 },
] as const;
const succeeded: SolveJob = {
  id: `job_${sha256.slice(0, 16)}`,
  sourceAssetSha256: sha256,
  state: 'succeeded',
  createdAtUTC: '2026-01-19T05:31:00Z',
  startedAtUTC: '2026-01-19T05:31:01Z',
  finishedAtUTC: '2026-01-19T05:31:02Z',
  stages: [...STAGES_DONE],
  solutionId,
};
const failed: SolveJob = {
  id: `job_${sha256.slice(0, 16)}`,
  sourceAssetSha256: sha256,
  state: 'failed',
  createdAtUTC: '2026-01-19T05:31:00Z',
  startedAtUTC: '2026-01-19T05:31:01Z',
  finishedAtUTC: '2026-01-19T05:31:02Z',
  stages: [STAGES_DONE[0], { stage: 'decode', unit: 'frames', done: 0, total: 1 }],
  error: 'The decoder could not read the file.',
};
const samples = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, ObservationSolution>;
const base = samples['plate_calibrated']!;
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

type Answer = () => Response | Promise<Response>;
function server(routes: { ingest?: Answer; solve?: Answer; solution?: Answer }) {
  const calls: string[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString();
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (url === '/api/ingest') return (routes.ingest ?? (() => json(receipt, 201)))();
    if (url === `/api/ingest/${sha256}/solve?wait=false`) return (routes.solve ?? (() => json(succeeded, 201)))();
    if (url === `/api/ingest/${sha256}/solution`) return (routes.solution ?? (() => json(solution)))();
    return new Response('Not found', { status: 404 });
  });
  return calls;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const file = () => new File([new Uint8Array(12)], 'night.png', { type: 'image/png' });
const kinds = (events: UploadEvent[]) => events.map((event) => event.kind);

afterEach(() => vi.restoreAllMocks());

describe('uploadAndSolveMedia tells its listener what happened, as it happens', () => {
  it('reports the receipt, the job record, then solved, in that order, with the records themselves', async () => {
    const calls = server({});
    const events: UploadEvent[] = [];
    await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) });
    expect(kinds(events)).toEqual(['stored', 'job', 'solved']);
    expect(events[0]).toEqual({ kind: 'stored', receipt });
    expect(events[1]).toEqual({ kind: 'job', job: succeeded });
    expect(events[2]).toEqual({ kind: 'solved', job: succeeded });
    expect(calls).toEqual(['POST /api/ingest', `POST /api/ingest/${sha256}/solve?wait=false`, `GET /api/ingest/${sha256}/solution`]);
  });

  it('reports the receipt before the solve is asked for, so the file is known to be stored while the solve runs', async () => {
    const seen: string[] = [];
    server({
      solve: () => {
        seen.push('solve asked');
        return json(succeeded, 201);
      },
    });
    await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => seen.push(event.kind) });
    expect(seen).toEqual(['stored', 'solve asked', 'job', 'solved']);
  });

  it("reports a failed job with the job's own sentence, word for word, and its record", async () => {
    server({ solve: () => json(failed, 201) });
    const events: UploadEvent[] = [];
    await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) });
    expect(kinds(events)).toEqual(['stored', 'job', 'failed']);
    expect(events[2]).toEqual({ kind: 'failed', sentence: 'The decoder could not read the file.', job: failed });
  });

  it('says only that the solve stopped when the solve request gets no answer: no job record says why', async () => {
    server({
      solve: () => {
        throw new TypeError('Failed to fetch');
      },
    });
    const events: UploadEvent[] = [];
    const session = await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) });
    expect(kinds(events)).toEqual(['stored', 'failed']);
    expect(events[1]).toEqual({ kind: 'failed', sentence: 'The sky solve for your photo stopped before it finished.', job: null });
    // The stored session still says what it said before
    expect(session.lastStatusBanner).toBe('Stored "night.png", but the sky solve did not finish: no answer from the Albedian server (Failed to fetch).');
  });

  it('says the same, with the job it has, when the solve answered but the solution cannot be read', async () => {
    server({ solution: () => json({ detail: 'no solution is stored' }, 404) });
    const events: UploadEvent[] = [];
    await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) });
    expect(kinds(events)).toEqual(['stored', 'job', 'failed']);
    expect(events[2]).toEqual({ kind: 'failed', sentence: 'The sky solve for your photo stopped before it finished.', job: succeeded });
  });

  it('says clip for a clip', async () => {
    const clip: IngestReceipt = {
      ...receipt,
      sourceAsset: { ...receipt.sourceAsset, mediaKind: 'video', durationMs: 4000, fpsNumerator: 30, fpsDenominator: 1, videoCodec: 'h264', mimeType: 'video/mp4' },
    };
    server({ ingest: () => json(clip, 201), solve: () => json({ detail: 'busy' }, 503) });
    const events: UploadEvent[] = [];
    await uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) });
    expect(events.at(-1)).toMatchObject({ kind: 'failed', sentence: 'The sky solve for your clip stopped before it finished.', job: null });
  });

  it('reports nothing when the upload is refused: it throws, and nothing was stored', async () => {
    server({ ingest: () => json({ detail: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file' }, 415) });
    const events: UploadEvent[] = [];
    await expect(uploadAndSolveMedia(file(), 'night.png', { onEvent: (event) => events.push(event) })).rejects.toBeInstanceOf(UploadRefusedError);
    expect(events).toEqual([]);
  });
});

describe('solveStoredUpload starts the solve of a stored file again', () => {
  it('asks only for the solve and the solution, never sends the file again, and reports as the first run does', async () => {
    const calls = server({});
    const events: UploadEvent[] = [];
    const updates: string[] = [];
    const session = await solveStoredUpload(receipt, {
      localObjectUrl: 'blob:again',
      onEvent: (event) => events.push(event),
      onSessionUpdate: (s) => updates.push(s.machines.calibration),
    });
    expect(calls).toEqual([`POST /api/ingest/${sha256}/solve?wait=false`, `GET /api/ingest/${sha256}/solution`]);
    expect(kinds(events)).toEqual(['job', 'solved']);
    expect(updates).toEqual(['solving', 'calibrated']);
    expect(session.sourceAsset!.uri).toBe('blob:again');
    expect(session.observationSolution!.status).toBe('calibrated');
  });

  it('never throws: a second failure is reported like the first', async () => {
    server({ solve: () => json(failed, 201) });
    const events: UploadEvent[] = [];
    const session = await solveStoredUpload(receipt, { onEvent: (event) => events.push(event) });
    expect(kinds(events)).toEqual(['job', 'failed']);
    expect(session.machines.calibration).toBe('unresolved');
  });
});

describe('ingestAndSolveUploadedFile passes the events on, and words an upload that did not finish', () => {
  it('passes every event on', async () => {
    server({});
    const events: UploadEvent[] = [];
    await ingestAndSolveUploadedFile(file(), 'blob:x', () => {}, undefined, (event) => events.push(event));
    expect(kinds(events)).toEqual(['stored', 'job', 'solved']);
  });

  it('reports an upload the server never answered as failed with no job, in the words the app already uses', async () => {
    server({
      ingest: () => {
        throw new Error('Network unreachable');
      },
    });
    const events: UploadEvent[] = [];
    const refused = vi.fn();
    await ingestAndSolveUploadedFile(file(), 'blob:x', () => {}, refused, (event) => events.push(event));
    expect(events).toEqual([
      { kind: 'failed', sentence: 'The upload of "night.png" did not finish: no answer from the Albedian server (Network unreachable).', job: null },
    ]);
    expect(refused).not.toHaveBeenCalled();
  });

  it('reports a 5xx answer the same way: neither stored nor refused is proved', async () => {
    server({ ingest: () => json({ detail: 'the store is not writable' }, 500) });
    const events: UploadEvent[] = [];
    const refused = vi.fn();
    await ingestAndSolveUploadedFile(file(), 'blob:x', () => {}, refused, (event) => events.push(event));
    expect(events).toEqual([{ kind: 'failed', sentence: 'The upload of "night.png" did not finish: the store is not writable.', job: null }]);
    expect(refused).not.toHaveBeenCalled();
  });

  it('reports a refusal to the refusal listener only: the home shows it, the working screen does not', async () => {
    server({ ingest: () => json({ detail: 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file' }, 415) });
    const events: UploadEvent[] = [];
    const refused = vi.fn();
    await ingestAndSolveUploadedFile(file(), 'blob:x', () => {}, refused, (event) => events.push(event));
    expect(events).toEqual([]);
    expect(refused).toHaveBeenCalledWith('not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file');
  });
});
