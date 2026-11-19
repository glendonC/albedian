// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import type { CameraSkyPose } from '../../spatial/cosmosRenderer.js';
import { createEmptyObservationSession, dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The working screen in the app (unit 1c): while an upload is on its way to a solved sky the window shows the photo,
// bare, and the approved rows, and none of the pieces that come after the solve.

// The Real sky layer draws with WebGL 2, which jsdom has not: a stand-in reports what each test says it drew
const sky = vi.hoisted(() => ({ drawn: true }));
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => {
        props.onDrawn?.(sky.drawn);
      }, [props.onDrawn]);
      return h('div', { className: 'real-sky', 'data-testid': 'real-sky', 'data-state': sky.drawn ? 'drawn' : 'loading' });
    },
  };
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

vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { SpatialObservatoryViewport } = await import('../../components/SpatialObservatoryViewport.js');
const { poseSession, sample } = await import('../../components/__tests__/poseSessions.js');
const { formatSkyPlace, formatSkyTime, isPredictedSkyShown, predictedSkyWords } = await import('../predictedSky.js');
const postcss = (await import('postcss')).default;

// D33 (UIUX-predsky-READY.md, decided by the lead 12:45): the sky Show predicted sky opens, on the laptop, in the
// There screen's frame with its own status column, and none of the old panel's lines.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const noop = () => {};
const q = (selector: string) => container.querySelector(selector);

/** The predicted-only sample opened into its predicted sky, as Show predicted sky does. */
function predictedSky(mediaKind: 'image' | 'video' = 'image'): ObservationSession {
  const session = poseSession(sample('horizon_predicted_only'), mediaKind);
  return dispatchAlbedianAction(session, {
    type: 'sky.show',
    requestId: 'test-sky-show',
    expectedViewRevision: session.viewRevision,
    mode: 'predicted',
    observationSolutionId: session.observationSolution!.id,
  }).session;
}
function renderViewport(session: ObservationSession, extra: Record<string, unknown> = {}) {
  act(() => {
    root.render(
      createElement(SpatialObservatoryViewport, {
        session, setSession: noop, selectedFinding: null, discoveredMedia: [], activeDiscoveredId: null, setActiveDiscoveredId: noop,
        activeDiscoveredItem: null, uploadedMediaUrl: 'blob:photo', setUploadedMediaUrl: noop, commandText: '', setCommandText: noop,
        onRunUtterance: noop, onSelectFinding: noop, onPushToTalk: noop, ...extra,
      })
    );
  });
}

describe("the predicted sky's words", () => {
  it('is shown for the sky view of a recording whose stars were not matched, and not for a matched one or the photo', () => {
    expect(isPredictedSkyShown(predictedSky())).toBe(true);
    expect(isPredictedSkyShown(poseSession(sample('horizon_predicted_only')))).toBe(false);
    const calibrated = poseSession(sample('plate_calibrated'));
    expect(isPredictedSkyShown({ ...calibrated, spatialView: { ...calibrated.spatialView!, mode: 'registered_sky' } } as ObservationSession)).toBe(false);
    expect(isPredictedSkyShown(createEmptyObservationSession())).toBe(false);
  });

  it("says the photo's heading, the disclosure with its new sentence, then the time, the place and the catalogues", () => {
    expect(predictedSkyWords(predictedSky())).toEqual({
      heading: "The sky predicted for your photo's time and place.",
      note: 'Drawn or computed by the app; not measured in the source recording. Not lined up with your photo.',
      facts: ['2031-08-30 03:01 UTC', '30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3'],
    });
  });

  it("says your clip's for a clip", () => {
    const words = predictedSkyWords(predictedSky('video'));
    expect(words.heading).toBe("The sky predicted for your clip's time and place.");
    expect(words.note).toBe('Drawn or computed by the app; not measured in the source recording. Not lined up with your clip.');
  });

  it("reads the time and place the solve predicted the sky for, so a time or place lent by the iPhone's record shows (13:04)", () => {
    const session = predictedSky();
    const lent = { ...session.observationSolution!, observedAtUTC: '2026-11-19T09:15:30Z', observerLocation: { latDeg: -12.5, lonDeg: 130.75 } };
    expect(predictedSkyWords({ ...session, observationSolution: lent }).facts).toEqual(['2026-11-19 09:15 UTC', '12.50° S · 130.75° E', 'Hipparcos 2007 · Gaia DR3']);
    // The file's own capture time and place are not what the screen names
    expect(session.sourceAsset!.capturedAtUTC).toBe('2030-01-01T02:00:00Z');
  });

  it('writes the time to the minute it falls in and the place to two decimals, with its hemisphere', () => {
    expect(formatSkyTime(Date.UTC(2025, 6, 24, 5, 32, 59))).toBe('2025-07-24 05:32 UTC');
    expect(formatSkyPlace(32.5349, -95.6312)).toBe('32.53° N · 95.63° W');
    expect(formatSkyPlace(-33.9, 151.2)).toBe('33.90° S · 151.20° E');
  });

  it('names no time or place the solution does not give', () => {
    const session = predictedSky();
    const { observedAtUTC: _t, observerLocation: _p, ...solution } = session.observationSolution!;
    expect(predictedSkyWords({ ...session, observationSolution: solution as ObservationSession['observationSolution'] }).facts).toEqual(['Hipparcos 2007 · Gaia DR3']);
  });
});

