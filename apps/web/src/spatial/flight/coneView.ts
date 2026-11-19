import { orientedSize, orientStoredPoint, type ObservationSession } from '@albedian/contracts';
import {
  insidePhotoCone,
  photoCameraClipMatrix,
  photoCornerRays,
  photoImagePlane,
  pixelToWorldWCS,
  raDecToUnitVector,
  wcsCameraRows,
  type Vec3,
  type WcsCameraRows,
} from '@albedian/camera-rig';
import { storedPointOfUpright } from '../../reveal/realSkyGrid.js';
import { flightCameraOf, placeFinding } from './flightFrame.js';
import { pinholeOfView, starViewOfOrbit, type StarView } from './starView.js';
import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';

/**
 * cone-1, as D28 decides it (UIUX-cone-READY.md): the photo's view in the third person after Go there. The cone runs
 * from Earth along the photo's four corner rays through the verified solve (its lens model included) out to the
 * cross-section of its farthest matched star with a measured distance; the photo sits on the cross-section at three
 * quarters of the nearest one's, so no star of yours lies between it and Earth; the camera starts beside the cone as
 * the decided picture places it and turns rigidly round Earth. Simulated: geometry from the solve and the catalog.
 */

/** The orbit camera's field of view across the window (D28's picture: a pinhole 50 degrees across). */
export const CONE_FOV_ACROSS_DEG = 50;
/** The cone's length when no matched star has a usable distance, parsecs: a drawing length, never a distance claim. */
export const CONE_LENGTH_WITHOUT_DISTANCES_PC = 100;
/** Where the photo sits, as a fraction of the nearest matched star's depth along the cone (D28 item 3). */
export const PHOTO_AT_NEAREST = 0.75;

export interface ConeGeometry {
  /** The cone's axis (the solve's centre) and the photo's up as shown, across the axis. */
  readonly axis: Vec3;
  readonly top: Vec3;
  /** Depth along the axis of the far cross-section and of the photo's. */
  readonly lengthPc: number;
  readonly photoDepthPc: number;
  /** The nearest and farthest matched stars with a measured distance, parsecs from Earth; none without any. */
  readonly nearestPc: number | null;
  readonly farthestPc: number | null;
  /** Matched stars whose direction lies inside the cone. */
  readonly insideCount: number;
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
function unit(a: Vec3): Vec3 {
  const n = Math.hypot(a[0], a[1], a[2]);
  return n > 0 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 1];
}

/** The cone's measures from the verified solve and the matched stars; none without a solve or a photo. */
export function coneGeometryOf(session: ObservationSession): ConeGeometry | null {
  const wcs = session.observationSolution?.wcs;
  const asset = session.sourceAsset;
  if (!wcs || !asset) return null;
  const axis = wcsCameraRows(wcs).z;
  // The photo's top as the page shows it: the ray through the upright image's top centre, made square to the axis
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const stored = storedPointOfUpright(shown.widthPx / 2, 0, asset.width, asset.height, asset.displayOrientation);
  const s = pixelToWorldWCS(stored.xPx - 0.5, stored.yPx - 0.5, wcs);
  const ray = raDecToUnitVector(s.raDeg, s.decDeg);
  const top = unit(sub(ray, mul(axis, dot(ray, axis))));
  let nearestPc: number | null = null;
  let farthestPc: number | null = null;
  let nearestDepth = Number.POSITIVE_INFINITY;
  let farthestDepth = 0;
  let insideCount = 0;
  for (const f of session.observationSolution!.findings) {
    if (f.evidenceClass !== 'catalog_matched') continue;
    const p = placeFinding(f);
    const dir = p.kind === 'at_distance' ? unit(p.positionPc) : p.direction;
    if (!insidePhotoCone(dir, wcs, asset.width, asset.height)) continue;
    insideCount += 1;
    if (p.kind !== 'at_distance') continue;
    nearestPc = nearestPc === null ? p.distancePc : Math.min(nearestPc, p.distancePc);
    farthestPc = farthestPc === null ? p.distancePc : Math.max(farthestPc, p.distancePc);
    const depth = dot(p.positionPc, axis);
    nearestDepth = Math.min(nearestDepth, depth);
    farthestDepth = Math.max(farthestDepth, depth);
  }
  const lengthPc = farthestDepth > 0 ? farthestDepth : CONE_LENGTH_WITHOUT_DISTANCES_PC;
  const photoDepthPc = Number.isFinite(nearestDepth) ? PHOTO_AT_NEAREST * nearestDepth : PHOTO_AT_NEAREST * lengthPc;
  return { axis, top, lengthPc, photoDepthPc, nearestPc, farthestPc, insideCount };
}

