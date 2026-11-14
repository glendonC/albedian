import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import * as mappingsModule from '../mappings';
import {
  DB_PER_MAGNITUDE,
  DISTANCE_CUES,
  DUST,
  LOUDNESS,
  PARSEC_KM,
  PLANCK_MEAN_X,
  PULSAR_MAX_SPIN_HZ,
  REGIME_DISTANCE_WINDOWS,
  SKYGLOW,
  STAR_DISTANCE_WINDOW,
  SUN_ABSOLUTE_BOLOMETRIC_MAG,
  TIMBRE,
  UNCERTAINTY,
  apparentBolometricMagnitude,
  artificialSkyglowRatio,
  ballesterosTeffFromBv,
  beatingFifthHz,
  clamp,
  dbToGain,
  directReverbGains,
  directToReverberantDb,
  distanceLowpassHz,
  dustLowpassHz,
  gainToDb,
  harmonicCentroidHz,
  isFiniteNumber,
  isValidDistanceWindow,
  lerp,
  logLerp,
  perceptualDistance,
  planckFrequencyScaleHz,
  planckHarmonicAmplitudes,
  pulsarPulseRateHz,
  receivedLevelDb,
  skyglowBedLevelDb,
  uncertaintyBeatHz,
  type DistanceCueParams,
  type DistanceWindow,
  type DustParams,
  type LoudnessParams,
  type ScaleAudioRegime,
  type SkyglowParams,
  type TimbreParams,
  type UncertaintyParams,
} from '../mappings';

const EXPECTED_RUNTIME_EXPORTS = [
  'DB_PER_MAGNITUDE',
  'DISTANCE_CUES',
  'DUST',
  'LOUDNESS',
  'PARSEC_KM',
  'PLANCK_MEAN_X',
  'PULSAR_MAX_SPIN_HZ',
  'REGIME_DISTANCE_WINDOWS',
  'SKYGLOW',
  'STAR_DISTANCE_WINDOW',
  'SUN_ABSOLUTE_BOLOMETRIC_MAG',
  'TIMBRE',
  'UNCERTAINTY',
  'apparentBolometricMagnitude',
  'artificialSkyglowRatio',
  'ballesterosTeffFromBv',
  'beatingFifthHz',
  'clamp',
  'dbToGain',
  'directReverbGains',
  'directToReverberantDb',
  'distanceLowpassHz',
  'dustLowpassHz',
  'gainToDb',
  'harmonicCentroidHz',
  'isFiniteNumber',
  'isValidDistanceWindow',
  'lerp',
  'logLerp',
  'perceptualDistance',
  'planckFrequencyScaleHz',
  'planckHarmonicAmplitudes',
  'pulsarPulseRateHz',
  'receivedLevelDb',
  'skyglowBedLevelDb',
  'uncertaintyBeatHz',
] as const;

const EXPECTED_FUNCTIONS = [
  'apparentBolometricMagnitude',
  'artificialSkyglowRatio',
  'ballesterosTeffFromBv',
  'beatingFifthHz',
  'clamp',
  'dbToGain',
  'directReverbGains',
  'directToReverberantDb',
  'distanceLowpassHz',
  'dustLowpassHz',
  'gainToDb',
  'harmonicCentroidHz',
  'isFiniteNumber',
  'isValidDistanceWindow',
  'lerp',
  'logLerp',
  'perceptualDistance',
  'planckFrequencyScaleHz',
  'planckHarmonicAmplitudes',
  'pulsarPulseRateHz',
  'receivedLevelDb',
  'skyglowBedLevelDb',
  'uncertaintyBeatHz',
] as const;

