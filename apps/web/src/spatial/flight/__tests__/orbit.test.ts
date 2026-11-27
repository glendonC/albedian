import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { steerCamera, type Vec3, type WcsCameraRows } from '@albedian/camera-rig';
import { ORBIT_RAD_PER_WINDOW, ORBIT_TOP_MARGIN_RAD, orbitAngles, orbitStep, orbitUpNext } from '../orbit.js';
import { STEER_DRAG_RAD_PER_PX } from '../steering.js';

// phone-orbit-one (MOTION's page, DECIDED-BY-LEAD 27 Nov 03:19 and 03:29): in a flight to a star every drag, the
// finger's and the mouse's, moves the craft and the view round the star with the look held on it (Celestia's orbit,
// NASA's Eyes' OrbitController, three.js OrbitControls); the keys still turn; free flight keeps turning.

const seed = Number(process.env.ALBEDIAN_FC_SEED ?? 1);
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const norm = (a: Vec3) => Math.hypot(a[0], a[1], a[2]);
const unit = (a: Vec3): Vec3 => a.map((x) => x / norm(a)) as Vec3;

/** A camera `d` parsecs from the star, looking straight at it, its up `up` (made perpendicular to the look). */
function lookingAt(star: Vec3, from: Vec3, up: Vec3): { positionPc: Vec3; rows: WcsCameraRows } {
  const z = unit(sub(star, from));
  const x = unit([up[1] * z[2] - up[2] * z[1], up[2] * z[0] - up[0] * z[2], up[0] * z[1] - up[1] * z[0]]);
  const y: Vec3 = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]];
  return { positionPc: from, rows: { x, y, z } };
}

const star: Vec3 = [10, -4, 120];
const up: Vec3 = [0, 1, 0];
const start = lookingAt(star, [10, -4, 119.9], up);
const rig = { ...start, chase: start.rows };

describe('the up the orbit goes round', () => {
  it('is the view\'s up while the hand-over turns the view and as a drag begins, and kept through the drag', () => {
    const kept: Vec3 = [0, 1, 0];
    const view: Vec3 = [0.1, 0.99, 0];
    expect(orbitUpNext(kept, view, true, false)).toEqual(view);
    expect(orbitUpNext(kept, view, false, true)).toEqual(view);
    expect(orbitUpNext(kept, view, false, false)).toBe(kept);
  });
});

describe('the orbit\'s angle from a drag', () => {
  it('takes 1.5 rad for a drag across the whole window, and as much for one down its whole height (Celestia\'s rate)', () => {
    expect(ORBIT_RAD_PER_WINDOW).toBe(1.5);
    // The hands give a drag as a turn rate: STEER_DRAG_RAD_PER_PX per pixel; across 390 px in one second
    const across = orbitAngles({ yawRadPerS: -390 * STEER_DRAG_RAD_PER_PX, pitchRadPerS: 0 }, 1, 390, 844);
    expect(Math.abs(across.yawRad)).toBeCloseTo(1.5, 12);
    const down = orbitAngles({ yawRadPerS: 0, pitchRadPerS: 844 * STEER_DRAG_RAD_PER_PX }, 1, 390, 844);
    expect(down.pitchRad).toBeCloseTo(1.5, 12);
  });
});

