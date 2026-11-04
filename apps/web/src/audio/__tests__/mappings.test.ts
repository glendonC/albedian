import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  DISTANCE_CUES,
  DUST,
  LOUDNESS,
  PLANCK_MEAN_X,
  REGIME_DISTANCE_WINDOWS,
  SKYGLOW,
  TIMBRE,
  UNCERTAINTY,
  apparentBolometricMagnitude,
  artificialSkyglowRatio,
  ballesterosTeffFromBv,
  beatingFifthHz,
  directReverbGains,
  directToReverberantDb,
  distanceLowpassHz,
  dustLowpassHz,
  harmonicCentroidHz,
  perceptualDistance,
  planckFrequencyScaleHz,
  planckHarmonicAmplitudes,
  pulsarPulseRateHz,
  receivedLevelDb,
  skyglowBedLevelDb,
  uncertaintyBeatHz,
  type ScaleAudioRegime,
} from '../mappings';

const missingValue = fc.constantFrom<number | null | undefined>(undefined, null, Number.NaN, Infinity, -Infinity);
const nonPositive = fc.oneof(fc.constant(0), fc.double({ min: -1e12, max: -1e-12, noNaN: true }));
const positiveDistancePc = fc.double({ min: 1e-14, max: 1e10, noNaN: true });
const luminosity = fc.double({ min: 1e-6, max: 1e8, noNaN: true });
const regime = fc.constantFrom<ScaleAudioRegime>(
  'earth_capture',
  'cislunar',
  'solar_neighborhood',
  'deep_sky',
  'planetary_surface'
);
const sortedPair = (arb: fc.Arbitrary<number>) =>
  fc.tuple(arb, arb).map(([a, b]) => (a <= b ? ([a, b] as const) : ([b, a] as const)));

describe('Property: distance cues (DRR and high-frequency rolloff)', () => {
  it('perceptual distance is bounded to [0, 1] and non-decreasing with distance', () => {
    fc.assert(
      fc.property(regime, sortedPair(positiveDistancePc), (r, [near, far]) => {
        const window = REGIME_DISTANCE_WINDOWS[r];
        const a = perceptualDistance(near, window)!;
        const b = perceptualDistance(far, window)!;
        expect(a).toBeGreaterThanOrEqual(0);
        expect(b).toBeLessThanOrEqual(1);
        expect(b).toBeGreaterThanOrEqual(a);
      })
    );
  });

  it('farther objects get a lower direct-to-reverberant ratio and a lower cutoff, within bounds', () => {
    fc.assert(
      fc.property(regime, sortedPair(positiveDistancePc), (r, [near, far]) => {
        const window = REGIME_DISTANCE_WINDOWS[r];
        const pNear = perceptualDistance(near, window);
        const pFar = perceptualDistance(far, window);
        const drrNear = directToReverberantDb(pNear)!;
        const drrFar = directToReverberantDb(pFar)!;
        expect(drrFar).toBeLessThanOrEqual(drrNear);
        expect(drrNear).toBeLessThanOrEqual(DISTANCE_CUES.drrNearDb);
        expect(drrFar).toBeGreaterThanOrEqual(DISTANCE_CUES.drrFarDb);
        const lpNear = distanceLowpassHz(pNear)!;
        const lpFar = distanceLowpassHz(pFar)!;
        expect(lpFar).toBeLessThanOrEqual(lpNear * (1 + 1e-12));
        expect(lpNear).toBeLessThanOrEqual(DISTANCE_CUES.lowpassNearHz * (1 + 1e-12));
        expect(lpFar).toBeGreaterThanOrEqual(DISTANCE_CUES.lowpassFarHz * (1 - 1e-12));
      })
    );
  });

  it('the direct/reverb split preserves unit energy and reproduces the requested ratio', () => {
    fc.assert(
      fc.property(fc.double({ min: -60, max: 60, noNaN: true }), (drrDb) => {
        const { direct, reverb } = directReverbGains(drrDb);
        expect(direct * direct + reverb * reverb).toBeCloseTo(1, 12);
        expect(20 * Math.log10(direct / reverb)).toBeCloseTo(drrDb, 9);
      })
    );
  });

  it('is silent (null) on missing or non-positive distance', () => {
    fc.assert(
      fc.property(regime, fc.oneof(missingValue, nonPositive), (r, d) => {
        expect(perceptualDistance(d, REGIME_DISTANCE_WINDOWS[r])).toBeNull();
      })
    );
    expect(directToReverberantDb(null)).toBeNull();
    expect(distanceLowpassHz(undefined)).toBeNull();
  });
});

