import * as THREE from 'three';
import { PackedSplats, SplatMesh } from '@sparkjsdev/spark';
import { BRUNETON_ATMO_GLSL_CHUNK } from '../volumetricSplatsAndLut.js';
import type { EarthUniforms, MoonUniforms } from './cislunarRegime.js';

export type PlanetaryTerrainPalette = 'lunar_regolith' | 'exoplanet_terminator' | 'titan_methane';

export interface PlanetarySkyUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uTime: { value: number };
  uAtmoZenith: { value: THREE.Vector3 };
  uAtmoHorizon: { value: THREE.Vector3 };
  uSunColor: { value: THREE.Vector3 };
  uSunAngularRadius: { value: number };
  uHasRings: { value: number };
  uPaletteId: { value: number };
  uBrunetonLutTex: { value: THREE.Texture | null };
}

export function createPlanetarySkyUniforms(): PlanetarySkyUniforms {
  return {
    uTime: { value: 0.0 },
    uAtmoZenith: { value: new THREE.Vector3(0.01, 0.03, 0.08) },
    uAtmoHorizon: { value: new THREE.Vector3(0.78, 0.28, 0.12) },
    uSunColor: { value: new THREE.Vector3(1.25, 0.52, 0.22) },
    uSunAngularRadius: { value: 0.095 },
    uHasRings: { value: 0.0 },
    uPaletteId: { value: 1.0 },
    uBrunetonLutTex: { value: null },
  };
}

export function createPlanetaryTerrainSplatMesh(
  palette: PlanetaryTerrainPalette,
  planetarySkyUniforms: PlanetarySkyUniforms
): SplatMesh {
  if (palette === 'lunar_regolith') {
    planetarySkyUniforms.uAtmoZenith.value.set(0.001, 0.002, 0.006);
    planetarySkyUniforms.uAtmoHorizon.value.set(0.05, 0.11, 0.22);
    planetarySkyUniforms.uSunColor.value.set(0.95, 0.98, 1.05);
    planetarySkyUniforms.uSunAngularRadius.value = 0.085;
    planetarySkyUniforms.uHasRings.value = 0.0;
    planetarySkyUniforms.uPaletteId.value = 0.0;
  } else if (palette === 'titan_methane') {
    planetarySkyUniforms.uAtmoZenith.value.set(0.05, 0.03, 0.012);
    planetarySkyUniforms.uAtmoHorizon.value.set(0.84, 0.48, 0.16);
    planetarySkyUniforms.uSunColor.value.set(1.12, 0.80, 0.42);
    planetarySkyUniforms.uSunAngularRadius.value = 0.11;
    planetarySkyUniforms.uHasRings.value = 1.0;
    planetarySkyUniforms.uPaletteId.value = 2.0;
  } else {
    // exoplanet_terminator (glacial canyon world)
    planetarySkyUniforms.uAtmoZenith.value.set(0.008, 0.018, 0.052);
    planetarySkyUniforms.uAtmoHorizon.value.set(0.48, 0.18, 0.10);
    planetarySkyUniforms.uSunColor.value.set(1.18, 0.58, 0.36);
    planetarySkyUniforms.uSunAngularRadius.value = 0.105;
    planetarySkyUniforms.uHasRings.value = 0.0;
    planetarySkyUniforms.uPaletteId.value = 1.0;
  }

  return new SplatMesh({
    constructSplats: (splats: PackedSplats) => {
      const center = new THREE.Vector3();
      const scales = new THREE.Vector3();
      const quat = new THREE.Quaternion();
      const color = new THREE.Color();

      const count = 1800;
      for (let i = 0; i < count; i++) {
        const seed1 = Math.sin(i * 12.9898) * 43758.5453;
        const seed2 = Math.sin(i * 78.233) * 43758.5453;
        const u = seed1 - Math.floor(seed1) - 0.5;
        const v = seed2 - Math.floor(seed2);
        const z = -12.0 - v * 26.0;
        const x = u * (24.0 + v * 34.0);
        const y = -0.8 + Math.sin(x * 0.25 + z * 0.2) * 0.8 + (i % 7) * 0.35;
        center.set(x, y, z);

        const s = 0.012 + (i % 4) * 0.003;
        scales.set(s * 1.5, s * 0.8, s * 1.5);

        if (palette === 'lunar_regolith') {
          color.setRGB(0.65, 0.78, 0.96);
        } else if (palette === 'titan_methane') {
          color.setRGB(0.95, 0.62, 0.24);
        } else {
          color.setRGB(0.92, 0.56, 0.42);
        }

        splats.pushSplat(center, scales, quat, 0.016, color);
      }
    },
  });
}

