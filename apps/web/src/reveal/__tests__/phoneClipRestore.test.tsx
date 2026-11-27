// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, UploadJob } from '@albedian/contracts';
import { DISCLOSURES } from '@albedian/contracts';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { clipPoseSolution, POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import type { AlignPose } from '../alignPose.js';
import { EARLY_SOURCE_WORDS } from '../earlyPose.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { CLIP_UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { unpressable, winningPointer } from './winningPointer.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    clearObserverCoordinates() {}
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
      return [];
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
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
  valid: { MediaVariant: Array<Record<string, unknown>> };
};
const doneJob = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...restJob } = structuredClone(doneJob);
const RUNNING_CLIP = {
  ...restJob,
  state: 'running',
  stages: [
    { stage: 'decode', unit: 'frames', done: 328, total: 328 },
    { stage: 'measure', unit: 'frames', done: 328, total: 328 },
    { stage: 'restore', unit: 'frames', done: 120, total: 328 },
  ],
} as unknown as UploadJob;
const QUEUED_CLIP = { ...restJob, state: 'queued', queueAhead: 2, stages: [] } as unknown as UploadJob;
const FAILED_CLIP = {
  ...restJob,
  state: 'failed',
  stages: [
    { stage: 'decode', unit: 'frames', done: 328, total: 328 },
    { stage: 'measure', unit: 'frames', done: 328, total: 328 },
    { stage: 'restore', unit: 'frames', done: 120, total: 328 },
  ],
} as unknown as UploadJob;
const REFUSED_CLIP = {
  ...restJob,
  state: 'rejected',
  stages: [
    { stage: 'decode', unit: 'frames', done: 328, total: 328 },
    { stage: 'measure', unit: 'frames', done: 328, total: 328 },
    { stage: 'restore', unit: 'frames', done: 328, total: 328 },
    { stage: 'encode', unit: 'frames', done: 328, total: 328 },
    { stage: 'write', unit: 'bytes', done: 900, total: 900 },
    { stage: 'verify', unit: 'frames', done: 328, total: 328 },
  ],
} as unknown as UploadJob;
const REFUSAL_REASON = 'The restoration changed how bright a star is, so it was not kept.';
const FAILURE_REASON = 'Albedian could not finish restoring your clip.';

const W = 390;
const H = 844;
const noop = () => {};

function slottedClip(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  return {
    ...session,
    cropTransform: { ...session.cropTransform, ...phoneSlotCrop(asset.width, asset.height, W, H, 'video') },
  } as ObservationSession;
}

function variantFor(sha: string, id: string, createdAtUTC: string, byteSize = 34_645_524) {
  const base = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
  return {
    ...base,
    id,
    sourceSha256: sha,
    sourceAssetId: `src_${sha.slice(0, 16)}`,
    createdAtUTC,
    byteSize,
    uri: `/api/media/${sha}/variants/${id}/file`,
  };
}

function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules, selector).toBe(1);
  return found;
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  Object.defineProperty(window, 'innerWidth', { value: W, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: H, configurable: true });
  (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
    matches: query.includes('max-width: 720px'),
    addEventListener: () => {},
    removeEventListener: () => {},
  });
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete (window as unknown as Record<string, unknown>).matchMedia;
  delete document.documentElement.dataset.phoneScroll;
  document.documentElement.style.removeProperty('--phone-scroll');
  vi.restoreAllMocks();
});

const q = (s: string) => container.querySelector(s);
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
const flush = async () => {
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};

