/** One splat row as `scripts/bake_bruneton_lut_and_splats.py` writes it. */
type SplatRow = [
  x: number, y: number, z: number,
  r: number, g: number, b: number,
  sx: number, sy: number, sz: number,
  qx: number, qy: number, qz: number, qw: number,
  opacity: number,
];

export const SPLAT_ROW_LENGTH = 14;

export interface SplatRows {
  count: number;
  /** xyz per splat in scene units: the row's position times the position scale. */
  centers: Float32Array;
  /** Per-axis Gaussian sigma per splat, scene units. */
  scales: Float32Array;
  /** Unit quaternion xyzw per splat. */
  quaternions: Float32Array;
  /** RGB in [0, 1] per splat. */
  colors: Float32Array;
  /** Opacity in [0, 1] per splat. */
  opacities: Float32Array;
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));

/**
 * Reads every complete row at its own position times `positionScale`. A row with a missing or
 * non-finite value is skipped rather than filled in, and no row is thinned out, moved or cut
 * away. `minScale` keeps a degenerate footprint from collapsing to zero.
 */
export function parseSplatRows(raw: unknown, positionScale: number, minScale: number): SplatRows {
  const rows: unknown[] = Array.isArray(raw) ? raw : [];
  const centers = new Float32Array(rows.length * 3);
  const scales = new Float32Array(rows.length * 3);
  const quaternions = new Float32Array(rows.length * 4);
  const colors = new Float32Array(rows.length * 3);
  const opacities = new Float32Array(rows.length);
  let count = 0;

  for (const row of rows) {
    if (!Array.isArray(row) || row.length < SPLAT_ROW_LENGTH) continue;
    const values = row.slice(0, SPLAT_ROW_LENGTH);
    if (!values.every((v) => typeof v === 'number' && Number.isFinite(v))) continue;
    const [x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity] = values as SplatRow;

    centers.set([x * positionScale, y * positionScale, z * positionScale], count * 3);
    scales.set([Math.max(minScale, sx), Math.max(minScale, sy), Math.max(minScale, sz)], count * 3);
    const qLen = Math.hypot(qx, qy, qz, qw);
    quaternions.set(qLen > 1e-3 ? [qx / qLen, qy / qLen, qz / qLen, qw / qLen] : [0, 0, 0, 1], count * 4);
    colors.set([clamp01(r), clamp01(g), clamp01(b)], count * 3);
    opacities[count] = clamp01(opacity);
    count += 1;
  }

  return {
    count,
    centers: centers.slice(0, count * 3),
    scales: scales.slice(0, count * 3),
    quaternions: quaternions.slice(0, count * 4),
    colors: colors.slice(0, count * 3),
    opacities: opacities.slice(0, count),
  };
}
