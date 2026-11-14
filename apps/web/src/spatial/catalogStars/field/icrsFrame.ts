import * as THREE from 'three';
import type { CatalogStarView } from '../CatalogStarTileLoader.js';
import { raDecDegToVec, type Vec3 } from '../healpix.js';

/**
 * ICRS axes (x toward RA 0h on the equator, z toward the north celestial pole) to scene axes:
 * +Y is celestial north, -Z faces RA 0h, +X faces RA 18h, so east sits to the left of a
 * viewer facing the equator, as on the real sky.
 */
export const ICRS_TO_SCENE_QUATERNION = new THREE.Quaternion().setFromRotationMatrix(
  new THREE.Matrix4().set(0, -1, 0, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0, 0, 0, 1)
);

/** Angular padding added beyond the camera's half-frustum diagonal when planning tiles (radians). */
export const VIEW_CONE_PADDING_RAD = 0.08;
/** Minimum angular change in view direction or cone radius before re-planning tiles (radians). */
export const VIEW_CHANGE_EPSILON_RAD = 0.01;
/** Minimum interval between view-cone tile updates while panning (milliseconds). */
export const VIEW_UPDATE_MIN_INTERVAL_MS = 120;

export function icrsToScene(v: Vec3, target = new THREE.Vector3()): THREE.Vector3 {
  return target.set(v[0], v[1], v[2]).applyQuaternion(ICRS_TO_SCENE_QUATERNION);
}

/** Scene direction (unit vector) of an ICRS RA/Dec catalog position in degrees. */
export function raDecToSceneDirection(raDeg: number, decDeg: number, target = new THREE.Vector3()): THREE.Vector3 {
  return icrsToScene(raDecDegToVec(raDeg, decDeg), target);
}

export interface ViewSyncState {
  readonly lastDir: THREE.Vector3;
  lastRadius: number;
  lastLimit: number;
  lastAt: number;
  readonly tmpDir: THREE.Vector3;
  readonly tmpQuat: THREE.Quaternion;
}

export function createViewSyncState(): ViewSyncState {
  return {
    lastDir: new THREE.Vector3(),
    lastRadius: -1,
    lastLimit: Number.NaN,
    lastAt: Number.NEGATIVE_INFINITY,
    tmpDir: new THREE.Vector3(),
    tmpQuat: new THREE.Quaternion(),
  };
}

export function invalidateViewSync(state: ViewSyncState): void {
  state.lastAt = Number.NEGATIVE_INFINITY;
}

export function selectActiveMount(mounts: readonly THREE.Group[]): THREE.Group | undefined {
  return mounts.find((m) => m.parent?.visible !== false) ?? mounts[0];
}

export function cameraHalfDiagonalRad(camera: THREE.PerspectiveCamera): number {
  return Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * Math.hypot(1, camera.aspect));
}

/**
 * Extracts the camera's ICRS view cone through the active sky mount's world rotation and
 * returns a `CatalogStarView` only when the view has changed beyond epsilon and the throttle
 * interval has elapsed.
 */
export function pollCameraIcrsView(
  state: ViewSyncState,
  camera: THREE.PerspectiveCamera,
  mounts: readonly THREE.Group[],
  limitingMagnitude: number,
  nowMs: number
): CatalogStarView | null {
  const mount = selectActiveMount(mounts);
  camera.getWorldDirection(state.tmpDir);
  if (mount) {
    state.tmpDir.applyQuaternion(mount.getWorldQuaternion(state.tmpQuat).invert());
  } else {
    state.tmpDir.applyQuaternion(state.tmpQuat.copy(ICRS_TO_SCENE_QUATERNION).invert());
  }
  const radius = cameraHalfDiagonalRad(camera) + VIEW_CONE_PADDING_RAD;
  const moved = state.lastDir.angleTo(state.tmpDir);
  const changed =
    moved > VIEW_CHANGE_EPSILON_RAD ||
    Math.abs(radius - state.lastRadius) > VIEW_CHANGE_EPSILON_RAD ||
    limitingMagnitude !== state.lastLimit;
  if (!changed || nowMs - state.lastAt < VIEW_UPDATE_MIN_INTERVAL_MS) return null;

  state.lastDir.copy(state.tmpDir);
  state.lastRadius = radius;
  state.lastLimit = limitingMagnitude;
  state.lastAt = nowMs;
  return {
    centerIcrs: [state.tmpDir.x, state.tmpDir.y, state.tmpDir.z],
    radiusRad: radius,
    limitingMagnitude,
  };
}
