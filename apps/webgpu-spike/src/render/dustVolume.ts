import {
  ClampToEdgeWrapping,
  Data3DTexture,
  DataUtils,
  HalfFloatType,
  LinearFilter,
  Matrix4,
  NearestFilter,
  RGBAFormat,
  RedFormat,
  Vector3,
  type Camera,
  type Node,
} from 'three/webgpu';
import {
  Break,
  Continue,
  Fn,
  If,
  Loop,
  clamp,
  exp,
  float,
  floor,
  getViewPosition,
  interleavedGradientNoise,
  ivec3,
  max,
  min,
  normalize,
  rtt,
  screenCoordinate,
  texture3D,
  texture3DLoad,
  uniform,
  uv,
  vec3,
  vec4,
} from 'three/tsl';
import { srgbExtinctionRatios } from '../color/extinction';
import type { DustVolume } from '../data/voxelize';

export interface DustVolumeOptions {
  /** Emission multiplier, matching the splat paths' intensity. */
  intensity: number;
  /**
   * Multiplier on the voxelized extinction (1 = same opacity as the splats). Emission stays tied
   * to the unscaled extinction, so values above 1 only add absorption (an illustration of
   * reddening, not calibrated dust).
   */
  extinctionScale: number;
  /** Ray step as a fraction of the voxel size. */
  stepFraction: number;
  /** Skip empty 8^3 bricks using the occupancy grid. */
  emptySpaceSkipping: boolean;
  /** Per-pixel start jitter (interleaved gradient noise) to trade banding for fine noise. */
  jitter: boolean;
  /** Apply CCM89 chromatic extinction (false = grey extinction, like splat alpha). */
  chromatic: boolean;
  maxSteps: number;
  /** March at this fraction of the output resolution (1 = full). */
  resolutionScale?: number;
}

export const DEFAULT_DUST_OPTIONS: DustVolumeOptions = {
  intensity: 2.2,
  extinctionScale: 1,
  stepFraction: 0.5,
  emptySpaceSkipping: true,
  jitter: true,
  chromatic: true,
  maxSteps: 1024,
  resolutionScale: 1,
};

export interface DustVolumeGpu {
  density: Data3DTexture;
  occupancy: Data3DTexture;
  volume: DustVolume;
  dispose(): void;
}

export function uploadDustVolume(volume: DustVolume): DustVolumeGpu {
  const [nx, ny, nz] = volume.dims;
  const half = new Uint16Array(volume.rgbaSigma.length);
  for (let i = 0; i < half.length; i++) half[i] = DataUtils.toHalfFloat(volume.rgbaSigma[i]!);
  const density = new Data3DTexture(half, nx, ny, nz);
  density.format = RGBAFormat;
  density.type = HalfFloatType;
  density.minFilter = LinearFilter;
  density.magFilter = LinearFilter;
  density.wrapS = density.wrapT = density.wrapR = ClampToEdgeWrapping;
  density.unpackAlignment = 1;
  density.needsUpdate = true;

  const [bx, by, bz] = volume.brickDims;
  const occ = new Uint16Array(volume.brickMaxSigma.length);
  for (let i = 0; i < occ.length; i++) occ[i] = DataUtils.toHalfFloat(volume.brickMaxSigma[i]!);
  const occupancy = new Data3DTexture(occ, bx, by, bz);
  occupancy.format = RedFormat;
  occupancy.type = HalfFloatType;
  occupancy.minFilter = NearestFilter;
  occupancy.magFilter = NearestFilter;
  occupancy.wrapS = occupancy.wrapT = occupancy.wrapR = ClampToEdgeWrapping;
  occupancy.unpackAlignment = 1;
  occupancy.needsUpdate = true;

  return {
    density,
    occupancy,
    volume,
    dispose() {
      density.dispose();
      occupancy.dispose();
    },
  };
}

/**
 * Emission-absorption volume ray marcher as a TSL full-screen node.
 *
 * Per pixel: intersect the view ray with the grid's box, march front to back with step ds,
 * and integrate dL_c/ds = -sigma_c L_c + j_c with sigma_c = k * sigma_V * (A_c / A_V) (CCM89 at
 * the channel wavelengths, k = extinctionScale) and j_c = sigma_V * S_c * intensity, using the
 * exact per-step solution L_c += T_c * S_c * intensity * (1 - exp(-sigma_c ds)) / (k * A_c / A_V)
 * and T_c *= exp(-sigma_c ds).
 * The scene behind the volume (stars) is attenuated per channel by the final T_c, so stars are
 * dimmed and reddened by the integrated column. Empty 8^3 bricks are skipped by jumping to the
 * brick exit; rays stop early once green transmittance falls below 0.002.
 */
