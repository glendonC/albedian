import type { SplatArrays } from './localBubble';

/**
 * Voxelizes the same Local Bubble splat set the splat paths draw into an emission-absorption
 * density grid, so the volume path renders identical data.
 *
 * Each splat i is an anisotropic Gaussian g_i(x) = exp(-1/2 d^T Sigma_i^-1 d) with
 * Sigma_i = R S^2 R^T from its scales and quaternion. It deposits an extinction coefficient
 * kappa_i * g_i(x) (per scene unit length), with kappa_i = alpha_i / (sqrt(2 pi) * sigma_geo,i)
 * so that the optical depth through its center along a ray of path sigma_geo equals its splat
 * opacity alpha_i in the optically thin limit (sigma_geo is the geometric mean of the three
 * sigmas; a splat's projected opacity is view independent while a real Gaussian's column is
 * not, so this is the view-averaged equivalent). The source color per voxel is the
 * extinction-weighted mean of the splat colors. This is a stand-in density: the baked opacities
 * are presentation values, not calibrated dust extinction.
 */

export interface DustVolume {
  dims: [number, number, number];
  boundsMin: [number, number, number];
  boundsMax: [number, number, number];
  voxelSize: number;
  /** Float RGBA per voxel: rgb = source color, a = extinction coefficient per scene unit. */
  rgbaSigma: Float32Array;
  maxSigma: number;
  brickSize: number;
  brickDims: [number, number, number];
  /** Max extinction per brick, dilated by one voxel for trilinear footprints. */
  brickMaxSigma: Float32Array;
  stats: {
    splats: number;
    voxels: number;
    nonEmptyVoxelFraction: number;
    nonEmptyBrickFraction: number;
    kernelEvaluations: number;
  };
}

export interface VoxelizeOptions {
  voxelSize?: number;
  /** Kernel support in sigmas. */
  cutoffSigma?: number;
  brickSize?: number;
  /** Extinction below which a voxel or brick counts as empty. */
  emptyThreshold?: number;
}

