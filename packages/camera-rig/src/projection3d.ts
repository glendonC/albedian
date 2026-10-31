import { QuaternionTuple, WCSCalibrationModel } from '@albedian/contracts';
import {
  DEG_TO_RAD,
  RAD_TO_DEG,
  Vec3,
  normalizeQuaternion,
  raDecToUnitVector,
  slerpQuaternion,
} from './quaternion.js';

export interface SourceProjection {
  readonly kind: 'source_projection';
  readonly widthPx: number;
  readonly heightPx: number;
  readonly zoom: number;
  readonly panX: number;
  readonly panY: number;
}

export interface AngularSkyCamera {
  readonly kind: 'angular_sky_camera';
  readonly wcs: WCSCalibrationModel;
  readonly quaternion: QuaternionTuple;
  readonly fovVerticalDeg: number;
  readonly frustumShiftNdc: [number, number];
}

export interface SpatialViewCamera {
  readonly kind: 'spatial_view_camera';
  readonly observerOriginParsec: Vec3;
  readonly quaternion: QuaternionTuple;
  readonly fovVerticalDeg: number;
  readonly aspect: number;
  readonly frustumShiftNdc: [number, number];
  readonly simulationEpochISO: string;
}

export interface AnchorConstraint {
  readonly findingId?: string;
  readonly anchorRaDeg: number;
  readonly anchorDecDeg: number;
  readonly anchorTargetNdc: [number, number];
}

export type ObservationCamera =
  | SourceProjection
  | AngularSkyCamera
  | SpatialViewCamera;

export interface CameraProjection3DResult {
  ndcX: number;
  ndcY: number;
  distancePc: number;
  visible: boolean;
}

export interface PhaseATransitionInput {
  anchorRaDeg: number;
  anchorDecDeg: number;
  anchorTargetNdc: [number, number];
  qStart: QuaternionTuple;
  qEnd: QuaternionTuple;
  fovStartDeg: number;
  fovEndDeg: number;
  aspect: number;
  progress: number;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
}

export interface PhaseATransitionState {
  quaternion: QuaternionTuple;
  fovVerticalDeg: number;
  frustumShiftNdc: [number, number];
  projectedAnchorNdc: [number, number];
  anchorDriftPx: number;
}

/**
 * Interpolates vertical FOV in `ln(tan(fov / 2))` space so perceived optical zoom
 * rate is uniform across wide-angle and telephoto focal lengths.
 */
export function interpolateLogTanFov(
  fov0Deg: number,
  fov1Deg: number,
  t: number
): number {
  const u = Math.max(0, Math.min(1, t));
  const half0Rad = Math.max(1e-4, Math.min(Math.PI * 0.495, fov0Deg * DEG_TO_RAD * 0.5));
  const half1Rad = Math.max(1e-4, Math.min(Math.PI * 0.495, fov1Deg * DEG_TO_RAD * 0.5));
  const logTan0 = Math.log(Math.tan(half0Rad));
  const logTan1 = Math.log(Math.tan(half1Rad));
  const interpolatedLogTan = (1 - u) * logTan0 + u * logTan1;
  return 2 * Math.atan(Math.exp(interpolatedLogTan)) * RAD_TO_DEG;
}

export const interpolateFovLogTan = interpolateLogTanFov;

/**
 * Subtracts `observerOriginParsec` in float64, rotates into camera coordinates by the
 * inverse of `cameraQuaternion`, projects with perspective `f = 1 / tan(fovVerticalRad / 2)`,
 * and applies `frustumShiftNdc`.
 */
