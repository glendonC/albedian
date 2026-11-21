import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import {
  ALIGN_FALLBACK_FOV_DEG,
  alignReadout,
  alignStartWords,
  alignTargets,
  fovFrom35mm,
  fovYOf,
  grabPose,
  pinchPose,
  projectAltAz,
  scalePose,
  skyAt,
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
          catalogRef: t.mag === undefined ? (/(mercury|venus|mars|jupiter|saturn|uranus|neptune)$/.test(t.id) ? { ...(template.catalogRef ?? {}), objectType: 'planet', apparentMagnitude: undefined } : undefined) : { ...(template.catalogRef ?? {}), apparentMagnitude: t.mag, objectType: 'star' },
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

  // C26 (UIUX-webuxpieces-READY.md item 2, decided by the lead 12:59): without a lens the readout's second line says
  // the field was not read from the photo, with the code's own start value
  it('says the file gives no lens, and the field it starts at, after where the start came from', () => {
    const brightest = startPose(withTargets(sky))!;
    expect(alignStartWords(brightest.from)).toBe(`Started from the brightest star, Fomalhaut. The file gives no lens, so the field starts at ${ALIGN_FALLBACK_FOV_DEG}° across.`);
    const heading = startPose(withTargets(sky), 115.89)!;
    expect(alignStartWords(heading.from)).toBe(`Started from your photo's compass heading. The file gives no lens, so the field starts at ${ALIGN_FALLBACK_FOV_DEG}° across.`);
  });

  it('keeps the start field in the words while the person scales the view, and says nothing of a lens the file gives', () => {
    const start = startPose(withTargets(sky))!;
    expect(alignReadout(scalePose(start.pose, 0.5))).toContain(`${ALIGN_FALLBACK_FOV_DEG / 2}° across`);
    expect(alignStartWords(start.from)).toContain(`starts at ${ALIGN_FALLBACK_FOV_DEG}° across.`);
    expect(alignStartWords(startPose(withTargets(sky, 26))!.from)).not.toContain('lens');
  });

  it('reads a photo turned a quarter as tall as it is shown', () => {
    const session = withTargets(sky, 26);
    const turned = { ...session, sourceAsset: { ...session.sourceAsset!, width: 4032, height: 3024, displayOrientation: { quarterTurnsClockwise: 1, mirrored: false } } } as ObservationSession;
    expect(startPose(turned, 100)!.pose.aspect).toBeCloseTo(4 / 3, 9);
  });
});

describe("the person's drag, turn and scale", () => {
  it('moves the stars with the pointer: a drag right turns the view left, a drag down raises it', () => {
    // The star under the centre goes with the pointer, 40 px right and 30 px down of a 675 by 900 frame
    const sky = skyAt(0.5, 0.5, START);
    const pose = grabPose(START, sky, { u: 0.5 + 40 / 675, v: 0.5 + 30 / 900 });
    const after = projectAltAz(sky.altDeg, sky.azDeg, pose)!;
    expect((after.u - 0.5) * 675).toBeCloseTo(40, 3);
    expect((after.v - 0.5) * 900).toBeCloseTo(30, 3);
    expect(pose.azDeg).toBeLessThan(START.azDeg);
    expect(pose.altDeg).toBeGreaterThan(START.altDeg);
  });

  it('keeps the drag in the turned axes once turned', () => {
    const turned = { ...START, rollDeg: 90 };
    const sky = skyAt(0.5, 0.5, turned);
    const after = projectAltAz(sky.altDeg, sky.azDeg, grabPose(turned, sky, { u: 0.5 + 40 / 675, v: 0.5 }))!;
    expect((after.u - 0.5) * 675).toBeCloseTo(40, 3);
    expect(Math.abs(after.v - 0.5) * 900).toBeLessThan(0.001);
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
          const turned = { ...START, rollDeg: roll };
          const pose = grabPose(turned, skyAt(0.5, 0.5, turned), { u: 0.5 + dx / 675, v: 0.5 + dy / 900 });
          return pose.azDeg >= 0 && pose.azDeg < 360 && pose.altDeg >= -89 && pose.altDeg <= 89;
        }),
        { seed, numRuns: 50 }
      );
    }
  });
});

