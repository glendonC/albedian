// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution } from '@albedian/contracts';
import { HOME_SCREEN } from '../components/__tests__/homeEntry.js';
import { PHONE_QUERY } from '../components/phoneSheet.js';
import { poseReceipt } from '../components/__tests__/poseSessions.js';

// The app opened at ?upload=<sha256>: the stored upload's reveal shows with no upload and no solve, and an address
// that names nothing openable leaves the app on its home, as if it had no such value.

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
// The solve as it is, wrapped so one test can make it stop on something unforeseen
vi.mock('../services/apiClient.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../services/apiClient.js')>();
  return { ...original, solveStoredUpload: vi.fn(original.solveStoredUpload) };
});
const { solveStoredUpload } = await import('../services/apiClient.js');

const { App } = await import('../App.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const sha256 = '4f055db773c14698'.padEnd(64, 'a');
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
const samples = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')) as Record<string, ObservationSolution>;
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
  Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:chosen', configurable: true, writable: true });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  delete (URL as unknown as Record<string, unknown>).createObjectURL;
});

const q = (selector: string) => container.querySelector(selector);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** The app at `address`, on a server that holds the one upload; returns every request but the media listing. */
async function openAppAt(address: string, routes: { receipt?: () => Response | Promise<Response>; solution?: () => Response | Promise<Response> } = {}) {
  window.history.replaceState({}, '', address);
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/media/discover') return json({ items: [] });
      // The sample's readiness read as the app starts (sample-pill-one): not offered here
      if (url === '/api/samples') return json({ detail: 'not found' }, 404);
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url === `/api/ingest/${sha256}`) return (routes.receipt ?? (() => json(receipt)))();
      if (url === `/api/ingest/${sha256}/solution`) return (routes.solution ?? (() => json(solution)))();
      return json({ detail: 'not found' }, 404);
    })
  );
  await act(async () => {
    root.render(createElement(App));
  });
  return calls;
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
const settle = () => act(async () => await new Promise((r) => setTimeout(r, 30)));

describe('the app opened at ?upload=<sha256>', () => {
  it("leaves the home for the stored upload's photo and sky, with three reads and nothing sent", async () => {
    const calls = await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    expect(q(HOME_SCREEN)).toBeNull();
    const photo = q('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    // The stored original, from the server's own route for that hash
    expect(photo.getAttribute('src')).toBe(`/api/ingest/${sha256}/original`);
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    // The receipt, the solution, and the upload's stored restorations, which the session learns as its records are read
    // (voice-restore-known-one)
    expect(calls).toEqual([`GET /api/ingest/${sha256}`, `GET /api/ingest/${sha256}/solution`, `GET /api/media/${sha256}/variants`]);
  });

  it.each([
    ['a value that is no sha256', '/?upload=..%2F..%2Fetc', 0],
    ['capital letters', `/?upload=${sha256.toUpperCase()}`, 0],
    ['the key twice', `/?upload=${sha256}&upload=${sha256}`, 0],
    ['no such value', '/?view=home', 0],
  ])('stays on the home and asks the server nothing for %s', async (_label, address, requests) => {
    const calls = await openAppAt(address);
    await settle();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(calls).toHaveLength(requests);
  });

  it('stays on the home when no file with that hash is stored', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, { receipt: () => json({ detail: 'no file with that hash is stored' }, 404) });
    await settle();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q('.home-refusal')!.textContent).toBe('');
    expect(calls).toEqual([`GET /api/ingest/${sha256}`]);
  });

  it('stays on the home when the upload has no stored solution, and starts no solve', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, { solution: () => json({ detail: 'no solution is stored for that file' }, 404) });
    await settle();
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(calls.some((call) => call.startsWith('POST'))).toBe(false);
  });
});

