import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { CHASE_FOLLOW_SECONDS, STEER_SPEED_LIMITS_PC_S, changeSpeed, followRows, raDecToUnitVector, steerCamera, type SteerState, type Vec3, type WcsCameraRows } from '../index.js';

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

describe('the chase camera following the craft (MOTION k13, decided 08:12)', () => {
  const angle = (a: Vec3, b: Vec3) => Math.acos(Math.max(-1, Math.min(1, dot(a, b))));
  const level: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
  const turned = steerCamera({ positionPc: [0, 0, 0], rows: level, speedPcPerS: 0 }, { yawRadPerS: 1, pitchRadPerS: 0.3 }, 0.5).rows;

  it('closes the gap to the craft\'s heading by 1 - exp(-dt / 0.25 s) a frame, at any frame rate, never past it', () => {
    const start = angle(level.z, turned.z);
    const one = followRows(level, turned, 0.1);
    expect(angle(one.z, turned.z)).toBeCloseTo(start * Math.exp(-0.1 / CHASE_FOLLOW_SECONDS), 3);
    let two = followRows(level, turned, 0.05);
    two = followRows(two, turned, 0.05);
    expect(angle(two.z, turned.z)).toBeCloseTo(angle(one.z, turned.z), 4);
    // First order: it approaches from one side and never overshoots
    let rows = level;
    let last = start;
    for (let i = 0; i < 120; i++) {
      rows = followRows(rows, turned, 1 / 60);
      const now = angle(rows.z, turned.z);
      expect(now).toBeLessThanOrEqual(last + 1e-12);
      last = now;
    }
    // About a second later it has caught up: the record's time constant is 0.25 s, so after 1 s the gap is e^-4
    expect(CHASE_FOLLOW_SECONDS).toBe(0.25);
    let oneSecond = level;
    for (let i = 0; i < 60; i++) oneSecond = followRows(oneSecond, turned, 1 / 60);
    expect(angle(oneSecond.z, turned.z) / start).toBeCloseTo(Math.exp(-4), 2);
    expect(last).toBeLessThan(start * 0.02);
  });

  it('keeps its axes orthonormal and right handed', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 0.2, noNaN: true }), (dt) => {
        const r = followRows(level, turned, dt);
        for (const a of [r.x, r.y, r.z]) expect(Math.abs(norm(a) - 1)).toBeLessThan(1e-9);
        expect(Math.abs(dot(r.x, r.y))).toBeLessThan(1e-9);
        expect(Math.abs(dot(r.y, r.z))).toBeLessThan(1e-9);
        const x = cross(r.y, r.z);
        for (let k = 0; k < 3; k++) expect(Math.abs(x[k]! - r.x[k]!)).toBeLessThan(1e-9);
      }),
      RUNS
    );
  });

  it('locks to the craft at once under reduced motion', () => {
    expect(followRows(level, turned, 0.01, { reduced: true })).toEqual(turned);
  });

  it('turns smoothly from an antipodal 180-degree craft heading instead of snapping in a single frame', () => {
    const aboutFaceYaw: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, -1] };
    const aboutFacePitch: WcsCameraRows = { x: [1, 0, 0], y: [0, -1, 0], z: [0, 0, -1] };
    for (const target of [aboutFaceYaw, aboutFacePitch]) {
      const zeroDt = followRows(level, target, 0);
      expect(angle(zeroDt.z, level.z)).toBeLessThan(1e-12);
      const step = followRows(level, target, 0.1);
      expect(angle(step.z, target.z)).toBeCloseTo(Math.PI * Math.exp(-0.1 / CHASE_FOLLOW_SECONDS), 6);
      for (const a of [step.x, step.y, step.z]) expect(Math.abs(norm(a) - 1)).toBeLessThan(1e-9);
      const x = cross(step.y, step.z);
      for (let k = 0; k < 3; k++) expect(Math.abs(x[k]! - step.x[k]!)).toBeLessThan(1e-9);
    }
    // Left-handed (parity -1) camera at 180 degrees keeps its handedness and exponential rate too
    const mirroredCam: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
    const mirroredAboutFace: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, -1] };
    const mStep = followRows(mirroredCam, mirroredAboutFace, 0.1);
    expect(angle(mStep.z, mirroredAboutFace.z)).toBeCloseTo(Math.PI * Math.exp(-0.1 / CHASE_FOLLOW_SECONDS), 6);
    expect(Math.sign(dot(cross(mStep.y, mStep.z), mStep.x))).toBe(-1);
  });
});

describe('a mirrored photo\'s camera (parity -1: left-handed axes) keeps its handedness', () => {
  const mirrored: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
  const handed = (r: WcsCameraRows) => Math.sign(dot(cross(r.y, r.z), r.x));

  it('steering neither mirrors the view nor moves it when nothing is pressed', () => {
    const still = steerCamera({ positionPc: [0, 0, 0], rows: mirrored, speedPcPerS: 0 }, { yawRadPerS: 0, pitchRadPerS: 0 }, 0.1);
    for (const k of ['x', 'y', 'z'] as const) for (let i = 0; i < 3; i++) expect(still.rows[k][i]).toBeCloseTo(mirrored[k][i]!, 12);
    const turned = steerCamera({ positionPc: [0, 0, 0], rows: mirrored, speedPcPerS: 0 }, { yawRadPerS: 0.7, pitchRadPerS: -0.4 }, 0.3);
    expect(handed(turned.rows)).toBe(-1);
    // A turn to the right still turns toward the screen's right (the x row)
    const right = steerCamera({ positionPc: [0, 0, 0], rows: mirrored, speedPcPerS: 0 }, { yawRadPerS: 1, pitchRadPerS: 0 }, 0.1);
    expect(dot(right.rows.z, mirrored.x)).toBeGreaterThan(0);
  });

  it('the chase follow keeps it too', () => {
    const turned = steerCamera({ positionPc: [0, 0, 0], rows: mirrored, speedPcPerS: 0 }, { yawRadPerS: 1, pitchRadPerS: 0.3 }, 0.5).rows;
    const r = followRows(mirrored, turned, 0.1);
    expect(handed(r)).toBe(-1);
    // A long follow lands on the craft on every axis, so the hand is kept without turning the view about its forward axis
    const landed = followRows(mirrored, turned, 20);
    for (const k of ['x', 'y', 'z'] as const) for (let i = 0; i < 3; i++) expect(landed[k][i]).toBeCloseTo(turned[k][i]!, 9);
    expect(followRows(mirrored, mirrored, 0.1)).toEqual(mirrored);
  });
});