describe('the predicted sky in the app (laptop)', () => {
  it('shows the logo, the Predicted tag, the heading, the disclosure and the facts, with Back to Moment and Ask out loud', () => {
    renderViewport(predictedSky());
    const screen = q('[data-testid="predicted-sky"]')!;
    expect(screen.querySelector('.reveal-word svg')).not.toBeNull();
    const status = screen.querySelector('.predsky-status')!;
    expect([...status.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'predsky-status__lead', 'predsky-status__note', 'predsky-status__facts']);
    expect(status.querySelector('.predsky-status__lead')!.textContent).toBe("The sky predicted for your photo's time and place.");
    expect([...status.querySelectorAll('.predsky-status__facts p')].map((p) => p.textContent)).toEqual(['2031-08-30 03:01 UTC', '30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3']);
    expect(q('.ask-bar')).not.toBeNull();
    expect([...container.querySelectorAll('.ask-bar button')].some((b) => b.textContent === 'Back to Moment')).toBe(true);
  });

  it('returns to the moment on Esc, as the There screen whose frame it takes does', () => {
    const sessions: ObservationSession[] = [];
    let current = predictedSky();
    const setSession = (next: ObservationSession | ((prev: ObservationSession) => ObservationSession)) => {
      current = typeof next === 'function' ? next(current) : next;
      sessions.push(current);
    };
    renderViewport(current, { setSession });
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(sessions.at(-1)?.spatialView?.mode).toBe('source_2d');
  });

  it("draws none of the old panel's lines: no checkpoint line, no hash, no solver line, no Restore or Compare in the sky, no layer count", async () => {
    renderViewport(predictedSky());
    // The renderer arrives a moment later, with its layers
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(q('[data-testid="epoch-and-disclosure-hud"]')).toBeNull();
    expect(q('[data-testid="celestial-marker-overlay"]')).toBeNull();
    expect(q('.alb-layer-disclosure')).toBeNull();
    const text = container.textContent ?? '';
    for (const old of ['Saved checkpoint', 'BACK TO MOMENT (ESC)', 'PRD', 'RESTORE STAR-PROTECTED', 'COMPARE A/B', 'MIXED EVIDENCE', 'LAYERS']) expect(text).not.toContain(old);
    expect(text).not.toContain(predictedSky().sourceAsset!.sha256.slice(0, 12));
    expect(q('.alb-lusion-brand-btn')).toBeNull();
    expect(q('.reveal-nav .look-views')).toBeNull();
  });

  it("at phone width: the close circle top left, the readout from top 584 with the time and place on one line, Back to Moment at the foot", () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    try {
      renderViewport(predictedSky());
      const screen = q('[data-testid="predicted-sky"]')!;
      expect(screen.className).toBe('predsky is-phone');
      expect(screen.querySelector('.reveal-word')).toBeNull();
      expect(screen.querySelector('.predsky-top button[aria-label="Close"]')).not.toBeNull();
      const status = screen.querySelector('.predsky-status')!;
      expect([...status.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'predsky-status__lead', 'predsky-status__note', 'predsky-status__facts']);
      expect([...status.querySelectorAll('.predsky-status__facts p')].map((p) => p.textContent)).toEqual(['2031-08-30 03:01 UTC · 30.00° N · 40.00° W']);
      expect([...container.querySelectorAll('.ask-bar button')].some((b) => b.textContent === 'Back to Moment')).toBe(true);
      expect(q('.ask-bar')!.className).toContain('is-phone');
      const text = container.textContent ?? '';
      for (const old of ['Saved checkpoint', 'PRD', 'MIXED EVIDENCE']) expect(text).not.toContain(old);
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });
});

