import { describe, expect, it } from 'vitest';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import { ALIVE, ALIVE_AT_REST, ALIVE_TAU, aliveAsk, aliveFrame, aliveFraming, diameterPerHeight, screenBoxOf, capLead, maxLeadRad, aliveRotation, axisRotation, chaseAxes, rolledRows, rotatedAboutCentre, speedShare, stepAlive } from '../craftAlive.js';
import { STEER_KEY_TURN_RAD_S, STEER_START_SPEED_PC_S } from '../steering.js';
import { CRAFTS, HANGAR_CRAFTS } from '../crafts.js';
import { dispatchAlbedianAction } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { ObservationSession } from '@albedian/contracts';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// craft-alive-one (DECIDED-BY-LEAD 25 Nov 03:22, MOTION's craft-alive-proposal): the craft alive while steered.

const ROWS: WcsCameraRows = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const apply = (m: readonly number[], v: Vec3): Vec3 => [0, 1, 2].map((i) => m[i * 3]! * v[0] + m[i * 3 + 1]! * v[1] + m[i * 3 + 2]! * v[2]) as Vec3;
const angleDeg = (a: Vec3, b: Vec3) => (Math.acos(Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))) * 180) / Math.PI;
const run = (ask: Parameters<typeof stepAlive>[1], seconds: number, dt = 1 / 60) => {
  let s = ALIVE_AT_REST;
  for (let t = 0; t < seconds - 1e-9; t += dt) s = stepAlive(s, ask, dt);
  return s;
};
const ASK = { turnShare: 0, speedPcPerS: 1, startPcPerS: 1, braking: false, reduced: false };

describe('the ruled numbers (MOTION craft-alive-proposal, DECIDED-BY-LEAD 03:22)', () => {
  it('are the proposal\'s, word for word', () => {
    expect(ALIVE).toEqual({ suitLeanStartDeg: 20, suitLeanCruiseDeg: 75, suitBrakeDeg: -35, suitBankDeg: 30, craftBankDeg: 12, craftNoseLeadDeg: 6, fallBackAtCruise: 0.45, cameraRollShare: 0.35, chaseElevationDeg: 15, shoulderDeg: 35, chaseXShare: 0.4 });
    expect(ALIVE_TAU).toEqual({ bank: 0.15, speed: 0.4, brake: 0.2, cameraRoll: 0.3 });
  });
});

describe('aliveAsk and aliveFrame', () => {
  it('asks the turn as a share of the key turn, the speed against the start speed, and the brake only on S', () => {
    expect(aliveAsk(-STEER_KEY_TURN_RAD_S, 0, 2, false)).toEqual({ turnShare: -1, speedPcPerS: 2, startPcPerS: STEER_START_SPEED_PC_S, braking: false, reduced: false });
    expect(aliveAsk(0, -1, 1, false).braking).toBe(true);
    expect(aliveAsk(0, 1, 1, false).braking).toBe(false);
    expect(aliveAsk(0, 0, 1, true).reduced).toBe(true);
  });

  it('hands the craft layer the drawn, rolled camera, the craft\'s own axes and the signals', () => {
    const alive = { ...ALIVE_AT_REST, bank: 1, roll: ALIVE.cameraRollShare };
    const s = { positionPc: [1, 2, 3] as Vec3, chase: ROWS, rows: { x: [0, 1, 0], y: [-1, 0, 0], z: [0, 0, 1] } as WcsCameraRows };
    const frame = aliveFrame(s, alive);
    expect(frame.drawn).toEqual(rolledRows(ROWS, alive));
    expect(frame.craft).toEqual({ positionPc: [1, 2, 3], rows: frame.drawn, craftRows: s.rows, alive });
  });
});

describe('speedShare', () => {
  it('is 0 at the start speed, 1 at sixteen times it, on doublings, held to 0 to 1', () => {
    expect(speedShare(1, 1)).toBe(0);
    expect(speedShare(4, 1)).toBeCloseTo(0.5, 12);
    expect(speedShare(16, 1)).toBe(1);
    expect(speedShare(64, 1)).toBe(1);
    expect(speedShare(0.5, 1)).toBe(0);
    expect(speedShare(0, 1)).toBe(0);
  });
});

