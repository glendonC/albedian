import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { EXIF_ORIENTATIONS, type CatalogReference } from '@albedian/contracts';
import {
  BLACKBODY_STOPS,
  DISTANCE_RAIL_LY,
  LUMINOSITY_RAIL_SUN,
  RUWE_CAUTION_ABOVE,
  SUN_LUMINOSITY,
  SUN_TEFF_K,
  TEMPERATURE_RAIL_K,
  astrophysicsOf,
  blackbodyGradientCss,
  formatSignificant,
  offScaleNote,
  cropPlacement,
  distanceMethodLabel,
  placeIntervalOnLogRail,
  placeOnLogRail,
} from '../scanScales.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const RAILS = [DISTANCE_RAIL_LY, LUMINOSITY_RAIL_SUN, TEMPERATURE_RAIL_K];
const positive = fc.double({ min: 1e-6, max: 1e7, noNaN: true });

describe('log rails', () => {
  it('runs from 1 to 10,000 ly, 0.01 to 10,000 Suns, and 2,000 to 40,000 K, with the Sun at 5,772 K and 1', () => {
    expect([DISTANCE_RAIL_LY.min, DISTANCE_RAIL_LY.max]).toEqual([1, 10000]);
    expect(DISTANCE_RAIL_LY.ticks).toEqual([1, 10, 100, 1000, 10000]);
    expect([LUMINOSITY_RAIL_SUN.min, LUMINOSITY_RAIL_SUN.max]).toEqual([0.01, 10000]);
    expect(LUMINOSITY_RAIL_SUN.ticks).toEqual([0.01, 1, 100, 10000]);
    expect([TEMPERATURE_RAIL_K.min, TEMPERATURE_RAIL_K.max]).toEqual([2000, 40000]);
    expect(SUN_TEFF_K).toBe(5772);
    expect(SUN_LUMINOSITY).toBe(1);
  });

  it('places the ends at 0 and 1 and each decade tick evenly', () => {
    for (const rail of RAILS) {
      expect(placeOnLogRail(rail.min, rail)).toEqual({ fraction: 0, offScale: null });
      expect(placeOnLogRail(rail.max, rail)).toEqual({ fraction: 1, offScale: null });
    }
    const ticks = DISTANCE_RAIL_LY.ticks.map((t) => placeOnLogRail(t, DISTANCE_RAIL_LY)?.fraction ?? NaN);
    ticks.forEach((f, i) => expect(f).toBeCloseTo(i / 4, 12));
    expect(placeOnLogRail(1, LUMINOSITY_RAIL_SUN)?.fraction).toBeCloseTo(2 / 6, 12);
  });

  it('keeps placement monotonic and inside the rail, flagging values past either end', () => {
    fc.assert(
      fc.property(positive, positive, fc.constantFrom(...RAILS), (a, b, rail) => {
        const pa = placeOnLogRail(a, rail)!;
        const pb = placeOnLogRail(b, rail)!;
        if (pa.fraction < 0 || pa.fraction > 1) return false;
        if (a < b && pa.fraction > pb.fraction) return false;
        if (a < rail.min && pa.offScale !== 'below') return false;
        if (a > rail.max && pa.offScale !== 'above') return false;
        if (a >= rail.min && a <= rail.max && pa.offScale !== null) return false;
        return true;
      }),
      { seed: SEED, examples: [[1, 10000, DISTANCE_RAIL_LY], [0.5, 20000, DISTANCE_RAIL_LY], [0.01, 1, LUMINOSITY_RAIL_SUN], [1999, 40001, TEMPERATURE_RAIL_K]] }
    );
  });

  it('refuses values that cannot sit on a log rail', () => {
    for (const bad of [0, -3, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(placeOnLogRail(bad, DISTANCE_RAIL_LY)).toBeNull();
    }
  });

  it('places an interval as a band that contains its median', () => {
    fc.assert(
      fc.property(positive, positive, positive, fc.constantFrom(...RAILS), (x, y, z, rail) => {
        const [lower, median, upper] = [x, y, z].sort((p, q) => p - q) as [number, number, number];
        const band = placeIntervalOnLogRail({ lower, median, upper }, rail)!;
        return (
          band.lower !== null &&
          band.upper !== null &&
          band.lower.fraction <= band.median.fraction &&
          band.median.fraction <= band.upper.fraction
        );
      }),
      { seed: SEED, examples: [[8.011 * 3.261563777, 8.024 * 3.261563777, 8.039 * 3.261563777, DISTANCE_RAIL_LY]] }
    );
    const medianOnly = placeIntervalOnLogRail({ median: 2.85 }, LUMINOSITY_RAIL_SUN)!;
    expect(medianOnly.lower).toBeNull();
    expect(medianOnly.upper).toBeNull();
    expect(placeIntervalOnLogRail({ median: 0 }, LUMINOSITY_RAIL_SUN)).toBeNull();
  });
});

/** Planck times the CIE 1931 2-degree observer (Wyman, Sloan and Shirley 2013 fit), to 8-bit sRGB, scaled to the brightest channel. */
function referenceBlackbody(temperatureK: number): [number, number, number] {
  const lobe = (x: number, mu: number, s1: number, s2: number) => Math.exp(-0.5 * ((x - mu) / (x < mu ? s1 : s2)) ** 2);
  let X = 0;
  let Y = 0;
  let Z = 0;
  for (let nm = 380; nm <= 780; nm += 1) {
    const m = nm * 1e-9;
    const radiance = 1 / (m ** 5 * (Math.exp(6.62607015e-34 * 2.99792458e8 / (m * 1.380649e-23 * temperatureK)) - 1));
    X += radiance * (1.056 * lobe(nm, 599.8, 37.9, 31.0) + 0.362 * lobe(nm, 442.0, 16.0, 26.7) - 0.065 * lobe(nm, 501.1, 20.4, 26.2));
    Y += radiance * (0.821 * lobe(nm, 568.8, 46.9, 40.5) + 0.286 * lobe(nm, 530.9, 16.3, 31.1));
    Z += radiance * (1.217 * lobe(nm, 437.0, 11.8, 36.0) + 0.681 * lobe(nm, 459.0, 26.0, 13.8));
  }
  const linear = [
    3.2406 * X - 1.5372 * Y - 0.4986 * Z,
    -0.9689 * X + 1.8758 * Y + 0.0415 * Z,
    0.0557 * X - 0.204 * Y + 1.057 * Z,
  ];
  const peak = Math.max(...linear);
  return linear.map((v) => {
    const c = Math.max(v / peak, 0);
    return Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055));
  }) as [number, number, number];
}

