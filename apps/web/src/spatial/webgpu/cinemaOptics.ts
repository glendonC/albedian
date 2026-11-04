import type { Node, TextureNode } from 'three/webgpu';
import { Fn, convertToTexture, dot, float, mix, smoothstep, uniform, uv, vec2, vec3, vec4 } from 'three/tsl';

type Vec4Node = Node<'vec4'>;

export interface CinemaOpticsUniforms {
  /** 0 at rest, up to 1 at the height of a regime flight; widens the chromatic aberration. */
  warpFactor: { value: number };
}

export function createCinemaOpticsUniforms(): CinemaOpticsUniforms {
  return { warpFactor: uniform(0) };
}

const LUMA_REC601 = vec3(0.299, 0.587, 0.114);

/**
 * Lens and sensor look of the landing scenes, ported from the GLSL `ShaderPass` in
 * SparkPortalRenderer.ts: radial chromatic aberration (red out, blue in), an 8-tap horizontal
 * anamorphic streak from pixels brighter than 0.76 in linear luminance, and a vignette that
 * dims the corners to 0.76. It runs on linear HDR, after bloom and before tone mapping, as the
 * `EffectComposer` chain did. All of it is modeled camera optics, not part of any data.
 */
export function cinemaOptics(input: Node, uniforms: CinemaOpticsUniforms): Vec4Node {
  const source = convertToTexture(input) as unknown as TextureNode;
  const warp = uniforms.warpFactor as unknown as Node<'float'>;
  const screenUv = (source.uvNode ?? uv()) as Node<'vec2'>;

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

    const streak = vec3(0).toVar('opticsStreak');
    for (let i = -4; i <= 4; i++) {
      if (i === 0) continue;
      const tap = source.sample(coord.add(vec2(i * 0.018, 0))).rgb;
      const brightMask = smoothstep(0.76, 1.35, dot(tap, LUMA_REC601));
      streak.addAssign(tap.mul(brightMask).mul(Math.exp(-Math.abs(i) * 0.55)));
    }
    const anamorphicTint = streak.mul(vec3(0.32, 0.58, 1.0)).mul(0.085);

    // GLSL smoothstep(0.92, 0.18, r2) with reversed edges; WGSL leaves that undefined, and
    // 1 - smoothstep(0.18, 0.92, r2) is the same curve
    const vignette = float(1).sub(smoothstep(0.18, 0.92, r2));
    return vec4(base.add(anamorphicTint).mul(mix(0.76, 1.0, vignette)), 1.0);
  })() as unknown as Vec4Node;
}