describe('stepAlive', () => {
  it('reaches 63 percent of a held turn in its time constant, and the camera rolls with 35 percent of the bank', () => {
    const one = run({ ...ASK, turnShare: 1 }, ALIVE_TAU.bank);
    expect(one.bank).toBeCloseTo(1 - Math.exp(-1), 2);
    const held = run({ ...ASK, turnShare: 1 }, 5);
    expect(held.bank).toBeCloseTo(1, 6);
    expect(held.roll).toBeCloseTo(ALIVE.cameraRollShare, 4);
    expect(run({ ...ASK, turnShare: -3 }, 5).bank).toBeCloseTo(-1, 6);
  });

  it('follows speed on its own time constant, and the brake while it is held', () => {
    expect(run({ ...ASK, speedPcPerS: 16 }, ALIVE_TAU.speed).speed).toBeCloseTo(1 - Math.exp(-1), 2);
    expect(run({ ...ASK, braking: true }, ALIVE_TAU.brake).brake).toBeCloseTo(1 - Math.exp(-1), 2);
    expect(stepAlive(ALIVE_AT_REST, { ...ASK, turnShare: 1 }, 0)).toEqual(ALIVE_AT_REST);
  });

  it('keeps everything at rest under Reduce Motion', () => {
    expect(run({ ...ASK, turnShare: 1, speedPcPerS: 16, braking: true, reduced: true }, 3)).toEqual(ALIVE_AT_REST);
  });
});

describe('aliveRotation', () => {
  it('leans the suit 20 degrees at the start speed, 75 at sixteen times it, back 35 on the brake', () => {
    for (const [alive, lean] of [[ALIVE_AT_REST, 20], [{ ...ALIVE_AT_REST, speed: 1 }, 75], [{ ...ALIVE_AT_REST, speed: 1, brake: 1 }, 40]] as const) {
      const up = apply(aliveRotation('suit', ROWS, alive), [0, 1, 0]);
      expect(angleDeg(up, [0, 1, 0])).toBeCloseTo(lean, 9);
      // Leaning about its right axis keeps the right axis
      expect(apply(aliveRotation('suit', ROWS, alive), [1, 0, 0])).toEqual([1, 0, 0].map((v) => expect.closeTo(v, 12)));
    }
  });

  it('banks the suit 30 degrees at the full turn, a spacecraft 12 with its nose 6 ahead, and nothing at rest', () => {
    const suitRight = apply(aliveRotation('suit', ROWS, { ...ALIVE_AT_REST, bank: 1 }), [1, 0, 0]);
    expect(angleDeg(suitRight, [1, 0, 0])).toBeCloseTo(30, 9);
    const m = aliveRotation('craft', ROWS, { ...ALIVE_AT_REST, bank: 1 });
    expect(angleDeg(apply(m, [0, 0, 1]), [0, 0, 1])).toBeCloseTo(ALIVE.craftNoseLeadDeg, 9);
    expect(aliveRotation('craft', ROWS, ALIVE_AT_REST).map((v) => Math.round(v * 1e12) / 1e12)).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });
});

describe('rotatedAboutCentre', () => {
  it('turns the model about its own centre, so the centre stays where it was drawn', () => {
    const m = [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 5, -3, 7, 1];
    const centre: Vec3 = [0.5, 1, -0.25];
    const at = (mm: readonly number[], v: Vec3) => [0, 1, 2].map((i) => mm[i]! * v[0] + mm[4 + i]! * v[1] + mm[8 + i]! * v[2] + mm[12 + i]!);
    const turned = rotatedAboutCentre(m, centre, axisRotation([0, 0, 1], 0.7));
    at(turned, centre).forEach((v, i) => expect(v).toBeCloseTo(at(m, centre)[i]!, 12));
    expect(at(turned, [1.5, 1, -0.25])).not.toEqual(at(m, [1.5, 1, -0.25]));
  });
});

