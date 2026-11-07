import * as THREE from 'three';
import { buildAnisotropicLocalBubbleSplatMesh } from '../volumetricSplatsAndLut.js';

export interface LocalBubbleUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uTime: { value: number };
  uWarpFactor: { value: number };
  uCoolColor: { value: THREE.Vector3 };
  uWarmColor: { value: THREE.Vector3 };
  uHotColor: { value: THREE.Vector3 };
}

export function createLocalBubbleUniforms(): LocalBubbleUniforms {
  return {
    uTime: { value: 0.0 },
    uWarpFactor: { value: 0.0 },
    uCoolColor: { value: new THREE.Vector3(0.28, 0.03, 0.005) },
    uWarmColor: { value: new THREE.Vector3(0.92, 0.24, 0.03) },
    uHotColor: { value: new THREE.Vector3(1.38, 0.78, 0.28) },
  };
}

/**
 * REGIME 2: solar neighbourhood (landing scene 03): a modelled star and 14,281 Spark splats
 * (`lb_splats.json`) on a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024),
 * all placed around the camera for composition, not at their sizes or distances; see
 * SOLAR_NEIGHBOURHOOD_WEBGL_RENDER_LAYERS in webglRenderLayers.ts.
 */
export function buildSolarNeighborhoodRegimeGroup(
  group: THREE.Group,
  localBubbleUniforms: LocalBubbleUniforms
): { betelgeuseMesh: THREE.Mesh; cavityGroup: THREE.Group } {
  const starRadius = 2.62;
  const starCenter = new THREE.Vector3(-0.35, 0.12, -9.2);
  const starGeo = new THREE.SphereGeometry(starRadius, 160, 160);
  const starMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: localBubbleUniforms.uTime,
      uCoolColor: localBubbleUniforms.uCoolColor,
      uWarmColor: localBubbleUniforms.uWarmColor,
      uHotColor: localBubbleUniforms.uHotColor,
    },
    vertexShader: /* glsl */ `
      varying vec3 vNormal;
      varying vec3 vViewDir;
      varying vec3 vObjPos;
      void main() {
        vObjPos = normalize(position);
        vNormal = normalize(normalMatrix * normal);
        vec4 mvPos = modelViewMatrix * vec4(position, 1.0);
        vViewDir = normalize(-mvPos.xyz);
        gl_Position = projectionMatrix * mvPos;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform vec3 uCoolColor;
      uniform vec3 uWarmColor;
      uniform vec3 uHotColor;
      varying vec3 vNormal;
      varying vec3 vViewDir;
      varying vec3 vObjPos;

      float hash(vec3 p) {
        p = fract(p * 0.3183099 + 0.1);
        p *= 17.0;
        return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
      }

      float noise3D(vec3 x) {
        vec3 i = floor(x);
        vec3 f = fract(x);
        f = f * f * (3.0 - 2.0 * f);
        return mix(
          mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
              mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
          mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
              mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y),
          f.z
        );
      }

      float fbm(vec3 p) {
        float v = 0.0;
        float a = 0.5;
        for (int i = 0; i < 6; i++) {
          v += a * noise3D(p);
          p = p * 2.12 + vec3(2.1, 4.7, 1.3);
          a *= 0.5;
        }
        return v;
      }

      float ridgedFbm(vec3 p) {
        float v = 0.0;
        float a = 0.5;
        for (int i = 0; i < 5; i++) {
          float n = 1.0 - abs(noise3D(p) * 2.0 - 1.0);
          v += a * (n * n);
          p = p * 2.18 + vec3(1.4, 3.2, 2.7);
          a *= 0.5;
        }
        return v;
      }

      void main() {
        vec3 N = normalize(vNormal);
        vec3 V = normalize(vViewDir);
        float mu = clamp(dot(N, V), 0.0, 1.0);

        vec3 flow = vec3(uTime * 0.028, -uTime * 0.019, uTime * 0.015);
        vec3 warp = vec3(
          fbm(vObjPos * 4.2 + flow),
          fbm(vObjPos * 4.2 + flow + vec3(5.2, 1.3, 2.8)),
          fbm(vObjPos * 4.2 + flow + vec3(1.7, 9.2, 4.1))
        );

        float mesoCells = ridgedFbm(vObjPos * 14.5 + warp * 1.45 - flow * 1.5);
        float microGranules = noise3D(vObjPos * 34.0 + warp * 2.2 + flow * 2.5);
        float microLanes = ridgedFbm(vObjPos * 26.0 + warp * 1.8);

        float convection = clamp(
          warp.x * 0.32 + mesoCells * 0.44 + microGranules * 0.14 + microLanes * 0.22,
          0.0,
          1.0
        );

        float spotField = fbm(vObjPos * 5.8 - flow * 0.6);
        float umbra = smoothstep(0.24, 0.14, spotField);
        float penumbra = smoothstep(0.33, 0.22, spotField) * (1.0 - umbra);

        float flareRibbon = pow(clamp(mesoCells * smoothstep(0.24, 0.34, spotField) * smoothstep(0.46, 0.33, spotField) * 2.1, 0.0, 1.0), 3.2);

        vec3 surfaceCol = mix(uCoolColor * 0.25, uWarmColor * 0.88, smoothstep(0.22, 0.68, convection));
        surfaceCol = mix(surfaceCol, uHotColor * 0.92, smoothstep(0.68, 0.98, convection));
        surfaceCol = mix(surfaceCol, uCoolColor * 0.08, umbra * 0.92 + penumbra * 0.55);
        surfaceCol += uHotColor * flareRibbon * 1.15;

        float limbDarkening = pow(mu, 0.48);
        surfaceCol *= (0.34 + 0.66 * limbDarkening);

        float rim = pow(1.0 - mu, 3.2);
        float rimFibrils = ridgedFbm(vObjPos * 24.0 + flow * 2.0);
        surfaceCol += mix(uWarmColor, uHotColor, 0.45) * rim * (0.65 + 0.65 * rimFibrils);

        gl_FragColor = vec4(surfaceCol, 1.0);
      }
    `,
  });
  const betelgeuseMesh = new THREE.Mesh(starGeo, starMat);
  betelgeuseMesh.position.copy(starCenter);
  group.add(betelgeuseMesh);

  // Spark splats (`lb_splats.json`) on a Local Bubble mesh of unrecorded origin, named for O'Neill et al. (2024)
  const cavityGroup = new THREE.Group();
  cavityGroup.position.set(0.0, 0.0, -9.8);
  group.add(cavityGroup);

  if (typeof fetch !== 'undefined') {
    fetch('/models/lb_splats.json')
      .then((res) => (res.ok ? res.json() : null))
      .then((raw: number[][] | null) => {
        if (!Array.isArray(raw) || raw.length === 0) return;
        const lbSplatMesh = buildAnisotropicLocalBubbleSplatMesh(raw);
        cavityGroup.add(lbSplatMesh);
      })
      .catch(() => {});
  }

  return { betelgeuseMesh, cavityGroup };
}

