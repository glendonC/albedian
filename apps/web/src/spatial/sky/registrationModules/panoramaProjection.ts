import type { Vec3 } from '../../catalogStars/healpix.js';
import { applyRows, sceneToPanoramaRows } from './matrix3.js';
import type { PanoramaOffsetTable, PanoramaRegistration } from './types.js';

const TWO_PI = 2 * Math.PI;
const TEXEL_CENTER_OFFSET = 0.5;
const MIN_POLE_COS_LAT = 1e-3;

function positiveMod(value: number, modulus: number): number {
  return ((value % modulus) + modulus) % modulus;
}

function decodeOffsetChannel(table: PanoramaOffsetTable, x: number, y: number, channel: 0 | 1): number {
  return (table.codes[2 * (y * table.width + x) + channel]! - table.bias) / table.scale;
}

function sampleOffsetChannel(
  table: PanoramaOffsetTable,
  xa: number,
  xb: number,
  y0: number,
  tx: number,
  ty: number,
  channel: 0 | 1
): number {
  const bottom =
    decodeOffsetChannel(table, xa, y0, channel) * (1 - tx) + decodeOffsetChannel(table, xb, y0, channel) * tx;
  const top =
    decodeOffsetChannel(table, xa, y0 + 1, channel) * (1 - tx) + decodeOffsetChannel(table, xb, y0 + 1, channel) * tx;
  return bottom * (1 - ty) + top * ty;
}

/** Plate carree texture coordinates of a unit vector: longitude 0 at u = 0.5, increasing to the left; north at v = 1. */
export function plateCarreeUv(v: Vec3): [number, number] {
  const lon = Math.atan2(v[1], v[0]);
  const lat = Math.asin(Math.max(-1, Math.min(1, v[2])));
  const u = 0.5 - lon / TWO_PI;
  return [u - Math.floor(u), 0.5 + lat / Math.PI];
}

/** Bilinear lookup of (east, north) texels as a GPU filters it: u repeats, v clamps to the edge. */
export function sampleOffsetTable(table: PanoramaOffsetTable, u: number, v: number): [number, number] {
  const fx = u * table.width - TEXEL_CENTER_OFFSET;
  const fy = Math.max(0, Math.min(table.height - 1, v * table.height - TEXEL_CENTER_OFFSET));
  const x0 = Math.floor(fx);
  const y0 = Math.min(Math.floor(fy), table.height - 2);
  const tx = fx - x0;
  const ty = fy - y0;
  const xa = positiveMod(x0, table.width);
  const xb = (xa + 1) % table.width;
  return [
    sampleOffsetChannel(table, xa, xb, y0, tx, ty, 0),
    sampleOffsetChannel(table, xa, xb, y0, tx, ty, 1),
  ];
}

/**
 * Panorama texture coordinates for a scene direction: rotate into the fitted frame, project,
 * then move by the offset table looked up there (east shifts u by 1 / cos(lat) texels).
 * The returned u is not wrapped; the texture repeats in u.
 */
export function panoramaUv(sceneDir: Vec3, registration: PanoramaRegistration): [number, number] {
  const p = applyRows(sceneToPanoramaRows(registration), sceneDir);
  const [u, v] = plateCarreeUv(p);
  const [east, north] = sampleOffsetTable(registration.offsetTable, u, v);
  const cosLat = Math.max(Math.hypot(p[0], p[1]), MIN_POLE_COS_LAT);
  return [
    u - east / (registration.imageWidth * cosLat),
    v + (north - registration.yOffsetPx) / registration.imageHeight,
  ];
}