describe('rolledRows', () => {
  it('rolls the drawn camera about its look by the roll share of 30 degrees, keeping its axes square', () => {
    const r = rolledRows(ROWS, { ...ALIVE_AT_REST, roll: ALIVE.cameraRollShare });
    expect(r.z).toEqual([0, 0, 1]);
    expect(angleDeg(r.x as Vec3, [1, 0, 0])).toBeCloseTo(ALIVE.suitBankDeg * ALIVE.cameraRollShare, 9);
    expect(r.x[0] * r.y[0] + r.x[1] * r.y[1] + r.x[2] * r.y[2]).toBeCloseTo(0, 12);
    expect(rolledRows(ROWS, ALIVE_AT_REST)).toEqual(ROWS);
  });
});

describe('aliveFraming', () => {
  it('fills a third of the frame\'s height at the start speed and 55 percent of that at sixteen times it', () => {
    const base = { xPx: 720, yPx: 558, diameterPx: 320, frameHeightPx: 900 };
    expect(aliveFraming(base, ALIVE_AT_REST)).toEqual({ ...base, diameterPx: 300 });
    expect(aliveFraming(base, { ...ALIVE_AT_REST, speed: 1 }).diameterPx).toBeCloseTo(165, 9);
    // The phone frames in the sky above its readout
    expect(aliveFraming({ xPx: 195, yPx: 400, diameterPx: 184, frameHeightPx: 480 }, ALIVE_AT_REST).diameterPx).toBe(160);
  });
});

describe('chaseAxes', () => {
  it('looks along the line of flight from 15 degrees above, square, for every hangar craft', () => {
    const behind = chaseAxes([0, 0, 1], [0, 1, 0], ALIVE.chaseElevationDeg, 0);
    expect(angleDeg(behind.forward, [0, 0, 1])).toBeCloseTo(ALIVE.chaseElevationDeg, 9);
    expect(behind.forward[1]).toBeLessThan(0);
    // craft-alive-shoulder-one (06:40): then turned 35 degrees about the craft's up, over its shoulder
    const { forward, up } = chaseAxes([0, 0, 1], [0, 1, 0]);
    expect(angleDeg(forward, behind.forward)).toBeCloseTo(ALIVE.shoulderDeg, 9);
    expect(up).toEqual(behind.up);
    expect(forward[0] * up[0] + forward[1] * up[1] + forward[2] * up[2]).toBeCloseTo(0, 12);
    for (const craft of HANGAR_CRAFTS) {
      expect(craft.flightForward, craft.id).toEqual([0, 0, 1]);
      expect(craft.flightUp, craft.id).toEqual([0, 1, 0]);
    }
    // The suit at rest faces the camera (NASA's picture); in the chase its back does
    expect(CRAFTS['astronaut-emu']!.modelForward).not.toEqual(CRAFTS['astronaut-emu']!.flightForward);
  });
});

