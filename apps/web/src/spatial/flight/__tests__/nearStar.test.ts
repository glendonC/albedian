import { describe, expect, it } from 'vitest';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { steerCamera } from '@albedian/camera-rig';
import {
  AU_PC, AU_PER_LY, HANDOVER_SECONDS, HEAD_ON_RAD, NEAR_STAR_TAU_S, NEAR_UNIT_BELOW_LY, SUN_ABSOLUTE_MAG,
  handoverShare, headingRows, keptOutside, nearStarFloorPc, nearStarOf, nearStarStep, pacedSpeed, readoutDistance, startHandover, stepHandover, turnBetween, turnedRows,
} from '../nearStar.js';
import { steeringSpeedLine } from '../steering.js';

// near-star-flight-one (DECIDED-BY-LEAD 25 Nov 07:28, MOTION's near-star-flight-one): the steered flight paced by its
// distance to the star flown to, never nearer than where it shines as our Sun does on Earth.

const LY_PER_PC = 3.261563777;
const ROWS: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const MIRRORED: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const hand = (r: WcsCameraRows) => Math.sign(dot(r.x, cross(r.y, r.z)));
const len = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const expectRows = (a: WcsCameraRows, b: WcsCameraRows, digits = 12) => {
  for (const k of ['x', 'y', 'z'] as const) for (let i = 0; i < 3; i++) expect(a[k][i]).toBeCloseTo(b[k][i]!, digits);
};
/** A star 10 pc from Earth with the given absolute magnitude, so its catalog magnitude is the same number. */
const starAt = (positionPc: Vec3, absolute: number) => ({ positionPc, earthPc: 10, magnitude: absolute });

describe('the ruled numbers', () => {
  it('are the ruling\'s: 2.5 s, three quarters of a second, the IAU\'s astronomical unit and the Sun\'s 4.83', () => {
    expect(NEAR_STAR_TAU_S).toBe(2.5);
    expect(HANDOVER_SECONDS).toBe(0.75);
    // 1 pc = 206,264.806 au
    expect(1 / AU_PC).toBeCloseTo(206264.806, 3);
    expect(SUN_ABSOLUTE_MAG).toBe(4.83);
  });
});

describe('nearStarFloorPc: where the star shines as our Sun does on Earth', () => {
  it('is 1 AU for a star as bright as the Sun, 10 AU for a hundred Suns, 0.1 AU for a hundredth', () => {
    expect(nearStarFloorPc(starAt([0, 0, 0], 4.83)) / AU_PC).toBeCloseTo(1, 9);
    expect(nearStarFloorPc(starAt([0, 0, 0], 4.83 - 5)) / AU_PC).toBeCloseTo(10, 9);
    expect(nearStarFloorPc(starAt([0, 0, 0], 4.83 + 5)) / AU_PC).toBeCloseTo(0.1, 9);
  });

  it('takes the magnitude from Earth back to 10 pc: the same star twice as far and four times fainter has the same floor', () => {
    const near = nearStarFloorPc({ earthPc: 100, magnitude: 2 });
    expect(nearStarFloorPc({ earthPc: 200, magnitude: 2 + 2.5 * Math.log10(4) }) / near).toBeCloseTo(1, 9);
  });

  it('is 1 AU when the star\'s light is not known, and never under a hundredth of an AU', () => {
    expect(nearStarFloorPc(null)).toBe(AU_PC);
    expect(nearStarFloorPc({ earthPc: 100, magnitude: Number.NaN })).toBe(AU_PC);
    expect(nearStarFloorPc({ earthPc: 0, magnitude: 1 })).toBe(AU_PC);
    expect(nearStarFloorPc(starAt([0, 0, 0], 40)) / AU_PC).toBeCloseTo(0.01, 9);
  });
});

