// @vitest-environment jsdom
import { Fragment, act, createElement } from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import { EXIF_ORIENTATIONS, invertDisplayOrientation } from '@albedian/contracts';
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

const {
  decodedFrameMatches,
  fitSourceToViewport,
  sourcePhotoDisplayRect,
  sourcePixelToViewport,
  isSourcePhotoShown,
  orientedSize,
  orientStoredPoint,
} = await import('../sourcePhotoRect.js');
const { SourcePhotoStage } = await import('../SourcePhotoStage.js');
const { CelestialMarkerOverlay, projectFindingToViewport } = await import('../CelestialMarkerOverlay.js');
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
    // Detections are in array indices, pixel centres at whole numbers, so the photo's corners are at -0.5 and size - 0.5
    expect(sourcePixelToViewport(-0.5, -0.5, 480, 360, 390, 844)).toEqual({ xPx: 0, yPx: 275.75 });
    expect(sourcePixelToViewport(479.5, 359.5, 480, 360, 390, 844)).toEqual({ xPx: 390, yPx: 568.25 });
  });

  it('puts a detection at the centre of its pixel, as the scan card crops it (centroid plus 0.5, experience-9h)', () => {
    // A 4 x 2 photo drawn 100 px to the source pixel in a 400 x 200 screen
    expect(sourcePixelToViewport(0, 0, 4, 2, 400, 200)).toEqual({ xPx: 50, yPx: 50 });
    expect(sourcePixelToViewport(3, 1, 4, 2, 400, 200)).toEqual({ xPx: 350, yPx: 150 });
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
          // Any detection on the photo, from its first pixel's outer edge to its last's
          const p = sourcePixelToViewport(fu * srcW - 0.5, fv * srcH - 0.5, srcW, srcH, viewW, viewH);
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
    // Original pixels: nothing filters, blends or re-tones the clip either
    expect(video.style.filter).toBe('');
    expect(video.style.mixBlendMode).toBe('');
    expect(video.style.opacity).toBe('');
    expect(video.style.transform).toBe('');
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
    // Nothing is on screen, so the layer label does not say the photo is shown as uploaded
    const label = container.querySelector('[data-testid="source-photo-layer-label"]') as HTMLElement;
    expect(label.textContent).not.toContain('Shown as uploaded');
    expect(label.textContent).not.toContain('original pixels');
    expect(label.textContent).toContain('not shown by this browser');
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

describe('display orientation (experience-9e)', () => {
  it('turns Orientation 6 a quarter clockwise: stored top-left lands top-right and the frame becomes portrait', () => {
    const o6 = EXIF_ORIENTATIONS[6];
    expect(orientedSize(4032, 3024, o6)).toEqual({ widthPx: 3024, heightPx: 4032 });
    expect(orientStoredPoint(0, 0, 4032, 3024, o6)).toEqual({ xPx: 3024, yPx: 0 });
    expect(orientStoredPoint(0, 3024, 4032, 3024, o6)).toEqual({ xPx: 0, yPx: 0 });
    expect(orientStoredPoint(4032, 0, 4032, 3024, o6)).toEqual({ xPx: 3024, yPx: 4032 });
  });

  it('turns Orientation 8 a quarter anticlockwise: stored top-left lands bottom-left', () => {
    const o8 = EXIF_ORIENTATIONS[8];
    expect(orientedSize(4032, 3024, o8)).toEqual({ widthPx: 3024, heightPx: 4032 });
    expect(orientStoredPoint(0, 0, 4032, 3024, o8)).toEqual({ xPx: 0, yPx: 4032 });
    expect(orientStoredPoint(4032, 0, 4032, 3024, o8)).toEqual({ xPx: 0, yPx: 0 });
  });

  it('matches the EXIF meaning of all eight orientations in the contract table', () => {
    // Where the stored top-left (0,0) and top-right (W,0) corners of a 40 x 30 frame are shown
    const expected: Record<number, [[number, number], [number, number]]> = {
      1: [[0, 0], [40, 0]], // as stored
      2: [[40, 0], [0, 0]], // mirrored left to right
      3: [[40, 30], [0, 30]], // half turn
      4: [[0, 30], [40, 30]], // mirrored top to bottom
      5: [[0, 0], [0, 40]], // transposed
      6: [[30, 0], [30, 40]], // quarter turn clockwise
      7: [[30, 40], [30, 0]], // transverse
      8: [[0, 40], [0, 0]], // quarter turn anticlockwise
    };
    for (const tag of [1, 2, 3, 4, 5, 6, 7, 8] as const) {
      const o = EXIF_ORIENTATIONS[tag];
      const [topLeft, topRight] = expected[tag]!;
      expect(orientStoredPoint(0, 0, 40, 30, o), `Orientation ${tag} top-left`).toEqual({
        xPx: topLeft[0],
        yPx: topLeft[1],
      });
      expect(orientStoredPoint(40, 0, 40, 30, o), `Orientation ${tag} top-right`).toEqual({
        xPx: topRight[0],
        yPx: topRight[1],
      });
      const turned = tag >= 5;
      expect(orientedSize(40, 30, o)).toEqual(turned ? { widthPx: 30, heightPx: 40 } : { widthPx: 40, heightPx: 30 });
    }
  });

  it('returns every pixel to where it was stored when the inverse orientation is applied (property)', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 9000 }),
        fc.integer({ min: 1, max: 9000 }),
        fc.integer({ min: 0, max: 3 }),
        fc.boolean(),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (w, h, quarterTurnsClockwise, mirrored, fu, fv) => {
          const o = { quarterTurnsClockwise, mirrored };
          const shown = orientedSize(w, h, o);
          const p = orientStoredPoint(fu * w, fv * h, w, h, o);
          expect(p.xPx).toBeGreaterThanOrEqual(0);
          expect(p.xPx).toBeLessThanOrEqual(shown.widthPx);
          expect(p.yPx).toBeGreaterThanOrEqual(0);
          expect(p.yPx).toBeLessThanOrEqual(shown.heightPx);
          const back = orientStoredPoint(p.xPx, p.yPx, shown.widthPx, shown.heightPx, invertDisplayOrientation(o));
          expect(back.xPx).toBeCloseTo(fu * w, 6);
          expect(back.yPx).toBeCloseTo(fv * h, 6);
        }
      ),
      {
        seed: SEED,
        examples: [
          [4032, 3024, 1, false, 0, 0],
          [4032, 3024, 3, false, 1, 0],
          [4032, 3024, 3, true, 0.25, 0.75],
          [1, 1, 2, true, 0.5, 0.5],
        ],
      }
    );
  });

  it('draws an Orientation 6 photo in a portrait box and puts its markers through the same turn', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    act(() => {
      root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844 }));
    });
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    // 480 x 360 stored, shown 360 x 480, fitted to 390 wide: 390 x 520 from y = 162
    expect(img.style.width).toBe('390px');
    expect(img.style.height).toBe('520px');
    expect(img.style.left).toBe('0px');
    expect(img.style.top).toBe('162px');
    // The browser turns the photo itself by its orientation tag, set inline so no stylesheet can undo it
    expect(img.style.getPropertyValue('image-orientation')).toBe('from-image');
    expect(img.style.transform).toBe('');

    const finding = {
      ...session.observationSolution!.findings[0]!,
      sourceDetection: {
        assetId: session.sourceAsset!.id,
        frameTimeSec: 0,
        // The stored photo's top-left corner (pixel centres are at whole numbers)
        xPx: -0.5,
        yPx: -0.5,
        flux: 900,
        snr: 30,
        fwhmPx: 3,
        centroidUncertaintyPx: 0.1,
      },
    };
    // Stored top-left is shown top-right
    const pos = projectFindingToViewport(finding, session, 390, 844);
    expect(pos.xPx).toBe(390);
    expect(pos.yPx).toBe(162);
  });

  it('says so and hides the markers when the browser shows a different frame shape than the orientation implies', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    const onFrameMismatch = vi.fn();
    act(() => {
      root.render(
        createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844, onFrameMismatch })
      );
    });
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    // The browser decoded it landscape (480 x 360) although the asset says it is shown portrait
    Object.defineProperty(img, 'naturalWidth', { value: 480 });
    Object.defineProperty(img, 'naturalHeight', { value: 360 });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
    const notice = container.querySelector('[data-testid="source-photo-orientation-mismatch"]') as HTMLElement;
    expect(notice).not.toBeNull();
    expect(notice.textContent).toContain('turned the photo');
    expect(onFrameMismatch).toHaveBeenLastCalledWith(true);
    // The box takes the shape the browser drew (480 x 360 fitted to 390 wide), not the tag's portrait shape
    expect(img.style.width).toBe('390px');
    expect(img.style.height).toBe('292.5px');
    expect(img.style.top).toBe('275.75px');
    // The label claims neither the measured orientation nor marker placement while they disagree
    const label = container.querySelector('[data-testid="source-photo-layer-label"]') as HTMLElement;
    expect(label.textContent).not.toContain('original pixels');
    expect(label.textContent).not.toContain('Each marker');
  });

  it('says clip in the mismatch notice for a clip (experience-9h)', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'video', mimeType: 'video/quicktime', durationMs: 12000, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    act(() => {
      root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844 }));
    });
    const video = container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    Object.defineProperty(video, 'videoWidth', { value: 480 });
    Object.defineProperty(video, 'videoHeight', { value: 360 });
    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    const notice = container.querySelector('[data-testid="source-photo-orientation-mismatch"]') as HTMLElement;
    expect(notice.textContent).toContain('turned the clip');
    expect(notice.textContent).not.toContain('photo');
  });

  it('reports no mismatch when the browser shows the oriented shape', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    const onFrameMismatch = vi.fn();
    act(() => {
      root.render(
        createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844, onFrameMismatch })
      );
    });
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 360 });
    Object.defineProperty(img, 'naturalHeight', { value: 480 });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
    expect(container.querySelector('[data-testid="source-photo-orientation-mismatch"]')).toBeNull();
    expect(onFrameMismatch).toHaveBeenLastCalledWith(false);
  });

  it('matches a decoded frame within 1 percent of the shown shape and no further (experience-9h)', () => {
    // Shown 360 x 480 (aspect 0.75); a frame a pixel off from rounding still matches
    expect(decodedFrameMatches(360, 481, 360, 480)).toBe(true);
    // Just inside and just outside 1 percent of 0.75, on both sides
    expect(decodedFrameMatches(3635, 4800, 360, 480)).toBe(true);
    expect(decodedFrameMatches(3637, 4800, 360, 480)).toBe(false);
    expect(decodedFrameMatches(3565, 4800, 360, 480)).toBe(true);
    expect(decodedFrameMatches(3563, 4800, 360, 480)).toBe(false);
    // The other shape is far outside
    expect(decodedFrameMatches(480, 360, 360, 480)).toBe(false);
  });

  it('keeps the markers when a clip reports no decoded size yet (0 x 0 metadata)', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: {
        ...base.sourceAsset!,
        mediaKind: 'video',
        mimeType: 'video/quicktime',
        durationMs: 12000,
        displayOrientation: EXIF_ORIENTATIONS[6],
      },
    } as ObservationSession;
    const onFrameMismatch = vi.fn();
    act(() => {
      root.render(
        createElement(SourcePhotoStage, { session, viewportWidthPx: 390, viewportHeightPx: 844, onFrameMismatch })
      );
    });
    const video = container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
    Object.defineProperty(video, 'videoWidth', { value: 0 });
    Object.defineProperty(video, 'videoHeight', { value: 0 });
    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(decodedFrameMatches(0, 0, 360, 480)).toBe(true);
    expect(container.querySelector('[data-testid="source-photo-orientation-mismatch"]')).toBeNull();
    expect(onFrameMismatch).toHaveBeenLastCalledWith(false);
  });
});

