import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { orientStoredPoint, type DisplayOrientation, type ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { sourcePixelToViewport } from '../../components/sourcePhotoRect.js';
import { parsePanoramaRegistration, panoramaUv, sceneToIcrsRows, transposeRows, applyRows } from '../../spatial/sky/skyRegistration.js';
import { panoramaUvOfIcrs, realSkyGrid, realSkyResidualArcmin, skyAtScreen, storedPointOfUpright } from '../realSkyGrid.js';
import { canDrawRealSky, realSkyArea, realSkyShade } from '../RealSkyLayer.js';

// Real sky (reveal, approved 2026-11-12, laptop 2): ESO's Milky Way panorama (eso0932a) drawn for every screen pixel
// through the photo's solved WCS and the app's own panorama registration. These are the screen-to-sky and
// sky-to-panorama steps, checked against the solve's own matched stars.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
const here = path.dirname(fileURLToPath(import.meta.url));
const registration = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../public/textures/eso0932a_registration.json'), 'utf8')));
const VIEW = { w: 1440, h: 900 };
const calibrated = () => poseSession(sample('plate_calibrated') as ObservationSolution);

/** The angle between two sky positions, in arcseconds. */
function separationArcsec(a: { raDeg: number; decDeg: number }, b: { raDeg: number; decDeg: number }): number {
  const r = Math.PI / 180;
  const cos = Math.sin(a.decDeg * r) * Math.sin(b.decDeg * r) + Math.cos(a.decDeg * r) * Math.cos(b.decDeg * r) * Math.cos((a.raDeg - b.raDeg) * r);
  return (Math.acos(Math.min(1, Math.max(-1, cos))) / r) * 3600;
}

describe('storedPointOfUpright: the photo point back in the stored frame', () => {
  it('undoes the display orientation exactly, for every turn and mirror', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 3 }),
        fc.boolean(),
        fc.integer({ min: 1, max: 5000 }),
        fc.integer({ min: 1, max: 5000 }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (turns, mirrored, w, h, fx, fy) => {
          const orientation: DisplayOrientation = { quarterTurnsClockwise: turns as 0 | 1 | 2 | 3, mirrored };
          const shown = orientStoredPoint(fx * w, fy * h, w, h, orientation);
          const back = storedPointOfUpright(shown.xPx, shown.yPx, w, h, orientation);
          expect(back.xPx).toBeCloseTo(fx * w, 6);
          expect(back.yPx).toBeCloseTo(fy * h, 6);
        }
      ),
      SEED
    );
  });
});