/**
 * REGIME 4 (`planetary_surface`):
 * Full-Viewport Analytical GPU Ray-Marched Planetary Worlds:
 * - Palette 0 (`lunar_regolith`): Ray-marched Shackleton Crater regolith & boulders + 3D Blue Marble Earth in sky
 * - Palette 1 (`exoplanet_terminator`): Ray-marched glacial canyons, slope-sorted ice & M-Dwarf + binary suns
 * - Palette 2 (`titan_methane`): Ray-marched coastal cliffs & specular liquid methane waves reflecting Ringed Saturn
 */
export function buildPlanetarySurfaceRegimeGroup(
  group: THREE.Group,
  planetarySkyUniforms: PlanetarySkyUniforms,
  earthUniforms: EarthUniforms,
  moonUniforms: MoonUniforms
): { parentBodyMesh: THREE.Mesh; terrainSplatMesh: SplatMesh } {
  const worldGeo = new THREE.SphereGeometry(85, 128, 96);
  const worldMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
    uniforms: {
      uTime: planetarySkyUniforms.uTime,
      uAtmoZenith: planetarySkyUniforms.uAtmoZenith,
      uAtmoHorizon: planetarySkyUniforms.uAtmoHorizon,
      uSunColor: planetarySkyUniforms.uSunColor,
      uPaletteId: planetarySkyUniforms.uPaletteId,
      uBrunetonLutTex: planetarySkyUniforms.uBrunetonLutTex,
      uEarthDayTex: earthUniforms.uDayTex,
      uEarthNightTex: earthUniforms.uNightTex,
      uEarthCloudsTex: earthUniforms.uCloudsTex,
      uEarthSpecTex: earthUniforms.uSpecTex,
      uMoonAlbedoTex: moonUniforms.uAlbedoTex,
    },
    vertexShader: /* glsl */ `
      varying vec3 vWorldDir;
      void main() {
        vWorldDir = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform vec3 uAtmoZenith;
      uniform vec3 uAtmoHorizon;
      uniform vec3 uSunColor;
      uniform float uPaletteId;
      uniform sampler2D uBrunetonLutTex;
      uniform sampler2D uEarthDayTex;
      uniform sampler2D uEarthNightTex;
      uniform sampler2D uEarthCloudsTex;
      uniform sampler2D uEarthSpecTex;
      uniform sampler2D uMoonAlbedoTex;
      varying vec3 vWorldDir;

      ${BRUNETON_ATMO_GLSL_CHUNK}

      // High-precision procedural hash & analytical-derivative multi-fractal noise
      float hash2(vec2 p) {
        p = fract(p * vec2(127.1, 311.7));
        p += dot(p, p + 34.5);
        return fract(p.x * p.y);
      }

      // Returns vec3(value, df/dx, df/dz)
      vec3 noised(vec2 x) {
        vec2 i = floor(x);
        vec2 f = fract(x);
        vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
        vec2 du = 30.0 * f * f * (f * (f - 2.0) + 1.0);

        float a = hash2(i + vec2(0.0, 0.0));
        float b = hash2(i + vec2(1.0, 0.0));
        float c = hash2(i + vec2(0.0, 1.0));
        float d = hash2(i + vec2(1.0, 1.0));

        float k0 = a;
        float k1 = b - a;
        float k2 = c - a;
        float k3 = a - b - c + d;

        return vec3(
          k0 + k1 * u.x + k2 * u.y + k3 * u.x * u.y,
          du * (vec2(k1, k2) + k3 * u.yx)
        );
      }

      const mat2 m2 = mat2(0.80, -0.60, 0.60, 0.80);

      // Multi-octave ridged & eroded canyon/crater heightfield
      float terrainHeight(vec2 p, out vec2 grad) {
        vec2 pos = p * 0.085;
        float a = 0.0;
        float b = 1.0;
        vec2 d = vec2(0.0);

        float dist = length(p);
        float valleyChannel = smoothstep(2.2, 11.5, abs(p.x + sin(p.y * 0.08) * 4.5));
        float bankSmooth = smoothstep(0.0, 0.42, valleyChannel);

        for (int i = 0; i < 8; i++) {
          vec3 n = noised(pos);
          d += b * n.yz;
          // Slope-dependent erosion (flattens valleys, sharpens alpine/crater ridges)
          float ridge = 1.0 - abs(n.x * 2.0 - 1.0);
          ridge = ridge * ridge;
          // Smooth alluvial river banks: suppress high-frequency micro-bumps near the shoreline so no jagged noise islands appear
          float octaveMask = (i >= 3) ? mix(0.12, 1.0, bankSmooth) : 1.0;
          a += b * octaveMask * mix(n.x, ridge, 0.52) / (1.0 + dot(d, d) * 0.85);
          b *= 0.49;
          pos = m2 * pos * 2.04;
        }

        // Distant monumental mountain range / crater wall framing the horizon
        float horizonWall = smoothstep(12.0, 58.0, dist) * (1.8 + 1.4 * valleyChannel);

        if (uPaletteId < 0.5) {
          // 0: Lunar Shackleton Crater Rim & Bowl
          float cDist = length(p - vec2(-2.0, -24.0));
          float craterRim = exp(-pow((cDist - 12.5) / 3.8, 2.0)) * 3.8;
          float craterBowl = cDist < 12.0 ? -1.8 * (1.0 - cDist / 12.0) : 0.0;
          grad = d * 0.65;
          return -2.4 + a * 2.4 + craterRim + craterBowl + horizonWall * 0.75;
        }

        grad = d;
        float riverBed = -2.48 + 0.55 * valleyChannel;
        return riverBed + a * (2.65 + horizonWall * 1.55) * (0.08 + 0.92 * valleyChannel);
      }

      // Evaluates the sky & primary celestial body along any ray direction rd (used for both direct sky and ocean reflections!)
      vec3 evaluateCelestialSky(vec3 rd, bool isReflection) {
        float elev = clamp(rd.y, -0.15, 1.0);
        float horizonGlow = exp(-abs(rd.y - 0.01) * 6.5);
        vec3 primaryDir = normalize(vec3(-0.18, 0.22, -0.958));
        vec4 brunetonSky = sampleBrunetonAtmoLut(uBrunetonLutTex, elev, dot(rd, primaryDir) * 0.38);
        vec3 baseSky = mix(uAtmoHorizon * 0.78, uAtmoZenith, smoothstep(-0.02, 0.52, elev));
        baseSky += uAtmoHorizon * horizonGlow * 0.65;
        vec3 sky = (uPaletteId < 0.5)
          ? baseSky
          : mix(baseSky, baseSky * 0.72 + brunetonSky.rgb * uAtmoHorizon * 0.85, 0.42);

        if (uPaletteId < 0.5) {
          // PALETTE 0: Lunar South Pole Sky -> Photorealistic 3D Ray-Traced Blue Marble Earth in the Vacuum Sky
          float b = dot(rd, primaryDir);
          float rEarth = 0.145;
          float disc = b * b - (1.0 - rEarth * rEarth);
          float limbGlow = exp(-max(0.0, acos(clamp(b, -1.0, 1.0)) - rEarth * 0.92) * 32.0);
          sky += vec3(0.18, 0.48, 0.96) * limbGlow * 0.55;

          if (disc > 0.0 && b > 0.0) {
            float tHit = b - sqrt(disc);
            vec3 hitPos = rd * tHit - primaryDir;
            vec3 N = normalize(hitPos);
            vec2 uv = vec2(atan(N.x, N.z) / 6.28318 + 0.5 + uTime * 0.0015, asin(clamp(N.y, -1.0, 1.0)) / 3.14159 + 0.5);

            vec3 daySample = texture2D(uEarthDayTex, uv).rgb;
            vec3 dayCol = pow(daySample, vec3(1.15));
            vec3 nightCol = texture2D(uEarthNightTex, uv).rgb;
            vec3 clouds = texture2D(uEarthCloudsTex, uv + vec2(uTime * 0.002, 0.0)).rgb;
            float waterMask = texture2D(uEarthSpecTex, uv).r;
            float specMask = waterMask * (1.0 - smoothstep(0.15, 0.35, min(daySample.r, daySample.g)));

            vec3 sunL = normalize(vec3(0.78, 0.32, 0.54));
            float ndl = dot(N, sunL);
            float dayFactor = smoothstep(-0.12, 0.22, ndl);
            float twilight = exp(-pow(ndl * 4.5, 2.0)) * 0.55;
            vec3 H = normalize(sunL - rd);
            float oceanSpec = pow(max(0.0, dot(N, H)), 48.0) * specMask * max(0.0, ndl);

            vec3 lit = dayCol * max(0.04, ndl * 1.2) + vec3(0.45, 0.72, 1.0) * oceanSpec * 0.75;
            lit = mix(lit, vec3(0.96, 0.98, 1.0) * max(0.1, ndl * 1.25), clouds.r * 0.74);
            vec3 cities = nightCol * vec3(1.3, 0.9, 0.45) * (1.0 - smoothstep(-0.18, 0.06, ndl)) * 1.35;
            vec3 earthSurf = mix(cities, lit, dayFactor) + vec3(0.95, 0.42, 0.16) * twilight;
            float mu = clamp(dot(N, -rd), 0.0, 1.0);
            earthSurf += vec3(0.24, 0.62, 1.0) * pow(1.0 - mu, 2.6) * (0.35 + 0.65 * dayFactor);
            return earthSurf;
          }
          return sky;
        }

        if (uPaletteId < 1.5) {
          // PALETTE 1: Proxima Centauri b Sky -> M5.5V Red Dwarf Sun + Coronal Loops + Alpha Centauri A/B Binary Pair
          float cosAng = dot(rd, primaryDir);
          float ang = acos(clamp(cosAng, -1.0, 1.0));
          float rStar = 0.105;
          float corona = exp(-max(0.0, ang - rStar * 0.75) * 14.0);
          float godRays = (0.65 + 0.35 * sin(atan(rd.y - primaryDir.y, rd.x - primaryDir.x) * 14.0 + uTime * 0.2)) * exp(-ang * 5.5);
          sky += vec3(1.25, 0.38, 0.12) * corona * 0.68 + vec3(1.05, 0.45, 0.18) * godRays * 0.28;

          if (ang < rStar) {
            float mu = sqrt(max(0.0, 1.0 - pow(ang / rStar, 2.0)));
            vec2 granUv = (rd.xy - primaryDir.xy) * 32.0;
            float gran = noised(granUv + vec2(uTime * 0.15, -uTime * 0.1)).x;
            vec3 photosphere = mix(vec3(0.55, 0.06, 0.01), vec3(1.45, 0.48, 0.12), gran * 0.55 + mu * 0.45);
            photosphere = mix(photosphere, vec3(1.75, 1.15, 0.55), pow(mu, 1.6) * 0.65);
            return photosphere * (0.42 + 0.58 * pow(mu, 0.45));
          }

          // Alpha Centauri A & B bright double star gleaming in the upper right twilight sky
          vec3 cenA = normalize(vec3(0.28, 0.34, -0.897));
          vec3 cenB = normalize(vec3(0.305, 0.332, -0.892));
          float dA = acos(clamp(dot(rd, cenA), -1.0, 1.0));
          float dB = acos(clamp(dot(rd, cenB), -1.0, 1.0));
          sky += vec3(1.4, 1.32, 1.05) * exp(-dA * 180.0) * 1.4 + vec3(1.35, 0.92, 0.55) * exp(-dB * 210.0) * 1.1;
          return sky;
        }

        if (uPaletteId < 2.5) {
          // PALETTE 2: Titan Sky -> Giant Ringed Saturn & Cassini Division Arching Across the Sky
          vec3 satCenter = normalize(vec3(-0.14, 0.25, -0.958));
          float rSat = 0.135;
          float b = dot(rd, satCenter);
          float disc = b * b - (1.0 - rSat * rSat);

          // Ring plane normal tilted at 26.7 degrees
          vec3 ringN = normalize(vec3(0.32, 0.84, 0.44));
          float denom = dot(rd, ringN);
          vec3 ringCol = vec3(0.0);
          float ringAlpha = 0.0;
          float tRing = 1e5;
          if (abs(denom) > 0.001) {
            tRing = dot(satCenter, ringN) / denom;
            if (tRing > 0.0) {
              vec3 rHit = rd * tRing - satCenter;
              float rDist = length(rHit) / rSat;
              if (isReflection) {
                float softBand = exp(-pow((rDist - 1.86) / 0.52, 2.0));
                ringCol = vec3(0.95, 0.78, 0.48) * softBand;
                ringAlpha = softBand * 0.48;
              } else if (rDist > 1.28 && rDist < 2.45) {
                float cassini = smoothstep(0.015, 0.045, abs(rDist - 1.95));
                float encke = smoothstep(0.008, 0.022, abs(rDist - 2.26));
                float ringlets = 0.62 + 0.38 * sin(rDist * 140.0 + sin(rDist * 38.0) * 2.0);
                // Planet shadow on back of rings
                vec3 sunDir = normalize(vec3(0.68, 0.28, 0.67));
                float inShadow = (dot(rHit, sunDir) < 0.0 && length(cross(rHit, sunDir)) < rSat * 0.96) ? 0.12 : 1.0;
                ringCol = mix(vec3(0.68, 0.56, 0.38), vec3(1.08, 0.92, 0.68), ringlets) * cassini * encke * inShadow;
                ringAlpha = clamp(ringlets * cassini * encke * 0.88, 0.0, 0.92);
              }
            }
          }

          if (isReflection) {
            float angSat = acos(clamp(b, -1.0, 1.0));
            float satGlow = exp(-max(0.0, angSat - rSat * 0.4) * 12.0);
            vec3 reflSat = mix(sky, vec3(0.96, 0.78, 0.46), clamp(satGlow * 0.65 + ringAlpha * 0.55, 0.0, 0.85));
            return reflSat;
          }

          if (disc > 0.0 && b > 0.0) {
            float tSphere = b - sqrt(disc);
            vec3 hitPos = rd * tSphere - satCenter;
            vec3 N = normalize(hitPos);
            vec3 sunDir = normalize(vec3(0.68, 0.28, 0.67));
            float ndl = max(0.06, dot(N, sunDir));
            float lat = dot(N, ringN);
            float belts = 0.5 + 0.5 * sin(lat * 28.0 + sin(lat * 9.0) * 1.5);
            vec3 satSurf = mix(vec3(0.72, 0.58, 0.36), vec3(1.02, 0.86, 0.58), belts) * ndl * 1.25;
            if (ringAlpha > 0.0 && tRing < tSphere) {
              satSurf = mix(satSurf, ringCol, ringAlpha);
            }
            return mix(sky, satSurf, 0.92);
          }
          if (ringAlpha > 0.0) {
            return mix(sky, ringCol, ringAlpha * 0.88);
          }
          return sky;
        }

        return sky;
      }

      void main() {
        vec3 rd = normalize(vWorldDir);
        vec3 skyCol = evaluateCelestialSky(rd, false);

        // Above the maximum mountain elevation, return pure sky
        if (rd.y > 0.22) {
          float zenithAlpha = clamp(1.0 - smoothstep(0.08, 0.58, rd.y) * 0.72, 0.22, 0.96);
          gl_FragColor = vec4(skyCol, zenithAlpha);
          return;
        }

        // Ray-march the planetary surface & ocean from camera origin ro = (0, 0.35, 0)
        vec3 ro = vec3(0.0, 0.35, 0.0);
        float t = 1.6;
        float prevT = 1.6;
        float prevDiff = 0.35;
        float tMax = 68.0;
        bool hit = false;
        vec2 hitGrad = vec2(0.0);
        float h = 0.0;

        for (int i = 0; i < 96; i++) {
          vec3 pos = ro + rd * t;
          h = terrainHeight(pos.xz, hitGrad);
          float diff = pos.y - h;
          if (diff <= 0.0) {
            // Exact secant root interpolation + 2-step bisection for zero step-banding
            float frac = clamp(prevDiff / max(1e-5, prevDiff - diff), 0.0, 1.0);
            float tSec = mix(prevT, t, frac);
            vec3 midPos = ro + rd * tSec;
            float midH = terrainHeight(midPos.xz, hitGrad);
            if (midPos.y - midH <= 0.0) {
              t = mix(prevT, tSec, 0.5);
            } else {
              t = mix(tSec, t, 0.5);
            }
            hit = true;
            break;
          }
          if (t > tMax) break;
          prevT = t;
          prevDiff = diff;
          t += max(0.07, diff * 0.40);
        }

        float waterLevel = uPaletteId > 1.5 ? -1.88 : (uPaletteId > 0.5 ? -1.94 : -99.0);
        float tWater = (uPaletteId > 0.5 && rd.y < -0.001) ? (waterLevel - ro.y) / rd.y : 1e5;
        vec3 wPos = ro + rd * tWater;
        vec2 wGrad = vec2(0.0);
        float bedH = (tWater > 1.2 && tWater < tMax) ? terrainHeight(wPos.xz, wGrad) : 99.0;
        float waterDepth = waterLevel - bedH;
        bool hitWater = (tWater > 1.2 && tWater < (hit ? t : tMax) && waterDepth > 0.0);

        if (!hit && !hitWater) {
          gl_FragColor = vec4(skyCol, 0.94);
          return;
        }

        vec3 sunL = normalize(vec3(-0.18, 0.26, -0.948));
        vec3 V = -rd;
        vec3 surfCol = skyCol;
        float finalT = hit ? t : tWater;

        if (hit) {
          vec3 pos = ro + rd * t;
          vec2 g1, g2;
          float eps = 0.06;
          h = terrainHeight(pos.xz, hitGrad);
          float hx = terrainHeight(pos.xz + vec2(eps, 0.0), g1);
          float hz = terrainHeight(pos.xz + vec2(0.0, eps), g2);
          vec3 N = normalize(vec3(h - hx, eps, h - hz));

          vec3 microN = noised(pos.xz * 3.8);
          N = normalize(N + vec3(microN.y, 0.0, microN.z) * 0.15);

          float ndl = max(0.0, dot(N, sunL));
          float ndv = max(0.0, dot(N, V));
          float lommel = ndl / max(0.14, ndl + ndv);
          vec3 H = normalize(sunL + V);
          float spec = pow(max(0.0, dot(N, H)), 38.0);

          float strata = 0.85 + 0.15 * sin(pos.y * 5.2 + microN.x * 6.0);
          float slope = clamp(N.y, 0.0, 1.0);

          if (uPaletteId < 0.5) {
            vec3 regolith = vec3(0.52, 0.55, 0.60) * strata * (0.72 + 0.28 * microN.x);
            vec3 earthshine = vec3(0.04, 0.08, 0.16) * (0.35 + 0.65 * slope);
            surfCol = regolith * (lommel * 1.55) + earthshine + vec3(0.85, 0.92, 1.0) * spec * 0.18;
          } else if (uPaletteId < 1.5) {
            float snowMask = smoothstep(0.56, 0.78, slope + (microN.x - 0.5) * 0.22);
            vec3 basaltRock = vec3(0.12, 0.08, 0.08) * strata;
            vec3 glacierIce = vec3(0.64, 0.83, 0.98) * (0.84 + 0.16 * microN.x);
            vec3 albedo = mix(basaltRock, glacierIce, snowMask);
            vec3 skyAmbient = vec3(0.08, 0.05, 0.07) * (0.35 + 0.65 * slope);
            surfCol = albedo * uSunColor * (lommel * 1.55) + skyAmbient + vec3(0.80, 0.92, 1.0) * spec * (0.10 + snowMask * 0.72);
          } else {
            float duneMask = smoothstep(0.60, 0.84, slope);
            vec3 cliffRock = vec3(0.20, 0.12, 0.07) * strata;
            vec3 tholinSand = vec3(0.66, 0.42, 0.17) * (0.82 + 0.18 * microN.x);
            vec3 albedo = mix(cliffRock, tholinSand, duneMask);
            surfCol = albedo * uSunColor * (lommel * 1.40 + 0.08) + uSunColor * spec * 0.24;
          }
        }

        if (hitWater) {
          // Exact Analytical Liquid Plane with 100% Continuous Analytical Water Depth & Fresnel Reflections
          vec2 waveUv = wPos.xz * 1.55 + vec2(uTime * 0.35, -uTime * 0.22);
          vec3 wn1 = noised(waveUv);
          vec3 wn2 = noised(waveUv * 2.6 - vec2(uTime * 0.48, uTime * 0.31));
          vec3 waterN = normalize(vec3(
            (wn1.y + wn2.y * 0.45) * 0.028,
            1.0,
            (wn1.z + wn2.z * 0.45) * 0.028
          ));

          vec3 reflDir = reflect(rd, waterN);
          reflDir.y = max(0.02, reflDir.y);
          vec3 reflSky = evaluateCelestialSky(normalize(reflDir), true);

          float cosTheta = clamp(dot(waterN, V), 0.0, 1.0);
          float fresnel = 0.08 + 0.92 * pow(1.0 - cosTheta, 4.0);

          vec3 deepLiquid = uPaletteId > 1.5 ? vec3(0.028, 0.036, 0.024) : vec3(0.018, 0.042, 0.068);
          vec3 waterCol = mix(deepLiquid, reflSky * 1.16, fresnel);

          float shoreBlend = smoothstep(0.0, 0.22, waterDepth);
          float shoreFoam = exp(-waterDepth * 14.0) * smoothstep(0.42, 0.88, wn1.x) * 0.18;
          surfCol = mix(surfCol * 0.72, waterCol + uSunColor * shoreFoam, shoreBlend);
          finalT = tWater;
        }

        // Physically based exponential distance haze & subtle canyon mist
        float mistDensity = uPaletteId < 0.5 ? 0.006 : 0.013;
        float fogFactor = 1.0 - exp(-finalT * mistDensity);
        vec3 finalPos = ro + rd * finalT;
        float valleyMist = uPaletteId < 0.5 ? 0.0 : exp(-max(0.0, finalPos.y + 2.15) * 2.2) * smoothstep(10.0, 42.0, finalT) * 0.20;
        vec3 finalCol = mix(surfCol, skyCol, clamp(fogFactor + valleyMist, 0.0, 0.88));

        gl_FragColor = vec4(finalCol, 0.99);
      }
    `,
  });
  const worldDome = new THREE.Mesh(worldGeo, worldMat);
  group.add(worldDome);

  // Invisible anchor mesh for camera telemetry projection
  const anchorGeo = new THREE.SphereGeometry(0.1, 8, 8);
  const anchorMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.0 });
  const parentBodyMesh = new THREE.Mesh(anchorGeo, anchorMat);
  parentBodyMesh.position.set(-3.6, 4.2, -19.0);
  group.add(parentBodyMesh);

  const terrainSplatMesh = createPlanetaryTerrainSplatMesh('exoplanet_terminator', planetarySkyUniforms);
  group.add(terrainSplatMesh);

  return { parentBodyMesh, terrainSplatMesh };
}