describe('the bank on screen, through the app\'s own steering axes', () => {
  // The steering rows are the camera's: x to the right on screen, y up, z the look, so right x up points at the viewer
  function startRows(): WcsCameraRows {
    const base = createSyntheticCalibratedSession();
    const session = { ...base, sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 1920, height: 1080, durationMs: 0 }, spatialView: { ...base.spatialView, mode: 'source_2d' } } as ObservationSession;
    const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
    const there = dispatchAlbedianAction(session, { type: 'space.enter', requestId: 'go', expectedViewRevision: session.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated' } as never).session;
    return steerStartOf(flightCameraOf(there, 1440, 900)!).rows;
  }
  const onScreen = (v: Vec3, rows: WcsCameraRows) => [v[0] * rows.x[0] + v[1] * rows.x[1] + v[2] * rows.x[2], v[0] * rows.y[0] + v[1] * rows.y[1] + v[2] * rows.y[2]];

  it('tips the craft\'s top toward the turn and its nose into it, with the camera rolling the same way', () => {
    const rows = startRows();
    const turn = (bank: number) => ({ ...ALIVE_AT_REST, bank, roll: ALIVE.cameraRollShare * bank });
    for (const kind of ['suit', 'craft'] as const) {
      for (const bank of [-1, 1]) {
        const drawn = rolledRows(rows, turn(bank));
        const m = aliveRotation(kind, rows, { ...turn(bank), speed: 0 });
        // A left turn is a negative bank: the top tips left on screen, the right turn right
        const top = onScreen(apply(m, rows.y as Vec3), drawn);
        expect(Math.sign(top[0]!), `${kind} ${bank}`).toBe(Math.sign(bank));
      }
      // The nose leads into the turn, left in a left turn
      const nose = onScreen(apply(aliveRotation(kind, rows, turn(-1)), rows.z as Vec3), rows);
      if (kind === 'craft') expect(nose[0]).toBeLessThan(0);
    }
    // The camera rolls with the craft: its up tips the same way as the craft's top, so the bank shows less than it is
    const drawnUp = rolledRows(rows, turn(-1)).y as Vec3;
    expect(onScreen(drawnUp, rows)[0]).toBeLessThan(0);
  });
});

describe('the over-the-shoulder framing (craft-alive-shoulder-one, DECIDED-BY-LEAD 06:40)', () => {
  it('sizes the craft by its height: the framing\'s diameter per unit of its height on screen', () => {
    // A box 2 wide, 4 high, 1 deep, its sphere radius 2.3: seen upright, its height is 4
    expect(diameterPerHeight(2.3, [2, 4, 1], [0, 1, 0])).toBeCloseTo(4.6 / 4, 12);
    // Tipped toward the camera its height on screen takes in some depth
    expect(diameterPerHeight(2.3, [2, 4, 1], [0, Math.cos(0.3), Math.sin(0.3)])).toBeCloseTo(4.6 / (4 * Math.cos(0.3) + Math.sin(0.3)), 12);
    expect(diameterPerHeight(2.3, [0, 0, 0], [0, 1, 0])).toBe(1);
  });

  it('frames a third of the frame\'s height of craft at 40 percent of the width, falling back with speed', () => {
    const base = { xPx: 720, yPx: 558, diameterPx: 320, frameHeightPx: 900 };
    expect(aliveFraming(base, ALIVE_AT_REST, { viewportWidthPx: 1440, diameterPerHeight: 1.5 })).toEqual({ ...base, xPx: 576, diameterPx: 450 });
    expect(aliveFraming(base, { ...ALIVE_AT_REST, speed: 1 }, { viewportWidthPx: 1440, diameterPerHeight: 1.5 }).diameterPx).toBeCloseTo(247.5, 9);
  });

  it('reads the craft\'s box on screen through the drawn matrices', () => {
    // Identity model, a projection that maps x, y straight to the clip (w = 1): the unit box at the origin spans the
    // window's middle half
    const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    expect(screenBoxOf(identity, identity, [0, 0, 0], [1, 1, 1], 200, 100)).toEqual({ x0: 50, y0: 25, x1: 150, y1: 75 });
    const behind = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1];
    expect(screenBoxOf(identity, behind, [0, 0, 0], [1, 1, 1], 200, 100)).toBeNull();
  });
});

describe('the lead held within a quarter of the width (craft-alive-shoulder-one, DECIDED-BY-LEAD 06:55)', () => {
  it('lets the chase fall behind the craft\'s heading by at most the angle a quarter of the width subtends', () => {
    const max = maxLeadRad(1440, 1000);
    expect(max).toBeCloseTo(Math.atan(360 / 1000), 12);
    const craft: WcsCameraRows = { x: [Math.cos(0.8), 0, -Math.sin(0.8)], y: [0, 1, 0], z: [Math.sin(0.8), 0, Math.cos(0.8)] };
    const capped = capLead(ROWS, craft, max);
    expect(angleDeg(capped.z as Vec3, craft.z as Vec3)).toBeCloseTo((max * 180) / Math.PI, 9);
    expect(capped.x[0] * capped.z[0] + capped.x[1] * capped.z[1] + capped.x[2] * capped.z[2]).toBeCloseTo(0, 12);
    // Within the bound the chase is left as it is
    const near: WcsCameraRows = { x: [Math.cos(0.1), 0, -Math.sin(0.1)], y: [0, 1, 0], z: [Math.sin(0.1), 0, Math.cos(0.1)] };
    expect(capLead(ROWS, near, max)).toBe(ROWS);
  });
});
