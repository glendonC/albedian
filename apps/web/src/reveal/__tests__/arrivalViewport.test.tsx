// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { createEmptyObservationSession, dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { HOME_SCREEN, choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import type { Arrival } from '../arrival.js';
import { clipPoseSolution, poseSession } from '../../components/__tests__/poseSessions.js';
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

const { SpatialObservatoryViewport } = await import('../../components/SpatialObservatoryViewport.js');
const { SourcePhotoStage } = await import('../../components/SourcePhotoStage.js');
const { sessionPhotoRect } = await import('../../components/sourcePhotoRect.js');
const { cornerPaths } = await import('../cornerPaths.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
// The contract's own predicted-only solution (a star search found points of light and matched none)
const PREDICTED_ONLY = (JSON.parse(
  fs.readFileSync(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, NonNullable<ObservationSession['observationSolution']>>)['horizon_predicted_only']!;
const WORKING = '[data-testid="working-screen"]';
const STAGE = '[data-testid="source-photo-stage"]';

/** A 3:4 phone photo as an upload leaves it once the receipt has arrived. */
function storedPhoto(over: Partial<ObservationSession> = {}): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', uri: 'blob:photo', width: 3024, height: 4032, durationMs: 0 },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
    ...over,
  } as ObservationSession;
}
const BYTES = 2_382_523;
const arrival = (over: Partial<Arrival> = {}): Arrival => ({
  fileName: 'night.jpg',
  byteSize: BYTES,
  stored: true,
  widthPx: 3024,
  heightPx: 4032,
  job: null,
  failure: null,
  ...over,
});

function renderViewport(session: ObservationSession, extra: Record<string, unknown> = {}) {
  act(() => {
    root.render(
      createElement(SpatialObservatoryViewport, {
        session,
        setSession: noop,
        selectedFinding: null,
        discoveredMedia: [],
        activeDiscoveredId: null,
        setActiveDiscoveredId: noop,
        activeDiscoveredItem: null,
        uploadedMediaUrl: 'blob:photo',
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

describe('sessionPhotoRect: where the session photo is drawn', () => {
  it("is the rectangle the stage draws the upright photo in: the sample's centre column at 1440 by 900", () => {
    expect(sessionPhotoRect(storedPhoto(), 1440, 900)).toEqual({ leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 });
  });

  it('turns a photo stored on its side upright first', () => {
    const base = storedPhoto();
    const sideways = { ...base, sourceAsset: { ...base.sourceAsset!, width: 4032, height: 3024, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    expect(sessionPhotoRect(sideways, 1440, 900)).toEqual({ leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 });
  });

  it('is none with no photo, outside the photo view, or before the photo has a measured size', () => {
    expect(sessionPhotoRect(createEmptyObservationSession(), 1440, 900)).toBeNull();
    const base = storedPhoto();
    expect(sessionPhotoRect({ ...base, spatialView: { ...base.spatialView, mode: 'catalog_3d' } } as ObservationSession, 1440, 900)).toBeNull();
    expect(sessionPhotoRect({ ...base, sourceAsset: { ...base.sourceAsset!, width: 1, height: 1 } } as ObservationSession, 1440, 900)).toBeNull();
  });
});

describe('SourcePhotoStage: bare and dimmed, for the working screen', () => {
  it('keeps its layer label by default and drops it when bare, the photo untouched either way', () => {
    act(() => root.render(createElement(SourcePhotoStage, { session: storedPhoto(), viewportWidthPx: 1440, viewportHeightPx: 900 })));
    expect(q('[data-testid="source-photo-layer-label"]')).not.toBeNull();
    expect((q(`${STAGE} img`) as HTMLImageElement).style.opacity).toBe('');
    act(() => root.render(createElement(SourcePhotoStage, { session: storedPhoto(), viewportWidthPx: 1440, viewportHeightPx: 900, bare: true })));
    expect(q('[data-testid="source-photo-layer-label"]')).toBeNull();
    const img = q(`${STAGE} img`) as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('blob:photo');
    expect(img.style.opacity).toBe('');
    expect(img.style.filter).toBe('');
    expect(img.style.left).toBe('382.5px');
    expect(img.style.width).toBe('675px');
    // Still the person's own pixels, and said so to the page
    expect((q(STAGE) as HTMLElement).dataset.evidenceClass).toBe('observed');
  });

  it('draws the photo at 55 percent when dimmed, and changes nothing else about it', () => {
    act(() => root.render(createElement(SourcePhotoStage, { session: storedPhoto(), viewportWidthPx: 1440, viewportHeightPx: 900, bare: true, dimmed: true })));
    const img = q(`${STAGE} img`) as HTMLImageElement;
    expect(img.style.opacity).toBe('0.55');
    expect(img.style.filter).toBe('');
    expect(img.getAttribute('src')).toBe('blob:photo');
  });
});

describe('the viewport shows the working screen while an upload is on its way', () => {
  function past(session: ObservationSession, extra: Record<string, unknown>) {
    // The home first, as the app opens; a chosen photo leaves it
    renderViewport(session, { uploadedMediaUrl: null, ...extra });
    choosePhotoOnHome(container);
    renderViewport(session, extra);
  }

  it('shows the photo bare and the rows, and none of the pieces that come after the solve', () => {
    past(storedPhoto(), { arrival: arrival() });
    expect(q(HOME_SCREEN)).toBeNull();
    expect(q(WORKING)).not.toBeNull();
    expect(q(STAGE)).not.toBeNull();
    expect(q('[data-testid="source-photo-layer-label"]')).toBeNull();
    for (const gone of [
      '.alb-lusion-header',
      '.reveal-nav',
      '.sound-control',
      '[data-testid="voice-command-hud"]',
      '[data-testid="ask-bar"]',
      '[data-testid="observation-solve-compare-hud"]',
      '[data-testid="celestial-marker-overlay"]',
      '[data-testid="celestial-target-card"]',
      '[data-testid="flight-and-restore-hud"]',
      '.alb-layer-disclosure-toggle',
    ]) {
      expect(q(gone), gone).toBeNull();
    }
    expect((q(`${STAGE} img`) as HTMLImageElement).style.opacity).toBe('');
  });

  it("draws the corners at the photo's own frame", () => {
    Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 900, configurable: true });
    past(storedPhoto(), { arrival: arrival() });
    const paths = [...container.querySelectorAll('svg.photo-corners path')].map((p) => p.getAttribute('d'));
    expect(paths).toEqual(cornerPaths({ leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 }));
  });

  it('covers the 3D scene with black, with no corners, while the file is still being sent and there is no photo yet', () => {
    past(createEmptyObservationSession(), { arrival: arrival({ stored: false, widthPx: null, heightPx: null }) });
    expect(q(WORKING)).not.toBeNull();
    expect(q(STAGE)).toBeNull();
    expect(q('.work-backdrop')).not.toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    // The modeled scene is not what is on screen, so nothing describes it
    expect(q('.alb-layer-disclosure-toggle')).toBeNull();
    expect(q('.alb-lusion-header')).toBeNull();
  });

  it('needs no black cover once the photo is on screen', () => {
    past(storedPhoto(), { arrival: arrival() });
    expect(q('.work-backdrop')).toBeNull();
  });

  it('dims the photo to 55 percent when the job failed, and passes Try again and Upload another on', () => {
    const onTryAgain = vi.fn();
    const onFileChosen = vi.fn();
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {});
    past(storedPhoto(), {
      arrival: arrival({ failure: { sentence: 'The solve stopped on an internal error.', canTryAgain: true } }),
      onTryAgain,
      onFileChosen,
    });
    expect((q(`${STAGE} img`) as HTMLImageElement).style.opacity).toBe('0.55');
    act(() => (q(`${WORKING} button.look-main`) as HTMLButtonElement).click());
    expect(onTryAgain).toHaveBeenCalledTimes(1);
    onFileChosen.mockClear();
    const input = q('[data-testid="working-file-input"]') as HTMLInputElement;
    const file = new File([new Uint8Array([0xff, 0xd8])], 'other.jpg', { type: 'image/jpeg' });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    act(() => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(onFileChosen).toHaveBeenCalledTimes(1);
    expect(onFileChosen).toHaveBeenCalledWith(file);
  });

  it('shows the screens after the solve once the arrival ends, with the photo labelled again', () => {
    past(storedPhoto(), { arrival: arrival() });
    renderViewport(storedPhoto(), { arrival: null });
    expect(q(WORKING)).toBeNull();
    expect(q('.alb-lusion-header')).not.toBeNull();
    // The Sound circle comes with the screens after the solve
    expect(q('.reveal-nav .sound-control button[aria-label="Sound"]')).not.toBeNull();
    // Over the laptop reveal the approved ask bar takes the old bar's place
    expect(q('[data-testid="ask-bar"]')).not.toBeNull();
    expect(q('[data-testid="voice-command-hud"]')).toBeNull();
    // The reveal takes the working screen's place over the same photo, at full strength again
    expect(q('[data-testid="reveal-photo"]')).not.toBeNull();
    expect((q(`${STAGE} img`) as HTMLImageElement).style.opacity).toBe('');
  });

  it('returns to the home from the logo while the upload goes on, and the home covers the working screen', () => {
    past(storedPhoto(), { arrival: arrival() });
    act(() => (q('[data-testid="working-home"]') as HTMLButtonElement).click());
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q(WORKING)).toBeNull();
  });

  it('keeps the home over everything while the home shows, arrival or not', () => {
    renderViewport(storedPhoto(), { uploadedMediaUrl: null, arrival: arrival() });
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q(WORKING)).toBeNull();
  });
});

describe('the viewport shows the approved reveal over the photo, above phone width (unit 1d)', () => {
  const PANEL = '[data-testid="observation-solve-compare-hud"]';
  const OLD_MARKS = '[data-testid="celestial-marker-overlay"]';
  const REVEAL = '[data-testid="reveal-photo"]';
  const LABEL = '[data-testid="source-photo-layer-label"]';
  const mask = () => {
    const img = q(`${STAGE} img`) as HTMLImageElement;
    return img.style.maskImage || img.style.getPropertyValue('-webkit-mask-image');
  };
  function open(session: ObservationSession) {
    renderViewport(session, { uploadedMediaUrl: null });
    choosePhotoOnHome(container);
    renderViewport(session);
  }
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).matchMedia;
  });

  it('draws the reveal, the photo bare with fading sides, and neither the old panel, the old rings nor the old logo', () => {
    open(storedPhoto());
    expect(q(REVEAL)).not.toBeNull();
    expect(q(PANEL)).toBeNull();
    expect(q(OLD_MARKS)).toBeNull();
    expect(q(LABEL)).toBeNull();
    expect(mask()).toContain('56px');
    expect(q('.alb-lusion-brand-btn')).toBeNull();
    expect(container.querySelectorAll('.look-logo')).toHaveLength(1);
  });

  it('keeps the old sheets on a phone, whose approved reveal is its own unit', () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
      matches: query.includes('max-width: 720px'),
      addEventListener: () => {},
      removeEventListener: () => {},
    });
    open(storedPhoto());
    expect(q(REVEAL)).toBeNull();
    expect(q(PANEL)).not.toBeNull();
    expect(q(OLD_MARKS)).not.toBeNull();
    expect(q(LABEL)).not.toBeNull();
    expect(mask()).toBe('');
    expect(q('.alb-lusion-brand-btn')).not.toBeNull();
  });

  it('is not shown in the sky and 3D views, which keep their own pieces until their units', () => {
    const base = storedPhoto();
    open({ ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' } } as ObservationSession);
    expect(q(REVEAL)).toBeNull();
    expect(q(PANEL)).not.toBeNull();
    expect(q('.alb-lusion-brand-btn')).not.toBeNull();
    // Every laptop screen after the home has the Sound circle, these views too
    expect(q('.reveal-nav .sound-control')).not.toBeNull();
  });

  it('draws neither corners nor marks once the browser shows the photo turned differently from its tag', () => {
    const base = storedPhoto();
    const tagged = { ...base, sourceAsset: { ...base.sourceAsset!, width: 4032, height: 3024, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    open(tagged);
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    const img = q(`${STAGE} img`) as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 4032, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 3024, configurable: true });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q(REVEAL)).not.toBeNull();
  });
});