describe('blackbody strip', () => {
  it('spans the temperature rail in rising order with the Sun among its stops', () => {
    const temps = BLACKBODY_STOPS.map((s) => s.temperatureK);
    expect(temps[0]).toBe(TEMPERATURE_RAIL_K.min);
    expect(temps.at(-1)).toBe(TEMPERATURE_RAIL_K.max);
    expect([...temps].sort((a, b) => a - b)).toEqual(temps);
    expect(temps).toContain(SUN_TEFF_K);
  });

  it('matches an independent Planck times CIE 1931 computation within 3 of 255 per channel', () => {
    for (const stop of BLACKBODY_STOPS) {
      const hex = stop.hex.replace('#', '');
      const got = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
      const want = referenceBlackbody(stop.temperatureK);
      got.forEach((channel, i) => {
        expect(Math.abs(channel - (want[i] ?? 0)), `${stop.temperatureK} K channel ${i}`).toBeLessThanOrEqual(3);
      });
    }
  });

  it('writes a left-to-right gradient with each stop at its log position', () => {
    const css = blackbodyGradientCss();
    expect(css.startsWith('linear-gradient(to right, ')).toBe(true);
    for (const stop of BLACKBODY_STOPS) {
      const at = (placeOnLogRail(stop.temperatureK, TEMPERATURE_RAIL_K)!.fraction * 100).toFixed(2);
      expect(css).toContain(`${stop.hex} ${at}%`);
    }
  });
});

describe('astrophysics on a catalog reference', () => {
  const base = { id: 'c', provider: 'Gaia DR3', releaseVersion: 'DR3', catalogSourceId: '1', commonName: 'Tabit', constellation: 'Orion', objectType: 'star', coordinateEpoch: 'J2016.0', raDeg: 72.46, decDeg: 6.96, apparentMagnitude: 3.19, citation: 'x' } as unknown as CatalogReference;
  const withBlock = (block: unknown) => ({ ...base, astrophysics: block }) as unknown as CatalogReference;

  it('is absent when the reference carries no block', () => {
    expect(astrophysicsOf(undefined)).toBeNull();
    expect(astrophysicsOf(base)).toBeNull();
  });

  it('reads temperature, luminosity and RUWE when present', () => {
    const got = astrophysicsOf(
      withBlock({
        temperatureK: { median: 6154, lower: 6150, upper: 6158 },
        luminositySolar: { median: 2.85, lower: 2.84, upper: 2.87 },
        ruwe: 3.93,
      })
    );
    expect(got).toEqual({
      temperatureK: { median: 6154, lower: 6150, upper: 6158 },
      luminositySolar: { median: 2.85, lower: 2.84, upper: 2.87 },
      ruwe: 3.93,
    });
  });

  it('drops any part that is not a positive finite value instead of guessing it', () => {
    const got = astrophysicsOf(
      withBlock({ temperatureK: { median: 0 }, luminositySolar: { median: 2.85, lower: null }, ruwe: 'high' })
    );
    expect(got).toEqual({ temperatureK: null, luminositySolar: { median: 2.85 }, ruwe: null });
    expect(astrophysicsOf(withBlock({ temperatureK: null, luminositySolar: null }))).toBeNull();
    expect(astrophysicsOf(withBlock('nope'))).toBeNull();
  });

  it('cautions above RUWE 1.4', () => {
    expect(RUWE_CAUTION_ABOVE).toBe(1.4);
  });
});

