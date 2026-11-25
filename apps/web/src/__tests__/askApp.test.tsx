// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IngestReceipt, ObservationSolution } from '@albedian/contracts';
import { HOME_SCREEN } from '../components/__tests__/homeEntry.js';
import { FLIGHT_MS } from '../hooks/useCameraFlightAnimation.js';

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
  // A field still leaving after the last send (its words out, 100 ms: the motion record) is not the one to type in
  if (q('.ask-bar__field.is-leaving')) await waitUntil(() => expect(q('.ask-bar__field')).toBeNull());
  // There after Go there the ask buttons are away until the camera rests (1500 ms after the press: the motion record).
  // As the there screen mounts they leave for 100 ms, still drawn but inert: only a control that is neither leaving nor
  // inert is one a person can use, so the wait is for that, however long the machine takes between two steps
  const usable = (selector: string) => [...container.querySelectorAll<HTMLElement>(selector)].find((el) => !el.closest('.is-leaving, [inert]')) ?? null;
  await waitUntil(() => expect(usable('.ask-bar__field input') ?? usable('.ask-bar__type') ?? usable('.ask-bar__ask')).not.toBeNull());
  if (!usable('.ask-bar__field input')) {
    // jsdom has no speech recognition, so the ask button itself reads "Type to ask" and opens the field
    await act(async () => (usable('.ask-bar__type') ?? usable('.ask-bar__ask'))!.click());
  }
  const input = usable('.ask-bar__field input') as HTMLInputElement;
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

  it('says the answer route gave no answer, and to ask again, when it does not answer (C11, decided by the lead)', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, { answer: () => json({ detail: 'down' }, 503) });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Is that a plane?');
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(q('.reveal-answer__q')!.textContent).toBe('Is that a plane?');
    expect(q('.reveal-answer__s')!.textContent).toBe('No answer from the Albedian server.');
    expect(q('.reveal-answer__sub')!.textContent).toBe('Ask again.');
    expect(q('.reveal-answer__st')).toBeNull();
    expect(calls).not.toContain('POST /api/voice/command');
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
      expect(q('.reveal-answer__s')!.textContent).toBe('No speech was heard.');
      expect(q('.reveal-answer__sub')!.textContent).toBe('Press Ask out loud and speak, or type your question.');
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
      expect(q('.ask-bar__ask')!.textContent).toBe('How far away is Rig');
      expect(q('.ask-bar__word.is-unsettled')!.textContent).toBe('Rig');
      await act(async () => rec!.onend?.());
      expect(q('.ask-bar__ask')!.textContent).toBe('Ask out loud');
      expect(q('.ask-bar__ask')!.innerHTML).not.toContain('Rig');
    } finally {
      delete (window as unknown as Record<string, unknown>).SpeechRecognition;
    }
  });

  it('answers and refuses on the predicted sky in its card\'s place, and keeps a refusal given before an upload opens from showing later', async () => {
    // refusal-on-predsky-one (UIUX-predsky-READY.md's addition for the lead's ask stamped 22:56): D33's predicted sky is
    // a view a person speaks to like the others, so a question there goes to the answer route and its answer, or a
    // refused command's sentence, stands in the card's place on the sky's card surface. Over ?view=observatory before an
    // upload opens (VoiceCommandHud, revealShown false) a refused command is still not stored to appear later
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    const noMatch = { ...samples['horizon_predicted_only']!, id: solutionId, sourceAssetId: assetId, sourceSha256: sha256 } as ObservationSolution;
    try {
      await openAppAt(`/?upload=${sha256}`, { solution: () => json(noMatch) });
      await waitUntil(() => expect(q('[data-testid="reveal-status"]')).not.toBeNull());
      const showSkyBtn = [...container.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === 'Show predicted sky')!;
      await act(async () => showSkyBtn.click());
      await waitUntil(() => expect(q('[data-testid="predicted-sky"]')).not.toBeNull());
      expect(q('[data-testid="reveal-photo"]')).toBeNull();
      const onSky = '[data-testid="predicted-sky"] > [data-testid="reveal-answer"].is-sky';
      await typeAndSend('Take me to Zorblax');
      await waitUntil(() => expect(q(onSky)).not.toBeNull());
      expect(q(`${onSky} .reveal-answer__q`)!.textContent).toBe('Take me to Zorblax');
      expect(q(`${onSky} .reveal-answer__s`)!.textContent).not.toBe('');
      expect(asked).toEqual([]);
      // A question goes to the answer route, whose answer takes the refusal's place
      await typeAndSend('Is that a plane?');
      await waitUntil(() => expect(q(`${onSky} .reveal-answer__s`)!.textContent).toBe("Nothing in this photo's data answers that."));
      expect(q(`${onSky} .reveal-answer__q`)!.textContent).toBe('Is that a plane?');
      expect(asked.map((a) => a.question)).toEqual(['Is that a plane?']);
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }

    act(() => root.unmount());
    root = createRoot(container);
    let resolveReceipt!: (res: Response) => void;
    const deferredReceipt = new Promise<Response>((resolve) => {
      resolveReceipt = resolve;
    });
    await openAppAt(`/?upload=${sha256}&view=observatory`, { receipt: () => deferredReceipt });
    await waitUntil(() => expect(q('[data-testid="voice-command-hud"]')).not.toBeNull());
    await act(async () => (q('button[aria-label="Type a question"]') as HTMLButtonElement).click());
    const input = q('[data-testid="voice-command-input"]') as HTMLInputElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Take me to Zorblax');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => (input.form as HTMLFormElement).requestSubmit());
    await settle();
    await act(async () => resolveReceipt(json(receipt)));
    await waitUntil(() => expect(q('[data-testid="ask-bar"]')).not.toBeNull());
    await settle();
    expect(q('[data-testid="reveal-photo"]')).not.toBeNull();
    expect(q(ANSWER)).toBeNull();
  });

  it('opens the phone reveal with Ask out loud over a pose-estimated sky too (d16phone-2)', async () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query.includes('max-width: 720px'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    const estimated = { ...samples['horizon_approximate']!, id: solutionId, sourceAssetId: assetId, sourceSha256: sha256 } as ObservationSolution;
    try {
      await openAppAt(`/?upload=${sha256}`, { solution: () => json(estimated) });
      await waitUntil(() => expect(q('[data-testid="reveal-phone-estimate"]')).not.toBeNull());
      expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');
      expect(q('[data-testid="voice-command-hud"]')).toBeNull();
      await typeAndSend('How far away is Rigel?');
      await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
      expect(q(ANSWER)!.className).toBe('reveal-answer is-phone');
      expect(q('[data-testid="reveal-phone-estimate"]')).not.toBeNull();
      expect(q('[data-testid="reveal-phone-estimate"] .reveal-phone-counts')).toBeNull();
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
      expect(q('[data-testid="flight-readout"]')!.textContent).toContain('1.00ly');
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
      // ask-flying-test-steady-one (LEAD ANSWER to WEB-UX 21:42 on 24 Nov): the flight's clock is held (its frames and
      // performance.now), so "still flying" is a state the test sets by moving the clock, never a race with a loaded
      // machine's wall clock; everything else keeps the real timers
      vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      // Three quarters through the flight (its travel, since flight-anim-one): there, the readout shown, still travelling
      await act(async () => void vi.advanceTimersByTime(FLIGHT_MS * 0.75));
      await settle();
      expect(q('[data-testid="flight-readout"]')).not.toBeNull();
      expect(q('.ask-bar__fly')).toBeNull();
      // Two frames short of the end it is still flying
      await act(async () => void vi.advanceTimersByTime(FLIGHT_MS * 0.25 - 40));
      await settle();
      expect(q('.ask-bar__fly')).toBeNull();
      // Arrived: offered
      await act(async () => void vi.advanceTimersByTime(80));
      await waitUntil(() => expect(q('.ask-bar__fly')).not.toBeNull());
    } finally {
      vi.useRealTimers();
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
      // It leaves on words out (motion-31), then is gone
      await waitUntil(() => expect(q('.ask-bar__fly')).toBeNull());
    } finally {
      delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it('on the phone, a drag on the flight\'s sky takes the steering and Autopilot gives it back (D30 item 6)', async () => {
    // Go there on the laptop (jsdom's phone slot shows no mark to pick), then the window narrows to a phone's
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    let phone = false;
    const listeners: Array<() => void> = [];
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      get matches() {
        return (phone && query.includes('max-width: 720px')) || query === '(prefers-reduced-motion: reduce)';
      },
      addEventListener: (_: string, listener: () => void) => listeners.push(listener),
      removeEventListener: () => {},
    });
    try {
      await openAppAt(`/?upload=${sha256}`);
      await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
      await act(async () => (q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement).click());
      await act(async () => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Go there')!).click());
      await waitUntil(() => expect(q('.ask-bar__fly')).not.toBeNull());
      phone = true;
      await act(async () => listeners.forEach((listener) => listener()));
      await waitUntil(() => expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone'));
      const autopilot = () => [...container.querySelectorAll('[data-testid="ask-bar"] button')].find((b) => b.textContent === 'Autopilot') ?? null;
      expect(autopilot()).toBeNull();
      const sky = q('[data-testid="flight-sky"]')!;
      await act(async () => {
        sky.dispatchEvent(new MouseEvent('pointerdown', { clientX: 200, clientY: 300 }));
        sky.dispatchEvent(new MouseEvent('pointermove', { clientX: 230, clientY: 310 }));
        sky.dispatchEvent(new MouseEvent('pointerup', { clientX: 230, clientY: 310 }));
      });
      expect(q('[data-testid="steer-line"]')!.textContent).toBe('Drag: turn');
      await act(async () => (autopilot() as HTMLButtonElement).click());
      expect(q('[data-testid="steer-line"]')).toBeNull();
      expect(autopilot()).toBeNull();
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

  it('shows plain C35 refusal on unmatched predicted_only upload when backend proposes an absent star (C37)', async () => {
    const noMatch = { ...samples['horizon_predicted_only']!, id: solutionId, sourceAssetId: assetId, sourceSha256: sha256 } as ObservationSolution;
    const calls = await openAppAt(`/?upload=${sha256}`, {
      solution: () => json(noMatch),
      other: (url, init) =>
        url === '/api/voice/command'
          ? json({
              status: 'proposed',
              actionId: 'req-sirius',
              proposedAction: {
                type: 'finding.describe',
                requestId: 'req-sirius',
                expectedViewRevision: JSON.parse(String(init?.body)).context.viewRevision,
                findingId: 'finding-predicted-star-sirius',
                fields: ['distance', 'type', 'motion', 'evidence'],
              },
              visualStatus: 'Described Sirius.',
            })
          : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="reveal-status"]')).not.toBeNull());
    await typeAndSend('where is Sirius?');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(q('.reveal-answer__q')!.textContent).toBe('where is Sirius?');
    expect(q('.reveal-answer__s')!.textContent).toBe("Sirius was not found in this photo's sky.");
    expect(q('.reveal-answer__s')!.textContent).not.toContain('finding-predicted-star-sirius');
    expect(q('.reveal-answer__s')!.textContent).not.toContain('Cannot describe unknown finding');
  });

  it('keeps local ambiguous_target refusal when backend also rejects as ambiguous_target, while showing C37 or non-ambiguous reducer refusals otherwise', async () => {
    let mode: 'unknown_finding' | 'no_distance' = 'unknown_finding';
    const observedOnly = solution.findings.find((f) => f.evidenceClass === 'observed')!;
    const calls = await openAppAt(`/?upload=${sha256}`, {
      other: (url, init) => {
        if (url !== '/api/voice/command') return undefined;
        const expectedViewRevision = JSON.parse(String(init?.body)).context.viewRevision;
        if (mode === 'unknown_finding') {
          return json({
            status: 'proposed',
            actionId: 'req-unknown',
            proposedAction: {
              type: 'finding.select',
              requestId: 'req-unknown',
              expectedViewRevision,
              findingId: 'finding-made-up',
            },
            visualStatus: 'Selected unknown.',
          });
        }
        return json({
          status: 'proposed',
          actionId: 'req-nodist',
          proposedAction: {
            type: 'space.enter',
            requestId: 'req-nodist',
            expectedViewRevision,
            findingId: observedOnly.id,
            distancePolicy: 'measured_or_estimated',
          },
          visualStatus: 'Entering space.',
        });
      },
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('Take me to Zorblax');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q(ANSWER)).not.toBeNull());
    expect(q('.reveal-answer__s')!.textContent).toBe('I did not understand "zorblax": no list of the app holds that word.');
    expect(q('.reveal-answer__s')!.textContent).not.toContain('finding-made-up');

    // When the backend resolves a real finding that fails space.enter on distance, keep the reducer's no_distance_estimate explanation
    mode = 'no_distance';
    calls.length = 0;
    await typeAndSend('Take me to Zorblax');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q('.reveal-answer__s')?.textContent).toContain('distance'));
  });

  it('uses C37 reducer wording when local reason is no_grounded_answer and keeps local ambiguous_target wording on HUD status banner', async () => {
    let targetId = 'finding-made-up';
    const calls = await openAppAt('/?view=observatory', {
      other: (url, init) =>
        url === '/api/voice/command'
          ? json({
              status: 'proposed',
              actionId: 'req-hud',
              proposedAction: {
                type: 'finding.describe',
                requestId: 'req-hud',
                expectedViewRevision: JSON.parse(String(init?.body)).context.viewRevision,
                findingId: targetId,
                fields: ['distance'],
              },
              visualStatus: 'Described target.',
            })
          : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="voice-command-hud"]')).not.toBeNull());
    const sendOnHud = async (words: string) => {
      await act(async () => (q('button[aria-label="Type a question"]') as HTMLButtonElement).click());
      const input = q('[data-testid="voice-command-input"]') as HTMLInputElement;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, words);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await act(async () => (input.form as HTMLFormElement).requestSubmit());
    };

    // localReason === 'ambiguous_target' + backend ambiguous_target -> keeps local explanation on HUD
    await sendOnHud('Take me to Zorblax');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q('[data-testid="voice-status-banner"]')?.textContent).toContain('I did not understand "zorblax": no list of the app holds that word.'));

    // localReason === 'no_grounded_answer' + backend ambiguous_target -> shows C37 reducer explanation (C33 on empty pending session)
    calls.length = 0;
    targetId = 'finding-predicted-star-sirius';
    await sendOnHud('Is that a plane?');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q('[data-testid="voice-status-banner"]')?.textContent).toContain('The sky in this photo has not been worked out, so nothing in it can be found by name.'));
    expect(q('[data-testid="voice-status-banner"]')!.textContent).not.toContain('finding-predicted-star-sirius');
  });
});