// open-direct-one (UIUX-opendirect-READY.md, asked by the lead at 22:19 on 24 Nov: MOBILE saw the web show its own home
// for about a second before the reveal when opened at an upload's address): until the upload's records are read, the
// app's black with the logo in its place and nothing else; then the sequence enters at its step 3, as if the drop had
// just happened: the photo frames in where the reveal places it, and with the job already done the reveal follows at
// once (marks brightest first, then the words), the working rows never drawn. An address whose upload is not stored
// lands on the home, the one screen that can take a file.
describe('a page opened at an upload\'s address, until the reveal is ready', () => {
  const held = () => {
    let release: (response: Response) => void = () => {};
    const answer = new Promise<Response>((resolve) => (release = resolve));
    return { answer: () => answer, release: (response: Response) => release(response) };
  };
  const wait = (ms: number) => act(async () => await new Promise((r) => setTimeout(r, ms)));
  const WAIT = '[data-testid="open-wait"]';
  const STAGE = '[data-testid="source-photo-stage"]';

  it("shows the app's black with the logo in its place and nothing else while the records are read", async () => {
    const receiptRead = held();
    await openAppAt(`/?upload=${sha256}`, { receipt: receiptRead.answer });
    await settle();
    expect(q(HOME_SCREEN)).toBeNull();
    const shown = q(WAIT)!;
    expect(shown.classList.contains('is-black')).toBe(true);
    // The logo, at the laptop's 21 px, and no other words, no button, no field, no count
    expect(shown.querySelector('.look-logo')!.getAttribute('style')).toContain('--look-logo-fs: 21px');
    expect(container.textContent).toBe('albedian');
    expect(container.querySelectorAll('button, input, [role="progressbar"], [role="status"]')).toHaveLength(0);
    expect(q(STAGE)).toBeNull();
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
    receiptRead.release(json(receipt));
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
  });

  it('frames the photo in where the reveal places it, then the reveal follows once the frame is in, the working rows never drawn', async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q(STAGE)).not.toBeNull());
    expect(q(STAGE)!.classList.contains('is-entering')).toBe(true);
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q('[data-testid="working-screen"]')).toBeNull();
    // The logo stays in its place on the photo's frame, the black gone: the photo is what comes in
    expect(q(WAIT)!.classList.contains('is-black')).toBe(false);
    expect(q(`${WAIT} .look-logo`)).not.toBeNull();
    // Nothing of the reveal while the photo frames in (400 ms): no marks, no words, no views, no credits
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
    await wait(300);
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
    expect([...container.querySelectorAll('button')].map((b) => b.outerHTML)).toEqual([]);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    expect(q(STAGE)!.classList.contains('is-entering')).toBe(false);
    expect(q(WAIT)).toBeNull();
    expect(q('[data-testid="reveal-home"] .look-logo')).not.toBeNull();
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(q(HOME_SCREEN)).toBeNull();
  });

  it("shows the app's black alone at phone width, with no logo, until the photo comes in (UIUX for open-direct-one)", async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === PHONE_QUERY, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    const receiptRead = held();
    await openAppAt(`/?upload=${sha256}`, { receipt: receiptRead.answer });
    await settle();
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q(WAIT)!.classList.contains('is-black')).toBe(true);
    expect(q(`${WAIT} .look-logo`)).toBeNull();
    expect(container.textContent).toBe('');
    receiptRead.release(json(receipt));
    await waitUntil(() => expect(q(STAGE)?.classList.contains('is-entering')).toBe(true));
    // The phone's reveal draws no logo, so none stands over the photo coming in either
    expect(container.querySelector('.look-logo')).toBeNull();
    await waitUntil(() => expect(q('[data-testid="reveal-photo"]')).not.toBeNull());
    expect(q(WAIT)).toBeNull();
  });

  it('frames the photo in on the 270 ms dissolve under Reduce Motion', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('reduce'), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q(STAGE)).not.toBeNull());
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
  });

  it('keeps the black until the browser has decoded the photo, so the photo frames in whole', async () => {
    let decoded: () => void = () => {};
    const decoding = new Promise<void>((resolve) => (decoded = resolve));
    const decode = vi.fn(() => decoding);
    Object.defineProperty(HTMLImageElement.prototype, 'decode', { value: decode, configurable: true, writable: true });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(decode).toHaveBeenCalled());
      await settle();
      expect(q(WAIT)!.classList.contains('is-black')).toBe(true);
      expect(q(STAGE)).toBeNull();
      decoded();
      await waitUntil(() => expect(q(STAGE)?.classList.contains('is-entering')).toBe(true));
    } finally {
      delete (HTMLImageElement.prototype as unknown as Record<string, unknown>).decode;
    }
  });

  it('goes on to the photo when the browser cannot decode it: the stage says so itself', async () => {
    Object.defineProperty(HTMLImageElement.prototype, 'decode', { value: () => Promise.reject(new Error('EncodingError')), configurable: true, writable: true });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q(STAGE)).not.toBeNull());
    } finally {
      delete (HTMLImageElement.prototype as unknown as Record<string, unknown>).decode;
    }
  });

  it.each<[string, { receipt?: () => Response; solution?: () => Response }]>([
    ['no file with that hash is stored', { receipt: () => json({ detail: 'no file with that hash is stored' }, 404) }],
    ['the upload has no stored solution', { solution: () => json({ detail: 'no solution is stored for that file' }, 404) }],
  ])('lands on the home after the black when %s, never the home first', async (_label, routes) => {
    const receiptRead = held();
    await openAppAt(`/?upload=${sha256}`, { receipt: receiptRead.answer, ...(routes.solution ? { solution: routes.solution } : {}) });
    await settle();
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q(WAIT)).not.toBeNull();
    receiptRead.release(routes.receipt ? routes.receipt() : json(receipt));
    await waitUntil(() => expect(q(HOME_SCREEN)).not.toBeNull());
    expect(q(WAIT)).toBeNull();
    expect(q(STAGE)).toBeNull();
  });

  it('shows the home at once, with no black first, for an address that names no upload', async () => {
    await openAppAt('/?upload=..%2F..%2Fetc');
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q(WAIT)).toBeNull();
  });

  it('draws the black over everything at its place and the logo where the reveal draws its own; the photo frames in on the frame-in time', () => {
    const declsOf = (file: string, selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(fs.readFileSync(path.resolve(here, `../styles/${file}`), 'utf8')).walkRules((rule) => {
        if (rule.parent?.type !== 'root' || !rule.selectors.includes(selector)) return;
        rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
      });
      return found;
    };
    expect(declsOf('reveal.css', '.open-wait')).toEqual({ position: 'fixed', inset: '0', 'z-index': '30', 'pointer-events': 'none' });
    expect(declsOf('reveal.css', '.open-wait.is-black')).toEqual({ background: 'var(--look-field)' });
    const reveal = declsOf('reveal.css', '.reveal-word');
    expect(declsOf('reveal.css', '.open-wait__word')).toEqual({ position: reveal.position, left: reveal.left, top: reveal.top });
    expect(declsOf('motion.css', '.alb-source-stage.is-entering')).toEqual({ animation: 'look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both' });
  });
});

