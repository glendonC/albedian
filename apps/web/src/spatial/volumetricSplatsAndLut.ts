export const BRUNETON_ATMO_LUT_URL = '/textures/bruneton_atmo_lut.png';

/**
 * Reusable GLSL chunk providing `vec4 sampleBrunetonAtmoLut(sampler2D lutTex, float viewMu, float sunMu)`
 * for physical Bruneton & Neyret (2008) Rayleigh + Cornette-Shanks Mie + Chappuis Ozone limb and
 * horizon scattering in `buildCislunarWorld`.
 *
 * - `viewMu` = cos(theta_v) in [-0.35, 1.0], mapped with signed-sqrt horizon concentration onto `u in [0, 1]`.
 * - `sunMu` = cos(theta_s) in [-1.0, 1.0], mapped onto `v = sunMu * 0.5 + 0.5 in [0, 1]`.
 * - `rgb`: pre-integrated Rayleigh/Mie/Ozone limb & sky radiance.
 * - `a`: normalized optical depth transmittance.
 */
export const BRUNETON_ATMO_GLSL_CHUNK = /* glsl */ `
vec2 brunetonAtmoLutUv(float viewMu, float sunMu) {
  float clampedView = clamp(viewMu, -0.35, 1.0);
  float s = sign(clampedView) * sqrt(abs(clampedView));
  float u = clamp((s + 0.59160798) / 1.59160798, 0.004, 0.996);
  float v = clamp(sunMu * 0.5 + 0.5, 0.008, 0.992);
  return vec2(u, v);
}

vec4 sampleBrunetonAtmoLut(sampler2D lutTex, float viewMu, float sunMu) {
  vec2 lutUv = brunetonAtmoLutUv(viewMu, sunMu);
  vec4 lutSample = texture2D(lutTex, lutUv);

  // Seamless physical analytical fallback while the async PNG texture is streaming
  float isLoaded = step(0.001, dot(lutSample.rgb, vec3(1.0)) + lutSample.a);
  float horizonPeak = exp(-pow((viewMu + 0.02) / 0.22, 2.0));
  float terminator = exp(-pow((sunMu + 0.02) / 0.24, 2.0));
  float day = smoothstep(-0.22, 0.45, sunMu);
  vec3 zenithCol = mix(vec3(0.022, 0.068, 0.240), vec3(0.075, 0.285, 0.820), day);
  vec3 limbCol = mix(
    vec3(0.96, 0.36, 0.10),
    vec3(0.22, 0.62, 1.0),
    clamp(day - terminator * 0.55, 0.0, 1.0)
  );
  vec3 fallbackRgb = mix(zenithCol, limbCol, horizonPeak) * (0.24 + 0.76 * smoothstep(-0.35, 0.30, sunMu));
  float fallbackAlpha = clamp(0.18 + 0.80 * clamp((viewMu + 0.35) / 1.35, 0.0, 1.0), 0.18, 0.98);
  return mix(vec4(fallbackRgb, fallbackAlpha), lutSample, isLoaded);
}
`;