// typed-refusal-kept-one (DECIDED-BY-LEAD 24 Nov 22:12, FIELD's break): the app parser's typed refusal is kept whenever
// the backend's reply is itself a refusal or a question; the backend's words replace the app's only when the backend
// proposes something. Over real solves of public corpus photos (tests/fixtures/rp_b_01_voice_solve.json, calibrated;
// rp_b_06_voice_solve.json, predicted only, drawing no southern showpiece), with the backend's replies as the real route
// words them (services/voice, rules parser, read from POST /api/voice/command on this branch)
describe('a typed refusal is kept when the backend refuses too', () => {
  const realSolve = (name: string): ObservationSolution => {
    const solve = JSON.parse(fs.readFileSync(path.resolve(here, `../../../../tests/fixtures/${name}`), 'utf8')) as ObservationSolution;
    return {
      ...solve,
      id: solutionId,
      sourceAssetId: assetId,
      sourceSha256: sha256,
      findings: solve.findings.map((f) => ({
        ...f,
        observationSolutionId: solutionId,
        sourceDetection: f.sourceDetection ? { ...f.sourceDetection, assetId } : undefined,
        evidence: {
          ...f.evidence,
          ...(f.evidence.sourceAssetSha256 ? { sourceAssetSha256: sha256 } : {}),
          ...(f.evidence.wcsSolutionId ? { wcsSolutionId: solutionId } : {}),
        },
      })),
    } as ObservationSolution;
  };
  const notUnderstood = (said: string) => ({
    status: 'rejected',
    actionId: 'req-backend',
    reason: 'ambiguous_target',
    explanation: `I did not understand "${said}": no list of the app holds that word.`,
    nextActions: ['Name a catalog star or planet, or point at one'],
    refusal: { kind: 'not_understood', said },
    interpretation: { parser: 'rules' },
  });
  const coalsackOutOfReach = {
    status: 'rejected',
    actionId: 'req-backend',
    reason: 'no_grounded_answer',
    explanation: "The Coalsack is in the app's deep-sky catalog (OpenNGC C099), but voice does not reach deep-sky objects yet, so it cannot be selected, described or flown to.",
    nextActions: [],
    refusal: { kind: 'deep_sky_out_of_reach', said: 'coalsack', name: 'the Coalsack', catalogRow: 'C099' },
    interpretation: { parser: 'rules' },
  };
  const said = () => q('.reveal-answer__s')?.textContent;

  it.each([
    ['where is Mercury', 'mercury', 'Mercury'],
    ['where is Neptune', 'neptune', 'Neptune'],
  ])('%s over a calibrated photo: the app parser\'s sentence, not the backend\'s not understood', async (words, spoken, name) => {
    const calls = await openAppAt(`/?upload=${sha256}`, {
      solution: () => json(realSolve('rp_b_01_voice_solve.json')),
      other: (url) => (url === '/api/voice/command' ? json(notUnderstood(spoken)) : undefined),
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend(words);
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await settle();
    await waitUntil(() => expect(said()).toBe(`The app does not predict where ${name} is, so it cannot be found in this photo's sky.`));
    expect(said()).not.toContain('did not understand');
  });

  it('an undrawn showpiece over a predicted-only photo: not in this sky, not the backend\'s out of reach', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, {
      solution: () => json(realSolve('rp_b_06_voice_solve.json')),
      other: (url) => (url === '/api/voice/command' ? json(coalsackOutOfReach) : undefined),
    });
    await waitUntil(() => expect(q('[data-testid="reveal-status"]')).not.toBeNull());
    await typeAndSend('where is the Coalsack');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await settle();
    await waitUntil(() => expect(said()).toBe("The Coalsack was not found in this photo's sky."));
    expect(said()).not.toContain('voice does not reach');
  });

  it('a question from the backend is no proposal either: the typed refusal stays', async () => {
    const calls = await openAppAt(`/?upload=${sha256}`, {
      solution: () => json(realSolve('rp_b_01_voice_solve.json')),
      other: (url) =>
        url === '/api/voice/command'
          ? json({ status: 'needs_input', actionId: 'req-backend', question: 'Which planet do you mean?', choices: [], interpretation: { parser: 'rules' } })
          : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('where is Mercury');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await settle();
    await waitUntil(() => expect(said()).toBe("The app does not predict where Mercury is, so it cannot be found in this photo's sky."));
  });

  it('a backend proposal still wins: it is applied through the reducer, and no refusal shows', async () => {
    const solve = realSolve('rp_b_01_voice_solve.json');
    const star = solve.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const calls = await openAppAt(`/?upload=${sha256}`, {
      solution: () => json(solve),
      other: (url, init) =>
        url === '/api/voice/command'
          ? json({
              status: 'proposed',
              actionId: 'req-backend',
              proposedAction: {
                type: 'finding.select',
                requestId: 'req-backend',
                expectedViewRevision: JSON.parse(String(init?.body)).context.viewRevision,
                findingId: star.id,
              },
              visualStatus: 'Checked finding.select; not applied',
              interpretation: { parser: 'rules' },
            })
          : undefined,
    });
    await waitUntil(() => expect(q('[data-testid="reveal-marks"]')).not.toBeNull());
    await typeAndSend('select zorblax');
    await waitUntil(() => expect(calls).toContain('POST /api/voice/command'));
    await waitUntil(() => expect(q('[data-testid="star-card"]')).not.toBeNull());
    expect(q(ANSWER)).toBeNull();
  });
});