/** The orbit: a turn about the photo's up and a tilt about the camera's right, both round Earth, and a zoom toward it. */
export interface ConeOrbit {
  readonly azimuthDeg: number;
  readonly elevationDeg: number;
  readonly zoom: number;
}

export const CONE_ORBIT_START: ConeOrbit = { azimuthDeg: 0, elevationDeg: 0, zoom: 1 };

/** Rodrigues' rotation of `v` about the unit axis `k` by `angle` radians. */
function rotate(v: Vec3, k: Vec3, angle: number): Vec3 {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return add(add(mul(v, c), mul(cross(k, v), s)), mul(k, dot(k, v) * (1 - c)));
}

/**
 * The orbit camera: D28's start beside the cone (the picture's camera: from the cone's middle, 1.25 lengths out to the
 * side and 0.25 above, looking at the middle), turned rigidly round Earth and brought closer by the zoom.
 */
export function conePose(geometry: ConeGeometry, orbit: ConeOrbit): { positionPc: Vec3; rows: WcsCameraRows } {
  const { axis, top, lengthPc: L } = geometry;
  const side = unit(cross(axis, top));
  const mid = mul(axis, 0.5 * L);
  const eye0 = add(add(mid, mul(side, 1.25 * L)), mul(top, 0.25 * L));
  const f0 = unit(sub(mid, eye0));
  const r0 = unit(cross(f0, top));
  const u0 = cross(r0, f0);
  const el = (Math.max(-85, Math.min(85, orbit.elevationDeg)) * Math.PI) / 180;
  const az = (orbit.azimuthDeg * Math.PI) / 180;
  const turn = (v: Vec3) => rotate(rotate(v, r0, el), top, az);
  return { positionPc: mul(turn(eye0), orbit.zoom), rows: { x: turn(r0), y: turn(u0), z: turn(f0) } };
}

/** A drag turns round Earth (0.25 degrees per CSS pixel), the tilt held within 85 degrees. */
export function dragConeOrbit(orbit: ConeOrbit, dxPx: number, dyPx: number, degPerPx = 0.25): ConeOrbit {
  return { ...orbit, azimuthDeg: orbit.azimuthDeg - dxPx * degPerPx, elevationDeg: Math.max(-85, Math.min(85, orbit.elevationDeg + dyPx * degPerPx)) };
}

/** The wheel or a pinch comes closer to Earth or goes back, by a factor, within 0.05 to 4 times the start. */
export function zoomConeOrbit(orbit: ConeOrbit, wheelDeltaY: number): ConeOrbit {
  return { ...orbit, zoom: Math.max(0.05, Math.min(4, orbit.zoom * Math.exp(wheelDeltaY * 0.001))) };
}

/** The star layer's camera in the cone view: a plain pinhole 50 degrees across; none without a verified solve. */
export function coneStarView(session: ObservationSession, orbit: ConeOrbit, viewportWidthPx: number, viewportHeightPx: number): StarView | null {
  const flight = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  const geometry = coneGeometryOf(session);
  if (!flight || !geometry) return null;
  const fovVerticalDeg = (2 * Math.atan(Math.tan((CONE_FOV_ACROSS_DEG * Math.PI) / 360) * (viewportHeightPx / viewportWidthPx)) * 180) / Math.PI;
  return starViewOfOrbit(conePose(geometry, orbit), fovVerticalDeg, viewportWidthPx, viewportHeightPx, flight.referencePc);
}

export interface ConeScene {
  /** Earth, the apex, from the camera (parsecs, ICRS axes; float64 subtraction, the floating origin). */
  readonly apexFromCamera: Vec3;
  /** The far cross-section's corners from the camera: the photo's top left as shown, then clockwise. */
  readonly farFromCamera: Vec3[];
  /** The photo's frame on its cross-section, the same order. */
  readonly photoFromCamera: Vec3[];
  /** The photo on its cross-section: vertices from the camera, texture coordinates on the upright image. */
  readonly plane: { cols: number; rows: number; positions: Float32Array; uvs: Float32Array };
  readonly clipMatrix: number[];
}

