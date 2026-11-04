import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/examples/jsm/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import {
  PackedSplats,
  SparkControls,
  SparkRenderer,
  SparkXr,
  SplatMesh,
} from '@sparkjsdev/spark';
import {
  ALL_SKY_CONSTELLATION_EDGES,
  NAMED_ALL_SKY_STARS_3D,
  bpRpToStarColorHex,
} from './allSkyStarfield.js';
import {
  createOriginCameraFrustumIndicator,
  updateOriginFrustumContinuity,
} from './referenceFrameContinuity.js';
import {
  BRUNETON_ATMO_GLSL_CHUNK,
  BRUNETON_ATMO_LUT_URL,
  buildAnisotropicLocalBubbleSplatMesh,
  buildAnisotropicNebulaSplatMesh,
} from './volumetricSplatsAndLut.js';

export type SpatialScaleRegime =
  | 'earth_capture'
  | 'cislunar'
  | 'solar_neighborhood'
  | 'deep_sky'
  | 'planetary_surface';

export type SpectralBandMode = 'optical' | 'halpha' | 'jwst_ir';

export const SCALE_REGIME_ORDER: SpatialScaleRegime[] = [
  'earth_capture',
  'cislunar',
  'solar_neighborhood',
  'deep_sky',
  'planetary_surface',
];

/**
 * Continuous 3D positions along the Earth -> Cislunar -> Solar Neighborhood -> Deep-Sky -> Planetary Surface flight corridor.
 */
const REGIME_CAMERA_ANCHORS: Record<SpatialScaleRegime, THREE.Vector3> = {
  earth_capture: new THREE.Vector3(0, 0, 0),
  cislunar: new THREE.Vector3(0, 42, -160),
  solar_neighborhood: new THREE.Vector3(0, 95, -380),
  deep_sky: new THREE.Vector3(0, 150, -620),
  planetary_surface: new THREE.Vector3(0, 210, -880),
};

export interface PlanetaryWorldDescriptor {
  worldName: string;
  worldClass: string;
  surfaceGravity: string;
  atmosphereComposition: string;
  equilibriumTemp: string;
  parentStarSkyDesc: string;
  earthReturnSightline: string;
  terrainPalette:
    | 'lunar_regolith'
    | 'exoplanet_terminator'
    | 'titan_methane'
    | 'gargantua_tidal_ocean'
    | 'supergiant_silicate';
}

/**
 * Computes a scientifically grounded Planetary / Exoplanetary World descriptor
 * from the active celestial target's astrophysical parameters.
 */
export function derivePlanetaryWorldDescriptor(
  title: string,
  bpRp = 0.8,
  distanceLy = 4.37,
  observerPlaceName = 'Earth Observation Site'
): PlanetaryWorldDescriptor {
  const lower = title.toLowerCase();
  if (
    lower.includes('sagittarius') ||
    lower.includes('sgr a') ||
    lower.includes('black hole') ||
    lower.includes('gargantua')
  ) {
    return {
      worldName: 'Tidal Ocean World · Sgr A* Horizon',
      worldClass: 'Kerr Metric Inner-Orbit Ocean World (a/M = 0.998 · Miller Analog)',
      surfaceGravity: '1.3× Earth (12.7 m/s²)',
      atmosphereComposition: 'Shallow Specular Tidal Ocean · Time Dilation 1.7×',
      equilibriumTemp: '+16°C (289 K · Accretion Disk Glow)',
      parentStarSkyDesc:
        '4.15×10⁶ M☉ Relativistic Black Hole, Photon Ring & Doppler-Beamed Accretion Disk',
      earthReturnSightline: `Gravitational time dilation γ ≈ 1.68 · 26,670 ly to ${observerPlaceName}`,
      terrainPalette: 'gargantua_tidal_ocean',
    };
  }
  if (lower.includes('moon') || lower.includes('luna')) {
    return {
      worldName: 'Lunar South Pole · Shackleton Crater Rim',
      worldClass: 'Airless Silicate Regolith Body (NASA LRO 100m)',
      surfaceGravity: '0.17× Earth (1.62 m/s²)',
      atmosphereComposition: 'Airless Vacuum · Blue Marble Earth Overhead',
      equilibriumTemp: '-163°C Shadow (110 K)',
      parentStarSkyDesc: `Blue Marble Earth hanging 3.7× wider than the Moon (${observerPlaceName} visible on limb)`,
      earthReturnSightline: `1.28 light-sec (384,400 km) direct laser link to ${observerPlaceName}`,
      terrainPalette: 'lunar_regolith',
    };
  }
  if (lower.includes('saturn') || lower.includes('titan') || lower.includes('jupiter') || lower.includes('uranus') || lower.includes('betelgeuse')) {
    return {
      worldName: 'Titan · Hydrocarbon Sea & Saturn Rings',
      worldClass: 'Cryogenic Organo-Nitrogen Ocean Moon & Ring Horizon',
      surfaceGravity: '0.14× Earth (1.35 m/s²)',
      atmosphereComposition: 'Dense Golden Nitrogen-Methane Haze · Ringed Saturn Sky',
      equilibriumTemp: '-179°C (94 K · Liquid Methane Sea)',
      parentStarSkyDesc: 'Giant Ringed Saturn & Cassini Division arching 35° across the twilight sky',
      earthReturnSightline: `75.3 light-min (9.06 AU) back to Sol & ${observerPlaceName}`,
      terrainPalette: 'titan_methane',
    };
  }
  if (
    lower.includes('alpha centauri') ||
    lower.includes('proxima') ||
    lower.includes('altair') ||
    lower.includes('acrux') ||
    lower.includes('fomalhaut') ||
    lower.includes('deneb') ||
    lower.includes('dabih') ||
    lower.includes('albireo') ||
    (bpRp < 1.15 && distanceLy < 450 && !lower.includes('nebula'))
  ) {
    const isAlphaCen = lower.includes('alpha centauri') || lower.includes('proxima');
    return {
      worldName: isAlphaCen
        ? 'Proxima Centauri b · Glacial Terminator Canyon'
        : `${title.split('(')[0]?.trim()} b · Circumstellar Glacier World`,
      worldClass: isAlphaCen
        ? 'Tidally Locked Terrestrial Exoplanet (1.07 M⊕ · 0.0485 AU)'
        : 'Data-Driven Circumstellar Terrestrial Exoplanet',
      surfaceGravity: isAlphaCen ? '1.1× Earth (10.9 m/s²)' : '0.96× Earth (9.4 m/s²)',
      atmosphereComposition: 'Twilight Glacier Canyon · Red Dwarf + Twin Suns Sky',
      equilibriumTemp: isAlphaCen ? '-39°C (234 K · Eternal Sunset Zone)' : '-5°C (268 K · Temperate Zone)',
      parentStarSkyDesc: isAlphaCen
        ? 'Proxima Cen (M5.5V Red Dwarf) + Alpha Cen A/B binary pair gleaming overhead'
        : `${title.split('(')[0]?.trim()} primary photosphere illuminating the glacier horizon`,
      earthReturnSightline: `Sol (Earth's Sun) shines as a mag +0.4 yellow star ${distanceLy.toFixed(2)} ly away`,
      terrainPalette: 'exoplanet_terminator',
    };
  }
  return {
    worldName: `${title.split('(')[0]?.trim()} · Relativistic Horizon World`,
    worldClass: 'Deep-Space Tidal Ocean & Eroded Basalt World',
    surfaceGravity: '1.1× Earth (11.2 m/s²)',
    atmosphereComposition: 'Ionized H-Alpha & Specular Tidal Ocean Envelope',
    equilibriumTemp: '+11°C (284 K · Radiative Equilibrium)',
    parentStarSkyDesc: `${title.split('(')[0]?.trim()} relativistic accretion & ionization front spanning 38° of sky`,
    earthReturnSightline: `Photons departing toward ${observerPlaceName} require ${Math.round(distanceLy).toLocaleString()} years to reach Earth`,
    terrainPalette: 'gargantua_tidal_ocean',
  };
}

/**
 * SparkPortalRenderer
 *
 * Full-screen photorealistic WebGL2 + `@sparkjsdev/spark` 3D Gaussian Splatting (`SparkRenderer` + `SplatMesh`)
 * & WebXR Space Observatory Engine.
 */
export class SparkPortalRenderer {
  private renderer: THREE.WebGLRenderer | null = null;
  private sparkRenderer: SparkRenderer | null = null;
  private composer: EffectComposer | null = null;
  private bloomPass: UnrealBloomPass | null = null;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private localFrame: THREE.Group;
  private sparkControls: SparkControls | null = null;
  private sparkXr: SparkXr | null = null;
  private textureLoader: THREE.TextureLoader;
  private gltfLoader: GLTFLoader;

  private regimeGroups: Record<SpatialScaleRegime, THREE.Group> = {
    earth_capture: new THREE.Group(),
    cislunar: new THREE.Group(),
    solar_neighborhood: new THREE.Group(),
    deep_sky: new THREE.Group(),
    planetary_surface: new THREE.Group(),
  };

  private skyDomeUniforms: {
    uMilkyWayTex: { value: THREE.Texture | null };
    uStarMapTex: { value: THREE.Texture | null };
    uRestoration: { value: number };
    uSpectralMode: { value: number };
    uMonocleActive: { value: number };
    uMonocleDir: { value: THREE.Vector3 };
    uMonocleCosRadius: { value: number };
    uTime: { value: number };
  };
  private earthUniforms: {
    uDayTex: { value: THREE.Texture | null };
    uNightTex: { value: THREE.Texture | null };
    uNormalTex: { value: THREE.Texture | null };
    uSpecTex: { value: THREE.Texture | null };
    uCloudsTex: { value: THREE.Texture | null };
    uBrunetonLutTex: { value: THREE.Texture | null };
    uSunDir: { value: THREE.Vector3 };
    uTime: { value: number };
  };
  private moonUniforms: {
    uAlbedoTex: { value: THREE.Texture | null };
    uDispTex: { value: THREE.Texture | null };
    uSunDir: { value: THREE.Vector3 };
  };
  private nebulaUniforms: {
    uOrionTex: { value: THREE.Texture | null };
    uLagoonTex: { value: THREE.Texture | null };
    uTime: { value: number };
    uSpectralMode: { value: number };
    uIsBlackHole: { value: number };
  };
  private localBubbleUniforms: {
    uDustTex: { value: THREE.Texture | null };
    uTime: { value: number };
    uSpectralMode: { value: number };
    uWarpFactor: { value: number };
    uCoolColor: { value: THREE.Vector3 };
    uWarmColor: { value: THREE.Vector3 };
    uHotColor: { value: THREE.Vector3 };
    uCoronaInner: { value: THREE.Vector3 };
    uCoronaOuter: { value: THREE.Vector3 };
  };
  private planetarySkyUniforms: {
    uTime: { value: number };
    uAtmoZenith: { value: THREE.Vector3 };
    uAtmoHorizon: { value: THREE.Vector3 };
    uSunColor: { value: THREE.Vector3 };
    uSunAngularRadius: { value: number };
    uHasRings: { value: number };
    uPaletteId: { value: number };
    uBrunetonLutTex: { value: THREE.Texture | null };
  };
  private cinemaOpticsUniforms: {
    tDiffuse: { value: THREE.Texture | null };
    uTime: { value: number };
    uWarpFactor: { value: number };
  };

  private skyMoonMesh: THREE.Mesh | null = null;
  private cislunarMoonMesh: THREE.Mesh | null = null;
  private cislunarEarthMesh: THREE.Mesh | null = null;
  private betelgeuseMesh: THREE.Mesh | null = null;
  private localBubbleCavityGroup: THREE.Group | null = null;
  private deepSkySculptureGroup: THREE.Group | null = null;
  private captureHorizonSplatMesh: SplatMesh | null = null;
  private planetaryTerrainSplatMesh: SplatMesh | null = null;
  private planetaryParentBodyMesh: THREE.Mesh | null = null;
  private planetaryRingMesh: THREE.Mesh | null = null;
  private originFrustumGroup: THREE.Group | null = null;

  // Unknown until a capture or the user supplies a location
  private observerLatDeg: number | null = null;
  private observerLonDeg: number | null = null;

  private activeRegime: SpatialScaleRegime = 'earth_capture';
  private flightState: {
    active: boolean;
    startPos: THREE.Vector3;
    endPos: THREE.Vector3;
    progress: number;
    targetRegime: SpatialScaleRegime;
  } | null = null;

  private lookTarget = {
    yaw: 0,
    pitch: 0,
    curYaw: 0,
    curPitch: 0,
    velYaw: 0,
    velPitch: 0,
  };
  private targetFov = 56;
  private curFov = 56;
  private showcaseMode = false;
  private onRegimeChange?: (regime: SpatialScaleRegime) => void;
  private rafId = 0;
  private clock = new THREE.Clock();

