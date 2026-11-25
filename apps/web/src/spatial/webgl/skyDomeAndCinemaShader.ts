import * as THREE from 'three';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';
import { PANORAMA_GRADE } from '../sky/panoramaGrade.js';
import { recordPanorama } from './skyPictureSwitch.js';
import { GLOW_SIZE_PX, PLANET_WINDOWS } from '../sky/flightMilkyWay.js';
import { loadPanoramaRegistration, sceneToPanoramaRows } from '../sky/skyRegistration.js';
import { BRUNETON_ATMO_LUT_URL } from '../volumetricSplatsAndLut.js';
import type { EarthUniforms, MoonUniforms } from './cislunarRegime.js';
import { glslFloat } from './glslFloat.js';

export interface SkyDomeUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uMilkyWayTex: { value: THREE.Texture | null };
  /** predsky-starless-one: 1 while the dome shows the starless glow, whose two planet windows are then filled. */
  uFillPlanets: { value: number };
  uSceneToPanorama: { value: THREE.Matrix3 };
  uPanoramaOffsets: { value: THREE.Texture | null };
  uPanoramaGeometry: { value: THREE.Vector3 };
  uPanoramaOffsetDecode: { value: THREE.Vector2 };
  uTime: { value: number };
}

export interface CinemaOpticsUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  tDiffuse: { value: THREE.Texture | null };
  uTime: { value: number };
  uWarpFactor: { value: number };
}

export function createSkyDomeUniforms(): SkyDomeUniforms {
  return {
    uMilkyWayTex: { value: null },
    uFillPlanets: { value: 0 },
    // Set from eso0932a_registration.json before the panorama is bound (sky/skyRegistration.ts)
    uSceneToPanorama: { value: new THREE.Matrix3() },
    uPanoramaOffsets: { value: null },
    // Image width and height in pixels, vertical offset in pixels
    uPanoramaGeometry: { value: new THREE.Vector3(1, 1, 0) },
    // Offset table codes per texel and bias
    uPanoramaOffsetDecode: { value: new THREE.Vector2(1, 0) },
    uTime: { value: 0.0 },
  };
}

export function createCinemaOpticsUniforms(): CinemaOpticsUniforms {
  return {
    tDiffuse: { value: null },
    uTime: { value: 0.0 },
    uWarpFactor: { value: 0.0 },
  };
}

