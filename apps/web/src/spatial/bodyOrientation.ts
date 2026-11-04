/**
 * Where the Earth and the Moon face at a moment, as rotations from ICRF to each body's fixed frame:
 * x toward longitude 0 on the equator, y toward longitude 90 deg east, z toward the north pole.
 *
 * - The Moon: the IAU WGCCRE model (Archinal et al. 2018, Celest. Mech. Dyn. Astron. 130:22, the
 *   2015 report, which keeps the 2009 lunar model): pole and prime meridian with the libration
 *   terms E1 to E13, in TDB. It approximates the mean Earth/polar axis frame the NASA SVS and LOLA
 *   maps are made in.
 * - The Earth: Greenwich mean sidereal time (IAU 1982; Aoki et al. 1982) after the IAU 1976
 *   precession (Lieske et al. 1977), with UT1 taken as UTC (they differ by under 0.9 s, under
 *   0.004 deg of turn). Nutation (under 0.005 deg) and polar motion are left out.
 *
 * Checked against JPL Horizons at the landing moment (__tests__/bodyOrientation.test.ts): the
 * sub-observer and sub-solar points of both bodies and the Moon's pole agree within 0.01 deg.
 */

/** Row-major 3x3 matrix. Row k of an ICRF-to-body matrix is body axis k in ICRF. */
export type Mat3 = [number, number, number, number, number, number, number, number, number];
type Vec3 = readonly [number, number, number];

const DEG = Math.PI / 180;
const ARCSEC = DEG / 3600;
const J2000 = 2451545.0;
const SECONDS_PER_DAY = 86400;

/** Julian date of a UTC time such as '2025-03-07T11:00:00Z'. */
export function julianDateUtc(isoUtc: string): number {
  const ms = Date.parse(isoUtc);
  if (Number.isNaN(ms)) throw new Error(`not a time: ${isoUtc}`);
  return ms / 86_400_000 + 2440587.5;
}

/** Passive rotation about x, y or z: the frame turns by `angle` (radians). */
function rotation(axis: 0 | 1 | 2, angle: number): Mat3 {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  if (axis === 0) return [1, 0, 0, 0, c, s, 0, -s, c];
  if (axis === 1) return [c, 0, -s, 0, 1, 0, s, 0, c];
  return [c, s, 0, -s, c, 0, 0, 0, 1];
}

function multiply(a: Mat3, b: Mat3): Mat3 {
  const out = new Array<number>(9);
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      out[i * 3 + j] = a[i * 3]! * b[j]! + a[i * 3 + 1]! * b[3 + j]! + a[i * 3 + 2]! * b[6 + j]!;
    }
  }
  return out as Mat3;
}

/** The IAU 2015 lunar arguments E1 to E13, in degrees, d days of TDB after J2000. */
function lunarArguments(d: number): number[] {
  return [
    125.045 - 0.0529921 * d,
    250.089 - 0.1059842 * d,
    260.008 + 13.0120009 * d,
    176.625 + 13.3407154 * d,
    357.529 + 0.9856003 * d,
    311.589 + 26.4057084 * d,
    134.963 + 13.064993 * d,
    276.617 + 0.3287146 * d,
    34.226 + 1.7484877 * d,
    15.134 - 0.1589763 * d,
    119.743 + 0.0036096 * d,
    239.961 + 0.1643573 * d,
    25.053 + 12.9590088 * d,
  ];
}

interface PoleAndMeridian {
  raDeg: number;
  decDeg: number;
  primeMeridianDeg: number;
}

function moonRotationElements(jdTdb: number): PoleAndMeridian {
  const d = jdTdb - J2000;
  const T = d / 36525;
  const E = lunarArguments(d);
  const sin = (k: number) => Math.sin(E[k - 1]! * DEG);
  const cos = (k: number) => Math.cos(E[k - 1]! * DEG);
  const raDeg =
    269.9949 + 0.0031 * T - 3.8787 * sin(1) - 0.1204 * sin(2) + 0.07 * sin(3) - 0.0172 * sin(4) + 0.0072 * sin(6) -
    0.0052 * sin(10) + 0.0043 * sin(13);
  const decDeg =
    66.5392 + 0.013 * T + 1.5419 * cos(1) + 0.0239 * cos(2) - 0.0278 * cos(3) + 0.0068 * cos(4) - 0.0029 * cos(6) +
    0.0009 * cos(7) + 0.0008 * cos(10) - 0.0009 * cos(13);
  const primeMeridianDeg =
    38.3213 + 13.17635815 * d - 1.4e-12 * d * d + 3.561 * sin(1) + 0.1208 * sin(2) - 0.0642 * sin(3) + 0.0158 * sin(4) +
    0.0252 * sin(5) - 0.0066 * sin(6) - 0.0047 * sin(7) - 0.0046 * sin(8) + 0.0028 * sin(9) + 0.0052 * sin(10) +
    0.004 * sin(11) + 0.0019 * sin(12) - 0.0044 * sin(13);
  return { raDeg, decDeg, primeMeridianDeg };
}

/** The Moon's north pole in ICRF (degrees) at a Julian date in TDB. */
export function moonPole(jdTdb: number): { raDeg: number; decDeg: number } {
  const { raDeg, decDeg } = moonRotationElements(jdTdb);
  return { raDeg: ((raDeg % 360) + 360) % 360, decDeg };
}

/** ICRF to the Moon's body frame at a Julian date in TDB. */
export function moonIcrfToBody(jdTdb: number): Mat3 {
  const { raDeg, decDeg, primeMeridianDeg } = moonRotationElements(jdTdb);
  return multiply(
    rotation(2, primeMeridianDeg * DEG),
    multiply(rotation(0, (90 - decDeg) * DEG), rotation(2, (90 + raDeg) * DEG))
  );
}

/** ICRF to the Earth's body frame at a Julian date in UTC; TDB - UTC sets the precession epoch. */
export function earthIcrfToBody(jdUtc: number, tdbMinusUtcSeconds: number): Mat3 {
  const T = (jdUtc + tdbMinusUtcSeconds / SECONDS_PER_DAY - J2000) / 36525;
  const zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T * T * T) * ARCSEC;
  const z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T * T * T) * ARCSEC;
  const theta = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T * T * T) * ARCSEC;
  const precession = multiply(rotation(2, -z), multiply(rotation(1, theta), rotation(2, -zeta)));
  const du = jdUtc - J2000;
  const Tu = du / 36525;
  const gmstDeg = 280.46061837 + 360.98564736629 * du + 0.000387933 * Tu * Tu - (Tu * Tu * Tu) / 38710000;
  return multiply(rotation(2, (gmstDeg % 360) * DEG), precession);
}

/** East longitude in [0, 360) and planetocentric latitude (degrees) of an ICRF direction on a body. */
export function subPoint(icrfToBody: Mat3, directionIcrf: Vec3): { lonDeg: number; latDeg: number } {
  const m = icrfToBody;
  const [x, y, z] = directionIcrf;
  const bx = m[0] * x + m[1] * y + m[2] * z;
  const by = m[3] * x + m[4] * y + m[5] * z;
  const bz = m[6] * x + m[7] * y + m[8] * z;
  const lonDeg = (((Math.atan2(by, bx) / DEG) % 360) + 360) % 360;
  // atan2 keeps full precision near the poles, where asin(z / |v|) loses half the digits
  return { lonDeg, latDeg: Math.atan2(bz, Math.hypot(bx, by)) / DEG };
}