describe('D34 and D38 phone clip restoration wait, compare, and stopped states', () => {
  it('wait while restoring a clip: keeps player at 676, places row at 590 and Cancel at 772, and anchors Predicted lead at y 576 when predicted rings draw', async () => {
    const estClip = slottedClip(poseSession(clipPoseSolution(3, 5), 'video'));
    const onCancel = vi.fn();
    const onFoot = vi.fn();
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: estClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'working', job: RUNNING_CLIP },
          onCancelRestore: onCancel,
          onPhoneFootChange: onFoot,
        })
      );
    });
    await flush();
    const wait = q('.restore-phone-wait')!;
    expect(wait.className).toBe('restore-phone-wait is-clip');
    expect(wait.querySelector('.work-phone-row > b')!.textContent).toBe('Restoring');
    expect(wait.querySelector('.work-phone-row__v')!.textContent).toBe('120 of 328');
    expect((wait.querySelector('.work-phone-row__bar b') as HTMLElement).style.width).toBe('36.585%');
    expect(onFoot).toHaveBeenLastCalledWith(true);

    const lead = q('.restore-phone-lead')!;
    expect(lead.className).toBe('restore-phone-lead is-clip');
    expect(lead.querySelector('[data-evidence-class="predicted"]')).not.toBeNull();
    expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();

    const cancelBtn = button('Cancel')!;
    expect(winningPointer(cancelBtn)).toBe('auto');
    act(() => cancelBtn.click());
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(cancelBtn.className).toContain('is-asked');

    // Queued state shows jobs ahead in .restore-phone-wait.is-clip
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: slottedClip(poseSession(sample('plate_calibrated'), 'video')),
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'working', job: QUEUED_CLIP },
          onCancelRestore: onCancel,
        })
      );
    });
    expect(q('.restore-phone-wait.is-clip .work-phone-row > b')!.textContent).toBe('Queued · 2 ahead');
    expect(q('.restore-phone-lead')).toBeNull();

    // 0-done gate checkpoint (jobs.py:519) keeps Checking mounted in .work-phone-row instead of dropping the row
    const gateZeroClip = {
      ...RUNNING_CLIP,
      stages: [
        { stage: 'decode', unit: 'frames', done: 328, total: 328 },
        { stage: 'measure', unit: 'frames', done: 8, total: 8 },
        { stage: 'restore', unit: 'frames', done: 328, total: 328 },
        { stage: 'encode', unit: 'frames', done: 328, total: 328 },
        { stage: 'write', unit: 'bytes', done: 900_000, total: 900_000 },
        { stage: 'verify', unit: 'frames', done: 0, total: 328 },
      ],
    } as unknown as UploadJob;
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: slottedClip(poseSession(sample('plate_calibrated'), 'video')),
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'working', job: gateZeroClip },
          onCancelRestore: onCancel,
        })
      );
    });
    expect(q('.restore-phone-wait.is-clip .work-phone-row > b')!.textContent).toBe('Checking');
    expect(q('.restore-phone-wait.is-clip .work-phone-row__v')!.textContent).toBe('0 of 328');
  });

  it('ready clip compare (crp-cmp): fits exact clip box, syncs muted video, portals 24 px labels above matched marks, filters predicted marks, and lists versions at 828', async () => {
    const calClip = poseSession(sample('plate_calibrated'), 'video');
    const sha = calClip.sourceAsset!.sha256;
    const vNew = variantFor(sha, 'var_0000000000000002', '2026-11-23T02:00:00Z');
    const vOld = variantFor(sha, 'var_0000000000000001', '2026-11-22T02:00:00Z', 30_000_000);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/variants')) {
        return new Response(JSON.stringify({ variants: [vNew, vOld], attempts: [] }), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    const renderVp = (session: ObservationSession, extra: Record<string, unknown> = {}) => {
      const selectedFinding = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId) ?? null;
      act(() => {
        root.render(
          createElement(SpatialObservatoryViewport, {
            session,
            setSession: noop,
            selectedFinding,
            discoveredMedia: [],
            activeDiscoveredId: null,
            setActiveDiscoveredId: noop,
            activeDiscoveredItem: null,
            uploadedMediaUrl: 'blob:clip',
            setUploadedMediaUrl: noop,
            commandText: '',
            setCommandText: noop,
            onRunUtterance: noop,
            onSelectFinding: noop,
            onPushToTalk: noop,
            ...extra,
          })
        );
      });
    };
    renderVp(calClip, { uploadedMediaUrl: null });
    choosePhotoOnHome(container);
    renderVp(calClip);
    await flush();

    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    expect(compare.className).toBe('restore-compare is-clip is-phone');
    expect(parseFloat(compare.style.left)).toBeCloseTo(0, 4);
    expect(parseFloat(compare.style.width)).toBeCloseTo(390, 4);
    expect(parseFloat(compare.style.height)).toBeCloseTo(219.375, 4);
    expect(parseFloat(compare.style.top)).toBeCloseTo(378 - 219.375 / 2, 4);
    expect(compare.querySelector('.restore-compare__original')).toBeNull();

    const parentVideo = q('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    const childVideo = compare.querySelector('.restore-compare__pic video') as HTMLVideoElement;
    expect(childVideo.muted).toBe(true);
    expect(childVideo.getAttribute('src')).toBe(`/api/media/${sha}/variants/${vNew.id}/file`);
    const childPlay = vi.spyOn(childVideo, 'play').mockResolvedValue(undefined);
    Object.defineProperty(parentVideo, 'paused', { value: false, configurable: true });
    act(() => {
      parentVideo.dispatchEvent(new Event('play'));
    });
    expect(childPlay).toHaveBeenCalled();
    Object.defineProperty(parentVideo, 'paused', { value: true, configurable: true });
    act(() => {
      parentVideo.dispatchEvent(new Event('pause'));
    });

    // Labels portalled after reveal-marks so they stand above matched marks, and glide split to 100% / 0%
    const marksEl = q('[data-testid="reveal-marks"]')!;
    const labelLayer = q('.restore-compare.is-labels.is-clip.is-phone')!;
    expect(marksEl.compareDocumentPosition(labelLayer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelectorAll('button.reveal-mark.is-catalog').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('button.reveal-mark.is-predicted')).toHaveLength(0);
    expect(unpressable(container)).toEqual([]);

    act(() => button('Original')!.click());
    expect(compare.style.getPropertyValue('--split')).toBe('100%');
    act(() => button('Restored')!.click());
    expect(compare.style.getPropertyValue('--split')).toBe('0%');

    // Disclosure note at 604, D16 player at 676, Download at 772 (AskBar hidden), RestoreVersions at 828; the note
    // stands from the restored clip's first frame (restored-disclosure-one)
    expect(q('.restore-phone-note')).toBeNull();
    act(() => void q('.restore-compare__pic video')!.dispatchEvent(new Event('loadeddata')));
    expect(q('.restore-phone-note')!.className).toBe('restore-phone-note is-clip');
    expect(q('.restore-phone-note span')!.textContent).toBe(DISCLOSURES.restoration);
    expect(q('[data-testid="clip-player"]')!.className).toBe('clip-player is-phone');
    expect(q('[data-testid="ask-bar"]')).toBeNull();
    expect(button('Download')).not.toBeUndefined();
    expect(q('.restore-phone-versions')).not.toBeNull();
    expect(q('[data-testid="reveal-photo"]')!.className).toContain('is-scroll');

    // Open answer hides Download and versions while keeping ClipPlayer; picked star card hides player, note, and Download
    renderVp(calClip, { answer: { heard: 'What star?', sentence: 'Rigel.', answer: null } });
    await flush();
    expect(button('Download')).toBeUndefined();
    expect(q('.restore-phone-note')).toBeNull();
    expect(q('[data-testid="clip-player"]')).not.toBeNull();

    const starId = calClip.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!.id;
    renderVp({ ...calClip, selectedFindingId: starId } as ObservationSession);
    await flush();
    expect(q('[data-testid="clip-player"]')).toBeNull();
    expect(button('Download')).toBeUndefined();
    expect(q('.restore-phone-note')).toBeNull();

    // Also opens immediately when restoration.phase === 'done' before versions fetch resolves, and filters predicted marks on an estimate clip
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
    const estClip = slottedClip(poseSession(clipPoseSolution(3, 5), 'video'));
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: estClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: {
            phase: 'done',
            job: doneJob,
            variantUrl: `/api/media/${estClip.sourceAsset!.sha256}/variants/${doneJob.variantId}/file`,
            orientation: { quarterTurnsClockwise: 0, mirrored: false },
          },
        })
      );
    });
    expect(q('[data-testid="restore-compare"]')!.className).toBe('restore-compare is-clip is-phone');
    expect(container.querySelectorAll('button.reveal-mark.is-predicted')).toHaveLength(0);
    expect(button('Download')).not.toBeUndefined();
  });

  it('stopped clip restoration: row at 590 without work-phone-fade, why at y 576 with CLIP_UNCHANGED on refusal, and returns Try again / Ask out loud / Show predicted sky / Line it up again at 772', () => {
    const calClip = slottedClip(poseSession(sample('plate_calibrated'), 'video'));
    const onRetry = vi.fn();
    const onFoot = vi.fn();

    // Retryable failure: Try again at 772, phoneFoot true, no .work-phone-fade over row at 590
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: calClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'stopped', job: FAILED_CLIP, sentence: FAILURE_REASON, subline: null, canTryAgain: true },
          onRestore: onRetry,
          onPhoneFootChange: onFoot,
        })
      );
    });
    const stopped = q('.restore-phone-stopped')!;
    expect(stopped.className).toBe('restore-phone-stopped is-clip');
    expect(stopped.querySelector('.work-phone-fade')).toBeNull();
    expect(stopped.querySelector('.work-phone-row.is-stopped > b')!.textContent).toBe('Restoring');
    expect(onFoot).toHaveBeenLastCalledWith(true);
    act(() => button('Try again')!.click());
    expect(onRetry).toHaveBeenCalledTimes(1);

    // Refusal on estimate clip: Predicted lead + reason + "Your clip is unchanged.", frees foot for Ask out loud
    const estClip = slottedClip(poseSession(clipPoseSolution(3, 5), 'video'));
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: estClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'stopped', job: REFUSED_CLIP, sentence: REFUSAL_REASON, subline: CLIP_UNCHANGED, canTryAgain: false },
          onPhoneFootChange: onFoot,
        })
      );
    });
    expect(onFoot).toHaveBeenLastCalledWith(false);
    expect(q('.restore-phone-stopped.is-clip .work-phone-row.is-stopped > b')!.textContent).toBe('Checking');
    expect(q('.restore-phone-stopped.is-clip .work-phone-row__v')!.textContent).toBe('328 of 328');
    expect(q('.restore-phone-why__lead [data-evidence-class="predicted"]')).not.toBeNull();
    expect([...q('.restore-phone-why')!.querySelectorAll(':scope > p')].map((p) => p.textContent)).toEqual([
      REFUSAL_REASON,
      'Your clip is unchanged.',
    ]);
    expect(button('Try again')).toBeUndefined();

    // Refusal on no-match clip: Show predicted sky returns at 772; over a kept refused alignment, Line it up again returns at 772 and reopens aligning
    const noMatchBase = sample('horizon_predicted_only');
    const unmatchedClip = slottedClip({
      ...poseSession({ ...noMatchBase, poseEstimate: undefined, findings: POSE.findings }, 'video'),
      sourceAsset: {
        ...poseSession(noMatchBase, 'video').sourceAsset!,
        capturedAtUTC: '2031-05-02T05:00:00Z',
        observerLocation: { latDeg: 35, lonDeg: -116 },
        cameraMetadata: { focalLength35mmMm: 26 },
      },
    } as ObservationSession);
    const onShowPred = vi.fn();
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: unmatchedClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'stopped', job: REFUSED_CLIP, sentence: REFUSAL_REASON, subline: CLIP_UNCHANGED, canTryAgain: false },
          onShowPredictedSky: onShowPred,
        })
      );
    });
    expect(q('.restore-phone-go button')!.textContent).toBe('Show predicted sky');

    const keptPose: AlignPose = { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 9 / 16 };
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: unmatchedClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'stopped', job: REFUSED_CLIP, sentence: REFUSAL_REASON, subline: CLIP_UNCHANGED, canTryAgain: false },
          onShowPredictedSky: onShowPred,
          onSolveFromHere: noop,
          alignedFrom: { sha256: unmatchedClip.sourceAsset!.sha256, pose: keptPose, from: { kind: 'brightest', name: 'Rigel' } },
        })
      );
    });
    expect(q('.restore-phone-why__lead .reveal-phone-estimate__source')!.textContent).toBe(EARLY_SOURCE_WORDS.alignment);
    const againBtn = q('.restore-phone-go button') as HTMLButtonElement;
    expect(againBtn.textContent).toBe('Line it up again');
    expect(winningPointer(againBtn)).toBe('auto');
    act(() => againBtn.click());
    expect(q('.restore-phone-stopped')).toBeNull();
    expect(button('Solve from here')).not.toBeUndefined();
  });

  it('pins the CSS rules for D34 and D38 phone clip wait, compare, and stopped restoration', () => {
    const d19 = declarationsOf('.reveal-answer.is-phone::before');
    expect(declarationsOf('.restore-compare__pic video')).toEqual({
      position: 'absolute',
      display: 'block',
      'object-fit': 'fill',
      'max-width': 'none',
    });
    expect(declarationsOf('.restore-phone-wait.is-clip .work-phone-row')).toEqual({ top: '590px' });
    expect(declarationsOf('.restore-phone-lead.is-clip')).toEqual({ bottom: 'calc(100% - 576px)' });
    expect(declarationsOf('.restore-compare.is-clip.is-phone .restore-compare__label')).toEqual({
      top: '10px',
      '--look-h': '24px',
      '--look-fs': '12px',
      padding: '0 12px',
    });
    expect(declarationsOf('.restore-compare.is-clip.is-phone .restore-compare__label.is-left')).toEqual({ left: '10px' });
    expect(declarationsOf('.restore-compare.is-clip.is-phone .restore-compare__label.is-right')).toEqual({ right: '10px' });
    expect(declarationsOf('.restore-phone-note.is-clip')).toEqual({ top: '604px' });
    expect(declarationsOf('.restore-phone-note.is-clip::before')).toEqual(d19);
    expect(declarationsOf('.restore-phone-stopped.is-clip .work-phone-row.is-stopped')).toEqual({ top: '590px' });
    expect(declarationsOf('.restore-phone-stopped.is-clip .restore-phone-why')).toEqual({ bottom: 'calc(100% - 576px)' });
  });
});
