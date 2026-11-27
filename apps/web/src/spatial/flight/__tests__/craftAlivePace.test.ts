import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { ALIVE_AT_REST, BRAKE_BEFORE_HOVER_S, aliveAsk, paceAsk, stepAlive } from '../craftAlive.js';
import { poseWeights } from '../craftPoses.js';
import { ARRIVAL_EASE_S, AU_PC, HOVER_WITHIN_FLOORS, NEAR_STAR_TAU_S, nearStarStep, timeToHoverS, type NearStep } from '../nearStar.js';

// craft-alive-three version two (DECIDED-BY-LEAD 27 Nov 11:59, MOTION's approach-curve-numbers.md item 2): the suit's
// poses on a paced approach by time to the hover, as the owner's reference leaves cruise: cruise held whole until three
// quarters of a second before the hover, the brake from there on its own smoothing, hover once within 1.001 floors.

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const LY = 1 / 3.261563777;
const FLOOR = 112 * AU_PC;
const EASE_FROM = FLOOR / (1 - ARRIVAL_EASE_S / NEAR_STAR_TAU_S);

describe('the ruled numbers', () => {
  it('are MOTION\'s: the brake three quarters of a second before the hover, the hover within 1.001 floors', () => {
    expect(BRAKE_BEFORE_HOVER_S).toBe(0.75);
    expect(HOVER_WITHIN_FLOORS).toBe(1.001);
  });
});

describe('timeToHoverS: straight at the star, the set speed, the pace and the ease, whichever is slowest', () => {
  it('is 0 within 1.001 floors and nearer', () => {
    expect(timeToHoverS(FLOOR * 1.001, LY, FLOOR)).toBe(0);
    expect(timeToHoverS(FLOOR * 1.0005, LY, FLOOR)).toBe(0);
    expect(timeToHoverS(FLOOR, LY, FLOOR)).toBe(0);
    expect(timeToHoverS(FLOOR * 0.5, LY, FLOOR)).toBe(0);
  });

  it('is the ease\'s 0.07 s times ln(0.0288 / 0.001) where the ease takes over, and adds 2.5 s times ln of the distance over that beyond', () => {
    const easeOnly = ARRIVAL_EASE_S * Math.log((EASE_FROM - FLOOR) / (FLOOR * 0.001));
    expect(easeOnly).toBeCloseTo(0.2355, 3);
    expect(timeToHoverS(EASE_FROM, LY, FLOOR)).toBeCloseTo(easeOnly, 12);
    expect(timeToHoverS(FLOOR * 3, LY, FLOOR)).toBeCloseTo(easeOnly + NEAR_STAR_TAU_S * Math.log((FLOOR * 3) / EASE_FROM), 12);
    // So the brake begins at 1.264 floors (MOTION's 1.35 leaves the ease out)
    const brakeAt = EASE_FROM * Math.exp((BRAKE_BEFORE_HOVER_S - easeOnly) / NEAR_STAR_TAU_S);
    expect(brakeAt / FLOOR).toBeCloseTo(1.264, 3);
    expect(timeToHoverS(brakeAt, LY, FLOOR)).toBeCloseTo(0.75, 12);
  });

  it('adds the set speed\'s straight run where the distance over 2.5 s is above the set speed', () => {
    const paceMeetsSet = LY * NEAR_STAR_TAU_S;
    expect(timeToHoverS(paceMeetsSet + LY * 10, LY, FLOOR)).toBeCloseTo(timeToHoverS(paceMeetsSet, LY, FLOOR) + 10, 9);
  });

  it('agrees with the flight itself, frame by frame, at a fast and at a slow set speed', () => {
    const star = { positionPc: [0, 0, 1] as Vec3, earthPc: 10, magnitude: 4.83 - 5 };
    const floor = 10 * AU_PC;
    const rows: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
    const dt = 1 / 1000;
    for (const [set, startFloors] of [[1000, 4], [floor * 0.2, 1.5], [floor * 0.01, 1.2]] as const) {
      let s: NearStep = nearStarStep({ positionPc: [0, 0, 1 - startFloors * floor], rows }, set, { yawRadPerS: 0, pitchRadPerS: 0 }, dt, star);
      const want = timeToHoverS(s.distancePc, set, floor);
      let t = 0;
      while (s.distancePc > floor * HOVER_WITHIN_FLOORS && t < 1000) {
        s = nearStarStep(s, set, { yawRadPerS: 0, pitchRadPerS: 0 }, dt, star);
        t += dt;
      }
      expect(Math.abs(t - want)).toBeLessThan(want * 0.002 + 3 * dt);
    }
  });

  it('only grows with the distance, at any set speed', () => {
    fc.assert(
      fc.property(fc.double({ min: 1.0005, max: 1e6, noNaN: true }), fc.double({ min: 1.0005, max: 1e6, noNaN: true }), fc.double({ min: 1e-6, max: 10, noNaN: true }), (a, b, set) => {
        const [nearer, farther] = a < b ? [a, b] : [b, a];
        expect(timeToHoverS(FLOOR * nearer, set, FLOOR)).toBeLessThanOrEqual(timeToHoverS(FLOOR * farther, set, FLOOR) + 1e-9);
      }),
      { seed: SEED, numRuns: 200 }
    );
  });
});

