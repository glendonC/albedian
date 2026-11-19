import { orientedSize, orientStoredPoint, type ObservationSession } from '@albedian/contracts';
import { orbitCamera, photoCameraClipMatrix, photoCornerRays, photoImagePlane, wcsCameraRows, type OrbitCamera, type Vec3 } from '@albedian/camera-rig';
import { isFlightShown } from '../../reveal/flightView.js';
import { flightCameraOf, placeFinding } from './flightFrame.js';
import { pinholeOfView, starViewOfOrbit, type StarView } from './starView.js';

/**
 * cone-1: the third-person view of the photo's view cone after Go there. An orbit camera turns around the cone's
 * middle; the cone's apex is Earth, its edges the photo's corner rays through the verified solve, the photo itself on
 * the cone's base, and the catalog stars around it at their distances (the star layer). Simulated: catalog geometry
 * drawn by the app. The framing numbers here are [proposed] until the design lane's record (SPATIAL-cone-1-QUESTION).
 */

/** The orbit camera's vertical field of view [proposed]. */
export const CONE_FOV_DEG = 50;
/** How far the cone reaches past its farthest matched star with a distance, as a fraction of that distance [proposed]. */
export const CONE_REACH = 1.15;
/** The cone's length when no matched star has a usable distance, in parsecs: a drawing length, not a distance claim. */
export const CONE_LENGTH_WITHOUT_DISTANCES_PC = 100;

/** Whether the cone view is on: after Go there, asked for by the page address (`?view=cone`), INTERIM until a control. */
export function isConeViewOn(session: ObservationSession, search: string = typeof window === 'undefined' ? '' : window.location.search): boolean {
  return isFlightShown(session) && new URLSearchParams(search).get('view') === 'cone';
}

/** The cone's length: past the farthest matched star placed at its catalog distance. */
export function coneLengthPc(session: ObservationSession): number {
  let farthest = 0;
  for (const f of session.observationSolution?.findings ?? []) {
    if (f.evidenceClass !== 'catalog_matched') continue;
    const p = placeFinding(f);
    if (p.kind === 'at_distance') farthest = Math.max(farthest, p.distancePc);
  }
  return farthest > 0 ? farthest * CONE_REACH : CONE_LENGTH_WITHOUT_DISTANCES_PC;
}

export interface OrbitState {
  readonly azimuthDeg: number;
  readonly elevationDeg: number;
  readonly distancePc: number;
}

/** Where the orbit starts: off to the side and above, far enough back to see the whole cone [proposed]. */
export function initialOrbit(lengthPc: number): OrbitState {
  return { azimuthDeg: 35, elevationDeg: 20, distancePc: 1.3 * lengthPc };
}

/** Turn and tilt by a drag (degrees per CSS pixel), come closer or go back by a wheel step (multiplicative). */
export function dragOrbit(state: OrbitState, dxPx: number, dyPx: number, degPerPx = 0.25): OrbitState {
  return { ...state, azimuthDeg: state.azimuthDeg - dxPx * degPerPx, elevationDeg: Math.max(-85, Math.min(85, state.elevationDeg + dyPx * degPerPx)) };
}
export function zoomOrbit(state: OrbitState, wheelDeltaY: number, lengthPc: number): OrbitState {
  const factor = Math.exp(wheelDeltaY * 0.001);
  return { ...state, distancePc: Math.max(0.05 * lengthPc, Math.min(20 * lengthPc, state.distancePc * factor)) };
}

/** The orbit camera around the cone's middle, the photo's own up as its up and its axis as the zero turn. */
export function coneOrbit(session: ObservationSession, state: OrbitState, lengthPc: number): OrbitCamera | null {
  const wcs = session.observationSolution?.wcs;
  if (!wcs) return null;
  const rows = wcsCameraRows(wcs);
  const half = lengthPc / 2;
  return orbitCamera({
    pivotPc: [rows.z[0] * half, rows.z[1] * half, rows.z[2] * half],
    distancePc: state.distancePc,
    azimuthDeg: state.azimuthDeg,
    elevationDeg: state.elevationDeg,
    up: rows.y,
    forwardAtZero: rows.z,
  });
}

/** The star layer's camera in the cone view; none without a verified solve. */
export function coneStarView(session: ObservationSession, state: OrbitState, viewportWidthPx: number, viewportHeightPx: number): StarView | null {
  const flight = flightCameraOf(session, viewportWidthPx, viewportHeightPx);
  if (!flight) return null;
  const orbit = coneOrbit(session, state, coneLengthPc(session));
  return orbit ? starViewOfOrbit(orbit, CONE_FOV_DEG, viewportWidthPx, viewportHeightPx, flight.referencePc) : null;
}

export interface ConeScene {
  /** Earth, the cone's apex, from the camera (parsecs, ICRS axes; float64 subtraction, the floating origin). */
  readonly apexFromCamera: Vec3;
  /** The base's corners from the camera: top left of the photo, then clockwise. */
  readonly cornersFromCamera: Vec3[];
  /** The photo on the base: vertices from the camera, and texture coordinates on the upright image as the page shows it. */
  readonly plane: { cols: number; rows: number; positions: Float32Array; uvs: Float32Array };
  readonly clipMatrix: number[];
  readonly near: number;
  readonly far: number;
}

/** The cone, its base and the photo on it, laid out around the orbit camera for the craft and cone renderer. */
export function coneSceneOf(session: ObservationSession, view: StarView, lengthPc: number, segments = 16): ConeScene | null {
  const wcs = session.observationSolution?.wcs;
  const asset = session.sourceAsset;
  if (!wcs || !asset) return null;
  const axis = wcsCameraRows(wcs).z;
  const cam = view.observerPc;
  const from = (p: Vec3): Vec3 => [p[0] - cam[0], p[1] - cam[1], p[2] - cam[2]];
  const corners = photoCornerRays(wcs, asset.width, asset.height).map((ray) => {
    const t = lengthPc / (ray[0] * axis[0] + ray[1] * axis[1] + ray[2] * axis[2]);
    return from([ray[0] * t, ray[1] * t, ray[2] * t]);
  });
  const grid = photoImagePlane(wcs, asset.width, asset.height, lengthPc, segments, Math.max(1, Math.round((segments * asset.height) / asset.width)));
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
  // Depth range around everything drawn: Earth and the base
  const distances = [from([0, 0, 0]), ...corners].map((p) => Math.hypot(p[0], p[1], p[2]));
  const near = Math.max(1e-4, Math.min(...distances) * 0.25);
  const far = Math.max(...distances) * 2;
  return {
    apexFromCamera: from([0, 0, 0]),
    cornersFromCamera: corners,
    plane: { cols: grid.cols, rows: grid.rows, positions, uvs },
    clipMatrix: photoCameraClipMatrix(pinholeOfView(view), near, far),
    near,
    far,
  };
}