describe('pacedSpeed and keptOutside', () => {
  it('flies the set speed far out, never more than the distance over 2.5 s, and the floor over 2.5 s at the floor', () => {
    expect(pacedSpeed(1, 10, 0.001)).toBe(1);
    expect(pacedSpeed(1, 1, 0.001)).toBeCloseTo(1 / NEAR_STAR_TAU_S, 12);
    expect(pacedSpeed(1, 0.0001, 0.001)).toBeCloseTo(0.001 / NEAR_STAR_TAU_S, 12);
    expect(pacedSpeed(0.01, 1, 0.001)).toBe(0.01);
  });

  it('moves a place inside the floor out to it along the line from the star, and leaves others as they are', () => {
    const star: Vec3 = [5, 0, 0];
    expect(keptOutside([5.5, 0, 0], star, 1)).toEqual([6, 0, 0]);
    expect(keptOutside([5, 0.25, 0], star, 0.5)).toEqual([5, 0.5, 0]);
    expect(keptOutside([7, 0, 0], star, 1)).toEqual([7, 0, 0]);
    expect(keptOutside([6, 0, 0], star, 1)).toEqual([6, 0, 0]);
  });
});

describe('the hand-over\'s heading', () => {
  it('looks at the star with the up kept as near as can be and the axes\' hand kept, mirrored or not', () => {
    const to: Vec3 = [0.3, 0.2, 1];
    for (const rows of [ROWS, MIRRORED]) {
      const h = headingRows(rows, to)!;
      expect(dot(h.z, to) / len(to)).toBeCloseTo(1, 12);
      expect(hand(h)).toBe(hand(rows));
      for (const v of [h.x, h.y, h.z]) expect(len(v)).toBeCloseTo(1, 12);
      expect(dot(h.x, h.y)).toBeCloseTo(0, 12);
      expect(dot(h.y, h.z)).toBeCloseTo(0, 12);
      // The up stays in the plane of the old up and the new look
      expect(dot(h.x, rows.y)).toBeCloseTo(0, 12);
      expect(dot(h.y, rows.y)).toBeGreaterThan(0.9);
    }
    expect(headingRows(ROWS, [0, 0, 0])).toBeNull();
    expect(headingRows(ROWS, [0, 2, 0])).toBeNull();
  });

  it('is one turn that takes the old rows to the new, about an axis by an angle', () => {
    for (const rows of [ROWS, MIRRORED]) {
      const h = headingRows(rows, [0.4, -0.3, 1])!;
      const t = turnBetween(rows, h);
      expectRows(turnedRows(rows, t.axis, t.angleRad), h);
      // At least the look's own angle: keeping the up as near as can be adds a little twist about the look
      expect(t.angleRad).toBeGreaterThanOrEqual(Math.acos(1 / Math.hypot(0.4, 0.3, 1)) - 1e-12);
      expect(t.angleRad).toBeLessThan(Math.acos(1 / Math.hypot(0.4, 0.3, 1)) + 0.01);
    }
    expect(turnBetween(ROWS, ROWS).angleRad).toBe(0);
    expect(turnedRows(ROWS, [0, 1, 0], 0)).toBe(ROWS);
  });

  it('is eased in and out over three quarters of a second, and made at once under Reduce Motion', () => {
    expect(handoverShare(0, false)).toBe(0);
    expect(handoverShare(HANDOVER_SECONDS / 2, false)).toBeCloseTo(0.5, 12);
    expect(handoverShare(HANDOVER_SECONDS / 4, false)).toBeCloseTo(0.15625, 12);
    expect(handoverShare(HANDOVER_SECONDS, false)).toBe(1);
    expect(handoverShare(5, false)).toBe(1);
    expect(handoverShare(0, true)).toBe(1);
  });

  it('heads the camera at the star frame by frame, all of it by three quarters of a second, then is done', () => {
    const star = { positionPc: [0.2, 0.1, 1] as Vec3, earthPc: 100, magnitude: 1 };
    const at: Vec3 = [0, 0, 0];
    let h = startHandover({ positionPc: at, rows: ROWS }, star);
    let rows = ROWS;
    let frames = 0;
    while (h) {
      ({ rows, handover: h } = stepHandover(h, { positionPc: at, rows }, star, 1 / 60, false));
      frames++;
    }
    expect(frames).toBeGreaterThanOrEqual(45);
    expect(frames).toBeLessThanOrEqual(46);
    expect(dot(rows.z, star.positionPc) / len(star.positionPc)).toBeCloseTo(1, 12);
    // Under Reduce Motion it is made in the first frame
    let r = startHandover({ positionPc: at, rows: ROWS }, star)!;
    const once = stepHandover(r, { positionPc: at, rows: ROWS }, star, 1 / 60, true);
    expect(once.handover).toBeNull();
    expect(dot(once.rows.z, star.positionPc) / len(star.positionPc)).toBeCloseTo(1, 12);
    // Half way through, eased: for a camera standing still, half the start's one turn made
    r = startHandover({ positionPc: at, rows: ROWS }, star)!;
    const half = stepHandover(r, { positionPc: at, rows: ROWS }, star, HANDOVER_SECONDS / 2, false);
    expectRows(half.rows, turnedRows(ROWS, r.axis, r.angleRad / 2), 12);
    // In two quarter steps, each the share's part of what is left: the look is half way to the star as in one step
    const lookOff = (rows: WcsCameraRows) => Math.acos(Math.min(1, dot(rows.z, star.positionPc) / len(star.positionPc)));
    const quarter = stepHandover(r, { positionPc: at, rows: ROWS }, star, HANDOVER_SECONDS / 4, false);
    const thenHalf = stepHandover(quarter.handover!, { positionPc: at, rows: quarter.rows }, star, HANDOVER_SECONDS / 4, false);
    expect(lookOff(thenHalf.rows) / lookOff(ROWS)).toBeCloseTo(0.5, 2);
    // No star, or the star dead ahead: no turn
    expect(startHandover({ positionPc: [0, 0, 0], rows: ROWS }, null)).toBeNull();
    expect(startHandover({ positionPc: [0, 0, 0], rows: ROWS }, { positionPc: [0, 0, 3], earthPc: 3, magnitude: 1 })).toBeNull();
  });

  it('ends looking straight at the star though the camera flew while it turned, and then flies into it', () => {
    // From a light year short, the star well off the look; the camera flies at a third of its distance a second
    const star = { positionPc: [0.08, 0.05, 0.3] as Vec3, earthPc: 100, magnitude: 1 };
    let state = { positionPc: [0, 0, 0] as Vec3, rows: ROWS };
    let h = startHandover(state, star);
    while (h) {
      let rows: WcsCameraRows;
      ({ rows, handover: h } = stepHandover(h, state, star, 1 / 60, false));
      state = nearStarStep({ positionPc: state.positionPc, rows }, 1000, { yawRadPerS: 0, pitchRadPerS: 0 }, 1 / 60, star);
    }
    const toStar = sub(star.positionPc, state.positionPc);
    expect(dot(state.rows.z, toStar) / len(toStar)).toBeCloseTo(1, 12);
    // Straight on, with no hand on the controls, it comes to the star's floor and stays
    let s = nearStarStep(state, 1000, { yawRadPerS: 0, pitchRadPerS: 0 }, 1 / 60, star);
    for (let i = 0; i < 60 * 60; i++) s = nearStarStep(s, 1000, { yawRadPerS: 0, pitchRadPerS: 0 }, 1 / 60, star);
    expect(s.distancePc / s.floorPc).toBeCloseTo(1, 9);
  });
});