describe('off-scale notes', () => {
  const note = (interval: { median: number; lower?: number; upper?: number }) =>
    offScaleNote(placeIntervalOnLogRail(interval, DISTANCE_RAIL_LY)!, DISTANCE_RAIL_LY, ' ly');

  it('names the end a median is past', () => {
    expect(note({ median: 13046 })).toBe('Past the 10,000 ly end of this scale');
    expect(note({ median: 0.8 })).toBe('Below the 1 ly end of this scale');
  });

  it('names the end a band runs past while the median stays on the rail', () => {
    expect(note({ median: 9459, lower: 8000, upper: 11089 })).toBe('The range runs past the 10,000 ly end of this scale');
    expect(note({ median: 1.5, lower: 0.8, upper: 2 })).toBe('The range runs below the 1 ly end of this scale');
  });

  it('says nothing when the whole interval is on the rail', () => {
    expect(note({ median: 26.17, lower: 26.13, upper: 26.22 })).toBeNull();
  });
});

describe('significant figures', () => {
  it('prints three significant figures with thousands separators', () => {
    expect(formatSignificant(8.024 * 3.261563777)).toBe('26.2');
    expect(formatSignificant(8.024)).toBe('8.02');
    expect(formatSignificant(642.4)).toBe('642');
    expect(formatSignificant(1234)).toBe('1,230');
    expect(formatSignificant(0.012345)).toBe('0.0123');
    expect(formatSignificant(2.85)).toBe('2.85');
  });
});

describe('pixel crop of the photo', () => {
  // ci-00 is 480 by 360; Tabit's centroid is (332.8, 202.0), pixel centres at whole numbers as the detector reports them
  const tabit = { xPx: 332.8, yPx: 202.0 };

  it('centres the crop on the detection in an upright photo', () => {
    // 72 px box showing 12 source pixels: 6 px per source pixel; the centroid's image position is x + 0.5
    const place = cropPlacement(tabit, { width: 480, height: 360 }, undefined, 72, 12)!;
    expect(place.width).toBe(2880);
    expect(place.height).toBe(2160);
    expect(place.left).toBeCloseTo(-(333.3 * 6 - 36), 6);
    expect(place.top).toBeCloseTo(-(202.5 * 6 - 36), 6);
  });

  it('turns the centroid and swaps the size for a photo stored on its side (EXIF Orientation 6)', () => {
    const place = cropPlacement(tabit, { width: 480, height: 360 }, EXIF_ORIENTATIONS[6], 72, 12)!;
    // Shown 360 wide and 480 tall; a clockwise quarter turn sends (x, y) to (height - y, x)
    expect(place.width).toBe(2160);
    expect(place.height).toBe(2880);
    expect(place.left).toBeCloseTo(-((360 - 202.5) * 6 - 36), 6);
    expect(place.top).toBeCloseTo(-(333.3 * 6 - 36), 6);
  });

  it('mirrors before turning, as the contract orders it', () => {
    const mirrored = cropPlacement(tabit, { width: 480, height: 360 }, EXIF_ORIENTATIONS[2], 72, 12)!;
    expect(mirrored.left).toBeCloseTo(-((480 - 333.3) * 6 - 36), 6);
  });

  it('draws no crop without a usable size or centroid', () => {
    expect(cropPlacement({ xPx: 1, yPx: 1 }, { width: 0, height: 360 }, undefined, 72, 12)).toBeNull();
    expect(cropPlacement({ xPx: Number.NaN, yPx: 1 }, { width: 480, height: 360 }, undefined, 72, 12)).toBeNull();
  });
});

describe('distance method names', () => {
  it('names each contract method for people and keeps an unknown one as it is', () => {
    expect(distanceMethodLabel('photogeometric_bailer_jones_edr3')).toEqual({ name: 'Bailer-Jones 2021 photogeometric', range: '16th to 84th percentile' });
    expect(distanceMethodLabel('geometric_parallax')).toEqual({ name: 'Inverse parallax', range: 'range from the parallax error' });
    expect(distanceMethodLabel('ephemeris_au')).toEqual({ name: 'Ephemeris', range: "range from the model's error bounds" });
    expect(distanceMethodLabel('something_new')).toEqual({ name: 'something_new', range: 'range as given' });
  });
});