describe('mappings public surface and constants characterization', () => {
  it('exports the exact 36 runtime bindings (13 constants and 23 functions)', () => {
    expect(Object.keys(mappingsModule).sort()).toEqual([...EXPECTED_RUNTIME_EXPORTS]);
    expect(EXPECTED_RUNTIME_EXPORTS).toHaveLength(36);
    expect(EXPECTED_FUNCTIONS).toHaveLength(23);
    for (const fnName of EXPECTED_FUNCTIONS) {
      expect(typeof mappingsModule[fnName]).toBe('function');
    }
  });

  it('preserves all exported constant values and regime distance windows', () => {
    const regimes: readonly ScaleAudioRegime[] = [
      'earth_capture',
      'cislunar',
      'solar_neighborhood',
      'deep_sky',
      'planetary_surface',
    ];
    expect(Object.keys(REGIME_DISTANCE_WINDOWS).sort()).toEqual([...regimes].sort());

    expect(DB_PER_MAGNITUDE).toBe(4);
    expect(PARSEC_KM).toBe(3.0856775814913673e13);
    expect(SUN_ABSOLUTE_BOLOMETRIC_MAG).toBe(4.74);
    expect(PULSAR_MAX_SPIN_HZ).toBe(2000);
    expect(PLANCK_MEAN_X).toBeCloseTo((4 * 1.0369277551433699) / (Math.PI ** 4 / 90), 15);

    const pcPerKm = 1 / PARSEC_KM;
    expect(STAR_DISTANCE_WINDOW).toEqual({ nearPc: 1, farPc: 2000 });
    expect(REGIME_DISTANCE_WINDOWS).toEqual({
      earth_capture: { nearPc: 1, farPc: 2000 },
      cislunar: { nearPc: 1e3 * pcPerKm, farPc: 1e7 * pcPerKm },
      solar_neighborhood: { nearPc: 1, farPc: 1000 },
      deep_sky: { nearPc: 1e3, farPc: 1e8 },
      planetary_surface: { nearPc: 1e3 * pcPerKm, farPc: 1e9 * pcPerKm },
    });

    expect(DISTANCE_CUES).toEqual({
      drrNearDb: 15,
      drrFarDb: -15,
      lowpassNearHz: 18000,
      lowpassFarHz: 3000,
    });
    expect(LOUDNESS).toEqual({
      referenceMag: -1.5,
      referenceLevelDb: -10,
      compression: 1,
      ceilingDb: -6,
      floorDb: -60,
    });
    expect(TIMBRE).toEqual({
      referenceTeffK: 5772,
      referenceCentroidHz: 1000,
      minTeffK: 2000,
      maxTeffK: 40000,
    });
    expect(SKYGLOW).toEqual({
      naturalSkyMagArcsec2: 22.0,
      referenceLevelDb: -34,
      compression: 1,
      ceilingDb: -12,
      floorDb: -72,
    });
    expect(UNCERTAINTY).toEqual({
      fullScaleResidualArcsec: 120,
      maxBeatHz: 4,
    });
    expect(DUST).toEqual({
      openHz: 20000,
      magnitudesPerOctave: 1,
      floorHz: 120,
    });
  });
});