describe('paceAsk: the poses by time to the hover', () => {
  const at = (distancePc: number, setPcPerS = LY, speedDirection = 0) => paceAsk({ setPcPerS, floorPc: FLOOR, distancePc, speedDirection });
  const brakeAt = EASE_FROM * Math.exp((BRAKE_BEFORE_HOVER_S - ARRIVAL_EASE_S * Math.log((EASE_FROM - FLOOR) / (FLOOR * 0.001))) / NEAR_STAR_TAU_S);

  it('asks the whole cruise from far out until three quarters of a second before the hover', () => {
    for (const d of [LY * 10, LY, FLOOR * 100, FLOOR * 2, brakeAt * 1.0001]) expect(at(d)).toEqual({ cruise: 1, brake: 0 });
  });

  it('asks the whole brake in the last three quarters of a second, and neither once within 1.001 floors: hover', () => {
    for (const d of [brakeAt * 0.9999, FLOOR * 1.1, FLOOR * 1.01, FLOOR * 1.0011]) expect(at(d)).toEqual({ cruise: 0, brake: 1 });
    for (const d of [FLOOR * 1.001, FLOOR]) expect(at(d)).toEqual({ cruise: 0, brake: 0 });
  });

  it('takes the brake by time at a slow set speed too, further out than at the pace', () => {
    const slow = FLOOR * 0.2;
    const near = (d: number) => timeToHoverS(d, slow, FLOOR);
    let lo = FLOOR * 1.001;
    let hi = FLOOR * 10;
    for (let i = 0; i < 200; i++) {
      const mid = (lo + hi) / 2;
      if (near(mid) > BRAKE_BEFORE_HOVER_S) hi = mid;
      else lo = mid;
    }
    expect(at(hi * 1.001, slow)).toEqual({ cruise: 1, brake: 0 });
    expect(at(lo * 0.999, slow)).toEqual({ cruise: 0, brake: 1 });
    expect(lo).toBeLessThan(brakeAt);
  });

  it('W held asks the whole cruise and S held the whole brake, at the floor too', () => {
    expect(at(LY, LY, 1)).toEqual({ cruise: 1, brake: 0 });
    expect(at(FLOOR, LY, 1)).toEqual({ cruise: 1, brake: 0 });
    expect(at(LY, LY, -1)).toEqual({ cruise: 0, brake: 1 });
    expect(at(FLOOR, LY, -1)).toEqual({ cruise: 0, brake: 1 });
  });

  it('the brake only ever comes nearer the floor, never further out, and the two never ask more than the whole', () => {
    fc.assert(
      fc.property(fc.double({ min: 1.002, max: 1e5, noNaN: true }), fc.double({ min: 1.002, max: 1e5, noNaN: true }), (a, b) => {
        const [nearer, farther] = a < b ? [a, b] : [b, a];
        const n = at(FLOOR * nearer);
        const f = at(FLOOR * farther);
        expect(n.brake).toBeGreaterThanOrEqual(f.brake);
        expect(n.cruise + n.brake).toBe(1);
      }),
      { seed: SEED, numRuns: 200 }
    );
  });
});

describe('stepAlive with a pace', () => {
  const hands = { turnShare: 0, dragHeld: false, speedDirection: 0 as const };

  it('follows the pace\'s cruise and brake on their own time constants, in place of the speed against the start speed', () => {
    let a = ALIVE_AT_REST;
    for (let i = 0; i < 300; i++) a = stepAlive(a, aliveAsk(hands, 0.001, false, { cruise: 0.8, brake: 0.2 }), 1 / 60);
    expect(a.speed).toBeCloseTo(0.8, 4);
    expect(a.brake).toBeCloseTo(0.2, 4);
    const w = poseWeights(a);
    expect(w.cruise).toBeCloseTo(0.64, 4);
    expect(w.brake).toBeCloseTo(0.2, 4);
  });

  it('holds hover under Reduce Motion whatever the pace asks', () => {
    let a = ALIVE_AT_REST;
    for (let i = 0; i < 300; i++) a = stepAlive(a, aliveAsk(hands, 0.001, true, { cruise: 1, brake: 1 }), 1 / 60);
    expect(poseWeights(a)).toEqual({ hover: 1, cruise: 0, brake: 0 });
  });

  it('without a pace, as before: the speed against the start speed, and the brake on S alone', () => {
    let a = ALIVE_AT_REST;
    for (let i = 0; i < 300; i++) a = stepAlive(a, aliveAsk(hands, 0.001, false), 1 / 60);
    expect(a.speed).toBe(0);
    expect(a.brake).toBe(0);
  });
});
