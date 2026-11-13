import {
  ClampToEdgeWrapping,
  LinearFilter,
  Mesh,
  NearestFilter,
  NoColorSpace,
  RepeatWrapping,
  SRGBColorSpace,
  SphereGeometry,
  type ColorSpace,
  type Group,
  type Scene,
  type Texture,
  type TextureLoader,
} from 'three/webgpu';
import { uniform } from 'three/tsl';
import { LANDING_SKY_GAIN } from '../../sky/panoramaGrade.js';
import { loadPanoramaRegistration } from '../../sky/skyRegistration.js';
import { BRUNETON_ATMO_LUT_URL, createBrunetonLut } from '../brunetonAtmosphere.js';
import { createAtmosphereShellMaterial, createEarthMaterial, createMoonMaterial } from './bodyMaterials.js';
import {
  EARTH_RADIUS_KM,
  KM_PER_UNIT,
  MOON_RADIUS_KM,
  type LandingPose,
} from './landingScene.js';
import { createSkyBackgroundNode } from './skyBackground.js';

export const ATMOSPHERE_RADIUS_FACTOR = 1.022;
export const CATALOG_STAR_MOUNT_RADIUS = 85.0;
/** Vertical field of view of the landing: the Moon fills about 11.6 deg of it, the Earth 1.85. */
export const LANDING_FOV_DEG = 26;
/** The narrowest wheel zoom, wide enough to keep the Moon's relief at the map's resolution. */
export const MIN_FOV_DEG = 6;
/**
 * Exposure of the landing. With the Moon at its true albedo, the camera exposes for the sunlit bodies; the Milky
 * Way is then shown far brighter than one exposure could record it, and dimmed from its usual grade by
 * LANDING_SKY_GAIN (sky/panoramaGrade.ts, beside the grade the Credits page words) so the bodies stay brightest.
 */
export const LANDING_EXPOSURE = 3.0;
export { LANDING_SKY_GAIN };
/**
 * The catalog's magnitude limit on the landing: every star in the release (Gaia DR3 to G 12,
 * Hipparcos 2007 at the bright end) is drawn as a sprite over the star-removed Milky Way map.
 */
export const LANDING_LIMITING_MAGNITUDE = 12;
/** Sharpens the 8k colour and the normal map toward the limb, where the sphere is seen edge-on. */
export const MOON_MAP_ANISOTROPY = 8;

/**
 * Starts loading a texture and returns it at once; materials bind it now and three uploads the
 * image when it arrives. `wrapU` repeats across the u seam of equirectangular maps.
 */
export function loadCislunarTexture(
  textureLoader: TextureLoader,
  pending: Promise<unknown>[],
  url: string,
  colorSpace: ColorSpace,
  wrapU: boolean,
  onLoad?: () => void
): Texture {
  let resolveLoad!: () => void;
  let rejectLoad!: (error: unknown) => void;
  pending.push(
    new Promise<void>((resolve, reject) => {
      resolveLoad = resolve;
      rejectLoad = reject;
    })
  );
  const tex = textureLoader.load(
    url,
    () => {
      onLoad?.();
      resolveLoad();
    },
    undefined,
    (error) => {
      console.warn(`[webgpu host] texture ${url} failed`, error);
      rejectLoad(error);
    }
  );
  tex.colorSpace = colorSpace;
  tex.wrapS = wrapU ? RepeatWrapping : ClampToEdgeWrapping;
  tex.wrapT = ClampToEdgeWrapping;
  return tex;
}

export function populateCislunarWorld(
  world: Group,
  scene: Scene,
  textureLoader: TextureLoader,
  pending: Promise<unknown>[],
  earthSunView: { value: unknown },
  landing: LandingPose
): { earth: Mesh; moon: Mesh } {
  const load = (url: string, colorSpace: ColorSpace, wrapU: boolean, onLoad?: () => void) =>
    loadCislunarTexture(textureLoader, pending, url, colorSpace, wrapU, onLoad);

  // ESO eso0932a with its stars removed by scripts/bake_starless_panorama.py; provenance in textures/PROVENANCE.json
  const milkyWay = load('/textures/eso_milkyway_diffuse_6k.webp', SRGBColorSpace, true);
  // The dome is drawn only with the panorama's fitted registration, never misaligned
  pending.push(
    loadPanoramaRegistration().then(
      (registration) => {
        scene.backgroundNode = createSkyBackgroundNode(milkyWay, registration, LANDING_SKY_GAIN);
      },
      (error: unknown) => console.warn('[webgpu host] sky panorama registration failed; no sky dome', error)
    )
  );

  const lutReady = uniform(0);
  const lutTexture = load(BRUNETON_ATMO_LUT_URL, NoColorSpace, false, () => (lutReady.value = 1));
  lutTexture.minFilter = LinearFilter;
  lutTexture.magFilter = LinearFilter;
  lutTexture.generateMipmaps = false;
  const lut = createBrunetonLut(lutTexture, lutReady);

  const earthHeight = load('/textures/earth_height_etopo2022_4k.png', NoColorSpace, true);
  earthHeight.minFilter = NearestFilter;
  earthHeight.magFilter = NearestFilter;
  earthHeight.generateMipmaps = false;
  const earth = new Mesh(
    new SphereGeometry(EARTH_RADIUS_KM / KM_PER_UNIT, 160, 160),
    createEarthMaterial(
      {
        // NASA Blue Marble (March 2004 base), Black Marble 2016, Natural Earth water, ETOPO 2022 heights, and 2002 clouds
        day: load('/textures/earth_day_bluemarble_2004-03_base_8k.jpg', SRGBColorSpace, true),
        night: load('/textures/earth_night_blackmarble_2016_8k.jpg', SRGBColorSpace, true),
        water: load('/textures/earth_water_natural_earth_4k.png', NoColorSpace, true),
        height: earthHeight,
        clouds: load('/textures/earth_clouds_bluemarble2002_2k.jpg', SRGBColorSpace, true),
      },
      lut,
      earthSunView
    )
  );
  earth.name = 'cislunarEarth';
  earth.position.copy(landing.earth.position);
  earth.quaternion.copy(landing.earth.quaternion);
  world.add(earth);

  const atmosphere = new Mesh(
    new SphereGeometry((EARTH_RADIUS_KM / KM_PER_UNIT) * ATMOSPHERE_RADIUS_FACTOR, 160, 160),
    createAtmosphereShellMaterial(lut, earthSunView)
  );
  atmosphere.name = 'cislunarAtmosphereShell';
  atmosphere.position.copy(landing.earth.position);
  world.add(atmosphere);

  // NASA SVS 4720 colour and LOLA normals, baked by scripts/bake_moon_maps.py
  const moonColour = load('/textures/moon_svs4720_color_8k.jpg', SRGBColorSpace, true);
  const moonNormal = load('/textures/moon_svs4720_lola_normal_16ppd.webp', NoColorSpace, true);
  moonColour.anisotropy = MOON_MAP_ANISOTROPY;
  moonNormal.anisotropy = MOON_MAP_ANISOTROPY;
  const moon = new Mesh(
    new SphereGeometry(MOON_RADIUS_KM / KM_PER_UNIT, 256, 128),
    createMoonMaterial({ colour: moonColour, normal: moonNormal }, uniform(landing.moon.sunDirection.clone()))
  );
  moon.name = 'cislunarMoon';
  moon.position.copy(landing.moon.position);
  moon.quaternion.copy(landing.moon.quaternion);
  world.add(moon);

  return { earth, moon };
}
