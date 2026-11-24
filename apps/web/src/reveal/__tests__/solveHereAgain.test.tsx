// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSolution, SolveJob } from '@albedian/contracts';
import { poseReceipt, sample } from '../../components/__tests__/poseSessions.js';

// The whole app through D24's Solve from here (UIUX-align-READY.md step 3) when that solve stops: Try again is the
// same solve (the working screen's rule, unit 1c), so it sends the person's alignment again, and a file chosen after
// it makes the aligned solve's late answers change nothing and gives Try again back to the new file's own solve.

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

// The no-match fixture with three predicted targets above the horizon, as the align screen's tests draw it
const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const starRef = sample('pose_moon_level_head').findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...starRef, apparentMagnitude: mag } }) as CelestialFinding;
const solution: ObservationSolution = {
  ...base,
  findings: [
    ...base.findings.filter((f) => f.evidenceClass !== 'predicted'),
    target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16),
    target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38),
  ],
};
const receipt = poseReceipt(solution, 'image');
const sha256 = receipt.sourceAsset.sha256;
const job = (state: SolveJob['state']): SolveJob => ({
  id: `job_${sha256.slice(0, 16)}`,
  sourceAssetSha256: sha256,
  state,
  createdAtUTC: '2026-01-19T05:31:00Z',
  startedAtUTC: '2026-01-19T05:31:01Z',
  finishedAtUTC: '2026-01-19T05:31:02Z',
  stages: [
    { stage: 'store', unit: 'bytes', done: 4, total: 4 },
    { stage: 'decode', unit: 'frames', done: 1, total: 1 },
    { stage: 'detect', unit: 'frames', done: 1, total: 1 },
    { stage: 'solve', unit: 'candidates', done: 48, total: 220 },
    { stage: 'verify', unit: 'steps', done: 1, total: 1 },
  ],
  ...(state === 'succeeded' ? { solutionId: solution.id } : { error: 'The solve stopped on an internal error.' }),
});

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  let made = 0;
  Object.defineProperty(URL, 'createObjectURL', { value: () => `blob:again-${++made}`, configurable: true, writable: true });
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
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
function held() {
  let release: (response: Response) => void = () => {};
  const answer = new Promise<Response>((resolve) => {
    release = resolve;
  });
  return { answer, release };
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

/** The app on a server answering each route from its queue in order; every solve start is kept with its body. */
async function renderApp(routes: { ingest: Array<() => Promise<Response>>; solve: Array<() => Promise<Response>>; solution: Array<() => Promise<Response>> }) {
  const starts: Array<string | null> = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/media/discover') return json({ items: [] });
      if (url.endsWith('/variants')) return json({ variants: [] });
      if (url.endsWith('/solve?wait=false')) starts.push(typeof init?.body === 'string' ? init.body : null);
      const queue = url === '/api/ingest' ? routes.ingest : url.endsWith('/solve?wait=false') ? routes.solve : url.endsWith('/solution') ? routes.solution : undefined;
      const next = queue?.shift();
      if (!next) throw new Error(`no answer planned for ${init?.method ?? 'GET'} ${url}`);
      return next();
    })
  );
  await act(async () => {
    root.render(createElement(App));
  });
  return starts;
}
function chooseFile(selector: string, name: string) {
  const file = new File([new Uint8Array(4)], name, { type: 'image/png' });
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(4).buffer });
  const input = container.querySelector<HTMLInputElement>(selector)!;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
/** Uploaded, solved with no star matched, lined up from the brightest star and sent from there. */
async function solveFromHere() {
  chooseFile('[data-testid="home-file-input"]', 'night.png');
  await waitUntil(() => expect(button('Line it up yourself')).toBeDefined());
  act(() => button('Line it up yourself')!.click());
  await waitUntil(() => expect(button('Solve from here')).toBeDefined());
  act(() => button('Solve from here')!.click());
}

describe('Try again after a Solve from here that stopped', () => {
  it("sends the person's alignment again, the same solve, and its answer reaches the screen", async () => {
    const again = held();
    const starts = await renderApp({
      ingest: [async () => json(receipt, 201)],
      solve: [async () => json(job('succeeded'), 201), async () => json({ detail: 'stop here' }, 500), () => again.answer],
      solution: [async () => json(solution), async () => json(solution)],
    });
    await solveFromHere();
    await waitUntil(() => expect(q(`${WORKING} .work-why p`)?.textContent).toBe('The sky solve for your photo stopped before it finished.'));
    const sent = starts[1]!;
    expect(JSON.parse(sent)).toMatchObject({ format: 'albedian.person-alignment', source: 'person' });

    act(() => (q(`${WORKING} button.look-main`) as HTMLButtonElement).click());
    // Asked again: the same alignment, word for word, and the sent state while it is answered
    expect(starts).toEqual([null, sent, sent]);
    expect(q(`${WORKING} .work-why p`)).toBeNull();
    again.release(json(job('succeeded'), 201));
    await waitUntil(() => expect(q(WORKING)).toBeNull());
    expect(button('Line it up again')).toBeDefined();
  });

  it("gives Try again back to a file chosen after it, and the aligned solve's late answer changes nothing", async () => {
    const late = held();
    const starts = await renderApp({
      ingest: [async () => json(receipt, 201), async () => json({ ...receipt, alreadyStored: true }, 200)],
      solve: [async () => json(job('succeeded'), 201), () => late.answer, async () => json(job('failed'), 201), async () => json(job('succeeded'), 201)],
      solution: [async () => json(solution)],
    });
    await solveFromHere();
    await waitUntil(() => expect(q(WORKING)).not.toBeNull());
    // Another file from the home, reached by the logo, while the aligned solve has not answered
    act(() => button('albedian')!.click());
    await waitUntil(() => expect(q('[data-testid="home-file-input"]')).not.toBeNull());
    chooseFile('[data-testid="home-file-input"]', 'other.png');
    await waitUntil(() => expect(q(`${WORKING} .work-why p`)?.textContent).toBe('The solve stopped on an internal error.'));
    // Its answer, a stop in other words, reaches neither the screen nor the session
    late.release(json({ detail: 'stop here' }, 500));
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(q(`${WORKING} .work-why p`)?.textContent).toBe('The solve stopped on an internal error.');

    act(() => (q(`${WORKING} button.look-main`) as HTMLButtonElement).click());
    // The new file's own solve, with no alignment
    expect(starts.slice(2)).toEqual([null, null]);
  });
});
