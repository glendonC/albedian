/**
 * Renderer-neutral port of `buildAnisotropicLocalBubbleSplatMesh` in
 * `apps/web/src/spatial/volumetricSplatsAndLut.ts` (which reads rows with `parseSplatRows`).
 *
 * The app's loader writes straight into a Spark `PackedSplats` (WebGL2 only). This port
 * applies the same per-splat rules to plain typed arrays so any WebGPU splat path
 * (three's `GaussianSplat` addon or `@sparkjsdev/radix`) can consume the identical splat set.
 * Keep the constants in lockstep with the app loader; `spike.test.ts` pins them.
 *
 * Provenance of `/models/lb_splats.json` (14,281 rows, 14-tuples):
 * - x, y, z: Local Bubble surface geometry, O'Neill et al. 2024 (Harvard CfA).
 * - sx, sy, sz, qx, qy, qz, qw: footprint and orientation from the local k-NN PCA frame, and
 *   r, g, b, opacity: one uniform presentation colour and opacity, all baked by
 *   `scripts/bake_bruneton_lut_and_splats.py`. They are presentation values, not measurements.
 *
 * The spike's measurements predate the loader fix and used the 2,811 splats the app then drew;
 * see section 12 of docs/architecture/RENDERER_WEBGPU_SPIKE.md.
 */

export interface SplatArrays {
  /** Number of splats written to the arrays below. */
  count: number;
  /** xyz per splat, scene units. */
  centers: Float32Array;
  /** Per-axis Gaussian standard deviation (sigma) per splat, scene units. */
  scales: Float32Array;
  /** Unit quaternion xyzw per splat. */
  quaternions: Float32Array;
  /** Authored RGB in [0, 1] per splat, as the app passes it to Spark. */
  colors: Float32Array;
  /** Opacity per splat after the app loader's clamps. */
  opacities: Float32Array;
}

/** Constants copied from the app loader. */
export const LOCAL_BUBBLE_LOADER_CONSTANTS = {
  rowLength: 14,
  positionScale: 1.24,
  minScale: 0.01,
} as const;

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

function emptyArrays(capacity: number): SplatArrays {
  return {
    count: 0,
    centers: new Float32Array(capacity * 3),
    scales: new Float32Array(capacity * 3),
    quaternions: new Float32Array(capacity * 4),
    colors: new Float32Array(capacity * 3),
    opacities: new Float32Array(capacity),
  };
}

function trim(arrays: SplatArrays): SplatArrays {
  const n = arrays.count;
  return {
    count: n,
    centers: arrays.centers.slice(0, n * 3),
    scales: arrays.scales.slice(0, n * 3),
    quaternions: arrays.quaternions.slice(0, n * 4),
    colors: arrays.colors.slice(0, n * 3),
    opacities: arrays.opacities.slice(0, n),
  };
}

/**
 * Mirrors the app loader exactly: every complete row at its own position times 1.24,
 * footprints floored at 0.01, quaternions normalized, colour and opacity clamped to [0, 1].
 * A row with a missing or non-finite value is skipped rather than filled in.
 */
export function parseLocalBubbleSplats(raw: unknown): SplatArrays {
  const rows: unknown[] = Array.isArray(raw) ? raw : [];
  const C = LOCAL_BUBBLE_LOADER_CONSTANTS;
  const out = emptyArrays(rows.length);

  for (const row of rows) {
    if (!Array.isArray(row) || row.length < C.rowLength) continue;
    const p = row.slice(0, C.rowLength) as number[];
    if (!p.every((v) => typeof v === 'number' && Number.isFinite(v))) continue;

    const k = out.count;
    out.centers[k * 3] = p[0]! * C.positionScale;
    out.centers[k * 3 + 1] = p[1]! * C.positionScale;
    out.centers[k * 3 + 2] = p[2]! * C.positionScale;
    out.colors[k * 3] = clamp01(p[3]!);
    out.colors[k * 3 + 1] = clamp01(p[4]!);
    out.colors[k * 3 + 2] = clamp01(p[5]!);
    out.scales[k * 3] = Math.max(C.minScale, p[6]!);
    out.scales[k * 3 + 1] = Math.max(C.minScale, p[7]!);
    out.scales[k * 3 + 2] = Math.max(C.minScale, p[8]!);

    let qx = p[9]!;
    let qy = p[10]!;
    let qz = p[11]!;
    let qw = p[12]!;
    const lenSq = qx * qx + qy * qy + qz * qz + qw * qw;
    if (lenSq > 1e-6) {
      const inv = 1 / Math.sqrt(lenSq);
      qx *= inv;
      qy *= inv;
      qz *= inv;
      qw *= inv;
    } else {
      qx = 0;
      qy = 0;
      qz = 0;
      qw = 1;
    }
    out.quaternions[k * 4] = qx;
    out.quaternions[k * 4 + 1] = qy;
    out.quaternions[k * 4 + 2] = qz;
    out.quaternions[k * 4 + 3] = qw;

    out.opacities[k] = clamp01(p[13]!);
    out.count += 1;
  }

  return trim(out);
}
