import type { Node } from 'three/webgpu';
import { GaussianSplat } from 'three/addons/objects/GaussianSplat.js';
import {
  createGaussianSplatGeometry,
  writeCovariance,
} from 'three/addons/utils/GaussianSplatUtils.js';
import { uniform, vec4 } from 'three/tsl';
import type { SplatArrays } from '../data/localBubble';

export interface ThreeSplatOptions {
  /** Linear emission multiplier applied in the fragment (HDR, not clamped). */
  intensity?: number;
  /**
   * Opacity is stored as 8-bit unorm. The Local Bubble opacities (0.003 to 0.045) land on
   * only about 11 code values, so they are stored multiplied by this factor and divided back
   * in the fragment. 1 reproduces the plain encoding.
   */
  opacityPrescale?: number;
}

export interface ThreeSplatLayer {
  mesh: GaussianSplat;
  intensity: { value: number };
  count: number;
  dispose(): void;
}

/**
 * Local Bubble splats through three.js r186+ `GaussianSplat` (TSL, WebGPU with GPU counting
 * sort; WebGL2 backend with CPU sort).
 */
export function createThreeSplatLayer(arrays: SplatArrays, options: ThreeSplatOptions = {}): ThreeSplatLayer {
  const n = arrays.count;
  const prescale = Math.max(1, options.opacityPrescale ?? 16);
  const covariances = new Float32Array(n * 6);
  const colors = new Uint8ClampedArray(n * 4);

  for (let i = 0; i < n; i++) {
    writeCovariance(
      covariances,
      i * 6,
      arrays.scales[i * 3]!,
      arrays.scales[i * 3 + 1]!,
      arrays.scales[i * 3 + 2]!,
      arrays.quaternions[i * 4]!,
      arrays.quaternions[i * 4 + 1]!,
      arrays.quaternions[i * 4 + 2]!,
      arrays.quaternions[i * 4 + 3]!
    );
    colors[i * 4] = arrays.colors[i * 3]! * 255;
    colors[i * 4 + 1] = arrays.colors[i * 3 + 1]! * 255;
    colors[i * 4 + 2] = arrays.colors[i * 3 + 2]! * 255;
    colors[i * 4 + 3] = arrays.opacities[i]! * prescale * 255;
  }

  const geometry = createGaussianSplatGeometry(arrays.centers.slice(), covariances, colors);
  const mesh = new GaussianSplat(geometry);
  mesh.name = 'local-bubble-three-gaussiansplat';

  const intensity = uniform(options.intensity ?? 1.0);
  const base = mesh.material.colorNode as Node<'vec4'>;
  mesh.material.colorNode = vec4(base.rgb.mul(intensity), base.a.div(prescale));

  return {
    mesh,
    intensity,
    count: n,
    dispose() {
      geometry.dispose();
      mesh.geometry.dispose();
      mesh.material.dispose();
    },
  };
}
