// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import { clipPoseSolution, POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import type { AlignPose } from '../alignPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { unpressable } from './winningPointer.js';

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
function renderViewport(session: ObservationSession, extra: Record<string, unknown> = {}) {
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
}
function openViewport(session: ObservationSession, extra: Record<string, unknown> = {}) {
  renderViewport(session, { uploadedMediaUrl: null, ...extra });
  choosePhotoOnHome(container);
  renderViewport(session, extra);
}

describe('D16 phone clip reveal across calibrated, estimate, no-match, refused-align, card and answer states', () => {
  it('calibrated clip: player takes the counts slot with no PhoneCounts, Ask out loud at 772, Restore at 832, and StarCard hides both', () => {
    const calClip = poseSession(sample('plate_calibrated'), 'video');
    openViewport(calClip);
    expect(q('[data-testid="clip-player"]')!.className).toBe('clip-player is-phone');
    expect(q('[data-testid="reveal-legend"]')).toBeNull();
    expect(q('.reveal-phone-counts')).toBeNull();
    expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');
    expect(q('.restore-phone-rest button')!.textContent).toBe('Restore');
    expect(q('[data-testid="reveal-photo"]')!.className).toContain('is-scroll');
    expect(container.querySelectorAll('button.reveal-mark.is-catalog').length).toBeGreaterThan(0);
    expect(unpressable(container)).toEqual([]);

    const starId = calClip.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!.id;
    renderViewport({ ...calClip, selectedFindingId: starId } as ObservationSession);
    expect(q('[data-testid="star-card"]')!.className).toBe('reveal-card is-phone');
    expect(q('[data-testid="clip-player"]')).toBeNull();
    expect(q('[data-testid="ask-bar"]')).toBeNull();
    expect(q('.restore-phone-rest')).toBeNull();
  });

  it('pose-estimated clip: opens phone reveal with Predicted tag and pose sentence at y 638 over D19 fade, no PhoneCounts, and switches cleanly when playing', () => {
    let current = poseSession(clipPoseSolution(3, 5), 'video');
    const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
      current = update(current);
    };
    openViewport(current, { setSession });
    expect(q('[data-testid="clip-player"]')!.className).toBe('clip-player is-phone');
    expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');
    expect(q('.restore-phone-rest button')!.textContent).toBe('Restore');
    expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBeGreaterThan(0);

    const est = q('[data-testid="reveal-phone-estimate"]')!;
    expect(est.className).toBe('reveal-phone-estimate is-clip');
    expect([...est.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-phone-estimate__source']);
    expect(est.querySelector('.reveal-phone-counts')).toBeNull();

    // Playing steps predicted marks and estimate block aside, switching to PhoneNoMatch without colliding with AskBar
    const video = q('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    video.currentTime = 3;
    let paused = true;
    Object.defineProperty(video, 'paused', { configurable: true, get: () => paused });
    video.play = () => {
      paused = false;
      video.dispatchEvent(new Event('play'));
      return Promise.resolve();
    };
    video.pause = () => {
      paused = true;
      video.dispatchEvent(new Event('pause'));
    };
    act(() => (q('[data-testid="clip-player"] button[aria-label="Play"]') as HTMLButtonElement).click());
    expect(container.querySelectorAll('button.reveal-mark.is-predicted')).toHaveLength(0);
    expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
    expect(q('[data-testid="ask-bar"]')).toBeNull();
    expect(q('[data-testid="reveal-status"]')!.className).toBe('reveal-status is-unmatched is-phone is-clip');

    // Pausing back on the solved frame brings the estimate block, predicted marks, and Ask out loud back
    act(() => (q('[data-testid="clip-player"] button[aria-label="Pause"]') as HTMLButtonElement).click());
    renderViewport(current, { setSession });
    expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-clip');
    expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBeGreaterThan(0);
    expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');

    // Picking a predicted star keeps the estimate block drawn before the rising StarCard (item D) and hides player/AskBar
    const predStarId = current.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!.id;
    renderViewport({ ...current, selectedFindingId: predStarId } as ObservationSession, { setSession });
    const card = q('[data-testid="star-card"]')!;
    const keptEst = q('[data-testid="reveal-phone-estimate"]')!;
    expect(keptEst.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q('[data-testid="clip-player"]')).toBeNull();
    expect(q('[data-testid="ask-bar"]')).toBeNull();
  });

  it('no-match and refused-alignment clips: place words above y 638 with D19 fade, actions from y 772, and Restore at 772 when alone', () => {
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

    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: unmatchedClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'idle' },
          onRestore: noop,
          onShowPredictedSky: noop,
          onSolveFromHere: noop,
        })
      );
    });
    const noMatchStatus = q('[data-testid="reveal-status"]')!;
    expect(noMatchStatus.className).toBe('reveal-status is-unmatched is-phone is-clip');
    expect([...noMatchStatus.querySelectorAll('button')].map((b) => b.textContent)).toEqual([
      'Show predicted sky',
      'Line it up yourself',
      'Restore',
    ]);

    // Clip with no time or place (predictedSky false, alignOffered false): Restore stands alone at y 772
    const bareNoMatchClip = slottedClip({
      ...unmatchedClip,
      sourceAsset: { ...unmatchedClip.sourceAsset!, capturedAtUTC: undefined, observerLocation: undefined },
      observationSolution: { ...noMatchBase, status: 'failed', poseEstimate: undefined, findings: [] },
    } as unknown as ObservationSession);
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: bareNoMatchClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          restoration: { phase: 'idle' },
          onRestore: noop,
        })
      );
    });
    const restoreOnly = q('.reveal-phone-go--restore')!;
    expect(restoreOnly.previousElementSibling!.className).toBe('reveal-phone-lead');
    expect([...q('[data-testid="reveal-status"]')!.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Restore']);

    // PhoneAlignRefused on a clip: one rising block at y 638 with Predicted tag + two sentences, Line it up again at 772, Show predicted sky + Restore at 832
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
          restoration: { phase: 'idle' },
          onRestore: noop,
          onShowPredictedSky: noop,
          alignedFrom: { sha256: unmatchedClip.sourceAsset!.sha256, pose: keptPose, from: { kind: 'brightest', name: 'Rigel' } },
        })
      );
    });
    const refused = q('.reveal-status.is-refused')!;
    expect(refused.className).toBe('reveal-status is-unmatched is-phone is-refused is-clip has-predicted');
    const block = refused.querySelector('.align-refused__block')!;
    expect([...block.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-status__lead', 'align-refused__kept']);
    expect([...refused.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Line it up again', 'Show predicted sky', 'Restore']);

    // Cascaded padding on refused phone clip stays 0 (height 970px), never inflated to 1782px by .is-clip padding
    const cascaded = (el: Element): Record<string, string> => {
      const matched: Array<{ spec: number; order: number; prop: string; val: string }> = [];
      let order = 0;
      postcss.parse(css).walkRules((rule) => {
        if (rule.parent?.type !== 'root') return;
        for (const sel of rule.selectors) {
          if (!el.matches(sel)) continue;
          const classes = (sel.match(/\.[A-Za-z0-9_-]+/g) ?? []).length;
          rule.walkDecls((d) => {
            matched.push({ spec: classes, order: ++order, prop: d.prop, val: d.value.replace(/\s+/g, ' ') });
          });
        }
      });
      matched.sort((a, b) => a.spec - b.spec || a.order - b.order);
      return Object.fromEntries(matched.map((m) => [m.prop, m.val]));
    };
    expect(cascaded(refused)).toMatchObject({ height: '970px', padding: '0' });
    expect(cascaded(noMatchStatus)).toMatchObject({ padding: '772px 24px 40px' });
  });

  it('voice answer on a phone clip: anchors at y 638 without a tag row, or 16 px above .reveal-phone-estimate while keeping its tag in view (item B)', () => {
    const shownAnswer = { heard: 'What star is that?', sentence: 'Rigel is a blue supergiant.', answer: null };
    const calClip = slottedClip(poseSession(sample('plate_calibrated'), 'video'));
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: calClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          answer: shownAnswer,
        })
      );
    });
    expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${H - 638}px`);

    // On an estimate clip, .reveal-phone-estimate stays in view at y 638 and AnswerPanel stands 16 px above its top
    const estClip = slottedClip(poseSession(clipPoseSolution(3, 5), 'video'));
    const origBcr = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-phone-estimate') {
        return { left: 24, top: 580, right: 366, bottom: 638, width: 342, height: 58, x: 24, y: 580, toJSON: () => ({}) } as DOMRect;
      }
      return origBcr.call(this);
    });
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: estClip,
          onSelectFinding: noop,
          onHome: noop,
          phone: true,
          viewportWidthPx: W,
          viewportHeightPx: H,
          answer: shownAnswer,
        })
      );
    });
    expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-clip');
    expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${H - 580 + 16}px`);
  });

  it('pins the CSS rules for D16 phone clip estimate, no-match, and refused-alignment blocks with D19 darkening', () => {
    const d19 = declarationsOf('.reveal-answer.is-phone::before');
    expect(declarationsOf('.reveal-phone-estimate.is-clip')).toEqual({ bottom: 'max(206px, calc(100% - 638px))' });
    expect(declarationsOf('.reveal-phone-estimate.is-clip::before')).toEqual(d19);
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-clip:not(.is-refused)')).toEqual({ padding: '772px 24px 40px' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-clip .reveal-phone-lead')).toEqual({
      position: 'absolute',
      left: '24px',
      right: '24px',
      top: '638px',
      transform: 'translateY(-100%)',
      'min-height': '0',
    });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone.is-clip .reveal-phone-lead::before')).toEqual(d19);
    expect(declarationsOf('.reveal-phone-lead + .reveal-phone-go--restore')).toEqual({ 'margin-top': '0' });
    expect(declarationsOf('.align-refused__block')).toEqual({
      position: 'absolute',
      left: '24px',
      right: '24px',
      top: '638px',
      transform: 'translateY(-100%)',
      display: 'flex',
      'flex-direction': 'column',
      'align-items': 'flex-start',
    });
    expect(declarationsOf('.align-refused__block::before')).toEqual(d19);
    expect(declarationsOf('.align-refused__block > .look-tag')).toEqual({ margin: '0 0 12px' });
    expect(declarationsOf('.align-refused__block > .reveal-status__lead')).toEqual({ margin: '0 0 8px' });
    expect(declarationsOf('.align-refused__block > .align-refused__kept')).toEqual({ margin: '0' });
  });
});
