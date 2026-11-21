// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution } from '@albedian/contracts';
import { HOME_SCREEN } from '../components/__tests__/homeEntry.js';
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
  it("leaves the home for the stored upload's photo and sky, with two reads and nothing sent", async () => {
    const calls = await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q(HOME_SCREEN)).toBeNull());
    const photo = q('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    // The stored original, from the server's own route for that hash
    expect(photo.getAttribute('src')).toBe(`/api/ingest/${sha256}/original`);
    expect(q('[data-testid="working-screen"]')).toBeNull();
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    expect(calls).toEqual([`GET /api/ingest/${sha256}`, `GET /api/ingest/${sha256}/solution`]);
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

  it('gives way to a photo chosen before the stored one arrived: the later answer changes nothing', async () => {
    let release: (response: Response) => void = () => {};
    const held = new Promise<Response>((resolve) => (release = resolve));
    await openAppAt(`/?upload=${sha256}`, { receipt: () => held });
    const input = q('[data-testid="home-file-input"]') as HTMLInputElement;
    const file = new File([new Uint8Array(4)], 'chosen.jpg', { type: 'image/jpeg' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(q('[data-testid="working-screen"] .work-size')!.textContent).toBe('0.000004 MB');
    release(json(receipt));
    await settle();
    // Still the chosen file's screen: no stored photo took its place
    expect(q('[data-testid="working-screen"]')).not.toBeNull();
    expect(q('[data-testid="source-photo-stage"] img')).toBeNull();
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
