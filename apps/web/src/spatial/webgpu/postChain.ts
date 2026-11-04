import { RenderPipeline, type Camera, type Node, type Scene, type WebGPURenderer } from 'three/webgpu';
import { pass, renderOutput } from 'three/tsl';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { fxaa } from 'three/addons/tsl/display/FXAANode.js';
import { smaa } from 'three/addons/tsl/display/SMAANode.js';
import { cinemaOptics, type CinemaOpticsUniforms } from './cinemaOptics.js';

type Vec4Node = Node<'vec4'>;

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

export interface CosmosPostChain {
  pipeline: RenderPipeline;
  options: CosmosPostOptions;
  /** Ordered description of the passes, for benchmark records. */
  describe(): string;
  dispose(): void;
}

/**
 * HDR post chain on three's TSL `RenderPipeline`, after apps/webgpu-spike/src/render/post.ts:
 * scene pass into a HalfFloat linear target, bloom added in HDR, the cinema optics node on HDR,
 * AgX tone mapping and sRGB encoding (`renderOutput`), then FXAA or SMAA in display space.
 * Tone mapping happens once, here; scene materials write linear radiance.
 */
export function buildCosmosPostChain(
  renderer: WebGPURenderer,
  scene: Scene,
  camera: Camera,
  options: CosmosPostOptions,
  optics: CinemaOpticsUniforms
): CosmosPostChain {
  const pipeline = new RenderPipeline(renderer);
  const scenePass = pass(scene, camera);
  const stages = ['scene(HalfFloat)'];

  const hdr = scenePass.getTextureNode('output') as unknown as Vec4Node;
  let composed: Vec4Node = hdr;
  if (options.bloom) {
    const bloomNode = bloom(hdr, options.bloomStrength, options.bloomRadius, options.bloomThreshold);
    composed = hdr.add(bloomNode) as Vec4Node;
    stages.push(`bloom(s=${options.bloomStrength}, r=${options.bloomRadius}, t=${options.bloomThreshold})`);
  }
  if (options.cinemaOptics) {
    composed = cinemaOptics(composed, optics);
    stages.push('cinema optics(CA, anamorphic streak, vignette)');
  }

  // Explicit output transform so display-space AA runs after tone mapping, as FXAA/SMAA expect
  pipeline.outputColorTransform = false;
  const display = renderOutput(composed) as unknown as Vec4Node;
  stages.push('AgX + sRGB (renderOutput)');

  let out: Vec4Node = display;
  if (options.aa === 'fxaa') {
    out = fxaa(display) as unknown as Vec4Node;
    stages.push('FXAA');
  } else if (options.aa === 'smaa') {
    out = smaa(display) as unknown as Vec4Node;
    stages.push('SMAA');
  }
  pipeline.outputNode = out;

  return {
    pipeline,
    options,
    describe: () => stages.join(' > '),
    dispose() {
      pipeline.dispose();
      scenePass.dispose();
    },
  };
}