describe("D20's marks on the predicted sky (item 4)", () => {
  it('rings the bright predicted stars where the renderer draws them, names the ones with room, and follows the camera', async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const { predictedSkyMarks } = await import('../predictedSkyMarks.js');
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const star = (id: string, magnitude: number, decDeg: number, commonName: string) =>
      ({ ...template, id, skyPosition: { raDeg: 10, decDeg, uncertaintyArcsec: 1 }, catalogRef: { id, provider: 'Hipparcos', releaseVersion: 'Hipparcos 2007', catalogSourceId: id, commonName, apparentMagnitude: magnitude } }) as unknown as typeof template;
    const session = { ...base, observationSolution: { ...base.observationSolution!, findings: [star('finding-predicted-star-a', 0.5, -80, 'Alpha'), star('finding-predicted-star-b', 2, -86, 'Beta')] } } as ObservationSession;
    let pose: CameraSkyPose | null = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: innerWidth / innerHeight, limitingMagnitude: 6 };
    act(() => root.render(createElement(PredictedSkyScreen, { session, onHome: noop, skyPose: () => pose })));
    const expected = predictedSkyMarks(session.observationSolution, pose, innerWidth, innerHeight);
    expect(expected.length).toBe(2);
    const circles = [...container.querySelectorAll('[data-testid="predicted-sky-rings"] circle')];
    expect(circles.map((c) => [Number(c.getAttribute('cx')), Number(c.getAttribute('cy')), Number(c.getAttribute('r'))])).toEqual(expected.map((m) => [m.xPx, m.yPx, m.r]));
    expect([...container.querySelectorAll('.predsky .align-layer__name')].map((n) => n.textContent)).toEqual(expected.filter((m) => m.name).map((m) => m.name));
    // The camera turns away: the marks go with it on the next frame
    pose = { ...pose!, cameraWorldQuaternion: [1, 0, 0, 0] };
    await act(async () => await new Promise((r) => setTimeout(r, 50)));
    expect(container.querySelectorAll('[data-testid="predicted-sky-rings"] circle')).toHaveLength(predictedSkyMarks(session.observationSolution, pose, innerWidth, innerHeight).length);
    expect(predictedSkyMarks(session.observationSolution, pose, innerWidth, innerHeight)).toHaveLength(0);
  });
});

describe("the predicted sky's rules are the record's", () => {
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
  it("lies over the sky's canvas, as the reveal does, taking no pointer but its own controls", () => {
    expect(declarationsOf('.predsky')).toEqual({ position: 'fixed', inset: '0', 'z-index': '30', 'pointer-events': 'none' });
    expect(declarationsOf('.reveal-photo')['z-index']).toBe('30');
  });

  it('places the phone readout from top 584, 24 px from the sides, the heading at 20 px, names at 11 px', () => {
    expect(declarationsOf('.predsky.is-phone .predsky-status')).toEqual({ left: '24px', right: '24px', top: '584px', width: 'auto' });
    expect(declarationsOf('.predsky.is-phone .predsky-status__lead')).toEqual({ 'font-size': '20px' });
    expect(declarationsOf('.predsky.is-phone .align-layer__name')).toEqual({ 'font-size': '11px' });
    expect(declarationsOf('.predsky-top')).toEqual({ position: 'absolute', left: '20px', top: '60px', 'pointer-events': 'auto' });
  });

  it('sets the heading 14 px under the tag at 24 px light, the disclosure 12 px under it, the facts after a hairline', () => {
    expect(declarationsOf('.predsky-status .look-tag')).toEqual({ 'margin-bottom': '0' });
    expect(declarationsOf('.predsky-status__lead')).toEqual({ margin: '14px 0 0', font: '300 24px / 1.2 var(--look-font-sans)', 'letter-spacing': '-0.03em', color: 'var(--look-ink)' });
    expect(declarationsOf('.predsky-status__note')).toEqual({ margin: '12px 0 0', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.predsky-status__facts')).toEqual({ 'margin-top': '14px', 'padding-top': '12px', 'border-top': '1px solid var(--look-hair)', 'font-size': '12px', 'line-height': '1.6', 'font-variant-numeric': 'tabular-nums', color: 'var(--look-ink-3)' });
  });
});
