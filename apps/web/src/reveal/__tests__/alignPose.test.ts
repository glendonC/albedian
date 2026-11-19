import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import {
  ALIGN_FALLBACK_FOV_DEG,
  alignReadout,
  alignStartWords,
  alignTargets,
  dragPose,
  fovFrom35mm,
  fovYOf,
  projectAltAz,
  scalePose,
  startPose,
  turnPose,
  type AlignPose,
} from '../alignPose.js';

// Line it up yourself (D24, UIUX-align-READY.md): the design lane's pose sum (.cache/uiux/lib/pose.mjs) and its numbers
// for rp-b-06 (a 26 mm equivalent lens, 53 by 67 degrees; heading 115.89; readout "116° · 34° up · turned 0° · 53°
// across").

const START: AlignPose = { azDeg: 115.89, altDeg: fovFrom35mm(26, 4 / 3).y / 2, rollDeg: 0, fovXDeg: fovFrom35mm(26, 4 / 3).x, aspect: 4 / 3 };

describe('the pose sum, as the design lane wrote it', () => {
  it("gives a 26 mm equivalent lens 53 by 67 degrees on a 3:4 photo, as the record says", () => {
    const fov = fovFrom35mm(26, 4 / 3);
    expect(Math.round(fov.x)).toBe(53);
    expect(Math.round(fov.y)).toBe(67);
    expect(fovYOf(START)).toBeCloseTo(fov.y, 6);
  });

  it('puts the pose centre in the middle, the horizon at the foot, and nothing behind the camera', () => {
    expect(projectAltAz(START.altDeg, START.azDeg, START)).toEqual({ u: 0.5, v: 0.5 });
    expect(projectAltAz(0, START.azDeg, START)!.v).toBeCloseTo(1, 6);
    expect(projectAltAz(START.altDeg, START.azDeg + 180, START)).toBeNull();
    // East of the centre is to the right
    expect(projectAltAz(START.altDeg, START.azDeg + 10, START)!.u).toBeGreaterThan(0.5);
  });

  it('turns about the centre: a quarter turn carries a point right of centre to below it', () => {
    const flat = projectAltAz(START.altDeg + 3, START.azDeg + 10, START)!;
    const turned = projectAltAz(START.altDeg + 3, START.azDeg + 10, { ...START, rollDeg: 90 })!;
    const [du, dv] = [flat.u - 0.5, flat.v - 0.5];
    // Clockwise by a quarter, in the photo's own square measure (its height counts aspect times its width)
    expect(turned.u - 0.5).toBeCloseTo(-dv * START.aspect, 9);
    expect((turned.v - 0.5) * START.aspect).toBeCloseTo(du, 9);
    expect(turned.v).toBeGreaterThan(0.5);
  });
});

