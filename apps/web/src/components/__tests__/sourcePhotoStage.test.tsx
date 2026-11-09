// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import type { ObservationSession, RenderLayerSet } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

const MODELED_LAYERS: RenderLayerSet = [
  {
    id: 'layer_modeled_star',
    kind: 'modeled_body',
    title: 'Modeled star',
    evidenceClass: 'simulated',
    disclosure: 'Drawn by the app, not measured in the photo.',
  },
] as unknown as RenderLayerSet;

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return MODELED_LAYERS;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));

vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => false),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));

vi.mock('../../audio/SkyAudioFeed.js', () => ({
  skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() },
}));

vi.mock('../CoalescingBar.js', () => ({
  CoalescingBar: () => null,
}));

const { fitSourceToViewport, sourcePhotoDisplayRect, sourcePixelToViewport, isSourcePhotoShown } =
  await import('../sourcePhotoRect.js');
const { SourcePhotoStage } = await import('../SourcePhotoStage.js');
const { projectFindingToViewport } = await import('../CelestialMarkerOverlay.js');
const { SpatialObservatoryViewport } = await import('../SpatialObservatoryViewport.js');

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** A phone photo the way an upload leaves it: a still with its measured size and a local URL. */
function uploadedPhotoSession(overrides: Partial<ObservationSession> = {}): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: {
      ...base.sourceAsset!,
      mediaKind: 'image',
      mimeType: 'image/jpeg',
      uri: 'blob:http://localhost:5189/uploaded-photo',
      width: 480,
      height: 360,
      durationMs: 0,
    },
    spatialView: { ...base.spatialView, mode: 'source_2d' },
    ...overrides,
  } as ObservationSession;
}

function renderViewport(session: ObservationSession, uploadedMediaUrl: string | null) {
  const noop = () => {};
  root.render(
    createElement(SpatialObservatoryViewport, {
      session,
      setSession: noop,
      selectedFinding: null,
      discoveredMedia: [],
      activeDiscoveredId: null,
      setActiveDiscoveredId: noop,
      activeDiscoveredItem: null,
      uploadedMediaUrl,
      setUploadedMediaUrl: noop,
      commandText: '',
      setCommandText: noop,
      onRunUtterance: noop,
      onSelectFinding: noop,
      onPushToTalk: noop,
      onTriggerUpload: noop,
    })
  );
}

