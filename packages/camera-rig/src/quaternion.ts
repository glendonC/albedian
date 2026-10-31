import { QuaternionTuple } from '@albedian/contracts';

export type Quaternion = QuaternionTuple;
export type Vec3 = [number, number, number];

export const DEG_TO_RAD = Math.PI / 180;
export const RAD_TO_DEG = 180 / Math.PI;

/**
 * Converts ICRS Right Ascension and Declination (degrees) into a 3D unit vector.
 */
export function raDecToUnitVector(raDeg: number, decDeg: number): Vec3 {
  const raRad = raDeg * DEG_TO_RAD;
  const decRad = decDeg * DEG_TO_RAD;
  const cosDec = Math.cos(decRad);
  return [
    cosDec * Math.cos(raRad),
    cosDec * Math.sin(raRad),
    Math.sin(decRad),
  ];
}

/**
 * Converts a 3D Cartesian vector in ICRS coordinates into Right Ascension [0, 360)
 * and Declination [-90, 90] in degrees.
 */
export function unitVectorToRaDec(vec: Vec3): {
  raDeg: number;
  decDeg: number;
} {
  const [x, y, z] = vec;
  const norm = Math.hypot(x, y, z);
  if (norm === 0) {
    return { raDeg: 0, decDeg: 0 };
  }
  const nx = x / norm;
  const ny = y / norm;
  const nz = Math.max(-1, Math.min(1, z / norm));
  const xyNorm = Math.hypot(nx, ny);
  const decDeg = Math.atan2(nz, xyNorm) * RAD_TO_DEG;
  let raDeg = 0;
  if (xyNorm > 1e-15) {
    raDeg = Math.atan2(ny, nx) * RAD_TO_DEG;
    raDeg = ((raDeg % 360) + 360) % 360;
    if (raDeg >= 360) {
      raDeg = 0;
    }
  }
  return { raDeg, decDeg };
}

/**
 * Computes great-circle angular separation (in degrees) between two ICRS coordinates.
 */
export function angularSeparationDeg(
  ra1Deg: number,
  dec1Deg: number,
  ra2Deg: number,
  dec2Deg: number
): number {
  const u1 = raDecToUnitVector(ra1Deg, dec1Deg);
  const u2 = raDecToUnitVector(ra2Deg, dec2Deg);
  const dot = u1[0] * u2[0] + u1[1] * u2[1] + u1[2] * u2[2];
  const cx = u1[1] * u2[2] - u1[2] * u2[1];
  const cy = u1[2] * u2[0] - u1[0] * u2[2];
  const cz = u1[0] * u2[1] - u1[1] * u2[0];
  const crossNorm = Math.hypot(cx, cy, cz);
  return Math.atan2(crossNorm, dot) * RAD_TO_DEG;
}

export function getTangentBasis(
  crvalRaDeg: number,
  crvalDecDeg: number
): {
  e0: Vec3;
  eEast: Vec3;
  eNorth: Vec3;
} {
  const alpha0 = crvalRaDeg * DEG_TO_RAD;
  const delta0 = crvalDecDeg * DEG_TO_RAD;
  const cosA = Math.cos(alpha0);
  const sinA = Math.sin(alpha0);
  const cosD = Math.cos(delta0);
  const sinD = Math.sin(delta0);

  return {
    e0: [cosD * cosA, cosD * sinA, sinD],
    eEast: [-sinA, cosA, 0],
    eNorth: [-sinD * cosA, -sinD * sinA, cosD],
  };
}

export function normalizeQuaternion(q: Quaternion): Quaternion {
  const [x, y, z, w] = q;
  const norm = Math.hypot(x, y, z, w);
  if (norm === 0) {
    return [0, 0, 0, 1];
  }
  return [x / norm, y / norm, z / norm, w / norm];
}

export function multiplyQuaternions(a: Quaternion, b: Quaternion): Quaternion {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return normalizeQuaternion([
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ]);
}

export function rotateVectorByQuaternion(vec: Vec3, q: Quaternion): Vec3 {
  const [qx, qy, qz, qw] = normalizeQuaternion(q);
  const [vx, vy, vz] = vec;
  const tx = 2 * (qy * vz - qz * vy);
  const ty = 2 * (qz * vx - qx * vz);
  const tz = 2 * (qx * vy - qy * vx);
  return [
    vx + qw * tx + (qy * tz - qz * ty),
    vy + qw * ty + (qz * tx - qx * tz),
    vz + qw * tz + (qx * ty - qy * tx),
  ];
}