  constructor(
    container: HTMLElement,
    onRegimeChange?: (regime: SpatialScaleRegime) => void
  ) {
    this.onRegimeChange = onRegimeChange;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x010205);

    this.camera = new THREE.PerspectiveCamera(56, 16 / 9, 0.05, 2800);
    this.camera.rotation.order = 'YXZ';
    this.localFrame = new THREE.Group();
    this.localFrame.position.copy(REGIME_CAMERA_ANCHORS.earth_capture);
    this.localFrame.add(this.camera);
    this.scene.add(this.localFrame);

    for (const r of SCALE_REGIME_ORDER) {
      this.regimeGroups[r].position.copy(REGIME_CAMERA_ANCHORS[r]);
      this.scene.add(this.regimeGroups[r]);
    }

    this.textureLoader = new THREE.TextureLoader();
    this.gltfLoader = new GLTFLoader();

    this.skyDomeUniforms = {
      uMilkyWayTex: { value: null },
      uStarMapTex: { value: null },
      uRestoration: { value: 1.0 },
      uSpectralMode: { value: 0.0 },
      uMonocleActive: { value: 0.0 },
      uMonocleDir: { value: new THREE.Vector3(0.5, 0.48, 0.0) },
      uMonocleCosRadius: { value: Math.cos(0.185) },
      uTime: { value: 0.0 },
    };
    this.earthUniforms = {
      uDayTex: { value: null },
      uNightTex: { value: null },
      uNormalTex: { value: null },
      uSpecTex: { value: null },
      uCloudsTex: { value: null },
      uBrunetonLutTex: { value: null },
      uSunDir: { value: new THREE.Vector3(0.68, 0.28, -0.58).normalize() },
      uTime: { value: 0.0 },
    };
    this.moonUniforms = {
      uAlbedoTex: { value: null },
      uDispTex: { value: null },
      uSunDir: { value: new THREE.Vector3(0.58, 0.22, 0.78).normalize() },
    };
    this.nebulaUniforms = {
      uOrionTex: { value: null },
      uLagoonTex: { value: null },
      uTime: { value: 0.0 },
      uSpectralMode: { value: 0.0 },
      uIsBlackHole: { value: 0.0 },
    };
    this.localBubbleUniforms = {
      uDustTex: { value: null },
      uTime: { value: 0.0 },
      uSpectralMode: { value: 0.0 },
      uWarpFactor: { value: 0.0 },
      uCoolColor: { value: new THREE.Vector3(0.28, 0.03, 0.005) },
      uWarmColor: { value: new THREE.Vector3(0.92, 0.24, 0.03) },
      uHotColor: { value: new THREE.Vector3(1.38, 0.78, 0.28) },
      uCoronaInner: { value: new THREE.Vector3(1.05, 0.48, 0.14) },
      uCoronaOuter: { value: new THREE.Vector3(0.68, 0.11, 0.015) },
    };
    this.planetarySkyUniforms = {
      uTime: { value: 0.0 },
      uAtmoZenith: { value: new THREE.Vector3(0.01, 0.03, 0.08) },
      uAtmoHorizon: { value: new THREE.Vector3(0.78, 0.28, 0.12) },
      uSunColor: { value: new THREE.Vector3(1.25, 0.52, 0.22) },
      uSunAngularRadius: { value: 0.095 },
      uHasRings: { value: 0.0 },
      uPaletteId: { value: 1.0 },
      uBrunetonLutTex: { value: null },
    };
    this.cinemaOpticsUniforms = {
      tDiffuse: { value: null },
      uTime: { value: 0.0 },
      uWarpFactor: { value: 0.0 },
    };

    try {
      this.renderer = new THREE.WebGLRenderer({
        antialias: false,
        powerPreference: 'high-performance',
        preserveDrawingBuffer: true,
      });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1.12;
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.domElement.style.width = '100%';
      this.renderer.domElement.style.height = '100%';
      this.renderer.domElement.style.display = 'block';
      container.appendChild(this.renderer.domElement);

      // Instantiate true @sparkjsdev/spark 3D Gaussian Splatting Renderer
      this.sparkRenderer = new SparkRenderer({
        renderer: this.renderer,
        maxStdDev: Math.sqrt(8),
        premultipliedAlpha: true,
        sortRadial: true,
      });
      this.sparkRenderer.frustumCulled = false;
      this.localFrame.add(this.sparkRenderer);

      const width = container.clientWidth || 1600;
      const height = container.clientHeight || 1000;
      this.composer = new EffectComposer(this.renderer);
      const renderPass = new RenderPass(this.scene, this.camera);
      this.composer.addPass(renderPass);

      this.bloomPass = new UnrealBloomPass(
        new THREE.Vector2(width, height),
        0.64,
        0.72,
        0.60
      );
      this.composer.addPass(this.bloomPass);

      const cinemaPass = new ShaderPass({
        uniforms: this.cinemaOpticsUniforms,
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

            vec3 anamorphicAccum = vec3(0.0);
            for (int i = -4; i <= 4; i++) {
              if (i == 0) continue;
              float offset = float(i) * 0.018;
              vec3 samp = texture2D(tDiffuse, vUv + vec2(offset, 0.0)).rgb;
              float lum = dot(samp, vec3(0.299, 0.587, 0.114));
              float brightMask = smoothstep(0.76, 1.35, lum);
              float weight = exp(-abs(float(i)) * 0.55);
              anamorphicAccum += samp * brightMask * weight;
            }
            vec3 anamorphicTint = anamorphicAccum * vec3(0.32, 0.58, 1.0) * 0.085;

            float vignette = smoothstep(0.92, 0.18, r2);
            vec3 finalCol = (baseCol + anamorphicTint) * mix(0.76, 1.0, vignette);
            gl_FragColor = vec4(finalCol, 1.0);
          }
        `,
      });
      this.composer.addPass(cinemaPass);

      const outputPass = new OutputPass();
      this.composer.addPass(outputPass);

      this.sparkControls = new SparkControls({
        canvas: this.renderer.domElement,
      });

      this.loadAstronomicalTextures();
      this.buildGlobalPhotorealisticSkyDome();
      this.buildEarthCaptureWorld();
      this.buildCislunarWorld();
      this.buildSolarNeighborhoodWorld();
      this.buildDeepSkyWorld();
      this.buildPlanetarySurfaceWorld();
      this.originFrustumGroup = createOriginCameraFrustumIndicator();
      this.localFrame.add(this.originFrustumGroup);
      this.syncRegimeVisibility();
      this.startLoop();
    } catch {
      this.renderer = null;
    }
  }

  private loadAstronomicalTextures(): void {
    const loadTex = (
      url: string,
      onLoad: (tex: THREE.Texture) => void,
      srgb = true
    ) => {
      this.textureLoader.load(
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

    loadTex('/textures/eso_milkyway_panorama.jpg', (t) => {
      this.skyDomeUniforms.uMilkyWayTex.value = t;
    });
    loadTex('/textures/nasa_starmap_4k.jpg', (t) => {
      this.skyDomeUniforms.uStarMapTex.value = t;
    });
    // NASA Blue Marble (Nov 2004) and Black Marble 2016 lights; provenance in earth_texture_sources.json
    loadTex('/textures/earth_day_bluemarble_2004-11_8k.jpg', (t) => {
      this.earthUniforms.uDayTex.value = t;
    });
    loadTex('/textures/earth_night_blackmarble_2016_8k.jpg', (t) => {
      this.earthUniforms.uNightTex.value = t;
    });
    loadTex(
      '/textures/earth_normal.jpg',
      (t) => {
        this.earthUniforms.uNormalTex.value = t;
      },
      false
    );
    loadTex(
      '/textures/earth_specular.jpg',
      (t) => {
        this.earthUniforms.uSpecTex.value = t;
      },
      false
    );
    loadTex('/textures/earth_clouds.png', (t) => {
      this.earthUniforms.uCloudsTex.value = t;
    });
    loadTex('/textures/lro_moon_2k.jpg', (t) => {
      this.moonUniforms.uAlbedoTex.value = t;
    });
    loadTex(
      '/textures/lro_moon_disp.jpg',
      (t) => {
        this.moonUniforms.uDispTex.value = t;
      },
      false
    );
    loadTex('/textures/eso_orion_nebula.jpg', (t) => {
      this.nebulaUniforms.uOrionTex.value = t;
    });
    loadTex('/textures/eso_lagoon_nebula.jpg', (t) => {
      this.nebulaUniforms.uLagoonTex.value = t;
      this.localBubbleUniforms.uDustTex.value = t;
    });
    this.textureLoader.load(
      BRUNETON_ATMO_LUT_URL,
      (lutTex) => {
        lutTex.wrapS = THREE.ClampToEdgeWrapping;
        lutTex.wrapT = THREE.ClampToEdgeWrapping;
        lutTex.minFilter = THREE.LinearFilter;
        lutTex.magFilter = THREE.LinearFilter;
        lutTex.colorSpace = THREE.NoColorSpace;
        lutTex.needsUpdate = true;
        this.earthUniforms.uBrunetonLutTex.value = lutTex;
        this.planetarySkyUniforms.uBrunetonLutTex.value = lutTex;
      },
      undefined,
      () => {}
    );
  }

  /**
   * 360-degree Photorealistic All-Sky Sphere attached to the camera's localFrame
   * so the 6K ESO Milky Way Panorama & NASA 4K Gaia Star Map sit at true optical infinity.
   */
  private buildGlobalPhotorealisticSkyDome(): void {
    const skyGeo = new THREE.SphereGeometry(900, 96, 64);
    const skyMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      uniforms: this.skyDomeUniforms,
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        varying vec4 vClipPos;
        void main() {
          vUv = uv;
          vClipPos = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          gl_Position = vClipPos;
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uMilkyWayTex;
        uniform sampler2D uStarMapTex;
        uniform float uRestoration;
        uniform float uSpectralMode;
        uniform float uMonocleActive;
        uniform vec3 uMonocleDir;
        uniform float uMonocleCosRadius;
        uniform float uTime;
        varying vec2 vUv;
        varying vec4 vClipPos;

        void main() {
          vec2 mwUv = vec2(fract(vUv.x + 0.48), vUv.y);
          vec3 mwCol = texture2D(uMilkyWayTex, mwUv).rgb;
          vec3 starCol = texture2D(uStarMapTex, mwUv).rgb;

          vec3 cleanMw = max(vec3(0.0), mwCol - vec3(0.042));
          vec3 cleanStars = max(vec3(0.0), starCol - vec3(0.068));

          // Deep velvet-obsidian cosmos + smooth Galactic Core dust-lane warmth (zero grainy JPEG noise)
          vec3 galacticBulge = pow(cleanMw, vec3(1.52)) * vec3(1.22, 1.04, 0.90) * 1.32;
          vec3 hAlphaKnots = vec3(1.25, 0.28, 0.48) * pow(max(0.0, cleanMw.r - cleanMw.g * 0.85), 1.6) * 1.85;

          vec3 deepSpaceBase = vec3(0.002, 0.003, 0.008);
          vec3 opticalSky =
            deepSpaceBase +
            galacticBulge +
            hAlphaKnots +
            pow(cleanStars, vec3(1.95)) * 0.72;

          float hAlphaLum = dot(opticalSky, vec3(0.55, 0.25, 0.20)) + dot(cleanMw, vec3(0.45, 0.15, 0.25)) * 0.75;
          vec3 hAlphaSky = mix(opticalSky, vec3(hAlphaLum * 2.15, hAlphaLum * 0.24, hAlphaLum * 0.62), 0.85);

          float irLum = dot(opticalSky, vec3(0.45, 0.40, 0.15)) + dot(cleanMw, vec3(0.5, 0.35, 0.15)) * 0.78;
          vec3 jwstIrSky = mix(opticalSky, vec3(irLum * 1.95, irLum * 1.12, irLum * 0.34), 0.85);

          vec3 deepRestoredSky = opticalSky * 1.55 + pow(cleanMw, vec3(1.38)) * vec3(0.28, 0.48, 0.85) * 0.48;

          vec3 lensBandSky = hAlphaSky;
          if (uSpectralMode < 0.5) {
            lensBandSky = deepRestoredSky;
          } else if (uSpectralMode >= 1.5) {
            lensBandSky = jwstIrSky;
          }

          vec2 ndc = vClipPos.xy / max(0.0001, vClipPos.w);
          vec2 screenUv = vec2(ndc.x * 0.5 + 0.5, -ndc.y * 0.5 + 0.5);
          vec2 deltaPx = (screenUv - uMonocleDir.xy) * vec2(1600.0, 1000.0);
          float distPx = length(deltaPx);
          float lensMask = uMonocleActive * (1.0 - smoothstep(126.0, 130.0, distPx));

          vec3 rawSensorVeil = vec3(0.010, 0.015, 0.030) + cleanStars * 0.08;
          vec3 outsideSky = mix(rawSensorVeil, opticalSky, uRestoration);
          vec3 finalCol = mix(outsideSky, lensBandSky, lensMask);

          gl_FragColor = vec4(finalCol, 1.0);
        }
      `,
    });

