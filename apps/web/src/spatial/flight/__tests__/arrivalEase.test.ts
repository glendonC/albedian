import { describe, expect, it } from 'vitest';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { ARRIVAL_EASE_S, AU_PC, NEAR_STAR_TAU_S, nearStarStep, type NearStep } from '../nearStar.js';

// craft-alive-three version two, its first commit (DECIDED-BY-LEAD 27 Nov 11:59, MOTION's approach-curve-numbers.md item
// 1): the arrival eased as the owner's reference stops its scene: speed = min(the ruled pace, (distance - floor) / 0.07 s),
// which takes over below 1.029 floors and closes 95 percent of the way left in 0.2 s; the floor is never crossed. The
// ease holds the part of each step that goes in toward the star, so a turned flight still slides round the floor (as
// ruled) and a flight away from the star leaves at its pace.

const ROWS: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const none = { yawRadPerS: 0, pitchRadPerS: 0 };
const len = (v: Vec3) => Math.hypot(v[0], v[1], v[2]);
const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
// A hundred Suns at 10 pc, its floor 10 AU, straight ahead of the camera along +z
const star = { positionPc: [0, 0, 1] as Vec3, earthPc: 10, magnitude: 4.83 - 5 };
const floor = 10 * AU_PC;
const DT = 1 / 60;

/** Straight at the star from `startFloors` floors, `frames` frames of DT at a set speed far above the pace. */
function approach(startFloors: number, frames: number, dt = DT): NearStep[] {
  let s: NearStep = nearStarStep({ positionPc: [0, 0, 1 - startFloors * floor], rows: ROWS }, 1000, none, dt, star);
  const out = [s];
  for (let i = 0; i < frames; i++) out.push((s = nearStarStep(s, 1000, none, dt, star)));
  return out;
}

