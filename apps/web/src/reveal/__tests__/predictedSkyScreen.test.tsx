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
const skyPictures = vi.hoisted(() => [] as Array<[string, number]>);
// What the stand-in's dome shows once asked: what was asked, unless a test makes the glow fail
const domeShows = vi.hoisted(() => ({ value: null as string | null }));
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
    setSkyPicture(picture: string, limitMag: number, onShown?: (shown: string) => void) {
      skyPictures.push([picture, limitMag]);
      onShown?.(domeShows.value ?? picture);
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
const { formatSkyPlace, formatSkyTime, isPredictedSkyShown, phoneFacts, predictedSkyWords } = await import('../predictedSky.js');
const postcss = (await import('postcss')).default;

// D33 (UIUX-predsky-READY.md, decided by the lead 12:45): the sky Show predicted sky opens, on the laptop, in the
// There screen's frame with its own status column, and none of the old panel's lines.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const tokens = fs.readFileSync(path.resolve(here, '../../styles/look-tokens.css'), 'utf8');
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
    expect(predictedSkyWords(predictedSky(), true)).toEqual({
      heading: "The sky predicted for your photo's time and place.",
      note: 'Drawn or computed by the app; not measured in the source recording. Not lined up with your photo.',
      milkyWay: "Milky Way: ESO/S. Brunier's photograph with its own stars removed, placed for your photo's time and place. Not light your camera recorded.",
      facts: ['2031-08-30 03:01 UTC', '30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3'],
    });
  });

  it("says your clip's for a clip", () => {
    const words = predictedSkyWords(predictedSky('video'), true);
    expect(words.heading).toBe("The sky predicted for your clip's time and place.");
    expect(words.note).toBe('Drawn or computed by the app; not measured in the source recording. Not lined up with your clip.');
    expect(words.milkyWay).toBe("Milky Way: ESO/S. Brunier's photograph with its own stars removed, placed for your clip's time and place. Not light your camera recorded.");
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

  it('keeps the catalogues at phone width, under the time and place or alone (F2)', () => {
    const words = predictedSkyWords(predictedSky());
    expect(phoneFacts(words)).toEqual(['2031-08-30 03:01 UTC · 30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3']);
    expect(phoneFacts({ ...words, facts: ['Hipparcos 2007 · Gaia DR3'] })).toEqual(['Hipparcos 2007 · Gaia DR3']);
    expect(phoneFacts({ ...words, facts: ['2031-08-30 03:01 UTC', 'Hipparcos 2007 · Gaia DR3'] })).toEqual(['2031-08-30 03:01 UTC', 'Hipparcos 2007 · Gaia DR3']);
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
    expect(status.querySelector('.predsky-status__sky')!.textContent).toBe("Milky Way: ESO/S. Brunier's photograph with its own stars removed, placed for your photo's time and place. Not light your camera recorded.");
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

  it('words the Milky Way line from what the dome shows: the photograph until the glow is on (predsky-starless-one, 21:51)', async () => {
    expect(predictedSkyWords(predictedSky()).milkyWay).toBe("Milky Way: ESO/S. Brunier's photograph, placed for your photo's time and place. Not light your camera recorded.");
    domeShows.value = 'photograph';
    try {
      renderViewport(predictedSky());
      for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
      expect(skyPictures.at(-1)).toEqual(['starless', 10.5]);
      expect(q('.predsky-status__sky')!.textContent).toBe("Milky Way: ESO/S. Brunier's photograph, placed for your photo's time and place. Not light your camera recorded.");
    } finally {
      domeShows.value = null;
    }
  });

  it('asks the renderer for the starless glow and the catalog to 10.5 while the predicted sky shows (predsky-starless-one)', async () => {
    skyPictures.length = 0;
    renderViewport(predictedSky());
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
    expect(skyPictures.at(-1)).toEqual(['starless', 10.5]);
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

  it("at phone width: the close circle top left, the readout with the time and place on one line and the catalogues under it, Back to Moment at the foot", () => {
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
      expect(status.querySelector('.predsky-status__sky')!.textContent).toBe("Milky Way: ESO/S. Brunier's photograph with its own stars removed, placed for your photo's time and place. Not light your camera recorded.");
      // F2 (UIUX-tagphone-READY.md, decided 13:08): the block keeps the stars' catalogue line under the time and place
      expect([...status.querySelectorAll('.predsky-status__facts p')].map((p) => p.textContent)).toEqual(['2031-08-30 03:01 UTC · 30.00° N · 40.00° W', 'Hipparcos 2007 · Gaia DR3']);
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
      ({ ...template, id, title: commonName, skyPosition: { raDeg: 10, decDeg, uncertaintyArcsec: 1 }, catalogRef: { id, provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: id, commonName, apparentMagnitude: magnitude } }) as unknown as typeof template;
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
    const star = { ...template, id: 'finding-predicted-star-a', title: 'Alpha', skyPosition: { raDeg: 10, decDeg: -80, uncertaintyArcsec: 1 }, catalogRef: { id: 'a', provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: 'HIP 1', commonName: 'Alpha', apparentMagnitude: 0.5 } } as unknown as typeof template;
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

  it("says each name's evidence class and whether it is picked, in the words the reveal's marks use, so a screen reader hears what the tag shows", async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const { getMarkerVisualSpec } = await import('@albedian/design-system');
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const star = { ...template, id: 'finding-predicted-star-a', title: 'Alpha', skyPosition: { raDeg: 10, decDeg: -80, uncertaintyArcsec: 1 }, catalogRef: { id: 'a', provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: 'HIP 1', commonName: 'Alpha', apparentMagnitude: 0.5 } } as unknown as typeof template;
    const session = { ...base, observationSolution: { ...base.observationSolution!, findings: [star] } } as ObservationSession;
    const pose: CameraSkyPose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: innerWidth / innerHeight, limitingMagnitude: 6 };
    const spoken = (isSelected: boolean) => getMarkerVisualSpec({ evidenceClass: 'predicted', title: 'Alpha', isSelected, isFocused: false, isHovered: false }).ariaDescription;
    const render = (selected: string | null) =>
      act(() => root.render(createElement(PredictedSkyScreen, { session: { ...session, selectedFindingId: selected }, onHome: noop, skyPose: () => pose, onSelect: noop, onCloseCard: noop, onGoThere: noop })));
    const alpha = () => [...container.querySelectorAll<HTMLButtonElement>('.predsky button.predsky-name')].find((b) => b.textContent === 'Alpha')!;
    render(null);
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(spoken(false)).not.toBe('Alpha');
    expect(alpha().getAttribute('aria-label')).toBe(spoken(false));
    expect(alpha().getAttribute('aria-pressed')).toBe('false');
    render('finding-predicted-star-a');
    await act(async () => await new Promise((r) => setTimeout(r, 30)));
    expect(alpha().getAttribute('aria-label')).toBe(spoken(true));
    expect(alpha().getAttribute('aria-pressed')).toBe('true');
    // The name a person sees stays the start of what is heard (WCAG 2.5.3, label in name)
    expect(alpha().getAttribute('aria-label')!.startsWith('Alpha')).toBe(true);
  });

  it('passes the matching OpenNGC deep-sky row to StarCard when a predicted deep-sky object is picked, and asks for no table without one', async () => {
    const { CelestialFindingSchema } = await import('@albedian/contracts');
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const { DEEP_SKY_CATALOG_URL, forgetDeepSkyTable } = await import('../deepSky.js');
    forgetDeepSkyTable();
    const tableFile = {
      format: 'albedian.deep-sky-catalog',
      columns: ['name', 'type', 'vMag', 'bMag'],
      rows: [
        ['NGC5139', 'GCl', 5.33, null],
        ['NGC6611', 'Cl+N', 6.0, null],
      ],
    };
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify(tableFile)));
    vi.stubGlobal('fetch', fetchSpy);
    try {
      const base = predictedSky();
      act(() => root.render(createElement(PredictedSkyScreen, { session: base, onHome: noop })));
      expect(fetchSpy).not.toHaveBeenCalled();

      const omega = CelestialFindingSchema.parse({
        id: 'finding-openngc-NGC5139',
        title: 'Omega Centauri',
        subtitle: 'Globular cluster in Centaurus',
        evidenceClass: 'predicted',
        skyPosition: { raDeg: 201.697, decDeg: -47.4795, uncertaintyArcsec: 12 },
        catalogRef: {
          id: 'openngc-NGC5139',
          provider: 'OpenNGC',
          releaseVersion: 'OpenNGC v20260501',
          catalogSourceId: 'NGC5139',
          commonName: 'Omega Centauri',
          constellation: 'Cen',
          objectType: 'deep_sky',
          coordinateEpoch: 'J2000.0',
          raDeg: 201.697,
          decDeg: -47.4795,
          apparentMagnitude: 5.33,
          citation: 'OpenNGC',
        },
        evidence: {
          id: 'ev-openngc-NGC5139',
          evidenceClass: 'predicted',
          catalogReferenceId: 'openngc-NGC5139',
          verificationMethod: 'Predicted for the given time and place; not measured in the image',
          limitations: ['Catalog centre only.'],
          verifiedByIndependentHoldout: false,
        },
      });
      const nonDeepSameId = CelestialFindingSchema.parse({
        ...omega,
        id: 'finding-predicted-star-other',
        title: 'Alpha Centauri',
        catalogRef: { ...omega.catalogRef!, id: 'star-other', provider: 'Hipparcos', releaseVersion: 'Hipparcos 2007', objectType: 'star', commonName: 'Alpha Centauri' },
        evidence: { ...omega.evidence, id: 'ev-star-other', catalogReferenceId: 'star-other' },
      });
      const session = {
        ...base,
        selectedFindingId: omega.id,
        observationSolution: { ...base.observationSolution!, findings: [...base.observationSolution!.findings, omega, nonDeepSameId] },
      } as ObservationSession;

      await act(async () => {
        root.render(createElement(PredictedSkyScreen, { session, onHome: noop, onCloseCard: noop, onGoThere: noop }));
      });
      await act(async () => {
        await Promise.resolve();
      });

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(fetchSpy).toHaveBeenCalledWith(DEEP_SKY_CATALOG_URL);
      const card = container.querySelector('.reveal-card.is-sky')!;
      expect(card).not.toBeNull();
      expect(card.querySelector('.reveal-card__name')!.textContent).toBe('Omega Centauri');
      expect(card.querySelector('.reveal-card__sub')!.textContent).toBe('Globular cluster · Centaurus');
      expect(card.querySelector('.reveal-card__number b')!.textContent).toBe('5.33');
      expect(card.querySelector('.reveal-card__label')!.textContent).toBe('magnitude');
      expect(card.querySelector('.reveal-card__source')!.textContent).toBe('OpenNGC v20260501 · NGC5139');
      expect(card.querySelector('.reveal-card__acts')).toBeNull();

      act(() => {
        root.render(createElement(PredictedSkyScreen, { session, onHome: noop, onCloseCard: noop, onGoThere: noop, phone: true }));
      });
      expect(container.querySelector('.predsky.is-phone .reveal-card.is-phone .reveal-card__sub')!.textContent).toBe('Globular cluster · Centaurus');

      act(() => {
        root.render(createElement(PredictedSkyScreen, { session: { ...session, selectedFindingId: nonDeepSameId.id }, onHome: noop, onCloseCard: noop, onGoThere: noop }));
      });
      expect(container.querySelector('.reveal-card__sub')!.textContent).toBe('Centaurus');
    } finally {
      forgetDeepSkyTable();
      vi.unstubAllGlobals();
    }
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

  it('words Back to Moment in at the words-in delay on laptop and phone instead of waiting a camera flight half or mark span', () => {
    // Laptop DOM: .predsky precedes .ask-bar > .look-main as an earlier sibling
    renderViewport(predictedSky());
    const laptopScreen = q('[data-testid="predicted-sky"]')!;
    const laptopBar = q('[data-testid="ask-bar"]')!;
    const laptopBack = q('.ask-bar > .look-main')!;
    expect(laptopBack.textContent).toBe('Back to Moment');
    expect(laptopScreen.parentElement).toBe(laptopBar.parentElement);
    expect(laptopBack.matches('.predsky ~ .ask-bar > .look-main')).toBe(true);
    expect(laptopBack.matches('.predsky.is-phone ~ .ask-bar:not(.is-leaving) > .look-main')).toBe(false);

    // Phone DOM: .predsky.is-phone precedes .ask-bar.is-phone > .look-main
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    try {
      renderViewport(predictedSky());
      const phoneBar = q('[data-testid="ask-bar"]')!;
      const phoneBack = q('.ask-bar > .look-main')!;
      expect(phoneBar.matches('.predsky.is-phone ~ .ask-bar')).toBe(true);
      expect(phoneBack.matches('.predsky.is-phone ~ .ask-bar:not(.is-leaving) > .look-main')).toBe(true);
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }

    // Calibrated flight (There): no .predsky is in the tree, so Back to Moment keeps its camera-half delay
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const base = createSyntheticCalibratedSession();
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const thereSession = {
      ...base,
      selectedFindingId: star.id,
      machines: { ...base.machines, navigation: 'space', exploration: 'space_3d' },
      spatialView: { ...base.spatialView, mode: 'catalog_3d', transitionPhase: 'at_space' },
    } as ObservationSession;
    (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    const ctxSpy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    try {
      renderViewport(thereSession);
      const flightBack = q('.ask-bar > .look-main')!;
      expect(flightBack.textContent).toBe('Back to Moment');
      expect(flightBack.matches('.predsky ~ .ask-bar > .look-main')).toBe(false);
    } finally {
      ctxSpy.mockRestore();
      delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }

    // Cascade and order in motion.css
    const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
    const seen: Array<{ sel: string; decls: Record<string, string> }> = [];
    postcss.parse(motion).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const decls: Record<string, string> = {};
      rule.walkDecls((d) => {
        decls[d.prop] = d.value.replace(/\s+/g, ' ');
      });
      for (const part of rule.selectors) seen.push({ sel: part.replace(/\s+/g, ' ').trim(), decls });
    });
    const idx = (sel: string) => seen.findIndex((r) => r.sel === sel);
    const declsOf = (sel: string) => seen.filter((r) => r.sel === sel).map((r) => r.decls);

    expect(idx('.ask-bar > .look-main')).toBeGreaterThanOrEqual(0);
    expect(idx('.predsky ~ .ask-bar > .look-main')).toBeGreaterThan(idx('.ask-bar > .look-main'));
    expect(idx('.ask-bar.is-leaving > .look-main')).toBeGreaterThan(idx('.predsky ~ .ask-bar > .look-main'));
    expect(declsOf('.predsky ~ .ask-bar > .look-main')).toEqual([{ 'animation-delay': 'var(--look-mo-delay-words-in)' }]);

    expect(idx('.ask-bar')).toBeGreaterThanOrEqual(0);
    expect(idx('.predsky.is-phone ~ .ask-bar')).toBeGreaterThan(idx('.ask-bar'));
    expect(declsOf('.predsky.is-phone ~ .ask-bar')).toEqual([{ 'animation-delay': 'var(--look-mo-delay-words-in)' }]);

    expect(idx('.predsky.is-phone ~ .ask-bar:not(.is-leaving) > .look-main')).toBeGreaterThan(idx('.predsky ~ .ask-bar > .look-main'));
    expect(declsOf('.predsky.is-phone ~ .ask-bar:not(.is-leaving) > .look-main')).toEqual([{ animation: 'none' }]);
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
  it("rings a focused name as every button is (UIUX-buttons-READY.md: a 2 px ink outline, 2 px offset)", () => {
    expect(declarationsOf('.predsky .predsky-name:focus-visible')).toEqual({
      outline: 'var(--look-focus-width) solid var(--look-ink)',
      'outline-offset': 'var(--look-focus-offset)',
    });
    expect(tokens).toMatch(/--look-focus-width:\s*2px;/);
    expect(tokens).toMatch(/--look-focus-offset:\s*2px;/);
  });

  it("lies over the sky's canvas, as the reveal does, taking no pointer but its own controls", () => {
    expect(declarationsOf('.predsky')).toEqual({ position: 'fixed', inset: '0', 'z-index': '30', 'pointer-events': 'none' });
    expect(declarationsOf('.reveal-photo')['z-index']).toBe('30');
  });

  it('anchors the phone readout by its foot 16 px above Back to Moment (y 756), growing upward, 24 px from the sides, the heading at 20 px, names at 11 px', () => {
    // F2, as D19 and D30: the foot at y 756, so the two tags, ESO's line and the catalogues push the block up, not down
    // phone-textsize-tag-one (UIUX-phone-textsize-READY.md item 1): from the window's own foot, 88 px as at 390 by 844,
    // never from the 844 px design height, so a shorter window (a browser's larger text) keeps the tags inside it
    expect(declarationsOf('.predsky.is-phone .predsky-status')).toEqual({ left: '24px', right: '24px', top: 'auto', bottom: '88px', width: 'auto' });
    expect(844 - 756).toBe(88);
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

  it('stacks the phone predicted sky above AskBar while StarCard stands and steps .predsky-status 16 px above the sheet with D19 fade', () => {
    expect(declarationsOf('.predsky.is-phone:has(.reveal-card)')).toEqual({ 'z-index': '31' });
    expect(declarationsOf('.predsky.is-phone:has(.reveal-card.is-leaving) ~ .ask-bar')).toEqual({ display: 'none' });
    expect(declarationsOf('.predsky.is-phone .predsky-status.is-above-card')).toEqual({
      bottom: 'min(391px, calc(100% - 102px))',
      animation: 'none',
    });
  });

  // predsky-phone-fade-1 (gap nine, DECIDED-BY-LEAD 24 Nov 12:58, UIUX-predsky-READY.md): at phone width D19's fade
  // (44 px to 82 percent black) stands behind the readout at rest as well as above a card, as the estimate block has
  // it, so no star shows through the Predicted tag or the words
  it("stands D19's fade behind the phone readout at rest and above a card, from one rule", () => {
    expect(declarationsOf('.predsky.is-phone .predsky-status::before')).toEqual({
      content: "''",
      position: 'absolute',
      left: '-24px',
      right: '-24px',
      top: '-44px',
      bottom: '0',
      'z-index': '-1',
      background: 'linear-gradient(to bottom, rgba(0, 0, 0, 0) 0, rgba(0, 0, 0, 0.82) 44px)',
      'pointer-events': 'none',
    });
    let aboveCardOnly = 0;
    postcss.parse(css).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') === '.predsky.is-phone .predsky-status.is-above-card::before') aboveCardOnly += 1;
    });
    expect(aboveCardOnly).toBe(0);
  });
});

describe('predsky-card-motion-1: StarCard and leader leave timing, phone status above card, and phone AskBar', () => {
  it('keeps laptop StarCard and predicted-sky-leader leaving for 420 ms (270 ms reduced) and swaps stars at once without leaving', async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const makeStar = (id: string, decDeg: number, commonName: string) =>
      ({
        ...template,
        id,
        title: commonName,
        skyPosition: { raDeg: 10, decDeg, uncertaintyArcsec: 1 },
        catalogRef: { id, provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: id, commonName, apparentMagnitude: 0.5 },
      }) as unknown as typeof template;
    const session = {
      ...base,
      observationSolution: {
        ...base.observationSolution!,
        findings: [makeStar('finding-predicted-star-a', -80, 'Alpha'), makeStar('finding-predicted-star-b', -86, 'Beta')],
      },
    } as ObservationSession;
    let pose: CameraSkyPose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: innerWidth / innerHeight, limitingMagnitude: 6 };

    vi.useFakeTimers();
    try {
      const render = (selectedFindingId: string | null, phone = false) =>
        act(() => {
          root.render(
            createElement(PredictedSkyScreen, {
              session: { ...session, selectedFindingId },
              onHome: noop,
              skyPose: () => pose,
              phone,
              onSelect: noop,
              onCloseCard: noop,
              onGoThere: noop,
            }),
          );
        });

      render('finding-predicted-star-a');
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(q('[data-testid="star-card"]')!.className).toBe('reveal-card is-sky');
      expect(q('[data-testid="star-card"] .reveal-card__name')!.textContent).toBe('Alpha');
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status');
      expect([...q('.predsky-status')!.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual([
        'predicted',
        'simulated',
      ]);
      const alphaD = q('[data-testid="predicted-sky-leader"] path')!.getAttribute('d');
      expect(alphaD).toBeTruthy();

      // At phone width, no leader line is rendered even when resizing to phone mid-leave with a held leader path
      render(null, false);
      expect(q('[data-testid="predicted-sky-leader"]')!.getAttribute('class')).toBe('reveal-leader is-predicted is-leaving');
      render(null, true);
      expect(q('[data-testid="predicted-sky-leader"]')).toBeNull();
      render('finding-predicted-star-a', false);

      // Switching directly to another predicted star swaps at once without entering is-leaving
      render('finding-predicted-star-b');
      expect(q('[data-testid="star-card"]')!.className).toBe('reveal-card is-sky');
      expect(q('[data-testid="star-card"] .reveal-card__name')!.textContent).toBe('Beta');
      const betaD = q('[data-testid="predicted-sky-leader"] path')!.getAttribute('d');
      expect(betaD).toBeTruthy();
      expect(betaD).not.toBe(alphaD);

      // Closing holds both StarCard and leader mounted in is-leaving through 419 ms, then unmounts at 420 ms
      render(null);
      expect(q('[data-testid="star-card"]')!.className).toBe('reveal-card is-sky is-leaving');
      expect(q('[data-testid="star-card"]')!.hasAttribute('inert')).toBe(true);
      expect(q('[data-testid="star-card"] .reveal-card__name')!.textContent).toBe('Beta');
      expect(q('[data-testid="predicted-sky-leader"]')!.getAttribute('class')).toBe('reveal-leader is-predicted is-leaving');
      expect(q('[data-testid="predicted-sky-leader"] path')!.getAttribute('d')).toBe(betaD);

      act(() => {
        vi.advanceTimersByTime(419);
      });
      expect(q('[data-testid="star-card"]')).not.toBeNull();
      expect(q('[data-testid="predicted-sky-leader"]')).not.toBeNull();

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('[data-testid="predicted-sky-leader"]')).toBeNull();

      // If the camera turns a picked star off-screen before Close, heldLeader clears so no ghost leader lingers on Close
      render('finding-predicted-star-a');
      expect(q('[data-testid="predicted-sky-leader"]')).not.toBeNull();
      pose = { ...pose, cameraWorldQuaternion: [1, 0, 0, 0] };
      act(() => {
        vi.advanceTimersByTime(450);
      });
      expect(q('[data-testid="predicted-sky-leader"]')).toBeNull();
      render(null);
      expect(q('[data-testid="predicted-sky-leader"]')).toBeNull();
      act(() => {
        vi.advanceTimersByTime(420);
      });
      pose = { ...pose, cameraWorldQuaternion: [0, 0, 0, 1] };
      act(() => {
        vi.advanceTimersByTime(20);
      });

      // Reduced motion: 270 ms dissolve for both card and leader
      (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
        matches: query.includes('prefers-reduced-motion: reduce'),
        addEventListener: () => {},
        removeEventListener: () => {},
      });
      render('finding-predicted-star-a');
      render(null);
      act(() => {
        vi.advanceTimersByTime(269);
      });
      expect(q('[data-testid="star-card"]')).not.toBeNull();
      expect(q('[data-testid="predicted-sky-leader"]')).not.toBeNull();
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('[data-testid="predicted-sky-leader"]')).toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
      vi.useRealTimers();
    }

    const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
    const motionRule = (sel: string) => {
      const out: Record<string, string> = {};
      postcss.parse(motion).walkRules((rule) => {
        if (rule.parent?.type === 'root' && rule.selector.replace(/\s+/g, ' ') === sel) {
          rule.walkDecls((d) => {
            out[d.prop] = d.value.replace(/\s+/g, ' ');
          });
        }
      });
      return out;
    };
    expect(motionRule('.reveal-card.is-sky')).toEqual({
      animation: 'look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both',
    });
    expect(motionRule('.reveal-card.is-sky.is-leaving')).toEqual({
      animation: 'look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) calc(var(--look-mo-stagger) * 6) both',
    });
  });

  it('steps phone .predsky-status 16 px above open and leaving phone StarCard with Simulated and ESO line only, then restores full status', async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const base = predictedSky();
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const words = predictedSkyWords(base);

    vi.useFakeTimers();
    try {
      const renderPhone = (selectedFindingId: string | null) =>
        act(() => {
          root.render(
            createElement(PredictedSkyScreen, {
              session: { ...base, selectedFindingId },
              onHome: noop,
              phone: true,
              onSelect: noop,
              onCloseCard: noop,
              onGoThere: noop,
            }),
          );
        });

      renderPhone(null);
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status');
      expect([...container.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual([
        'predicted',
        'simulated',
      ]);

      renderPhone(star.id);
      const aboveStatus = q('.predsky-status')!;
      expect(aboveStatus.className).toBe('reveal-status predsky-status is-above-card');
      expect([...aboveStatus.children].map((el) => el.className)).toEqual(['predsky-status__tags', 'predsky-status__sky']);
      expect([...aboveStatus.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual([
        'predicted',
        'simulated',
      ]);
      expect(aboveStatus.querySelector('.predsky-status__sky')!.textContent).toBe(words.milkyWay);
      expect(q('.reveal-card.is-phone .reveal-card__top > .look-tag')!.getAttribute('data-evidence-class')).toBe('predicted');

      // Closing holds the phone sheet and is-above-card through 499 ms (sheetMs = 500 ms), then restores full status at 500 ms
      renderPhone(null);
      act(() => {
        vi.advanceTimersByTime(499);
      });
      expect(q('.reveal-card.is-phone.is-leaving')).not.toBeNull();
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status is-above-card');
      expect([...q('.predsky-status')!.children].map((el) => el.className)).toEqual(['predsky-status__tags', 'predsky-status__sky']);
      expect([...q('.predsky-status')!.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual([
        'predicted',
        'simulated',
      ]);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status');
      expect([...container.querySelectorAll('.predsky-status__tags > .look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual([
        'predicted',
        'simulated',
      ]);
      expect(q('.predsky-status__lead')!.textContent).toBe(words.heading);
      expect(q('.predsky-status__facts')).not.toBeNull();

      // Reduced motion on phone: 270 ms sheet dissolve
      (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
        matches: query.includes('prefers-reduced-motion: reduce'),
        addEventListener: () => {},
        removeEventListener: () => {},
      });
      renderPhone(star.id);
      renderPhone(null);
      act(() => {
        vi.advanceTimersByTime(269);
      });
      expect(q('.reveal-card.is-phone.is-leaving')).not.toBeNull();
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status is-above-card');
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('.predsky-status')!.className).toBe('reveal-status predsky-status');
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
      vi.useRealTimers();
    }
  });

  it('hides phone AskBar while a predicted StarCard is open on PredictedSkyScreen, keeping laptop AskBar and restoring phone AskBar on close', () => {
    const base = predictedSky();
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;

    // Laptop keeps AskBar mounted alongside the right-column sky StarCard
    renderViewport({ ...base, selectedFindingId: star.id }, { selectedFinding: star });
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(q('[data-testid="ask-bar"]')).not.toBeNull();

    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);

    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query.includes('max-width: 720px'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    try {
      // A leftover observed selection on the unmatched recording opens no sky StarCard, so Back to Moment stays in reach
      const observed = {
        ...star,
        id: 'finding-observed-glare',
        evidenceClass: 'observed' as const,
        evidence: { ...star.evidence, evidenceClass: 'observed' as const },
      };
      const withObserved = {
        ...base,
        selectedFindingId: observed.id,
        observationSolution: { ...base.observationSolution!, findings: [...base.observationSolution!.findings, observed] },
      } as ObservationSession;
      renderViewport(withObserved, { selectedFinding: observed });
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('[data-testid="ask-bar"]')).not.toBeNull();

      renderViewport({ ...base, selectedFindingId: star.id }, { selectedFinding: star });
      expect(q('.predsky.is-phone .reveal-card.is-phone')).not.toBeNull();
      expect(q('[data-testid="ask-bar"]')).toBeNull();

      renderViewport({ ...base, selectedFindingId: null }, { selectedFinding: null });
      expect(q('[data-testid="ask-bar"]')).not.toBeNull();
    } finally {
      delete (window as unknown as Record<string, unknown>).matchMedia;
    }
  });

  it('clears unpicked marks under the phone sheet settled box (469..814 px) even when frame-0 sheet-in translateY puts getBoundingClientRect off-screen', async () => {
    const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');
    const base = predictedSky();
    const template = base.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    // At 390x844 with identity pose and fov 60 deg, dec -80 projects above y=469 (~y=273) and dec -90 projects at screen centre (y=422);
    // dec -72 with ra 270 (+Y down in camera space: look -Z, +Y up -> dec -68 at ra 270 projects into y > 469). Let's read predictedSkyMarks first!
    const { predictedSkyMarks } = await import('../predictedSkyMarks.js');
    const pose: CameraSkyPose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: 390 / 844, limitingMagnitude: 6 };
    const makeStar = (id: string, raDeg: number, decDeg: number, commonName: string) =>
      ({
        ...template,
        id,
        title: commonName,
        skyPosition: { raDeg, decDeg, uncertaintyArcsec: 1 },
        catalogRef: { id, provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: id, commonName, apparentMagnitude: 0.5 },
      }) as unknown as typeof template;
    const upper = makeStar('finding-predicted-upper', 90, -75, 'UpperStar');
    const lower = makeStar('finding-predicted-lower', 270, -70, 'LowerStar');
    const session = {
      ...base,
      selectedFindingId: upper.id,
      observationSolution: { ...base.observationSolution!, findings: [upper, lower] },
    } as ObservationSession;
    const rawMarks = predictedSkyMarks(session.observationSolution, pose, 390, 844, 11);
    const lowerMark = rawMarks.find((m) => m.id === lower.id)!;
    expect(lowerMark.yPx).toBeGreaterThan(469);
    expect(lowerMark.yPx).toBeLessThan(814);

    const prevW = window.innerWidth;
    const prevH = window.innerHeight;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });
    const origOffsetLeft = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetLeft');
    const origOffsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetTop');
    const origOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
    const origOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
    Object.defineProperty(HTMLElement.prototype, 'offsetLeft', { configurable: true, get() { return (this as HTMLElement).classList.contains('reveal-card') ? 12 : 0; } });
    Object.defineProperty(HTMLElement.prototype, 'offsetTop', { configurable: true, get() { return (this as HTMLElement).classList.contains('reveal-card') ? 469 : 0; } });
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return (this as HTMLElement).classList.contains('reveal-card') ? 366 : 0; } });
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return (this as HTMLElement).classList.contains('reveal-card') ? 345 : 0; } });
    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      if ((this as HTMLElement).classList?.contains('reveal-card')) {
        // Frame 0 of look-mo-sheet-in: translateY pushes getBoundingClientRect below y = 844
        return { left: 12, right: 378, top: 844, bottom: 1189, width: 366, height: 345, x: 12, y: 844, toJSON: () => ({}) } as DOMRect;
      }
      return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    try {
      act(() => {
        root.render(createElement(PredictedSkyScreen, { session, onHome: noop, skyPose: () => pose, phone: true }));
      });
      // LowerStar falls inside 469..814 px so it is cleared under the settled sheet even while getBoundingClientRect is at y=844
      expect([...container.querySelectorAll('.predsky-name')].map((b) => b.textContent)).toEqual(['UpperStar']);
    } finally {
      rectSpy.mockRestore();
      if (origOffsetLeft) Object.defineProperty(HTMLElement.prototype, 'offsetLeft', origOffsetLeft);
      if (origOffsetTop) Object.defineProperty(HTMLElement.prototype, 'offsetTop', origOffsetTop);
      if (origOffsetWidth) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', origOffsetWidth);
      if (origOffsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', origOffsetHeight);
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: prevW });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: prevH });
    }
  });
});
