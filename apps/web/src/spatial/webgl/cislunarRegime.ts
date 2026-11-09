import * as THREE from 'three';
import type { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { BRUNETON_ATMO_GLSL_CHUNK } from '../volumetricSplatsAndLut.js';

export interface EarthUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uDayTex: { value: THREE.Texture | null };
  uNightTex: { value: THREE.Texture | null };
  uNormalTex: { value: THREE.Texture | null };
  uSpecTex: { value: THREE.Texture | null };
  uCloudsTex: { value: THREE.Texture | null };
  uBrunetonLutTex: { value: THREE.Texture | null };
  uSunDir: { value: THREE.Vector3 };
  uTime: { value: number };
}

export interface MoonUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uAlbedoTex: { value: THREE.Texture | null };
  uDispTex: { value: THREE.Texture | null };
  uSunDir: { value: THREE.Vector3 };
}

export function createEarthUniforms(): EarthUniforms {
  return {
    uDayTex: { value: null },
    uNightTex: { value: null },
    uNormalTex: { value: null },
    uSpecTex: { value: null },
    uCloudsTex: { value: null },
    uBrunetonLutTex: { value: null },
    uSunDir: { value: new THREE.Vector3(0.68, 0.28, -0.58).normalize() },
    uTime: { value: 0.0 },
  };
}

export function createMoonUniforms(): MoonUniforms {
  return {
    uAlbedoTex: { value: null },
    uDispTex: { value: null },
    uSunDir: { value: new THREE.Vector3(0.58, 0.22, 0.78).normalize() },
  };
}

/**
 * The Moon: NASA SVS's colour map (from LRO's camera) and LOLA relief map, bump-lit with
 * Lommel-Seeliger shading, an opposition surge and a faint earthshine (the same shading as the
 * WebGPU port, webgpu/cislunar/bodyMaterials.ts). Only the mapped relief is drawn.
 */