describe('nearStarStep: a steering frame paced by the star', () => {
  const star = starAt([0, 0, 1], 4.83 - 5); // a hundred Suns, its floor 10 AU
  const floor = 10 * AU_PC;
  const none = { yawRadPerS: 0, pitchRadPerS: 0 };

  it('flies the set speed far from the star, its true speed the set speed', () => {
    const far = starAt([0, 0, 100], 0);
    const s = nearStarStep({ positionPc: [0, 0, 0], rows: ROWS }, 1, none, 1 / 60, far);
    expect(s.positionPc[2]).toBeCloseTo(1 / 60, 12);
    expect(s.speedPcPerS).toBeCloseTo(1, 9);
    expect(s.pacedPcPerS).toBe(1);
  });

  it('comes in on the distance over 2.5 s: a share e to the minus one of the way left after 2.5 s, whatever the set speed', () => {
    let state = { positionPc: [0, 0, 0] as Vec3, rows: ROWS };
    for (let i = 0; i < 150; i++) state = nearStarStep(state, 1000, none, 1 / 60, star);
    expect(len(sub(star.positionPc, state.positionPc))).toBeCloseTo(Math.exp(-1), 2);
  });

  it('never comes nearer than the floor: straight at the star it stops there, its true speed nothing', () => {
    let s = nearStarStep({ positionPc: [0, 0, 1 - 1.5 * floor], rows: ROWS }, 1000, none, 1 / 60, star);
    for (let i = 0; i < 600; i++) s = nearStarStep(s, 1000, none, 1 / 60, star);
    expect(s.distancePc / floor).toBeCloseTo(1, 9);
    expect(s.speedPcPerS).toBeCloseTo(0, 9);
    expect(s.pacedPcPerS).toBeCloseTo(floor / NEAR_STAR_TAU_S, 12);
  });

  it('holds there with W held for minutes, 168 pc from Earth, though rounding puts it a hair off the line', () => {
    // A star 168 pc away, its floor 1 AU; the craft on the floor's sphere, looking at the star a hair off its centre
    const far = { positionPc: [100, 120, 60] as Vec3, earthPc: Math.hypot(100, 120, 60), magnitude: Number.NaN };
    const fl = AU_PC;
    const look: Vec3 = [0, 0, 1];
    const at: Vec3 = [100 + 1e-19, 120, 60 - fl];
    const rows: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: look };
    let s = nearStarStep({ positionPc: at, rows }, 1000, none, 1 / 60, far);
    for (let i = 0; i < 120 * 60; i++) s = nearStarStep(s, 1000, none, 1 / 60, far);
    expect(len(sub(s.positionPc, at)) / fl).toBeLessThan(1e-6);
    expect(s.speedPcPerS).toBeLessThan(fl * 1e-6);
    expect(HEAD_ON_RAD).toBe(1e-4);
    // The free flight measured live reached the floor 3 microradians off: that holds too
    const hair = 3e-6;
    const hairRows: WcsCameraRows = { x: [1, 0, 0], y: [0, Math.cos(hair), -Math.sin(hair)], z: [0, Math.sin(hair), Math.cos(hair)] };
    let hs = nearStarStep({ positionPc: [100, 120, 60 - fl], rows: hairRows }, 1000, none, 1 / 60, far);
    for (let i = 0; i < 120 * 60; i++) hs = nearStarStep(hs, 1000, none, 1 / 60, far);
    expect(len(sub(hs.positionPc, [100, 120, 60 - fl])) / fl).toBeLessThan(1e-6);
    // A look a thousandth of a radian off is no hair: it slides round the sphere, as the ruling's sliding says
    const off: WcsCameraRows = { x: [1, 0, 0], y: [0, Math.cos(1e-3), -Math.sin(1e-3)], z: [0, Math.sin(1e-3), Math.cos(1e-3)] };
    let o = nearStarStep({ positionPc: [100, 120, 60 - fl], rows: off }, 1000, none, 1 / 60, far);
    for (let i = 0; i < 30 * 60; i++) o = nearStarStep(o, 1000, none, 1 / 60, far);
    expect(len(sub(o.positionPc, [100, 120, 60 - fl])) / fl).toBeGreaterThan(0.1);
  });

  it('slides round the floor\'s sphere when turned across it, at its true speed', () => {
    // At the floor, heading across the star's line
    const at: Vec3 = [0, 0, 1 - floor];
    const across: WcsCameraRows = { x: [0, 0, -1], y: [0, 1, 0], z: [1, 0, 0] };
    let s = nearStarStep({ positionPc: at, rows: across }, 1000, { yawRadPerS: -1 / NEAR_STAR_TAU_S, pitchRadPerS: 0 }, 1 / 60, star);
    for (let i = 0; i < 120; i++) s = nearStarStep(s, 1000, { yawRadPerS: -1 / NEAR_STAR_TAU_S, pitchRadPerS: 0 }, 1 / 60, star);
    expect(s.distancePc / floor).toBeGreaterThanOrEqual(1 - 1e-9);
    expect(s.distancePc / floor).toBeLessThan(1.01);
    expect(len(sub(s.positionPc, at)) / floor).toBeGreaterThan(0.5);
    expect(s.speedPcPerS / (floor / NEAR_STAR_TAU_S)).toBeGreaterThan(0.9);
  });

  it('with no star flies the set speed, as steering did before', () => {
    const s = nearStarStep({ positionPc: [1, 2, 3], rows: ROWS }, 2, { yawRadPerS: 0.3, pitchRadPerS: 0 }, 1 / 60, null);
    const before = steerCamera({ positionPc: [1, 2, 3], rows: ROWS, speedPcPerS: 2 }, { yawRadPerS: 0.3, pitchRadPerS: 0 }, 1 / 60);
    expect(s.positionPc).toEqual(before.positionPc);
    expect(s.rows).toEqual(before.rows);
    expect(s.speedPcPerS).toBeCloseTo(2, 9);
    expect(Number.isNaN(s.distancePc)).toBe(true);
    // A frame of no time: the paced speed is said
    expect(nearStarStep({ positionPc: [0, 0, 0], rows: ROWS }, 1, none, 0, star).speedPcPerS).toBeCloseTo(1 / NEAR_STAR_TAU_S, 12);
  });

  it('is paced by the star as drawn when its row is found, else by the place flown to with its light unknown, and none in free flight', () => {
    const arrival = { positionPc: [1, 1, 1] as Vec3, earthPc: Math.sqrt(3), magnitude: 2 };
    expect(nearStarOf(arrival, [9, 9, 9])).toBe(arrival);
    const placed = nearStarOf(null, [3, 0, 4])!;
    expect(placed.positionPc).toEqual([3, 0, 4]);
    expect(placed.earthPc).toBe(5);
    expect(nearStarFloorPc(placed)).toBe(AU_PC);
    expect(nearStarOf(null, [0, 0, 0])).toBeNull();
  });
});

describe('the readout near the star (condition two, until UIUX\'s record)', () => {
  it('gives light years down to a tenth of one, then astronomical units', () => {
    expect(NEAR_UNIT_BELOW_LY).toBe(0.1);
    expect(AU_PER_LY).toBeCloseTo(63241.077, 2);
    expect(readoutDistance(2.19)).toEqual({ value: 2.19, unit: 'ly' });
    expect(readoutDistance(0.1)).toEqual({ value: 0.1, unit: 'ly' });
    const near = readoutDistance(0.05);
    expect(near.unit).toBe('AU');
    expect(near.value).toBeCloseTo(0.05 * AU_PER_LY, 9);
  });

  it('says the steering speed in light years a second, and in astronomical units a second below a tenth of one', () => {
    expect(steeringSpeedLine(1 / LY_PER_PC)).toBe('this flight: 1.00 ly each second');
    expect(steeringSpeedLine(0.1 / LY_PER_PC)).toBe('this flight: 0.100 ly each second');
    expect(steeringSpeedLine((26.8 * AU_PC))).toBe('this flight: 26.8 AU each second');
  });
});
