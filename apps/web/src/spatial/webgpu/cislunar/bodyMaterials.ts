import { AdditiveBlending, BackSide, NodeMaterial, type Node, type Texture } from 'three/webgpu';
import {
  Fn,
  cameraPosition,
  clamp,
  cross,
  dFdx,
  dFdy,
  dot,
  float,
  fract,
  max,
  min,
  mix,
  modelViewMatrix,
  modelWorldMatrix,
  normalViewGeometry,
  normalize,
  positionLocal,
  positionView,
  positionWorld,
  pow,
  smoothstep,
  sqrt,
  texture,
  uv,
  vec2,
  vec3,
  vec4,
  vertexStage,
  asin,
  atan,
} from 'three/tsl';
import { gaussian, type BrunetonLut } from '../brunetonAtmosphere.js';

type Vec3Node = Node<'vec3'>;

/**
 * Earth, its atmosphere shell and the Moon of the cislunar landing scene. The Earth and its shell
 * are ported from the GLSL `ShaderMaterial`s in SparkPortalRenderer.ts and keep its lighting in
 * view space: the composition lights the Earth from a fixed direction relative to the camera.
 * The Moon is lit by a world-space Sun direction. Materials write linear radiance; AgX in the
 * post chain is the only tone mapping.
 */

export const EARTH_RADIUS_M = 6_371_000;
export const EARTH_HEIGHT_OFFSET_M = 11_000;
const EARTH_HEIGHT_TEX_WIDTH = 4096;
const EARTH_HEIGHT_TEX_HEIGHT = 2048;
const EARTH_NORTH_STEP_M = (Math.PI / EARTH_HEIGHT_TEX_HEIGHT) * EARTH_RADIUS_M;

/**
 * Decodes a normalized `[0, 1]` `(r, g)` sample from `earth_height_etopo2022_4k.png`
 * (`round(height_m + 11000)` split across high byte `R` and low byte `G`) to metres above the
 * EGM2008 geoid: `r * 65280 + g * 255 - 11000`.
 */
export function decodeEtopoHeightMetres(r: number, g: number): number {
  return r * 65280 + g * 255 - EARTH_HEIGHT_OFFSET_M;
}

/**
 * Computes the open-water fraction from `earth_water_natural_earth_4k.png` and the linear RGB
 * sample of `earth_day_bluemarble_2004-03_base_8k.jpg`. Natural Earth's ocean polygon covers the
 * water beneath Antarctica's floating ice shelves (Ross, Ronne-Filchner), which the March 2004
 * base map draws as bright white ice (`linear R, G > 0.57`) while open water is dark blue
 * (`linear R < 0.025`). Suppressing water where `min(r, g)` is bright keeps sun glint and ocean
 * tint on open water only.
 */
export function openWaterFraction(
  waterMask: number,
  dayLinearR: number,
  dayLinearG: number,
  _dayLinearB: number
): number {
  const minRg = Math.min(dayLinearR, dayLinearG);
  const t = Math.max(0, Math.min(1, (minRg - 0.15) / (0.35 - 0.15)));
  const iceShelf = t * t * (3 - 2 * t);
  return waterMask * (1 - iceShelf);
}

/**
 * Computes the tangent-space unit normal `(east, north, up)` at true slopes from four cardinal
 * ETOPO 2022 height samples (in metres) on the `4096x2048` equirectangular grid. Where
 * `earth_water_natural_earth_4k.png` marks water (`waterMask > 0`), ETOPO stores the lake or sea
 * floor rather than the water surface, so the slope is scaled by `1 - waterMask`.
 */
export function earthTangentNormal(
  hWestM: number,
  hEastM: number,
  hSouthM: number,
  hNorthM: number,
  sinLat: number,
  waterMask: number
): [number, number, number] {
  const land = Math.max(0, Math.min(1, 1 - waterMask));
  const cosLat = Math.max(0.05, Math.sqrt(Math.max(0, 1 - sinLat * sinLat)));
  const twoDEastM = 2 * EARTH_NORTH_STEP_M * cosLat;
  const twoDNorthM = 2 * EARTH_NORTH_STEP_M;
  const nx = -((hEastM - hWestM) / twoDEastM) * land;
  const ny = -((hNorthM - hSouthM) / twoDNorthM) * land;
  const nz = 1;
  const len = Math.hypot(nx, ny, nz);
  return [nx / len, ny / len, nz / len];
}

