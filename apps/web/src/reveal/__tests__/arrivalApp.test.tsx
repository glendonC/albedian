// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IngestReceiptSchema, type IngestReceipt, type ObservationSolution, type SolveJob } from '@albedian/contracts';
import { HOME_SCREEN } from '../../components/__tests__/homeEntry.js';
import { arrivalAfter, arrivalOf, type Arrival } from '../arrival.js';

// The whole app through an upload (unit 1c): the working screen follows the real steps of the upload and its solve,
// a failed job can be tried again without sending the file again, and a new choice makes an older upload's steps
// change nothing on screen.

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    showSkyView() {}
    setDrawing() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));
vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { App } = await import('../../App.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const sha256 = 'd'.repeat(64);
const assetId = `src_${sha256.slice(0, 16)}`;
const solutionId = `sol_${sha256.slice(0, 16)}`;
const receipt: IngestReceipt = {
  sourceAsset: {
    id: assetId,
    title: 'night.jpg',
    mediaKind: 'image',
    sha256,
    uri: `/api/ingest/${sha256}/original`,
    byteSize: 2_382_523,
    mimeType: 'image/jpeg',
    width: 4032,
    height: 3024,
    displayOrientation: { quarterTurnsClockwise: 1, mirrored: false },
    metadataSources: { displayOrientation: { container: 'exif', tags: ['Orientation'] } },
    durationMs: 0,
    videoCodec: 'mjpeg',
    audioStreams: [],
    privacyClass: 'private',
    immutable: true,
  },
  // A phone photo stored on its side: the orientation is a hint with its source, as the contract requires
  hints: { orientation: { quarterTurnsClockwise: 1, mirrored: false, source: { container: 'exif', tags: ['Orientation'] } } },
  alreadyStored: false,
};
const STORE = { stage: 'store', unit: 'bytes', done: 2_382_523, total: 2_382_523 } as const;
const succeeded: SolveJob = {
  id: `job_${sha256.slice(0, 16)}`,
  sourceAssetSha256: sha256,
  state: 'succeeded',
  createdAtUTC: '2026-01-19T05:31:00Z',
  startedAtUTC: '2026-01-19T05:31:01Z',
  finishedAtUTC: '2026-01-19T05:31:02Z',
  stages: [
    STORE,
    { stage: 'decode', unit: 'frames', done: 1, total: 1 },
    { stage: 'detect', unit: 'frames', done: 1, total: 1 },
    { stage: 'solve', unit: 'candidates', done: 48, total: 220 },
    { stage: 'verify', unit: 'steps', done: 1, total: 1 },
  ],
  solutionId,
};
const failed: SolveJob = {
  id: `job_${sha256.slice(0, 16)}`,
  sourceAssetSha256: sha256,
  state: 'failed',
  createdAtUTC: '2026-01-19T05:31:00Z',
  startedAtUTC: '2026-01-19T05:31:01Z',
  finishedAtUTC: '2026-01-19T05:31:02Z',
  stages: [STORE, { stage: 'decode', unit: 'frames', done: 1, total: 1 }, { stage: 'detect', unit: 'frames', done: 1, total: 1 }, { stage: 'solve', unit: 'candidates', done: 2080, total: 4160 }],
  error: 'The solve stopped on an internal error.',
};
const samples = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, ObservationSolution>;
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

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  let made = 0;
  Object.defineProperty(URL, 'createObjectURL', { value: () => `blob:arrival-${++made}`, configurable: true, writable: true });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (URL as unknown as Record<string, unknown>).createObjectURL;
});

const q = (selector: string) => container.querySelector(selector);
const WORKING = '[data-testid="working-screen"]';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const rowClasses = () => [...container.querySelectorAll('.work-step')].map((row) => row.className.replace('work-step ', ''));
const rowValues = () => [...container.querySelectorAll('.work-step__value')].map((v) => v.textContent);