// MOTION-READY-web.md section 6 A (k12: Stellarium Web and Aladin Lite grab the sky 1:1 and stop dead): the star under
// the pointer stays under it, wherever in the frame it was taken and however high the photo points
describe('the sky held by the hand (MOTION-READY-web.md section 6 A)', () => {
  const W = 675;
  /** A sky point's place in frame pixels through a pose. */
  const placeOf = (sky: { altDeg: number; azDeg: number }, pose: AlignPose) => {
    const at = projectAltAz(sky.altDeg, sky.azDeg, pose)!;
    return { x: at.u * W, y: at.v * W * pose.aspect };
  };

  it('finds the sky under any place in the frame, the projection undone', () => {
    for (const pose of [START, { ...START, altDeg: 80, rollDeg: -35, fovXDeg: 100 }]) {
      for (const [u, v] of [[0.5, 0.5], [0.05, 0.1], [0.9, 0.8]] as const) {
        const sky = skyAt(u, v, pose);
        const at = projectAltAz(sky.altDeg, sky.azDeg, pose)!;
        expect(at.u).toBeCloseTo(u, 9);
        expect(at.v).toBeCloseTo(v, 9);
      }
    }
  });

  it('keeps a star taken off the centre under the pointer through a drag, low and high in the sky', () => {
    // Live on rp-b-06 (21 Nov 12:40): a ring taken 425 px off the centre ended 541 px off the pointer after 180 px
    for (const altDeg of [20, START.altDeg, 70]) {
      const dy = 100;
      for (const [u, v] of [[0.1, 0.1], [0.8, 0.7], [0.5, 0.5]] as const) {
        let pose: AlignPose = { ...START, altDeg };
        const sky = skyAt(u, v, pose);
        const start = placeOf(sky, pose);
        // 15 pointer moves, 150 px across and 100 down (or up) in all, each from where the last left the pointer
        for (let k = 1; k <= 15; k += 1) {
          const to = { u: (start.x + (150 * k) / 15) / W, v: (start.y + (dy * k) / 15) / (W * pose.aspect) };
          const from = { u: (start.x + (150 * (k - 1)) / 15) / W, v: (start.y + (dy * (k - 1)) / 15) / (W * pose.aspect) };
          pose = grabPose(pose, skyAt(from.u, from.v, pose), to);
        }
        const end = placeOf(sky, pose);
        expect(Math.hypot(end.x - (start.x + 150), end.y - (start.y + dy))).toBeLessThan(0.05);
      }
    }
  });

  it('carries the star along the hand\'s path in one long move, as when a slow frame joins many pointer moves', () => {
    // From the top of an 82 degree frame to its foot in one move: solved in one go, the pose lands 33 px off the
    // pointer (found by a sweep of 40000 moves, 61 such); walked along the path in steps, on it
    const pose: AlignPose = { azDeg: 100.64, altDeg: -6.88, rollDeg: -14.24, fovXDeg: 82.13, aspect: 4 / 3 };
    const sky = skyAt(0.566, 0.034, pose);
    const at = placeOf(sky, grabPose(pose, sky, { u: 0.695, v: 0.991 }));
    expect(Math.hypot(at.x - 0.695 * W, at.y - 0.991 * W * pose.aspect)).toBeLessThan(0.05);
  });

  /** One drag as the aligning layer makes it: the sky taken at the first place held, each move from the live pose. */
  const dragThrough = (pose: AlignPose, sky: { altDeg: number; azDeg: number }, places: ReadonlyArray<{ u: number; v: number }>) => {
    const heights: number[] = [];
    for (const to of places) {
      pose = grabPose(pose, sky, to);
      heights.push(pose.altDeg);
    }
    return { pose, heights };
  };
  const along = (from: { u: number; v: number }, to: { u: number; v: number }, moves: number) =>
    Array.from({ length: moves }, (_, k) => ({ u: from.u + ((to.u - from.u) * (k + 1)) / moves, v: from.v + ((to.v - from.v) * (k + 1)) / moves }));

  it('follows a drag down the sky from a pose at the height clamp, 89 degrees', () => {
    // AUDIT-C, 21 Nov 20:29: at 89 the height's difference was taken past the clamp, so it read as no change and the
    // pose never moved again
    const high: AlignPose = { ...START, altDeg: 89 };
    const sky = skyAt(0.5, 0.8, high);
    const { pose } = dragThrough(high, sky, along({ u: 0.5, v: 0.8 }, { u: 0.5, v: 0.5 }, 15));
    const at = placeOf(sky, pose);
    expect(Math.hypot(at.x - 0.5 * W, at.y - 0.5 * W * pose.aspect)).toBeLessThan(0.05);
    expect(pose.altDeg).toBeLessThan(89);
  });

  it('keeps a star taken beyond the zenith on its own side: a small drag never turns the view half round', () => {
    // A 100 degree frame at 70 degrees reaches past the zenith; a star at its top lies beyond it. Two poses put a star
    // at a place there, one facing each way; the hand carries the view on, the one nearer the pose it is at
    const wide: AlignPose = { azDeg: 0, altDeg: 70, rollDeg: 0, fovXDeg: 100, aspect: 4 / 3 };
    const sky = skyAt(0.5, 0.05, wide);
    expect(sky.azDeg).toBeCloseTo(180, 6);
    // Moves of 0.4 percent, as a pointer gives them, each one step of the path: the view checked after every one
    let pose = wide;
    for (const to of along({ u: 0.5, v: 0.05 }, { u: 0.52, v: 0.07 }, 5)) {
      pose = grabPose(pose, sky, to);
      const at = placeOf(sky, pose);
      expect(Math.hypot(at.x - to.u * W, at.y - to.v * W * pose.aspect)).toBeLessThan(0.05);
      expect(Math.min(pose.azDeg, 360 - pose.azDeg)).toBeLessThan(5);
      expect(Math.abs(pose.altDeg - 70)).toBeLessThan(5);
    }
  });

  it('at the height clamp, still turns the heading to bring a star no pose can reach as near the pointer as it goes', () => {
    // From 89 degrees a star in the frame's lower right corner dragged to its lower left: no pose with the turn held
    // puts it there; the nearest keeps the height at the clamp and turns the heading until no turn brings it nearer
    const high: AlignPose = { azDeg: 0, altDeg: 89, rollDeg: 0, fovXDeg: 30, aspect: 4 / 3 };
    const sky = skyAt(0.9, 0.9, high);
    const to = { u: 0.05, v: 0.95 };
    const missAt = (pose: AlignPose) => {
      const at = placeOf(sky, pose);
      return Math.hypot(at.x - to.u * W, at.y - to.v * W * pose.aspect);
    };
    const pose = grabPose(high, sky, to);
    expect(pose.altDeg).toBe(89);
    for (const turn of [-2, -0.5, 0.5, 2]) expect(missAt({ ...pose, azDeg: pose.azDeg + turn })).toBeGreaterThan(missAt(pose) - 1);
  });

  it('brings the star back under the pointer once a pose can put it there, after the drag has met the clamp', () => {
    // AUDIT-C's probe: from 75 degrees the centre star dragged to the foot in 2 percent moves meets 89 at v 0.72;
    // dragged back to the centre in the same drag it ended 19 percent of the height off the pointer
    const pose: AlignPose = { ...START, altDeg: 75 };
    const sky = skyAt(0.5, 0.5, pose);
    const down = dragThrough(pose, sky, along({ u: 0.5, v: 0.5 }, { u: 0.5, v: 1 }, 25));
    expect(Math.max(...down.heights)).toBe(89);
    const back = dragThrough(down.pose, sky, along({ u: 0.5, v: 1 }, { u: 0.5, v: 0.5 }, 25));
    const at = placeOf(sky, back.pose);
    expect(Math.hypot(at.x - 0.5 * W, at.y - 0.5 * W * back.pose.aspect)).toBeLessThan(0.05);
    expect(back.pose.altDeg).toBeCloseTo(75, 6);
  });

  it('moves only the heading and height: the turn and the field stay', () => {
    const turned = { ...START, rollDeg: 40, fovXDeg: 30 };
    const after = grabPose(turned, skyAt(0.3, 0.6, turned), { u: 0.45, v: 0.5 });
    expect(after.rollDeg).toBe(40);
    expect(after.fovXDeg).toBe(30);
    expect(after.aspect).toBe(turned.aspect);
  });

  /**
   * Whether a pose with this turn and field puts a star this high at this place: with the turn held, the camera's
   * height `a` must solve sin(star height) = (y cos a + sin a) / |(x, y, 1)| for the place's direction (x right, y up,
   * 1 ahead) in the unturned camera, within the poles (the zenith always lies on the frame's middle line, so a star
   * near it cannot go far to the side; Stellarium Web's held horizon is alike).
   */
  const heights = (altDeg: number, u: number, v: number, pose: AlignPose): [number, number] | null => {
    const half = Math.tan((pose.fovXDeg / 2) * (Math.PI / 180));
    const xr = (u - 0.5) * 2 * half;
    const yr = (0.5 - v) * 2 * half * pose.aspect;
    const c = Math.cos(pose.rollDeg * (Math.PI / 180));
    const sn = Math.sin(pose.rollDeg * (Math.PI / 180));
    const x = xr * c - yr * sn;
    const y = xr * sn + yr * c;
    const k = (Math.hypot(x, y, 1) * Math.sin(altDeg * (Math.PI / 180))) / Math.hypot(y, 1);
    if (Math.abs(k) > 0.999) return null;
    const phi = Math.atan2(y, 1);
    // The two heights that solve it, in degrees, each wrapped to within a half turn
    const wrap = (a: number) => ((((a * 180) / Math.PI + 540) % 360) - 180);
    return [wrap(Math.asin(k) - phi), wrap(Math.PI - Math.asin(k) - phi)];
  };
  /** Whether the camera height the hand carries from the start stays solvable and within the poles along the path. */
  const reachable = (altDeg: number, path: ReadonlyArray<readonly [number, number]>, pose: AlignPose) => {
    const first = heights(altDeg, path[0]![0], path[0]![1], pose);
    if (!first) return false;
    const branch = Math.abs(first[0] - pose.altDeg) < Math.abs(first[1] - pose.altDeg) ? 0 : 1;
    if (Math.abs(first[branch] - pose.altDeg) > 1e-6) return false;
    return path.every(([pu, pv]) => {
      const h = heights(altDeg, pu, pv, pose);
      return h !== null && Math.abs(h[branch]) < 88;
    });
  };

  it('holds the star under the pointer wherever a pose with the held turn can, along the whole move (property, 20 seeds)', () => {
    let held = 0;
    for (let seed = 1; seed <= 20; seed += 1) {
      fc.assert(
        fc.property(
          fc.double({ min: 0, max: 359.9, noNaN: true }),
          fc.double({ min: -60, max: 75, noNaN: true }),
          fc.double({ min: -180, max: 180, noNaN: true }),
          fc.double({ min: 10, max: 110, noNaN: true }),
          fc.double({ min: 0.05, max: 0.95, noNaN: true }),
          fc.double({ min: 0.05, max: 0.95, noNaN: true }),
          fc.double({ min: 0.05, max: 0.95, noNaN: true }),
          fc.double({ min: 0.05, max: 0.95, noNaN: true }),
          (azDeg, altDeg, rollDeg, fovXDeg, u, v, u2, v2) => {
            const pose: AlignPose = { azDeg, altDeg, rollDeg, fovXDeg, aspect: 4 / 3 };
            const sky = skyAt(u, v, pose);
            const after = grabPose(pose, sky, { u: u2, v: v2 });
            const inRange = after.azDeg >= 0 && after.azDeg < 360 && after.altDeg >= -89 && after.altDeg <= 89 && after.rollDeg === rollDeg && after.fovXDeg === fovXDeg;
            const path = Array.from({ length: 21 }, (_, i) => [u + ((u2 - u) * i) / 20, v + ((v2 - v) * i) / 20] as const);
            if (!reachable(sky.altDeg, path, pose)) return inRange;
            held += 1;
            const at = placeOf(sky, after);
            return inRange && Math.hypot(at.x - u2 * W, at.y - v2 * W * pose.aspect) < 0.05;
          }
        ),
        { seed, numRuns: 50 }
      );
    }
    // Most moves are ones a pose can follow
    expect(held).toBeGreaterThan(500);
  });

  it('leaves the height clamp whenever the drag comes back to where a pose can follow it (property, 20 seeds)', () => {
    let clamped = 0;
    for (let seed = 1; seed <= 20; seed += 1) {
      fc.assert(
        fc.property(
          fc.double({ min: 0, max: 359.9, noNaN: true }),
          fc.double({ min: 55, max: 89, noNaN: true }),
          fc.double({ min: -30, max: 30, noNaN: true }),
          fc.double({ min: 20, max: 110, noNaN: true }),
          fc.double({ min: 0.1, max: 0.9, noNaN: true }),
          fc.double({ min: 0.1, max: 0.6, noNaN: true }),
          (azDeg, altDeg, rollDeg, fovXDeg, u, v) => {
            // A star taken high in the frame, dragged to its foot (the view rises into the clamp), and back
            const pose: AlignPose = { azDeg, altDeg, rollDeg, fovXDeg, aspect: 4 / 3 };
            const sky = skyAt(u, v, pose);
            const down = dragThrough(pose, sky, along({ u, v }, { u, v: 0.98 }, 50));
            if (down.heights.includes(89)) clamped += 1;
            const back = dragThrough(down.pose, sky, along({ u, v: 0.98 }, { u, v }, 50));
            const at = placeOf(sky, back.pose);
            return Math.hypot(at.x - u * W, at.y - v * W * pose.aspect) < 0.05;
          }
        ),
        // Found by this property and a sweep of 1000: a star high in a frame's corner, left 217 and 269 px off the
        // pointer by Newton's method alone, from where the nearest pose had been left on the way down
        {
          seed,
          numRuns: 25,
          examples: [
            [0, 55, 0, 60.50988173213951, 0.8999508849316039, 0.10000000000000023],
            [0, 55, 0, 88.75819962188253, 0.9, 0.12520034225857085],
          ],
        }
      );
    }
    // Most of these drags meet the clamp on the way down
    expect(clamped).toBeGreaterThan(250);
  });

  it('turns and scales about the point between two fingers: that star stays between them, 1:1 in angle and spread', () => {
    const H = W * START.aspect;
    // Two fingers 120 px apart about a place up and left of the centre, spread to 180 px and turned 20 degrees
    const mid = { x: W * 0.33, y: H * 0.33 };
    const sky = skyAt(mid.x / W, mid.y / H, START);
    const at = (gap: number, deg: number) => {
      const c = Math.cos((deg * Math.PI) / 180);
      const s = Math.sin((deg * Math.PI) / 180);
      return [{ x: mid.x - (gap / 2) * c, y: mid.y - (gap / 2) * s }, { x: mid.x + (gap / 2) * c, y: mid.y + (gap / 2) * s }] as const;
    };
    let pose = START;
    for (let k = 1; k <= 10; k += 1) pose = pinchPose(pose, at(120 + 6 * (k - 1), 2 * (k - 1)), at(120 + 6 * k, 2 * k), W, H);
    const end = placeOf(sky, pose);
    expect(Math.hypot(end.x - mid.x, end.y - mid.y)).toBeLessThan(0.05);
    expect(pose.rollDeg).toBeCloseTo(20, 6);
    // 1:1 in spread: the field's half width in tangent measure shrinks by 120 over 180 (log-tan-half-FOV, as the rig)
    expect(Math.tan((pose.fovXDeg / 2) * (Math.PI / 180)) / Math.tan((START.fovXDeg / 2) * (Math.PI / 180))).toBeCloseTo(120 / 180, 9);
  });
});
