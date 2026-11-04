import * as THREE from 'three';
import { PackedSplats, SplatMesh } from '@sparkjsdev/spark';

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

/**
 * Builds an anisotropic 3D Gaussian Splat mesh (`SplatMesh`) for the Harvard CfA
 * O'Neill et al. (2024) Local Bubble Chimney (`lb_splats.json`).
 *
 * Reads the pre-baked 14-tuple `[x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity]`
 * when `pt.length >= 14`, with a graceful fallback if `pt.length >= 6`.
 */
export function buildAnisotropicLocalBubbleSplatMesh(raw: number[][]): SplatMesh {
  return new SplatMesh({
    constructSplats: (splats: PackedSplats) => {
      if (!Array.isArray(raw) || raw.length === 0) return;

      const center = new THREE.Vector3();
      const scales = new THREE.Vector3();
      const quat = new THREE.Quaternion();
      const color = new THREE.Color();

      const stride = raw.length > 6000 ? 2 : 1;
      const enforceInnerCavity = raw.length > 64;

      for (let i = 0; i < raw.length; i += stride) {
        const pt = raw[i];
        if (!pt || pt.length < 6) continue;

        const jx = (Math.sin(i * 12.9898) - 0.5) * 0.28;
        const jy = (Math.cos(i * 78.233) - 0.5) * 0.28;
        const jz = (Math.sin(i * 37.719) - 0.5) * 0.28;
        const x = pt[0]! * 1.24 + jx;
        const y = pt[1]! * 1.24 + jy;
        const z = pt[2]! * 1.24 + jz;

        // Preserve a clean hollow inner cavity around the central stellar photosphere
        const xyDist = Math.hypot(x, y);
        if (enforceInnerCavity && xyDist < 4.55) continue;

        center.set(x, y, z);

        if (pt.length >= 14) {
          color.setRGB(
            THREE.MathUtils.clamp(pt[3]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[4]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[5]!, 0.0, 1.0)
          );
          scales.set(
            Math.max(0.01, pt[6]!),
            Math.max(0.01, pt[7]!),
            Math.max(0.01, pt[8]!)
          );
          quat.set(pt[9]!, pt[10]!, pt[11]!, pt[12]!);
          if (quat.lengthSq() > 1e-6) {
            quat.normalize();
          } else {
            quat.identity();
          }
          const cavityFade = enforceInnerCavity
            ? Math.min(1.0, Math.max(0.25, (xyDist - 4.55) * 0.55))
            : 1.0;
          const opacity = THREE.MathUtils.clamp(pt[13]! * cavityFade, 0.003, 0.045);
          splats.pushSplat(center, scales, quat, opacity, color);
        } else {
          const s = 0.095 + (i % 5) * 0.022;
          scales.set(s * 1.5, s * 1.1, s * 0.65);
          quat.identity();

          const warmMix = (i % 9) / 9.0;
          color.setRGB(
            0.78 + 0.18 * warmMix,
            0.54 + 0.14 * warmMix,
            0.36 + 0.12 * (1.0 - warmMix)
          );
          const opacity = Math.min(0.016, Math.max(0.004, Math.max(0.1, xyDist - 4.55) * 0.0035));
          splats.pushSplat(center, scales, quat, opacity, color);
        }
      }
    },
  });
}

/**
 * Builds an anisotropic 3D Gaussian Splat mesh (`SplatMesh`) for the Multi-Wavelength
 * Volumetric Nebula (`nasa_nebula_splats.json`).
 *
 * Uses the pre-baked multi-scale anisotropic covariance (`sx, sy, sz`), shock/pillar-aligned
 * orientation quaternions (`qx, qy, qz, qw`), physical emission/extinction colors (`r, g, b`),
 * and volumetric `opacity` when `pt.length >= 14`, with graceful fallback if `pt.length >= 6`.
 */
export function buildAnisotropicNebulaSplatMesh(raw: number[][]): SplatMesh {
  return new SplatMesh({
    constructSplats: (splats: PackedSplats) => {
      if (!Array.isArray(raw) || raw.length === 0) return;

      const center = new THREE.Vector3();
      const scales = new THREE.Vector3();
      const quat = new THREE.Quaternion();
      const color = new THREE.Color();

      const stride = raw.length > 6000 ? 2 : 1;

      for (let i = 0; i < raw.length; i += stride) {
        const pt = raw[i];
        if (!pt || pt.length < 6) continue;

        const x = pt[0]! * 1.05;
        const y = pt[1]! * 1.05;
        const z = pt[2]! * 1.05;
        center.set(x, y, z);

        if (pt.length >= 14) {
          color.setRGB(
            THREE.MathUtils.clamp(pt[3]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[4]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[5]!, 0.0, 1.0)
          );
          scales.set(
            Math.max(0.005, pt[6]!),
            Math.max(0.005, pt[7]!),
            Math.max(0.005, pt[8]!)
          );
          quat.set(pt[9]!, pt[10]!, pt[11]!, pt[12]!);
          if (quat.lengthSq() > 1e-6) {
            quat.normalize();
          } else {
            quat.identity();
          }
          const opacity = THREE.MathUtils.clamp(pt[13]!, 0.006, 0.085);
          splats.pushSplat(center, scales, quat, opacity, color);
        } else {
          const s = 0.018 + (i % 7) * 0.0035;
          scales.set(s * 1.35, s * 0.95, s * 1.25);
          quat.identity();
          color.setRGB(
            THREE.MathUtils.clamp(pt[3]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[4]!, 0.0, 1.0),
            THREE.MathUtils.clamp(pt[5]!, 0.0, 1.0)
          );
          const radial = Math.min(1.0, Math.hypot(x, y, z) / 6.2);
          const opacity = (1.0 - radial * 0.55) * 0.036;
          splats.pushSplat(center, scales, quat, opacity, color);
        }
      }
    },
  });
}