/** An answer the test releases when it chooses, so the screen can be read while the server has not answered. */
function held() {
  let release: (response: Response) => void = () => {};
  let fail: (error: unknown) => void = () => {};
  const answer = new Promise<Response>((resolve, reject) => {
    release = resolve;
    fail = reject;
  });
  return { answer, release, fail };
}

/** The app on a server whose answers to each route come from the given queues, in order; returns the calls made. */
async function renderApp(routes: { ingest: Array<() => Promise<Response>>; solve?: Array<() => Promise<Response>>; solution?: Array<() => Promise<Response>> }) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/media/discover') return json({ items: [] });
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      const queue = url === '/api/ingest' ? routes.ingest : url.endsWith('/solve') ? routes.solve : url.endsWith('/solution') ? routes.solution : undefined;
      const next = queue?.shift();
      if (!next) throw new Error(`no answer planned for ${url}`);
      return next();
    })
  );
  await act(async () => {
    root.render(createElement(App));
  });
  return calls;
}
function readableFile(name: string, bytes = 4): File {
  const file = new File([new Uint8Array(bytes)], name, { type: 'image/jpeg' });
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(bytes).buffer });
  return file;
}
function choose(selector: string, file: File) {
  const input = container.querySelector<HTMLInputElement>(selector)!;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
const chooseOnHome = (file: File) => choose('[data-testid="home-file-input"]', file);
async function waitUntil(check: () => void) {
  for (let waited = 0; ; waited += 10) {
    await act(async () => await new Promise((r) => setTimeout(r, 10)));
    try {
      return check();
    } catch (error) {
      if (waited >= 2000) throw error;
    }
  }
}

describe('arrivalOf and arrivalAfter: the working screen holds only what the app has been told', () => {
  const chosen = arrivalOf({ name: 'night.jpg', size: 2_400_000 });

  it('reads receipts the contract accepts', () => {
    expect(() => IngestReceiptSchema.parse(receipt)).not.toThrow();
  });

  it('knows a chosen file by its name and the bytes the browser counts, and nothing else', () => {
    expect(chosen).toEqual({ fileName: 'night.jpg', byteSize: 2_400_000, stored: false, widthPx: null, heightPx: null, job: null, failure: null });
  });

  it("takes the stored bytes and the upright frame from the receipt: a photo stored on its side is written as it is shown", () => {
    const stored = arrivalAfter(chosen, { kind: 'stored', receipt })!;
    expect(stored).toEqual({ fileName: 'night.jpg', byteSize: 2_382_523, stored: true, widthPx: 3024, heightPx: 4032, job: null, failure: null });
    const upright: IngestReceipt = { ...receipt, hints: {}, sourceAsset: { ...receipt.sourceAsset, displayOrientation: undefined, metadataSources: undefined } };
    expect(arrivalAfter(chosen, { kind: 'stored', receipt: upright })).toMatchObject({ widthPx: 4032, heightPx: 3024 });
  });

  it("keeps the browser's byte count when the receipt gives none, and writes no frame the receipt did not measure", () => {
    const bare: IngestReceipt = { ...receipt, sourceAsset: { ...receipt.sourceAsset, byteSize: undefined, width: 1, height: 1 } };
    expect(arrivalAfter(chosen, { kind: 'stored', receipt: bare })).toMatchObject({ stored: true, byteSize: 2_400_000, widthPx: null, heightPx: null });
  });

  it('replaces the job record with each new one', () => {
    const stored = arrivalAfter(chosen, { kind: 'stored', receipt })!;
    const running = arrivalAfter(stored, { kind: 'job', job: failed })!;
    expect(running.job).toBe(failed);
    expect(arrivalAfter(running, { kind: 'job', job: succeeded })!.job).toBe(succeeded);
    expect(running.failure).toBeNull();
  });

  it("adds a failure's sentence and its job, keeps the job it had when the failure brings none, and offers to try again", () => {
    const stored = arrivalAfter(chosen, { kind: 'stored', receipt })!;
    const withJob = arrivalAfter(stored, { kind: 'job', job: succeeded })!;
    const stopped = arrivalAfter(withJob, { kind: 'failed', sentence: 'The sky solve for your photo stopped before it finished.', job: null })!;
    expect(stopped.failure).toEqual({ sentence: 'The sky solve for your photo stopped before it finished.', canTryAgain: true });
    expect(stopped.job).toBe(succeeded);
    expect(stopped.stored).toBe(true);
    const failedJob = arrivalAfter(withJob, { kind: 'failed', sentence: failed.error!, job: failed })!;
    expect(failedJob.job).toBe(failed);
    expect(failedJob.failure!.sentence).toBe('The solve stopped on an internal error.');
  });

  it('ends when the sky is solved, and an event with no arrival on screen starts none', () => {
    const stored = arrivalAfter(chosen, { kind: 'stored', receipt })!;
    expect(arrivalAfter(stored, { kind: 'solved', job: succeeded })).toBeNull();
    const none: Arrival | null = null;
    expect(arrivalAfter(none, { kind: 'stored', receipt })).toBeNull();
    expect(arrivalAfter(none, { kind: 'job', job: succeeded })).toBeNull();
    expect(arrivalAfter(none, { kind: 'failed', sentence: 'x', job: null })).toBeNull();
  });
});

describe('the app follows an upload on the working screen', () => {
  it('shows Saved being worked while the file is sent, Saved done once stored, then the solved photo', async () => {
    const ingest = held();
    const solve = held();
    const calls = await renderApp({ ingest: [() => ingest.answer], solve: [() => solve.answer], solution: [async () => json(solution)] });
    chooseOnHome(readableFile('night.jpg'));
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q(WORKING)).not.toBeNull();
    expect(rowClasses()).toEqual(['is-now', 'is-next', 'is-next', 'is-next', 'is-next']);
    expect(q('.work-size')!.textContent).toBe('0.000004 MB');
    expect(q('[data-testid="source-photo-stage"]')).toBeNull();

    ingest.release(json(receipt, 201));
    await waitUntil(() => expect(rowClasses()).toEqual(['is-done', 'is-next', 'is-next', 'is-next', 'is-next']));
    // From the receipt: the stored bytes and the frame as shown, upright
    expect(q('.work-size')!.textContent).toBe('3024 × 4032 · 2.4 MB');
    expect(rowValues()).toEqual(['2.4 MB', '', '', '', '']);
    expect(q('[data-testid="source-photo-stage"] img')!.getAttribute('src')).toBe('blob:arrival-1');
    expect(q('.alb-lusion-header')).toBeNull();

    solve.release(json(succeeded, 201));
    await waitUntil(() => expect(q(WORKING)).toBeNull());
    expect(q('[data-testid="source-photo-stage"] img')!.getAttribute('src')).toBe('blob:arrival-1');
    expect(q('.alb-lusion-header')).not.toBeNull();
    expect(calls).toEqual(['POST /api/ingest', `POST /api/ingest/${sha256}/solve`, `GET /api/ingest/${sha256}/solution`]);
  });

  it("shows a failed job's own sentence and stage, and Try again runs the same solve without sending the file again", async () => {
    const again = held();
    const calls = await renderApp({
      ingest: [async () => json(receipt, 201)],
      solve: [async () => json(failed, 201), () => again.answer],
      solution: [async () => json(solution)],
    });
    chooseOnHome(readableFile('night.jpg'));
    await waitUntil(() => expect(q('.work-why p')?.textContent).toBe('The solve stopped on an internal error.'));
    expect(rowClasses()).toEqual(['is-done', 'is-done', 'is-done', 'is-failed', 'is-next']);
    expect(rowValues()).toEqual(['2.4 MB', '', '', '2,080 of 4,160', '']);
    expect((q('[data-testid="source-photo-stage"] img') as HTMLImageElement).style.opacity).toBe('0.55');

    act(() => (q(`${WORKING} button.look-main`) as HTMLButtonElement).click());
    // Asked again: the failure is gone from the screen, and nothing is claimed about the new job yet
    expect(q('.work-why')).toBeNull();
    expect(rowClasses()).toEqual(['is-done', 'is-next', 'is-next', 'is-next', 'is-next']);
    expect((q('[data-testid="source-photo-stage"] img') as HTMLImageElement).style.opacity).toBe('');
    again.release(json(succeeded, 201));
    await waitUntil(() => expect(q(WORKING)).toBeNull());
    expect(calls).toEqual([
      'POST /api/ingest',
      `POST /api/ingest/${sha256}/solve`,
      `POST /api/ingest/${sha256}/solve`,
      `GET /api/ingest/${sha256}/solution`,
    ]);
  });

  it('marks Saved as stopped when the file never reached the server, and Try again sends the same file again', async () => {
    const calls = await renderApp({
      ingest: [async () => Promise.reject(new TypeError('Load failed')), async () => json(receipt, 201)],
      solve: [async () => json(succeeded, 201)],
      solution: [async () => json(solution)],
    });
    chooseOnHome(readableFile('night.jpg'));
    await waitUntil(() =>
      expect(q('.work-why p')?.textContent).toBe('The upload of "night.jpg" did not finish: no answer from the Albedian server (Load failed).')
    );
    expect(rowClasses()).toEqual(['is-failed', 'is-next', 'is-next', 'is-next', 'is-next']);
    expect(q('[data-testid="source-photo-stage"]')).toBeNull();
    act(() => (q(`${WORKING} button.look-main`) as HTMLButtonElement).click());
    await waitUntil(() => expect(q(WORKING)).toBeNull());
    expect(calls.filter((c) => c === 'POST /api/ingest')).toHaveLength(2);
  });

  it('starts a new upload from Upload another, and the failed one is gone from the screen', async () => {
    const second = held();
    const calls = await renderApp({ ingest: [async () => json(receipt, 201), () => second.answer], solve: [async () => json(failed, 201)] });
    chooseOnHome(readableFile('night.jpg'));
    await waitUntil(() => expect(q('.work-why')).not.toBeNull());
    choose('[data-testid="working-file-input"]', readableFile('other.jpg', 8));
    expect(q('.work-why')).toBeNull();
    expect(rowClasses()).toEqual(['is-now', 'is-next', 'is-next', 'is-next', 'is-next']);
    expect(q('.work-size')!.textContent).toBe('0.000008 MB');
    await waitUntil(() => expect(calls.filter((c) => c === 'POST /api/ingest')).toHaveLength(2));
    second.release(json({ detail: 'the upload is empty' }, 400));
    // Refused: nothing was stored, so the home comes back with the reason and the working screen is gone
    await waitUntil(() => expect(q('.home-refusal')?.textContent).toBe("Albedian can't open other.jpgThe upload is empty."));
    expect(q(WORKING)).toBeNull();
  });

  it("changes nothing for an older upload's steps once another file was chosen", async () => {
    const first = held();
    const second = held();
    await renderApp({ ingest: [() => first.answer, () => second.answer], solve: [async () => json(failed, 201)] });
    chooseOnHome(readableFile('first.jpg'));
    act(() => (q('[data-testid="working-home"]') as HTMLButtonElement).click());
    chooseOnHome(readableFile('second.jpg', 16));
    expect(q('.work-size')!.textContent).toBe('0.00002 MB');
    // The first upload is stored and its solve fails, after the second was chosen: the screen stays the second's
    first.release(json(receipt, 201));
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(q('.work-size')!.textContent).toBe('0.00002 MB');
    expect(rowClasses()).toEqual(['is-now', 'is-next', 'is-next', 'is-next', 'is-next']);
    expect(q('.work-why')).toBeNull();
    expect(q('[data-testid="source-photo-stage"]')).toBeNull();
    second.release(new Response('', { status: 503 }));
    await act(async () => await new Promise((r) => setTimeout(r, 0)));
  });
});