describe('sourcePhotoRect (experience-9a)', () => {
  it('fits a landscape phone photo inside a phone screen and a laptop screen with its shape kept', () => {
    // 480x360 on 390x844: full width, letterboxed above and below
    expect(fitSourceToViewport(480, 360, 390, 844)).toEqual({
      leftPx: 0,
      topPx: 275.75,
      widthPx: 390,
      heightPx: 292.5,
    });
    // 480x360 on 1440x900: full height, pillarboxed left and right
    expect(fitSourceToViewport(480, 360, 1440, 900)).toEqual({
      leftPx: 120,
      topPx: 0,
      widthPx: 1200,
      heightPx: 900,
    });
  });

  it('zooms about the photo centre and pans by fractions of the fitted photo', () => {
    const rect = sourcePhotoDisplayRect(480, 360, 1440, 900, { zoom: 2, panX: 0.1, panY: 0 });
    expect(rect.widthPx).toBeCloseTo(2400, 6);
    expect(rect.heightPx).toBeCloseTo(1800, 6);
    // Centre moves from x=720 by 0.1 of the fitted 1200 px width
    expect(rect.leftPx + rect.widthPx / 2).toBeCloseTo(840, 6);
    expect(rect.topPx + rect.heightPx / 2).toBeCloseTo(450, 6);
  });

  it('maps the photo corners onto the drawn photo corners', () => {
    expect(sourcePixelToViewport(0, 0, 480, 360, 390, 844)).toEqual({ xPx: 0, yPx: 275.75 });
    expect(sourcePixelToViewport(480, 360, 480, 360, 390, 844)).toEqual({ xPx: 390, yPx: 568.25 });
  });

  it('keeps every photo pixel inside the drawn photo, which stays on screen and keeps its aspect (property)', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 12000 }),
        fc.integer({ min: 1, max: 12000 }),
        fc.integer({ min: 1, max: 4000 }),
        fc.integer({ min: 1, max: 4000 }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (srcW, srcH, viewW, viewH, fu, fv) => {
          const rect = fitSourceToViewport(srcW, srcH, viewW, viewH);
          const eps = 1e-6 * Math.max(viewW, viewH);
          expect(rect.leftPx).toBeGreaterThanOrEqual(-eps);
          expect(rect.topPx).toBeGreaterThanOrEqual(-eps);
          expect(rect.leftPx + rect.widthPx).toBeLessThanOrEqual(viewW + eps);
          expect(rect.topPx + rect.heightPx).toBeLessThanOrEqual(viewH + eps);
          // Touches the screen on at least one axis
          const fillsWidth = Math.abs(rect.widthPx - viewW) <= eps;
          const fillsHeight = Math.abs(rect.heightPx - viewH) <= eps;
          expect(fillsWidth || fillsHeight).toBe(true);
          // Same shape as the photo
          expect(rect.widthPx * srcH).toBeCloseTo(rect.heightPx * srcW, 0);
          const p = sourcePixelToViewport(fu * srcW, fv * srcH, srcW, srcH, viewW, viewH);
          expect(p.xPx).toBeGreaterThanOrEqual(rect.leftPx - eps);
          expect(p.xPx).toBeLessThanOrEqual(rect.leftPx + rect.widthPx + eps);
          expect(p.yPx).toBeGreaterThanOrEqual(rect.topPx - eps);
          expect(p.yPx).toBeLessThanOrEqual(rect.topPx + rect.heightPx + eps);
        }
      ),
      {
        seed: SEED,
        examples: [
          [480, 360, 390, 844, 0, 0],
          [480, 360, 1440, 900, 1, 1],
          [1, 1, 1, 1, 0.5, 0.5],
          [12000, 1, 1, 4000, 1, 0],
        ],
      }
    );
  });

  it('shows the photo only in the source view of a session with a source asset', () => {
    const session = uploadedPhotoSession();
    expect(isSourcePhotoShown(session)).toBe(true);
    expect(
      isSourcePhotoShown({ ...session, spatialView: { ...session.spatialView, mode: 'catalog_3d' } })
    ).toBe(false);
    expect(
      isSourcePhotoShown({ ...session, spatialView: { ...session.spatialView, mode: 'registered_sky' } })
    ).toBe(false);
    const { sourceAsset: _omit, ...withoutAsset } = session;
    expect(isSourcePhotoShown(withoutAsset as ObservationSession)).toBe(false);
  });
});

