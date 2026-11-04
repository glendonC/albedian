import type { Node, Texture } from 'three/webgpu';
import { Fn, abs, clamp, exp, mix, sign, smoothstep, sqrt, texture, vec2, vec3, vec4 } from 'three/tsl';

type FloatNode = Node<'float'>;
type Vec4Node = Node<'vec4'>;

/**
 * TSL port of `BRUNETON_ATMO_GLSL_CHUNK` (volumetricSplatsAndLut.ts), shared by the Earth and
 * its atmosphere shell.
 *
 * The LUT (`/textures/bruneton_atmo_lut.png`, 256x128 RGBA8) is baked by
 * scripts/bake_bruneton_lut_and_splats.py: single scattering (Rayleigh, Cornette-Shanks Mie,
 * ozone) along 32 steps plus hand-set zenith and limb colour terms, then tone-mapped into 8 bits
 * with 1 - exp(-1.18 x). It is a modeled appearance, not a precomputed Bruneton and Neyret
 * (2008) radiance table. u encodes the view zenith cosine on a signed square root over
 * [-0.35, 1], v the sun zenith cosine.
 */

/** The same LUT file the WebGL chunk loads (volumetricSplatsAndLut.ts), kept free of its Spark import. */
export const BRUNETON_ATMO_LUT_URL = '/textures/bruneton_atmo_lut.png';

export const brunetonAtmoLutUv = Fn(([viewMu, sunMu]: [FloatNode, FloatNode]) => {
  const clampedView = clamp(viewMu, -0.35, 1.0);
  const s = sign(clampedView).mul(sqrt(abs(clampedView)));
  const u = clamp(s.add(0.59160798).div(1.59160798), 0.004, 0.996);
  const v = clamp(sunMu.mul(0.5).add(0.5), 0.008, 0.992);
  return vec2(u, v);
});

/** exp(-x^2), squared explicitly: GLSL and WGSL pow() are undefined or NaN for a negative base. */
export const gaussian = Fn(([x]: [FloatNode]) => exp(x.mul(x).negate()));

/** Analytic stand-in the GLSL chunk blends to while the LUT image is still loading. */
const brunetonLoadingFallback = Fn(([viewMu, sunMu]: [FloatNode, FloatNode]) => {
  const horizonPeak = gaussian(viewMu.add(0.02).div(0.22));
  const terminator = gaussian(sunMu.add(0.02).div(0.24));
  const day = smoothstep(-0.22, 0.45, sunMu);
  const zenithCol = mix(vec3(0.022, 0.068, 0.24), vec3(0.075, 0.285, 0.82), day);
  const limbCol = mix(vec3(0.96, 0.36, 0.1), vec3(0.22, 0.62, 1.0), clamp(day.sub(terminator.mul(0.55)), 0.0, 1.0));
  const rgb = mix(zenithCol, limbCol, horizonPeak).mul(smoothstep(-0.35, 0.3, sunMu).mul(0.76).add(0.24));
  const alpha = clamp(clamp(viewMu.add(0.35).div(1.35), 0.0, 1.0).mul(0.8).add(0.18), 0.18, 0.98);
  return vec4(rgb, alpha);
});

export interface BrunetonLut {
  /** `sample(viewMu, sunMu)`: LUT value, or the analytic stand-in until the image has loaded. */
  sample(viewMu: FloatNode, sunMu: FloatNode): Vec4Node;
}

/**
 * Binds the LUT texture once. `ready` is a uniform set to 1 when the image has loaded; the GLSL
 * chunk inferred this from an all-zero texel, which a WebGPU placeholder texture does not give.
 */
export function createBrunetonLut(lutTexture: Texture, ready: { value: number }): BrunetonLut {
  const lut = texture(lutTexture);
  const readyNode = ready as unknown as FloatNode;
  const sampleFn = Fn(([viewMu, sunMu]: [FloatNode, FloatNode]) => {
    const lutSample = lut.sample(brunetonAtmoLutUv(viewMu, sunMu));
    return mix(brunetonLoadingFallback(viewMu, sunMu), lutSample, readyNode);
  });
  return {
    sample: (viewMu, sunMu) => sampleFn(viewMu, sunMu) as unknown as Vec4Node,
  };
}
