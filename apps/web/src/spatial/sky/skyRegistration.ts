import * as THREE from 'three';
import { ICRS_TO_SCENE_QUATERNION } from '../catalogStars/CatalogStarField.js';
import type { Vec3 } from '../catalogStars/healpix.js';

/**
 * Where the sky dome's image points, so both renderers can sample it by view direction and draw
 * it under the catalog stars (which sit at ICRS directions through `ICRS_TO_SCENE_QUATERNION`).
 * The shaders in `SparkPortalRenderer.ts` (`skyMat`) and `webgpu/cislunar/skyBackground.ts`
 * implement `panoramaUv`; this CPU version is what the tests check against the measured star
 * images.
 *
 * The ESO Milky Way panorama (eso0932a) is plate carree in its own frame, about 3.8 degrees from
 * galactic, plus a vertical offset and a table of stitching offsets, all fitted to catalog stars
 * by `scripts/register_sky_panorama.py` into `eso0932a_registration.json`.
 *
 * Texture coordinates follow three: u to the right, v = 1 at the top row of the image.
 */

export const SKY_PANORAMA_REGISTRATION_URL = '/textures/eso0932a_registration.json';
export const SKY_PANORAMA_REGISTRATION_FORMAT = 'albedian.sky-panorama-registration';

/** Row-major 3x3 matrix. */
export type Mat3Rows = readonly [number, number, number, number, number, number, number, number, number];

/**
 * ICRS to galactic: A_G' of the Hipparcos catalogue (ESA 1997, vol. 1, sect. 1.5.3), as given in
 * the Gaia DR3 documentation (sect. 4.1.7). Rows are the galactic centre, l = 90 deg and north
 * galactic pole directions in ICRS. `services/science/sky_registration.py` holds the same values.
 */
export const ICRS_TO_GALACTIC: Mat3Rows = [
  -0.0548755604162154, -0.873437090234885, -0.4838350155487132, 0.4941094278755837, -0.4448296299600112,
  0.7469822444972189, -0.8676661490190047, -0.1980763734312015, 0.4559837761750669,
];

export interface PanoramaOffsetTable {
  width: number;
  height: number;
  /** Codes per texel of offset; value = (code - bias) / scale. */
  scale: number;
  bias: number;
  /** Interleaved (east, north) codes per texel, row 0 at v = 0 (latitude -90). */
  codes: Uint8Array;
}

export interface PanoramaRegistration {
  imageWidth: number;
  imageHeight: number;
  galacticToPanorama: Mat3Rows;
  /** Vertical offset of the image in pixels (y down), after the rotation. */
  yOffsetPx: number;
  offsetTable: PanoramaOffsetTable;
}

export function multiplyRows(a: Mat3Rows, b: Mat3Rows): Mat3Rows {
  const out: number[] = [];
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) out.push(a[3 * r]! * b[c]! + a[3 * r + 1]! * b[3 + c]! + a[3 * r + 2]! * b[6 + c]!);
  }
  return out as unknown as Mat3Rows;
}

export function transposeRows(m: Mat3Rows): Mat3Rows {
  return [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]];
}

export function applyRows(m: Mat3Rows, v: Vec3): Vec3 {
  return [
    m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
    m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
  ];
}

/** Scene axes to ICRS: the inverse of the rotation the catalog star mounts carry. */
export function sceneToIcrsRows(): Mat3Rows {
  const e = new THREE.Matrix4().makeRotationFromQuaternion(ICRS_TO_SCENE_QUATERNION).elements;
  // Matrix4 elements are column-major; the transpose of the rotation is its inverse
  return [e[0]!, e[1]!, e[2]!, e[4]!, e[5]!, e[6]!, e[8]!, e[9]!, e[10]!];
}

/** Scene axes to the panorama's fitted frame. */
export function sceneToPanoramaRows(registration: PanoramaRegistration): Mat3Rows {
  return multiplyRows(registration.galacticToPanorama, multiplyRows(ICRS_TO_GALACTIC, sceneToIcrsRows()));
}

