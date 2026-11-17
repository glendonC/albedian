import { BRUNETON_ATMO_GLSL_CHUNK } from '../../volumetricSplatsAndLut.js';
import { PLANETARY_CELESTIAL_SKY_GLSL } from './planetaryCelestialSkyGlsl.js';
import { PLANETARY_NOISE_AND_HEIGHT_GLSL } from './planetaryNoiseAndHeightGlsl.js';

export const PLANETARY_SURFACE_VERTEX_SHADER = /* glsl */ `
  varying vec3 vWorldDir;
  void main() {
    vWorldDir = normalize(position);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export function buildPlanetarySurfaceFragmentShader(): string {
  return /* glsl */ `
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
    varying vec3 vWorldDir;

    ${BRUNETON_ATMO_GLSL_CHUNK}
    ${PLANETARY_NOISE_AND_HEIGHT_GLSL}
    ${PLANETARY_CELESTIAL_SKY_GLSL}

    void main() {
      vec3 rd = normalize(vWorldDir);
      vec3 skyCol = evaluateCelestialSky(rd, false);

      if (rd.y > 0.22) {
        float zenithAlpha = clamp(1.0 - smoothstep(0.08, 0.58, rd.y) * 0.72, 0.22, 0.96);
        gl_FragColor = vec4(skyCol, zenithAlpha);
        return;
      }

      vec3 ro = vec3(0.0, 0.35, 0.0);
      float t = 1.6;
      float prevT = 1.6;
      float prevDiff = 0.35;
      float tMax = 68.0;
      bool hit = false;
      float h = 0.0;

      for (int i = 0; i < 96; i++) {
        vec3 pos = ro + rd * t;
        h = terrainHeight(pos.xz);
        float diff = pos.y - h;
        if (diff <= 0.0) {
          float frac = clamp(prevDiff / max(1e-5, prevDiff - diff), 0.0, 1.0);
          float tSec = mix(prevT, t, frac);
          vec3 midPos = ro + rd * tSec;
          float midH = terrainHeight(midPos.xz);
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
      float bedH = (tWater > 1.2 && tWater < tMax) ? terrainHeight(wPos.xz) : 99.0;
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
        float eps = 0.06;
        h = terrainHeight(pos.xz);
        float hx = terrainHeight(pos.xz + vec2(eps, 0.0));
        float hz = terrainHeight(pos.xz + vec2(0.0, eps));
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
        // Analytical liquid plane intersection with procedural wave normals and Fresnel blend
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

      // Exponential distance haze and subtle valley mist
      float mistDensity = uPaletteId < 0.5 ? 0.006 : 0.013;
      float fogFactor = 1.0 - exp(-finalT * mistDensity);
      vec3 finalPos = ro + rd * finalT;
      float valleyMist = uPaletteId < 0.5 ? 0.0 : exp(-max(0.0, finalPos.y + 2.15) * 2.2) * smoothstep(10.0, 42.0, finalT) * 0.20;
      vec3 finalCol = mix(surfCol, skyCol, clamp(fogFactor + valleyMist, 0.0, 0.88));

      gl_FragColor = vec4(finalCol, 0.99);
    }
  `;
}