describe('SourcePhotoStage (experience-9a)', () => {
  it('draws the uploaded photo from its own URL, untouched, inside the fitted rectangle, and labels it observed', () => {
    const session = uploadedPhotoSession();
    act(() => {
      root.render(
        createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844 })
      );
    });
    const stage = container.querySelector('[data-testid="source-photo-stage"]') as HTMLElement;
    expect(stage).not.toBeNull();
    expect(stage.dataset.evidenceClass).toBe('observed');
    const img = stage.querySelector('img') as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute('src')).toBe('blob:http://localhost:5189/uploaded-photo');
    expect(img.style.left).toBe('0px');
    expect(img.style.top).toBe('275.75px');
    expect(img.style.width).toBe('390px');
    expect(img.style.height).toBe('292.5px');
    // Original pixels: nothing filters, blends or re-tones the photo
    expect(img.style.filter).toBe('');
    expect(img.style.mixBlendMode).toBe('');
    expect(img.style.opacity).toBe('');
    // The stage covers the 3D canvas so no modeled scene shows around or through the photo
    expect(stage.style.position).toBe('fixed');
    expect(stage.style.backgroundColor).not.toBe('');
    const label = container.querySelector('[data-testid="source-photo-layer-label"]') as HTMLElement;
    expect(label.textContent).toContain('OBS');
    expect(label.textContent).toContain('Your photo');
    expect(label.textContent).toContain('original pixels');
  });

  it('draws a clip as a video at the solved frame', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'video', mimeType: 'video/mp4', durationMs: 12000 },
    } as ObservationSession;
    act(() => {
      root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 }));
    });
    const video = container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video.getAttribute('src')).toBe('blob:http://localhost:5189/uploaded-photo');
    expect(video.muted).toBe(true);
    expect(container.querySelector('[data-testid="source-photo-stage"] img')).toBeNull();
  });

  it('says plainly when the browser cannot show the file instead of a broken image', () => {
    const base = uploadedPhotoSession();
    const session = { ...base, sourceAsset: { ...base.sourceAsset!, mimeType: 'image/heic' } } as ObservationSession;
    act(() => {
      root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844 }));
    });
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    act(() => {
      img.dispatchEvent(new Event('error'));
    });
    expect(container.querySelector('[data-testid="source-photo-stage"] img')).toBeNull();
    const notice = container.querySelector('[data-testid="source-photo-unavailable"]') as HTMLElement;
    expect(notice).not.toBeNull();
    expect(notice.textContent).toContain('image/heic');
  });

  it('draws nothing outside the source view', () => {
    const base = uploadedPhotoSession();
    const session = { ...base, spatialView: { ...base.spatialView, mode: 'catalog_3d' } } as ObservationSession;
    act(() => {
      root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844 }));
    });
    expect(container.querySelector('[data-testid="source-photo-stage"]')).toBeNull();
  });

  it('puts each marker on the photo pixel it was measured at', () => {
    const session = uploadedPhotoSession();
    const finding = {
      ...session.observationSolution!.findings[0]!,
      sourceDetection: {
        assetId: session.sourceAsset!.id,
        frameTimeSec: 0,
        xPx: 120,
        yPx: 270,
        flux: 900,
        snr: 30,
        fwhmPx: 3,
        centroidUncertaintyPx: 0.1,
      },
    };
    const pos = projectFindingToViewport(finding, session, 390, 844);
    const expected = sourcePixelToViewport(120, 270, 480, 360, 390, 844);
    expect(pos.visible).toBe(true);
    expect(pos.xPct).toBeCloseTo((expected.xPx / 390) * 100, 6);
    expect(pos.yPct).toBeCloseTo((expected.yPx / 844) * 100, 6);
  });
});

describe('SpatialObservatoryViewport upload path (experience-9a)', () => {
  it('leaves the landing and shows the photo when a file is uploaded, and hides the modeled-scene credits behind it', () => {
    const session = uploadedPhotoSession();
    act(() => renderViewport(session, null));
    expect(container.querySelector('[data-testid="enter-observatory-btn"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="source-photo-stage"]')).toBeNull();

    act(() => renderViewport(session, 'blob:http://localhost:5189/uploaded-photo'));
    expect(container.querySelector('[data-testid="enter-observatory-btn"]')).toBeNull();
    expect(container.querySelector('[data-testid="source-photo-stage"]')).not.toBeNull();
    // The canvas is covered by the photo, so the modeled-scene disclosure would describe nothing on screen
    expect(container.querySelector('.alb-layer-disclosure-toggle')).toBeNull();
  });

  it('keeps the modeled-scene credits while the 3D view is showing', () => {
    const base = uploadedPhotoSession();
    const session = { ...base, spatialView: { ...base.spatialView, mode: 'catalog_3d' } } as ObservationSession;
    act(() => renderViewport(session, 'blob:http://localhost:5189/uploaded-photo'));
    expect(container.querySelector('[data-testid="source-photo-stage"]')).toBeNull();
    expect(container.querySelector('.alb-layer-disclosure-toggle')).not.toBeNull();
  });

  it('stays on the landing when nothing was uploaded', () => {
    act(() => renderViewport(uploadedPhotoSession(), null));
    expect(container.querySelector('[data-testid="enter-observatory-btn"]')).not.toBeNull();
    expect(container.querySelector('.alb-layer-disclosure-toggle')).not.toBeNull();
  });
});