export interface EarthTextures {
  /** NASA Blue Marble Next Generation, March 2004 base map without relief shading (sRGB) */
  day: Texture;
  /** NASA Black Marble 2016 city lights (sRGB) */
  night: Texture;
  /** Natural Earth 1:10m ocean and lakes water fraction (linear) */
  water: Texture;
  /** NOAA ETOPO 2022 surface elevation encoded as round(h + 11000) in R and G (linear, NearestFilter) */
  height: Texture;
  /** NASA Blue Marble 2002 cloud layer (sRGB, red channel used) */
  clouds: Texture;
}

/** View-space unit normal as `normalize(normalMatrix * normal)`; not flipped on back faces. */
const viewNormal = () => normalize(normalViewGeometry);
/** View-space direction from the surface to the camera, as `normalize(-mvPosition.xyz)`. */
const viewDirection = () => normalize(vertexStage(normalize(positionView.negate())));

export function createEarthMaterial(textures: EarthTextures, lut: BrunetonLut, sunDirView: { value: unknown }): NodeMaterial {
  const day = texture(textures.day);
  const night = texture(textures.night);
  const water = texture(textures.water);
  const height = texture(textures.height);
  const clouds = texture(textures.clouds);
  const sunDir = sunDirView as unknown as Vec3Node;
  const objectDir = vertexStage(normalize(positionLocal));

  const material = new NodeMaterial();
  material.name = 'cislunarEarth';
  material.fragmentNode = Fn(() => {
    // The globe's own frame, as SphereGeometry lays it out: +y north, +x longitude 0, -z
    // longitude 90 E; the maps run from longitude -180 at the left edge. The mesh's orientation
    // turns the globe to the landing's moment (landingScene.ts).
    const p = normalize(objectDir).toVar('earthP');
    const eqUv = vec2(
      atan(p.z.negate(), p.x).mul(1 / (2 * Math.PI)).add(0.5),
      asin(clamp(p.y, -1.0, 1.0)).mul(1 / Math.PI).add(0.5)
    ).toVar('earthUv');
    // atan wraps at the texture seam; wrapping the u derivative keeps full mip detail there
    const rawDx = dFdx(eqUv);
    const rawDy = dFdy(eqUv);
    const gradX = vec2(fract(rawDx.x.add(0.5)).sub(0.5), rawDx.y).toVar('earthGradX');
    const gradY = vec2(fract(rawDy.x.add(0.5)).sub(0.5), rawDy.y).toVar('earthGradY');

    const daySample = day.sample(eqUv).grad(gradX, gradY).rgb.toVar('earthDaySample');
    const dayCol = pow(daySample, vec3(1.32));
    const nightLights = night.sample(eqUv).grad(gradX, gradY).rgb;
    const waterMask = water.sample(eqUv).grad(gradX, gradY).r.toVar('earthWaterMask');
    // Natural Earth's ocean polygon covers the water under Antarctica's floating ice shelves
    // (Ross, Ronne-Filchner), which the March 2004 base map draws white; open water is dark blue
    const iceShelf = smoothstep(0.15, 0.35, min(daySample.r, daySample.g));
    const openWater = waterMask.mul(float(1).sub(iceShelf)).toVar('earthOpenWater');
    const cloud = clouds.sample(eqUv).grad(gradX, gradY).r.toVar('earthClouds');
    const cloudShadow = clouds.sample(eqUv.sub(vec2(0.0018, 0.0012))).grad(gradX, gradY).r;

    // ETOPO 2022 surface elevation (metres above EGM2008) decoded from (R, G) at true slopes on land
    const du = float(1 / EARTH_HEIGHT_TEX_WIDTH);
    const dv = float(1 / EARTH_HEIGHT_TEX_HEIGHT);
    const hW = height.sample(vec2(fract(eqUv.x.sub(du)), eqUv.y));
    const hE = height.sample(vec2(fract(eqUv.x.add(du)), eqUv.y));
    const hS = height.sample(vec2(eqUv.x, clamp(eqUv.y.sub(dv), 0.0, 1.0)));
    const hN = height.sample(vec2(eqUv.x, clamp(eqUv.y.add(dv), 0.0, 1.0)));
    const hWestM = hW.r.mul(65280).add(hW.g.mul(255)).sub(EARTH_HEIGHT_OFFSET_M);
    const hEastM = hE.r.mul(65280).add(hE.g.mul(255)).sub(EARTH_HEIGHT_OFFSET_M);
    const hSouthM = hS.r.mul(65280).add(hS.g.mul(255)).sub(EARTH_HEIGHT_OFFSET_M);
    const hNorthM = hN.r.mul(65280).add(hN.g.mul(255)).sub(EARTH_HEIGHT_OFFSET_M);
    const land = float(1).sub(waterMask);
    const cosLat = max(0.05, sqrt(max(0.0, float(1).sub(p.y.mul(p.y)))));
    const slopeE = hEastM.sub(hWestM).div(cosLat.mul(2 * EARTH_NORTH_STEP_M)).mul(land);
    const slopeN = hNorthM.sub(hSouthM).div(2 * EARTH_NORTH_STEP_M).mul(land);
    const eastLocal = normalize(cross(vec3(0, 1, 0), p).add(vec3(1e-7, 0, 0)));
    const northLocal = cross(p, eastLocal);
    const localNormal = normalize(p.sub(eastLocal.mul(slopeE)).sub(northLocal.mul(slopeN)));

    const sphereN = viewNormal().toVar('earthSphereN');
    const N = normalize(modelViewMatrix.mul(vec4(localNormal, 0)).xyz).toVar('earthN');
    const L = normalize(sunDir);
    const V = viewDirection().toVar('earthV');
    const sunDot = dot(sphereN, L).toVar('earthSunDot');
    const surfaceSunDot = dot(N, L).toVar('earthSurfaceSunDot');
    const dayFactor = smoothstep(-0.08, 0.22, sunDot);

    const H = normalize(L.add(V));
    const spec = pow(max(0.0, dot(N, H)), 72.0).mul(openWater).mul(max(0.0, sunDot)).mul(float(1).sub(cloud.mul(0.85)));

    const oceanTint = mix(dayCol, vec3(0.025, 0.095, 0.24), openWater.mul(0.55)).toVar('earthOcean');
    const surfaceAlbedo = oceanTint.mul(float(1).sub(cloudShadow.mul(0.48)));
    const cloudLayer = vec3(0.92, 0.95, 1.0).mul(cloud).mul(0.85);
    const litSurface = surfaceAlbedo.mul(max(0.03, surfaceSunDot.mul(1.05)));
    const litClouds = cloudLayer.mul(max(0.03, sunDot.mul(1.05)));
    const litEarth = mix(litSurface, litClouds, clamp(cloud.mul(0.84), 0.0, 1.0))
      .add(vec3(0.42, 0.68, 0.98).mul(spec).mul(0.65));

    // NASA Black Marble 2016 city light (dim base layer removed at bake time), dimmed under cloud.
    // An exposure for the sunlit side could not show it; it is drawn at the level it had before
    // the landing's exposure of 3 (cityLightGain 1.25 then). Nothing else lights the night side:
    // moonlight is a few millionths of sunlight at most, so the port's blue fill is gone.
    const cityLightGain = 1.25 / 3;
    const nightEarth = nightLights.mul(cityLightGain).mul(float(1).sub(cloud.mul(0.72)));

    const viewDot = dot(sphereN, V).toVar('earthViewDot');
    const fresnel = pow(float(1).sub(max(0.0, viewDot)), 3.8);
    const lutSample = lut.sample(viewDot, sunDot).toVar('earthLut');
    // Sunlit air only. The port's warm twilight terms added more light along the terminator than
    // the Sun gives the day side, a bright stripe no photograph shows, so they are gone.
    const atmoTint = mix(vec3(0.06, 0.24, 0.64), lutSample.rgb.mul(1.25), 0.68)
      .mul(fresnel)
      .mul(dayFactor.mul(0.94));

    // The GLSL compressed this with x / (1 + 0.56 x); AgX now does all the tone mapping
    const radiance = mix(nightEarth, litEarth, dayFactor).add(atmoTint);
    return vec4(radiance, 1.0);
  })();
  return material;
}