/**
 * Unit quaternion Spherical Linear Interpolation (SLERP) with shortest-arc
 * hemisphere flip (`dot < 0`) and small-angle linear fallback.
 */
export function slerpQuaternion(
  q0: Quaternion,
  q1: Quaternion,
  t: number
): Quaternion {
  const [ax, ay, az, aw] = normalizeQuaternion(q0);
  let [bx, by, bz, bw] = normalizeQuaternion(q1);
  const u = Math.max(0, Math.min(1, t));

  let dot = ax * bx + ay * by + az * bz + aw * bw;
  if (dot < 0) {
    dot = -dot;
    bx = -bx;
    by = -by;
    bz = -bz;
    bw = -bw;
  }

  if (dot > 0.9995) {
    return normalizeQuaternion([
      ax + u * (bx - ax),
      ay + u * (by - ay),
      az + u * (bz - az),
      aw + u * (bw - aw),
    ]);
  }

  const theta0 = Math.acos(Math.min(1, dot));
  const sinTheta0 = Math.sin(theta0);
  const theta = theta0 * u;
  const s0 = Math.sin(theta0 - theta) / sinTheta0;
  const s1 = Math.sin(theta) / sinTheta0;

  return normalizeQuaternion([
    s0 * ax + s1 * bx,
    s0 * ay + s1 * by,
    s0 * az + s1 * bz,
    s0 * aw + s1 * bw,
  ]);
}

/**
 * Computes a unit camera orientation quaternion `[x, y, z, w]` whose forward `-Z`
 * camera axis points along `dirICRS`.
 */
export function lookAtDirectionQuaternion(
  dirICRS: Vec3,
  upHint: Vec3 = [0, 0, 1]
): Quaternion {
  const fNorm = Math.hypot(dirICRS[0], dirICRS[1], dirICRS[2]);
  const fx = fNorm > 0 ? dirICRS[0] / fNorm : 0;
  const fy = fNorm > 0 ? dirICRS[1] / fNorm : 0;
  const fz = fNorm > 0 ? dirICRS[2] / fNorm : -1;

  const bx = -fx;
  const by = -fy;
  const bz = -fz;

  let ux = upHint[0];
  let uy = upHint[1];
  let uz = upHint[2];
  const uNorm = Math.hypot(ux, uy, uz);
  if (uNorm > 0) {
    ux /= uNorm;
    uy /= uNorm;
    uz /= uNorm;
  } else {
    ux = 0;
    uy = 0;
    uz = 1;
  }

  if (Math.abs(fx * ux + fy * uy + fz * uz) > 0.999) {
    [ux, uy, uz] = Math.abs(fy) > 0.999 ? [1, 0, 0] : [0, 1, 0];
  }

  let rx = uy * bz - uz * by;
  let ry = uz * bx - ux * bz;
  let rz = ux * by - uy * bx;
  const rNorm = Math.hypot(rx, ry, rz);
  rx /= rNorm;
  ry /= rNorm;
  rz /= rNorm;

  const camUx = by * rz - bz * ry;
  const camUy = bz * rx - bx * rz;
  const camUz = bx * ry - by * rx;

  const [m00, m01, m02] = [rx, camUx, bx];
  const [m10, m11, m12] = [ry, camUy, by];
  const [m20, m21, m22] = [rz, camUz, bz];
  const trace = m00 + m11 + m22;
  let qx: number, qy: number, qz: number, qw: number;

  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1.0);
    qw = 0.25 / s;
    qx = (m21 - m12) * s;
    qy = (m02 - m20) * s;
    qz = (m10 - m01) * s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2.0 * Math.sqrt(1.0 + m00 - m11 - m22);
    qw = (m21 - m12) / s;
    qx = 0.25 * s;
    qy = (m01 + m10) / s;
    qz = (m02 + m20) / s;
  } else if (m11 > m22) {
    const s = 2.0 * Math.sqrt(1.0 + m11 - m00 - m22);
    qw = (m02 - m20) / s;
    qx = (m01 + m10) / s;
    qy = 0.25 * s;
    qz = (m12 + m21) / s;
  } else {
    const s = 2.0 * Math.sqrt(1.0 + m22 - m00 - m11);
    qw = (m10 - m01) / s;
    qx = (m02 + m20) / s;
    qy = (m12 + m21) / s;
    qz = 0.25 * s;
  }

  return normalizeQuaternion([qx, qy, qz, qw]);
}