describe('a step of the orbit round the star', () => {
  it('keeps the distance to the star and the look on it, the star where it stood in the view', () => {
    fc.assert(
      fc.property(fc.double({ min: -0.5, max: 0.5, noNaN: true }), fc.double({ min: -0.5, max: 0.5, noNaN: true }), (yaw, pitch) => {
        const next = orbitStep(rig, star, up, yaw, pitch);
        expect(norm(sub(next.positionPc, star))).toBeCloseTo(norm(sub(start.positionPc, star)), 12);
        // The star stays along the look: dead ahead
        const toStar = unit(sub(star, next.positionPc));
        expect(dot(toStar, next.rows.z)).toBeCloseTo(1, 12);
        // and the chase view turned with it, as Celestia's orbit turns the observer
        expect(dot(next.chase.z, next.rows.z)).toBeCloseTo(1, 12);
      }),
      { seed, numRuns: 200 }
    );
  });

  it('goes across about the fixed up, the sky following the finger as the turn did', () => {
    const a = 0.2;
    const next = orbitStep(rig, star, up, a, 0);
    // Round the up: the height above the star's level kept
    expect(dot(sub(next.positionPc, star), up)).toBeCloseTo(dot(sub(start.positionPc, star), up), 12);
    // The view's axes turn as a turn of the same angle would have turned them (the sky moves the same way on screen)
    const turned = steerCamera({ ...start, speedPcPerS: 0 }, { yawRadPerS: a, pitchRadPerS: 0 }, 1).rows;
    for (const k of ['x', 'y', 'z'] as const) next.rows[k].forEach((v, i) => expect(v).toBeCloseTo(turned[k][i]!, 12));
  });

  it('goes over and under about the horizontal, the sky following the finger as the turn did', () => {
    const b = 0.15;
    const next = orbitStep(rig, star, up, 0, b);
    const turned = steerCamera({ ...start, speedPcPerS: 0 }, { yawRadPerS: 0, pitchRadPerS: b }, 1).rows;
    for (const k of ['x', 'y', 'z'] as const) next.rows[k].forEach((v, i) => expect(v).toBeCloseTo(turned[k][i]!, 12));
  });

  it('holds short of straight over the top and under the bottom, so the view never flips', () => {
    let s = rig;
    for (let i = 0; i < 40; i++) s = orbitStep(s, star, up, 0, -0.1);
    const lat = (p: Vec3) => Math.asin(dot(unit(sub(p, star)), up));
    expect(lat(s.positionPc)).toBeCloseTo(Math.PI / 2 - ORBIT_TOP_MARGIN_RAD, 9);
    expect(dot(s.rows.z, unit(sub(star, s.positionPc)))).toBeCloseTo(1, 12);
    for (let i = 0; i < 80; i++) s = orbitStep(s, star, up, 0, 0.1);
    expect(lat(s.positionPc)).toBeCloseTo(-(Math.PI / 2 - ORBIT_TOP_MARGIN_RAD), 9);
    // Across still goes round at the hold
    const across = orbitStep(s, star, up, 0.3, 0);
    expect(norm(sub(across.positionPc, star))).toBeCloseTo(norm(sub(start.positionPc, star)), 12);
  });

  it('keeps a mirrored photo\'s left-handed axes, the sense still the turn\'s', () => {
    const mirrored = { ...rig, rows: { x: rig.rows.x.map((v) => -v) as Vec3, y: rig.rows.y, z: rig.rows.z }, chase: { x: rig.rows.x.map((v) => -v) as Vec3, y: rig.rows.y, z: rig.rows.z } };
    const next = orbitStep(mirrored, star, up, 0.2, 0.1);
    const turned = steerCamera({ positionPc: mirrored.positionPc, rows: mirrored.rows, speedPcPerS: 0 }, { yawRadPerS: 0.2, pitchRadPerS: 0 }, 1).rows;
    const h = (r: WcsCameraRows) => Math.sign(dot(r.x, [r.y[1] * r.z[2] - r.y[2] * r.z[1], r.y[2] * r.z[0] - r.y[0] * r.z[2], r.y[0] * r.z[1] - r.y[1] * r.z[0]]));
    expect(h(next.rows)).toBe(-1);
    // across alone matches the turn
    const across = orbitStep(mirrored, star, up, 0.2, 0);
    for (const k of ['x', 'y', 'z'] as const) across.rows[k].forEach((v, i) => expect(v).toBeCloseTo(turned[k][i]!, 12));
    expect(dot(next.rows.z, unit(sub(star, next.positionPc)))).toBeCloseTo(1, 12);
  });
});