// open-direct-two (UIUX-opendirect-READY.md item 2, on PLATFORM's job-by-upload-one route: GET /api/ingest/{sha256}/solve
// gives the upload's newest solve job in the job record's shape, 404 when none was started): an upload with no stored
// solution whose solve is still running opens on the working rows, as the record's step 3, and follows that job to its
// reveal; one whose solve stopped opens on the stopped screen (D2), whose Try again is the same solve of the stored
// file. Nothing is started by opening: the route is a read. With no job at all, the home after the black, as before.
describe("a page opened at the address of an upload whose solve has not ended, or stopped", () => {
  const jobId = `job_${sha256.slice(0, 16)}`;
  const record = (state: string, extra: Record<string, unknown> = {}) => ({
    id: jobId,
    sourceAssetSha256: sha256,
    state,
    createdAtUTC: '2026-01-19T05:31:00Z',
    startedAtUTC: '2026-01-19T05:31:01Z',
    stages: [
      { stage: 'store', unit: 'bytes', done: 2_382_523, total: 2_382_523 },
      { stage: 'decode', unit: 'frames', done: 1, total: 1 },
      { stage: 'solve', unit: 'candidates', done: 120, total: 400 },
    ],
    ...extra,
  });
  /** The app at the upload's address on a server whose answers `route` gives; every request it made, with its method. */
  async function openWith(route: (method: string, url: string) => Response | undefined) {
    window.history.replaceState({}, '', `/?upload=${sha256}`);
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === '/api/media/discover') return json({ items: [] });
        // The sample's readiness read as the app starts (sample-pill-one): not offered here
        if (url === '/api/samples') return json({ detail: 'not found' }, 404);
        const method = init?.method ?? 'GET';
        calls.push(`${method} ${url}`);
        if (method === 'GET' && url === `/api/ingest/${sha256}`) return json(receipt);
        return route(method, url) ?? json({ detail: 'not found' }, 404);
      })
    );
    await act(async () => {
      root.render(createElement(App));
    });
    return calls;
  }
  const noSolution = () => json({ detail: 'no solution is stored for that file' }, 404);

  it('opens a running solve on the working rows after the black, never the home, and follows it to the reveal', async () => {
    let done = false;
    const calls = await openWith((method, url) => {
      if (method === 'GET' && url === `/api/ingest/${sha256}/solution`) return done ? json(solution) : noSolution();
      if (method === 'GET' && url === `/api/ingest/${sha256}/solve`) return json(record('running'));
      if (method === 'GET' && url === `/api/ingest/jobs/${jobId}`) {
        done = true;
        return json(record('succeeded', { finishedAtUTC: '2026-01-19T05:31:09Z', solutionId }));
      }
      return undefined;
    });
    await waitUntil(() => expect(q('[data-testid="working-screen"]')).not.toBeNull());
    expect(q(HOME_SCREEN)).toBeNull();
    // The record's step 3: the photo frames in under the working rows, whose own logo stands, so no wait is left over it
    expect(q('[data-testid="source-photo-stage"]')!.classList.contains('is-entering')).toBe(true);
    expect(q('[data-testid="open-wait"]')).toBeNull();
    // The photo stands in its reveal place under the rows, the size line the stored file's own
    expect((q('[data-testid="source-photo-stage"] img') as HTMLImageElement).getAttribute('src')).toBe(`/api/ingest/${sha256}/original`);
    expect(q('[data-testid="working-screen"] .work-size')!.textContent).toContain('3024 × 4032');
    // Followed to its end, then the reveal
    for (let waited = 0; !q('[data-testid="reveal-marks"]') && waited < 4000; waited += 50) await act(async () => await new Promise((r) => setTimeout(r, 50)));
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(q(HOME_SCREEN)).toBeNull();
    // Read only: nothing was started or sent
    expect(calls.some((call) => call.startsWith('POST'))).toBe(false);
    expect(calls).toContain(`GET /api/ingest/${sha256}/solve`);
  });

  it('opens a stopped solve on the stopped screen, whose Try again starts the same solve of the stored file', async () => {
    const calls = await openWith((method, url) => {
      if (method === 'GET' && url === `/api/ingest/${sha256}/solution`) return noSolution();
      if (method === 'GET' && url === `/api/ingest/${sha256}/solve`) return json(record('failed', { finishedAtUTC: '2026-01-19T05:31:09Z', error: 'The sky solve for your photo stopped before it finished.' }));
      if (method === 'POST' && url === `/api/ingest/${sha256}/solve?wait=false`) return json(record('queued', { startedAtUTC: undefined }));
      return undefined;
    });
    await waitUntil(() => expect(q('[data-testid="working-screen"] [role="alert"]')).not.toBeNull());
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q('[data-testid="working-screen"]')!.textContent).toContain('The sky solve for your photo stopped before it finished.');
    expect(calls.some((call) => call.startsWith('POST'))).toBe(false);
    const tryAgain = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Try again')!;
    await act(async () => tryAgain.click());
    await waitUntil(() => expect(calls).toContain(`POST /api/ingest/${sha256}/solve?wait=false`));
  });

  it('cancels the running solve it opened on, with the working screen\'s Cancel', async () => {
    const calls = await openWith((method, url) => {
      if (method === 'GET' && url === `/api/ingest/${sha256}/solution`) return noSolution();
      if (method === 'GET' && url === `/api/ingest/${sha256}/solve`) return json(record('running'));
      if (method === 'GET' && url === `/api/ingest/jobs/${jobId}`) return json(record('running'));
      if (method === 'POST' && url === `/api/ingest/jobs/${jobId}/cancel`) return json(record('running', { cancelRequestedAtUTC: '2026-01-19T05:31:05Z' }));
      return undefined;
    });
    // The record it opened on is told at once: Cancel stands with the rows, before the first read of the job (a second)
    await waitUntil(() => expect(q('[data-testid="working-screen"]')).not.toBeNull());
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Cancel')).toBe(true);
    expect(calls).not.toContain(`GET /api/ingest/jobs/${jobId}`);
    await act(async () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Cancel')!.click());
    await waitUntil(() => expect(calls).toContain(`POST /api/ingest/jobs/${jobId}/cancel`));
  });

  it('keeps the black until the browser has decoded the photo, so it frames in whole under the rows', async () => {
    let decoded: () => void = () => {};
    const decoding = new Promise<void>((resolve) => (decoded = resolve));
    const decode = vi.fn(() => decoding);
    Object.defineProperty(HTMLImageElement.prototype, 'decode', { value: decode, configurable: true, writable: true });
    try {
      await openWith((method, url) => {
        if (method === 'GET' && url === `/api/ingest/${sha256}/solution`) return noSolution();
        if (method === 'GET' && url === `/api/ingest/${sha256}/solve`) return json(record('running'));
        if (method === 'GET' && url === `/api/ingest/jobs/${jobId}`) return json(record('running'));
        return undefined;
      });
      await waitUntil(() => expect(decode).toHaveBeenCalled());
      await settle();
      expect(q('[data-testid="open-wait"]')!.classList.contains('is-black')).toBe(true);
      expect(q('[data-testid="working-screen"]')).toBeNull();
      decoded();
      await waitUntil(() => expect(q('[data-testid="working-screen"]')).not.toBeNull());
    } finally {
      delete (HTMLImageElement.prototype as unknown as Record<string, unknown>).decode;
    }
  });

  it('lands on the home after the black when no solve was ever started, as before', async () => {
    const calls = await openWith((method, url) => {
      if (method === 'GET' && url === `/api/ingest/${sha256}/solution`) return noSolution();
      if (method === 'GET' && url === `/api/ingest/${sha256}/solve`) return json({ detail: 'no solve job is stored for that file' }, 404);
      return undefined;
    });
    await waitUntil(() => expect(q(HOME_SCREEN)).not.toBeNull());
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`, `GET /api/ingest/${sha256}/solution`, `GET /api/ingest/${sha256}/solve`]);
  });
});

// Solve from here on an upload opened by its address (as the iPhone's Open leads here): the tab has no upload of its
// own, so the solve is sent with the stored receipt, whose hints are the stored photo's recorded moment and place (the
// receipt contract checks each against the asset); a solve that cannot start shows the decided failed job, never a
// screen that does not move (WEB-UX-solveaddr-NOTE.md: on main the screen stayed on "Matching near your alignment.")
describe('Solve from here on an upload opened by its address', () => {
  const placed = samples['horizon_predicted_only']!;
  const template = placed.findings.find((f) => f.evidenceClass === 'predicted')!;
  const starRef = samples['pose_moon_level_head']!.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
  const star = (id: string, title: string, altDeg: number, azDeg: number, apparentMagnitude: number) => ({
    ...template,
    id,
    title,
    observationSolutionId: solutionId,
    skyPosition: { ...template.skyPosition!, altDeg, azDeg },
    catalogRef: { ...starRef, apparentMagnitude },
  });
  const sky: ObservationSolution = {
    ...placed,
    id: solutionId,
    sourceAssetId: assetId,
    sourceSha256: sha256,
    findings: [star('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16), star('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38)],
  };
  // The stored receipt with the photo's recorded moment and place, its hints saying the same
  const withMoment = poseReceipt(sky, 'image');
  const stored: IngestReceipt = { ...withMoment, sourceAsset: { ...withMoment.sourceAsset, id: assetId, sha256 }, alreadyStored: true };
  const press = (label: string) => act(() => [...container.querySelectorAll('button')].find((b) => b.textContent === label)!.click());

  async function openAndSolve(receiptRead: () => Response) {
    let reads = 0;
    const sent: Array<{ url: string; body: unknown }> = [];
    window.history.replaceState({}, '', `/?upload=${sha256}`);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === '/api/media/discover') return json({ items: [] });
        // The sample's readiness read as the app starts (sample-pill-one): not offered here
        if (url === '/api/samples') return json({ detail: 'not found' }, 404);
        if (url === `/api/ingest/${sha256}`) return ++reads === 1 ? json(stored) : receiptRead();
        if (url === `/api/ingest/${sha256}/solution`) return json(sky);
        if ((init?.method ?? 'GET') === 'POST') sent.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return json({ detail: 'not found' }, 404);
      })
    );
    await act(async () => {
      root.render(createElement(App));
    });
    await waitUntil(() => expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Line it up yourself')).toBe(true));
    press('Line it up yourself');
    press('Solve from here');
    return sent;
  }

  it('sends the solve with the person\'s alignment, from the stored receipt read again', async () => {
    const sent = await openAndSolve(() => json(stored));
    await waitUntil(() => expect(sent.map((s) => s.url)).toEqual([`/api/ingest/${sha256}/solve?wait=false`]));
    expect(sent[0]!.body).toMatchObject({ format: 'albedian.person-alignment', source: 'person' });
  });

  it('shows the failed job when the solve stops on anything unforeseen, never a screen that does not move', async () => {
    vi.mocked(solveStoredUpload).mockRejectedValueOnce(new Error('unforeseen'));
    await openAndSolve(() => json(stored));
    await waitUntil(() => expect(q('[data-testid="working-screen"] [role="alert"]')).not.toBeNull());
    expect(q('[data-testid="working-screen"]')!.textContent).toContain('The sky solve for your photo stopped before it finished.');
  });

  it('shows the failed job when the stored receipt cannot be read again, and sends nothing', async () => {
    const sent = await openAndSolve(() => json({ detail: 'no file with that hash is stored' }, 404));
    await waitUntil(() => expect(q('[data-testid="working-screen"] [role="alert"]')).not.toBeNull());
    expect(q('[data-testid="working-screen"]')!.textContent).toContain('The sky solve for your photo stopped before it finished.');
    expect(sent).toEqual([]);
  });
});