export function createPhotorealisticMoonMesh(
  radius: number,
  moonUniforms: MoonUniforms
): THREE.Mesh {
  const geo = new THREE.SphereGeometry(radius, 160, 160);
  const mat = new THREE.ShaderMaterial({
    uniforms: moonUniforms,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      varying vec3 vNormal;
      varying vec3 vViewDir;
      varying vec3 vObjPos;
      void main() {
        vUv = uv;
        vObjPos = normalize(position);
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewDir = normalize(-mvPos.xyz);
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uAlbedoTex;
      uniform sampler2D uDispTex;
      uniform vec3 uSunDir;
      varying vec2 vUv;
      varying vec3 vNormal;
      varying vec3 vViewDir;
      varying vec3 vObjPos;

      void main() {
        vec3 albedo = texture2D(uAlbedoTex, vUv).rgb;

        vec2 texel = vec2(1.0 / 2048.0, 1.0 / 1024.0);
        float hL = texture2D(uDispTex, vUv - vec2(texel.x, 0.0)).r;
        float hR = texture2D(uDispTex, vUv + vec2(texel.x, 0.0)).r;
        float hD = texture2D(uDispTex, vUv - vec2(0.0, texel.y)).r;
        float hU = texture2D(uDispTex, vUv + vec2(0.0, texel.y)).r;

        vec3 bumpOffset = vec3((hL - hR) * 3.8, (hD - hU) * 3.8, 0.0);
        vec3 N = normalize(vNormal + bumpOffset * 0.62);

        vec3 L = normalize(uSunDir);
        vec3 V = normalize(vViewDir);
        float rawNdotL = dot(vNormal, L);
        float NdotL = max(0.0, dot(N, L));
        float NdotV = max(0.05, dot(N, V));

        // Hapke / Lommel-Seeliger lunar regolith backscatter with dramatic grazing terminator relief
        float lommelSeeliger = NdotL / (NdotL + NdotV * 0.62 + 0.001);
        float oppositionSurge = 1.0 + 0.32 * pow(max(0.0, dot(V, L)), 6.0);
        float terminatorMask = smoothstep(-0.08, 0.12, rawNdotL + (hL - hR) * 0.45);
        float regolithReflectance = lommelSeeliger * terminatorMask * oppositionSurge * 1.68;

        // Subtle cobalt Earthshine illuminating the dark lunar nightside
        float rimFresnel = pow(1.0 - max(0.0, dot(vNormal, V)), 2.4);
        vec3 earthshine = pow(albedo, vec3(1.1)) * vec3(0.024, 0.046, 0.088) * (1.0 - terminatorMask * 0.85) * (0.7 + 0.6 * rimFresnel);

        vec3 sunlitMoon = pow(albedo, vec3(1.22)) * vec3(1.06, 1.01, 0.93) * regolithReflectance;
        vec3 lunarColor = sunlitMoon + earthshine;
        gl_FragColor = vec4(lunarColor, 1.0);
      }
    `,
  });

  return new THREE.Mesh(geo, mat);
}

/**
 * REGIME 1: Cislunar Orbital Space (384,400 km - Orbital Sunrise over Earth's Rayleigh Limb + High-Relief LRO Moon)
 */
export function buildCislunarRegimeGroup(
  group: THREE.Group,
  earthUniforms: EarthUniforms,
  moonUniforms: MoonUniforms,
  gltfLoader: GLTFLoader
): { earthMesh: THREE.Mesh; moonMesh: THREE.Mesh } {
  const earthRadius = 13.5;
  const earthCenter = new THREE.Vector3(-11.8, -16.8, -28.0);

  const earthGeo = new THREE.SphereGeometry(earthRadius, 160, 160);
  const earthMat = new THREE.ShaderMaterial({
    uniforms: earthUniforms,
    vertexShader: /* glsl */ `
      varying vec3 vObjPos;
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        vObjPos = normalize(position);
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewDir = normalize(-mvPos.xyz);
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uDayTex;
      // three.js declares normalMatrix for vertex shaders only; the relief normal needs it here too
      uniform mat3 normalMatrix;
      uniform sampler2D uNightTex;
      uniform sampler2D uNormalTex;
      uniform sampler2D uSpecTex;
      uniform sampler2D uCloudsTex;
      uniform sampler2D uBrunetonLutTex;
      uniform vec3 uSunDir;
      varying vec3 vObjPos;
      varying vec3 vNormal;
      varying vec3 vViewDir;

      ${BRUNETON_ATMO_GLSL_CHUNK}

      // Brightness of Black Marble city light relative to the shader's other night-side terms
      const float CITY_LIGHT_GAIN = 1.25;

      void main() {
        // Rotate local sampling coordinates so the equatorial belt faces the camera
        // and the equirectangular polar pinch is tucked away on the far limb
        vec3 rawP = normalize(vObjPos);
        mat3 globeTilt = mat3(
           0.866,  0.250, -0.433,
           0.000,  0.866,  0.500,
           0.500, -0.433,  0.750
        );
        vec3 p = normalize(globeTilt * rawP);
        float u = atan(p.z, -p.x) / (2.0 * 3.14159265359) + 0.5;
        float v = asin(clamp(p.y, -1.0, 1.0)) / 3.14159265359 + 0.5;
        vec2 eqUv = vec2(u, v);
        // atan wraps at the texture seam; wrapping the u derivative keeps full mip detail there
        vec2 uvDx = dFdx(eqUv);
        vec2 uvDy = dFdy(eqUv);
        uvDx.x = fract(uvDx.x + 0.5) - 0.5;
        uvDy.x = fract(uvDy.x + 0.5) - 0.5;

        vec3 daySample = textureGrad(uDayTex, eqUv, uvDx, uvDy).rgb;
        vec3 dayCol = pow(daySample, vec3(1.32));
        vec3 nightLights = textureGrad(uNightTex, eqUv, uvDx, uvDy).rgb;
        float waterMask = textureGrad(uSpecTex, eqUv, uvDx, uvDy).r;
        float iceShelf = smoothstep(0.15, 0.35, min(daySample.r, daySample.g));
        float specMask = waterMask * (1.0 - iceShelf);
        float clouds = textureGrad(uCloudsTex, eqUv, uvDx, uvDy).r;
        float cloudsShadow = textureGrad(uCloudsTex, eqUv - vec2(0.0018, 0.0012), uvDx, uvDy).r;

        // ETOPO 2022 surface elevation (metres above EGM2008) decoded from (R, G) at true slopes on land
        vec2 hStep = vec2(1.0 / 4096.0, 1.0 / 2048.0);
        vec2 hW = texture2D(uNormalTex, vec2(fract(eqUv.x - hStep.x), eqUv.y)).rg;
        vec2 hE = texture2D(uNormalTex, vec2(fract(eqUv.x + hStep.x), eqUv.y)).rg;
        vec2 hS = texture2D(uNormalTex, vec2(eqUv.x, clamp(eqUv.y - hStep.y, 0.0, 1.0))).rg;
        vec2 hN = texture2D(uNormalTex, vec2(eqUv.x, clamp(eqUv.y + hStep.y, 0.0, 1.0))).rg;
        float hWestM = hW.r * 65280.0 + hW.g * 255.0 - 11000.0;
        float hEastM = hE.r * 65280.0 + hE.g * 255.0 - 11000.0;
        float hSouthM = hS.r * 65280.0 + hS.g * 255.0 - 11000.0;
        float hNorthM = hN.r * 65280.0 + hN.g * 255.0 - 11000.0;
        float land = 1.0 - waterMask;
        float cosLat = max(0.05, sqrt(max(0.0, 1.0 - p.y * p.y)));
        float slopeE = ((hEastM - hWestM) / (19546.0 * cosLat)) * land;
        float slopeN = ((hNorthM - hSouthM) / 19546.0) * land;
        vec3 eastTilt = normalize(cross(vec3(0.0, 1.0, 0.0), p) + vec3(1e-7, 0.0, 0.0));
        vec3 northTilt = cross(p, eastTilt);
        vec3 tiltedN = normalize(p - eastTilt * slopeE - northTilt * slopeN);
        vec3 localN = normalize(vec3(
          dot(vec3(0.866, 0.000, 0.500), tiltedN),
          dot(vec3(0.250, 0.866, -0.433), tiltedN),
          dot(vec3(-0.433, 0.500, 0.750), tiltedN)
        ));

        vec3 N = normalize(vNormal);
        vec3 surfaceN = normalize(normalMatrix * localN);
        vec3 L = normalize(uSunDir);
        vec3 V = normalize(vViewDir);

        float sunDot = dot(N, L);
        float surfaceSunDot = dot(surfaceN, L);
        float dayFactor = smoothstep(-0.08, 0.22, sunDot);
        // Tight stratospheric Rayleigh/Mie sunset band along the orbital terminator
        float twilightFactor = exp(-pow((sunDot + 0.02) * 9.0, 2.0));

        vec3 H = normalize(L + V);
        float spec = pow(max(0.0, dot(N, H)), 72.0) * specMask * max(0.0, sunDot) * (1.0 - clouds * 0.85);

        // Deep cobalt ocean & rich continental albedo under directional sunlight
        vec3 oceanTint = mix(dayCol, vec3(0.025, 0.095, 0.24), specMask * 0.55);
        vec3 surfaceAlbedo = oceanTint * (1.0 - cloudsShadow * 0.48);
        vec3 cloudLayer = vec3(0.92, 0.95, 1.0) * clouds * (0.85 + 0.25 * twilightFactor);
        vec3 litSurface = surfaceAlbedo * max(0.03, surfaceSunDot * 1.05);
        vec3 litClouds = cloudLayer * max(0.03, sunDot * 1.05);
        vec3 litEarth = mix(litSurface, litClouds, clamp(clouds * 0.84, 0.0, 1.0))
                      + vec3(0.42, 0.68, 0.98) * spec * 0.65;

        vec3 twilightGlow = vec3(0.92, 0.36, 0.10) * twilightFactor * (0.20 + 0.32 * clouds);

        // NASA Black Marble 2016 city light (dim base layer removed at bake time), dimmed under cloud
        vec3 cityGlow = nightLights * CITY_LIGHT_GAIN * (1.0 - clouds * 0.72);

        vec3 darkContinents = oceanTint * vec3(0.015, 0.034, 0.075) + vec3(0.005, 0.014, 0.032) * (1.0 - specMask * 0.5);
        vec3 nightEarth = darkContinents + cityGlow + vec3(0.012, 0.026, 0.055) * clouds;

        float fresnel = pow(1.0 - max(0.0, dot(N, V)), 3.8);
        vec4 brunetonSample = sampleBrunetonAtmoLut(uBrunetonLutTex, dot(N, V), sunDot);
        vec3 atmoTint = mix(vec3(0.06, 0.24, 0.64), brunetonSample.rgb * 1.25, 0.68) * fresnel * (0.42 + 0.52 * dayFactor);
        vec3 lutTwilight = brunetonSample.rgb * twilightFactor * (0.28 + 0.36 * clouds);

        vec3 rawEarth = mix(nightEarth, litEarth, dayFactor) + mix(twilightGlow, lutTwilight, 0.65) + atmoTint;
        // Filmic tone-mapping so sunlit clouds & ocean never clip to flat white
        vec3 finalEarth = rawEarth / (vec3(1.0) + rawEarth * 0.56);
        gl_FragColor = vec4(finalEarth, 1.0);
      }
    `,
  });

  const earthMesh = new THREE.Mesh(earthGeo, earthMat);
  earthMesh.position.copy(earthCenter);
  earthMesh.rotation.y = -1.52;
  earthMesh.rotation.x = 0.12;
  earthMesh.rotation.z = -0.18;
  group.add(earthMesh);

  const atmoGeo = new THREE.SphereGeometry(earthRadius * 1.022, 160, 160);
  const atmoMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uSunDir: earthUniforms.uSunDir,
      uBrunetonLutTex: earthUniforms.uBrunetonLutTex,
    },
    vertexShader: /* glsl */ `
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewDir = normalize(-mvPos.xyz);
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uSunDir;
      uniform sampler2D uBrunetonLutTex;
      varying vec3 vNormal;
      varying vec3 vViewDir;

      ${BRUNETON_ATMO_GLSL_CHUNK}

      void main() {
        float viewDot = dot(vNormal, vViewDir);
        float limbIntensity = exp(-pow((viewDot + 0.14) * 5.2, 2.0));
        float sunAlign = dot(-vNormal, normalize(uSunDir));
        float sunOrient = 0.22 + 0.78 * smoothstep(-0.20, 0.65, sunAlign);
        float sunriseArc = exp(-pow(sunAlign - 0.32, 2.0) * 6.5) * 0.38;
        vec4 lutLimb = sampleBrunetonAtmoLut(uBrunetonLutTex, clamp(viewDot + 0.16, -0.35, 1.0), sunAlign);
        vec3 baseRayleigh = mix(vec3(0.14, 0.50, 0.96), vec3(0.52, 0.78, 1.0), sunriseArc);
        vec3 rayleighCol = mix(baseRayleigh, lutLimb.rgb * 1.35, 0.62) * limbIntensity * sunOrient * 0.98;
        gl_FragColor = vec4(rayleighCol, clamp(limbIntensity * 0.86, 0.0, 0.84));
      }
    `,
  });
  const atmoMesh = new THREE.Mesh(atmoGeo, atmoMat);
  atmoMesh.position.copy(earthCenter);
  group.add(atmoMesh);

  const moonRadius = 1.98;
  const moonCenter = new THREE.Vector3(2.45, 1.25, -8.2);
  const moonMesh = createPhotorealisticMoonMesh(moonRadius, moonUniforms);
  moonMesh.position.copy(moonCenter);
  moonMesh.rotation.y = -0.85;
  group.add(moonMesh);

  gltfLoader.load(
    '/models/nasa_lro_moon_3d.glb',
    (gltf) => {
      gltf.scene.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) {
          const m = child as THREE.Mesh;
          moonMesh.geometry.dispose();
          moonMesh.geometry = m.geometry.clone();
          moonMesh.geometry.scale(moonRadius / 10.0, moonRadius / 10.0, moonRadius / 10.0);
        }
      });
    },
    undefined,
    () => {}
  );

  return { earthMesh, moonMesh };
}
