import { hasVerifiedWcs, type CelestialFinding, type DisplayOrientation, type ObservationSession, type WCSCalibrationModel } from '@albedian/contracts';
import {
  angularSeparationDeg,
  pixelToWorldWCS,
  placeStar,
  projectPlacementThroughWcs,
  raDecToUnitVector,
  rebaseFloatingOrigin,
  resolveFindingParsecPosition,
  usableDistancePc,
  type StarPlacement,
  type Vec3,
} from '@albedian/camera-rig';
import { sourcePixelToViewport, type PhotoCrop } from '../../components/sourcePhotoRect.js';
import { distanceKindOf, type CatalogStarCoreTile } from '../catalogStars/tileCodec.js';
import type { CatalogStarView } from '../catalogStars/tileLoaderTypes.js';

/**
 * The flight through the stars (REQ-SCN-2), as the There screen draws it after Go there: the person's photo is the
 * camera (its verified solve's centre, roll, parity, scale and lens model, in the same frame the photo is drawn in on
 * the window), standing where the session's camera stands; every catalog star sits at its own catalog distance, or on
 * the sky sphere when it has none that can be used. Simulated: catalog geometry, never light the recording measured.
 */

/** Stars the flight loads, by their magnitude seen from Earth: the catalog's layers to G 9.5 [proposed limit]. */
export const FLIGHT_LOAD_LIMIT_MAG = 9.5;

/** Stars the flight draws, by their magnitude seen from the camera: the naked-eye limit the sky view also uses. */
export const FLIGHT_DRAW_LIMIT_MAG = 6.5;

/** A 2D affine map, [a, b, c, d, e, f]: x' = a x + b y + c, y' = d x + e y + f. */
export type Affine2 = readonly [number, number, number, number, number, number];

/**
 * The stored photo's pixel (the solve's convention: pixel centres at whole numbers) to the window, exactly as
 * `sourcePixelToViewport` places a detection: the display orientation's turn and mirror, then the photo's rectangle.
 * The map is affine, so three points fix it.
 */
export function storedToScreenAffine(
  storedWidthPx: number,
  storedHeightPx: number,
  viewportWidthPx: number,
  viewportHeightPx: number,
  crop?: PhotoCrop,
  orientation?: DisplayOrientation
): Affine2 {
  const at = (u: number, v: number) => sourcePixelToViewport(u, v, storedWidthPx, storedHeightPx, viewportWidthPx, viewportHeightPx, crop, orientation);
  const o = at(0, 0);
  const x = at(1, 0);
  const y = at(0, 1);
  return [x.xPx - o.xPx, y.xPx - o.xPx, o.xPx, x.yPx - o.yPx, y.yPx - o.yPx, o.yPx];
}

export function invertAffine(m: Affine2): Affine2 {
  const det = m[0] * m[4] - m[1] * m[3];
  const a = m[4] / det;
  const b = -m[1] / det;
  const d = -m[3] / det;
  const e = m[0] / det;
  return [a, b, -(a * m[2] + b * m[5]), d, e, -(d * m[2] + e * m[5])];
}

export interface FlightCamera {
  readonly wcs: WCSCalibrationModel;
  /** Stored photo pixel to window CSS pixel. */
  readonly storedToScreen: Affine2;
  readonly screenToStored: Affine2;
  readonly viewportWidthPx: number;
  readonly viewportHeightPx: number;
  /** Where the camera stands now, ICRS parsecs from the Sun (the session's observer origin). */
  readonly observerPc: Vec3;
  /**
   * The floating origin the GPU positions are rebased on in float64: the picked star's place, where the flight ends
   * (one light year short of it) and precision matters most; the Sun when no star with a place is picked.
   */
  readonly referencePc: Vec3;
}

/**
 * The photo as the camera, for a session with a photo and a verified solve, or with a pose estimate: go-there-predicted-1
 * (LEAD ANSWER 12:55), the pose's own nominal WCS, the one its predicted rings are drawn through
 * (`poseProjection.ts` projectThroughPose), so a flight to a predicted star leaves from its ring and never from a claimed
 * pixel. None otherwise.
 */
