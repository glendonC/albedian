// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution } from '@albedian/contracts';
import { HOME_SCREEN } from '../components/__tests__/homeEntry.js';

// Asking about the photo on the laptop reveal (ask, approved 2026-11-12): a question goes to the answer route of the
// upload it is about, and its answer or refusal takes the card's place; a command stays with the app.

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
async function openAppAt(
  address: string,
  routes: {
    receipt?: () => Response | Promise<Response>;
    solution?: () => Response | Promise<Response>;
    answer?: () => Response | Promise<Response>;
    other?: (url: string) => Response | undefined;
  } = {}
) {
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
      const other = routes.other?.(url);
      if (other) return other;
      if (url === `/api/voice/answer/${sha256}`) {
        asked.push(JSON.parse(String(init?.body)));
        return (routes.answer ?? (() => json(answerFor(JSON.parse(String(init?.body)).question))))();
      }
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


const asked: Array<{ question: string; selectedFindingId?: string }> = [];
afterEach(() => {
  asked.length = 0;
});
const rigelIndex = solution.findings.findIndex((f) => f.title === 'Rigel');
const rigel = solution.findings[rigelIndex]!;
/** The route's reply, as the real route words these two (services/voice, read from the lane's API on 2026-11-18). */
function answerFor(question: string) {
  if (question.startsWith('How far')) {
    return {
      heard: question,
      answer: {
        status: 'answered',
        question: 'distance',
        observationSolutionId: solutionId,
        sentence: 'Rigel is about 260 parsecs away, about 860 light years.',
        statements: [
          {
            subject: { kind: 'finding', findingId: rigel.id, evidenceClass: 'catalog_matched' },
            quantity: 'distance',
            value: { kind: 'number', amount: 264.55, unit: 'pc' },
            evidenceClass: 'catalog_matched',
            source: { kind: 'solution_field', path: ['findings', rigelIndex, 'catalogRef', 'bailerJonesDistancePc', 'median'] },
          },
        ],
      },
    };
  }
  return { heard: question, answer: { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence: "Nothing in this photo's data answers that." } };
}

/** Types the words into the bar the app has today and sends them. */
async function typeAndSend(words: string) {
  if (!q('[data-testid="voice-command-input"]')) {
    await act(async () => (q('button[aria-label="Type a question"]') as HTMLButtonElement).click());
  }
  const input = q('[data-testid="voice-command-input"]') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, words);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => {
    (input.form as HTMLFormElement).requestSubmit();
  });
  await settle();
}
const ANSWER = '[data-testid="reveal-answer"]';

describe('asking about the photo on the laptop reveal', () => {
  it("sends a question to the answer route of the photo's upload, and shows the answer in the card's place with its star lit", async () => {
    const calls = await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('How far away is Rigel?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(asked).toEqual([{ question: 'How far away is Rigel?' }]);
    expect(calls).toContain(`POST /api/voice/answer/${sha256}`);
    expect(q('.reveal-answer__q')!.textContent).toBe('How far away is Rigel?');
    expect(q('.reveal-answer__s')!.textContent).toBe('Rigel is about 260 parsecs away, about 860 light years.');
    expect(q(`[data-testid="reveal-mark-${rigel.id}"]`)!.className).toContain('is-picked');
  });

  it('shows a refusal in the same place, with nothing lit, and sends the picked star along', async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(asked).toEqual([{ question: 'Is that a plane?', selectedFindingId: rigel.id }]);
    expect(q('.reveal-answer__s')!.textContent).toBe("Nothing in this photo's data answers that.");
    expect(q('[data-testid="star-card"]')).toBeNull();
    expect(q('.reveal-mark.is-picked')).toBeNull();
  });

  it('keeps a command with the app: nothing goes to the answer route', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Back to moment');
    expect(asked).toEqual([]);
    expect(calls.filter((call) => call.includes('/api/voice/answer/'))).toEqual([]);
    expect(q(ANSWER)).toBeNull();
  });

  it('runs the words as the app did before when the answer route does not answer', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, { answer: () => json({ detail: 'down' }, 503) });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    expect(q(ANSWER)).toBeNull();
  });

  it('closes the answer when a command runs, and on Esc', async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    await typeAndSend('Take me to Zorblax');
    expect(q(ANSWER)).toBeNull();
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    await act(async () => {
      (document.activeElement as HTMLElement | null)?.blur();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(q(ANSWER)).toBeNull();
  });

  it('does not show an answer over another photo opened since', async () => {
    const other = '9b'.repeat(32);
    const otherAsset = `src_${other.slice(0, 16)}`;
    const otherSolutionId = `sol_${other.slice(0, 16)}`;
    const otherReceipt = { ...receipt, sourceAsset: { ...receipt.sourceAsset, id: otherAsset, sha256: other, uri: `/api/ingest/${other}/original` }, solveJobId: `job_${other.slice(0, 16)}` };
    const otherSolution = {
      ...solution,
      id: otherSolutionId,
      sourceAssetId: otherAsset,
      sourceSha256: other,
      findings: solution.findings.map((f) => ({
        ...f,
        observationSolutionId: otherSolutionId,
        sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId: otherAsset } : undefined,
        evidence: { ...f.evidence, sourceAssetSha256: other, wcsSolutionId: otherSolutionId },
      })),
    };
    const job = {
      id: `job_${other.slice(0, 16)}`,
      sourceAssetSha256: other,
      state: 'succeeded',
      createdAtUTC: '2026-01-19T05:31:00Z',
      startedAtUTC: '2026-01-19T05:31:01Z',
      finishedAtUTC: '2026-01-19T05:31:02Z',
      stages: [
        { stage: 'store', unit: 'bytes', done: 2_382_523, total: 2_382_523 },
        { stage: 'decode', unit: 'frames', done: 1, total: 1 },
        { stage: 'detect', unit: 'frames', done: 1, total: 1 },
        { stage: 'solve', unit: 'candidates', done: 48, total: 220 },
        { stage: 'verify', unit: 'steps', done: 1, total: 1 },
      ],
      solutionId: otherSolutionId,
    };
    await openAppAt(`/?upload=${sha256}`, {
      other: (url) =>
        url === '/api/ingest'
          ? json({ ...otherReceipt, alreadyStored: false }, 201)
          : url === `/api/ingest/${other}/solve`
            ? json(job, 201)
            : url === `/api/ingest/${other}/solution`
              ? json(otherSolution)
              : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    // Another photo chosen on the home, uploaded and solved in the same visit
    await act(async () => (q('[data-testid="reveal-home"]') as HTMLButtonElement).click());
    const file = new File([new Uint8Array(4)], 'other.jpg', { type: 'image/jpeg' });
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array(4).buffer });
    const input = q('[data-testid="home-file-input"]') as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await waitUntil(() => expect(q('[data-testid="working-screen"]')).toBeNull());
    expect(q(ANSWER)).toBeNull();
  });

  it('closes the answer when a star is picked, and shows that star\'s card', async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
    expect(q(ANSWER)).toBeNull();
    expect(q('[data-testid="star-card"]')).not.toBeNull();
  });
});