export function dustVolumeComposite(
  background: Node<'vec4'>,
  gpu: DustVolumeGpu,
  options: DustVolumeOptions,
  camera: Camera
): { node: Node<'vec4'>; uniforms: { intensity: { value: number }; extinctionScale: { value: number } } } {
  const v = gpu.volume;
  // This node runs in a full-screen quad pass, where cameraPosition / cameraWorldMatrix refer
  // to the quad's own camera, so the scene camera is bound explicitly (as GodraysNode does).
  const tmp = new Vector3();
  const camPos = uniform(new Vector3()).onRenderUpdate(() => camera.getWorldPosition(tmp));
  const camWorld = uniform(new Matrix4()).onRenderUpdate(() => camera.matrixWorld);
  const projInv = uniform(new Matrix4()).onRenderUpdate(() => camera.projectionMatrixInverse);
  const boundsMin = uniform(new Vector3(...v.boundsMin));
  const boundsMax = uniform(new Vector3(...v.boundsMax));
  const boundsSize = uniform(
    new Vector3(v.boundsMax[0] - v.boundsMin[0], v.boundsMax[1] - v.boundsMin[1], v.boundsMax[2] - v.boundsMin[2])
  );
  const brickWorld = v.voxelSize * v.brickSize;
  const brickDims = vec3(...v.brickDims);
  const intensity = uniform(options.intensity);
  const extinctionScale = uniform(options.extinctionScale);
  const ratios = options.chromatic ? srgbExtinctionRatios() : [1, 1, 1];
  const ratio = vec3(ratios[0]!, ratios[1]!, ratios[2]!);
  const step = v.voxelSize * options.stepFraction;
  const emptySigma = 1e-5;

  // Returns vec4(L_rgb, tau): tau is the integrated optical depth at the CCM reference (green)
  // channel. Because every voxel shares one extinction law, T_c = exp(-tau * ratio_c / ratio_g)
  // exactly, so one channel of optical depth is enough to reconstruct chromatic transmittance.
  const march = Fn(() => {
    const viewPos = getViewPosition(uv(), float(0.5), projInv);
    const dir = normalize(camWorld.mul(vec4(normalize(viewPos), 0.0)).xyz).toVar('dustDir');
    const ro = vec3(camPos).toVar('dustOrigin');
    const inv = vec3(1.0).div(dir.add(vec3(1e-7))).toVar('dustInvDir');
    const tA = boundsMin.sub(ro).mul(inv);
    const tB = boundsMax.sub(ro).mul(inv);
    const tLo = min(tA, tB);
    const tHi = max(tA, tB);
    const tNear = max(max(tLo.x, tLo.y), max(tLo.z, 0.0)).toVar('dustTNear');
    const tFar = min(min(tHi.x, tHi.y), tHi.z).toVar('dustTFar');
    const trans = vec3(1.0).toVar('dustT');
    const radiance = vec3(0.0).toVar('dustL');
    const tauG = float(0.0).toVar('dustTauG');

    If(tFar.greaterThan(tNear), () => {
      const jitter = options.jitter ? interleavedGradientNoise(screenCoordinate.xy) : float(0.5);
      const t = tNear.add(jitter.mul(step)).toVar('dustTCur');
      Loop(options.maxSteps, () => {
        If(t.greaterThanEqual(tFar).or(trans.y.lessThan(0.002)), () => {
          Break();
        });
        const p = ro.add(dir.mul(t)).toVar('dustP');
        const uvw = p.sub(boundsMin).div(boundsSize).toVar('dustUvw');
        if (options.emptySpaceSkipping) {
          // Brick index from world position (bricks are brickSize voxels; the last brick on each
          // axis may extend past the grid, so do not derive it from uvw).
          const brick = clamp(floor(p.sub(boundsMin).div(brickWorld)), vec3(0.0), brickDims.sub(1.0)).toVar('dustBrick');
          const occ = texture3DLoad(gpu.occupancy, ivec3(brick)).r;
          If(occ.mul(extinctionScale).lessThan(emptySigma), () => {
            // Jump to where the ray leaves this brick.
            const bMin = boundsMin.add(brick.mul(brickWorld));
            const bMax = bMin.add(brickWorld);
            const exitAxis = max(bMin.sub(p).mul(inv), bMax.sub(p).mul(inv));
            const tExit = min(min(exitAxis.x, exitAxis.y), exitAxis.z);
            t.addAssign(max(tExit, 0.0).add(step * 0.5));
            Continue();
          });
        }
        const s = texture3D(gpu.density, uvw).toVar('dustSample');
        const sigma = ratio.mul(s.a.mul(extinctionScale)).toVar('dustSigma');
        const attenuation = exp(sigma.mul(-step)).toVar('dustAtt');
        // Emission is tied to the unscaled extinction, so extinctionScale only adds absorption.
        radiance.addAssign(
          trans.mul(s.rgb).mul(intensity).mul(vec3(1.0).sub(attenuation)).div(ratio.mul(extinctionScale))
        );
        trans.mulAssign(attenuation);
        tauG.addAssign(sigma.y.mul(step));
        t.addAssign(step);
      });
    });

    return vec4(radiance, tauG);
  });

  // Full resolution: march and composite in one pass. Reduced resolution: march into a smaller
  // HalfFloat target and composite at full resolution, so stars stay sharp while the smooth
  // dust emission and optical depth are bilinearly upsampled.
  const scale = options.resolutionScale ?? 1;
  const marched = scale < 1 ? rtt(march(), null, null, { resolutionScale: scale }) : march();
  const node = Fn(() => {
    const m = vec4(marched).toVar('dustMarched');
    const trans = exp(ratio.div(ratio.y).mul(m.a.negate()));
    return vec4(background.rgb.mul(trans).add(m.rgb), 1.0);
  })();

  return { node: node as unknown as Node<'vec4'>, uniforms: { intensity, extinctionScale } };
}