/** Additive back-side limb glow around the Earth, from the same LUT. */
export function createAtmosphereShellMaterial(lut: BrunetonLut, sunDirView: { value: unknown }): NodeMaterial {
  const sunDir = sunDirView as unknown as Vec3Node;
  const material = new NodeMaterial();
  material.name = 'cislunarAtmosphereShell';
  material.side = BackSide;
  material.transparent = true;
  material.depthWrite = false;
  material.blending = AdditiveBlending;
  material.fragmentNode = Fn(() => {
    const N = viewNormal().toVar('atmoN');
    const viewDot = dot(N, viewDirection()).toVar('atmoViewDot');
    const limbIntensity = gaussian(viewDot.add(0.14).mul(5.2)).toVar('atmoLimb');
    // The shell is drawn from inside (BackSide) with its outward normals unflipped, so the limb's own
    // normal says how sunlit its air is; the negated normal of the port put the glow on the night side
    const sunAlign = dot(N, normalize(sunDir)).toVar('atmoSunAlign');
    // The limb glows where its air is sunlit, not around the night side
    const sunOrient = smoothstep(-0.2, 0.65, sunAlign);
    const sunriseArc = gaussian(sunAlign.sub(0.32).mul(Math.sqrt(6.5))).mul(0.38);
    const lutLimb = lut.sample(clamp(viewDot.add(0.16), -0.35, 1.0), sunAlign);
    const baseRayleigh = mix(vec3(0.14, 0.5, 0.96), vec3(0.52, 0.78, 1.0), sunriseArc);
    const rayleighCol = mix(baseRayleigh, lutLimb.rgb.mul(1.35), 0.62).mul(limbIntensity).mul(sunOrient).mul(0.98);
    // Additive blending scales rgb by alpha, as THREE.AdditiveBlending did in WebGL
    return vec4(rayleighCol, clamp(limbIntensity.mul(0.86), 0.0, 0.84));
  })();
  return material;
}