export function flightCameraOf(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number): FlightCamera | null {
  const solution = session.observationSolution;
  const asset = session.sourceAsset;
  const verified = solution?.status === 'calibrated' && hasVerifiedWcs(solution);
  const posed = solution?.status === 'approximate' && solution.poseEstimate !== undefined;
  if (!solution || !(verified || posed) || !solution.wcs) return null;
  if (!asset || !(asset.width > 1) || !(asset.height > 1)) return null;
  const storedToScreen = storedToScreenAffine(asset.width, asset.height, viewportWidthPx, viewportHeightPx, session.cropTransform, asset.displayOrientation);
  const picked = solution.findings.find((f) => f.id === session.selectedFindingId);
  const pickedPlacement = picked ? placeFinding(picked) : null;
  const origin = session.spatialView?.observerOriginParsec ?? [0, 0, 0];
  return {
    wcs: solution.wcs,
    storedToScreen,
    screenToStored: invertAffine(storedToScreen),
    viewportWidthPx,
    viewportHeightPx,
    observerPc: [origin[0], origin[1], origin[2]],
    referencePc: pickedPlacement?.kind === 'at_distance' ? pickedPlacement.positionPc : [0, 0, 0],
  };
}

/**
 * A finding placed for the flight: at the very point Go there flies toward (`resolveFindingParsecPosition`, the
 * reducer's and the readout's own), when its catalog distance can be used; else on the sky sphere along its sky
 * position, never at an invented distance.
 */
export function placeFinding(finding: CelestialFinding): StarPlacement {
  const interval = finding.inferredDistancePc ?? finding.catalogRef?.bailerJonesDistancePc;
  const distance = interval ? { medianPc: interval.median, lowerPc: interval.lower, upperPc: interval.upper } : null;
  const at = usableDistancePc(distance) !== null ? resolveFindingParsecPosition(finding) : null;
  if (at) {
    const d = Math.hypot(at[0], at[1], at[2]);
    if (d > 0) return { kind: 'at_distance', positionPc: at, distancePc: d };
  }
  const sky = finding.skyPosition ?? finding.catalogRef;
  return placeStar(sky ? raDecToUnitVector(sky.raDeg, sky.decDeg) : [0, 0, 1], null);
}

/** Where the camera sees a placed star in the window, in CSS pixels; none behind it or past the lens model's fold. */
export function screenOfPlacement(camera: FlightCamera, placement: StarPlacement): { xPx: number; yPx: number } | null {
  const p = projectPlacementThroughWcs(placement, camera.observerPc, camera.wcs);
  if (!p.visible) return null;
  const m = camera.storedToScreen;
  return { xPx: m[0] * p.uPx + m[1] * p.vPx + m[2], yPx: m[3] * p.uPx + m[4] * p.vPx + m[5] };
}

/**
 * The sky the window shows from Earth, as the tile loader asks for it: the direction at the window's centre and the
 * widest angle from it to any point of the window's edge, plus a margin of a degree. Flying straight toward a star
 * in the window only ever moves the other stars away from it, so no star outside this cone enters the window.
 */
export function flightViewCone(camera: FlightCamera, limitingMagnitude = FLIGHT_LOAD_LIMIT_MAG): CatalogStarView {
  const inv = camera.screenToStored;
  const sky = (x: number, y: number) => pixelToWorldWCS(inv[0] * x + inv[1] * y + inv[2], inv[3] * x + inv[4] * y + inv[5], camera.wcs);
  const w = camera.viewportWidthPx;
  const h = camera.viewportHeightPx;
  const centre = sky(w / 2, h / 2);
  let widest = 0;
  const steps = 16;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    for (const [x, y] of [[t * w, 0], [t * w, h], [0, t * h], [w, t * h]] as const) {
      const at = sky(x, y);
      widest = Math.max(widest, angularSeparationDeg(centre.raDeg, centre.decDeg, at.raDeg, at.decDeg));
    }
  }
  return {
    centerIcrs: raDecToUnitVector(centre.raDeg, centre.decDeg),
    radiusRad: (Math.min(180, widest + 1) * Math.PI) / 180,
    limitingMagnitude,
  };
}