describe('Property: luminosity loudness attenuated with distance', () => {
  it('is non-decreasing in luminosity and non-increasing in distance, never above the ceiling', () => {
    fc.assert(
      fc.property(sortedPair(luminosity), sortedPair(positiveDistancePc), ([lLow, lHigh], [dNear, dFar]) => {
        const quiet = receivedLevelDb(lLow, dFar)!;
        const louder = receivedLevelDb(lHigh, dFar)!;
        const nearer = receivedLevelDb(lLow, dNear)!;
        expect(louder).toBeGreaterThanOrEqual(quiet);
        expect(nearer).toBeGreaterThanOrEqual(quiet);
        for (const level of [quiet, louder, nearer]) {
          expect(level).toBeLessThanOrEqual(LOUDNESS.ceilingDb);
          expect(level === -Infinity || level >= LOUDNESS.floorDb).toBe(true);
        }
      })
    );
  });

  it('follows Pogson exactly inside the unclamped range: one magnitude brighter is 4 dB louder', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 8, noNaN: true }), (magBelowReference) => {
        // Build a source whose apparent bolometric magnitude is referenceMag + magBelowReference.
        const mag = LOUDNESS.referenceMag + 1 + magBelowReference;
        const distancePc = 10;
        const lum = Math.pow(10, -0.4 * (mag - 4.74));
        const brighter = receivedLevelDb(lum * Math.pow(10, 0.4), distancePc)!;
        const base = receivedLevelDb(lum, distancePc)!;
        expect(brighter - base).toBeCloseTo(4, 9);
      })
    );
  });

  it('is silent (null) when luminosity or distance is missing', () => {
    fc.assert(
      fc.property(fc.oneof(missingValue, nonPositive), positiveDistancePc, (lum, d) => {
        expect(receivedLevelDb(lum, d)).toBeNull();
        expect(receivedLevelDb(1, lum)).toBeNull();
      })
    );
  });

  it('places the Sun at 10 pc at its absolute bolometric magnitude', () => {
    expect(apparentBolometricMagnitude(1, 10)).toBeCloseTo(4.74, 12);
  });
});