export interface MoonTextures {
  /** NASA SVS 4720 colour from the LROC WAC mosaic (sRGB); longitude -180 at the left edge */
  colour: Texture;
  /** Tangent-space normals (east, north, up) from the LOLA heights (linear data), same layout */
  normal: Texture;
}

/**
 * How far past the sphere's terminator relief may still catch the Sun: the sine of 2.7 deg, the
 * dip of the horizon seen from a 2 km peak, acos(1737.4 / 1739.4).
 */
const MOON_HORIZON_DIP = Math.sin((2.7 * Math.PI) / 180);

/**
 * The SVS colour map is made for appearance: its area-weighted mean linear luminance is 0.483
 * (tests/integration/test_moon_maps.py measures it), about four times the Moon's geometric albedo
 * of 0.12 (NASA Moon fact sheet). Scaling it by the ratio keeps the map's contrast and gives the
 * Moon its true brightness beside the Earth under one Sun.
 */
export const MOON_GEOMETRIC_ALBEDO = 0.12;
export const SVS_COLOUR_MEAN_LINEAR_LUMINANCE = 0.483;
const MOON_ALBEDO_SCALE = MOON_GEOMETRIC_ALBEDO / SVS_COLOUR_MEAN_LINEAR_LUMINANCE;

/**
 * The Moon as a sphere carrying the SVS colour map and the LOLA normal map at true slopes
 * (scripts/bake_moon_maps.py), lit by one world-space Sun direction. Reflectance is
 * Lommel-Seeliger, 2 mu0 / (mu0 + mu), the single-scattering law of dark regolith, scaled to 1
 * for light and view along the normal; the colour map supplies the albedo.
 *
 * Not modelled: shadows cast by relief, earthshine (the landing looks at the far side, which never
 * faces the Earth) and the opposition surge (only within about 10 deg of zero phase). For
 * the missing cast shadows, facets are lit only where the sphere is lit or up to
 * MOON_HORIZON_DIP beyond its terminator, fading over that band.
 */
export function createMoonMaterial(textures: MoonTextures, sunDirWorld: { value: unknown }): NodeMaterial {
  const colourMap = texture(textures.colour);
  const normalMap = texture(textures.normal);
  const sunDir = sunDirWorld as unknown as Vec3Node;
  const material = new NodeMaterial();
  material.name = 'cislunarMoon';
  material.fragmentNode = Fn(() => {
    const coord = uv().toVar('moonUv');
    const albedo = colourMap.sample(coord).rgb.mul(MOON_ALBEDO_SCALE);
    // SphereGeometry's frame: +y the north pole, +x longitude 0, -z longitude 90 E. The tiny x
    // term keeps east defined at the poles, where the cross product vanishes.
    const up = normalize(positionLocal).toVar('moonUp');
    const east = normalize(cross(vec3(0, 1, 0), up).add(vec3(1e-7, 0, 0))).toVar('moonEast');
    const north = cross(up, east);
    const tangentNormal = normalMap.sample(coord).xyz.mul(2).sub(1);
    const localNormal = east.mul(tangentNormal.x).add(north.mul(tangentNormal.y)).add(up.mul(tangentNormal.z));
    const N = normalize(modelWorldMatrix.mul(vec4(localNormal, 0)).xyz).toVar('moonN');
    const sphereNormal = normalize(modelWorldMatrix.mul(vec4(up, 0)).xyz);
    const L = normalize(sunDir).toVar('moonL');
    const V = normalize(cameraPosition.sub(positionWorld));
    const mu0 = max(0.0, dot(N, L)).toVar('moonMu0');
    const mu = max(0.001, dot(N, V));
    const reflectance = mu0.mul(2.0).div(mu0.add(mu));
    const sunlit = smoothstep(-MOON_HORIZON_DIP, 0.0, dot(sphereNormal, L));
    return vec4(albedo.mul(reflectance).mul(sunlit), 1.0);
  })();
  return material;
}