describe('the start', () => {
  const base = sample('horizon_predicted_only');
  /** The no-match fixture with predicted stars and a planet at given heights and headings. */
  function withTargets(targets: Array<{ id: string; title: string; alt: number; az: number; mag?: number }>, focal?: number): ObservationSession {
    const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
    const findings = targets.map(
      (t) =>
        ({
          ...template,
          id: t.id,
          title: t.title,
          skyPosition: { ...template.skyPosition!, altDeg: t.alt, azDeg: t.az },
          catalogRef: t.mag === undefined ? undefined : { ...(template.catalogRef ?? {}), apparentMagnitude: t.mag },
        }) as CelestialFinding
    );
    // Placed by hand (a catalog row with a magnitude only), so the session is built from the fixture and given them after
    const session = poseSession(base);
    const asset = { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: focal === undefined ? undefined : { focalLength35mmMm: focal } };
    return { ...session, sourceAsset: asset, observationSolution: { ...session.observationSolution!, findings } as ObservationSolution } as ObservationSession;
  }
  const sky = [
    { id: 'finding-predicted-star-hip-1', title: 'Fomalhaut (Predicted Ephemeris)', alt: 20, az: 150, mag: 1.16 },
    { id: 'finding-predicted-star-hip-2', title: 'Enif', alt: 40, az: 100, mag: 2.38 },
    { id: 'finding-predicted-star-hip-3', title: 'Faint', alt: 30, az: 120, mag: 3.21 },
    { id: 'finding-predicted-star-hip-4', title: 'Below', alt: -5, az: 120, mag: -1.4 },
    { id: 'finding-predicted-saturn', title: 'Saturn (Predicted Ephemeris)', alt: 25, az: 140 },
    { id: 'finding-openngc-NGC104', title: '47 Tucanae', alt: 30, az: 160, mag: 4.1 },
  ];

  it('draws the stars of magnitude 3.2 or brighter and the planets, with their plain names', () => {
    const names = alignTargets(withTargets(sky).observationSolution).map((t) => t.name);
    expect(names.sort()).toEqual(['Below', 'Enif', 'Fomalhaut', 'Saturn']);
  });

  it("starts from the file's heading and lens with the horizon at the photo's foot and no turn, as the record's numbers", () => {
    const start = startPose(withTargets(sky, 26), 115.89)!;
    expect(start.from).toEqual({ kind: 'heading' });
    expect(alignReadout(start.pose)).toBe('116° · 34° up · turned 0° · 53° across');
    expect(alignStartWords(start.from)).toBe("Started from your photo's compass heading and lens.");
  });

  it('with no heading, faces the brightest star above the horizon and says so', () => {
    const start = startPose(withTargets(sky, 26))!;
    expect(start.from).toEqual({ kind: 'brightest', name: 'Fomalhaut' });
    expect(start.pose.azDeg).toBe(150);
    // Low, it keeps the horizon at the foot; high, the centre rises to it, so it is always in view
    expect(start.pose.altDeg).toBeCloseTo(fovFrom35mm(26, 4 / 3).y / 2, 9);
    expect(alignStartWords(start.from)).toBe('Started from the brightest star, Fomalhaut.');
    const high = startPose(withTargets([{ id: 'finding-predicted-star-hip-9', title: 'Vega', alt: 70, az: 310, mag: 0.03 }], 26))!;
    expect(high.pose.altDeg).toBe(70);
    const centred = projectAltAz(70, 310, high.pose)!;
    expect(centred.u).toBeCloseTo(0.5, 9);
    expect(centred.v).toBeCloseTo(0.5, 9);
  });

  it('with no lens, starts at the proposed field across, and with nothing above the horizon, offers no start', () => {
    expect(startPose(withTargets(sky))!.pose.fovXDeg).toBe(ALIGN_FALLBACK_FOV_DEG);
    expect(startPose(withTargets([sky[3]!]))).toBeNull();
  });

  it('reads a photo turned a quarter as tall as it is shown', () => {
    const session = withTargets(sky, 26);
    const turned = { ...session, sourceAsset: { ...session.sourceAsset!, width: 4032, height: 3024, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    expect(startPose(turned, 100)!.pose.aspect).toBeCloseTo(4 / 3, 9);
  });
});

describe("the person's drag, turn and scale", () => {
  it('moves the stars with the pointer: a drag right turns the view left, a drag down raises it', () => {
    // The star under the centre follows the pointer to within 5 percent (the tangent plane bends away from it)
    const after = projectAltAz(START.altDeg, START.azDeg, dragPose(START, 40, 30, 675))!;
    expect((after.u - 0.5) * 675).toBeGreaterThan(38);
    expect((after.u - 0.5) * 675).toBeLessThan(42);
    expect((after.v - 0.5) * 900).toBeGreaterThan(28.5);
    expect((after.v - 0.5) * 900).toBeLessThan(31.5);
  });

  it('keeps the drag in the turned axes once turned', () => {
    const turned = { ...START, rollDeg: 90 };
    const star = { alt: START.altDeg, az: START.azDeg };
    const after = projectAltAz(star.alt, star.az, dragPose(turned, 40, 0, 675))!;
    expect(after.u - 0.5).toBeGreaterThan(0.04);
    expect(Math.abs(after.v - 0.5)).toBeLessThan(0.01);
  });

  it('turns by the angle swept, within a half turn either way, and scales the field between 5 and 150 degrees', () => {
    expect(turnPose(START, 30).rollDeg).toBe(30);
    expect(turnPose({ ...START, rollDeg: 170 }, 20).rollDeg).toBe(-170);
    expect(scalePose(START, 0.5).fovXDeg).toBeCloseTo(START.fovXDeg / 2, 9);
    expect(scalePose(START, 100).fovXDeg).toBe(150);
    expect(scalePose(START, 0.01).fovXDeg).toBe(5);
  });

  it('writes the readout in whole degrees, the heading within 0 to 359, the height and turn signed', () => {
    expect(alignReadout({ ...START, azDeg: 359.7, altDeg: -3.4, rollDeg: -12.6, fovXDeg: 30.2 })).toBe('0° · -3° up · turned -13° · 30° across');
    expect(alignReadout({ ...START, rollDeg: -0.2 })).toBe('116° · 34° up · turned 0° · 53° across');
  });

  it('keeps the heading within 0 to 360 and the height within the poles after any drag (property, 20 seeds)', () => {
    for (let seed = 1; seed <= 20; seed += 1) {
      fc.assert(
        fc.property(fc.double({ min: -5000, max: 5000, noNaN: true }), fc.double({ min: -5000, max: 5000, noNaN: true }), fc.double({ min: -180, max: 180, noNaN: true }), (dx, dy, roll) => {
          const pose = dragPose({ ...START, rollDeg: roll }, dx, dy, 675);
          return pose.azDeg >= 0 && pose.azDeg < 360 && pose.altDeg >= -89 && pose.altDeg <= 89;
        }),
        { seed, numRuns: 50 }
      );
    }
  });
});
