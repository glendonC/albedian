/**
 * HEALPix nested-scheme geometry for catalog star tiles.
 *
 * Mirrors `services/science/catalog_tiles.py` (`nest_to_face_ixiy`, `face_xy_to_vec`), which
 * the bake validates against the HEALPix index Gaia encodes in every DR3 source_id.
 * Face coordinates `x`, `y` run over [0, 1] across a base face; vectors are ICRS unit vectors.
 */

export type Vec3 = [number, number, number];

const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4] as const;
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7] as const;
const HALF_PI = Math.PI / 2;

export function tileCount(order: number): number {
  return 12 * 4 ** order;
}

function compactBits(v: number): number {
  let out = 0;
  for (let b = 0; b < 16; b++) {
    out |= ((v >>> (2 * b)) & 1) << b;
  }
  return out;
}

export function nestToFaceIxIy(order: number, pix: number): { face: number; ix: number; iy: number } {
  const perFace = 4 ** order;
  const face = Math.floor(pix / perFace);
  const sub = pix - face * perFace;
  return { face, ix: compactBits(sub), iy: compactBits(sub >>> 1) };
}

/** Unit ICRS vector for continuous face coordinates; writes into `out` when given. */
export function faceXyToVec(face: number, x: number, y: number, out: Vec3 = [0, 0, 0]): Vec3 {
  const jr = (JRLL[face] ?? 0) - x - y;
  let nr: number;
  let z: number;
  let sinTheta: number;
  if (jr < 1) {
    nr = jr;
    const tmp = (nr * nr) / 3;
    z = 1 - tmp;
    sinTheta = Math.sqrt(Math.max(tmp * (2 - tmp), 0));
  } else if (jr > 3) {
    nr = 4 - jr;
    const tmp = (nr * nr) / 3;
    z = tmp - 1;
    sinTheta = Math.sqrt(Math.max(tmp * (2 - tmp), 0));
  } else {
    nr = 1;
    z = ((2 - jr) * 2) / 3;
    sinTheta = Math.sqrt(Math.max((1 - z) * (1 + z), 0));
  }
  let t = (JPLL[face] ?? 0) * nr + x - y;
  if (t < 0) t += 8;
  if (t >= 8) t -= 8;
  const phi = nr < 1e-15 ? 0 : (0.5 * HALF_PI * t) / nr;
  out[0] = sinTheta * Math.cos(phi);
  out[1] = sinTheta * Math.sin(phi);
  out[2] = z;
  return out;
}

export function tileCenter(order: number, pix: number): Vec3 {
  const { face, ix, iy } = nestToFaceIxIy(order, pix);
  const nside = 2 ** order;
  return faceXyToVec(face, (ix + 0.5) / nside, (iy + 0.5) / nside);
}

export function angleBetween(a: Vec3, b: Vec3): number {
  const cx = a[1] * b[2] - a[2] * b[1];
  const cy = a[2] * b[0] - a[0] * b[2];
  const cz = a[0] * b[1] - a[1] * b[0];
  return Math.atan2(Math.hypot(cx, cy, cz), a[0] * b[0] + a[1] * b[1] + a[2] * b[2]);
}

/**
 * Conservative angular radius of a tile around its center: the largest distance to the
 * corners and edge midpoints, widened by 5 % because pixel edges are not great circles.
 */
export function tileBoundingRadius(order: number, pix: number): number {
  const { face, ix, iy } = nestToFaceIxIy(order, pix);
  const nside = 2 ** order;
  const center = faceXyToVec(face, (ix + 0.5) / nside, (iy + 0.5) / nside);
  let radius = 0;
  const probe: Vec3 = [0, 0, 0];
  for (const [dx, dy] of [
    [0, 0],
    [1, 0],
    [0, 1],
    [1, 1],
    [0.5, 0],
    [0, 0.5],
    [1, 0.5],
    [0.5, 1],
  ] as const) {
    faceXyToVec(face, (ix + dx) / nside, (iy + dy) / nside, probe);
    radius = Math.max(radius, angleBetween(center, probe));
  }
  return radius * 1.05;
}

export function raDecDegToVec(raDeg: number, decDeg: number): Vec3 {
  const ra = (raDeg * Math.PI) / 180;
  const dec = (decDeg * Math.PI) / 180;
  return [Math.cos(dec) * Math.cos(ra), Math.cos(dec) * Math.sin(ra), Math.sin(dec)];
}

export function vecToRaDecDeg(v: Vec3): { raDeg: number; decDeg: number } {
  const ra = (Math.atan2(v[1], v[0]) * 180) / Math.PI;
  return {
    raDeg: (ra + 360) % 360,
    decDeg: (Math.asin(Math.max(-1, Math.min(1, v[2]))) * 180) / Math.PI,
  };
}
