import { CatalogReference, CelestialFinding } from '@albedian/contracts';
import {
  RAD_TO_DEG,
  Vec3,
  getTangentBasis,
  lookAtDirectionQuaternion,
  raDecToUnitVector,
  unitVectorToRaDec,
} from './quaternion.js';
import {
  computeAnchorLockedPhaseAState,
  projectWorldPointWithCamera,
  rebaseFloatingOrigin,
} from './projection3d.js';

const MAS_TO_RAD = (Math.PI / 180) / (3600 * 1000);
const KM_S_TO_PC_YR = 1.022712165e-6;

export interface SightlineConeItem {
  finding: CelestialFinding;
  angularSeparationDeg: number;
  distanceFromObserverPc: number;
}

export interface RendererAdapterBenchmark {
  customRigAnchorDriftPx: number;
  wwtAdapterCenterLockDriftPx: number;
  customRigFramePrepMs: number;
  recommendedPrimaryEngine: 'custom_wcs_three_rig';
  surveyFallbackAdapter: 'aladin_wwt_bridge';
}

/**
 * Propagates a catalog object's 3D ICRS position and apparent sky coordinates
 * from `referenceYearCE` (default 2026) to `targetYearCE` using proper motion
 * (`properMotionRaMasYr`, `properMotionDecMasYr`) and radial velocity (`radialVelocityKmS`).
 */
export function propagateCatalogPositionToYear(
  catalogRef: CatalogReference,
  targetYearCE: number,
  referenceYearCE = 2026
): {
  raDeg: number;
  decDeg: number;
  cartesianParsec: Vec3;
} {
  const dtYears = targetYearCE - referenceYearCE;
  const r0Pc =
    catalogRef.bailerJonesDistancePc?.median ??
    (catalogRef.parallaxMas && catalogRef.parallaxMas > 0
      ? 1000 / catalogRef.parallaxMas
      : 100);

  const { e0: uRadial, eEast: uAlpha, eNorth: uDelta } = getTangentBasis(
    catalogRef.raDeg,
    catalogRef.decDeg
  );

  const pmRaRadYr = (catalogRef.properMotionRaMasYr ?? 0) * MAS_TO_RAD;
  const pmDecRadYr = (catalogRef.properMotionDecMasYr ?? 0) * MAS_TO_RAD;
  const vrPcYr = (catalogRef.radialVelocityKmS ?? 0) * KM_S_TO_PC_YR;

  const radialDispPc = r0Pc + vrPcYr * dtYears;
  const eastDispPc = r0Pc * pmRaRadYr * dtYears;
  const northDispPc = r0Pc * pmDecRadYr * dtYears;

  const xPc = radialDispPc * uRadial[0] + eastDispPc * uAlpha[0] + northDispPc * uDelta[0];
  const yPc = radialDispPc * uRadial[1] + eastDispPc * uAlpha[1] + northDispPc * uDelta[1];
  const zPc = radialDispPc * uRadial[2] + eastDispPc * uAlpha[2] + northDispPc * uDelta[2];
  const cartesianParsec: Vec3 = [xPc, yPc, zPc];
  const { raDeg, decDeg } = unitVectorToRaDec(cartesianParsec);
  return { raDeg, decDeg, cartesianParsec };
}

/**
 * A finding's ICRS position in parsecs, or `null` when it has no 3D position: no
 * Cartesian coordinates and no sky direction with a positive distance estimate.
 */
export function resolveFindingParsecPosition(finding: CelestialFinding): Vec3 | null {
  if (finding.cartesianParsecICRS) {
    return finding.cartesianParsecICRS;
  }
  const distancePc =
    finding.inferredDistancePc?.median ??
    finding.catalogRef?.bailerJonesDistancePc?.median;
  if (
    finding.skyPosition &&
    distancePc !== undefined &&
    Number.isFinite(distancePc) &&
    distancePc > 0
  ) {
    const u = raDecToUnitVector(
      finding.skyPosition.raDeg,
      finding.skyPosition.decDeg
    );
    return [u[0] * distancePc, u[1] * distancePc, u[2] * distancePc];
  }
  return null;
}

function resolveSightlineDirection(
  targetFinding: CelestialFinding,
  observerOriginParsec: Vec3
): Vec3 | null {
  const targetPos = resolveFindingParsecPosition(targetFinding);
  if (targetPos) {
    const rx = targetPos[0] - observerOriginParsec[0];
    const ry = targetPos[1] - observerOriginParsec[1];
    const rz = targetPos[2] - observerOriginParsec[2];
    const rNorm = Math.hypot(rx, ry, rz);
    if (rNorm > 1e-12) {
      return [rx / rNorm, ry / rNorm, rz / rNorm];
    }
  }
  return targetFinding.skyPosition
    ? raDecToUnitVector(targetFinding.skyPosition.raDeg, targetFinding.skyPosition.decDeg)
    : null;
}

/**
 * Queries all catalog-backed findings with 3D distance estimates that lie within
 * `halfAngleDeg` (default 18°) of the sightline ray from `observerOriginParsec`
 * through `targetFinding`, sorted by `distanceFromObserverPc` ascending ("What's behind it?").
 * Returns `null` when the target has no direction to look along, as for a detection
 * with a pixel position only: an unknown sightline is not an empty one.
 */
