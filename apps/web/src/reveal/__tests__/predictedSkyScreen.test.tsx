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

const skyLooks = vi.hoisted(() => [] as Array<[number, number, number | undefined]>);
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
    lookAtSky(azDeg: number, altDeg: number, acrossDeg?: number) {
      skyLooks.push([azDeg, altDeg, acrossDeg]);
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
      milkyWay: "Milky Way: ESO/S. Brunier's photograph, placed for your photo's time and place. Not light your camera recorded.",
      facts: ['2031-08-30 03:01 UTC', '30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3'],
    });
  });

  it("says your clip's for a clip", () => {
    const words = predictedSkyWords(predictedSky('video'));
    expect(words.heading).toBe("The sky predicted for your clip's time and place.");
    expect(words.note).toBe('Drawn or computed by the app; not measured in the source recording. Not lined up with your clip.');
    expect(words.milkyWay).toBe("Milky Way: ESO/S. Brunier's photograph, placed for your clip's time and place. Not light your camera recorded.");
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
    // The 14:55 ruling (decided 13:08): the stars' Predicted tag, then the Milky Way's Simulated tag, and ESO's line
    expect([...status.children].map((el) => el.className)).toEqual(['predsky-status__tags', 'predsky-status__lead', 'predsky-status__note', 'predsky-status__sky', 'predsky-status__facts']);
    expect([...status.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual(['predicted', 'simulated']);
    expect(status.querySelector('.predsky-status__sky')!.textContent).toBe("Milky Way: ESO/S. Brunier's photograph, placed for your photo's time and place. Not light your camera recorded.");
    expect(status.querySelector('.predsky-status__lead')!.textContent).toBe("The sky predicted for your photo's time and place.");
    expect([...status.querySelectorAll('.predsky-status__facts p')].map((p) => p.textContent)).toEqual(['2031-08-30 03:01 UTC', '30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3']);
    expect(q('.ask-bar')).not.toBeNull();
    expect([...container.querySelectorAll('.ask-bar button')].some((b) => b.textContent === 'Back to Moment')).toBe(true);
  });

  it("opens facing D24's start, 30 degrees up on the laptop and 26 on the phone (decided 13:23)", async () => {
    const { startPose } = await import('../alignPose.js');
    const { predictedSkyStart } = await import('../predictedSky.js');
    // The sample's sky with one bright predicted star given its altitude and azimuth, as the server computes them
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const vega = { ...template, id: 'finding-predicted-star-vega', title: 'Vega', skyPosition: { raDeg: 279.2, decDeg: 38.8, uncertaintyArcsec: 1, altDeg: 52, azDeg: 75 }, catalogRef: { id: 'vega', provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: 'HIP 91262', commonName: 'Vega', apparentMagnitude: 0.03 } } as unknown as typeof template;
    const session = { ...base, observationSolution: { ...base.observationSolution!, findings: [...base.observationSolution!.findings, vega] } } as ObservationSession;
    const start = startPose(session)!;
    expect(start.pose.azDeg).toBe(75);
    // The picture's field across (ruled 17:05): 100 degrees on the laptop, 64 on the phone
    expect(predictedSkyStart(session, false)).toEqual({ azDeg: start.pose.azDeg, altDeg: 30, acrossDeg: 100 });
    expect(predictedSkyStart(session, true)).toEqual({ azDeg: start.pose.azDeg, altDeg: 26, acrossDeg: 64 });
    skyLooks.length = 0;
    renderViewport(session);
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(skyLooks.at(-1)).toEqual([start.pose.azDeg, 30, 100]);
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
      // Only the close circle at the top: not the old Audio strip (the design lane's read, 13:22)
      expect(q('[data-testid="inline-audio-coalescing"]')).toBeNull();
      const status = screen.querySelector('.predsky-status')!;
      expect([...status.children].map((el) => el.className)).toEqual(['predsky-status__tags', 'predsky-status__lead', 'predsky-status__note', 'predsky-status__sky', 'predsky-status__facts']);
      expect([...status.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual(['predicted', 'simulated']);
      expect(status.querySelector('.predsky-status__sky')!.textContent).toBe("Milky Way: ESO/S. Brunier's photograph, placed for your photo's time and place. Not light your camera recorded.");
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
      ({ ...template, id, skyPosition: { raDeg: 10, decDeg, uncertaintyArcsec: 1 }, catalogRef: { id, provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: id, commonName, apparentMagnitude: magnitude } }) as unknown as typeof template;
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

describe("D14's card from a name on the predicted sky (ruled 13:25)", () => {
  it('opens on a pressed name; no Original pixels; Go there only where the star can be flown to; Esc closes the card first', async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const star = { ...template, id: 'finding-predicted-star-a', skyPosition: { raDeg: 10, decDeg: -80, uncertaintyArcsec: 1 }, catalogRef: { id: 'a', provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: 'HIP 1', commonName: 'Alpha', apparentMagnitude: 0.5 } } as unknown as typeof template;
    const session = { ...base, observationSolution: { ...base.observationSolution!, findings: [star] } } as ObservationSession;
    const pose: CameraSkyPose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: innerWidth / innerHeight, limitingMagnitude: 6 };
    const picked: string[] = [];
    const render = (selected: string | null) =>
      act(() => root.render(createElement(PredictedSkyScreen, { session: { ...session, selectedFindingId: selected }, onHome: noop, skyPose: () => pose, onSelect: (id: string) => picked.push(id), onCloseCard: noop, onGoThere: noop })));
    render(null);
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    const name = [...container.querySelectorAll<HTMLButtonElement>('.predsky button.predsky-name')].find((b) => b.textContent === 'Alpha')!;
    act(() => name.click());
    expect(picked).toEqual(['finding-predicted-star-a']);
    render('finding-predicted-star-a');
    const card = container.querySelector('.reveal-card')!;
    expect(card).not.toBeNull();
    expect([...card.querySelectorAll('button')].map((b) => b.textContent)).not.toContain('Original pixels');
    // On the dark panel in the sky (ruled 17:05), with the leader line from the picked star
    expect(card.className).toBe('reveal-card is-sky');
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(container.querySelector('.predsky svg.reveal-leader.is-predicted path')).not.toBeNull();
    // At phone width the card is the sheet inside the phone's predicted sky, its own Predicted tag in its top row
    // (UIUX-tagphone-READY.md item D: motion.css stands that tag at full there)
    act(() => root.render(createElement(PredictedSkyScreen, { session: { ...session, selectedFindingId: 'finding-predicted-star-a' }, onHome: noop, skyPose: () => pose, onSelect: noop, onCloseCard: noop, onGoThere: noop, phone: true })));
    const sheet = container.querySelector('.predsky.is-phone > .reveal-card.is-phone')!;
    expect(sheet).not.toBeNull();
    expect(sheet.querySelector('.reveal-card__top > .look-tag')!.getAttribute('data-evidence-class')).toBe('predicted');
    expect([...sheet.querySelector('.reveal-card__top')!.children].map((el) => el.matches('.look-tag'))).toEqual([true, false]);
  });
});

describe("the card's action row in the sky (ruled 13:25)", () => {
  it('keeps Go there where the star can be flown to, and never Original pixels', async () => {
    const { StarCard } = await import('../StarCard.js');
    const { starCardFacts } = await import('../starCardFacts.js');
    const flyable = sample('plate_calibrated').findings.find((f) => starCardFacts(f).canGoThere)!;
    expect(flyable).toBeDefined();
    const card = (inSky: boolean) => {
      act(() => root.render(createElement(StarCard, { finding: flyable, originalPixels: false, onToggleOriginalPixels: noop, onGoThere: noop, onClose: noop, inSky })));
      return [...container.querySelectorAll('.reveal-card__acts button')].map((b) => b.textContent);
    };
    expect(card(false)).toEqual(['Go there', 'Original pixels']);
    expect(card(true)).toEqual(['Go there']);
  });
});

describe("the predicted sky's disclosure from its first frame (the lead's rule, 17:30)", () => {
  it('shows the Predicted tag, the heading and the disclosure at once: no delay and no fade from nothing', () => {
    const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
    const rules: Record<string, string> = {};
    postcss.parse(motion).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') === '.predsky .reveal-status' && rule.parent?.type === 'root') rule.walkDecls((d) => {
        rules[d.prop] = d.value;
      });
    });
    expect(rules).toEqual({ animation: 'none' });
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

  it("sets the card in the sky on D26's dark panel, 20 px inside it, in the card's own place", () => {
    expect(declarationsOf('.reveal-card.is-sky')).toEqual({
      'box-sizing': 'border-box',
      padding: '20px',
      'border-radius': '18px',
      border: '1px solid var(--look-hair)',
      background: 'rgba(16, 16, 18, 0.92)',
      '-webkit-backdrop-filter': 'blur(12px)',
      'backdrop-filter': 'blur(12px)',
    });
  });

  it('sets the heading 14 px under the tag at 24 px light, the disclosure 12 px under it, the facts after a hairline', () => {
    expect(declarationsOf('.predsky-status .look-tag')).toEqual({ 'margin-bottom': '0' });
    expect(declarationsOf('.predsky-status__tags')).toEqual({ display: 'flex', gap: '8px' });
    expect(declarationsOf('.predsky-status__sky')).toEqual({ margin: '8px 0 0', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.predsky-status__lead')).toEqual({ margin: '14px 0 0', font: '300 24px / 1.2 var(--look-font-sans)', 'letter-spacing': '-0.03em', color: 'var(--look-ink)' });
    expect(declarationsOf('.predsky-status__note')).toEqual({ margin: '12px 0 0', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.predsky-status__facts')).toEqual({ 'margin-top': '14px', 'padding-top': '12px', 'border-top': '1px solid var(--look-hair)', 'font-size': '12px', 'line-height': '1.6', 'font-variant-numeric': 'tabular-nums', color: 'var(--look-ink-3)' });
  });
});