/** One tile's stars as the flight layer uploads them. */
export interface FlightTileArrays {
  readonly count: number;
  /** A placed star's offset from the reference (float64 subtraction, then float32); a sky-sphere star's direction. */
  readonly positions: Float32Array;
  /** 1 for a star at its catalog distance, 0 for one kept on the sky sphere. */
  readonly placed: Float32Array;
  /** A placed star's distance from the Sun, for its brightness from the camera; 0 on the sky sphere. */
  readonly earthDistancePc: Float32Array;
  /** Catalog magnitude seen from Earth. */
  readonly magnitude: Float32Array;
  /** How many of the tile's stars have no usable distance. */
  readonly skySphereCount: number;
}

/** Places each of a core tile's stars by its catalog distance and kind, and rebases the placed ones on `referencePc`. */
export function buildFlightTileArrays(tile: CatalogStarCoreTile, referencePc: Vec3): FlightTileArrays {
  const n = tile.count;
  const world = new Float64Array(3 * n);
  const placed = new Float32Array(n);
  const earthDistancePc = new Float32Array(n);
  let skySphereCount = 0;
  for (let i = 0; i < n; i++) {
    const d = usableDistancePc({ medianPc: tile.distancePc[i] ?? Number.NaN, kind: distanceKindOf(tile.flags[i] ?? 0) });
    const x = tile.directions[3 * i] ?? 0;
    const y = tile.directions[3 * i + 1] ?? 0;
    const z = tile.directions[3 * i + 2] ?? 0;
    if (d === null) {
      skySphereCount += 1;
      continue;
    }
    // The float32 unit vector made exact unit length in float64 before scaling, so distance is the catalog's
    const len = Math.hypot(x, y, z) || 1;
    world[3 * i] = (x / len) * d;
    world[3 * i + 1] = (y / len) * d;
    world[3 * i + 2] = (z / len) * d;
    placed[i] = 1;
    earthDistancePc[i] = d;
  }
  const positions = rebaseFloatingOrigin(world, referencePc);
  for (let i = 0; i < n; i++) {
    if (placed[i] === 1) continue;
    positions[3 * i] = tile.directions[3 * i] ?? 0;
    positions[3 * i + 1] = tile.directions[3 * i + 1] ?? 0;
    positions[3 * i + 2] = tile.directions[3 * i + 2] ?? 0;
  }
  return { count: n, positions, placed, earthDistancePc, magnitude: tile.magnitude, skySphereCount };
}

export interface PhotoStarInFlight {
  readonly findingId: string;
  readonly placement: StarPlacement['kind'];
  /** Where it is in the window now; none behind the camera. */
  readonly screen: { xPx: number; yPx: number } | null;
  /** How far it is from the camera now; none on the sky sphere. */
  readonly distanceFromCameraPc: number | null;
}

/** The stars the photo matched (catalog matched findings), each placed and seen from where the camera stands now. */
export function photoStarsInFlight(session: ObservationSession, viewportWidthPx: number, viewportHeightPx: number): PhotoStarInFlight[] {
  const camera = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  if (!camera) return [];
  return session.observationSolution!.findings
    .filter((f) => f.evidenceClass === 'catalog_matched')
    .map((finding) => {
      const placement = placeFinding(finding);
      const p = projectPlacementThroughWcs(placement, camera.observerPc, camera.wcs);
      return {
        findingId: finding.id,
        placement: placement.kind,
        screen: screenOfPlacement(camera, placement),
        distanceFromCameraPc: p.distancePc,
      };
    });
}
