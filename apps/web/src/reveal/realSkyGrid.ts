import { hasVerifiedWcs, orientedSize, type DisplayOrientation, type ObservationSession, type ObservationSolution, type SkyView } from '@albedian/contracts';
import { wcsOfSkyView } from '../spatial/flight/skyViewWcs.js';
import { pixelToWorldWCS, raDecToUnitVector, type Vec3 } from '@albedian/camera-rig';
import { sourcePhotoDisplayRect } from '../components/sourcePhotoRect.js';
import { applyRows, panoramaUv, sceneToIcrsRows, transposeRows, type PanoramaRegistration } from '../spatial/sky/skyRegistration.js';

/**
 * Real sky (reveal, approved 2026-11-12, laptop 2): ESO's Milky Way panorama (eso0932a) drawn for every screen pixel
 * through the photo's solved WCS and the app's own panorama registration, running past the photo's edges. A screen
 * point goes to the photo (the same rectangle the photo is drawn in, carried past its edges), back to the stored frame,
 * through the solve to the sky, and through the registration into the panorama. Only over a verified solve.
 */

/** Undoes `orientStoredPoint`: a point on the photo as shown, in the stored frame. */
export function storedPointOfUpright(
  xPx: number,
  yPx: number,
  storedWidthPx: number,
  storedHeightPx: number,
  orientation?: DisplayOrientation
): { xPx: number; yPx: number } {
  const turns = orientation?.quarterTurnsClockwise ?? 0;
  // The shown frame's size after the turns, then each turn undone: a counter-clockwise quarter turn sends (x, y) in a
  // width x height frame to (y, width - x) in height x width
  let width = turns % 2 === 1 ? storedHeightPx : storedWidthPx;
  let height = turns % 2 === 1 ? storedWidthPx : storedHeightPx;
  let x = xPx;
  let y = yPx;
  for (let turn = 0; turn < turns; turn++) {
    [x, y] = [y, width - x];
    [width, height] = [height, width];
  }
  return { xPx: orientation?.mirrored ? storedWidthPx - x : x, yPx: y };
}

/**
 * The verified solve of the session, or none; with `pose`, go-there-predicted-1 (ruling A): a pose estimate's own
 * nominal WCS too, for the flight's starless glow at a predicted star (simulated, as the glow always is), never for
 * ESO's photograph.
 */
function verifiedSolve(session: ObservationSession, pose = false): ObservationSolution | null {
  const solution = session.observationSolution;
  if (!solution || !solution.wcs) return null;
  if (solution.status === 'calibrated' && hasVerifiedWcs(solution)) return solution;
  return pose && solution.status === 'approximate' && solution.poseEstimate !== undefined ? solution : null;
}

/**
 * go-there-predicted-glow-one (DECIDED-BY-LEAD 25 Nov 04:47): the predicted sky's view kept in the checkpoint a flight
 * left from; the window is its WCS's pixel grid (skyViewWcs.ts), as the flight's stars are drawn through it.
 */
function keptSkyView(session: ObservationSession): SkyView | undefined {
  const history = session.navigationHistory;
  return history?.checkpoints[history.cursorIndex]?.spatialView.skyView;
}

/**
 * The sky at a screen point, through the photo's frame and its verified solve; with `pose`, a pose estimate's WCS, or
 * the window itself through a flight's kept sky view (the glow only); none without one.
 */
export function skyAtScreen(
  session: ObservationSession,
  xPx: number,
  yPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  pose = false
): { raDeg: number; decDeg: number } | null {
  const solution = verifiedSolve(session, pose);
  if (!solution) {
    const view = pose ? keptSkyView(session) : undefined;
    return view ? pixelToWorldWCS(xPx, yPx, wcsOfSkyView(view, viewportWidthPx, viewportHeightPx)) : null;
  }
  const asset = session.sourceAsset;
  if (!asset || !(asset.width > 0) || !(asset.height > 0)) return null;
  const orientation = asset.displayOrientation;
  const shown = orientedSize(asset.width, asset.height, orientation);
  const rect = sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, viewportWidthPx, viewportHeightPx, session.cropTransform);
  const upright = {
    xPx: ((xPx - rect.leftPx) / rect.widthPx) * shown.widthPx,
    yPx: ((yPx - rect.topPx) / rect.heightPx) * shown.heightPx,
  };
  const stored = storedPointOfUpright(upright.xPx, upright.yPx, asset.width, asset.height, orientation);
  // The detector reports centroids with pixel centres at whole numbers (sourcePixelToViewport adds the half back)
  return pixelToWorldWCS(stored.xPx - 0.5, stored.yPx - 0.5, solution.wcs!);
}