export function querySightlineCone(
  findings: CelestialFinding[],
  observerOriginParsec: Vec3,
  targetFinding: CelestialFinding,
  halfAngleDeg = 18
): SightlineConeItem[] | null {
  const rayDir = resolveSightlineDirection(targetFinding, observerOriginParsec);
  if (!rayDir) {
    return null;
  }

  const results: SightlineConeItem[] = [];

  for (const candidate of findings) {
    const pos = resolveFindingParsecPosition(candidate);
    if (!pos) {
      continue;
    }
    const dx = pos[0] - observerOriginParsec[0];
    const dy = pos[1] - observerOriginParsec[1];
    const dz = pos[2] - observerOriginParsec[2];
    const distPc = Math.hypot(dx, dy, dz);
    if (distPc <= 1e-12) {
      continue;
    }

    const ux = dx / distPc;
    const uy = dy / distPc;
    const uz = dz / distPc;

    const dot = rayDir[0] * ux + rayDir[1] * uy + rayDir[2] * uz;
    const cx = rayDir[1] * uz - rayDir[2] * uy;
    const cy = rayDir[2] * ux - rayDir[0] * uz;
    const cz = rayDir[0] * uy - rayDir[1] * ux;
    const sepDeg = Math.atan2(Math.hypot(cx, cy, cz), dot) * RAD_TO_DEG;

    if (sepDeg <= halfAngleDeg) {
      results.push({
        finding: candidate,
        angularSeparationDeg: sepDeg,
        distanceFromObserverPc: distPc,
      });
    }
  }

  results.sort((a, b) => a.distanceFromObserverPc - b.distanceFromObserverPc);
  return results;
}

/**
 * Benchmarks Albedian's custom off-axis WCS + floating-origin 3D camera rig
 * against a standard center-locked sky viewer adapter (WWT / Aladin `gotoRaDecZoom`),
 * measuring screen-space anchor drift during Phase A and 50,000-star float64->float32
 * frame preparation latency.
 */
export function benchmarkRendererAdapters(
  starCount = 50000
): RendererAdapterBenchmark {
  const anchorRaDeg = 88.7929; // Betelgeuse
  const anchorDecDeg = 7.4071;
  const anchorTargetNdc: [number, number] = [0.42, -0.31];
  const viewportWidthPx = 1920;
  const viewportHeightPx = 1080;
  const aspect = viewportWidthPx / viewportHeightPx;

  const qStart = lookAtDirectionQuaternion(raDecToUnitVector(83.82, -1.2));
  const qEnd = lookAtDirectionQuaternion(raDecToUnitVector(anchorRaDeg, anchorDecDeg));
  const fovStartDeg = 52;
  const fovEndDeg = 28;
  const anchorDir = raDecToUnitVector(anchorRaDeg, anchorDecDeg);

  let maxCustomDriftPx = 0;
  let maxWwtCenterLockDriftPx = 0;

  for (let step = 0; step <= 10; step++) {
    const progress = step / 10;
    const state = computeAnchorLockedPhaseAState({
      anchorRaDeg,
      anchorDecDeg,
      anchorTargetNdc,
      qStart,
      qEnd,
      fovStartDeg,
      fovEndDeg,
      aspect,
      progress,
      viewportWidthPx,
      viewportHeightPx,
    });
    if (state.anchorDriftPx > maxCustomDriftPx) {
      maxCustomDriftPx = state.anchorDriftPx;
    }

    const centerLockedProj = projectWorldPointWithCamera(
      anchorDir,
      [0, 0, 0],
      state.quaternion,
      state.fovVerticalDeg,
      aspect,
      [0, 0]
    );
    const wwtDriftX =
      (centerLockedProj.ndcX - anchorTargetNdc[0]) * (viewportWidthPx * 0.5);
    const wwtDriftY =
      (centerLockedProj.ndcY - anchorTargetNdc[1]) * (viewportHeightPx * 0.5);
    const wwtDriftPx = Math.hypot(wwtDriftX, wwtDriftY);
    if (wwtDriftPx > maxWwtCenterLockDriftPx) {
      maxWwtCenterLockDriftPx = wwtDriftPx;
    }
  }

  const catalogPositions = new Float64Array(starCount * 3);
  for (let i = 0; i < catalogPositions.length; i += 3) {
    catalogPositions[i] = 150 + (i % 97) * 0.25;
    catalogPositions[i + 1] = -80 + (i % 53) * 0.35;
    catalogPositions[i + 2] = 40 + (i % 71) * 0.15;
  }

  const t0 = performance.now();
  const rebased = rebaseFloatingOrigin(catalogPositions, [152.5, -78.2, 41.0]);
  const t1 = performance.now();
  const elapsedMs = rebased.length > 0 ? Math.max(0.001, t1 - t0) : 0;

  return {
    customRigAnchorDriftPx: maxCustomDriftPx,
    wwtAdapterCenterLockDriftPx: maxWwtCenterLockDriftPx,
    customRigFramePrepMs: elapsedMs,
    recommendedPrimaryEngine: 'custom_wcs_three_rig',
    surveyFallbackAdapter: 'aladin_wwt_bridge',
  };
}