describe('skyAtScreen: a screen point through the photo frame and the solve', () => {
  it("lands each matched star's mark on its catalog position, within the solve's own residual", () => {
    const session = calibrated();
    const solution = session.observationSolution!;
    const asset = session.sourceAsset!;
    const stars = solution.findings.filter((f) => f.evidenceClass === 'catalog_matched' && f.sourceDetection && f.catalogRef);
    expect(stars.length).toBeGreaterThan(2);
    const allowance = Math.max(30, 4 * (solution.quality!.medianResidualArcsec ?? solution.quality!.medianResidualPx! * solution.wcs!.scaleDegPerPx * 3600));
    for (const star of stars) {
      const mark = sourcePixelToViewport(star.sourceDetection!.xPx, star.sourceDetection!.yPx, asset.width, asset.height, VIEW.w, VIEW.h, session.cropTransform, asset.displayOrientation);
      const sky = skyAtScreen(session, mark.xPx, mark.yPx, VIEW.w, VIEW.h)!;
      expect(separationArcsec(sky, { raDeg: star.catalogRef!.raDeg, decDeg: star.catalogRef!.decDeg }), star.title).toBeLessThan(allowance);
    }
  });

  it('follows a photo shown upright from its side, letterboxed in the window, back through the turn', () => {
    const base = calibrated();
    // The same frame stored on its side: shown 540 by 960, pillarboxed in the 1440 by 900 window
    const session = { ...base, sourceAsset: { ...base.sourceAsset!, displayOrientation: { quarterTurnsClockwise: 1 as const, mirrored: false } } };
    const solution = session.observationSolution!;
    const asset = session.sourceAsset;
    const allowance = Math.max(30, 4 * (solution.quality!.medianResidualArcsec ?? solution.quality!.medianResidualPx! * solution.wcs!.scaleDegPerPx * 3600));
    for (const star of solution.findings.filter((f) => f.evidenceClass === 'catalog_matched' && f.sourceDetection && f.catalogRef)) {
      const mark = sourcePixelToViewport(star.sourceDetection!.xPx, star.sourceDetection!.yPx, asset.width, asset.height, VIEW.w, VIEW.h, session.cropTransform, asset.displayOrientation);
      expect(mark.xPx).toBeGreaterThan(400);
      const sky = skyAtScreen(session, mark.xPx, mark.yPx, VIEW.w, VIEW.h)!;
      expect(separationArcsec(sky, { raDeg: star.catalogRef!.raDeg, decDeg: star.catalogRef!.decDeg }), star.title).toBeLessThan(allowance);
    }
  });

  it('reaches past the photo the same way, and gives nothing without a verified solve', () => {
    const session = calibrated();
    expect(skyAtScreen(session, 5, 5, VIEW.w, VIEW.h)).not.toBeNull();
    const unverified = { ...session, observationSolution: { ...session.observationSolution!, quality: { ...session.observationSolution!.quality!, passedIndependentVerification: false } } };
    expect(skyAtScreen(unverified, 700, 450, VIEW.w, VIEW.h)).toBeNull();
    const noWcs = { ...session, observationSolution: { ...session.observationSolution!, wcs: undefined } };
    expect(skyAtScreen(noWcs, 700, 450, VIEW.w, VIEW.h)).toBeNull();
  });
});

