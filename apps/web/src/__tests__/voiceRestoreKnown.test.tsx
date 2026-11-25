// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution, UploadJob } from '@albedian/contracts';

/**
 * voice-restore-known-one (DECIDED-BY-LEAD 25 Nov 02:37, choice (a); AUDIT-C's O10 and T14 on the voice stack): the
 * session learns the upload's stored restorations when its records are read, so what voice says of them is true on a
 * first visit too, and on the recording a restore that completes while the restored compare is not on screen does
 * what the screen's own control does: the Restore button's action when the compare is not up, Original pixels turned
 * off when it is on. The whole app is the real one (viewport and reveal), on a server that holds one upload with one
 * stored restoration; nothing is put in the session by hand.
 */

vi.mock('../spatial/SparkPortalRenderer.js', () => ({
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
vi.mock('../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { App } = await import('../App.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../tests/fixtures');
const sha256 = '5a17c0de0f1e2d3c'.padEnd(64, 'b');
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
    width: 3024,
    height: 4032,
    durationMs: 0,
    videoCodec: 'mjpeg',
    audioStreams: [],
    privacyClass: 'private',
    immutable: true,
  },
  hints: {},
  alreadyStored: true,
  solveJobId: `job_${sha256.slice(0, 16)}`,
};
const samples = JSON.parse(fs.readFileSync(path.join(fixtures, 'python_contract_solutions.json'), 'utf8')) as Record<string, ObservationSolution>;
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
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const job = { ...structuredClone(jobs.find((j) => j.kind === 'restoration' && j.state === 'succeeded')!), sourceAssetSha256: sha256 };
const stored = {
  ...media.valid.MediaVariant.find((v) => v.kind === 'restoration')!,
  id: job.variantId!,
  sourceSha256: sha256,
  sourceAssetId: assetId,
  uri: `/api/media/${sha256}/variants/${job.variantId}/file`,
};

let container: HTMLDivElement;
let root: Root;
let restoreAsked = 0;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  restoreAsked = 0;
  Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:chosen', configurable: true, writable: true });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
});

const q = (selector: string) => container.querySelector(selector);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** The app at the upload's address, on a server holding the upload, its solution and one stored restoration. */
async function openUpload({ storedNone = false, restoreHangs = false } = {}) {
  window.history.replaceState({}, '', `/?upload=${sha256}`);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/media/discover') return json({ items: [] });
      if (url === `/api/ingest/${sha256}`) return json(receipt);
      if (url === `/api/ingest/${sha256}/solution`) return json(solution);
      if (url === `/api/media/${sha256}/variants`) return json({ variants: storedNone ? [] : [stored], attempts: [] });
      if (url === `/api/media/${sha256}/variants/${stored.id}`) return json(stored);
      if (url === `/api/media/${sha256}/restore` && init?.method === 'POST') {
        restoreAsked += 1;
        return restoreHangs ? new Promise<Response>(() => {}) : json(job);
      }
      return json({ detail: 'not found' }, 404);
    })
  );
  await act(async () => {
    root.render(createElement(App));
  });
}
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
async function typeAndSend(words: string) {
  const usable = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)].find((el) => !el.closest('.is-leaving, [inert]')) ?? null;
  await waitUntil(() => expect(usable('.ask-bar__field input') ?? usable('.ask-bar__type') ?? usable('.ask-bar__ask')).not.toBeNull());
  if (!usable('.ask-bar__field input')) await act(async () => (usable('.ask-bar__type') ?? usable('.ask-bar__ask'))!.click());
  const input = usable('.ask-bar__field input') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, words);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => (input.form as HTMLFormElement).requestSubmit());
  await act(async () => await new Promise((r) => setTimeout(r, 30)));
}
const compareShown = () => q('[data-testid="restore-compare"]') !== null;
const button = (label: string) => [...container.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === label) ?? null;
const answer = () => q('.reveal-answer__s')?.textContent ?? null;

describe('a stored restoration, known from the records', () => {
  it('on a first visit, asked on the photo: the Restore button\'s action brings the stored one up', async () => {
    await openUpload();
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    expect(compareShown()).toBe(false);
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(compareShown()).toBe(true));
    expect(restoreAsked).toBe(1);
    expect(answer()).toBeNull();
    // Asked again with the compare up: the control does nothing, no second ask of the route
    await typeAndSend('make the sky clearer');
    expect(compareShown()).toBe(true);
    expect(restoreAsked).toBe(1);
    expect(answer()).toBeNull();
  });

  it('none known and one restoring: asked again, the true refusal shows and nothing is asked twice', async () => {
    await openUpload({ storedNone: true, restoreHangs: true });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(restoreAsked).toBe(1));
    expect(answer()).toBeNull();
    // The restoration is still being asked for: the control cannot act, so the refusal stands
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(answer()).toBe('This photo has not been restored.'));
    expect(restoreAsked).toBe(1);
  });

  it('compare with the original on a first visit: the stored version brought up, compared with the photo', async () => {
    await openUpload();
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('compare with the original');
    await waitUntil(() => expect(compareShown()).toBe(true));
    expect(restoreAsked).toBe(1);
    expect(answer()).toBeNull();
  });

  it('on a first visit, asked in Real sky: the true sentence, never not restored', async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    await openUpload();
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('.reveal-nav [role="group"] button')].find((b) => b.textContent === 'Real sky')!).click());
    await waitUntil(() => expect(q('[data-testid="real-sky"]')).not.toBeNull());
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(answer()).toBe('The restored version shows on this photo itself; Your photo returns to it.'));
    expect(restoreAsked).toBe(0);
  });

  it('under Original pixels, asked on the photo: Original pixels turned off and the restored compare shown (T14)', async () => {
    await openUpload();
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(compareShown()).toBe(true));
    // A star's card open, then Original pixels pressed on it: the photo whole, the compare gone
    const mark = q('[data-testid^="reveal-mark-"]') as HTMLButtonElement;
    await act(async () => mark.click());
    await waitUntil(() => expect(button('Original pixels')).not.toBeNull());
    await act(async () => button('Original pixels')!.click());
    await waitUntil(() => expect(compareShown()).toBe(false));
    await typeAndSend('make the sky clearer');
    await waitUntil(() => expect(compareShown()).toBe(true));
    expect(button('Original pixels')?.getAttribute('aria-pressed') ?? 'false').toBe('false');
    expect(answer()).toBeNull();
  });
});
