import { RenderPipeline, type Camera, type Node, type Scene, type WebGPURenderer } from 'three/webgpu';
import { convertToTexture, mrt, output, pass, renderOutput, velocity } from 'three/tsl';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { fxaa } from 'three/addons/tsl/display/FXAANode.js';
import { smaa } from 'three/addons/tsl/display/SMAANode.js';
import { traa } from 'three/addons/tsl/display/TRAANode.js';
import { dustVolumeComposite, type DustVolumeGpu, type DustVolumeOptions } from './dustVolume';

export type AaMode = 'none' | 'fxaa' | 'smaa' | 'traa' | 'msaa4';
export const AA_MODES: readonly AaMode[] = ['none', 'fxaa', 'smaa', 'traa', 'msaa4'];

export interface PostOptions {
  aa: AaMode;
  bloom: boolean;
  bloomStrength: number;
  bloomRadius: number;
  /** Linear HDR luminance above which pixels feed the bloom. */
  bloomThreshold: number;
}

export const DEFAULT_POST: PostOptions = {
  aa: 'fxaa',
  bloom: true,
  // The star sprites carry their own Airy, halo and spike terms, so post bloom is only the
  // camera's veiling glare for very bright pixels and extended sources.
  bloomStrength: 0.35,
  bloomRadius: 0.5,
  bloomThreshold: 2.0,
};

export interface DustStage {
  gpu: DustVolumeGpu;
  options: DustVolumeOptions;
}

export interface PostChain {
  pipeline: RenderPipeline;
  options: PostOptions;
  dust: DustStage | null;
  /** Ordered description of the passes, for the HUD and benchmark records. */
  describe(): string;
  dispose(): void;
}

/**
 * HDR post chain on three's TSL RenderPipeline:
 * scene pass (HalfFloat linear HDR; optional 4x MSAA, or velocity MRT for TRAA), optional TRAA
 * on HDR, optional dust volume ray march composited over the scene in HDR (rendered once to a
 * HalfFloat target), bloom added in HDR, AgX tone mapping and sRGB encode (renderOutput), then
 * FXAA or SMAA in display space. Optics (bloom) act after the dust, as in a real camera.
 */
export function buildPostChain(
  renderer: WebGPURenderer,
  scene: Scene,
  camera: Camera,
  options: PostOptions,
  dust: DustStage | null = null
): PostChain {
  const pipeline = new RenderPipeline(renderer);
  const scenePass = pass(scene, camera, options.aa === 'msaa4' ? { samples: 4 } : {});
  const stages: string[] = [options.aa === 'msaa4' ? 'scene(HalfFloat, MSAA 4x)' : 'scene(HalfFloat)'];

  let hdr: Node<'vec4'>;
  if (options.aa === 'traa') {
    scenePass.setMRT(mrt({ output, velocity }));
    hdr = traa(
      scenePass.getTextureNode('output'),
      scenePass.getTextureNode('depth'),
      scenePass.getTextureNode('velocity'),
      camera
    ) as unknown as Node<'vec4'>;
    stages.push('TRAA(HDR)');
  } else {
    hdr = scenePass.getTextureNode('output') as unknown as Node<'vec4'>;
  }

  if (dust) {
    const composite = dustVolumeComposite(hdr, dust.gpu, dust.options, camera);
    hdr = convertToTexture(composite.node) as unknown as Node<'vec4'>;
    const o = dust.options;
    stages.push(
      `dust ray march(step ${o.stepFraction} voxel, ${o.emptySpaceSkipping ? 'empty-space skipping' : 'no skipping'}, ${o.resolutionScale ?? 1}x res, ${o.chromatic ? 'CCM89 chromatic' : 'grey'} extinction x${o.extinctionScale})`
    );
  }

  let composed: Node<'vec4'> = hdr;
  if (options.bloom) {
    const bloomNode = bloom(hdr, options.bloomStrength, options.bloomRadius, options.bloomThreshold);
    composed = hdr.add(bloomNode) as Node<'vec4'>;
    stages.push(`bloom(s=${options.bloomStrength}, r=${options.bloomRadius}, t=${options.bloomThreshold})`);
  }

  // Explicit output transform so display-space AA runs after tone mapping, as FXAA/SMAA expect.
  pipeline.outputColorTransform = false;
  const display = renderOutput(composed) as unknown as Node<'vec4'>;
  stages.push('AgX + sRGB (renderOutput)');

  let out: Node<'vec4'> = display;
  if (options.aa === 'fxaa') {
    out = fxaa(display) as unknown as Node<'vec4'>;
    stages.push('FXAA');
  } else if (options.aa === 'smaa') {
    out = smaa(display) as unknown as Node<'vec4'>;
    stages.push('SMAA');
  }
  pipeline.outputNode = out;

  return {
    pipeline,
    options,
    dust,
    describe: () => stages.join(' > '),
    dispose() {
      pipeline.dispose();
      scenePass.dispose();
    },
  };
}
