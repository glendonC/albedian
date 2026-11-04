import { Object3D, type WebGPURenderer } from 'three/webgpu';
import {
  GpuArrays,
  GpuSplats,
  SplatCollection,
  SplatRenderer,
  Splats,
  type ExtSplats,
  type SplatModifier,
} from '@sparkjsdev/radix';
import type { SplatArrays } from '../data/localBubble';
import { applyRadixR187ComputeShim } from './radixCompat';

export interface RadixSplatOptions {
  /** Linear emission multiplier, baked into the stored color (extSplats colors may exceed 1). */
  intensity?: number;
  /**
   * radix stores opacity in 8 bits at 1/128 steps, so 0.003 would round to 0. Opacities are
   * stored multiplied by this factor and divided back in a TSL modifier before projection.
   */
  opacityPrescale?: number;
  /** View parameter: pixels below this alpha are discarded and kernels cut there. */
  minAlpha?: number;
}

export interface RadixSplatLayer {
  renderer: SplatRenderer;
  anchor: Object3D;
  count: number;
  dispose(): void;
}

/** Linear to sRGB transfer, extended above 1 (radix decodes sRGB when drawing into linear targets). */
function srgbEncode(x: number): number {
  return x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
}

/**
 * Local Bubble splats through `@sparkjsdev/radix` 0.0.1 (World Labs, WebGPU compute projection
 * and radix sort, drawn by three's WebGPURenderer).
 *
 * Color parity with the three path: radix treats splat RGB as sRGB-encoded (as trained 3DGS
 * colors are) and decodes it when the target is linear, as a `pass()` HalfFloat target is.
 * The app passes its colors to Spark as linear values, so they are sRGB-encoded here and the
 * decode returns the original linear value.
 */
export function createRadixSplatLayer(
  renderer: WebGPURenderer,
  arrays: SplatArrays,
  options: RadixSplatOptions = {}
): RadixSplatLayer {
  applyRadixR187ComputeShim(renderer);
  const n = arrays.count;
  const intensity = options.intensity ?? 1.0;
  const prescale = Math.max(1, options.opacityPrescale ?? 32);

  const data = Splats.make(n);
  const found = Splats.get(data);
  const bundle = found.bundle as ExtSplats;
  const s = Splats.makeExtSplat();

  for (let i = 0; i < n; i++) {
    s.center.x = arrays.centers[i * 3]!;
    s.center.y = arrays.centers[i * 3 + 1]!;
    s.center.z = arrays.centers[i * 3 + 2]!;
    s.scales.x = arrays.scales[i * 3]!;
    s.scales.y = arrays.scales[i * 3 + 1]!;
    s.scales.z = arrays.scales[i * 3 + 2]!;
    s.quaternion.x = arrays.quaternions[i * 4]!;
    s.quaternion.y = arrays.quaternions[i * 4 + 1]!;
    s.quaternion.z = arrays.quaternions[i * 4 + 2]!;
    s.quaternion.w = arrays.quaternions[i * 4 + 3]!;
    s.rgb.r = srgbEncode(arrays.colors[i * 3]! * intensity);
    s.rgb.g = srgbEncode(arrays.colors[i * 3 + 1]! * intensity);
    s.rgb.b = srgbEncode(arrays.colors[i * 3 + 2]! * intensity);
    s.opacity = arrays.opacities[i]! * prescale;
    Splats.encodeExt(bundle, i, s);
  }

  const gpu = GpuArrays.fromData(renderer, data);
  const anchor = new Object3D();
  anchor.name = 'local-bubble-radix-anchor';

  const invPrescale = 1 / prescale;
  const modifier: SplatModifier = (splat) =>
    GpuSplats.Splat(
      splat.get('center'),
      splat.get('scales'),
      splat.get('quaternion'),
      splat.get('rgb'),
      splat.get('opacity').mul(invPrescale),
      splat.get('index')
    );

  const splatRenderer = new SplatRenderer({
    renderer,
    collection: new SplatCollection([SplatCollection.arrays(gpu, { matrixWorld: anchor.matrixWorld })]),
    modifier,
    // Match the app's Spark settings (maxStdDev sqrt(8)); a low minAlpha keeps the soft wings
    // of these very low-opacity splats instead of cutting them near 1 sigma.
    params: { maxStdDev: Math.sqrt(8), minAlpha: options.minAlpha ?? 0.0002 },
  });
  splatRenderer.name = 'local-bubble-radix';
  splatRenderer.frustumCulled = false;

  return {
    renderer: splatRenderer,
    anchor,
    count: n,
    dispose() {
      splatRenderer.dispose();
      gpu.dispose();
    },
  };
}
