import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { STEER_SPEED_LIMITS_PC_S, changeSpeed, raDecToUnitVector, steerCamera, type SteerState, type Vec3, type WcsCameraRows } from '../index.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RUNS = { numRuns: 200, ...(SEED !== undefined ? { seed: SEED } : {}) };

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);

function rowsAlong(raDeg: number, decDeg: number): WcsCameraRows {
  const z = raDecToUnitVector(raDeg, decDeg);
  const helper: Vec3 = Math.abs(z[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
  const x0 = cross(z, helper);
  const x: Vec3 = [x0[0] / norm(x0), x0[1] / norm(x0), x0[2] / norm(x0)];
  return { x, y: cross(z, x), z };
}

const stateArb = fc
  .record({
    ra: fc.double({ min: 0, max: 359.9, noNaN: true }),
    dec: fc.double({ min: -89, max: 89, noNaN: true }),
    p: fc.tuple(fc.double({ min: -500, max: 500, noNaN: true }), fc.double({ min: -500, max: 500, noNaN: true }), fc.double({ min: -500, max: 500, noNaN: true })),
    speed: fc.double({ min: 0.01, max: 100, noNaN: true }),
  })
  .map(({ ra, dec, p, speed }): SteerState => ({ positionPc: p, rows: rowsAlong(ra, dec), speedPcPerS: speed }));

describe('steering the free camera through the star field (D26 item 6)', () => {
  it('moves along where it looks by speed times time, and keeps its axes orthonormal and right handed', () => {
    fc.assert(
      fc.property(stateArb, fc.double({ min: -2, max: 2, noNaN: true }), fc.double({ min: -2, max: 2, noNaN: true }), fc.double({ min: 0, max: 0.1, noNaN: true }), (s, yaw, pitch, dt) => {
        const next = steerCamera(s, { yawRadPerS: yaw, pitchRadPerS: pitch }, dt);
        for (const a of [next.rows.x, next.rows.y, next.rows.z]) expect(Math.abs(norm(a) - 1)).toBeLessThan(1e-9);
        expect(Math.abs(dot(next.rows.x, next.rows.y))).toBeLessThan(1e-9);
        expect(Math.abs(dot(next.rows.y, next.rows.z))).toBeLessThan(1e-9);
        const zx = cross(next.rows.y, next.rows.z);
        for (let k = 0; k < 3; k++) expect(Math.abs(zx[k]! - next.rows.x[k]!)).toBeLessThan(1e-9);
        const moved: Vec3 = [next.positionPc[0] - s.positionPc[0], next.positionPc[1] - s.positionPc[1], next.positionPc[2] - s.positionPc[2]];
        expect(Math.abs(norm(moved) - s.speedPcPerS * dt)).toBeLessThan(1e-9 * Math.max(1, s.speedPcPerS));
        if (norm(moved) > 0) expect(dot(moved, next.rows.z)).toBeCloseTo(norm(moved), 9);
      }),
      RUNS
    );
  });

  it('turns right for a positive yaw and up for a positive pitch, by rate times time', () => {
    const s: SteerState = { positionPc: [0, 0, 0], rows: { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }, speedPcPerS: 1 };
    const right = steerCamera(s, { yawRadPerS: 1, pitchRadPerS: 0 }, 0.1);
    expect(right.rows.z[0]).toBeCloseTo(Math.sin(0.1), 12);
    expect(right.rows.z[2]).toBeCloseTo(Math.cos(0.1), 12);
    const up = steerCamera(s, { yawRadPerS: 0, pitchRadPerS: 1 }, 0.1);
    expect(up.rows.z[1]).toBeCloseTo(Math.sin(0.1), 12);
    // Standing still turns without moving
    const still = steerCamera({ ...s, speedPcPerS: 0 }, { yawRadPerS: 1, pitchRadPerS: 1 }, 0.5);
    expect(still.positionPc).toEqual([0, 0, 0]);
  });

  it('changes speed by a factor per second held, within its limits', () => {
    expect(changeSpeed(1, 1, 1)).toBeCloseTo(2, 12);
    expect(changeSpeed(1, -1, 1)).toBeCloseTo(0.5, 12);
    expect(changeSpeed(1, 0, 3)).toBe(1);
    expect(changeSpeed(1, 1, 1e6)).toBe(STEER_SPEED_LIMITS_PC_S.max);
    expect(changeSpeed(1, -1, 1e6)).toBe(STEER_SPEED_LIMITS_PC_S.min);
  });
});
