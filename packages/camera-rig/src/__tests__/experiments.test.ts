import { describe, expect, it } from 'vitest';
import {
  benchmarkRendererAdapters,
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  projectWorldPointWithCamera,
  raDecToUnitVector,
  rebaseFloatingOrigin,
} from '../index.js';

describe('Spatial Camera Rig: Empirical Experiments (EXP-05, EXP-06, EXP-07, EXP-11)', () => {
  it('EXP-05: Off-axis frustum shift eliminates Phase A anchor star drift (< 0.01 px vs > 100 px unshifted)', () => {
    const anchorRaDeg = 88.7929; // Betelgeuse
    const anchorDecDeg = 7.4071;
    const anchorTargetNdc: [number, number] = [0.38, -0.27];
    const qStart = lookAtDirectionQuaternion(raDecToUnitVector(83.8, -1.2));
    const qEnd = lookAtDirectionQuaternion(
      raDecToUnitVector(anchorRaDeg, anchorDecDeg)
    );

    let maxLockedDriftPx = 0;
    for (let i = 0; i <= 100; i++) {
      const progress = i / 100;
      const state = computeAnchorLockedPhaseAState({
        anchorRaDeg,
        anchorDecDeg,
        anchorTargetNdc,
        qStart,
        qEnd,
        fovStartDeg: 54,
        fovEndDeg: 26,
        aspect: 16 / 9,
        progress,
        viewportWidthPx: 1920,
        viewportHeightPx: 1080,
      });
      if (state.anchorDriftPx > maxLockedDriftPx) {
        maxLockedDriftPx = state.anchorDriftPx;
      }
    }

    expect(maxLockedDriftPx).toBeLessThan(0.01);
    expect(maxLockedDriftPx).toBeLessThan(1e-5);
  });

  it('EXP-06: Double-precision floating-origin rebasing prevents Float32 vertex jitter at 1000 pc', () => {
    const observerOrigin: [number, number, number] = [850.25, -420.75, 315.5];
    const localOffset: [number, number, number] = [1.234567, -0.876543, 0.456789];
    const starWorld: [number, number, number] = [
      observerOrigin[0] + localOffset[0],
      observerOrigin[1] + localOffset[1],
      observerOrigin[2] + localOffset[2],
    ];

    // Float64 rebasing before Float32 cast
    const catalogF64 = new Float64Array(starWorld);
    const rebasedF32 = rebaseFloatingOrigin(catalogF64, observerOrigin);
    const floatingOriginErrPc = Math.hypot(
      (rebasedF32[0] ?? 0) - localOffset[0],
      (rebasedF32[1] ?? 0) - localOffset[1],
      (rebasedF32[2] ?? 0) - localOffset[2]
    );

    // Naive Float32 world-space subtraction (simulating standard GPU shader subtraction without floating origin)
    const naiveWorldF32 = new Float32Array(starWorld);
    const naiveOriginF32 = new Float32Array(observerOrigin);
    const naiveDiffX = Math.fround((naiveWorldF32[0] ?? 0) - (naiveOriginF32[0] ?? 0));
    const naiveDiffY = Math.fround((naiveWorldF32[1] ?? 0) - (naiveOriginF32[1] ?? 0));
    const naiveDiffZ = Math.fround((naiveWorldF32[2] ?? 0) - (naiveOriginF32[2] ?? 0));
    const naiveErrPc = Math.hypot(
      naiveDiffX - localOffset[0],
      naiveDiffY - localOffset[1],
      naiveDiffZ - localOffset[2]
    );

    expect(floatingOriginErrPc).toBeLessThan(1e-4);
    expect(floatingOriginErrPc).toBeLessThan(naiveErrPc);

    // Also verify projectWorldPointWithCamera projects local offset accurately at 1000 pc
    const q = lookAtDirectionQuaternion(localOffset);
    const proj = projectWorldPointWithCamera(
      starWorld,
      observerOrigin,
      q,
      45,
      16 / 9
    );
    expect(proj.visible).toBe(true);
    expect(Math.hypot(proj.ndcX, proj.ndcY)).toBeLessThan(1e-12);
  });

  it('EXP-07 & EXP-11: Renderer adapter benchmark & 50,000-star floating-origin frame preparation', () => {
    const benchmark = benchmarkRendererAdapters(50000);

    expect(benchmark.customRigAnchorDriftPx).toBeLessThan(0.01);
    expect(benchmark.wwtAdapterCenterLockDriftPx).toBeGreaterThan(100);
    // Wall-clock time depends on the machine and its load, so it is reported, not asserted
    expect(Number.isFinite(benchmark.customRigFramePrepMs)).toBe(true);
    console.info(`[EXP-11] 50,000-star frame preparation took ${benchmark.customRigFramePrepMs.toFixed(3)} ms on this run`);
    expect(benchmark.recommendedPrimaryEngine).toBe('custom_wcs_three_rig');
    expect(benchmark.surveyFallbackAdapter).toBe('aladin_wwt_bridge');
  });

  it('EXP-11: frame preparation rebases all 50,000 positions around the observer to float32', () => {
    const count = 50000;
    const positions = new Float64Array(count * 3);
    for (let i = 0; i < positions.length; i += 3) {
      positions[i] = 150 + (i % 97) * 0.25;
      positions[i + 1] = -80 + (i % 53) * 0.35;
      positions[i + 2] = 40 + (i % 71) * 0.15;
    }
    const origin: [number, number, number] = [152.5, -78.2, 41.0];
    const rebased = rebaseFloatingOrigin(positions, origin);

    expect(rebased.length).toBe(count * 3);
    let mismatches = 0;
    for (let i = 0; i < rebased.length; i++) {
      if (rebased[i] !== Math.fround(positions[i]! - origin[i % 3]!)) mismatches++;
    }
    expect(mismatches).toBe(0);
  });
});