const ICRS_TO_SCENE = transposeRows(sceneToIcrsRows());

/** Where an ICRS direction sits in the panorama (u to the right, v = 1 at the top row), as the 3D view samples it. */
export function panoramaUvOfIcrs(icrs: Vec3, registration: PanoramaRegistration): [number, number] {
  return panoramaUv(applyRows(ICRS_TO_SCENE, icrs), registration);
}

export interface RealSkyGrid {
  cols: number;
  rows: number;
  /** Screen x, y of each vertex, row by row, in CSS pixels. */
  positions: Float32Array;
  /** Panorama u, v of each vertex; u is carried past 0 or 1 so neighbours never jump across the image's seam. */
  uvs: Float32Array;
}

/**
 * The panorama position at each vertex of a grid over the window, `stepPx` apart: the layer draws the panorama through
 * it, each cell linearly in between (the mapping is smooth at that scale). None without a verified solve.
 */
export function realSkyGrid(
  session: ObservationSession,
  viewportWidthPx: number,
  viewportHeightPx: number,
  registration: PanoramaRegistration,
  stepPx = 8,
  /**
   * arrival-star-1: the window area the grid covers, in window pixels; the window by default. While the flight brings
   * the star flown to to its resting place the panorama moves with the stars, so it is drawn over the window and the
   * strip the move uncovers.
   */
  area: { x0: number; y0: number; widthPx: number; heightPx: number } = { x0: 0, y0: 0, widthPx: viewportWidthPx, heightPx: viewportHeightPx },
  /** go-there-predicted-1 and -glow-one: a pose estimate's WCS, or a flight's kept sky view, taken too (the glow only). */
  pose = false
): RealSkyGrid | null {
  if (!verifiedSolve(session, pose) && !(pose && keptSkyView(session))) return null;
  const cols = Math.ceil(area.widthPx / stepPx) + 1;
  const rows = Math.ceil(area.heightPx / stepPx) + 1;
  const positions = new Float32Array(cols * rows * 2);
  const uvs = new Float32Array(cols * rows * 2);
  for (let j = 0; j < rows; j++) {
    let lastU = Number.NaN;
    for (let i = 0; i < cols; i++) {
      const x = area.x0 + i * stepPx;
      const y = area.y0 + j * stepPx;
      const sky = skyAtScreen(session, x, y, viewportWidthPx, viewportHeightPx, pose)!;
      let [u, v] = panoramaUvOfIcrs(raDecToUnitVector(sky.raDeg, sky.decDeg), registration);
      // Carry u on from its neighbour (the row's left, or the cell above for a row's first vertex)
      const near = i > 0 ? lastU : j > 0 ? uvs[((j - 1) * cols) * 2]! : Number.NaN;
      if (Number.isFinite(near)) u += Math.round(near - u);
      lastU = u;
      const k = (j * cols + i) * 2;
      positions[k] = x;
      positions[k + 1] = y;
      uvs[k] = u;
      uvs[k + 1] = v;
    }
  }
  return { cols, rows, positions, uvs };
}

/**
 * The "within" of the Real sky sentence: the solve's median residual in whole arcminutes, rounded up so the sentence
 * never claims a closer fit than the solve has, and at least 1. From arcseconds where the solve gives them, else from
 * pixels through its scale. None without a residual.
 */
export function realSkyResidualArcmin(solution: ObservationSolution): number | null {
  const quality = solution.quality;
  if (!quality) return null;
  const arcsec =
    quality.medianResidualArcsec ??
    (quality.medianResidualPx !== undefined && solution.wcs ? quality.medianResidualPx * solution.wcs.scaleDegPerPx * 3600 : undefined);
  if (arcsec === undefined) return null;
  return Math.max(1, Math.ceil(arcsec / 60));
}