export function voxelizeSplats(arrays: SplatArrays, options: VoxelizeOptions = {}): DustVolume {
  const voxelSize = options.voxelSize ?? 0.1;
  const cutoff = options.cutoffSigma ?? 3;
  const brickSize = options.brickSize ?? 8;
  const emptyThreshold = options.emptyThreshold ?? 1e-5;
  const n = arrays.count;

  // Per-splat inverse covariance and axis-aligned support.
  const inv = new Float64Array(n * 6);
  const half = new Float64Array(n * 3);
  const kappa = new Float64Array(n);
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];

  for (let i = 0; i < n; i++) {
    const sx = arrays.scales[i * 3]!;
    const sy = arrays.scales[i * 3 + 1]!;
    const sz = arrays.scales[i * 3 + 2]!;
    const qx = arrays.quaternions[i * 4]!;
    const qy = arrays.quaternions[i * 4 + 1]!;
    const qz = arrays.quaternions[i * 4 + 2]!;
    const qw = arrays.quaternions[i * 4 + 3]!;
    // Rotation matrix columns from the unit quaternion.
    const r00 = 1 - 2 * (qy * qy + qz * qz);
    const r01 = 2 * (qx * qy - qz * qw);
    const r02 = 2 * (qx * qz + qy * qw);
    const r10 = 2 * (qx * qy + qz * qw);
    const r11 = 1 - 2 * (qx * qx + qz * qz);
    const r12 = 2 * (qy * qz - qx * qw);
    const r20 = 2 * (qx * qz - qy * qw);
    const r21 = 2 * (qy * qz + qx * qw);
    const r22 = 1 - 2 * (qx * qx + qy * qy);
    const ix = 1 / (sx * sx);
    const iy = 1 / (sy * sy);
    const iz = 1 / (sz * sz);
    // Sigma^-1 = R diag(1/s^2) R^T (symmetric: xx, xy, xz, yy, yz, zz)
    inv[i * 6] = r00 * r00 * ix + r01 * r01 * iy + r02 * r02 * iz;
    inv[i * 6 + 1] = r00 * r10 * ix + r01 * r11 * iy + r02 * r12 * iz;
    inv[i * 6 + 2] = r00 * r20 * ix + r01 * r21 * iy + r02 * r22 * iz;
    inv[i * 6 + 3] = r10 * r10 * ix + r11 * r11 * iy + r12 * r12 * iz;
    inv[i * 6 + 4] = r10 * r20 * ix + r11 * r21 * iy + r12 * r22 * iz;
    inv[i * 6 + 5] = r20 * r20 * ix + r21 * r21 * iy + r22 * r22 * iz;
    // Sigma diagonal gives the per-axis standard deviation of the rotated Gaussian.
    const cxx = r00 * r00 * sx * sx + r01 * r01 * sy * sy + r02 * r02 * sz * sz;
    const cyy = r10 * r10 * sx * sx + r11 * r11 * sy * sy + r12 * r12 * sz * sz;
    const czz = r20 * r20 * sx * sx + r21 * r21 * sy * sy + r22 * r22 * sz * sz;
    half[i * 3] = cutoff * Math.sqrt(cxx);
    half[i * 3 + 1] = cutoff * Math.sqrt(cyy);
    half[i * 3 + 2] = cutoff * Math.sqrt(czz);
    kappa[i] = arrays.opacities[i]! / (Math.sqrt(2 * Math.PI) * Math.cbrt(sx * sy * sz));
    for (let a = 0; a < 3; a++) {
      lo[a] = Math.min(lo[a]!, arrays.centers[i * 3 + a]! - half[i * 3 + a]!);
      hi[a] = Math.max(hi[a]!, arrays.centers[i * 3 + a]! + half[i * 3 + a]!);
    }
  }
  if (n === 0) {
    lo[0] = lo[1] = lo[2] = 0;
    hi[0] = hi[1] = hi[2] = voxelSize;
  }

  const dims: [number, number, number] = [0, 0, 0];
  const boundsMin: [number, number, number] = [0, 0, 0];
  const boundsMax: [number, number, number] = [0, 0, 0];
  for (let a = 0; a < 3; a++) {
    boundsMin[a] = lo[a]! - voxelSize;
    dims[a] = Math.max(1, Math.ceil((hi[a]! + voxelSize - boundsMin[a]!) / voxelSize));
    boundsMax[a] = boundsMin[a]! + dims[a]! * voxelSize;
  }
  const [nx, ny, nz] = dims;
  const voxels = nx * ny * nz;
  const sigma = new Float32Array(voxels);
  const colorSum = new Float32Array(voxels * 3);
  let kernelEvaluations = 0;

  for (let i = 0; i < n; i++) {
    const cx = arrays.centers[i * 3]!;
    const cy = arrays.centers[i * 3 + 1]!;
    const cz = arrays.centers[i * 3 + 2]!;
    const a00 = inv[i * 6]!;
    const a01 = inv[i * 6 + 1]!;
    const a02 = inv[i * 6 + 2]!;
    const a11 = inv[i * 6 + 3]!;
    const a12 = inv[i * 6 + 4]!;
    const a22 = inv[i * 6 + 5]!;
    const k = kappa[i]!;
    const cr = arrays.colors[i * 3]!;
    const cg = arrays.colors[i * 3 + 1]!;
    const cb = arrays.colors[i * 3 + 2]!;
    const x0 = Math.max(0, Math.floor((cx - half[i * 3]! - boundsMin[0]) / voxelSize));
    const x1 = Math.min(nx - 1, Math.ceil((cx + half[i * 3]! - boundsMin[0]) / voxelSize));
    const y0 = Math.max(0, Math.floor((cy - half[i * 3 + 1]! - boundsMin[1]) / voxelSize));
    const y1 = Math.min(ny - 1, Math.ceil((cy + half[i * 3 + 1]! - boundsMin[1]) / voxelSize));
    const z0 = Math.max(0, Math.floor((cz - half[i * 3 + 2]! - boundsMin[2]) / voxelSize));
    const z1 = Math.min(nz - 1, Math.ceil((cz + half[i * 3 + 2]! - boundsMin[2]) / voxelSize));
    const qMax = cutoff * cutoff;
    for (let z = z0; z <= z1; z++) {
      const dz = boundsMin[2] + (z + 0.5) * voxelSize - cz;
      for (let y = y0; y <= y1; y++) {
        const dy = boundsMin[1] + (y + 0.5) * voxelSize - cy;
        const row = (z * ny + y) * nx;
        for (let x = x0; x <= x1; x++) {
          const dx = boundsMin[0] + (x + 0.5) * voxelSize - cx;
          kernelEvaluations++;
          const q =
            a00 * dx * dx + a11 * dy * dy + a22 * dz * dz + 2 * (a01 * dx * dy + a02 * dx * dz + a12 * dy * dz);
          if (q > qMax) continue;
          const w = k * Math.exp(-0.5 * q);
          const v = row + x;
          sigma[v] = sigma[v]! + w;
          colorSum[v * 3] = colorSum[v * 3]! + w * cr;
          colorSum[v * 3 + 1] = colorSum[v * 3 + 1]! + w * cg;
          colorSum[v * 3 + 2] = colorSum[v * 3 + 2]! + w * cb;
        }
      }
    }
  }

  const rgbaSigma = new Float32Array(voxels * 4);
  let maxSigma = 0;
  let nonEmpty = 0;
  for (let v = 0; v < voxels; v++) {
    const s = sigma[v]!;
    if (s > emptyThreshold) nonEmpty++;
    maxSigma = Math.max(maxSigma, s);
    const invS = s > 0 ? 1 / s : 0;
    rgbaSigma[v * 4] = colorSum[v * 3]! * invS;
    rgbaSigma[v * 4 + 1] = colorSum[v * 3 + 1]! * invS;
    rgbaSigma[v * 4 + 2] = colorSum[v * 3 + 2]! * invS;
    rgbaSigma[v * 4 + 3] = s;
  }

  const brickDims: [number, number, number] = [
    Math.ceil(nx / brickSize),
    Math.ceil(ny / brickSize),
    Math.ceil(nz / brickSize),
  ];
  const [bx, by, bz] = brickDims;
  const brickMaxSigma = new Float32Array(bx * by * bz);
  let nonEmptyBricks = 0;
  for (let k = 0; k < bz; k++) {
    for (let j = 0; j < by; j++) {
      for (let i = 0; i < bx; i++) {
        let m = 0;
        // One voxel of dilation so trilinear taps at brick borders are covered.
        const xs = Math.max(0, i * brickSize - 1);
        const xe = Math.min(nx - 1, (i + 1) * brickSize);
        const ys = Math.max(0, j * brickSize - 1);
        const ye = Math.min(ny - 1, (j + 1) * brickSize);
        const zs = Math.max(0, k * brickSize - 1);
        const ze = Math.min(nz - 1, (k + 1) * brickSize);
        for (let z = zs; z <= ze; z++) {
          for (let y = ys; y <= ye; y++) {
            const row = (z * ny + y) * nx;
            for (let x = xs; x <= xe; x++) m = Math.max(m, sigma[row + x]!);
          }
        }
        brickMaxSigma[(k * by + j) * bx + i] = m;
        if (m > emptyThreshold) nonEmptyBricks++;
      }
    }
  }

  return {
    dims,
    boundsMin,
    boundsMax,
    voxelSize,
    rgbaSigma,
    maxSigma,
    brickSize,
    brickDims,
    brickMaxSigma,
    stats: {
      splats: n,
      voxels,
      nonEmptyVoxelFraction: voxels ? nonEmpty / voxels : 0,
      nonEmptyBrickFraction: brickMaxSigma.length ? nonEmptyBricks / brickMaxSigma.length : 0,
      kernelEvaluations,
    },
  };
}

/** Optical depth along a straight segment through the grid (CPU reference, nearest voxel). */
export function opticalDepthAlong(
  volume: DustVolume,
  origin: [number, number, number],
  direction: [number, number, number],
  length: number,
  step = volume.voxelSize * 0.5
): number {
  const [nx, ny, nz] = volume.dims;
  let tau = 0;
  for (let t = 0.5 * step; t < length; t += step) {
    const p = [origin[0] + direction[0] * t, origin[1] + direction[1] * t, origin[2] + direction[2] * t];
    const x = Math.floor((p[0]! - volume.boundsMin[0]) / volume.voxelSize);
    const y = Math.floor((p[1]! - volume.boundsMin[1]) / volume.voxelSize);
    const z = Math.floor((p[2]! - volume.boundsMin[2]) / volume.voxelSize);
    if (x < 0 || y < 0 || z < 0 || x >= nx || y >= ny || z >= nz) continue;
    tau += volume.rgbaSigma[((z * ny + y) * nx + x) * 4 + 3]! * step;
  }
  return tau;
}
