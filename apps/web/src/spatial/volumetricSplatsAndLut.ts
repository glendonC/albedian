import * as THREE from 'three';
import { PackedSplats, SplatMesh } from '@sparkjsdev/spark';
import { parseSplatRows, type SplatRows } from './splatRows.js';

export const BRUNETON_ATMO_LUT_URL = '/textures/bruneton_atmo_lut.png';

/**
 * Reusable GLSL chunk providing `vec4 sampleBrunetonAtmoLut(sampler2D lutTex, float viewMu, float sunMu)`
 * for physical Bruneton & Neyret (2008) Rayleigh + Cornette-Shanks Mie + Chappuis Ozone limb and
 * horizon scattering in `buildCislunarWorld` and `buildPlanetarySurfaceWorld`.
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

/** Scene scale of each splat set. It scales positions only; footprints stay as baked. */
const LOCAL_BUBBLE_POSITION_SCALE = 1.24;
const NEBULA_POSITION_SCALE = 1.05;

function splatMeshFromRows(rows: SplatRows): SplatMesh {
  return new SplatMesh({
    constructSplats: (splats: PackedSplats) => {
      const center = new THREE.Vector3();
      const scales = new THREE.Vector3();
      const quat = new THREE.Quaternion();
      const color = new THREE.Color();
      for (let i = 0; i < rows.count; i++) {
        center.fromArray(rows.centers, i * 3);
        scales.fromArray(rows.scales, i * 3);
        quat.fromArray(rows.quaternions, i * 4);
        color.fromArray(rows.colors, i * 3);
        splats.pushSplat(center, scales, quat, rows.opacities[i]!, color);
      }
    },
  });
}

/**
 * Builds the Local Bubble splat mesh from `lb_splats.json`, one splat per row at the row's own
 * position. The positions are vertices of `local_bubble_oneill2024.glb`, a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024); colour,
 * footprint, orientation and opacity are presentation values from the bake, since the mesh has no
 * dust density or emission per point.
 */
export function buildAnisotropicLocalBubbleSplatMesh(raw: unknown): SplatMesh {
  return splatMeshFromRows(parseSplatRows(raw, LOCAL_BUBBLE_POSITION_SCALE, 0.01));
}

/**
 * Builds the nebula splat mesh from `nasa_nebula_splats.json`, one splat per row at the row's
 * own position. The positions match the vertices of `nasa_pillars_3d.glb`; colour, footprint,
 * orientation and opacity are presentation values from the bake, not measured emission.
 */
export function buildAnisotropicNebulaSplatMesh(raw: unknown): SplatMesh {
  return splatMeshFromRows(parseSplatRows(raw, NEBULA_POSITION_SCALE, 0.005));
}
