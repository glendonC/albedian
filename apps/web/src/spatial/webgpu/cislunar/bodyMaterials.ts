import type { NodeMaterial } from 'three/webgpu';
import { atan } from 'three/tsl';
import type { BrunetonLut } from '../brunetonAtmosphere.js';
import { buildAtmosphereShellNodeMaterial } from './materials/atmosphereShellMaterial.js';
import { buildEarthNodeMaterial } from './materials/earthMaterial.js';
import {
  EARTH_HEIGHT_OFFSET_M,
  EARTH_RADIUS_M,
  decodeEtopoHeightMetres,
  earthTangentNormal,
  openWaterFraction,
} from './materials/earthReliefMath.js';
import type { EarthTextures, MoonTextures } from './materials/materialTypes.js';
import { buildMoonNodeMaterial } from './materials/moonMaterial.js';

export {
  EARTH_HEIGHT_OFFSET_M,
  EARTH_RADIUS_M,
  decodeEtopoHeightMetres,
  earthTangentNormal,
  openWaterFraction,
};
export type { EarthTextures, MoonTextures };

/**
 * The SVS colour map is made for appearance: its area-weighted mean linear luminance is 0.483
 * (tests/integration/test_moon_maps.py measures it), about four times the Moon's geometric albedo
 * of 0.12 (NASA Moon fact sheet). Scaling by their ratio preserves contrast at true relative albedo.
 */
export const MOON_GEOMETRIC_ALBEDO = 0.12;
export const SVS_COLOUR_MEAN_LINEAR_LUMINANCE = 0.483;
const MOON_ALBEDO_SCALE = MOON_GEOMETRIC_ALBEDO / SVS_COLOUR_MEAN_LINEAR_LUMINANCE;

export function createEarthMaterial(
  textures: EarthTextures,
  lut: BrunetonLut,
  sunDirView: { value: unknown },
): NodeMaterial {
  return buildEarthNodeMaterial(textures, lut, sunDirView, (p) => atan(p.z.negate(), p.x));
}

export function createAtmosphereShellMaterial(
  lut: BrunetonLut,
  sunDirView: { value: unknown },
): NodeMaterial {
  return buildAtmosphereShellNodeMaterial(lut, sunDirView);
}

export function createMoonMaterial(
  textures: MoonTextures,
  sunDirWorld: { value: unknown },
): NodeMaterial {
  return buildMoonNodeMaterial(textures, sunDirWorld, MOON_ALBEDO_SCALE);
}
