import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  earthIcrfToBody,
  julianDateUtc,
  moonIcrfToBody,
  moonPole,
  subPoint,
  type Mat3,
} from '../bodyOrientation.js';

/**
 * The rotation models against JPL Horizons at the landing moment. landingMoment.json holds the
 * geocentric Moon and Sun (ICRF, geometric) and Horizons' own sub-observer and sub-solar points
 * for the Moon seen from the Earth's centre and the Earth seen from the Moon's centre
 * (scripts/plan_landing_moment.py). The models leave out light time (1.3 s, up to 0.006 deg of
 * the Earth's turn), aberration and nutation, so they agree within 0.01 deg, not exactly.
 */
interface Seen {
  subObserverLonDeg: number;
  subObserverLatDeg: number;
  subSolarLonDeg: number;
  subSolarLatDeg: number;
  northPoleRaDeg: number;
  northPoleDecDeg: number;
  phaseAngleDeg: number;
  radiiKm?: [number, number, number];
}
interface LandingMoment {
  utc: string;
  tdbMinusUtcSeconds: number;
  moonKm: [number, number, number];
  sunKm: [number, number, number];
  checks: { moonSeenFromEarthCentre: Seen; earthSeenFromMoonCentre: Seen };
}
const moment: LandingMoment = JSON.parse(
  fs.readFileSync(fileURLToPath(new URL('../webgpu/cislunar/landingMoment.json', import.meta.url)), 'utf8')
);

const TOLERANCE_DEG = 0.01;
const DEG = Math.PI / 180;
const lonDiff = (a: number, b: number) => ((((a - b) % 360) + 540) % 360) - 180;
const minus = (a: readonly number[], b: readonly number[]) => [a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!] as const;
const neg = (a: readonly number[]) => [-a[0]!, -a[1]!, -a[2]!] as const;
/** Horizons gives the Earth's latitudes as geodetic; the models give geocentric ones. */
const geocentric = (geodeticDeg: number, [a, , c]: [number, number, number]) =>
  Math.atan(((c * c) / (a * a)) * Math.tan(geodeticDeg * DEG)) / DEG;

const jdUtc = julianDateUtc(moment.utc);
const jdTdb = jdUtc + moment.tdbMinusUtcSeconds / 86400;

describe('body orientation against JPL Horizons at the landing moment', () => {
  it('reads the moment as a Julian date', () => {
    expect(moment.utc).toBe('2025-03-07T11:00:00Z');
    expect(jdUtc).toBeCloseTo(2460741.958333333, 8);
  });

  it("puts the Moon's sub-Earth and sub-solar points where Horizons does", () => {
    const moon = moonIcrfToBody(jdTdb);
    const seen = moment.checks.moonSeenFromEarthCentre;
    const subEarth = subPoint(moon, neg(moment.moonKm));
    const subSun = subPoint(moon, minus(moment.sunKm, moment.moonKm));
    expect(Math.abs(lonDiff(subEarth.lonDeg, seen.subObserverLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subEarth.latDeg - seen.subObserverLatDeg)).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(lonDiff(subSun.lonDeg, seen.subSolarLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subSun.latDeg - seen.subSolarLatDeg)).toBeLessThan(TOLERANCE_DEG);
  });

  it("points the Moon's pole where Horizons does", () => {
    const pole = moonPole(jdTdb);
    const seen = moment.checks.moonSeenFromEarthCentre;
    expect(Math.abs(lonDiff(pole.raDeg, seen.northPoleRaDeg)) * Math.cos(seen.northPoleDecDeg * DEG)).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(pole.decDeg - seen.northPoleDecDeg)).toBeLessThan(TOLERANCE_DEG);
  });

  it("puts the Earth's sub-Moon and sub-solar points where Horizons does", () => {
    const earth = earthIcrfToBody(jdUtc, moment.tdbMinusUtcSeconds);
    const seen = moment.checks.earthSeenFromMoonCentre;
    const subMoon = subPoint(earth, moment.moonKm);
    const subSun = subPoint(earth, moment.sunKm);
    expect(Math.abs(lonDiff(subMoon.lonDeg, seen.subObserverLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subMoon.latDeg - geocentric(seen.subObserverLatDeg, seen.radiiKm!))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(lonDiff(subSun.lonDeg, seen.subSolarLonDeg))).toBeLessThan(TOLERANCE_DEG);
    expect(Math.abs(subSun.latDeg - geocentric(seen.subSolarLatDeg, seen.radiiKm!))).toBeLessThan(TOLERANCE_DEG);
  });

  it("lights the Moon from the moment's Sun: the phase angle matches Horizons", () => {
    const toEarth = neg(moment.moonKm);
    const toSun = minus(moment.sunKm, moment.moonKm);
    const dot = toEarth[0] * toSun[0] + toEarth[1] * toSun[1] + toEarth[2] * toSun[2];
    const phase = Math.acos(dot / (Math.hypot(...toEarth) * Math.hypot(...toSun))) / DEG;
    expect(Math.abs(phase - moment.checks.moonSeenFromEarthCentre.phaseAngleDeg)).toBeLessThan(0.02);
  });
});

/** M times its transpose, minus the identity: largest element. */
function orthonormalityError(m: Mat3): number {
  let worst = 0;
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      let sum = 0;
      for (let k = 0; k < 3; k++) sum += m[i * 3 + k]! * m[j * 3 + k]!;
      worst = Math.max(worst, Math.abs(sum - (i === j ? 1 : 0)));
    }
  }
  return worst;
}
const det = (m: Mat3) =>
  m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]);

describe('body orientation is a rotation at any moment', () => {
  // 1950 to 2100: the span the precession and libration series are fitted for
  const julianDate = fc.double({ min: 2433282.5, max: 2488069.5, noNaN: true });

  it('PBT-ORIENT-1: both models give proper rotations', () => {
    fc.assert(
      fc.property(julianDate, (jd) => {
        for (const m of [moonIcrfToBody(jd), earthIcrfToBody(jd, 69.184)]) {
          expect(orthonormalityError(m)).toBeLessThan(1e-12);
          expect(det(m)).toBeCloseTo(1, 12);
        }
      }),
      { numRuns: 200 }
    );
  });

  it('PBT-ORIENT-2: the sub-point of the body pole is the pole, and of the prime meridian is 0, 0', () => {
    fc.assert(
      fc.property(julianDate, (jd) => {
        const m = earthIcrfToBody(jd, 69.184);
        // Row k of an ICRF-to-body matrix is body axis k in ICRF
        expect(subPoint(m, [m[6], m[7], m[8]]).latDeg).toBeCloseTo(90, 9);
        const meridian = subPoint(m, [m[0], m[1], m[2]]);
        expect(Math.abs(lonDiff(meridian.lonDeg, 0))).toBeLessThan(1e-9);
        expect(Math.abs(meridian.latDeg)).toBeLessThan(1e-9);
      }),
      { numRuns: 100 }
    );
  });
});