describe('Property: effective temperature to timbre brightness', () => {
  const fundamental = fc.constantFrom(110, 137.5, 165, 220, 330, 440);

  it('produces non-negative amplitudes with unit total power', () => {
    fc.assert(
      fc.property(fc.double({ min: 500, max: 1e5, noNaN: true }), fundamental, (teff, f0) => {
        const amps = planckHarmonicAmplitudes(teff, f0, 128)!;
        let power = 0;
        for (const a of amps) {
          expect(a).toBeGreaterThanOrEqual(0);
          power += a * a;
        }
        expect(power).toBeCloseTo(1, 9);
      })
    );
  });

  it('raises the spectral centroid strictly with Teff inside the mapped range', () => {
    fc.assert(
      fc.property(
        fc.double({ min: TIMBRE.minTeffK, max: TIMBRE.maxTeffK, noNaN: true }),
        fc.double({ min: TIMBRE.minTeffK, max: TIMBRE.maxTeffK, noNaN: true }),
        fundamental,
        (t1, t2, f0) => {
          fc.pre(Math.abs(t1 - t2) > 1);
          const [cool, hot] = t1 < t2 ? [t1, t2] : [t2, t1];
          const cCool = harmonicCentroidHz(planckHarmonicAmplitudes(cool, f0, 145)!, f0);
          const cHot = harmonicCentroidHz(planckHarmonicAmplitudes(hot, f0, 145)!, f0);
          expect(cHot).toBeGreaterThan(cCool);
        }
      )
    );
  });

  it('matches the continuous Planck centroid when the harmonics sample the curve finely', () => {
    const f0 = 5;
    const amps = planckHarmonicAmplitudes(TIMBRE.referenceTeffK, f0, 6000)!;
    expect(harmonicCentroidHz(amps, f0)).toBeCloseTo(TIMBRE.referenceCentroidHz, -1);
    expect(planckFrequencyScaleHz(TIMBRE.referenceTeffK)! * PLANCK_MEAN_X).toBeCloseTo(TIMBRE.referenceCentroidHz, 9);
  });

  it('is silent (null) on missing or non-positive Teff', () => {
    fc.assert(
      fc.property(fc.oneof(missingValue, nonPositive), (teff) => {
        expect(planckFrequencyScaleHz(teff)).toBeNull();
        expect(planckHarmonicAmplitudes(teff, 220, 32)).toBeNull();
      })
    );
  });

  it('converts B-V with Ballesteros (2012) Eq. 14 and refuses colours outside its domain', () => {
    expect(ballesterosTeffFromBv(0.65)).toBeCloseTo(5778.5, 0);
    fc.assert(
      fc.property(sortedPair(fc.double({ min: -0.5, max: 3, noNaN: true })), ([blue, red]) => {
        fc.pre(red - blue > 1e-9);
        expect(ballesterosTeffFromBv(blue)!).toBeGreaterThan(ballesterosTeffFromBv(red)!);
      })
    );
    for (const bad of [undefined, null, Number.NaN, -0.6, 3.1]) expect(ballesterosTeffFromBv(bad)).toBeNull();
  });
});

describe('Property: skyglow bed from sky brightness', () => {
  const sb = fc.double({ min: 10, max: 24, noNaN: true });
  const unveil = fc.double({ min: 0, max: 1, noNaN: true });

  it('gets louder as the sky gets brighter (smaller mag/arcsec^2) and quieter as it is unveiled', () => {
    fc.assert(
      fc.property(sortedPair(sb), sortedPair(unveil), ([bright, dark], [less, more]) => {
        expect(skyglowBedLevelDb(bright, less)!).toBeGreaterThanOrEqual(skyglowBedLevelDb(dark, less)!);
        expect(skyglowBedLevelDb(bright, less)!).toBeGreaterThanOrEqual(skyglowBedLevelDb(bright, more)!);
      })
    );
  });

  it('stays within bounds, and is silent for a natural sky or a fully unveiled one', () => {
    fc.assert(
      fc.property(sb, unveil, (s, u) => {
        const level = skyglowBedLevelDb(s, u)!;
        expect(level).toBeLessThanOrEqual(SKYGLOW.ceilingDb);
        expect(level === -Infinity || level >= SKYGLOW.floorDb).toBe(true);
        expect(skyglowBedLevelDb(s, 1)).toBe(-Infinity);
      })
    );
    fc.assert(
      fc.property(fc.double({ min: SKYGLOW.naturalSkyMagArcsec2, max: 30, noNaN: true }), unveil, (s, u) => {
        expect(artificialSkyglowRatio(s)).toBe(0);
        expect(skyglowBedLevelDb(s, u)).toBe(-Infinity);
      })
    );
  });

  it('is the Falchi et al. artificial-to-natural ratio: 1 when the sky is twice as bright as natural', () => {
    const twiceNatural = SKYGLOW.naturalSkyMagArcsec2 - 2.5 * Math.log10(2);
    expect(artificialSkyglowRatio(twiceNatural)).toBeCloseTo(1, 12);
    expect(skyglowBedLevelDb(twiceNatural, 0)).toBeCloseTo(SKYGLOW.referenceLevelDb, 9);
  });

  it('is silent (null) on missing sky brightness, whatever the unveil state', () => {
    fc.assert(
      fc.property(missingValue, unveil, (s, u) => {
        expect(artificialSkyglowRatio(s)).toBeNull();
        expect(skyglowBedLevelDb(s, u)).toBeNull();
      })
    );
  });
});