describe('the viewport shows the approved star card over the photo, above phone width (unit 1e)', () => {
  const CARD = '[data-testid="star-card"]';
  const OLD_CARD = '[data-testid="celestial-target-card"]';
  const mask = () => {
    const img = q(`${STAGE} img`) as HTMLImageElement;
    return img.style.maskImage || img.style.getPropertyValue('-webkit-mask-image');
  };
  const pickedSession = (): ObservationSession => {
    const base = storedPhoto();
    return { ...base, selectedFindingId: base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!.id } as ObservationSession;
  };
  function open(session: ObservationSession, extra: Record<string, unknown> = {}) {
    const selectedFinding = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId) ?? null;
    renderViewport(session, { uploadedMediaUrl: null, selectedFinding, ...extra });
    choosePhotoOnHome(container);
    renderViewport(session, { selectedFinding, ...extra });
  }
  const press = (label: string) => act(() => ([...container.querySelectorAll(`${CARD} button`)].find((b) => (b.textContent || b.getAttribute('aria-label')) === label) as HTMLButtonElement).click());
  // A browser that can draw Real sky (WebGL 2); jsdom has none, so each test here stands one in
  beforeEach(() => {
    (window as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    sky.drawn = true;
  });
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).matchMedia;
    delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
  });

  it('draws Real sky over the photo\'s frame in the measured sky view: the layer, the Simulated tag and sentence, the photo beneath, none of the old sky view\'s pieces', () => {
    const base = storedPhoto();
    const sky = { ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' } } as ObservationSession;
    open(sky);
    expect(q('[data-testid="reveal-photo"]')).not.toBeNull();
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(q('[data-testid="reveal-status"] .look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    const residual = Math.max(1, Math.ceil((base.observationSolution!.quality!.medianResidualArcsec ?? base.observationSolution!.quality!.medianResidualPx! * base.observationSolution!.wcs!.scaleDegPerPx * 3600) / 60));
    expect(q('[data-testid="reveal-status"] .reveal-status__line')!.textContent).toBe(`ESO's photograph of this part of the sky, placed by your photo's solve to within ${residual}′. Not light your camera recorded.`);
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
    expect(q('svg.photo-corners')).not.toBeNull();
    expect(q('[data-testid="source-photo-stage"] img')).not.toBeNull();
    for (const gone of ['[data-testid="flight-and-restore-hud"]', '.alb-layer-disclosure', '[data-testid="observation-solve-compare-hud"]', '[data-testid="voice-command-hud"]']) {
      expect(q(gone), gone).toBeNull();
    }
    const pills = [...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')];
    expect(pills.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
  });

  // The verdict on unit-realsky-1: the status says what is on screen, never Simulated over the person's own photo
  const skyOpen = () => {
    const base = storedPhoto();
    return { ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' } } as ObservationSession;
  };
  const statusTag = () => q('[data-testid="reveal-status"] .look-tag')?.getAttribute('data-evidence-class') ?? null;
  const statusText = () => q('[data-testid="reveal-status"]')!.textContent!;

  it('keeps the photo\'s own status while the panorama loads or when it cannot be drawn (scenarios B and C)', () => {
    sky.drawn = false;
    open(skyOpen());
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(statusTag()).toBeNull();
    expect(statusText()).toContain('Calibrated');
    expect(statusText()).not.toContain('Not light your camera recorded');
  });

  it('says Simulated only once the panorama is drawn, and goes back to the photo\'s own status with Original pixels (scenario A)', () => {
    const base = skyOpen();
    const picked = { ...base, selectedFindingId: base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!.id } as ObservationSession;
    open(picked, { selectedFinding: picked.observationSolution!.findings.find((f) => f.id === picked.selectedFindingId) });
    expect(statusTag()).toBe('simulated');
    act(() => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Original pixels')!).click());
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(statusTag()).toBeNull();
    expect(statusText()).toContain('Calibrated');
    act(() => ([...container.querySelectorAll<HTMLButtonElement>('[data-testid="star-card"] button')].find((b) => b.textContent === 'Original pixels')!).click());
    expect(statusTag()).toBe('simulated');
  });

  it('comes back to the photo from Your photo, through the saved moment, and does nothing from Real sky while on it', () => {
    const sessions: ObservationSession[] = [];
    const base = storedPhoto();
    let current = base;
    const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
      current = update(current);
      sessions.push(current);
    };
    open(base, { setSession });
    act(() => ([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].find((b) => b.textContent === 'Real sky')!).click());
    expect(current.spatialView.mode).toBe('registered_sky');
    renderViewport(current, { setSession });
    // Left through the reducer: the old view's restore button would show here; Your photo is the way back
    expect(current.machines.navigation).toBe('sky_registered');
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(q('[data-testid="flight-and-restore-hud"]')).toBeNull();
    const pill = (label: string) => [...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].find((b) => b.textContent === label)!;
    const before = sessions.length;
    act(() => pill('Real sky').click());
    expect(sessions.length).toBe(before);
    act(() => pill('Your photo').click());
    expect(current.spatialView.mode).toBe('source_2d');
    expect(current.lastStatusBanner).toMatch(/^Returned to Moment/);
  });

  it('never draws Real sky over a solve that did not pass its check, whatever its status word', () => {
    const base = storedPhoto();
    const sky = { ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' } } as ObservationSession;
    // The contract refuses such a solution, so it is given after the session is built: the screen checks for itself
    const unverified = { ...sky, observationSolution: { ...sky.observationSolution!, quality: { ...sky.observationSolution!.quality!, passedIndependentVerification: false } } } as ObservationSession;
    open(unverified);
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
  });

  it('opens nothing it cannot draw: without WebGL 2, Real sky is not pressable and the measured sky view keeps its old pieces', () => {
    delete (window as unknown as Record<string, unknown>).WebGL2RenderingContext;
    const base = storedPhoto();
    open(base);
    expect([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].map((b) => b.disabled)).toEqual([false, true, true]);
    renderViewport({ ...base, spatialView: { ...base.spatialView, mode: 'registered_sky' } } as ObservationSession);
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(q('[data-testid="reveal-photo"]')).toBeNull();
  });

  /** The picked session after the card's Go there, through the reducer (space.enter saves the moment first). */
  const thereFrom = (session: ObservationSession): ObservationSession => {
    const out = dispatchAlbedianAction(session, {
      type: 'space.enter',
      requestId: 'ui-go-there',
      expectedViewRevision: session.viewRevision,
      findingId: session.selectedFindingId!,
      distancePolicy: 'measured_or_estimated',
    });
    expect(out.outcome.status).toBe('completed');
    return out.session;
  };

  it('shows there after Go there: the panorama, the picked mark alone, the readout, Back to Moment first, 3D current, and none of the old 3D pieces', () => {
    const there = thereFrom(pickedSession());
    open(there, { selectedFinding: there.observationSolution!.findings.find((f) => f.id === there.selectedFindingId) });
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(q('[data-testid="flight-readout"] h1')!.textContent).toBe(there.observationSolution!.findings.find((f) => f.id === there.selectedFindingId)!.title);
    expect(container.querySelectorAll('button.reveal-mark')).toHaveLength(1);
    expect(q('.ask-bar > button.look-main')!.textContent).toBe('Back to Moment');
    expect([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'true']);
    for (const gone of ['[data-testid="flight-and-restore-hud"]', '.alb-layer-disclosure', '[data-testid="star-card"]', '[data-testid="voice-command-hud"]']) {
      expect(q(gone), gone).toBeNull();
    }
  });

  it('lands Back to Moment on the exact moment: the photo with the same star picked, its card back; Esc and Your photo do the same', () => {
    const picked = pickedSession();
    for (const way of ['button', 'esc', 'pill'] as const) {
      let current = thereFrom(picked);
      const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
        current = update(current);
      };
      open(current, { setSession });
      if (way === 'button') act(() => (q('.ask-bar > button.look-main') as HTMLButtonElement).click());
      if (way === 'esc') act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      });
      if (way === 'pill') act(() => ([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].find((b) => b.textContent === 'Your photo')!).click());
      expect(current.spatialView.mode, way).toBe('source_2d');
      expect(current.selectedFindingId, way).toBe(picked.selectedFindingId);
      expect(current.lastStatusBanner, way).toMatch(/^Returned to Moment/);
      act(() => root.unmount());
      root = createRoot(container);
    }
  });

  it('keeps Esc for an open answer there, and Go there pressed again does nothing', () => {
    const onRunUtterance = vi.fn();
    let current = thereFrom(pickedSession());
    const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
      current = update(current);
    };
    open(current, { setSession, onRunUtterance, answer: { heard: 'Is that a plane?', sentence: "Nothing in this photo's data answers that.", answer: null } });
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(current.spatialView.mode).toBe('catalog_3d');
    act(() => ([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].find((b) => b.textContent === '3D')!).click());
    expect(onRunUtterance).not.toHaveBeenCalled();
  });

  it('opens keyboard help on "?" after the home on the laptop, the 3D keys listed only in the 3D view (INTERIM)', () => {
    const press = () => act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true, cancelable: true }));
    });
    const labels = () => [...container.querySelectorAll('.keys-help__row span:first-child')].map((s) => s.textContent);
    open(storedPhoto());
    press();
    expect(q('.keys-help [role="dialog"]')).not.toBeNull();
    expect(labels()).not.toContain('Move in 3D');
    act(() => root.unmount());
    root = createRoot(container);
    const base = storedPhoto();
    open({ ...base, spatialView: { ...base.spatialView, mode: 'catalog_3d' } } as ObservationSession);
    press();
    expect(labels()).toContain('Move in 3D');
  });

  it('has no keyboard help on a phone', () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    open(storedPhoto());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true, cancelable: true }));
    });
    expect(q('.keys-help')).toBeNull();
  });

  /** A wide clip as an upload leaves it, ten seconds long. */
  const clip = (): ObservationSession => {
    const base = storedPhoto();
    return { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'video', mimeType: 'video/mp4', width: 1920, height: 1080, durationMs: 10_900 } } as ObservationSession;
  };

  it("shows a clip's player under the frame over the reveal, its sides shaded, and names the counts as the clip's (INTERIM)", () => {
    open(clip());
    expect(q('[data-testid="clip-player"]')).not.toBeNull();
    expect(q('.clip-player__line')!.getAttribute('aria-valuemax')).toBe('10.9');
    expect(q('.clip-shade')).not.toBeNull();
    expect((q('.clip-shade') as HTMLElement).style.backgroundImage).toContain('linear-gradient');
    expect(q('.reveal-legend__title')!.textContent).toBe('In this clip');
    // A clip filling the width has no corners, as the design lane's page draws it
    expect(q('svg.photo-corners')).toBeNull();
  });

  it("hides a lined-up clip's predicted marks while the clip plays, and draws them again once paused on its frame (the verdict on unit-clip-1)", () => {
    open(poseSession(clipPoseSolution(3, 5), 'video'));
    const predicted = () => container.querySelectorAll('button.reveal-mark.is-predicted').length;
    expect(predicted()).toBeGreaterThan(0);
    // jsdom cannot play a clip: the element is stood in to start and stop as a browser's does
    const video = q('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
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
    expect(predicted()).toBe(0);
    act(() => (q('[data-testid="clip-player"] button[aria-label="Pause"]') as HTMLButtonElement).click());
    expect(predicted()).toBeGreaterThan(0);
  });

  it("sets the session clock to the clip's end when it plays out on its own, so marks lined up for another frame stay hidden (the re-read of unit-clip-1)", () => {
    let current = poseSession(clipPoseSolution(3, 5), 'video');
    const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
      current = update(current);
    };
    open(current, { setSession });
    const video = q('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    let paused = true;
    let time = 3;
    Object.defineProperty(video, 'paused', { configurable: true, get: () => paused });
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => time, set: (t: number) => (time = t) });
    video.play = () => {
      paused = false;
      video.dispatchEvent(new Event('play'));
      return Promise.resolve();
    };
    act(() => (q('[data-testid="clip-player"] button[aria-label="Play"]') as HTMLButtonElement).click());
    const end = current.sourceAsset!.durationMs / 1000;
    act(() => {
      time = end;
      paused = true;
      video.dispatchEvent(new Event('timeupdate'));
      video.dispatchEvent(new Event('pause'));
      video.dispatchEvent(new Event('ended'));
    });
    expect(end).not.toBe(3);
    expect(current.currentSourceTimeSec).toBe(end);
    renderViewport(current, { setSession });
    expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBe(0);
  });

  it('sets the session clock through the reducer where the player stops', () => {
    let current = clip();
    const setSession = (update: (prev: ObservationSession) => ObservationSession) => {
      current = update(current);
    };
    open(current, { setSession });
    const line = q('.clip-player__line') as HTMLElement;
    act(() => {
      line.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
    });
    // jsdom's clip element reports no time of its own: the step lands one second in
    expect(current.currentSourceTimeSec).toBe(1);
    expect(current.lastStatusBanner).toMatch(/^Seeked to 1\.00s/);
  });

  it('shows no player over Real sky, which draws no clip', () => {
    const sky = { ...clip(), spatialView: { ...clip().spatialView, mode: 'registered_sky' } } as ObservationSession;
    open(sky);
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(q('[data-testid="clip-player"]')).toBeNull();
  });

  it('shows no player for a photo, no shade for a tall clip, and none on a phone', () => {
    open(storedPhoto());
    expect(q('[data-testid="clip-player"]')).toBeNull();
    expect(q('.clip-shade')).toBeNull();
    expect(q('.reveal-legend__title')!.textContent).toBe('In this photo');
    act(() => root.unmount());
    root = createRoot(container);
    const tall = { ...clip(), sourceAsset: { ...clip().sourceAsset!, width: 1080, height: 1920 } } as ObservationSession;
    open(tall);
    expect(q('[data-testid="clip-player"]')).not.toBeNull();
    expect(q('.clip-shade')).toBeNull();
    // A tall clip stands in the photo's column, with its corners, as a photo does
    expect(q('svg.photo-corners')).not.toBeNull();
    act(() => root.unmount());
    root = createRoot(container);
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    open(clip());
    expect(q('[data-testid="clip-player"]')).toBeNull();
  });

  it('draws the approved card for the picked star, and not the old card', () => {
    open(pickedSession());
    expect(q(CARD)).not.toBeNull();
    expect(q(OLD_CARD)).toBeNull();
    expect(q(`${CARD} h1`)!.textContent).toBe(pickedSession().observationSolution!.findings.find((f) => f.id === pickedSession().selectedFindingId)!.title);
  });

  it('keeps the old card on a phone and in the sky and 3D views', () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    open(pickedSession());
    expect(q(CARD)).toBeNull();
    expect(q(OLD_CARD)).not.toBeNull();
  });

  it('shows the photo whole while Original pixels is pressed, and faded with its marks again once pressed again', () => {
    open(pickedSession());
    expect(mask()).toContain('56px');
    press('Original pixels');
    expect(mask()).toBe('');
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q(CARD)).not.toBeNull();
    press('Original pixels');
    expect(mask()).toContain('56px');
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
  });

  it('lets Original pixels last only while the card is open: closing the card brings the fade and the marks back', () => {
    const session = pickedSession();
    open(session);
    press('Original pixels');
    expect(mask()).toBe('');
    // The card closes: the session no longer has a picked star
    renderViewport({ ...session, selectedFindingId: null } as ObservationSession, { selectedFinding: null });
    expect(q(CARD)).toBeNull();
    expect(mask()).toContain('56px');
    renderViewport(session, { selectedFinding: session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId) });
    expect(q('.reveal-card__acts .look-second')!.getAttribute('aria-pressed')).toBe('false');
  });

  it('puts the view pills before the Sound circle: Your photo current, Real sky ready, 3D only with a star it can fly to', () => {
    open(storedPhoto());
    const nav = q('.reveal-nav')!;
    expect([...nav.children].map((el) => el.className)).toEqual(['look-views look-size-compact', 'sound-control is-off']);
    const pills = [...nav.querySelectorAll<HTMLButtonElement>('[role="group"] button')];
    expect(nav.querySelector('[role="group"]')!.getAttribute('aria-label')).toBe('View');
    expect(pills.map((b) => b.textContent)).toEqual(['Your photo', 'Real sky', '3D']);
    expect(pills.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false']);
    expect(pills.map((b) => b.disabled)).toEqual([false, false, true]);
    // With a star the app can fly to, 3D is ready (the window is past the home already)
    const session = pickedSession();
    renderViewport(session, { selectedFinding: session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId) });
    expect([...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].map((b) => b.disabled)).toEqual([false, false, false]);
  });

  it('keeps Real sky unpressable on a solve calibrated in word only, and 3D on a picked star the app cannot fly to', () => {
    const base = storedPhoto();
    const solution = base.observationSolution!;
    // The contract refuses an unverified calibrated solve, so it is given after the session is built
    const unverified = { ...base, observationSolution: { ...solution, quality: { ...solution.quality!, passedIndependentVerification: false } } } as ObservationSession;
    open(unverified);
    const disabled = () => [...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].map((b) => b.disabled);
    expect(disabled()).toEqual([false, true, true]);
    // A picked star with no distance to fly to: 3D stays unpressable
    const star = solution.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const noDistance = {
      ...base,
      selectedFindingId: star.id,
      observationSolution: { ...solution, findings: solution.findings.map((f) => (f.id === star.id ? { ...f, inferredDistancePc: undefined } : f)) },
    } as ObservationSession;
    renderViewport(noDistance, { selectedFinding: noDistance.observationSolution!.findings.find((f) => f.id === star.id) });
    expect(disabled()).toEqual([false, false, true]);
  });

  it('opens the measured sky from Real sky, flies to the picked star from 3D, and does nothing from Your photo', () => {
    const onRunUtterance = vi.fn();
    const sessions: ObservationSession[] = [];
    const session = pickedSession();
    const title = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!.title;
    open(session, { onRunUtterance, setSession: (update: (prev: ObservationSession) => ObservationSession) => sessions.push(update(session)) });
    const pill = (label: string) => [...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')].find((b) => b.textContent === label)!;
    act(() => pill('Your photo').click());
    expect(sessions).toHaveLength(0);
    expect(onRunUtterance).not.toHaveBeenCalled();
    act(() => pill('Real sky').click());
    expect(sessions).toHaveLength(1);
    expect(sessions[0]!.spatialView.mode).toBe('registered_sky');
    // Measured: the registered sky of a verified solve
    expect(sessions[0]!.lastStatusBanner).toMatch(/^Calibrated sky registered/);
    act(() => pill('3D').click());
    expect(onRunUtterance).toHaveBeenCalledTimes(1);
    expect(onRunUtterance).toHaveBeenCalledWith(`Take me to ${title}`, 'pointer');
  });

  it('keeps Real sky and 3D in place at 60 percent and not pressable when no star was matched, and opens the predicted sky from Show predicted sky', () => {
    const sessions: ObservationSession[] = [];
    const base = storedPhoto();
    const unmatched = { ...base, observationSolution: { ...PREDICTED_ONLY, poseEstimate: undefined }, selectedFindingId: null } as ObservationSession;
    open(unmatched, { setSession: (update: (prev: ObservationSession) => ObservationSession) => sessions.push(update(unmatched)) });
    const pills = [...q('.reveal-nav')!.querySelectorAll<HTMLButtonElement>('[role="group"] button')];
    expect(pills.map((b) => b.textContent)).toEqual(['Your photo', 'Real sky', '3D']);
    expect(pills.map((b) => b.disabled)).toEqual([false, true, true]);
    act(() => (q('.reveal-status button.look-main') as HTMLButtonElement).click());
    expect(sessions).toHaveLength(1);
    expect(sessions[0]!.spatialView.mode).toBe('registered_sky');
    expect(sessions[0]!.lastStatusBanner).toBe('Showing predicted sky model from recorded timestamp and location (separate from source pixels).');
  });

  it('shows no view pills in the 3D view, whose pieces are its own unit, the Sound circle staying', () => {
    const base = storedPhoto();
    open({ ...base, spatialView: { ...base.spatialView, mode: 'catalog_3d' } } as ObservationSession);
    expect(q('.reveal-nav [role="group"]')).toBeNull();
    expect(q('.reveal-nav .sound-control')).not.toBeNull();
  });

  it('shows no view pills on a phone', () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    open(storedPhoto());
    expect(q('.reveal-nav')).toBeNull();
    expect(q('[role="group"][aria-label="View"]')).toBeNull();
  });

  it('gives the ask bar the listening state, the words heard and the browser\'s ability, and sends typed words as text', () => {
    const onRunUtterance = vi.fn();
    const onPushToTalk = vi.fn();
    const base = storedPhoto();
    const listening = { ...base, machines: { ...base.machines, voice: 'listening' } } as ObservationSession;
    open(listening, { onRunUtterance, onPushToTalk, heardWords: { text: 'How far away is Vega', settled: false }, canListen: true });
    const ask = q('.ask-bar__ask') as HTMLButtonElement;
    expect(ask.classList.contains('is-listening')).toBe(true);
    expect(ask.innerHTML).toContain('How far away is <i>Vega</i>');
    act(() => ask.click());
    expect(onPushToTalk).toHaveBeenCalledTimes(1);
    renderViewport({ ...base, machines: { ...base.machines, voice: 'permission' } } as ObservationSession, { onRunUtterance, onPushToTalk, heardWords: null, canListen: true });
    expect(q('.ask-bar__ask')!.className).toContain('is-listening');
    expect(q('.ask-bar__ask')!.textContent).toBe('Ask out loud');
    renderViewport(base, { onRunUtterance, onPushToTalk, heardWords: null, canListen: false });
    expect(q('.ask-bar__ask')!.textContent).toBe('Type to ask');
    expect(q('.ask-bar__ask')!.className).not.toContain('is-listening');
    act(() => (q('.ask-bar__ask') as HTMLButtonElement).click());
    const input = q('.ask-bar__field input') as HTMLInputElement;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'Is that a plane?');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => (q('.ask-bar__field') as HTMLFormElement).requestSubmit());
    expect(onRunUtterance).toHaveBeenCalledWith('Is that a plane?', 'text');
  });

  it('keeps the old bar on a phone, and no ask bar', () => {
    (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({ matches: query.includes('max-width: 720px'), addEventListener: () => {}, removeEventListener: () => {} });
    open(storedPhoto());
    expect(q('[data-testid="ask-bar"]')).toBeNull();
    expect(q('[data-testid="voice-command-hud"]')).not.toBeNull();
  });

  it('runs Go there as the words "Take me to" the star, once per press, and clears the picked star on Close', () => {
    const onRunUtterance = vi.fn();
    const sessions: ObservationSession[] = [];
    const session = pickedSession();
    const title = session.observationSolution!.findings.find((f) => f.id === session.selectedFindingId)!.title;
    open(session, { onRunUtterance, setSession: (update: (prev: ObservationSession) => ObservationSession) => sessions.push(update(session)) });
    press('Go there');
    expect(onRunUtterance).toHaveBeenCalledTimes(1);
    expect(onRunUtterance).toHaveBeenCalledWith(`Take me to ${title}`, 'pointer');
    press('Close');
    expect(sessions.at(-1)!.selectedFindingId).toBeNull();
  });
});