export function projectWorldPointWithCamera(
  pointParsecICRS: Vec3,
  observerOriginParsec: Vec3,
  cameraQuaternion: QuaternionTuple,
  fovVerticalDeg: number,
  aspect: number,
  frustumShiftNdc: [number, number] = [0, 0]
): CameraProjection3DResult {
  const dx = pointParsecICRS[0] - observerOriginParsec[0];
  const dy = pointParsecICRS[1] - observerOriginParsec[1];
  const dz = pointParsecICRS[2] - observerOriginParsec[2];
  const distancePc = Math.hypot(dx, dy, dz);

  const [qx, qy, qz, qw] = normalizeQuaternion(cameraQuaternion);
  const ix = -qx;
  const iy = -qy;
  const iz = -qz;
  const iw = qw;

  const tx = 2 * (iy * dz - iz * dy);
  const ty = 2 * (iz * dx - ix * dz);
  const tz = 2 * (ix * dy - iy * dx);

  const camX = dx + iw * tx + (iy * tz - iz * ty);
  const camY = dy + iw * ty + (iz * tx - ix * tz);
  const camZ = dz + iw * tz + (ix * ty - iy * tx);

  const depth = -camZ;
  const visible = depth > 1e-6;
  const safeDepth = depth > 1e-9 ? depth : 1e-9;

  const halfFovRad = Math.max(1e-4, Math.min(Math.PI * 0.495, fovVerticalDeg * DEG_TO_RAD * 0.5));
  const f = 1 / Math.tan(halfFovRad);
  const safeAspect = aspect > 0 ? aspect : 1;

  const rawNdcX = (camX * (f / safeAspect)) / safeDepth;
  const rawNdcY = (camY * f) / safeDepth;

  return {
    ndcX: rawNdcX + frustumShiftNdc[0],
    ndcY: rawNdcY + frustumShiftNdc[1],
    distancePc,
    visible,
  };
}

export function computeOffAxisFrustumShift(
  anchorDirICRS: Vec3,
  anchorTargetNdc: [number, number],
  quaternion: QuaternionTuple,
  fovVerticalDeg: number,
  aspect: number
): [number, number] {
  const unshifted = projectWorldPointWithCamera(
    anchorDirICRS,
    [0, 0, 0],
    quaternion,
    fovVerticalDeg,
    aspect,
    [0, 0]
  );
  return [
    anchorTargetNdc[0] - unshifted.ndcX,
    anchorTargetNdc[1] - unshifted.ndcY,
  ];
}

/**
 * Computes the anchor-locked Phase A (`Photo -> Registered Sky`) camera state.
 * Solves the exact off-axis frustum shift `[shiftX, shiftY]` at eased progress `s(t)`
 * so the selected anchor star remains locked at `anchorTargetNdc` with `< 1e-5 px` drift.
 */
export function computeAnchorLockedPhaseAState(
  params: PhaseATransitionInput
): PhaseATransitionState {
  const p = Math.max(0, Math.min(1, params.progress));
  const s = p * p * (3 - 2 * p);

  const quaternion = slerpQuaternion(params.qStart, params.qEnd, s);
  const fovVerticalDeg = interpolateLogTanFov(params.fovStartDeg, params.fovEndDeg, s);
  const anchorDir = raDecToUnitVector(params.anchorRaDeg, params.anchorDecDeg);

  const frustumShiftNdc = computeOffAxisFrustumShift(
    anchorDir,
    params.anchorTargetNdc,
    quaternion,
    fovVerticalDeg,
    params.aspect
  );

  const locked = projectWorldPointWithCamera(
    anchorDir,
    [0, 0, 0],
    quaternion,
    fovVerticalDeg,
    params.aspect,
    frustumShiftNdc
  );

  const projectedAnchorNdc: [number, number] = [locked.ndcX, locked.ndcY];
  const widthPx = params.viewportWidthPx ?? 1920;
  const heightPx = params.viewportHeightPx ?? 1080;

  const driftXPx = (projectedAnchorNdc[0] - params.anchorTargetNdc[0]) * (widthPx * 0.5);
  const driftYPx = (projectedAnchorNdc[1] - params.anchorTargetNdc[1]) * (heightPx * 0.5);
  const anchorDriftPx = Math.hypot(driftXPx, driftYPx);

  return {
    quaternion,
    fovVerticalDeg,
    frustumShiftNdc,
    projectedAnchorNdc,
    anchorDriftPx,
  };
}

/**
 * Subtracts `observerOriginParsec` in 64-bit IEEE-754 floating point before
 * casting relative star positions to `Float32Array` for GPU vertex buffer upload.
 */
export function rebaseFloatingOrigin(
  catalogPositionsParsec: Float64Array,
  observerOriginParsec: Vec3
): Float32Array {
  const length = catalogPositionsParsec.length;
  const rebased = new Float32Array(length);
  const [ox, oy, oz] = observerOriginParsec;

  const limit = length - (length % 3);
  for (let i = 0; i < limit; i += 3) {
    rebased[i] = (catalogPositionsParsec[i] ?? 0) - ox;
    rebased[i + 1] = (catalogPositionsParsec[i + 1] ?? 0) - oy;
    rebased[i + 2] = (catalogPositionsParsec[i + 2] ?? 0) - oz;
  }

  return rebased;
}
