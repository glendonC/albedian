import type { Node, TextureNode } from 'three/webgpu';
import { Fn, convertToTexture, dot, float, mix, nodeObject, smoothstep, uniform, uv, vec2, vec3, vec4 } from 'three/tsl';
import GaussianBlurNode from 'three/addons/tsl/display/GaussianBlurNode.js';

type Vec4Node = Node<'vec4'>;

export interface CinemaOpticsUniforms {
  /** 0 at rest, up to 1 at the height of a regime flight; widens the chromatic aberration. */
  warpFactor: { value: number };
}

export function createCinemaOpticsUniforms(): CinemaOpticsUniforms {
  return { warpFactor: uniform(0) };
}

const LUMA_REC601 = vec3(0.299, 0.587, 0.114);

/** Linear luminance from which pixels start to feed the anamorphic streak, and fully from the second. */
export const STREAK_THRESHOLD: readonly [number, number] = [0.76, 1.35];
/**
 * Width in texels of the streak's blur target at any canvas size, so the streak spans the same
 * share of the frame at 1080p and 2160p.
 */
export const STREAK_BLUR_WIDTH = 480;
/**
 * GaussianBlurNode's sigma parameter: a kernel reaching 3 + 2 x 16 = 35 texels, 0.073 of the frame
 * width (the 8 taps it replaces reached 4 x 0.018 = 0.072), shaped by a Gaussian of a third of that.
 */
export const STREAK_SIGMA = 16;
/** The replaced taps' summed weight, 2 (e^-0.55 + e^-1.1 + e^-1.65 + e^-2.2); the streak keeps it. */
export const STREAK_ENERGY = 2 * [1, 2, 3, 4].reduce((sum, i) => sum + Math.exp(-0.55 * i), 0);

/** The streak's weights for texel offsets 0 to its reach, as GaussianBlurNode builds them (both sides sum to 1). */
export function streakKernel(): number[] {
  const radius = 3 + 2 * STREAK_SIGMA;
  const sigma = radius / 3;
  const weights = [1];
  let sum = 1;
  for (let i = 1; i < radius; i++) {
    const w = Math.exp((-0.5 * i * i) / (sigma * sigma));
    weights.push(w);
    sum += 2 * w;
  }
  return weights.map((w) => w / sum);
}

/** A Gaussian blur whose target is STREAK_BLUR_WIDTH texels wide whatever the canvas width. */
export class StreakBlurNode extends GaussianBlurNode {
  override setSize(width: number, height: number): void {
    this.resolutionScale = STREAK_BLUR_WIDTH / width;
    super.setSize(width, height);
  }
}

/**
 * Lens and sensor look of the landing scenes, ported from the GLSL `ShaderPass` in
 * SparkPortalRenderer.ts: radial chromatic aberration (red out, blue in), a horizontal
 * anamorphic streak from pixels brighter than 0.76 in linear luminance, and a vignette that
 * dims the corners to 0.76. It runs on linear HDR, after bloom and before tone mapping, as the
 * `EffectComposer` chain did. All of it is modeled camera optics, not part of any data.
 *
 * The streak is one horizontal Gaussian over the bright pixels (StreakBlurNode), so a star draws a
 * single smooth line and an extended source a soft glow. The port's 8 taps at fixed offsets drew
 * stars as rows of dots and left copies of the Moon's limb beside it.
 */
export function cinemaOptics(input: Node, uniforms: CinemaOpticsUniforms): Vec4Node {
  const source = convertToTexture(input) as unknown as TextureNode;
  const warp = uniforms.warpFactor as unknown as Node<'float'>;
  const screenUv = (source.uvNode ?? uv()) as Node<'vec2'>;

  const brightPass = Fn(() => {
    const colour = source.sample(uv()).rgb;
    return vec4(colour.mul(smoothstep(STREAK_THRESHOLD[0], STREAK_THRESHOLD[1], dot(colour, LUMA_REC601))), 1.0);
  })();
  const streak = nodeObject(new StreakBlurNode(convertToTexture(brightPass), vec2(1, 0), STREAK_SIGMA)) as unknown as Vec4Node;

  return Fn(() => {
    const coord = screenUv.toVar('opticsUv');
    const centered = coord.sub(0.5).toVar('opticsCentered');
    const r2 = dot(centered, centered).toVar('opticsR2');

    const caStrength = float(0.0018).add(warp.mul(0.018));
    const caOffset = centered.mul(r2).mul(caStrength).toVar('opticsCaOffset');
    const base = vec3(
      source.sample(coord.add(caOffset)).r,
      source.sample(coord).g,
      source.sample(coord.sub(caOffset)).b
    ).toVar('opticsBase');

    const anamorphicTint = streak.rgb.mul(STREAK_ENERGY).mul(vec3(0.32, 0.58, 1.0)).mul(0.085);

    // GLSL smoothstep(0.92, 0.18, r2) with reversed edges; WGSL leaves that undefined, and
    // 1 - smoothstep(0.18, 0.92, r2) is the same curve
    const vignette = float(1).sub(smoothstep(0.18, 0.92, r2));
    return vec4(base.add(anamorphicTint).mul(mix(0.76, 1.0, vignette)), 1.0);
  })() as unknown as Vec4Node;
}
