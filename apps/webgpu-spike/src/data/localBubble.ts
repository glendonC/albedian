/**
 * Renderer-neutral port of `buildAnisotropicLocalBubbleSplatMesh` in
 * `apps/web/src/spatial/volumetricSplatsAndLut.ts`.
 *
 * The app's loader writes straight into a Spark `PackedSplats` (WebGL2 only). This port
 * applies the same per-splat transforms to plain typed arrays so any WebGPU splat path
 * (three's `GaussianSplat` addon or `@sparkjsdev/radix`) can consume the identical splat set.
 * Keep the constants in lockstep with the app loader; `localBubble.test.ts` pins them.
 *
 * Provenance of `/models/lb_splats.json` (14,281 rows, 14-tuples):
 * - x, y, z: Local Bubble shell geometry, O'Neill et al. 2024 (Harvard CfA).
 * - r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity: procedurally baked styling from
 *   `scripts/bake_bruneton_lut_and_splats.py` (k-NN PCA orientation, artistic palette).
 *   They are presentation values, not measurements.
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
  positionScale: 1.24,
  jitterAmplitude: 0.28,
  innerCavityRadius: 4.55,
  strideThreshold: 6000,
  cavityRuleMinRows: 64,
  minScale: 0.01,
  minOpacity: 0.003,
  maxOpacity: 0.045,
} as const;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
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
 * Mirrors the app loader exactly: stride 2 above 6,000 rows, deterministic position jitter,
 * 1.24x position scale, removal of splats inside the cylindrical inner cavity
 * (xy distance < 4.55), opacity cavity fade and clamp to [0.003, 0.045].
 */
export function parseLocalBubbleSplats(raw: unknown): SplatArrays {
  if (!Array.isArray(raw) || raw.length === 0) return emptyArrays(0);

  const C = LOCAL_BUBBLE_LOADER_CONSTANTS;
  const stride = raw.length > C.strideThreshold ? 2 : 1;
  const enforceInnerCavity = raw.length > C.cavityRuleMinRows;
  const out = emptyArrays(Math.ceil(raw.length / stride));

  for (let i = 0; i < raw.length; i += stride) {
    const pt = raw[i] as unknown;
    if (!Array.isArray(pt) || pt.length < 6) continue;
    const p = pt as number[];

    const jx = (Math.sin(i * 12.9898) - 0.5) * C.jitterAmplitude;
    const jy = (Math.cos(i * 78.233) - 0.5) * C.jitterAmplitude;
    const jz = (Math.sin(i * 37.719) - 0.5) * C.jitterAmplitude;
    const x = p[0]! * C.positionScale + jx;
    const y = p[1]! * C.positionScale + jy;
    const z = p[2]! * C.positionScale + jz;

    const xyDist = Math.hypot(x, y);
    if (enforceInnerCavity && xyDist < C.innerCavityRadius) continue;

    const k = out.count;
    out.centers[k * 3] = x;
    out.centers[k * 3 + 1] = y;
    out.centers[k * 3 + 2] = z;

    if (p.length >= 14) {
      out.colors[k * 3] = clamp(p[3]!, 0, 1);
      out.colors[k * 3 + 1] = clamp(p[4]!, 0, 1);
      out.colors[k * 3 + 2] = clamp(p[5]!, 0, 1);
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

      const cavityFade = enforceInnerCavity
        ? Math.min(1.0, Math.max(0.25, (xyDist - C.innerCavityRadius) * 0.55))
        : 1.0;
      out.opacities[k] = clamp(p[13]! * cavityFade, C.minOpacity, C.maxOpacity);
    } else {
      // Same fallback branch as the app loader for short rows.
      const s = 0.095 + (i % 5) * 0.022;
      out.scales[k * 3] = s * 1.5;
      out.scales[k * 3 + 1] = s * 1.1;
      out.scales[k * 3 + 2] = s * 0.65;
      out.quaternions[k * 4 + 3] = 1;
      const warmMix = (i % 9) / 9.0;
      out.colors[k * 3] = 0.78 + 0.18 * warmMix;
      out.colors[k * 3 + 1] = 0.54 + 0.14 * warmMix;
      out.colors[k * 3 + 2] = 0.36 + 0.12 * (1.0 - warmMix);
      out.opacities[k] = Math.min(
        0.016,
        Math.max(0.004, Math.max(0.1, xyDist - C.innerCavityRadius) * 0.0035)
      );
    }
    out.count += 1;
  }

  return trim(out);
}
