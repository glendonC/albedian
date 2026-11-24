import type { Node, TextureNode } from 'three/webgpu';
import { Fn, convertToTexture, dot, float, mix, smoothstep, uniform, uv, vec3, vec4 } from 'three/tsl';

type Vec4Node = Node<'vec4'>;

export interface CinemaOpticsUniforms {
  /** 0 at rest, up to 1 at the height of a regime flight; widens the chromatic aberration. */
  warpFactor: { value: number };
}

export function createCinemaOpticsUniforms(): CinemaOpticsUniforms {
  return { warpFactor: uniform(0) };
}

/**
 * Lens and sensor look of the landing scenes, ported from the GLSL `ShaderPass` in
 * SparkPortalRenderer.ts: radial chromatic aberration (red out, blue in) and a vignette that dims
 * the corners to 0.76. It runs on linear HDR, after bloom and before tone mapping, as the
 * `EffectComposer` chain did. All of it is modeled camera optics, not part of any data.
 *
 * No streak, as in WebGL (streak-ghosts-one): the port's 8 taps at fixed offsets drew stars as rows
 * of dots, and the continuous Gaussian that replaced them added under one code value at 1080p.
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

    // GLSL smoothstep(0.92, 0.18, r2) with reversed edges; WGSL leaves that undefined, and
    // 1 - smoothstep(0.18, 0.92, r2) is the same curve
    const vignette = float(1).sub(smoothstep(0.18, 0.92, r2));
    return vec4(base.mul(mix(0.76, 1.0, vignette)), 1.0);
  })() as unknown as Vec4Node;
}
