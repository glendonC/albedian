// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MOTION_TOKENS, resolveMotionTokens } from '@albedian/design-system';
import type { CelestialFinding, ObservationSession, ObservationSolution, UploadJob } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { blockLeaveMs } from '../../hooks/useLeaving.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

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
      return [{ kind: 'catalog_stars', evidenceClass: 'simulated', label: 'Catalog stars', sources: [] }];
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

const { SpatialObservatoryViewport } = await import('../../components/SpatialObservatoryViewport.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const doneJob = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...restJob } = structuredClone(doneJob);
const stillStage = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
const RUNNING_JOB = { ...restJob, state: 'running', stages: [stillStage('decode'), stillStage('measure')] } as unknown as UploadJob;
const FAILED_JOB = { ...restJob, state: 'failed', stages: [stillStage('decode'), stillStage('measure')] } as unknown as UploadJob;
const REFUSED_JOB = {
  ...restJob,
  state: 'rejected',
  stages: [stillStage('decode'), stillStage('measure'), stillStage('restore'), stillStage('encode'), { stage: 'write', unit: 'bytes', done: 900, total: 900 }, stillStage('verify')],
} as unknown as UploadJob;

const W = 390;
const H = 844;

/**
 * Calibrated 390x520 photo slotted at phone width: its upright frame spans x = 0..390, y = 118..638 (1:1 CSS px),
 * so source vPx maps directly to viewport yPx = 118 + vPx.
 */
function boundarySession(selectedFindingId: string | null = null): ObservationSession {
  const base = poseSession(sample('plate_calibrated'));
  const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
  const makeStar = (id: string, title: string, xPx: number, yPx: number): CelestialFinding => ({
    ...template,
    id,
    title,
    sourceDetection: { ...template.sourceDetection!, xPx, yPx },
  });
  // yPx = 318 (< 469), 469 (exact card top boundary), 470 (just covered), 558 (deep under card)
  const findings: CelestialFinding[] = [
    makeStar('star-above', 'Rigel', 120, 199.5),
    makeStar('star-edge', 'Bellatrix', 220, 350.5),
    makeStar('star-just-under', 'Saiph', 140, 351.5),
    makeStar('star-deep', 'Mintaka', 250, 439.5),
  ];
  return {
    ...base,
    selectedFindingId,
    sourceAsset: {
      ...base.sourceAsset!,
      width: 390,
      height: 520,
      displayOrientation: { quarterTurnsClockwise: 0, mirrored: false },
    },
    cropTransform: {
      ...base.cropTransform,
      ...phoneSlotCrop(390, 520, W, H),
    },
    observationSolution: {
      ...base.observationSolution!,
      findings,
    } as ObservationSolution,
  } as ObservationSession;
}

function unmatchedWithObserved(selectedFindingId: string | null = null): ObservationSession {
  const base = poseSession(sample('horizon_predicted_only'));
  const predTemplate = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
  const starRef = POSE.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
  const moon = POSE.findings.find((f) => f.evidenceClass === 'observed')!;
  const obsGlare: CelestialFinding = { ...moon, id: 'finding-observed-glare-1', title: 'Bright glare' };
  const fomalhaut: CelestialFinding = {
    ...predTemplate,
    id: 'finding-predicted-star-hip-113368',
    title: 'Fomalhaut',
    skyPosition: { ...predTemplate.skyPosition!, altDeg: 20, azDeg: 150 },
    catalogRef: { ...starRef, apparentMagnitude: 1.16 },
  };
  return {
    ...base,
    selectedFindingId,
    sourceAsset: {
      ...base.sourceAsset!,
      width: 3024,
      height: 4032,
      displayOrientation: undefined,
      cameraMetadata: { focalLength35mmMm: 26 },
    },
    cropTransform: {
      ...base.cropTransform,
      ...phoneSlotCrop(3024, 4032, W, H),
    },
    observationSolution: {
      ...base.observationSolution!,
      findings: [obsGlare, fomalhaut],
    } as ObservationSolution,
  } as ObservationSession;
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const q = (s: string) => container.querySelector(s);

describe('phone-starcard-inert-1: occluded controls behind an open phone StarCard', () => {
  it('marks only .reveal-mark buttons with yPx > 469 inert (tabIndex -1) while phone StarCard is open, freeing them on card leave and at laptop width', () => {
    vi.useFakeTimers();
    try {
      const render = (session: ObservationSession, phone = true, vw = W, vh = H) =>
        act(() =>
          root.render(
            createElement(RevealPhoto, {
              session,
              onSelectFinding: () => {},
              onHome: () => {},
              viewportWidthPx: vw,
              viewportHeightPx: vh,
              phone,
              restoration: { phase: 'idle' },
              onRestore: () => {},
            }),
          ),
        );

      // 1. No card open: every mark is active and .restore-phone-rest is offered
      render(boundarySession(null));
      expect(q('.restore-phone-rest')).not.toBeNull();
      for (const id of ['star-above', 'star-edge', 'star-just-under', 'star-deep']) {
        const mark = q(`[data-testid="reveal-mark-${id}"]`) as HTMLButtonElement;
        expect(mark.hasAttribute('inert'), id).toBe(false);
        expect(mark.tabIndex, id).toBe(0);
      }

      // 2. Phone StarCard open: .restore-phone-rest unmounts; yPx 318 and 469 stay interactive; yPx 470 and 558 become inert + tabIndex -1
      render(boundarySession('star-above'));
      expect(q('.restore-phone-rest')).toBeNull();
      const above = q('[data-testid="reveal-mark-star-above"]') as HTMLButtonElement;
      const edge = q('[data-testid="reveal-mark-star-edge"]') as HTMLButtonElement;
      const justUnder = q('[data-testid="reveal-mark-star-just-under"]') as HTMLButtonElement;
      const deep = q('[data-testid="reveal-mark-star-deep"]') as HTMLButtonElement;
      expect(above.style.top).toBe('318px');
      expect(edge.style.top).toBe('469px');
      expect(justUnder.style.top).toBe('470px');
      expect(deep.style.top).toBe('558px');

      expect(above.hasAttribute('inert')).toBe(false);
      expect(above.tabIndex).toBe(0);
      expect(edge.hasAttribute('inert')).toBe(false);
      expect(edge.tabIndex).toBe(0);

      expect(justUnder.hasAttribute('inert')).toBe(true);
      expect(justUnder.tabIndex).toBe(-1);
      expect(deep.hasAttribute('inert')).toBe(true);
      expect(deep.tabIndex).toBe(-1);

      // StarCard itself is open and interactive
      const card = q('[data-testid="star-card"]') as HTMLElement;
      expect(card.hasAttribute('inert')).toBe(false);
      for (const btn of card.querySelectorAll('button')) {
        expect(btn.tabIndex).toBe(0);
      }

      // 3. As soon as StarCard begins leaving, covered marks become interactive again while StarCard turns inert
      const cardLeaveMs = Math.max(blockLeaveMs(7, resolveMotionTokens(false)), MOTION_TOKENS.sheetMs);
      render(boundarySession(null));
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="star-card"]')!.hasAttribute('inert')).toBe(true);
      expect((q('[data-testid="reveal-mark-star-just-under"]') as HTMLButtonElement).hasAttribute('inert')).toBe(false);
      expect((q('[data-testid="reveal-mark-star-just-under"]') as HTMLButtonElement).tabIndex).toBe(0);
      expect((q('[data-testid="reveal-mark-star-deep"]') as HTMLButtonElement).hasAttribute('inert')).toBe(false);
      expect((q('[data-testid="reveal-mark-star-deep"]') as HTMLButtonElement).tabIndex).toBe(0);
      act(() => vi.advanceTimersByTime(cardLeaveMs));

      // 4. Laptop width (phone: false): marks below y = 469 stay interactive when StarCard is open
      act(() => root.unmount());
      root = createRoot(container);
      const laptopBase = poseSession(sample('plate_calibrated'));
      const laptopTemplate = laptopBase.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
      const laptopSession: ObservationSession = {
        ...laptopBase,
        selectedFindingId: 'laptop-top',
        sourceAsset: {
          ...laptopBase.sourceAsset!,
          width: 1440,
          height: 900,
          displayOrientation: { quarterTurnsClockwise: 0, mirrored: false },
        },
        observationSolution: {
          ...laptopBase.observationSolution!,
          findings: [
            { ...laptopTemplate, id: 'laptop-top', title: 'Rigel', sourceDetection: { ...laptopTemplate.sourceDetection!, xPx: 600, yPx: 220 } },
            { ...laptopTemplate, id: 'laptop-low', title: 'Saiph', sourceDetection: { ...laptopTemplate.sourceDetection!, xPx: 650, yPx: 700 } },
          ],
        } as ObservationSolution,
      } as ObservationSession;
      render(laptopSession, false, 1440, 900);
      const laptopLow = q('[data-testid="reveal-mark-laptop-low"]') as HTMLButtonElement;
      expect(parseFloat(laptopLow.style.top)).toBeGreaterThan(469);
      expect(laptopLow.hasAttribute('inert')).toBe(false);
      expect(laptopLow.tabIndex).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it('marks RestorePhoneWait, RestorePhoneStopped, post-refusal .restore-phone-go, PhoneAlignRefused, and PhoneNoMatch inert while phone StarCard is open while keeping .reveal-phone-estimate.is-above-card un-inerted', () => {
    vi.useFakeTimers();
    try {
      const est = poseSession(POSE);
      const moon = est.observationSolution!.findings.find((f) => f.evidenceClass === 'observed')!;
      const renderPhone = (session: ObservationSession, extra: Record<string, unknown> = {}) =>
        act(() =>
          root.render(
            createElement(RevealPhoto, {
              session,
              onSelectFinding: () => {},
              onHome: () => {},
              viewportWidthPx: W,
              viewportHeightPx: H,
              phone: true,
              onRestore: () => {},
              onCancelRestore: () => {},
              onShowPredictedSky: () => {},
              onSolveFromHere: () => {},
              ...extra,
            } as unknown as Parameters<typeof RevealPhoto>[0]),
          ),
        );

      // 1. RestorePhoneWait: inert under open phone StarCard, active without card or while card leaves
      renderPhone({ ...est, selectedFindingId: moon.id }, { restoration: { phase: 'working', job: RUNNING_JOB } });
      expect(q('.restore-phone-wait')!.hasAttribute('inert')).toBe(true);
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(q('[data-testid="reveal-phone-estimate"]')!.hasAttribute('inert')).toBe(false);
      renderPhone({ ...est, selectedFindingId: null }, { restoration: { phase: 'working', job: RUNNING_JOB } });
      expect(q('[data-testid="star-card"]')!.hasAttribute('inert')).toBe(true);
      expect(q('.restore-phone-wait')!.hasAttribute('inert')).toBe(false);
      act(() => vi.advanceTimersByTime(1000));

      // 2. RestorePhoneStopped (retryable Try again): inert under open phone StarCard, active without card
      renderPhone(
        { ...est, selectedFindingId: moon.id },
        { restoration: { phase: 'stopped', job: FAILED_JOB, sentence: 'Stopped.', subline: null, canTryAgain: true } },
      );
      expect(q('.restore-phone-stopped')!.hasAttribute('inert')).toBe(true);
      renderPhone(
        { ...est, selectedFindingId: null },
        { restoration: { phase: 'stopped', job: FAILED_JOB, sentence: 'Stopped.', subline: null, canTryAgain: true } },
      );
      expect(q('.restore-phone-stopped')!.hasAttribute('inert')).toBe(false);
      act(() => vi.advanceTimersByTime(1000));

      // 3. Unmatched with keptRefused: PhoneAlignRefused (idle) and post-refusal .restore-phone-go (stopped refusal)
      const unmatchedPicked = unmatchedWithObserved('finding-observed-glare-1');
      const alignedFrom = {
        sha256: unmatchedPicked.sourceAsset!.sha256,
        pose: { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 3 / 4 },
        from: { kind: 'brightest' as const, name: 'Fomalhaut' },
      };
      renderPhone(unmatchedPicked, { alignedFrom, restoration: { phase: 'idle' } });
      expect(q('[data-testid="reveal-phone-scroll"]')!.hasAttribute('inert')).toBe(true);
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(q('[data-testid="reveal-phone-estimate"]')!.hasAttribute('inert')).toBe(false);
      renderPhone(unmatchedWithObserved(null), { alignedFrom, restoration: { phase: 'idle' } });
      expect(q('[data-testid="reveal-phone-scroll"]')!.hasAttribute('inert')).toBe(false);
      act(() => vi.advanceTimersByTime(1000));

      renderPhone(unmatchedPicked, {
        alignedFrom,
        restoration: { phase: 'stopped', job: REFUSED_JOB, sentence: 'Refused.', subline: UNCHANGED, canTryAgain: false },
      });
      expect(q('.restore-phone-stopped')!.hasAttribute('inert')).toBe(true);
      expect(q('.restore-phone-go')!.hasAttribute('inert')).toBe(true);
      renderPhone(unmatchedWithObserved(null), {
        alignedFrom,
        restoration: { phase: 'stopped', job: REFUSED_JOB, sentence: 'Refused.', subline: UNCHANGED, canTryAgain: false },
      });
      expect(q('.restore-phone-stopped')!.hasAttribute('inert')).toBe(false);
      expect(q('.restore-phone-go')!.hasAttribute('inert')).toBe(false);
      act(() => vi.advanceTimersByTime(1000));

      // 4. PhoneNoMatch (unmatched without keptRefused): inert under open observed StarCard, active without card
      renderPhone(unmatchedPicked, { restoration: { phase: 'idle' } });
      expect(q('[data-testid="reveal-phone-scroll"]')!.hasAttribute('inert')).toBe(true);
      renderPhone(unmatchedWithObserved(null), { restoration: { phase: 'idle' } });
      expect(q('[data-testid="reveal-phone-scroll"]')!.hasAttribute('inert')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('in SpatialObservatoryViewport on a phone clip, keeps .reveal-phone-top interactive while ClipPlayer, AskBar, and .restore-phone-rest are absent under open StarCard', async () => {
    const origInnerW = window.innerWidth;
    const origInnerH = window.innerHeight;
    const origMatchMedia = window.matchMedia;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: W });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: H });
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    const prevHref = window.location.href;
    window.history.replaceState({}, '', '/?view=observatory');
    try {
      const clipSession = poseSession(sample('plate_calibrated'), 'video');
      const star = clipSession.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
      const pickedClip: ObservationSession = { ...clipSession, selectedFindingId: star.id };
      const noop = () => {};
      await act(async () => {
        root.render(
          createElement(SpatialObservatoryViewport, {
            session: pickedClip,
            setSession: noop,
            selectedFinding: star,
            discoveredMedia: [],
            activeDiscoveredId: null,
            setActiveDiscoveredId: noop,
            activeDiscoveredItem: null,
            uploadedMediaUrl: 'blob:clip-test',
            setUploadedMediaUrl: noop,
            commandText: '',
            setCommandText: noop,
            onRunUtterance: noop,
            onSelectFinding: noop,
            onPushToTalk: noop,
          }),
        );
      });
      const topBar = q('.reveal-phone-top') as HTMLElement;
      expect(topBar).not.toBeNull();
      expect(topBar.hasAttribute('inert')).toBe(false);
      const closeTop = topBar.querySelector('button[aria-label="Close"]') as HTMLButtonElement;
      expect(closeTop.tabIndex).toBe(0);
      for (const pill of topBar.querySelectorAll<HTMLButtonElement>('.look-views__item:not(:disabled)')) {
        expect(pill.tabIndex).toBe(0);
      }
      expect(q('[data-testid="clip-player"]')).toBeNull();
      expect(q('[data-testid="ask-bar"]')).toBeNull();
      expect(q('.restore-phone-rest')).toBeNull();
      expect(q('[data-testid="star-card"]')!.hasAttribute('inert')).toBe(false);
    } finally {
      window.history.replaceState({}, '', prevHref);
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: origInnerW });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: origInnerH });
      window.matchMedia = origMatchMedia;
    }
  });
});