describe('Property: uncertainty beating', () => {
  const residual = fc.double({ min: 0, max: 10_000, noNaN: true });

  it('is proportional to the residual below full scale, bounded, and zero at lock', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: UNCERTAINTY.fullScaleResidualArcsec, noNaN: true }), (r) => {
        const expected = (UNCERTAINTY.maxBeatHz / UNCERTAINTY.fullScaleResidualArcsec) * r;
        expect(uncertaintyBeatHz(r)).toBeCloseTo(expected, 12);
      })
    );
    fc.assert(
      fc.property(sortedPair(residual), ([a, b]) => {
        const lo = uncertaintyBeatHz(a)!;
        const hi = uncertaintyBeatHz(b)!;
        expect(hi).toBeGreaterThanOrEqual(lo);
        expect(hi).toBeLessThanOrEqual(UNCERTAINTY.maxBeatHz);
      })
    );
    expect(uncertaintyBeatHz(0)).toBe(0);
  });

  it('detunes a just fifth so the coinciding harmonics beat at exactly the requested rate', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 50, max: 2000, noNaN: true }),
        fc.double({ min: 0, max: UNCERTAINTY.maxBeatHz, noNaN: true }),
        (lower, beat) => {
          const pair = beatingFifthHz(lower, beat);
          expect(2 * pair.upperHz - 3 * pair.lowerHz).toBeCloseTo(beat, 9);
        }
      )
    );
    const locked = beatingFifthHz(220, 0);
    expect(locked.upperHz / locked.lowerHz).toBe(1.5);
  });

  it('is silent (null) on a missing or negative residual (no clean interval is implied)', () => {
    fc.assert(
      fc.property(fc.oneof(missingValue, fc.double({ min: -1e6, max: -1e-9, noNaN: true })), (r) => {
        expect(uncertaintyBeatHz(r)).toBeNull();
      })
    );
  });
});

describe('Property: pulsar spin rates', () => {
  it('passes valid catalogue spin frequencies through with no scaling', () => {
    fc.assert(
      fc.property(fc.double({ min: 1e-4, max: 2000, noNaN: true }), (f0) => {
        expect(Object.is(pulsarPulseRateHz(f0), f0)).toBe(true);
      })
    );
    // ATNF Pulsar Catalogue v2.6.1, F0 of the Crab (J0534+2200) and Vela (J0835-4510)
    expect(pulsarPulseRateHz(29.946923)).toBe(29.946923);
    expect(pulsarPulseRateHz(11.1946499395)).toBe(11.1946499395);
  });

  it('is silent (null) on a missing, non-positive or impossible spin frequency', () => {
    fc.assert(
      fc.property(fc.oneof(missingValue, nonPositive, fc.double({ min: 2000.0001, max: 1e9, noNaN: true })), (f0) => {
        expect(pulsarPulseRateHz(f0)).toBeNull();
      })
    );
  });
});

describe('Property: dust column muffling', () => {
  const av = fc.double({ min: 0, max: 50, noNaN: true });

  it('muffles more as A_V grows, stays within bounds, and is open at A_V = 0', () => {
    fc.assert(
      fc.property(sortedPair(av), ([thin, thick]) => {
        const open = dustLowpassHz(thin)!;
        const muffled = dustLowpassHz(thick)!;
        expect(muffled).toBeLessThanOrEqual(open);
        expect(muffled).toBeGreaterThanOrEqual(DUST.floorHz);
        expect(open).toBeLessThanOrEqual(DUST.openHz);
      })
    );
    expect(dustLowpassHz(0)).toBe(DUST.openHz);
  });

  it('adds no muffling layer (null) when A_V is missing or negative', () => {
    fc.assert(
      fc.property(fc.oneof(missingValue, fc.double({ min: -100, max: -1e-9, noNaN: true })), (a) => {
        expect(dustLowpassHz(a)).toBeNull();
      })
    );
  });
});