describe('the arrival eased (MOTION item 1)', () => {
  it('is 0.07 s, the reference\'s own stop', () => {
    expect(ARRIVAL_EASE_S).toBe(0.07);
  });

  it('keeps the ruled pace above 1.029 floors: the way left falls by dt over 2.5 s a frame', () => {
    const run = approach(1.5, 600);
    let checked = 0;
    for (let i = 1; i < run.length; i++) {
      const before = run[i - 1]!.distancePc;
      if (before / floor < 1.04) break;
      expect((before - run[i]!.distancePc) / before).toBeCloseTo(DT / NEAR_STAR_TAU_S, 9);
      checked += 1;
    }
    expect(checked).toBeGreaterThan(20);
  });

  it('below 1.029 floors closes the way to the floor e-fold every 0.07 s, never inside it', () => {
    const run = approach(1.5, 600);
    const share = Math.exp(-DT / ARRIVAL_EASE_S);
    let eased = 0;
    for (let i = 1; i < run.length; i++) {
      const before = run[i - 1]!.distancePc - floor;
      const after = run[i]!.distancePc - floor;
      expect(after).toBeGreaterThan(0);
      // Read while a ten-thousandth of the floor is left: a place 1 pc out holds about 2e-16 pc, so smaller ways left
      // carry rounding past six figures
      if (run[i - 1]!.distancePc / floor < 1.028 && before > floor * 1e-4) {
        expect(after / before).toBeCloseTo(share, 6);
        eased += 1;
      }
    }
    expect(eased).toBeGreaterThan(20);
  });

  it('takes about a quarter of a second from where the ease begins to within a thousandth of the floor, 95 percent of it in 0.2 s', () => {
    const fine = 1 / 1000;
    const run = approach(1.1, 4000, fine);
    const begins = run.findIndex((s) => s.distancePc / floor <= 1 / (1 - ARRIVAL_EASE_S / NEAR_STAR_TAU_S));
    const hovers = run.findIndex((s) => s.distancePc / floor <= 1.001);
    expect(begins).toBeGreaterThan(0);
    // 0.07 s times ln(0.0288 / 0.001), as MOTION's 95 percent in 0.2 s says
    expect((hovers - begins) * fine).toBeCloseTo(ARRIVAL_EASE_S * Math.log((1 / (1 - ARRIVAL_EASE_S / NEAR_STAR_TAU_S) - 1) / 0.001), 2);
    const left = (i: number) => run[i]!.distancePc - floor;
    const at02 = begins + Math.round(0.2 / fine);
    expect(1 - left(at02) / left(begins)).toBeGreaterThan(0.94);
    // Where the old approach stopped dead at the floor's pace, the arrival's speed there is a hair of it
    expect(run[hovers]!.speedPcPerS).toBeLessThan((floor / NEAR_STAR_TAU_S) * 0.05);
  });

  it('holds a long frame too: a 120 ms frame near the floor ends outside it, on the same curve', () => {
    let s: NearStep = nearStarStep({ positionPc: [0, 0, 1 - 1.01 * floor], rows: ROWS }, 1000, none, 0.12, star);
    expect(s.distancePc).toBeGreaterThan(floor);
    expect((s.distancePc - floor) / (0.01 * floor)).toBeCloseTo(Math.exp(-0.12 / ARRIVAL_EASE_S), 9);
    s = nearStarStep(s, 1000, none, 0.12, star);
    expect(s.distancePc).toBeGreaterThan(floor);
  });

  it('holds only what goes in: turned across at the floor the flight slides on at its pace', () => {
    const at: Vec3 = [0, 0, 1 - floor];
    const across: WcsCameraRows = { x: [0, 0, -1], y: [0, 1, 0], z: [1, 0, 0] };
    const turn = { yawRadPerS: -1 / NEAR_STAR_TAU_S, pitchRadPerS: 0 };
    let s: NearStep = nearStarStep({ positionPc: at, rows: across }, 1000, turn, DT, star);
    for (let i = 0; i < 120; i++) s = nearStarStep(s, 1000, turn, DT, star);
    expect(s.distancePc / floor).toBeGreaterThanOrEqual(1 - 1e-9);
    expect(s.distancePc / floor).toBeLessThan(1.01);
    expect(len(sub(s.positionPc, at)) / floor).toBeGreaterThan(0.5);
    expect(s.speedPcPerS / (floor / NEAR_STAR_TAU_S)).toBeGreaterThan(0.9);
  });

  it('turned half across within the ease, comes in by the ease\'s own share and keeps its part across whole', () => {
    // 1.02 floors out, the look 45 degrees off the line to the star
    const c = Math.SQRT1_2;
    const oblique: WcsCameraRows = { x: [c, 0, -c], y: [0, 1, 0], z: [c, 0, c] };
    const from: Vec3 = [0, 0, 1 - 1.02 * floor];
    const s = nearStarStep({ positionPc: from, rows: oblique }, 1000, none, DT, star);
    const moved = sub(s.positionPc, from);
    const room = 0.02 * floor * (1 - Math.exp(-DT / ARRIVAL_EASE_S));
    expect(moved[2] / room).toBeCloseTo(1, 6);
    // The part across is the paced step's own: the pace (1.02 floors over 2.5 s) times the frame, times sin 45 degrees
    expect(moved[0] / (((1.02 * floor) / NEAR_STAR_TAU_S) * DT * c)).toBeCloseTo(1, 6);
  });

  it('moves a camera already inside the floor (a star chosen from within it) out to the floor', () => {
    const inside: Vec3 = [0, 0, 1 - 0.5 * floor];
    const away: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, -1] };
    for (const rows of [ROWS, away]) {
      const s = nearStarStep({ positionPc: inside, rows }, 1000, none, DT, star);
      expect(s.distancePc / floor).toBeGreaterThanOrEqual(1 - 1e-9);
    }
  });

  it('never holds a flight away from the star: from a hair off the floor it leaves at its pace', () => {
    const away: WcsCameraRows = { x: [-1, 0, 0], y: [0, 1, 0], z: [0, 0, -1] };
    const s = nearStarStep({ positionPc: [0, 0, 1 - 1.0001 * floor], rows: away }, 1000, none, DT, star);
    expect(s.speedPcPerS / ((1.0001 * floor) / NEAR_STAR_TAU_S)).toBeCloseTo(1, 6);
    expect(s.distancePc).toBeGreaterThan(1.0001 * floor);
  });
});