describe('SpatialObservatoryViewport orientation mismatch (experience-9e)', () => {
  it('hides the markers over a photo the browser turned differently, and keeps them when it agrees', () => {
    const base = uploadedPhotoSession();
    const session = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    act(() => renderViewport(session, 'blob:http://localhost:5189/uploaded-photo'));
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).not.toBeNull();

    const loadAs = (w: number, h: number) => {
      const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
      Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true });
      Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true });
      act(() => {
        img.dispatchEvent(new Event('load'));
      });
    };
    loadAs(480, 360);
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).toBeNull();
    loadAs(360, 480);
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).not.toBeNull();
  });

  it('forgets a mismatch when a new photo arrives, before it loads (experience-9h)', () => {
    const base = uploadedPhotoSession();
    const turned = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, displayOrientation: EXIF_ORIENTATIONS[6] },
    } as ObservationSession;
    act(() => renderViewport(turned, 'blob:http://localhost:5189/uploaded-photo'));
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    Object.defineProperty(img, 'naturalWidth', { value: 480, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 360, configurable: true });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).toBeNull();
    const next = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, id: 'src_next_photo', uri: 'blob:http://localhost:5189/next-photo' },
    } as ObservationSession;
    act(() => renderViewport(next, 'blob:http://localhost:5189/next-photo'));
    expect(container.querySelector('[data-testid="celestial-marker-overlay"]')).not.toBeNull();
  });
});