/** Corner rays in the order of the upright image (top left, top right, bottom right, bottom left), as the page shows it. */
function uprightCornerRays(session: ObservationSession): Vec3[] {
  const asset = session.sourceAsset!;
  const wcs = session.observationSolution!.wcs!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return ([[0, 0], [shown.widthPx, 0], [shown.widthPx, shown.heightPx], [0, shown.heightPx]] as const).map(([x, y]) => {
    const p = storedPointOfUpright(x, y, asset.width, asset.height, asset.displayOrientation);
    const s = pixelToWorldWCS(p.xPx - 0.5, p.yPx - 0.5, wcs);
    return raDecToUnitVector(s.raDeg, s.decDeg);
  });
}

/** The cone, the photo's frame and the photo on it, laid out around the orbit camera. */
export function coneSceneOf(session: ObservationSession, view: StarView, geometry: ConeGeometry, segments = 16): ConeScene | null {
  const wcs = session.observationSolution?.wcs;
  const asset = session.sourceAsset;
  if (!wcs || !asset) return null;
  const cam = view.observerPc;
  const from = (p: Vec3): Vec3 => [p[0] - cam[0], p[1] - cam[1], p[2] - cam[2]];
  const at = (ray: Vec3, depth: number) => mul(ray, depth / dot(ray, geometry.axis));
  const rays = uprightCornerRays(session);
  const grid = photoImagePlane(wcs, asset.width, asset.height, geometry.photoDepthPc, segments, Math.max(1, Math.round((segments * asset.height) / asset.width)));
  const positions = new Float32Array(grid.positionsPc.length);
  for (let i = 0; i < positions.length; i += 3) {
    positions[i] = grid.positionsPc[i]! - cam[0];
    positions[i + 1] = grid.positionsPc[i + 1]! - cam[1];
    positions[i + 2] = grid.positionsPc[i + 2]! - cam[2];
  }
  // The stored frame's texture coordinates turned into the upright image's, as the page draws the photo
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const uvs = new Float32Array(grid.uvs.length);
  for (let k = 0; k < grid.uvs.length / 2; k++) {
    const p = orientStoredPoint(grid.uvs[2 * k]! * asset.width, (1 - grid.uvs[2 * k + 1]!) * asset.height, asset.width, asset.height, asset.displayOrientation);
    uvs[2 * k] = p.xPx / shown.widthPx;
    uvs[2 * k + 1] = 1 - p.yPx / shown.heightPx;
  }
  const farFromCamera = rays.map((r) => from(at(r, geometry.lengthPc)));
  const distances = [from([0, 0, 0]), ...farFromCamera].map((p) => Math.hypot(p[0], p[1], p[2]));
  const near = Math.max(1e-4, Math.min(...distances) * 0.25);
  const far = Math.max(...distances) * 2;
  return {
    apexFromCamera: from([0, 0, 0]),
    farFromCamera,
    photoFromCamera: rays.map((r) => from(at(r, geometry.photoDepthPc))),
    plane: { cols: grid.cols, rows: grid.rows, positions, uvs },
    clipMatrix: photoCameraClipMatrix(pinholeOfView(view), near, far),
  };
}

const WHOLE = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** D28 item 6: the readout's numbers and line, from the solve and the catalog. */
export function coneReadout(geometry: ConeGeometry): { inside: string; farthestLy: string | null; nearestLine: string | null } {
  return {
    inside: WHOLE.format(geometry.insideCount),
    farthestLy: geometry.farthestPc === null ? null : WHOLE.format(geometry.farthestPc * PARSEC_TO_LIGHT_YEAR),
    nearestLine: geometry.nearestPc === null ? null : `The nearest is ${WHOLE.format(geometry.nearestPc * PARSEC_TO_LIGHT_YEAR)} ly away.`,
  };
}

/** D28 item 7: the disclosure's lines in the cone view, in order (the no-distance sentence is added when any). */
export const CONE_LINES = {
  spec: 'Drawn or computed by the app; not measured in the source recording.',
  view: "Your photo's view: the part of the sky your photo holds, from its solve, out to its farthest matched star with a measured distance.",
  stars: 'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances.',
  rings: 'Rings: the stars your photo matched.',
} as const;