describe('shared numeric and decibel helpers', () => {
  it('guards finite numbers strictly against non-numbers, NaN, and infinities', () => {
    for (const valid of [0, -0, 1, -42.5, Number.MIN_VALUE, Number.MAX_VALUE, Number.EPSILON]) {
      expect(isFiniteNumber(valid)).toBe(true);
    }
    for (const invalid of [
      Number.NaN,
      Infinity,
      -Infinity,
      null,
      undefined,
      '0',
      '42',
      true,
      false,
      {},
      [],
      0n,
      Symbol('x'),
    ]) {
      expect(isFiniteNumber(invalid)).toBe(false);
    }
  });

  it('clamps, linearly interpolates, and geometrically interpolates across ranges', () => {
    expect(clamp(-5, 0, 10)).toBe(0);
    expect(clamp(15, 0, 10)).toBe(10);
    expect(clamp(4.2, 0, 10)).toBe(4.2);
    expect(clamp(7, 3, 3)).toBe(3);

    expect(lerp(15, -15, 0)).toBe(15);
    expect(lerp(15, -15, 0.5)).toBe(0);
    expect(lerp(15, -15, 1)).toBe(-15);
    expect(lerp(10, 30, 0.25)).toBe(15);

    expect(logLerp(18000, 3000, 0)).toBe(18000);
    expect(logLerp(18000, 3000, 1)).toBeCloseTo(3000, 10);
    expect(logLerp(18000, 3000, 0.5)).toBeCloseTo(Math.sqrt(18000 * 3000), 10);

    fc.assert(
      fc.property(
        fc.double({ min: 1, max: 50000, noNaN: true }),
        fc.double({ min: 1, max: 50000, noNaN: true }),
        fc.double({ min: 0, max: 0.75, noNaN: true }),
        (a, b, t) => {
          const step = 0.25;
          const r1 = logLerp(a, b, t + step) / logLerp(a, b, t);
          const r0 = logLerp(a, b, step) / logLerp(a, b, 0);
          expect(r1).toBeCloseTo(r0, 9);
        }
      )
    );
  });

  it('converts between decibels and linear amplitude gain with exact silence handling', () => {
    expect(dbToGain(-Infinity)).toBe(0);
    expect(dbToGain(0)).toBe(1);
    expect(dbToGain(20)).toBeCloseTo(10, 12);
    expect(dbToGain(-20)).toBeCloseTo(0.1, 12);
    expect(dbToGain(-6)).toBeCloseTo(Math.pow(10, -6 / 20), 12);

    expect(gainToDb(0)).toBe(-Infinity);
    expect(gainToDb(-0)).toBe(-Infinity);
    expect(gainToDb(-1)).toBe(-Infinity);
    expect(gainToDb(-Infinity)).toBe(-Infinity);
    expect(gainToDb(1)).toBe(0);
    expect(gainToDb(10)).toBeCloseTo(20, 12);
    expect(gainToDb(0.1)).toBeCloseTo(-20, 12);

    fc.assert(
      fc.property(fc.double({ min: -160, max: 160, noNaN: true }), (db) => {
        const gain = dbToGain(db);
        expect(gain).toBeGreaterThan(0);
        expect(gainToDb(gain)).toBeCloseTo(db, 9);
      })
    );
  });
});

describe('distance window validation and cue parameter overrides', () => {
  it('rejects malformed distance windows and accepts strictly ordered positive finite windows', () => {
    expect(isValidDistanceWindow(null)).toBe(false);
    expect(isValidDistanceWindow(undefined)).toBe(false);
    expect(isValidDistanceWindow({ nearPc: 0, farPc: 10 })).toBe(false);
    expect(isValidDistanceWindow({ nearPc: -1, farPc: 10 })).toBe(false);
    expect(isValidDistanceWindow({ nearPc: 10, farPc: 10 })).toBe(false);
    expect(isValidDistanceWindow({ nearPc: 20, farPc: 10 })).toBe(false);
    expect(isValidDistanceWindow({ nearPc: Number.NaN, farPc: 10 })).toBe(false);
    expect(isValidDistanceWindow({ nearPc: 1, farPc: Infinity })).toBe(false);

    const validWindow: DistanceWindow = { nearPc: 2, farPc: 200 };
    expect(isValidDistanceWindow(validWindow)).toBe(true);
    expect(perceptualDistance(2, validWindow)).toBe(0);
    expect(perceptualDistance(20, validWindow)).toBeCloseTo(0.5, 12);
    expect(perceptualDistance(200, validWindow)).toBe(1);
    expect(perceptualDistance(0.5, validWindow)).toBe(0);
    expect(perceptualDistance(5000, validWindow)).toBe(1);
    expect(perceptualDistance(10, { nearPc: 10, farPc: 10 })).toBeNull();
  });

  it('clamps out-of-range perceptual positions and honors custom DistanceCueParams', () => {
    const customCues: DistanceCueParams = {
      drrNearDb: 12,
      drrFarDb: -18,
      lowpassNearHz: 16000,
      lowpassFarHz: 4000,
    };
    expect(directToReverberantDb(-0.5, customCues)).toBe(12);
    expect(directToReverberantDb(0.5, customCues)).toBe(-3);
    expect(directToReverberantDb(1.5, customCues)).toBe(-18);
    expect(directToReverberantDb(Number.NaN, customCues)).toBeNull();
    expect(directToReverberantDb(Infinity, customCues)).toBeNull();

    expect(distanceLowpassHz(-1, customCues)).toBe(16000);
    expect(distanceLowpassHz(0.5, customCues)).toBeCloseTo(8000, 10);
    expect(distanceLowpassHz(2, customCues)).toBeCloseTo(4000, 10);
    expect(distanceLowpassHz(-Infinity, customCues)).toBeNull();

    const equal = directReverbGains(0);
    expect(equal.direct).toBeCloseTo(Math.SQRT1_2, 12);
    expect(equal.reverb).toBeCloseTo(Math.SQRT1_2, 12);
    expect(directReverbGains(500)).toEqual(directReverbGains(120));
    expect(directReverbGains(-500)).toEqual(directReverbGains(-120));
  });
});