describe('auditor fixes on the stage (experience-9e)', () => {
  it('seeks a clip to the solved frame again when the source view comes back', () => {
    const base = uploadedPhotoSession();
    const clip = {
      ...base,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'video', mimeType: 'video/mp4', durationMs: 12000 },
      observationSolution: { ...base.observationSolution!, frameTimeSec: 4.5 },
    } as ObservationSession;
    const away = { ...clip, spatialView: { ...clip.spatialView, mode: 'catalog_3d' } } as ObservationSession;
    const show = (session: ObservationSession) =>
      act(() => {
        root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 }));
      });
    const loadedVideo = () => {
      const video = container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
      act(() => {
        video.dispatchEvent(new Event('loadedmetadata'));
      });
      return video;
    };

    show(clip);
    const loaded = loadedVideo();
    expect(loaded.currentTime).toBe(4.5);
    // A new solve of the same clip on another frame moves the loaded clip there too (experience-9h)
    Object.defineProperty(loaded, 'readyState', { value: 1, configurable: true });
    show({ ...clip, observationSolution: { ...clip.observationSolution!, frameTimeSec: 7.25 } } as ObservationSession);
    expect(loaded.currentTime).toBe(7.25);
    show(away);
    expect(container.querySelector('[data-testid="source-photo-stage"] video')).toBeNull();
    // Back in the source view the clip is a new element, which must land on the solved frame too
    show(clip);
    expect(loadedVideo().currentTime).toBe(4.5);
  });

  it('keeps each marker on its photo pixel with the photo zoomed and panned, stage and overlay drawn together', () => {
    const base = uploadedPhotoSession();
    const marked = {
      ...base.observationSolution!.findings[0]!,
      sourceDetection: {
        assetId: base.sourceAsset!.id,
        frameTimeSec: 0,
        xPx: 260,
        yPx: 170,
        flux: 900,
        snr: 30,
        fwhmPx: 3,
        centroidUncertaintyPx: 0.1,
      },
    };
    const session = {
      ...base,
      cropTransform: { zoom: 2, panX: 0.1, panY: -0.05 },
      observationSolution: { ...base.observationSolution!, findings: [marked] },
    } as ObservationSession;
    act(() => {
      root.render(
        createElement(
          Fragment,
          null,
          createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 }),
          createElement(CelestialMarkerOverlay, {
            session,
            selectedFinding: null,
            onSelectFinding: () => {},
            viewportWidthPx: 1440,
            viewportHeightPx: 900,
          })
        )
      );
    });
    const img = container.querySelector('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    const imgLeft = parseFloat(img.style.left);
    const imgTop = parseFloat(img.style.top);
    const imgW = parseFloat(img.style.width);
    const imgH = parseFloat(img.style.height);
    // Zoomed: the drawn photo is larger than the screen
    expect(imgW).toBeGreaterThan(1440);
    const marker = container.querySelector(`[data-testid="celestial-marker-${marked.id}"]`) as HTMLElement;
    expect(marker).not.toBeNull();
    const markerX = (parseFloat(marker.style.left) / 100) * 1440;
    const markerY = (parseFloat(marker.style.top) / 100) * 900;
    // Where the centre of pixel (260, 170) of the 480 x 360 photo is drawn; the overlay rounds to 0.01 % of the screen
    expect(Math.abs(markerX - (imgLeft + (260.5 / 480) * imgW))).toBeLessThan(0.15);
    expect(Math.abs(markerY - (imgTop + (170.5 / 360) * imgH))).toBeLessThan(0.1);
  });

  it('draws the stage above the 3D canvas host and under the markers', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '../../styles/albedian.css'), 'utf8');
    const zIndexOf = (rule: RegExp) => Number(rule.exec(css)?.[1]);
    const canvasZ = zIndexOf(/\.alb-canvas-host\s*\{[^}]*z-index:\s*(\d+)/);
    const markersZ = zIndexOf(/\.alb-marker-overlay\s*\{[^}]*z-index:\s*(\d+)/);
    expect(Number.isFinite(canvasZ)).toBe(true);
    expect(Number.isFinite(markersZ)).toBe(true);
    act(() => {
      root.render(
        createElement(SourcePhotoStage, { session: uploadedPhotoSession(), viewportWidthPx: 390, viewportHeightPx: 844 })
      );
    });
    const stage = container.querySelector('[data-testid="source-photo-stage"]') as HTMLElement;
    const stageZ = Number(stage.style.zIndex);
    expect(stageZ).toBeGreaterThan(canvasZ);
    expect(stageZ).toBeLessThan(markersZ);
  });
});