export function applyTargetAppearanceToUniforms(
  title: string,
  bpRp: number | undefined,
  objectType: string | undefined,
  localBubbleUniforms: LocalBubbleUniforms
): boolean {
  const lower = title.toLowerCase();
  const isBlackHole =
    lower.includes('sagittarius') ||
    lower.includes('sgr a') ||
    lower.includes('black hole');

  if (lower.includes('uranus')) {
    localBubbleUniforms.uCoolColor.value.set(0.08, 0.38, 0.52);
    localBubbleUniforms.uWarmColor.value.set(0.24, 0.72, 0.88);
    localBubbleUniforms.uHotColor.value.set(0.58, 0.92, 1.0);
    return isBlackHole;
  }
  if (lower.includes('saturn') || lower.includes('jupiter') || objectType === 'planet') {
    localBubbleUniforms.uCoolColor.value.set(0.42, 0.28, 0.12);
    localBubbleUniforms.uWarmColor.value.set(0.88, 0.68, 0.36);
    localBubbleUniforms.uHotColor.value.set(1.18, 0.96, 0.64);
    return isBlackHole;
  }
  const colorIdx = bpRp ?? (lower.includes('betelgeuse') || lower.includes('aldebaran') ? 1.85 : 0.1);
  if (colorIdx >= 1.2) {
    localBubbleUniforms.uCoolColor.value.set(0.28, 0.03, 0.005);
    localBubbleUniforms.uWarmColor.value.set(0.92, 0.24, 0.03);
    localBubbleUniforms.uHotColor.value.set(1.38, 0.78, 0.28);
  } else if (colorIdx < -0.02) {
    localBubbleUniforms.uCoolColor.value.set(0.14, 0.28, 0.68);
    localBubbleUniforms.uWarmColor.value.set(0.48, 0.72, 1.25);
    localBubbleUniforms.uHotColor.value.set(0.95, 1.15, 1.55);
  } else {
    localBubbleUniforms.uCoolColor.value.set(0.32, 0.42, 0.65);
    localBubbleUniforms.uWarmColor.value.set(0.82, 0.92, 1.18);
    localBubbleUniforms.uHotColor.value.set(1.32, 1.38, 1.52);
  }
  return isBlackHole;
}