describe('luminosity, Planck timbre, skyglow, uncertainty, pulsar, and dust edge semantics', () => {
  it('preserves inverse-square invariance and floor/ceiling thresholds in receivedLevelDb', () => {
    expect(apparentBolometricMagnitude(100, 100)).toBeCloseTo(SUN_ABSOLUTE_BOLOMETRIC_MAG, 12);
    expect(apparentBolometricMagnitude(0, 10)).toBeNull();
    expect(apparentBolometricMagnitude(1, 0)).toBeNull();

    // At apparent bolometric magnitude -1.5, receivedLevelDb equals referenceLevelDb (-10 dB).
    const lumAtRef = Math.pow(10, -0.4 * (LOUDNESS.referenceMag - SUN_ABSOLUTE_BOLOMETRIC_MAG));
    expect(receivedLevelDb(lumAtRef, 10)).toBeCloseTo(-10, 12);
    // Very luminous nearby source saturates at ceilingDb (-6 dB).
    expect(receivedLevelDb(1e6, 1)).toBe(-6);
    // Very faint distant source drops below floorDb (-60 dB) to -Infinity.
    expect(receivedLevelDb(1e-4, 1e6)).toBe(-Infinity);

    const customLoudness: LoudnessParams = {
      referenceMag: 0,
      referenceLevelDb: -12,
      compression: 0.5,
      ceilingDb: -4,
      floorDb: -30,
    };
    // Exact floor boundary: mag = 9 -> -12 - 4 * 0.5 * 9 = -30 >= -30 -> -30 (not -Infinity).
    const lumAtFloor = Math.pow(10, -0.4 * (9 - SUN_ABSOLUTE_BOLOMETRIC_MAG));
    expect(receivedLevelDb(lumAtFloor, 10, customLoudness)).toBeCloseTo(-30, 10);
  });

  it('clamps Teff, rejects malformed harmonic requests, handles empty centroids, and pins Ballesteros bounds', () => {
    const customTimbre: TimbreParams = {
      referenceTeffK: 5000,
      referenceCentroidHz: 800,
      minTeffK: 2500,
      maxTeffK: 25000,
    };
    expect(planckFrequencyScaleHz(1000, customTimbre)).toBeCloseTo(
      planckFrequencyScaleHz(2500, customTimbre)!,
      12
    );
    expect(planckFrequencyScaleHz(90000, customTimbre)).toBeCloseTo(
      planckFrequencyScaleHz(25000, customTimbre)!,
      12
    );

    expect(planckHarmonicAmplitudes(5772, 0, 16)).toBeNull();
    expect(planckHarmonicAmplitudes(5772, -110, 16)).toBeNull();
    expect(planckHarmonicAmplitudes(5772, Number.NaN, 16)).toBeNull();
    expect(planckHarmonicAmplitudes(5772, 220, 0)).toBeNull();
    expect(planckHarmonicAmplitudes(5772, 220, -4)).toBeNull();
    expect(planckHarmonicAmplitudes(5772, 220, 2.5)).toBeNull();
    // Extreme fundamental overflows expm1(x) so total power is 0 -> null.
    expect(planckHarmonicAmplitudes(2000, 1e9, 4)).toBeNull();

    expect(harmonicCentroidHz([], 220)).toBe(0);
    expect(harmonicCentroidHz([0, 0, 0], 220)).toBe(0);
    expect(harmonicCentroidHz([0, 0, 5, 0], 200)).toBeCloseTo(600, 12);

    // Exact inclusive domain endpoints [-0.5, 3.0] for Ballesteros (2012) Eq. 14.
    expect(ballesterosTeffFromBv(-0.5)).toBeCloseTo(
      4600 * (1 / (0.92 * -0.5 + 1.7) + 1 / (0.92 * -0.5 + 0.62)),
      9
    );
    expect(ballesterosTeffFromBv(3)).toBeCloseTo(
      4600 * (1 / (0.92 * 3 + 1.7) + 1 / (0.92 * 3 + 0.62)),
      9
    );
    expect(ballesterosTeffFromBv(-0.500001)).toBeNull();
    expect(ballesterosTeffFromBv(3.000001)).toBeNull();
  });

  it('handles unveil clamping and non-finite unveil fallback in skyglowBedLevelDb', () => {
    const customSkyglow: SkyglowParams = {
      naturalSkyMagArcsec2: 21.5,
      referenceLevelDb: -30,
      compression: 1,
      ceilingDb: -10,
      floorDb: -60,
    };
    const twiceNatural = 21.5 - 2.5 * Math.log10(2);
    expect(artificialSkyglowRatio(twiceNatural, customSkyglow)).toBeCloseTo(1, 12);
    // Non-finite unveil is treated as 0 (no unveiling applied).
    expect(skyglowBedLevelDb(twiceNatural, Number.NaN, customSkyglow)).toBeCloseTo(-30, 9);
    expect(skyglowBedLevelDb(twiceNatural, -0.5, customSkyglow)).toBeCloseTo(-30, 9);
    expect(skyglowBedLevelDb(twiceNatural, 1.5, customSkyglow)).toBe(-Infinity);
    // Very bright urban sky clamps to ceilingDb.
    expect(skyglowBedLevelDb(12, 0, customSkyglow)).toBe(-10);
  });

  it('respects exact boundary conditions for uncertainty, pulsar spin, and dust extinction', () => {
    const customUncertainty: UncertaintyParams = {
      fullScaleResidualArcsec: 60,
      maxBeatHz: 6,
    };
    expect(uncertaintyBeatHz(30, customUncertainty)).toBeCloseTo(3, 12);
    expect(uncertaintyBeatHz(120, customUncertainty)).toBe(6);
    expect(beatingFifthHz(200, 4)).toEqual({ lowerHz: 200, upperHz: 302 });

    expect(pulsarPulseRateHz(0)).toBeNull();
    expect(pulsarPulseRateHz(-0)).toBeNull();
    expect(pulsarPulseRateHz(PULSAR_MAX_SPIN_HZ)).toBe(2000);
    expect(pulsarPulseRateHz(PULSAR_MAX_SPIN_HZ + 1e-9)).toBeNull();

    const customDust: DustParams = {
      openHz: 16000,
      magnitudesPerOctave: 2,
      floorHz: 250,
    };
    expect(dustLowpassHz(0, customDust)).toBe(16000);
    expect(dustLowpassHz(2, customDust)).toBeCloseTo(8000, 10);
    expect(dustLowpassHz(4, customDust)).toBeCloseTo(4000, 10);
    expect(dustLowpassHz(40, customDust)).toBe(250);
  });
});