export function createCinemaOpticsPass(uniforms: CinemaOpticsUniforms): ShaderPass {
  return new ShaderPass({
    uniforms,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D tDiffuse;
      uniform float uTime;
      uniform float uWarpFactor;
      varying vec2 vUv;

      void main() {
        vec2 centered = vUv - 0.5;
        float r2 = dot(centered, centered);

        float caStrength = 0.0018 + uWarpFactor * 0.018;
        vec2 caOffset = centered * r2 * caStrength;
        float rCh = texture2D(tDiffuse, vUv + caOffset).r;
        float gCh = texture2D(tDiffuse, vUv).g;
        float bCh = texture2D(tDiffuse, vUv - caOffset).b;
        vec3 baseCol = vec3(rCh, gCh, bCh);

        // No streak: eight samples at fixed offsets drew each bright star as a row of blue dots (streak-ghosts-one)
        float vignette = smoothstep(0.92, 0.18, r2);
        vec3 finalCol = baseCol * mix(0.76, 1.0, vignette);
        gl_FragColor = vec4(finalCol, 1.0);
      }
    `,
  });
}

/**
 * All-sky dome around the camera (in the camera's localFrame): ESO's Milky Way panorama
 * (eso0932a), sampled by view direction so it sits at infinity and lines up with the catalog
 * stars, through its fitted rotation and offset table (sky/skyRegistration.ts).
 */
export function createGlobalPhotorealisticSkyDomeMesh(
  uniforms: SkyDomeUniforms
): THREE.Mesh {
  // The panorama's grade, from the one list both renderers and the Credits page read (sky/panoramaGrade.ts)
  const grade = {
    blackLevel: glslFloat(PANORAMA_GRADE.blackLevel),
    power: glslFloat(PANORAMA_GRADE.power),
    tint: PANORAMA_GRADE.tint.map(glslFloat).join(', '),
    gain: glslFloat(PANORAMA_GRADE.gain),
    base: PANORAMA_GRADE.base.map(glslFloat).join(', '),
  };
  const skyGeo = new THREE.SphereGeometry(900, 96, 64);
  const skyMat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms,
    vertexShader: /* glsl */ `
      varying vec3 vSkyDir;
      void main() {
        vec4 worldPos = modelMatrix * vec4(position, 1.0);
        // Only the direction from the camera matters for a sky at infinity
        vSkyDir = worldPos.xyz - cameraPosition;
        gl_Position = projectionMatrix * viewMatrix * worldPos;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uMilkyWayTex;
      uniform float uFillPlanets;
      uniform mat3 uSceneToPanorama;
      uniform sampler2D uPanoramaOffsets;
      uniform vec3 uPanoramaGeometry;
      uniform vec2 uPanoramaOffsetDecode;
      uniform float uTime;
      varying vec3 vSkyDir;

      // Plate carree coordinates of a unit vector: longitude 0 at u = 0.5, increasing to the left; north at v = 1
      vec2 plateCarree(vec3 d) {
        return vec2(fract(0.5 - atan(d.y, d.x) * 0.15915494309189535), 0.5 + asin(clamp(d.z, -1.0, 1.0)) * 0.3183098861837907);
      }

      // Screen-space gradients of plate carree coordinates, wrapped where u jumps across the seam
      void seamlessGrad(vec2 uv, out vec2 gradX, out vec2 gradY) {
        gradX = dFdx(uv);
        gradY = dFdy(uv);
        gradX.x = fract(gradX.x + 0.5) - 0.5;
        gradY.x = fract(gradY.x + 0.5) - 0.5;
      }

      // predsky-starless-one: in the starless glow, Venus's and Jupiter's windows (the bake puts them back as
      // photographed) filled from a ring of eight samples outside them, as the flight fills them (flightMilkyWay.ts)
      const vec2 GLOW_SIZE = vec2(${GLOW_SIZE_PX.width.toFixed(1)}, ${GLOW_SIZE_PX.height.toFixed(1)});
      const vec3 PLANETS[${PLANET_WINDOWS.length}] = vec3[${PLANET_WINDOWS.length}](${PLANET_WINDOWS.map((w) => `vec3(${(w.xPx / GLOW_SIZE_PX.width).toFixed(6)}, ${(1 - w.yPx / GLOW_SIZE_PX.height).toFixed(6)}, ${w.radiusPx.toFixed(1)})`).join(', ')});
      vec3 fillPlanets(vec2 uv, vec3 base, vec2 gx, vec2 gy) {
        for (int i = 0; i < ${PLANET_WINDOWS.length}; i++) {
          vec2 c = PLANETS[i].xy;
          float radiusPx = PLANETS[i].z;
          vec2 dPx = vec2((fract(uv.x - c.x + 0.5) - 0.5) * GLOW_SIZE.x, (uv.y - c.y) * GLOW_SIZE.y);
          if (length(dPx) <= radiusPx) {
            vec3 sum = vec3(0.0);
            for (int k = 0; k < 8; k++) {
              float a = float(k) * 0.7853981633974483;
              sum += textureGrad(uMilkyWayTex, c + vec2(cos(a), sin(a)) * 2.0 * radiusPx / GLOW_SIZE, gx, gy).rgb;
            }
            return sum / 8.0;
          }
        }
        return base;
      }

      void main() {
        vec3 dir = normalize(vSkyDir);

        // ESO panorama: its fitted frame, then the (east, north) offset table in texels
        vec3 p = normalize(uSceneToPanorama * dir);
        vec2 frameUv = plateCarree(p);
        vec2 offset = (texture2D(uPanoramaOffsets, frameUv).rg * 255.0 - uPanoramaOffsetDecode.y) / uPanoramaOffsetDecode.x;
        float cosLat = max(length(p.xy), 1e-3);
        vec2 mwUv = frameUv + vec2(
          -offset.x / (uPanoramaGeometry.x * cosLat),
          (offset.y - uPanoramaGeometry.z) / uPanoramaGeometry.y
        );
        vec2 mwGradX, mwGradY;
        seamlessGrad(frameUv, mwGradX, mwGradY);

        vec3 mwCol = textureGrad(uMilkyWayTex, mwUv, mwGradX, mwGradY).rgb;
        if (uFillPlanets > 0.5) mwCol = fillPlanets(mwUv, mwCol, mwGradX, mwGradY);
        vec3 cleanMw = max(vec3(0.0), mwCol - vec3(${grade.blackLevel}));

        // Deep velvet-obsidian cosmos + smooth Galactic Core dust-lane warmth (zero grainy JPEG noise)
        vec3 galacticBulge = pow(cleanMw, vec3(${grade.power})) * vec3(${grade.tint}) * ${grade.gain};
        vec3 deepSpaceBase = vec3(${grade.base});
        vec3 opticalSky = deepSpaceBase + galacticBulge;
        gl_FragColor = vec4(opticalSky, 1.0);
      }
    `,
  });

  return new THREE.Mesh(skyGeo, skyMat);
}

/** A loader for the scenes' pictures: repeating across, clamped up and down, sRGB unless asked otherwise. */
function textureLoading(textureLoader: THREE.TextureLoader) {
  return (
    url: string,
    onLoad: (tex: THREE.Texture) => void,
    srgb = true
  ) => {
    textureLoader.load(
      url,
      (tex) => {
        tex.wrapS = THREE.RepeatWrapping;
        tex.wrapT = THREE.ClampToEdgeWrapping;
        tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        tex.needsUpdate = true;
        onLoad(tex);
      },
      undefined,
      () => {}
    );
  };
}

/** The sky view's one picture, the registered Milky Way panorama: loaded on every visit. */
export function loadSkyViewTextures(textureLoader: THREE.TextureLoader, skyDomeUniforms: SkyDomeUniforms): void {
  const loadTex = textureLoading(textureLoader);
  const panorama = new Promise<THREE.Texture>((resolve) =>
    loadTex('/textures/eso_milkyway_panorama.jpg', resolve)
  );
  const bound = Promise.all([loadPanoramaRegistration(), panorama]).then(
    ([registration, tex]) => {
      const { width, height, scale, bias, codes } = registration.offsetTable;
      const offsets = new THREE.DataTexture(codes, width, height, THREE.RGFormat, THREE.UnsignedByteType);
      offsets.wrapS = THREE.RepeatWrapping;
      offsets.wrapT = THREE.ClampToEdgeWrapping;
      offsets.minFilter = THREE.LinearFilter;
      offsets.magFilter = THREE.LinearFilter;
      offsets.generateMipmaps = false;
      offsets.unpackAlignment = 1;
      offsets.needsUpdate = true;
      skyDomeUniforms.uPanoramaOffsets.value = offsets;
      skyDomeUniforms.uPanoramaOffsetDecode.value.set(scale, bias);
      skyDomeUniforms.uPanoramaGeometry.value.set(
        registration.imageWidth,
        registration.imageHeight,
        registration.yOffsetPx
      );
      skyDomeUniforms.uSceneToPanorama.value.set(...sceneToPanoramaRows(registration));
      skyDomeUniforms.uMilkyWayTex.value = tex;
      return tex;
    },
    (error: unknown) => {
      console.warn('[sky] panorama registration failed; the dome is drawn without the panorama', error);
      return null;
    }
  );
  // predsky-starless-one: kept for the picture switch, which waits on it before it puts the starless glow on
  recordPanorama(skyDomeUniforms, bound);
}

export { heardLimitOf, rendererUpload, setSkyPictureOf, type SkyPicture } from './skyPictureSwitch.js';

/**
 * The landing scene's pictures, which the sky view never draws: the Earth, the Moon and the atmosphere table. Loaded
 * when the landing scene first starts, not on every visit.
 */
export function loadLandingSceneTextures(
  textureLoader: THREE.TextureLoader,
  earthUniforms: EarthUniforms,
  moonUniforms: MoonUniforms
): void {
  const loadTex = textureLoading(textureLoader);
  loadTex('/textures/earth_day_bluemarble_2004-03_base_8k.jpg', (t) => {
    earthUniforms.uDayTex.value = t;
  });
  loadTex('/textures/earth_night_blackmarble_2016_8k.jpg', (t) => {
    earthUniforms.uNightTex.value = t;
  });
  loadTex(
    '/textures/earth_height_etopo2022_4k.png',
    (t) => {
      t.minFilter = THREE.NearestFilter;
      t.magFilter = THREE.NearestFilter;
      t.generateMipmaps = false;
      t.needsUpdate = true;
      earthUniforms.uNormalTex.value = t;
    },
    false
  );
  loadTex(
    '/textures/earth_water_natural_earth_4k.png',
    (t) => {
      earthUniforms.uSpecTex.value = t;
    },
    false
  );
  loadTex('/textures/earth_clouds_bluemarble2002_2k.jpg', (t) => {
    earthUniforms.uCloudsTex.value = t;
  });
  loadTex('/textures/lro_moon_2k.jpg', (t) => {
    moonUniforms.uAlbedoTex.value = t;
  });
  loadTex(
    '/textures/lro_moon_disp.jpg',
    (t) => {
      moonUniforms.uDispTex.value = t;
    },
    false
  );
  textureLoader.load(
    BRUNETON_ATMO_LUT_URL,
    (lutTex) => {
      lutTex.wrapS = THREE.ClampToEdgeWrapping;
      lutTex.wrapT = THREE.ClampToEdgeWrapping;
      lutTex.minFilter = THREE.LinearFilter;
      lutTex.magFilter = THREE.LinearFilter;
      lutTex.colorSpace = THREE.NoColorSpace;
      lutTex.needsUpdate = true;
      earthUniforms.uBrunetonLutTex.value = lutTex;
    },
    undefined,
    () => {}
  );
}