/** Plate carree texture coordinates of a unit vector: longitude 0 at u = 0.5, increasing to the left; north at v = 1. */
export function plateCarreeUv(v: Vec3): [number, number] {
  const lon = Math.atan2(v[1], v[0]);
  const lat = Math.asin(Math.max(-1, Math.min(1, v[2])));
  const u = 0.5 - lon / (2 * Math.PI);
  return [u - Math.floor(u), 0.5 + lat / Math.PI];
}

/** Bilinear lookup of (east, north) texels as a GPU filters it: u repeats, v clamps to the edge. */
export function sampleOffsetTable(table: PanoramaOffsetTable, u: number, v: number): [number, number] {
  const fx = u * table.width - 0.5;
  const fy = Math.max(0, Math.min(table.height - 1, v * table.height - 0.5));
  const x0 = Math.floor(fx);
  const y0 = Math.min(Math.floor(fy), table.height - 2);
  const tx = fx - x0;
  const ty = fy - y0;
  const xa = ((x0 % table.width) + table.width) % table.width;
  const xb = (xa + 1) % table.width;
  const at = (x: number, y: number, k: number) => (table.codes[2 * (y * table.width + x) + k]! - table.bias) / table.scale;
  const out: [number, number] = [0, 0];
  for (let k = 0; k < 2; k++) {
    const bottom = at(xa, y0, k) * (1 - tx) + at(xb, y0, k) * tx;
    const top = at(xa, y0 + 1, k) * (1 - tx) + at(xb, y0 + 1, k) * tx;
    out[k] = bottom * (1 - ty) + top * ty;
  }
  return out;
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
  const cosLat = Math.max(Math.hypot(p[0], p[1]), 1e-3);
  return [
    u - east / (registration.imageWidth * cosLat),
    v + (north - registration.yOffsetPx) / registration.imageHeight,
  ];
}

function base64ToBytes(data: string): Uint8Array {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Reads `eso0932a_registration.json`; throws on anything malformed rather than drawing the photograph unregistered. */
export function parsePanoramaRegistration(json: unknown): PanoramaRegistration {
  const doc = json as {
    format?: unknown;
    formatVersion?: unknown;
    image?: { width?: unknown; height?: unknown };
    galacticToPanorama?: unknown;
    yOffsetPx?: unknown;
    offsetTable?: { width?: unknown; height?: unknown; scale?: unknown; bias?: unknown; data?: unknown };
  };
  if (doc?.format !== SKY_PANORAMA_REGISTRATION_FORMAT || doc.formatVersion !== 1) {
    throw new Error('not a version 1 sky panorama registration');
  }
  const rows = Array.isArray(doc.galacticToPanorama) ? doc.galacticToPanorama.flat() : [];
  const { image, offsetTable: table } = doc;
  if (
    rows.length !== 9 ||
    !rows.every(isFiniteNumber) ||
    !isFiniteNumber(image?.width) ||
    !isFiniteNumber(image?.height) ||
    !isFiniteNumber(doc.yOffsetPx) ||
    !isFiniteNumber(table?.width) ||
    !isFiniteNumber(table?.height) ||
    !isFiniteNumber(table?.scale) ||
    !isFiniteNumber(table?.bias) ||
    typeof table?.data !== 'string'
  ) {
    throw new Error('sky panorama registration is missing fields');
  }
  const codes = base64ToBytes(table.data);
  if (codes.length !== table.width * table.height * 2) {
    throw new Error('sky panorama offset table has the wrong size');
  }
  return {
    imageWidth: image.width,
    imageHeight: image.height,
    galacticToPanorama: rows as unknown as Mat3Rows,
    yOffsetPx: doc.yOffsetPx,
    offsetTable: { width: table.width, height: table.height, scale: table.scale, bias: table.bias, codes },
  };
}

export async function loadPanoramaRegistration(
  url: string = SKY_PANORAMA_REGISTRATION_URL,
  fetchImpl: typeof fetch = fetch
): Promise<PanoramaRegistration> {
  const response = await fetchImpl(url);
  if (!response.ok) throw new Error(`sky panorama registration: HTTP ${response.status}`);
  return parsePanoramaRegistration(await response.json());
}
