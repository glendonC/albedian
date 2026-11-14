/**
 * Skyglow: broadband noise bed level from artificial sky brightness (Falchi et al. 2016).
 */

import { applyAudibilityBoundsDb, clampUnit, isFiniteNumber } from './math.js';

export interface SkyglowParams {
  /** Falchi et al. (2016) natural background, about 22.0 V mag/arcsec^2 (174 ucd/m^2). */
  readonly naturalSkyMagArcsec2: number;
  /** Bed level when artificial brightness equals the natural background, dBFS. */
  readonly referenceLevelDb: number;
  /** 1 maps the artificial-to-natural flux ratio to an intensity ratio. */
  readonly compression: number;
  readonly ceilingDb: number;
  readonly floorDb: number;
}

export const SKYGLOW: SkyglowParams = {
  naturalSkyMagArcsec2: 22.0,
  referenceLevelDb: -34,
  compression: 1,
  ceilingDb: -12,
  floorDb: -72,
};

/**
 * Ratio of artificial to natural sky brightness (the quantity mapped by Falchi et al. 2016).
 * Zero for a sky at or darker than the natural background.
 */
export function artificialSkyglowRatio(
  skyBrightnessMagArcsec2: number | null | undefined,
  params: SkyglowParams = SKYGLOW
): number | null {
  if (!isFiniteNumber(skyBrightnessMagArcsec2)) return null;
  const total = Math.pow(10, -0.4 * (skyBrightnessMagArcsec2 - params.naturalSkyMagArcsec2));
  return Math.max(0, total - 1);
}

/**
 * Bed level in dBFS. The hiss stands for the artificial part of the sky glow only, so a
 * pristine sky is silent. `unveil` in [0, 1] is the user's reveal control: it scales the
 * artificial flux by (1 - unveil), reaching silence when fully unveiled.
 */
export function skyglowBedLevelDb(
  skyBrightnessMagArcsec2: number | null | undefined,
  unveil: number,
  params: SkyglowParams = SKYGLOW
): number | null {
  const ratio = artificialSkyglowRatio(skyBrightnessMagArcsec2, params);
  if (ratio === null) return null;
  const u = isFiniteNumber(unveil) ? clampUnit(unveil) : 0;
  const effective = ratio * (1 - u);
  if (!(effective > 0)) return -Infinity;
  const level = params.referenceLevelDb + 10 * params.compression * Math.log10(effective);
  return applyAudibilityBoundsDb(level, params.floorDb, params.ceilingDb);
}