describe('panoramaUvOfIcrs and the grid the layer draws', () => {
  it('places a sky direction in the panorama exactly as the 3D view does', () => {
    const icrsToScene = transposeRows(sceneToIcrsRows());
    for (const [ra, dec] of [
      [83.82, -5.39],
      [279.23, 38.78],
      [186.65, -63.1],
      [0.5, 0.2],
    ] as const) {
      const r = Math.PI / 180;
      const icrs: [number, number, number] = [Math.cos(dec * r) * Math.cos(ra * r), Math.cos(dec * r) * Math.sin(ra * r), Math.sin(dec * r)];
      const [u, v] = panoramaUvOfIcrs(icrs, registration);
      const [u3, v3] = panoramaUv(applyRows(icrsToScene, icrs), registration);
      expect(u).toBeCloseTo(u3, 10);
      expect(v).toBeCloseTo(v3, 10);
    }
  });

  it('covers the whole window in cells of the given size, with no cell spanning the panorama seam', () => {
    const grid = realSkyGrid(calibrated(), VIEW.w, VIEW.h, registration, 16)!;
    expect(grid.cols).toBe(Math.ceil(VIEW.w / 16) + 1);
    expect(grid.rows).toBe(Math.ceil(VIEW.h / 16) + 1);
    expect(grid.positions.length).toBe(grid.cols * grid.rows * 2);
    expect(grid.uvs.length).toBe(grid.cols * grid.rows * 2);
    expect([grid.positions[0], grid.positions[1]]).toEqual([0, 0]);
    expect(grid.positions[grid.positions.length - 2]).toBeGreaterThanOrEqual(VIEW.w);
    expect(grid.positions[grid.positions.length - 1]).toBeGreaterThanOrEqual(VIEW.h);
    // Neighbours sit close in the panorama: u is carried past 0 or 1 rather than jumping across the image
    for (let j = 0; j < grid.rows; j++) {
      for (let i = 1; i < grid.cols; i++) {
        const a = (j * grid.cols + i - 1) * 2;
        const b = (j * grid.cols + i) * 2;
        expect(Math.abs(grid.uvs[b]! - grid.uvs[a]!)).toBeLessThan(0.05);
      }
    }
    expect(realSkyGrid({ ...calibrated(), observationSolution: undefined }, VIEW.w, VIEW.h, registration)).toBeNull();
  });

  it('covers the strip a move uncovers, each point showing the sky the window point shows (arrival-star-1)', () => {
    // A move of 64 px right and 32 px up uncovers 64 px at the left and 32 px at the foot
    const area = realSkyArea(VIEW.w, VIEW.h, [64, -32]);
    expect(area).toEqual({ x0: -64, y0: 0, widthPx: VIEW.w + 64, heightPx: VIEW.h + 32 });
    expect(realSkyArea(VIEW.w, VIEW.h, null)).toEqual({ x0: 0, y0: 0, widthPx: VIEW.w, heightPx: VIEW.h });
    expect(realSkyArea(VIEW.w, VIEW.h, [-10, 20])).toEqual({ x0: 0, y0: -20, widthPx: VIEW.w + 10, heightPx: VIEW.h + 20 });
    const grid = realSkyGrid(calibrated(), VIEW.w, VIEW.h, registration, 16, area)!;
    const plain = realSkyGrid(calibrated(), VIEW.w, VIEW.h, registration, 16)!;
    expect([grid.positions[0], grid.positions[1]]).toEqual([-64, 0]);
    expect(grid.cols).toBe(Math.ceil((VIEW.w + 64) / 16) + 1);
    expect(grid.rows).toBe(Math.ceil((VIEW.h + 32) / 16) + 1);
    // The window's own grid points carry the same sky as before: 64 px is four cells in
    for (const [i, j] of [[0, 0], [5, 3], [20, 10]] as const) {
      const a = (j * plain.cols + i) * 2;
      const b = (j * grid.cols + i + 4) * 2;
      expect([grid.positions[b], grid.positions[b + 1]]).toEqual([plain.positions[a], plain.positions[a + 1]]);
      expect(grid.uvs[b]! - Math.round(grid.uvs[b]! - plain.uvs[a]!)).toBeCloseTo(plain.uvs[a]!, 9);
      expect(grid.uvs[b + 1]).toBeCloseTo(plain.uvs[a + 1]!, 9);
    }
  });
});

describe('realSkyResidualArcmin: the "within" of the status sentence', () => {
  it("is the solve's median residual in whole arcminutes, rounded up so the sentence never claims more than it has", () => {
    const base = sample('plate_calibrated') as ObservationSolution;
    const at = (arcsec: number | undefined, px?: number) => realSkyResidualArcmin({ ...base, quality: { ...base.quality!, medianResidualArcsec: arcsec, medianResidualPx: px ?? base.quality!.medianResidualPx } });
    expect(at(240)).toBe(4);
    expect(at(181)).toBe(4);
    expect(at(30)).toBe(1);
    expect(at(0)).toBe(1);
    // With no residual in arcseconds, the pixel residual through the solve's scale
    expect(at(undefined, 2)).toBe(Math.max(1, Math.ceil((2 * base.wcs!.scaleDegPerPx * 3600) / 60)));
    expect(realSkyResidualArcmin({ ...base, quality: undefined })).toBeNull();
  });
});

describe('the layer around the panorama', () => {
  it("shades the side columns as the approved page does, its stops at the photo's own frame", () => {
    // The approved page: a 675 px photo column from 382.5 px in a 1440 px window
    expect(realSkyShade({ leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 }, 1440)).toBe(
      'linear-gradient(to right, rgba(0, 0, 0, 0.74) 0px, rgba(0, 0, 0, 0.5) 300.5px, rgba(0, 0, 0, 0) 382.5px, rgba(0, 0, 0, 0) 1057.5px, rgba(0, 0, 0, 0.5) 1139.5px, rgba(0, 0, 0, 0.74) 1440px)'
    );
  });

  it('says it can draw only where the browser has WebGL 2', () => {
    expect(canDrawRealSky()).toBe('WebGL2RenderingContext' in globalThis);
  });
});
