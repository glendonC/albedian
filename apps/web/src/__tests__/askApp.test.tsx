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
    other?: (url: string, init?: RequestInit) => Response | undefined;
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
      const other = routes.other?.(url, init);
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

/** Types the words into the approved field (the keyboard button turns the ask button into it) and sends them. */
async function typeAndSend(words: string) {
  if (!q('.ask-bar__field input')) {
    // jsdom has no speech recognition, so the ask button itself reads "Type to ask" and opens the field
    await act(async () => ((q('.ask-bar__type') ?? q('.ask-bar__ask')) as HTMLButtonElement).click());
  }
  const input = q('.ask-bar__field input') as HTMLInputElement;
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

  it('keeps a command with the app: nothing goes to the answer route, and one it carries out shows nothing in the answer place', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Select Rigel');
    expect(asked).toEqual([]);
    expect(calls.filter((call) => call.includes('/api/voice/answer/'))).toEqual([]);
    expect(q(ANSWER)).toBeNull();
    expect(q('[data-testid="star-card"] h1')!.textContent).toBe('Rigel');
  });

  it("shows a command it cannot carry out where answers show: the words as typed and the app's own sentence, nothing lit (command, decided)", async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Take me to Zorblax');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(asked).toEqual([]);
    expect(q('.reveal-answer__q')!.textContent).toBe('Take me to Zorblax');
    expect(q('.reveal-answer__s')!.textContent!.length).toBeGreaterThan(0);
    expect(q('.reveal-answer__st')).toBeNull();
    expect(q('.reveal-mark.is-picked')).toBeNull();
    // No old caption box or bar over the laptop reveal
    expect(q('[data-testid="voice-command-hud"]')).toBeNull();
    expect(q('[data-testid="ask-bar"]')).not.toBeNull();
  });

  it('runs the words as the app did before when the answer route does not answer', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, { answer: () => json({ detail: 'down' }, 503) });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    // The app's own refusal shows where answers show, with no statements (waiting for UIUX's words: QUESTION unit-1g-b)
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(q('.reveal-answer__q')!.textContent).toBe('Is that a plane?');
    expect(q('.reveal-answer__st')).toBeNull();
  });

  it('closes the answer when a command runs, and on Esc', async () => {
    await openAppAt(`/?upload=${sha256}`);
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    await typeAndSend('Select Rigel');
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
          : url === `/api/ingest/${other}/solve?wait=false`
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

  it("says why listening stopped where answers show, in the app's own sentence, when nothing was heard (the lead's answer 21:33)", async () => {
    class SilentRecognition {
      lang = '';
      interimResults = false;
      onresult = null;
      onerror: ((ev: { error?: string }) => void) | null = null;
      onend: (() => void) | null = null;
      onaudiostart: (() => void) | null = null;
      start() {
        setTimeout(() => {
          this.onaudiostart?.();
          this.onerror?.({ error: 'no-speech' });
          this.onend?.();
        }, 0);
      }
    }
    (window as unknown as Record<string, unknown>).SpeechRecognition = SilentRecognition;
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      expect(q('.ask-bar__ask')!.textContent).toBe('Ask out loud');
      await act(async () => (q('.ask-bar__ask') as HTMLButtonElement).click());
      await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
      expect(q('.reveal-answer__q')).toBeNull();
      expect(q('.reveal-answer__s')!.textContent).toBe('No speech was heard. Press push-to-talk and speak, or type the command below.');
      expect(q('.ask-bar__ask')!.className).not.toContain('is-listening');
    } finally {
      delete (window as unknown as Record<string, unknown>).SpeechRecognition;
    }
  });

  it('shows no refusal when the voice backend reads the words the app could not and carries them out', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, {
      other: (url, init) =>
        url === '/api/voice/command'
          ? json({
              status: 'proposed',
              actionId: 'backend-1',
              // The revision the app sent, as the backend echoes it
              proposedAction: { type: 'finding.select', requestId: 'backend-1', expectedViewRevision: JSON.parse(String(init?.body)).context.viewRevision, findingId: rigel.id },
              visualStatus: 'Selected Rigel.',
            })
          : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    // The view revision the backend's action must name, as the session stands
    await typeAndSend('Take me to Zorblax');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await settle();
    expect(q(ANSWER)).toBeNull();
    expect(q('[data-testid="star-card"] h1')!.textContent).toBe('Rigel');
  });

  it('asks back where answers show when the app cannot tell which star is meant', async () => {
    // Two stars measured equally bright on the same frame: "the brightest" names neither on its own
    const [a, b] = solution.findings.filter((f) => f.sourceDetection);
    const tied = {
      ...solution,
      findings: solution.findings.map((f) =>
        f.id === b!.id ? { ...f, sourceDetection: { ...f.sourceDetection!, flux: a!.sourceDetection!.flux, snr: a!.sourceDetection!.snr, frameTimeSec: a!.sourceDetection!.frameTimeSec } } : f
      ),
    };
    await openAppAt(`/?upload=${sha256}`, { solution: () => json(tied) });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Select the brightest star');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(q('.reveal-answer__q')!.textContent).toBe('Select the brightest star');
    expect(q('.reveal-answer__s')!.textContent).toContain('Which did you mean?');
  });

  it('shows the words in the Ask out loud button as they are heard, and its own label again once the capture ends', async () => {
    let rec: { onresult: ((ev: unknown) => void) | null; onend: (() => void) | null; onaudiostart: (() => void) | null } | null = null;
    class Hearing {
      lang = '';
      interimResults = false;
      onresult = null;
      onerror = null;
      onend = null;
      onaudiostart = null;
      constructor() {
        rec = this as never;
      }
      start() {}
    }
    (window as unknown as Record<string, unknown>).SpeechRecognition = Hearing;
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => (q('.ask-bar__ask') as HTMLButtonElement).click());
      await act(async () => rec!.onaudiostart?.());
      await act(async () => rec!.onresult?.({ results: [Object.assign([{ transcript: 'How far away is Rig' }], { isFinal: false })] }));
      expect(q('.ask-bar__ask')!.innerHTML).toContain('How far away is <i>Rig</i>');
      await act(async () => rec!.onend?.());
      expect(q('.ask-bar__ask')!.textContent).toBe('Ask out loud');
      expect(q('.ask-bar__ask')!.innerHTML).not.toContain('Rig');
    } finally {
      delete (window as unknown as Record<string, unknown>).SpeechRecognition;
    }
  });

  it('keeps a refusal given where the reveal is not shown from showing when the reveal comes back', async () => {
    // A phone's window over a sky lined up by an estimate, where the old bar asks until the phone's unit for it; then a
    // laptop's, where the reveal shows
    let phone = true;
    const listeners: Array<() => void> = [];
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      get matches() {
        return phone && query.includes('max-width: 720px');
      },
      addEventListener: (_: string, listener: () => void) => listeners.push(listener),
      removeEventListener: () => {},
    });
    const unmatched = { ...samples['horizon_approximate']!, id: solutionId, sourceAssetId: assetId, sourceSha256: sha256 } as ObservationSolution;
    try {
      await openAppAt(`/?upload=${sha256}`, { solution: () => json(unmatched) });
      await waitUntil(() => expect(q('[data-testid="voice-command-hud"]')).not.toBeNull());
      await act(async () => (q('button[aria-label="Type a question"]') as HTMLButtonElement).click());
      const input = q('[data-testid="voice-command-input"]') as HTMLInputElement;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Take me to Zorblax');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await act(async () => (input.form as HTMLFormElement).requestSubmit());
      await settle();
      phone = false;
      await act(async () => listeners.forEach((listener) => listener()));
      await waitUntil(() => expect(q('[data-testid="ask-bar"]')).not.toBeNull());
      await settle();
      expect(q('[data-testid="reveal-photo"]')).not.toBeNull();
      expect(q(ANSWER)).toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it("shows answers over the phone's reveal too, under the photo in the counts' place, from the compact Ask out loud", async () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query.includes('max-width: 720px'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('.reveal-phone-counts')).not.toBeNull());
      expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');
      await typeAndSend('How far away is Rigel?');
      await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
      expect(asked).toEqual([{ question: 'How far away is Rigel?' }]);
      expect(q(ANSWER)!.className).toBe('reveal-answer is-phone');
      expect(q('.reveal-phone-counts')).toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it('shows answers there after Go there too, in the readout\'s place', async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      await waitUntil(() => expect(q('[data-testid="flight-readout"]')).not.toBeNull());
      await typeAndSend('Is that a plane?');
      await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
      expect(q('[data-testid="flight-readout"]')).toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }
  });

  it('arrives at once, with no flight, when the person asks for less motion (REQ-DSA-2.1)', async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
      // One press, nothing awaited after it: with a flight, the first frame is still the photo turning into the sky
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      expect(q('[data-testid="flight-readout"]')).not.toBeNull();
      expect(q('[data-testid="flight-readout"]')!.textContent).toContain('1.0ly');
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it('does not offer "Fly on your own" while the flight is still flying', async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      // The second half of the flight: there, Back to Moment shown, the camera still travelling
      await waitUntil(() => expect(q('[data-testid="flight-readout"]')).not.toBeNull());
      expect(q('.ask-bar__fly')).toBeNull();
      await waitUntil(() => expect(q('.ask-bar__fly')).not.toBeNull());
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }
  });

  it('offers "Fly on your own" once arrived, and hands the camera to the person with no star ahead (pilot-1f)', async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      expect(q('.ask-bar__fly')).toBeNull();
      await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      await waitUntil(() => expect(q('.ask-bar__fly')).not.toBeNull());
      await act(async () => (q('.ask-bar__fly') as HTMLButtonElement).click());
      expect([...container.querySelectorAll('[data-testid="steer-line"]')].map((p) => p.textContent)).toContain('Press the sky: autopilot again');
      expect(q('[data-testid="flight-readout"]')!.textContent).not.toContain('to Rigel');
      expect(q('.ask-bar__fly')).toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it("shows answers over Real sky too, the photo's frame still the reveal's", async () => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('.reveal-nav [role="group"] button')].find((b) => b.textContent === 'Real sky')!).click());
      await waitUntil(() => expect(q('[data-testid="real-sky"]')).not.toBeNull());
      await typeAndSend('Is that a plane?');
      await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
      expect(asked).toEqual([{ question: 'Is that a plane?' }]);
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }
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
