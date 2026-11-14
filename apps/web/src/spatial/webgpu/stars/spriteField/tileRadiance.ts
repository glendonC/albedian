import { usesBMinusV, type CatalogStarCoreTile } from '../../../catalogStars/tileCodec.js';
import { fluxFromMagnitude } from '../blackbody.js';
import type { StarColourTable } from '../starColor.js';

/** Star radiance per tile: unit-luminance colour from the colour index times 10^(-0.4 m). */
export function tileRadiance(tile: CatalogStarCoreTile, colours: StarColourTable): Float32Array {
  const radiance = new Float32Array(tile.count * 3);
  for (let i = 0; i < tile.count; i++) {
    colours.sampleInto(tile.colorIndex[i] ?? Number.NaN, usesBMinusV(tile.flags[i] ?? 0), radiance, i * 3);
    const flux = fluxFromMagnitude(tile.magnitude[i] ?? Number.POSITIVE_INFINITY);
    radiance[i * 3] = radiance[i * 3]! * flux;
    radiance[i * 3 + 1] = radiance[i * 3 + 1]! * flux;
    radiance[i * 3 + 2] = radiance[i * 3 + 2]! * flux;
  }
  return radiance;
}