    const skyDome = new THREE.Mesh(skyGeo, skyMat);
    skyDome.rotation.z = 0.32;
    skyDome.rotation.x = 0.18;
    this.localFrame.add(skyDome);
  }

  /**
   * Helper: Creates a razor-sharp optical Point-Spread Function (PSF) Starfield
   */
  private createOpticalStarfieldPoints(
    radiusScale: number,
    includeDeepFieldCount: number
  ): THREE.Points {
    const count = Math.min(includeDeepFieldCount, 1400);
    const totalCount = NAMED_ALL_SKY_STARS_3D.length + count;
    const positions = new Float32Array(totalCount * 3);
    const colors = new Float32Array(totalCount * 3);
    const sizes = new Float32Array(totalCount);
    const spikeFlags = new Float32Array(totalCount);

    const tmpColor = new THREE.Color();

    NAMED_ALL_SKY_STARS_3D.forEach((star, idx) => {
      const [px, py, pz] = star.cartesianParsec;
      const norm = Math.hypot(px, py, pz) || 1;
      positions[idx * 3] = (px / norm) * radiusScale;
      positions[idx * 3 + 1] = Math.abs(py / norm) * radiusScale * 0.75 + 2.0;
      positions[idx * 3 + 2] = -Math.abs(pz / norm) * radiusScale - 12.0;

      tmpColor.set(star.colorHex || bpRpToStarColorHex(star.bpRp));
      colors[idx * 3] = tmpColor.r;
      colors[idx * 3 + 1] = tmpColor.g;
      colors[idx * 3 + 2] = tmpColor.b;

      const magFactor = Math.max(0.15, (5.5 - star.mag) / 5.5);
      sizes[idx] = star.name ? 14.0 + magFactor * 22.0 : 4.0 + magFactor * 7.0;
      spikeFlags[idx] = star.name && star.mag < 1.8 ? 1.0 : 0.0;
    });

    const baseIdx = NAMED_ALL_SKY_STARS_3D.length;
    for (let i = 0; i < count; i++) {
      const k = baseIdx + i;
      const h1 = Math.sin(i * 12.9898) * 43758.5453;
      const r1 = h1 - Math.floor(h1);
      const h2 = Math.sin(i * 78.233) * 43758.5453;
      const r2 = h2 - Math.floor(h2);
      const h3 = Math.sin(i * 41.17) * 43758.5453;
      const r3 = h3 - Math.floor(h3);

      const theta = r1 * Math.PI * 2;
      const phi = Math.acos(1 - 2 * r2);
      const r = radiusScale * (0.68 + 0.62 * r3);

      positions[k * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[k * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[k * 3 + 2] = -Math.abs(r * Math.cos(phi)) - 8.0;

      if (r3 > 0.82) {
        tmpColor.setRGB(0.66, 0.82, 1.0);
      } else if (r3 > 0.45) {
        tmpColor.setRGB(0.94, 0.96, 1.0);
      } else if (r3 > 0.18) {
        tmpColor.setRGB(1.0, 0.88, 0.70);
      } else {
        tmpColor.setRGB(1.0, 0.64, 0.42);
      }

      colors[k * 3] = tmpColor.r;
      colors[k * 3 + 1] = tmpColor.g;
      colors[k * 3 + 2] = tmpColor.b;

      sizes[k] = 1.8 + Math.pow(r1, 5.0) * 9.5;
      spikeFlags[k] = r1 > 0.992 ? 1.0 : 0.0;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    geo.setAttribute('aSpike', new THREE.BufferAttribute(spikeFlags, 1));

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexColors: true,
      vertexShader: /* glsl */ `
        attribute float aSize;
        attribute float aSpike;
        varying vec3 vColor;
        varying float vSpike;
        void main() {
          vColor = color;
          vSpike = aSpike;
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          gl_Position = projectionMatrix * mvPosition;
          gl_PointSize = clamp(aSize * (34.0 / max(1.0, -mvPosition.z)), 1.2, 38.0);
        }
      `,
      fragmentShader: /* glsl */ `
        varying vec3 vColor;
        varying float vSpike;
        void main() {
          vec2 uv = gl_PointCoord * 2.0 - 1.0;
          float d = length(uv);
          if (d > 1.0) discard;

          float core = exp(-d * d * 48.0);
          float halo = exp(-d * 7.5) * 0.26;

          float spikes = 0.0;
          if (vSpike > 0.5) {
            float sx = exp(-abs(uv.y) * 42.0) * exp(-abs(uv.x) * 3.8);
            float sy = exp(-abs(uv.x) * 42.0) * exp(-abs(uv.y) * 3.8);
            vec2 rot1 = vec2(uv.x * 0.5 - uv.y * 0.866, uv.x * 0.866 + uv.y * 0.5);
            vec2 rot2 = vec2(uv.x * 0.5 + uv.y * 0.866, -uv.x * 0.866 + uv.y * 0.5);
            float s6 = (exp(-abs(rot1.y) * 52.0) * exp(-abs(rot1.x) * 4.5) +
                        exp(-abs(rot2.y) * 52.0) * exp(-abs(rot2.x) * 4.5)) * 0.35;
            spikes = (sx + sy) * 0.65 + s6;
          }

          float intensity = core + halo + spikes;
          vec3 col = mix(vColor, vec3(1.0), core * 0.85) * intensity;
          gl_FragColor = vec4(col, clamp(intensity, 0.0, 1.0));
        }
      `,
    });

    return new THREE.Points(geo, mat);
  }

  /**
   * GPGPU-Driven 3D Curl-Noise Magnetohydrodynamic (MHD) Plasma & Coronal Ejection Stream.
   */
  private createGPGPUCurlPlasmaParticles(
    center: THREE.Vector3,
    innerRadius: number,
    outerRadius: number,
    particleCount = 14000
  ): THREE.Points {
    const positions = new Float32Array(particleCount * 3);
    const seeds = new Float32Array(particleCount * 4);

    for (let i = 0; i < particleCount; i++) {
      const h1 = Math.sin(i * 19.19) * 43758.5453;
      const u = h1 - Math.floor(h1);
      const h2 = Math.sin(i * 73.73) * 43758.5453;
      const v = h2 - Math.floor(h2);
      const h3 = Math.sin(i * 37.37) * 43758.5453;
      const w = h3 - Math.floor(h3);

      const theta = u * Math.PI * 2.0;
      const phi = Math.acos(2.0 * v - 1.0);
      const r = innerRadius + Math.pow(w, 1.65) * (outerRadius - innerRadius);

      positions[i * 3] = center.x + r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = center.y + r * Math.sin(phi) * Math.sin(theta) * 0.72;
      positions[i * 3 + 2] = center.z + r * Math.cos(phi);

      seeds[i * 4] = u;
      seeds[i * 4 + 1] = v;
      seeds[i * 4 + 2] = w;
      seeds[i * 4 + 3] = r;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 4));

    const mat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: this.localBubbleUniforms.uTime,
        uWarmColor: this.localBubbleUniforms.uWarmColor,
        uHotColor: this.localBubbleUniforms.uHotColor,
        uCoronaOuter: this.localBubbleUniforms.uCoronaOuter,
        uCenter: { value: center.clone() },
      },
      vertexShader: /* glsl */ `
        attribute vec4 aSeed;
        uniform float uTime;
        uniform vec3 uCenter;
        varying float vEnergy;
        varying float vRadialNorm;

        vec3 curlFlow(vec3 p, float t) {
          vec3 q = p * 0.55 + vec3(t * 0.14, -t * 0.11, t * 0.09);
          vec3 n1 = sin(q * 2.1 + cos(q.yzx * 1.7));
          vec3 n2 = cos(q.zxy * 3.3 - sin(q * 2.4 + t * 0.3));
          return cross(n1, n2);
        }

        void main() {
          vec3 rel = position - uCenter;
          float r = length(rel);
          float angle = uTime * (0.18 / max(1.2, r * 0.35)) + aSeed.x * 6.28318;

          float cs = cos(angle * 0.25);
          float sn = sin(angle * 0.25);
          vec3 rotated = vec3(
            rel.x * cs - rel.z * sn,
            rel.y,
            rel.x * sn + rel.z * cs
          );

          vec3 curl = curlFlow(rotated, uTime);
          vec3 displaced = uCenter + rotated + curl * (0.38 + aSeed.z * 0.55);

          vEnergy = clamp(0.35 + 0.65 * sin(uTime * 1.6 + aSeed.y * 12.0) + length(curl) * 0.35, 0.0, 1.4);
          vRadialNorm = clamp((r - 2.6) / 6.0, 0.0, 1.0);

          vec4 mvPos = modelViewMatrix * vec4(displaced, 1.0);
          gl_Position = projectionMatrix * mvPos;
          gl_PointSize = clamp((1.1 + aSeed.x * 2.1) * (12.0 / max(2.0, -mvPos.z)), 0.9, 3.8);
        }
      `,
      fragmentShader: /* glsl */ `
        uniform vec3 uWarmColor;
        uniform vec3 uHotColor;
        uniform vec3 uCoronaOuter;
        varying float vEnergy;
        varying float vRadialNorm;

        void main() {
          vec2 uv = gl_PointCoord * 2.0 - 1.0;
          float d = length(uv);
          if (d > 1.0) discard;

          float glow = exp(-d * d * 16.0) * (1.0 - vRadialNorm * 0.72);
          vec3 col = mix(uHotColor, uWarmColor, smoothstep(0.0, 0.55, vRadialNorm));
          col = mix(col, uCoronaOuter, smoothstep(0.45, 1.0, vRadialNorm));
          float alpha = clamp(glow * vEnergy * 0.24, 0.0, 0.45);
          gl_FragColor = vec4(col * glow * vEnergy * 0.52, alpha);
        }
      `,
    });

    return new THREE.Points(geo, mat);
  }

  /**
   * Helper: Creates a photorealistic 3D Moon sphere using NASA Lunar Reconnaissance Orbiter (LRO)
   * color albedo + LOLA laser altimeter displacement relief + procedural micro-crater normal detail + Lommel-Seeliger BRDF.
   */
  private createPhotorealisticMoonMesh(radius: number): THREE.Mesh {
    const geo = new THREE.SphereGeometry(radius, 160, 160);
    const mat = new THREE.ShaderMaterial({
      uniforms: this.moonUniforms,
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

        float hash2(vec2 p) {
          return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
        }

        float noise2(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(
            mix(hash2(i), hash2(i + vec2(1.0, 0.0)), f.x),
            mix(hash2(i + vec2(0.0, 1.0)), hash2(i + vec2(1.0, 1.0)), f.x),
            f.y
          );
        }

        float microRelief(vec2 uv) {
          vec2 p = uv * vec2(180.0, 90.0);
          float h = 0.0;
          float a = 0.5;
          for (int i = 0; i < 4; i++) {
            float n = noise2(p);
            float crater = abs(n * 2.0 - 1.0);
            h += a * mix(n, crater * crater, 0.65);
            p *= 2.15;
            a *= 0.48;
          }
          return h;
        }

        void main() {
          vec3 albedo = texture2D(uAlbedoTex, vUv).rgb;

          vec2 texel = vec2(1.0 / 2048.0, 1.0 / 1024.0);
          float hL = texture2D(uDispTex, vUv - vec2(texel.x, 0.0)).r;
          float hR = texture2D(uDispTex, vUv + vec2(texel.x, 0.0)).r;
          float hD = texture2D(uDispTex, vUv - vec2(0.0, texel.y)).r;
          float hU = texture2D(uDispTex, vUv + vec2(0.0, texel.y)).r;

          float mL = microRelief(vUv - vec2(0.0008, 0.0));
          float mR = microRelief(vUv + vec2(0.0008, 0.0));
          float mD = microRelief(vUv - vec2(0.0, 0.0008));
          float mU = microRelief(vUv + vec2(0.0, 0.0008));

          vec3 bumpOffset = vec3(
            (hL - hR) * 3.8 + (mL - mR) * 0.55,
            (hD - hU) * 3.8 + (mD - mU) * 0.55,
            0.0
          );
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
   * Builds a subtle atmospheric horizon mist layer (`SplatMesh`) in `earth_capture`
   * and updates the Earthly Horizon Landscape Dome uniform (`uIsSouthern`) for Sahara vs. Aoraki New Zealand.
   */
  private rebuildCaptureHorizonSplatMesh(latDeg: number, lonDeg: number): void {
    const group = this.regimeGroups.earth_capture;
    if (this.captureHorizonSplatMesh) {
      group.remove(this.captureHorizonSplatMesh);
      this.captureHorizonSplatMesh.dispose();
      this.captureHorizonSplatMesh = null;
    }

    const isSouthernAlpsNZ = latDeg < -34.0 && lonDeg > 160.0;
    const horizonDome = group.getObjectByName('captureHorizonLandscapeDome') as THREE.Mesh | undefined;
    if (horizonDome) {
      const mat = horizonDome.material as THREE.ShaderMaterial;
      if (mat.uniforms?.uIsSouthern) {
        mat.uniforms.uIsSouthern.value = isSouthernAlpsNZ ? 1.0 : 0.0;
      }
    }
  }

  /**
   * REGIME 0: Night Sky (62 hand-entered named stars plus a procedural, hash-generated fill; not Gaia data)
   */
  private buildEarthCaptureWorld(): void {
    const group = this.regimeGroups.earth_capture;

    // Named bright stars plus procedural fill points, drawn with Airy-disk & diffraction spikes
    const stars = this.createOpticalStarfieldPoints(58.0, 2800);
    group.add(stars);
  }

  /**
   * Updates the observer's geographic coordinates (`latDeg`, `lonDeg`) across all 3D regimes:
   * - Updates the Earthly Horizon Landscape Dome for the capture site
   * - Rotates Planet Earth in Cislunar space so the observer's continent/site faces the H5 sightline
   * - Inverts Lunar orientation when observing from the Southern Hemisphere (`latDeg < 0`, e.g. New Zealand)
   */
  public setObserverCoordinates(latDeg: number, lonDeg: number): void {
    if (
      this.observerLatDeg !== null &&
      this.observerLonDeg !== null &&
      Math.abs(latDeg - this.observerLatDeg) < 0.01 &&
      Math.abs(lonDeg - this.observerLonDeg) < 0.01
    ) {
      return;
    }
    this.observerLatDeg = latDeg;
    this.observerLonDeg = lonDeg;
    this.rebuildCaptureHorizonSplatMesh(latDeg, lonDeg);

    const isSouthern = latDeg < 0;
    if (this.skyMoonMesh) {
      this.skyMoonMesh.rotation.z = isSouthern ? Math.PI : 0.0;
    }
    if (this.cislunarMoonMesh) {
      this.cislunarMoonMesh.rotation.z = isSouthern ? Math.PI * 0.85 : 0.0;
    }
    if (this.cislunarEarthMesh) {
      // Orient Earth's 3D globe so the observer's continent faces the camera without exposing the UV seam or polar pinch
      if (isSouthern) {
        this.cislunarEarthMesh.rotation.set(-0.88, -1.15, 0.18);
      } else {
        this.cislunarEarthMesh.rotation.set(-0.55, 1.68, -0.24);
      }
    }
  }

  /**
   * REGIME 1: Cislunar Orbital Space (384,400 km — Orbital Sunrise over Earth's Rayleigh Limb + High-Relief LRO Moon)
   */
  private buildCislunarWorld(): void {
    const group = this.regimeGroups.cislunar;

    group.add(this.createOpticalStarfieldPoints(85.0, 2600));

    const earthRadius = 13.5;
    const earthCenter = new THREE.Vector3(-11.8, -16.8, -28.0);

    const earthGeo = new THREE.SphereGeometry(earthRadius, 160, 160);
    const earthMat = new THREE.ShaderMaterial({
      uniforms: this.earthUniforms,
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
          // Rotate local sampling coordinates so the Mediterranean / Sahara / Atlantic equatorial belt faces the camera
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

          vec3 dayCol = pow(textureGrad(uDayTex, eqUv, uvDx, uvDy).rgb, vec3(1.32));
          vec3 nightLights = textureGrad(uNightTex, eqUv, uvDx, uvDy).rgb;
          float specMask = textureGrad(uSpecTex, eqUv, uvDx, uvDy).r;
          float clouds = textureGrad(uCloudsTex, eqUv, uvDx, uvDy).r;
          float cloudsShadow = textureGrad(uCloudsTex, eqUv - vec2(0.0018, 0.0012), uvDx, uvDy).r;

          vec3 N = normalize(vNormal);
          vec3 L = normalize(uSunDir);
          vec3 V = normalize(vViewDir);

          float sunDot = dot(N, L);
          float dayFactor = smoothstep(-0.08, 0.22, sunDot);
          // Tight stratospheric Rayleigh/Mie sunset band along the orbital terminator
          float twilightFactor = exp(-pow((sunDot + 0.02) * 9.0, 2.0));

          vec3 H = normalize(L + V);
          float spec = pow(max(0.0, dot(N, H)), 72.0) * specMask * max(0.0, sunDot) * (1.0 - clouds * 0.85);

          // Deep cobalt ocean & rich continental albedo under directional sunlight
          vec3 oceanTint = mix(dayCol, vec3(0.025, 0.095, 0.24), specMask * 0.55);
          vec3 surfaceAlbedo = oceanTint * (1.0 - cloudsShadow * 0.48);
          vec3 cloudLayer = vec3(0.92, 0.95, 1.0) * clouds * (0.85 + 0.25 * twilightFactor);
          vec3 litEarth = mix(surfaceAlbedo, cloudLayer, clamp(clouds * 0.84, 0.0, 1.0)) * max(0.03, sunDot * 1.05)
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
    this.cislunarEarthMesh = earthMesh;

    const atmoGeo = new THREE.SphereGeometry(earthRadius * 1.022, 160, 160);
    const atmoMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uSunDir: this.earthUniforms.uSunDir,
        uBrunetonLutTex: this.earthUniforms.uBrunetonLutTex,
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
    const moonMesh = this.createPhotorealisticMoonMesh(moonRadius);
    moonMesh.position.copy(moonCenter);
    moonMesh.rotation.y = -0.85;
    group.add(moonMesh);
    this.cislunarMoonMesh = moonMesh;

    this.gltfLoader.load(
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
  }

  /**
   * REGIME 2: Stellar & Planetary System Closeup + Real @sparkjsdev/spark 3D Gaussian Splat Local Bubble Chimney (`lb_splats.json`)
   * Zero polygon wireframes: the O'Neill et al. (2024) Harvard CfA Local Bubble Chimney is rendered as 14,281 soft 3D Gaussian Splats.
   */
  private buildSolarNeighborhoodWorld(): void {
    const group = this.regimeGroups.solar_neighborhood;

    group.add(this.createOpticalStarfieldPoints(56.0, 2600));

    const starRadius = 2.62;
    const starCenter = new THREE.Vector3(-0.35, 0.12, -9.2);
    const starGeo = new THREE.SphereGeometry(starRadius, 160, 160);
    const starMat = new THREE.ShaderMaterial({
      uniforms: {
        uTime: this.localBubbleUniforms.uTime,
        uSpectralMode: this.localBubbleUniforms.uSpectralMode,
        uCoolColor: this.localBubbleUniforms.uCoolColor,
        uWarmColor: this.localBubbleUniforms.uWarmColor,
        uHotColor: this.localBubbleUniforms.uHotColor,
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
        uniform float uSpectralMode;
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
    const betelgeuse = new THREE.Mesh(starGeo, starMat);
    betelgeuse.position.copy(starCenter);
    group.add(betelgeuse);
    this.betelgeuseMesh = betelgeuse;

    const coronaPlaneSize = starRadius * 5.8;
    const coronaGeo = new THREE.PlaneGeometry(coronaPlaneSize, coronaPlaneSize);
    const coronaMat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: this.localBubbleUniforms.uTime,
        uCoronaInner: this.localBubbleUniforms.uCoronaInner,
        uCoronaOuter: this.localBubbleUniforms.uCoronaOuter,
      },
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        void main() {
          vUv = uv * 2.0 - 1.0;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */ `
        uniform float uTime;
        uniform vec3 uCoronaInner;
        uniform vec3 uCoronaOuter;
        varying vec2 vUv;

        float hash(vec2 p) {
          return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
        }

        float noise2D(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(
            mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), f.x),
            mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
            f.y
          );
        }

        float fbm2(vec2 p) {
          float v = 0.0;
          float a = 0.5;
          for (int i = 0; i < 5; i++) {
            v += a * noise2D(p);
            p = p * 2.15 + vec2(3.7, 1.9);
            a *= 0.5;
          }
          return v;
        }

        void main() {
          float r = length(vUv);
          if (r > 0.98) discard;

          float innerDiskMask = smoothstep(0.30, 0.35, r);
          float distFromLimb = max(0.0, r - 0.335);
          float angle = atan(vUv.y, vUv.x);

          float fineRays = fbm2(vec2(angle * 18.0, r * 4.5 - uTime * 0.09));
          float loopArches = sin(angle * 14.0 + fbm2(vUv * 6.0 + uTime * 0.05) * 4.0) * 0.5 + 0.5;
          float prominence = fbm2(vec2(angle * 11.0 + uTime * 0.03, r * 8.5 - uTime * 0.14));

          float outerWindow = smoothstep(0.96, 0.36, r) * innerDiskMask;
          float innerChromosphere = exp(-distFromLimb * 11.5) * (0.65 + 0.65 * prominence * loopArches);
          float outerCorona = exp(-distFromLimb * 4.5) * (0.32 + 0.62 * fineRays) * outerWindow;

          float totalGlow = (innerChromosphere * 0.85 + outerCorona * 0.42) * outerWindow;
          vec3 col = mix(uCoronaOuter, uCoronaInner, clamp(innerChromosphere * 1.1, 0.0, 1.0)) * totalGlow;
          gl_FragColor = vec4(col, clamp(totalGlow, 0.0, 1.0));
        }
      `,
    });
    const coronaMesh = new THREE.Mesh(coronaGeo, coronaMat);
    coronaMesh.position.set(starCenter.x, starCenter.y, starCenter.z - 0.15);
    group.add(coronaMesh);

    const ringGeo = new THREE.RingGeometry(starRadius * 1.28, starRadius * 2.95, 160, 12);
    const ringMat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: this.localBubbleUniforms.uTime,
        uInnerRadius: { value: starRadius * 1.28 },
        uOuterRadius: { value: starRadius * 2.95 },
        uCoolColor: this.localBubbleUniforms.uCoolColor,
        uWarmColor: this.localBubbleUniforms.uWarmColor,
        uHotColor: this.localBubbleUniforms.uHotColor,
      },
      vertexShader: /* glsl */ `
        varying vec3 vLocalPos;
        void main() {
          vLocalPos = position;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */ `
        uniform float uTime;
        uniform float uInnerRadius;
        uniform float uOuterRadius;
        uniform vec3 uCoolColor;
        uniform vec3 uWarmColor;
        uniform vec3 uHotColor;
        varying vec3 vLocalPos;

        float hash(vec2 p) {
          return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
        }

        float noise2D(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          return mix(
            mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), f.x),
            mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
            f.y
          );
        }

        void main() {
          float r = length(vLocalPos.xy);
          float normR = clamp((r - uInnerRadius) / (uOuterRadius - uInnerRadius), 0.0, 1.0);
          float angle = atan(vLocalPos.y, vLocalPos.x);

          float spiral1 = noise2D(vec2(normR * 28.0, angle * 3.0 - uTime * 0.45 / (0.4 + normR)));
          float spiral2 = noise2D(vec2(normR * 64.0, angle * 6.0 - uTime * 0.75 / (0.3 + normR)));
          float filaments = smoothstep(0.25, 0.85, spiral1 * 0.6 + spiral2 * 0.4);

          float envelope = smoothstep(0.0, 0.14, normR) * smoothstep(1.0, 0.22, normR);
          float doppler = 0.72 + 0.42 * cos(angle + 0.45);

          vec3 col = mix(uCoolColor, uWarmColor, filaments);
          col = mix(col, uHotColor * 1.15, smoothstep(0.55, 0.0, normR) * filaments);
          float alpha = envelope * (0.06 + 0.22 * filaments) * doppler;

          gl_FragColor = vec4(col * alpha * 1.15, clamp(alpha, 0.0, 0.48));
        }
      `,
    });
    const plasmaRing = new THREE.Mesh(ringGeo, ringMat);
    plasmaRing.position.copy(starCenter);
    plasmaRing.rotation.x = 1.24;
    plasmaRing.rotation.y = -0.28;
    plasmaRing.rotation.z = 0.22;
    group.add(plasmaRing);

    group.add(
      this.createGPGPUCurlPlasmaParticles(
        starCenter,
        starRadius * 1.16,
        starRadius * 3.15,
        5400
      )
    );

    // 6. H6 Surrounding Cavity: Real @sparkjsdev/spark SplatMesh from Harvard CfA O'Neill et al. (2024) Local Bubble Chimney (`lb_splats.json`)
    const cavityGroup = new THREE.Group();
    cavityGroup.position.set(0.0, 0.0, -9.8);
    group.add(cavityGroup);
    this.localBubbleCavityGroup = cavityGroup;

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
  }

  /**
   * REGIME 3: Deep-Sky Relativistic Black Hole (James, von Tunzelmann, Franklin, & Thorne 2015 DNGR)
   * & Multi-Wavelength Volumetric Nebula (`@sparkjsdev/spark` SplatMesh from `nasa_nebula_splats.json`)
   */
  private buildDeepSkyWorld(): void {
    const group = this.regimeGroups.deep_sky;

    group.add(this.createOpticalStarfieldPoints(38.0, 2600));

    const nebulaCenter = new THREE.Vector3(0, 0.3, -13.5);
    group.add(this.createGPGPUCurlPlasmaParticles(nebulaCenter, 3.8, 12.8, 2200));

    const volGeo = new THREE.PlaneGeometry(31, 19.5);
    const volMat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.NormalBlending,
      uniforms: this.nebulaUniforms,
      vertexShader: /* glsl */ `
        varying vec2 vUv;
        varying vec3 vLocalPos;
        void main() {
          vUv = uv;
          vLocalPos = position;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: /* glsl */ `
        uniform sampler2D uOrionTex;
        uniform sampler2D uLagoonTex;
        uniform float uTime;
        uniform float uSpectralMode;
        uniform float uIsBlackHole;
        varying vec2 vUv;
        varying vec3 vLocalPos;

        float hash21(vec2 p) {
          p = fract(p * vec2(123.34, 456.21));
          p += dot(p, p + 45.32);
          return fract(p.x * p.y);
        }

        float noise2(vec2 p) {
          vec2 i = floor(p);
          vec2 f = fract(p);
          f = f * f * (3.0 - 2.0 * f);
          float a = hash21(i);
          float b = hash21(i + vec2(1.0, 0.0));
          float c = hash21(i + vec2(0.0, 1.0));
          float d = hash21(i + vec2(1.0, 1.0));
          return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
        }

        float fbmDisk(vec2 p) {
          float v = 0.0;
          float a = 0.5;
          for (int i = 0; i < 5; i++) {
            v += a * noise2(p);
            p *= 2.07;
            a *= 0.5;
          }
          return v;
        }

        void main() {
          vec2 p = (vUv * 2.0 - 1.0) * vec2(1.58, 1.0);

          if (uIsBlackHole > 0.5) {
            // Kip Thorne / Oliver James et al. (2015) Relativistic Kerr/Schwarzschild Geodesic Ray-Marcher (Gargantua / Sgr A*)
            vec3 ro = vec3(0.0, 0.0, 15.5);
            vec3 rd = normalize(vec3(p * 0.62, -1.0));

            // Tilt accretion disk by 9.5 deg so we see the upper & lower gravitationally lensed halos
            float tilt = 0.165;
            float ct = cos(tilt), st = sin(tilt);
            mat3 diskRot = mat3(
              1.0, 0.0, 0.0,
              0.0, ct, -st,
              0.0, st, ct
            );
            // Slight 6-deg roll for cinema framing
            float cr = cos(-0.10), sr = sin(-0.10);
            mat3 rollRot = mat3(
              cr, -sr, 0.0,
              sr, cr, 0.0,
              0.0, 0.0, 1.0
            );
            ro = diskRot * (rollRot * ro);
            rd = normalize(diskRot * (rollRot * rd));

            float rs = 1.0; // Schwarzschild radius r_s = 2GM/c^2
            vec3 pos = ro;
            vec3 vel = rd;
            vec3 hVec = cross(pos, vel);
            float h2 = dot(hVec, hVec);

            vec3 accumDisk = vec3(0.0);
            float trans = 1.0;
            bool captured = false;
            float minR = 100.0;

            for (int i = 0; i < 120; i++) {
              float r = length(pos);
              minR = min(minR, r);
              if (r < rs * 1.02) {
                captured = true;
                break;
              }
              if (r > 28.0) break;

              float dt = clamp(0.042 * r, 0.022, 0.26);
              // Relativistic null geodesic acceleration: d^2x/dlambda^2 = -1.5 * rs * h^2 * x / r^5
              vec3 accel = -1.5 * rs * h2 * pos / pow(r, 5.0);
              vec3 nextVel = normalize(vel + accel * dt);
              vec3 nextPos = pos + 0.5 * (vel + nextVel) * dt;

              // Check equatorial accretion disk plane crossing (pos.y * nextPos.y <= 0.0)
              if (pos.y * nextPos.y <= 0.0) {
                float frac = abs(pos.y) / max(0.0001, abs(pos.y - nextPos.y));
                vec3 hit = mix(pos, nextPos, clamp(frac, 0.0, 1.0));
                float diskR = length(hit.xz);
                // Relativistic accretion disk from ISCO (2.65 r_s) out to 9.2 r_s
                if (diskR > 2.62 * rs && diskR < 9.2 * rs) {
                  float rNorm = (diskR - 2.62 * rs) / (6.58 * rs);
                  float radialEnv = smoothstep(0.0, 0.08, rNorm) * exp(-rNorm * 3.4);

                  float phi = atan(hit.z, hit.x);
                  float omega = 1.65 / pow(diskR, 1.25);
                  float spiralPhase = phi + uTime * omega + 11.0 / (diskR + 0.25);

                  // Magneto-rotational instability (MRI) fine concentric rings & spiral filaments
                  float rings = 0.52 + 0.48 * sin(diskR * 32.0 + fbmDisk(vec2(diskR * 3.8, phi * 2.0)) * 5.5);
                  float streaks = fbmDisk(vec2(diskR * 5.5, spiralPhase * 2.0));
                  float density = radialEnv * mix(rings, streaks, 0.52);

                  // Relativistic Keplerian Doppler beaming D = 1 / (gamma * (1 - beta * cos(theta)))
                  vec3 tangent = normalize(vec3(-hit.z, 0.0, hit.x));
                  float beta = clamp(sqrt(0.5 * rs / diskR), 0.0, 0.58);
                  float gamma = inversesqrt(1.0 - beta * beta);
                  float cosTheta = dot(tangent, -vel);
                  float doppler = 1.0 / (gamma * (1.0 - beta * cosTheta));
                  float gravRedshift = sqrt(max(0.12, 1.0 - rs / diskR));
                  float shift = clamp(doppler * gravRedshift, 0.48, 1.85);

                  // Blackbody thermal gradient shifted by relativistic Doppler factor
                  vec3 coldRed = vec3(0.88, 0.18, 0.03);
                  vec3 warmGold = vec3(1.42, 0.82, 0.30);
                  vec3 hotWhiteBlue = vec3(1.75, 1.60, 1.42);
                  vec3 diskCol = mix(coldRed, warmGold, smoothstep(0.55, 1.05, shift));
                  diskCol = mix(diskCol, hotWhiteBlue, smoothstep(1.05, 1.65, shift));

                  float stepAlpha = clamp(density * 0.92, 0.0, 0.95);
                  accumDisk += trans * diskCol * pow(shift, 2.1) * stepAlpha * 1.65;
                  trans *= (1.0 - stepAlpha * 0.82);
                }
              }

              pos = nextPos;
              vel = nextVel;
            }

            // Photon sphere relativistic ring at b_crit = sqrt(27)/2 * rs (~2.598 rs impact parameter, r_photon = 1.5 rs)
            float photonRing = exp(-pow((minR - 1.52 * rs) / 0.065, 2.0)) * (captured ? 0.35 : 1.55);
            accumDisk += vec3(1.65, 1.22, 0.72) * photonRing;

            // Gravitationally lensed Einstein ring starlight for uncaptured rays
            if (!captured) {
              vec2 lensedUv = vec2(atan(vel.z, vel.x) / 6.28318 + 0.5, asin(clamp(vel.y, -1.0, 1.0)) / 3.14159 + 0.5);
              vec3 bgNeb = texture2D(uLagoonTex, lensedUv).rgb;
              float einsteinAmplification = 1.0 + 1.85 * exp(-max(0.0, minR - 1.5 * rs) * 1.4);
              accumDisk += trans * pow(max(vec3(0.0), bgNeb - 0.08), vec3(1.9)) * 0.32 * einsteinAmplification;
            }

            // Anamorphic horizontal cinema flare across the bright approaching wing
            float anamorphic = exp(-abs(p.y + 0.02) * 26.0) * exp(-abs(p.x + 0.18) * 2.4) * 0.38;
            accumDisk += vec3(1.15, 0.78, 0.42) * anamorphic;

            // Tone-map while preserving deep black shadow inside the event horizon
            vec3 finalBh = accumDisk / (vec3(1.0) + accumDisk * 0.42);
            float vignette = smoothstep(1.45, 0.25, length(p * vec2(0.72, 0.95)));
            float outAlpha = clamp((captured ? 0.98 : length(finalBh) * 1.65) * vignette, 0.0, 0.98);
            gl_FragColor = vec4(finalBh * vignette, outAlpha);
            return;
          }

          float radialVignette = smoothstep(1.0, 0.18, length((vUv * 2.0 - 1.0) * vec2(0.84, 1.02)));

          vec2 orionUv = vec2(vUv.x, 1.0 - vUv.y);
          vec3 orionCol = texture2D(uOrionTex, orionUv).rgb;
          vec3 lagoonCol = texture2D(uLagoonTex, vUv).rgb;

          vec3 orionLinear = pow(max(orionCol, vec3(0.0)), vec3(1.75)) * 0.78;
          vec3 lagoonLinear = pow(max(lagoonCol, vec3(0.0)), vec3(1.85)) * 0.22;
          vec3 combined = orionLinear + lagoonLinear;

          combined = combined / (vec3(1.0) + combined * 0.82);

          if (uSpectralMode > 0.5 && uSpectralMode < 1.5) {
            float hAlpha = dot(combined, vec3(0.62, 0.22, 0.16));
            float oiii = dot(combined, vec3(0.10, 0.52, 0.48));
            combined = vec3(hAlpha * 1.38, oiii * 0.52, hAlpha * 0.44 + oiii * 0.68);
          } else if (uSpectralMode >= 1.5) {
            float pahDust = dot(combined, vec3(0.52, 0.36, 0.12));
            float hotGas = dot(combined, vec3(0.15, 0.45, 0.40));
            combined = vec3(pahDust * 1.45, pahDust * 0.88 + hotGas * 0.22, hotGas * 0.65);
          }

          vec3 finalNebula = combined * radialVignette;
          float alpha = clamp(radialVignette * clamp(dot(combined, vec3(0.58)) * 1.45, 0.0, 0.76), 0.0, 0.76);
          gl_FragColor = vec4(finalNebula, alpha);
        }
      `,
    });

    const mainNebulaMesh = new THREE.Mesh(volGeo, volMat);
    mainNebulaMesh.position.set(0, 0.3, -15.8);
    group.add(mainNebulaMesh);

    // 4. Real @sparkjsdev/spark 3D Gaussian Splat Nebula Cloud (`nasa_nebula_splats.json`)
    const sculptureGroup = new THREE.Group();
    sculptureGroup.position.set(0.0, 0.2, -11.5);
    sculptureGroup.rotation.x = 0.24;
    sculptureGroup.rotation.y = -0.32;
    group.add(sculptureGroup);
    this.deepSkySculptureGroup = sculptureGroup;

    if (typeof fetch !== 'undefined') {
      fetch('/models/nasa_nebula_splats.json')
        .then((res) => (res.ok ? res.json() : null))
        .then((raw: number[][] | null) => {
          if (!Array.isArray(raw) || raw.length === 0) return;
          const nebulaSplatMesh = buildAnisotropicNebulaSplatMesh(raw);
          sculptureGroup.add(nebulaSplatMesh);
        })
        .catch(() => {});
    }
  }

  /**
   * REGIME 4 (`planetary_surface` — `4 · Surface World`):
   * Full-Viewport Analytical GPU Ray-Marched Planetary Worlds & Relativistic Skies (Zero Polygon Sine-Waves!).
   * Renders:
   * - Palette 0 (`lunar_regolith`): Ray-marched Shackleton Crater regolith & boulders + 3D Blue Marble Earth in sky
   * - Palette 1 (`exoplanet_terminator`): Ray-marched Proxima b glacial canyons, slope-sorted ice & M-Dwarf + binary suns
   * - Palette 2 (`titan_methane`): Ray-marched coastal cliffs & specular liquid methane waves reflecting Ringed Saturn
   * - Palette 3 (`gargantua_tidal_ocean`): Miller's Relativistic Tidal Ocean World reflecting Gargantua's warped accretion disk
   */
  private buildPlanetarySurfaceWorld(): void {
    const group = this.regimeGroups.planetary_surface;

    // 1. Crisp Zenith Starfield visible through the upper planetary atmosphere
    group.add(this.createOpticalStarfieldPoints(72.0, 2200));

    // 2. Full-Viewport 360° GPU Ray-Marched Planetary World & Relativistic Sky Sphere
    const worldGeo = new THREE.SphereGeometry(85, 128, 96);
    const worldMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      transparent: true,
      depthWrite: false,
      uniforms: {
        uTime: this.planetarySkyUniforms.uTime,
        uAtmoZenith: this.planetarySkyUniforms.uAtmoZenith,
        uAtmoHorizon: this.planetarySkyUniforms.uAtmoHorizon,
        uSunColor: this.planetarySkyUniforms.uSunColor,
        uPaletteId: this.planetarySkyUniforms.uPaletteId,
        uBrunetonLutTex: this.planetarySkyUniforms.uBrunetonLutTex,
        uEarthDayTex: this.earthUniforms.uDayTex,
        uEarthNightTex: this.earthUniforms.uNightTex,
        uEarthCloudsTex: this.earthUniforms.uCloudsTex,
        uEarthSpecTex: this.earthUniforms.uSpecTex,
        uMoonAlbedoTex: this.moonUniforms.uAlbedoTex,
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
          } else if (uPaletteId > 2.5) {
            // 3: Gargantua Relativistic Tidal Ocean World (Miller's Planet — specular ocean + distant towering tidal wave + basalt sea stacks)
            float swell = exp(-pow((p.y + 38.0 + sin(p.x * 0.06) * 3.5) / 6.5, 2.0)) * 3.2;
            float seaStacks = (a * 3.1 - 2.65) * smoothstep(5.5, 16.0, abs(p.x));
            grad = d * 0.45;
            return max(-1.95 + swell * 0.65, seaStacks);
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

              vec3 dayCol = pow(texture2D(uEarthDayTex, uv).rgb, vec3(1.15));
              vec3 nightCol = texture2D(uEarthNightTex, uv).rgb;
              vec3 clouds = texture2D(uEarthCloudsTex, uv + vec2(uTime * 0.002, 0.0)).rgb;
              float specMask = texture2D(uEarthSpecTex, uv).r;

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

          // PALETTE 3: Gargantua / Sgr A* Relativistic Black Hole & Warped Accretion Disk in the Sky (Interstellar Miller's Planet)
          vec3 bhDir = normalize(vec3(-0.08, 0.24, -0.967));
          vec3 upOrtho = normalize(vec3(0.22, 0.95, 0.21));
          vec3 rightOrtho = normalize(cross(bhDir, upOrtho));
          upOrtho = cross(rightOrtho, bhDir);

          vec2 bhUv = vec2(dot(rd - bhDir, rightOrtho), dot(rd - bhDir, upOrtho));
          float rAng = length(bhUv);
          float rShadow = 0.095;

          // Primary inclined disk + gravitationally lensed upper & lower Einstein arches
          float diskY = bhUv.y + bhUv.x * 0.14;
          float archUpper = abs(rAng - rShadow * 1.15 - 0.022 * (1.0 - clamp(bhUv.y / 0.18, 0.0, 1.0)));
          float ringlets = isReflection
            ? 0.82
            : (0.65 + 0.35 * sin(rAng * 190.0 - uTime * 1.4));
          float primarySlab = exp(-abs(diskY) * 64.0) * smoothstep(rShadow * 0.78, rShadow * 1.12, abs(bhUv.x)) * exp(-abs(bhUv.x) * 4.5) * ringlets;
          float lensedHalo = exp(-archUpper * 46.0) * smoothstep(rShadow * 0.75, rShadow * 1.02, rAng) * (0.75 + 0.25 * ringlets);
          float photonRing = exp(-pow((rAng - rShadow) / 0.0055, 2.0)) * 2.1;

          // Relativistic Doppler beaming (left side brilliant white-gold, right side redshifted amber-crimson)
          float dopplerShift = clamp(1.0 - bhUv.x * 4.2, 0.42, 1.80);
          vec3 diskColor = mix(vec3(0.92, 0.20, 0.03), vec3(1.55, 1.12, 0.58), smoothstep(0.50, 1.30, dopplerShift));
          diskColor = mix(diskColor, vec3(1.85, 1.72, 1.48), smoothstep(1.25, 1.75, dopplerShift));

          vec3 bhEmission = diskColor * (primarySlab * 1.65 + lensedHalo * 1.15 + photonRing) * pow(dopplerShift, 1.35);
          float shadowMask = smoothstep(rShadow * 0.92, rShadow * 1.04, rAng);
          return sky * shadowMask + bhEmission;
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

          float waterLevel = uPaletteId > 2.5 ? -1.78 : (uPaletteId > 1.5 ? -1.88 : (uPaletteId > 0.5 ? -1.94 : -99.0));
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
            } else if (uPaletteId < 2.5) {
              float duneMask = smoothstep(0.60, 0.84, slope);
              vec3 cliffRock = vec3(0.20, 0.12, 0.07) * strata;
              vec3 tholinSand = vec3(0.66, 0.42, 0.17) * (0.82 + 0.18 * microN.x);
              vec3 albedo = mix(cliffRock, tholinSand, duneMask);
              surfCol = albedo * uSunColor * (lommel * 1.40 + 0.08) + uSunColor * spec * 0.24;
            } else {
              vec3 wetBasalt = vec3(0.10, 0.12, 0.15) * strata;
              surfCol = wetBasalt * uSunColor * (lommel * 1.35 + 0.06) + uSunColor * spec * 0.60;
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

            vec3 deepLiquid = uPaletteId > 2.5
              ? vec3(0.012, 0.028, 0.045)
              : (uPaletteId > 1.5 ? vec3(0.028, 0.036, 0.024) : vec3(0.018, 0.042, 0.068));
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

    // 3. Invisible anchor mesh for camera telemetry projection
    const anchorGeo = new THREE.SphereGeometry(0.1, 8, 8);
    const anchorMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.0 });
    const parentBody = new THREE.Mesh(anchorGeo, anchorMat);
    parentBody.position.set(-3.6, 4.2, -19.0);
    group.add(parentBody);
    this.planetaryParentBodyMesh = parentBody;

    // 4. Real @sparkjsdev/spark SplatMesh Exospheric Dust & Volumetric Surface Mist
    this.rebuildPlanetaryTerrainSplats('exoplanet_terminator');
  }

  /**
   * Rebuilds the `@sparkjsdev/spark` 3D Gaussian Splat exospheric dust/mist (`SplatMesh`) and atmospheric shader uniforms
   * to match the astrophysical class of the selected celestial target.
   */
  private rebuildPlanetaryTerrainSplats(
    palette: PlanetaryWorldDescriptor['terrainPalette']
  ): void {
    const group = this.regimeGroups.planetary_surface;
    if (this.planetaryTerrainSplatMesh) {
      group.remove(this.planetaryTerrainSplatMesh);
      this.planetaryTerrainSplatMesh.dispose();
      this.planetaryTerrainSplatMesh = null;
    }

    if (palette === 'lunar_regolith') {
      this.planetarySkyUniforms.uAtmoZenith.value.set(0.001, 0.002, 0.006);
      this.planetarySkyUniforms.uAtmoHorizon.value.set(0.05, 0.11, 0.22);
      this.planetarySkyUniforms.uSunColor.value.set(0.95, 0.98, 1.05);
      this.planetarySkyUniforms.uSunAngularRadius.value = 0.085;
      this.planetarySkyUniforms.uHasRings.value = 0.0;
      this.planetarySkyUniforms.uPaletteId.value = 0.0;
    } else if (palette === 'titan_methane') {
      this.planetarySkyUniforms.uAtmoZenith.value.set(0.05, 0.03, 0.012);
      this.planetarySkyUniforms.uAtmoHorizon.value.set(0.84, 0.48, 0.16);
      this.planetarySkyUniforms.uSunColor.value.set(1.12, 0.80, 0.42);
      this.planetarySkyUniforms.uSunAngularRadius.value = 0.11;
      this.planetarySkyUniforms.uHasRings.value = 1.0;
      this.planetarySkyUniforms.uPaletteId.value = 2.0;
    } else if (palette === 'gargantua_tidal_ocean' || palette === 'supergiant_silicate') {
      this.planetarySkyUniforms.uAtmoZenith.value.set(0.005, 0.010, 0.024);
      this.planetarySkyUniforms.uAtmoHorizon.value.set(0.28, 0.18, 0.11);
      this.planetarySkyUniforms.uSunColor.value.set(1.35, 0.95, 0.58);
      this.planetarySkyUniforms.uSunAngularRadius.value = 0.145;
      this.planetarySkyUniforms.uHasRings.value = 0.0;
      this.planetarySkyUniforms.uPaletteId.value = 3.0;
    } else {
      // exoplanet_terminator (Proxima Centauri b / glacial canyon world)
      this.planetarySkyUniforms.uAtmoZenith.value.set(0.008, 0.018, 0.052);
      this.planetarySkyUniforms.uAtmoHorizon.value.set(0.48, 0.18, 0.10);
      this.planetarySkyUniforms.uSunColor.value.set(1.18, 0.58, 0.36);
      this.planetarySkyUniforms.uSunAngularRadius.value = 0.105;
      this.planetarySkyUniforms.uHasRings.value = 0.0;
      this.planetarySkyUniforms.uPaletteId.value = 1.0;
    }

    const terrainSplatMesh = new SplatMesh({
      constructSplats: (splats: PackedSplats) => {
        const center = new THREE.Vector3();
        const scales = new THREE.Vector3();
        const quat = new THREE.Quaternion();
        const color = new THREE.Color();

        const count = 1800;
        for (let i = 0; i < count; i++) {
          const seed1 = Math.sin(i * 12.9898) * 43758.5453;
          const seed2 = Math.sin(i * 78.233) * 43758.5453;
          const u = (seed1 - Math.floor(seed1)) - 0.5;
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
          } else if (palette === 'gargantua_tidal_ocean' || palette === 'supergiant_silicate') {
            color.setRGB(1.0, 0.78, 0.45);
          } else {
            color.setRGB(0.92, 0.56, 0.42);
          }

          splats.pushSplat(center, scales, quat, 0.016, color);
        }
      },
    });

    group.add(terrainSplatMesh);
    this.planetaryTerrainSplatMesh = terrainSplatMesh;
  }

  /**
   * Configures the `planetary_surface` (`08 World Surface`) regime for a specific astronomical target
   * and optionally flies the camera down to its surface.
   */
  public descendToPlanetarySurface(
    title: string,
    bpRp = 0.8,
    distanceLy = 4.37,
    observerPlaceName = 'Earth Observation Site'
  ): PlanetaryWorldDescriptor {
    this.setTargetAppearance(title, bpRp);
    const desc = derivePlanetaryWorldDescriptor(title, bpRp, distanceLy, observerPlaceName);
    this.rebuildPlanetaryTerrainSplats(desc.terrainPalette);

    if (this.activeRegime !== 'planetary_surface') {
      this.flightState = {
        active: true,
        startPos: this.localFrame.position.clone(),
        endPos: REGIME_CAMERA_ANCHORS.planetary_surface.clone(),
        progress: 0,
        targetRegime: 'planetary_surface',
      };
      this.syncRegimeVisibility();
    }
    return desc;
  }

  private syncRegimeVisibility(): void {
    for (const r of SCALE_REGIME_ORDER) {
      const isVisible =
        r === this.activeRegime || this.flightState?.targetRegime === r;
      this.regimeGroups[r].visible = isVisible;
    }
  }

  private startLoop(): void {
    if (typeof window === 'undefined' || !this.renderer) return;
    const tick = () => {
      if (!this.renderer) return;
      const elapsed = this.clock.getElapsedTime();

      this.skyDomeUniforms.uTime.value = elapsed;
      this.earthUniforms.uTime.value = elapsed;
      this.nebulaUniforms.uTime.value = elapsed;
      this.localBubbleUniforms.uTime.value = elapsed;
      this.planetarySkyUniforms.uTime.value = elapsed;
      this.cinemaOpticsUniforms.uTime.value = elapsed;

      const rotMult = this.showcaseMode ? 3.2 : 1.0;
      if (this.cislunarMoonMesh) {
        this.cislunarMoonMesh.rotation.y += 0.0005 * rotMult;
      }
      if (this.cislunarEarthMesh) {
        this.cislunarEarthMesh.rotation.y += 0.00018 * rotMult;
      }
      if (this.betelgeuseMesh) {
        this.betelgeuseMesh.rotation.y += 0.0006 * rotMult;
      }
      if (this.localBubbleCavityGroup) {
        this.localBubbleCavityGroup.rotation.y += 0.00045 * rotMult;
      }
      if (this.deepSkySculptureGroup) {
        this.deepSkySculptureGroup.rotation.z += 0.0008 * rotMult;
      }
      if (this.planetaryParentBodyMesh) {
        this.planetaryParentBodyMesh.rotation.y += 0.0005 * rotMult;
      }

      if (this.showcaseMode && !this.flightState?.active) {
        this.lookTarget.yaw = Math.sin(elapsed * 0.42) * 0.095;
        this.lookTarget.pitch = Math.cos(elapsed * 0.29) * 0.042;
        this.targetFov = 53 + Math.sin(elapsed * 0.35) * 2.4;
      }

      const prevYaw = this.lookTarget.curYaw;
      const prevPitch = this.lookTarget.curPitch;
      this.lookTarget.curYaw += (this.lookTarget.yaw - this.lookTarget.curYaw) * 0.14;
      this.lookTarget.curPitch +=
        (this.lookTarget.pitch - this.lookTarget.curPitch) * 0.14;
      this.lookTarget.velYaw =
        this.lookTarget.velYaw * 0.82 + (this.lookTarget.curYaw - prevYaw) * 18.0;
      this.lookTarget.velPitch =
        this.lookTarget.velPitch * 0.82 + (this.lookTarget.curPitch - prevPitch) * 18.0;
      this.curFov += (this.targetFov - this.curFov) * 0.14;

      if (this.flightState?.active) {
        this.flightState.progress = Math.min(1, this.flightState.progress + 0.048);
        const t = this.flightState.progress;
        const smoothT = t * t * (3 - 2 * t);
        this.localFrame.position.lerpVectors(
          this.flightState.startPos,
          this.flightState.endPos,
          smoothT
        );
        const warpArc = Math.sin(t * Math.PI);
        this.camera.fov = this.curFov + warpArc * 16;
        this.camera.updateProjectionMatrix();
        this.cinemaOpticsUniforms.uWarpFactor.value = warpArc;
        this.localBubbleUniforms.uWarpFactor.value = warpArc;

        if (this.flightState.progress >= 1) {
          const target = this.flightState.targetRegime;
          this.flightState = null;
          this.cinemaOpticsUniforms.uWarpFactor.value = 0;
          this.localBubbleUniforms.uWarpFactor.value = 0;
          this.jumpToRegime(target);
        }
      } else {
        this.cinemaOpticsUniforms.uWarpFactor.value *= 0.85;
        this.localBubbleUniforms.uWarpFactor.value *= 0.85;
        this.camera.rotation.y = this.lookTarget.curYaw;
        this.camera.rotation.x = this.lookTarget.curPitch;
        if (Math.abs(this.camera.fov - this.curFov) > 0.02) {
          this.camera.fov = this.curFov;
          this.camera.updateProjectionMatrix();
        }
      }

      if (this.originFrustumGroup) {
        this.originFrustumGroup.visible = false;
      }

      this.sparkXr?.updateControllers(this.camera);
      if (this.composer) {
        this.composer.render();
      } else {
        this.renderer.render(this.scene, this.camera);
      }

      this.rafId = window.requestAnimationFrame(tick);
    };
    this.rafId = window.requestAnimationFrame(tick);
  }

  public getCameraTelemetry(
    viewportWidth: number,
    viewportHeight: number
  ): {
    yawDeg: number;
    pitchDeg: number;
    fovDeg: number;
    velYaw: number;
    velPitch: number;
    warpFactor: number;
    subjectScreen: { x: number; y: number; visible: boolean };
  } {
    const rawAz = ((-this.lookTarget.curYaw * 180) / Math.PI + 210) % 360;
    const yawDeg = (rawAz + 360) % 360;
    const pitchDeg = Math.round(((this.lookTarget.curPitch * 180) / Math.PI + 24) * 10) / 10;
    const fovDeg = Math.round(this.curFov);

    let subjectWorld = new THREE.Vector3(0, 0, -10);
    if (this.activeRegime === 'cislunar' && this.cislunarMoonMesh) {
      subjectWorld = this.cislunarMoonMesh.getWorldPosition(new THREE.Vector3());
    } else if (this.activeRegime === 'solar_neighborhood' && this.betelgeuseMesh) {
      subjectWorld = this.betelgeuseMesh.getWorldPosition(new THREE.Vector3());
    } else if (this.activeRegime === 'deep_sky') {
      subjectWorld = new THREE.Vector3(0, 0.3, -13.5).add(
        REGIME_CAMERA_ANCHORS.deep_sky
      );
    } else if (this.activeRegime === 'planetary_surface' && this.planetaryParentBodyMesh) {
      subjectWorld = this.planetaryParentBodyMesh.getWorldPosition(new THREE.Vector3());
    }

    this.camera.updateMatrixWorld();
    const camSpace = subjectWorld.clone().applyMatrix4(this.camera.matrixWorldInverse);
    let subjectScreen = { x: viewportWidth * 0.5, y: viewportHeight * 0.5, visible: false };
    if (this.activeRegime !== 'earth_capture' && camSpace.z < -0.5) {
      const ndc = subjectWorld.clone().project(this.camera);
      subjectScreen = {
        x: (ndc.x * 0.5 + 0.5) * viewportWidth,
        y: (-ndc.y * 0.5 + 0.5) * viewportHeight,
        visible: true,
      };
    }

    return {
      yawDeg,
      pitchDeg,
      fovDeg,
      velYaw: this.lookTarget.velYaw,
      velPitch: this.lookTarget.velPitch,
      warpFactor: this.cinemaOpticsUniforms.uWarpFactor.value,
      subjectScreen,
    };
  }

  public dragLook(deltaPixelsX: number, deltaPixelsY: number): void {
    const fovScale = this.curFov / 56;
    const sensitivity = 0.0032 * fovScale;
    this.lookTarget.yaw -= deltaPixelsX * sensitivity;
    this.lookTarget.pitch = Math.max(
      -1.15,
      Math.min(1.15, this.lookTarget.pitch - deltaPixelsY * sensitivity)
    );
    if (this.activeRegime === 'cislunar' && this.cislunarMoonMesh) {
      this.cislunarMoonMesh.rotation.y += deltaPixelsX * 0.004;
      this.cislunarMoonMesh.rotation.x = Math.max(
        -0.6,
        Math.min(0.6, this.cislunarMoonMesh.rotation.x + deltaPixelsY * 0.003)
      );
    } else if (this.activeRegime === 'solar_neighborhood') {
      if (this.betelgeuseMesh) {
        this.betelgeuseMesh.rotation.y += deltaPixelsX * 0.004;
      }
      if (this.localBubbleCavityGroup) {
        this.localBubbleCavityGroup.rotation.y += deltaPixelsX * 0.0035;
        this.localBubbleCavityGroup.rotation.x = Math.max(
          -0.6,
          Math.min(0.6, this.localBubbleCavityGroup.rotation.x + deltaPixelsY * 0.0025)
        );
      }
    } else if (this.activeRegime === 'deep_sky' && this.deepSkySculptureGroup) {
      this.deepSkySculptureGroup.rotation.z += deltaPixelsX * 0.0045;
      this.deepSkySculptureGroup.rotation.x = Math.max(
        -1.65,
        Math.min(-0.65, this.deepSkySculptureGroup.rotation.x + deltaPixelsY * 0.003)
      );
    }
  }

  public zoomByWheel(deltaY: number): void {
    const step = Math.max(-120, Math.min(120, deltaY)) * 0.045;
    this.targetFov = Math.max(18, Math.min(74, this.targetFov + step));
    if (this.activeRegime !== 'earth_capture') {
      const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(this.camera.quaternion);
      this.localFrame.position.addScaledVector(forward, -step * 0.22);
    }
  }

  public getFovDeg(): number {
    return Math.round(this.targetFov);
  }

  public nudgeMovement(dx: number, dy: number, dz: number): void {
    if (this.activeRegime === 'earth_capture') {
      this.lookTarget.yaw -= dx * 0.14;
      this.lookTarget.pitch = Math.max(
        -1.15,
        Math.min(1.15, this.lookTarget.pitch - dz * 0.14 + dy * 0.14)
      );
      return;
    }
    const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(this.camera.quaternion);
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const up = new THREE.Vector3(0, 1, 0);
    this.localFrame.position.addScaledVector(right, dx);
    this.localFrame.position.addScaledVector(up, dy);
    this.localFrame.position.addScaledVector(forward, dz);
  }

  public nudgeLook(dYaw: number, dPitch: number): void {
    this.lookTarget.yaw += dYaw;
    this.lookTarget.pitch = Math.max(-1.15, Math.min(1.15, this.lookTarget.pitch + dPitch));
  }

  public getSkyTargetPosition(
    id: string,
    title: string,
    raDeg: number,
    decDeg: number
  ): THREE.Vector3 {
    const lowerId = id.toLowerCase();
    const lowerTitle = title.toLowerCase();
    if (lowerTitle.includes('moon')) {
      return new THREE.Vector3(5.4, 2.8, -26.0);
    }
    if (lowerId.includes('alpha_centauri') || lowerTitle.includes('alpha centauri')) {
      return new THREE.Vector3(-3.8, 4.4, -25.4);
    }
    if (lowerId.includes('carina') || lowerTitle.includes('carina')) {
      return new THREE.Vector3(2.8, 3.6, -25.6);
    }
    if (lowerId.includes('acrux') || lowerTitle.includes('acrux') || lowerTitle.includes('southern cross')) {
      return new THREE.Vector3(-0.6, 5.1, -25.4);
    }
    if (lowerId.includes('canopus') || lowerTitle.includes('canopus')) {
      return new THREE.Vector3(6.8, 1.4, -25.8);
    }
    if (lowerId.includes('altair') || lowerTitle.includes('altair')) {
      return new THREE.Vector3(-6.4, 4.6, -25.0);
    }
    if (lowerId.includes('tarazed') || lowerTitle.includes('tarazed')) {
      return new THREE.Vector3(-8.8, 6.2, -25.2);
    }
    if (lowerId.includes('alshain') || lowerTitle.includes('alshain')) {
      return new THREE.Vector3(-4.2, 2.8, -25.2);
    }
    if (lowerId.includes('albireo') || lowerTitle.includes('albireo')) {
      return new THREE.Vector3(-10.5, 8.2, -24.8);
    }
    if (lowerId.includes('deneb_algedi') || lowerTitle.includes('deneb algedi')) {
      return new THREE.Vector3(1.8, 0.2, -25.8);
    }
    if (lowerId.includes('sadalsuud') || lowerTitle.includes('sadalsuud')) {
      return new THREE.Vector3(0.4, 4.2, -25.6);
    }
    if (lowerId.includes('dabih') || lowerTitle.includes('dabih')) {
      return new THREE.Vector3(-2.6, -1.2, -25.8);
    }
    if (lowerId.includes('algedi') || lowerTitle.includes('algedi')) {
      return new THREE.Vector3(-4.8, -0.2, -25.8);
    }
    if (lowerId.includes('nashira') || lowerTitle.includes('nashira')) {
      return new THREE.Vector3(0.2, -1.6, -25.8);
    }
    if (lowerId.includes('fomalhaut') || lowerTitle.includes('fomalhaut')) {
      return new THREE.Vector3(4.2, -3.4, -26.2);
    }
    if (lowerId.includes('saturn') || lowerTitle.includes('saturn')) {
      return new THREE.Vector3(3.1, 5.4, -25.8);
    }
    if (lowerId.includes('uranus') || lowerTitle.includes('uranus')) {
      return new THREE.Vector3(8.8, 3.6, -25.5);
    }
    if (lowerId.includes('vega') || lowerTitle.includes('vega')) {
      return new THREE.Vector3(-10.2, 7.4, -24.5);
    }
    if (lowerId.includes('betelgeuse') || lowerTitle.includes('betelgeuse')) {
      return new THREE.Vector3(-4.2, 3.8, -25.5);
    }
    if (lowerId.includes('rigel') || lowerTitle.includes('rigel')) {
      return new THREE.Vector3(-1.4, -2.1, -26.0);
    }
    if (lowerId.includes('aldebaran') || lowerTitle.includes('aldebaran')) {
      return new THREE.Vector3(4.6, 5.6, -25.2);
    }
    if (lowerId.includes('sirius') || lowerTitle.includes('sirius')) {
      return new THREE.Vector3(-7.6, -3.2, -25.5);
    }
    if (lowerId.includes('jupiter') || lowerTitle.includes('jupiter')) {
      return new THREE.Vector3(2.4, 6.4, -25.4);
    }
    if (lowerId.includes('sgr_a') || lowerTitle.includes('sagittarius') || lowerTitle.includes('black hole')) {
      return new THREE.Vector3(0.8, 3.2, -25.4);
    }
    const normRa = (((raDeg - 80 + 540) % 360) - 180) / 180;
    const normDec = Math.max(-45, Math.min(65, decDeg)) / 65;
    return new THREE.Vector3(normRa * 11.5, normDec * 6.5 + 1.2, -26.0);
  }

  public projectSkyTargetToScreen(
    id: string,
    title: string,
    raDeg: number,
    decDeg: number,
    viewportWidth: number,
    viewportHeight: number
  ): { x: number; y: number; visible: boolean } {
    if (
      viewportWidth > 0 &&
      viewportHeight > 0 &&
      Math.abs(this.camera.aspect - viewportWidth / viewportHeight) > 0.001
    ) {
      this.resize(viewportWidth, viewportHeight);
    }
    const worldPos = this.getSkyTargetPosition(id, title, raDeg, decDeg);
    this.camera.updateMatrixWorld();
    const camSpace = worldPos.clone().applyMatrix4(this.camera.matrixWorldInverse);
    if (camSpace.z >= -0.2) {
      return { x: -999, y: -999, visible: false };
    }
    const ndc = worldPos.clone().project(this.camera);
    const x = (ndc.x * 0.5 + 0.5) * viewportWidth;
    const y = (-ndc.y * 0.5 + 0.5) * viewportHeight;
    const visible =
      x >= 32 &&
      x <= viewportWidth - 32 &&
      y >= 56 &&
      y <= viewportHeight - 64;
    return { x, y, visible };
  }

  public focusSkyTarget(
    id: string,
    title: string,
    raDeg: number,
    decDeg: number
  ): void {
    const pos = this.getSkyTargetPosition(id, title, raDeg, decDeg);
    const dir = pos.clone().normalize();
    this.lookTarget.yaw = Math.atan2(-dir.x, -dir.z);
    this.lookTarget.pitch = Math.asin(Math.max(-0.85, Math.min(0.85, dir.y)));
  }

  public setTargetAppearance(
    title: string,
    bpRp?: number,
    objectType?: string
  ): void {
    const lower = title.toLowerCase();
    const isBh =
      lower.includes('sagittarius') ||
      lower.includes('sgr a') ||
      lower.includes('black hole') ||
      lower.includes('gargantua');
    this.nebulaUniforms.uIsBlackHole.value = isBh ? 1.0 : 0.0;
    if (this.deepSkySculptureGroup) {
      this.deepSkySculptureGroup.visible = !isBh;
    }

    if (lower.includes('uranus')) {
      this.localBubbleUniforms.uCoolColor.value.set(0.08, 0.38, 0.52);
      this.localBubbleUniforms.uWarmColor.value.set(0.24, 0.72, 0.88);
      this.localBubbleUniforms.uHotColor.value.set(0.58, 0.92, 1.0);
      this.localBubbleUniforms.uCoronaInner.value.set(0.35, 0.85, 1.0);
      this.localBubbleUniforms.uCoronaOuter.value.set(0.08, 0.35, 0.58);
      return;
    }
    if (lower.includes('saturn') || lower.includes('jupiter') || objectType === 'planet') {
      this.localBubbleUniforms.uCoolColor.value.set(0.42, 0.28, 0.12);
      this.localBubbleUniforms.uWarmColor.value.set(0.88, 0.68, 0.36);
      this.localBubbleUniforms.uHotColor.value.set(1.18, 0.96, 0.64);
      this.localBubbleUniforms.uCoronaInner.value.set(0.95, 0.78, 0.45);
      this.localBubbleUniforms.uCoronaOuter.value.set(0.45, 0.32, 0.12);
      return;
    }
    const colorIdx = bpRp ?? (lower.includes('betelgeuse') || lower.includes('aldebaran') ? 1.85 : 0.1);
    if (colorIdx >= 1.2) {
      this.localBubbleUniforms.uCoolColor.value.set(0.28, 0.03, 0.005);
      this.localBubbleUniforms.uWarmColor.value.set(0.92, 0.24, 0.03);
      this.localBubbleUniforms.uHotColor.value.set(1.38, 0.78, 0.28);
      this.localBubbleUniforms.uCoronaInner.value.set(1.05, 0.48, 0.14);
      this.localBubbleUniforms.uCoronaOuter.value.set(0.68, 0.11, 0.015);
    } else if (colorIdx < -0.02) {
      this.localBubbleUniforms.uCoolColor.value.set(0.14, 0.28, 0.68);
      this.localBubbleUniforms.uWarmColor.value.set(0.48, 0.72, 1.25);
      this.localBubbleUniforms.uHotColor.value.set(0.95, 1.15, 1.55);
      this.localBubbleUniforms.uCoronaInner.value.set(0.68, 0.88, 1.35);
      this.localBubbleUniforms.uCoronaOuter.value.set(0.18, 0.42, 0.95);
    } else {
      this.localBubbleUniforms.uCoolColor.value.set(0.32, 0.42, 0.65);
      this.localBubbleUniforms.uWarmColor.value.set(0.82, 0.92, 1.18);
      this.localBubbleUniforms.uHotColor.value.set(1.32, 1.38, 1.52);
      this.localBubbleUniforms.uCoronaInner.value.set(0.88, 0.94, 1.25);
      this.localBubbleUniforms.uCoronaOuter.value.set(0.28, 0.48, 0.88);
    }
  }

  public flyToTargetIn3D(
    id: string,
    title: string,
    bpRp?: number,
    objectType?: string
  ): SpatialScaleRegime {
    const lowerId = id.toLowerCase();
    const lowerTitle = title.toLowerCase();
    this.setTargetAppearance(title, bpRp, objectType);
    let targetRegime: SpatialScaleRegime = 'solar_neighborhood';
    if (lowerTitle.includes('moon')) {
      targetRegime = 'cislunar';
    } else if (
      lowerId.includes('m42') ||
      lowerTitle.includes('nebula') ||
      lowerTitle.includes('m42') ||
      lowerTitle.includes('carina') ||
      lowerTitle.includes('sagittarius') ||
      lowerTitle.includes('sgr a') ||
      lowerTitle.includes('black hole') ||
      lowerTitle.includes('gargantua')
    ) {
      targetRegime = 'deep_sky';
    } else {
      targetRegime = 'solar_neighborhood';
    }

    const desc = derivePlanetaryWorldDescriptor(title, bpRp ?? 0.8);
    this.rebuildPlanetaryTerrainSplats(desc.terrainPalette);

    if (this.activeRegime === targetRegime) {
      this.lookTarget.yaw = 0;
      this.lookTarget.pitch = 0;
      this.targetFov = 52;
      return targetRegime;
    }

    this.flightState = {
      active: true,
      startPos: this.localFrame.position.clone(),
      endPos: REGIME_CAMERA_ANCHORS[targetRegime].clone(),
      progress: 0,
      targetRegime,
    };
    this.syncRegimeVisibility();
    return targetRegime;
  }

  public stepThroughPortal(direction: 1 | -1 = 1): void {
    const curIdx = SCALE_REGIME_ORDER.indexOf(this.activeRegime);
    const nextIdx =
      direction > 0 ? (curIdx + 1) % SCALE_REGIME_ORDER.length : Math.max(0, curIdx - 1);
    const targetRegime = SCALE_REGIME_ORDER[nextIdx]!;
    this.flightState = {
      active: true,
      startPos: this.localFrame.position.clone(),
      endPos: REGIME_CAMERA_ANCHORS[targetRegime].clone(),
      progress: 0,
      targetRegime,
    };
    this.syncRegimeVisibility();
  }

  public jumpToRegime(regime: SpatialScaleRegime): void {
    this.activeRegime = regime;
    this.flightState = null;
    const anchor = REGIME_CAMERA_ANCHORS[regime];
    this.localFrame.position.copy(anchor);
    this.localFrame.rotation.set(0, 0, 0);
    this.lookTarget.yaw = 0;
    this.lookTarget.pitch = 0;
    this.lookTarget.curYaw = 0;
    this.lookTarget.curPitch = 0;
    this.camera.rotation.set(0, 0, 0);
    this.targetFov = 56;
    this.curFov = 56;
    this.camera.fov = 56;
    this.camera.updateProjectionMatrix();
    this.localFrame.updateMatrixWorld(true);
    this.camera.updateMatrixWorld(true);

    this.syncRegimeVisibility();
    this.onRegimeChange?.(regime);
  }

  public setSkyRestorationActive(active: boolean): void {
    const val = active ? 1.0 : 0.0;
    this.skyDomeUniforms.uRestoration.value = val;
  }

  public setShowcaseMode(enabled: boolean): void {
    this.showcaseMode = enabled;
    if (!enabled) {
      this.lookTarget.yaw = 0;
      this.lookTarget.pitch = 0;
      this.targetFov = 56;
    }
  }

  public transitionToShowcaseScene(
    sceneKey: 'cislunar' | 'black_hole' | 'supergiant' | 'orion_nebula' | 'saturn_horizon',
    instant = false
  ): void {
    let targetRegime: SpatialScaleRegime = 'cislunar';
    if (sceneKey === 'cislunar') {
      targetRegime = 'cislunar';
      this.setSpectralBand('optical');
    } else if (sceneKey === 'black_hole') {
      targetRegime = 'deep_sky';
      this.setTargetAppearance('Sagittarius A* (Galactic Center)', 1.85, 'deep_sky');
      this.setSpectralBand('optical');
    } else if (sceneKey === 'supergiant') {
      targetRegime = 'solar_neighborhood';
      this.setTargetAppearance('Betelgeuse (α Orionis)', 1.85, 'star');
      this.setSpectralBand('optical');
    } else if (sceneKey === 'orion_nebula') {
      targetRegime = 'deep_sky';
      this.setTargetAppearance('Orion Nebula (M42)', -0.1, 'nebula');
      this.setSpectralBand('halpha');
    } else if (sceneKey === 'saturn_horizon') {
      targetRegime = 'planetary_surface';
      this.setTargetAppearance('Saturn', 0.9, 'planet');
      this.rebuildPlanetaryTerrainSplats('titan_methane');
      this.setSpectralBand('optical');
    }

    this.jumpToRegime(targetRegime);
    if (!instant) {
      this.curFov = 61;
      this.cinemaOpticsUniforms.uWarpFactor.value = 0.48;
      this.localBubbleUniforms.uWarpFactor.value = 0.48;
    }
  }

  public setSpectralBand(band: SpectralBandMode): void {
    const modeVal = band === 'halpha' ? 1.0 : band === 'jwst_ir' ? 2.0 : 0.0;
    this.skyDomeUniforms.uSpectralMode.value = modeVal;
    this.nebulaUniforms.uSpectralMode.value = modeVal;
    this.localBubbleUniforms.uSpectralMode.value = modeVal;
  }

  public setSpectralMonocleScreenPos(
    normX: number,
    normY: number,
    active = true
  ): void {
    this.skyDomeUniforms.uMonocleActive.value = active ? 1.0 : 0.0;
    this.skyDomeUniforms.uMonocleDir.value.set(normX, normY, 0.0);
  }

  public async enterWebXRSession(): Promise<boolean> {
    if (!this.renderer) return false;
    try {
      if (!this.sparkXr) {
        this.sparkXr = new SparkXr({
          renderer: this.renderer,
          mode: 'vr',
          enableHands: true,
        });
      }
      await this.sparkXr.toggleXr();
      return true;
    } catch {
      return false;
    }
  }

  public resize(width: number, height: number): void {
    if (!this.renderer || width <= 0 || height <= 0) return;
    const aspect = width / height;
    this.camera.aspect = aspect;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.composer?.setSize(width, height);
    this.bloomPass?.resolution.set(width, height);
  }

  public dispose(): void {
    if (typeof window !== 'undefined' && this.rafId) {
      window.cancelAnimationFrame(this.rafId);
    }
    this.captureHorizonSplatMesh?.dispose();
    this.planetaryTerrainSplatMesh?.dispose();
    this.sparkRenderer?.dispose();
    this.composer?.dispose();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
      this.renderer = null;
    }
  }
}
