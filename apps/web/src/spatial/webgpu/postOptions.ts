/**
 * The WebGPU 3D view's post options: the steps its post chain (postChain.ts) applies to the whole finished frame after
 * the scene is drawn, each a switch with the numbers of its own settings. CislunarWebGpuRenderer starts from
 * DEFAULT_COSMOS_POST. No three.js here: the Credits page words every switch of this list (spatial/frameFinishing.ts),
 * and every key here is a switch it words or a number set to one, or creditChanges.test.ts fails and names the key.
 */
export type CosmosAaMode = 'none' | 'fxaa' | 'smaa';

export interface CosmosPostOptions {
  aa: CosmosAaMode;
  bloom: boolean;
  bloomStrength: number;
  bloomRadius: number;
  /** Linear HDR luminance above which pixels feed the bloom. */
  bloomThreshold: number;
  cinemaOptics: boolean;
}

/**
 * Bloom uses the spike's settings (apps/webgpu-spike/src/render/post.ts), not the WebGL
 * composer's `UnrealBloomPass` (strength 0.64, radius 0.72, threshold 0.6). The catalog stars now
 * carry their own Airy, halo and spike terms with linear flux: a star's core peaks near
 * 318 x 10^(-0.4 m), so a 0.6 threshold put a second glare halo on every star brighter than
 * magnitude 6.8, nearly all of them. At 2.0 only stars brighter than about 5.5 and the brightest
 * surfaces bloom, and more weakly. `BloomNode` is a TSL port of `UnrealBloomPass`, so only the
 * settings and the tone mapping after it (ACES to AgX) differ.
 */
export const DEFAULT_COSMOS_POST: CosmosPostOptions = {
  aa: 'fxaa',
  bloom: true,
  bloomStrength: 0.35,
  bloomRadius: 0.5,
  bloomThreshold: 2.0,
  cinemaOptics: true,
};
