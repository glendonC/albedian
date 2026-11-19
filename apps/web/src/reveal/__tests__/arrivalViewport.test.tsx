// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { createEmptyObservationSession } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { HOME_SCREEN, choosePhotoOnHome } from '../../components/__tests__/homeEntry.js';
import type { Arrival } from '../arrival.js';

// The working screen in the app (unit 1c): while an upload is on its way to a solved sky the window shows the photo,
// bare, and the approved rows, and none of the pieces that come after the solve.

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
      '[data-testid="voice-command-hud"]',
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
    expect(q('[data-testid="voice-command-hud"]')).not.toBeNull();
    expect(q('[data-testid="source-photo-layer-label"]')).not.toBeNull();
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
