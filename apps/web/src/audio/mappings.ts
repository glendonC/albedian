/**
 * Pure data-to-parameter mappings for the Albedian spatial audio engine.
 *
 * One perceptual channel per physical quantity:
 *   distance            -> direct-to-reverberant ratio + high-frequency rolloff
 *   luminosity          -> source power, attenuated by the inverse-square law
 *   effective temp.     -> timbre brightness (spectral centroid from the Planck curve)
 *   sky brightness      -> level of a broadband noise bed (artificial skyglow only)
 *   residual            -> beat rate between two partials, clean interval at zero
 *   pulsar spin         -> pulse rate equal to the catalogue spin frequency, unscaled
 *   extinction A_V      -> low-pass muffling
 *
 * Every mapping returns `null` when its input is missing or invalid; the engine renders
 * `null` as silence for that layer and never substitutes a default value. Nothing in this
 * file touches Web Audio, so all of it runs, and is property-tested, in Node.
 */

export type ScaleAudioRegime =
  | 'earth_capture'
  | 'cislunar'
  | 'solar_neighborhood'
  | 'deep_sky'
  | 'planetary_surface';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Geometric interpolation, so equal steps of `t` are equal steps of log frequency. */
export function logLerp(a: number, b: number, t: number): number {
  return a * Math.pow(b / a, t);
}

export function dbToGain(db: number): number {
  return db === -Infinity ? 0 : Math.pow(10, db / 20);
}

export function gainToDb(gain: number): number {
  return gain <= 0 ? -Infinity : 20 * Math.log10(gain);
}

/**
 * Pogson's scale: one magnitude is a flux ratio of 10^0.4, which is exactly 4 dB of
 * intensity (10 * log10(10^0.4) = 4). Used wherever a magnitude becomes a level.
 */
export const DB_PER_MAGNITUDE = 4;

// ---------------------------------------------------------------------------
// Distance: direct-to-reverberant ratio (DRR) and high-frequency rolloff
// ---------------------------------------------------------------------------

/** IAU 2012 Resolution B2 astronomical unit and the parsec derived from it, in km. */
export const PARSEC_KM = 3.0856775814913673e13;
const PC_PER_KM = 1 / PARSEC_KM;

export interface DistanceWindow {
  readonly nearPc: number;
  readonly farPc: number;
}

/**
 * Log-distance window per scale regime. This is a display choice, like the scale bar of a
 * map: the distance cues sweep their whole range across the window of the regime the user
 * is in, so a few decades of distance stay resolvable by ear. It is not data.
 */
export const REGIME_DISTANCE_WINDOWS: Readonly<Record<ScaleAudioRegime, DistanceWindow>> = {
  earth_capture: { nearPc: 1, farPc: 2000 },
  cislunar: { nearPc: 1e3 * PC_PER_KM, farPc: 1e7 * PC_PER_KM },
  solar_neighborhood: { nearPc: 1, farPc: 1000 },
  deep_sky: { nearPc: 1e3, farPc: 1e8 },
  planetary_surface: { nearPc: 1e3 * PC_PER_KM, farPc: 1e9 * PC_PER_KM },
};

export function isValidDistanceWindow(window: DistanceWindow | null | undefined): window is DistanceWindow {
  return (
    !!window &&
    isFiniteNumber(window.nearPc) &&
    isFiniteNumber(window.farPc) &&
    window.nearPc > 0 &&
    window.farPc > window.nearPc
  );
}

export interface DistanceCueParams {
  /** DRR at the near edge of the window, dB (dry and close). */
  readonly drrNearDb: number;
  /** DRR at the far edge of the window, dB (wet and distant). */
  readonly drrFarDb: number;
  readonly lowpassNearHz: number;
  readonly lowpassFarHz: number;
}

export const DISTANCE_CUES: DistanceCueParams = {
  drrNearDb: 15,
  drrFarDb: -15,
  lowpassNearHz: 18000,
  lowpassFarHz: 3000,
};

/** Position of a distance inside the regime window: 0 at the near edge, 1 at the far edge. */
export function perceptualDistance(
  distancePc: number | null | undefined,
  window: DistanceWindow
): number | null {
  if (!isFiniteNumber(distancePc) || distancePc <= 0) return null;
  if (!isValidDistanceWindow(window)) return null;
  const near = Math.log10(window.nearPc);
  const far = Math.log10(window.farPc);
  return clamp((Math.log10(distancePc) - near) / (far - near), 0, 1);
}

export function directToReverberantDb(
  perceptual: number | null | undefined,
  params: DistanceCueParams = DISTANCE_CUES
): number | null {
  if (!isFiniteNumber(perceptual)) return null;
  return lerp(params.drrNearDb, params.drrFarDb, clamp(perceptual, 0, 1));
}

/**
 * Splits unit energy between the dry path and the reverb send so that
 * dry^2 / wet^2 is the requested ratio and dry^2 + wet^2 = 1. Loudness stays the job of
 * the luminosity mapping; this only moves energy between direct and reverberant.
 * Assumes the impulse response is normalized to unit energy (see `energyNormalize`).
 */
export function directReverbGains(drrDb: number): { direct: number; reverb: number } {
  const ratio = Math.pow(10, clamp(drrDb, -120, 120) / 10);
  return { direct: Math.sqrt(ratio / (1 + ratio)), reverb: Math.sqrt(1 / (1 + ratio)) };
}

export function distanceLowpassHz(
  perceptual: number | null | undefined,
  params: DistanceCueParams = DISTANCE_CUES
): number | null {
  if (!isFiniteNumber(perceptual)) return null;
  return logLerp(params.lowpassNearHz, params.lowpassFarHz, clamp(perceptual, 0, 1));
}

// ---------------------------------------------------------------------------
// Luminosity: loudness at the source, attenuated with distance
// ---------------------------------------------------------------------------

/** IAU 2015 Resolution B2: absolute bolometric magnitude of the nominal solar luminosity. */
export const SUN_ABSOLUTE_BOLOMETRIC_MAG = 4.74;

export interface LoudnessParams {
  /** Apparent bolometric magnitude that plays at `referenceLevelDb`. */
  readonly referenceMag: number;
  readonly referenceLevelDb: number;
  /** 1 maps flux ratios to acoustic intensity ratios (4 dB per magnitude). */
  readonly compression: number;
  readonly ceilingDb: number;
  /** Fainter than this is inaudible (silent because faint, not because data is missing). */
  readonly floorDb: number;
}

export const LOUDNESS: LoudnessParams = {
  referenceMag: -1.5,
  referenceLevelDb: -10,
  compression: 1,
  ceilingDb: -6,
  floorDb: -60,
};

/** Apparent bolometric magnitude from luminosity and distance, with no extinction term. */
export function apparentBolometricMagnitude(
  luminositySolar: number | null | undefined,
  distancePc: number | null | undefined
): number | null {
  if (!isFiniteNumber(luminositySolar) || luminositySolar <= 0) return null;
  if (!isFiniteNumber(distancePc) || distancePc <= 0) return null;
  return (
    SUN_ABSOLUTE_BOLOMETRIC_MAG - 2.5 * Math.log10(luminositySolar) + 5 * Math.log10(distancePc / 10)
  );
}

/**
 * Level of one voice in dBFS. Source power follows luminosity and the direct path falls with
 * the inverse-square law, so with `compression` 1 the result is 4 dB per magnitude of
 * apparent bolometric brightness. Returns -Infinity below the audibility floor and `null`
 * when an input is missing.
 */
export function receivedLevelDb(
  luminositySolar: number | null | undefined,
  distancePc: number | null | undefined,
  params: LoudnessParams = LOUDNESS
): number | null {
  const mag = apparentBolometricMagnitude(luminositySolar, distancePc);
  if (mag === null) return null;
  const level =
    params.referenceLevelDb - DB_PER_MAGNITUDE * params.compression * (mag - params.referenceMag);
  if (!(level >= params.floorDb)) return -Infinity;
  return Math.min(params.ceilingDb, level);
}

// ---------------------------------------------------------------------------
// Effective temperature: timbre brightness via the Planck curve
// ---------------------------------------------------------------------------

export interface TimbreParams {
  readonly referenceTeffK: number;
  /** Power-weighted spectral centroid given to a star at `referenceTeffK`, Hz. */
  readonly referenceCentroidHz: number;
  readonly minTeffK: number;
  readonly maxTeffK: number;
}

export const TIMBRE: TimbreParams = {
  referenceTeffK: 5772,
  referenceCentroidHz: 1000,
  minTeffK: 2000,
  maxTeffK: 40000,
};

/**
 * Power-weighted mean frequency of the Planck spectrum B_nu in units of kT/h:
 * the integral of x^4/(e^x - 1) over that of x^3/(e^x - 1), which is 4 zeta(5) / zeta(4).
 */
export const PLANCK_MEAN_X = (4 * 1.0369277551433699) / (Math.PI ** 4 / 90);

/**
 * The audio frequency standing in for kT/h. The voice spectrum is the star's Planck curve
 * scaled by one fixed factor into hearing, so the centroid is proportional to Teff
 * (Wien displacement). Teff outside [minTeffK, maxTeffK] saturates at the edge.
 */
export function planckFrequencyScaleHz(
  teffK: number | null | undefined,
  params: TimbreParams = TIMBRE
): number | null {
  if (!isFiniteNumber(teffK) || teffK <= 0) return null;
  const t = clamp(teffK, params.minTeffK, params.maxTeffK);
  return (params.referenceCentroidHz / PLANCK_MEAN_X) * (t / params.referenceTeffK);
}

/**
 * Amplitudes of harmonics 1..count (index 0 is harmonic 1) whose powers sample the Planck
 * curve, normalized to unit total power so timbre does not change the voice's power.
 */
export function planckHarmonicAmplitudes(
  teffK: number | null | undefined,
  fundamentalHz: number,
  harmonicCount: number,
  params: TimbreParams = TIMBRE
): Float64Array | null {
  const scale = planckFrequencyScaleHz(teffK, params);
  if (scale === null || !isFiniteNumber(fundamentalHz) || fundamentalHz <= 0) return null;
  if (!Number.isInteger(harmonicCount) || harmonicCount < 1) return null;
  const amplitudes = new Float64Array(harmonicCount);
  let power = 0;
  for (let n = 1; n <= harmonicCount; n++) {
    const x = (n * fundamentalHz) / scale;
    const p = (x * x * x) / Math.expm1(x);
    amplitudes[n - 1] = Math.sqrt(p);
    power += p;
  }
  if (!(power > 0)) return null;
  const norm = 1 / Math.sqrt(power);
  for (let i = 0; i < harmonicCount; i++) amplitudes[i] = (amplitudes[i] ?? 0) * norm;
  return amplitudes;
}

/** Power-weighted spectral centroid of a harmonic tone, Hz. */
export function harmonicCentroidHz(amplitudes: ArrayLike<number>, fundamentalHz: number): number {
  let weighted = 0;
  let total = 0;
  for (let i = 0; i < amplitudes.length; i++) {
    const p = (amplitudes[i] ?? 0) ** 2;
    weighted += (i + 1) * fundamentalHz * p;
    total += p;
  }
  return total > 0 ? weighted / total : 0;
}

/**
 * Ballesteros (2012, EPL 97, 34008, Eq. 14) black-body temperature from B-V. Used only by
 * the legacy lock-tone API. The paper states no validity range for Eq. 14; colours outside
 * [-0.5, 3.0] are treated as missing instead of extrapolated toward the pole at -0.67.
 */
export function ballesterosTeffFromBv(bv: number | null | undefined): number | null {
  if (!isFiniteNumber(bv) || bv < -0.5 || bv > 3) return null;
  return 4600 * (1 / (0.92 * bv + 1.7) + 1 / (0.92 * bv + 0.62));
}

// ---------------------------------------------------------------------------
// Skyglow: broadband bed level from sky brightness
// ---------------------------------------------------------------------------

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
  const u = isFiniteNumber(unveil) ? clamp(unveil, 0, 1) : 0;
  const effective = ratio * (1 - u);
  if (!(effective > 0)) return -Infinity;
  const level = params.referenceLevelDb + 10 * params.compression * Math.log10(effective);
  if (!(level >= params.floorDb)) return -Infinity;
  return Math.min(params.ceilingDb, level);
}

// ---------------------------------------------------------------------------
// Uncertainty: beating that resolves to a clean interval at lock
// ---------------------------------------------------------------------------

export interface UncertaintyParams {
  /** Residual at which the beat rate saturates, arcsec. */
  readonly fullScaleResidualArcsec: number;
  readonly maxBeatHz: number;
}

export const UNCERTAINTY: UncertaintyParams = {
  fullScaleResidualArcsec: 120,
  maxBeatHz: 4,
};

/** Beat rate proportional to the residual, saturating at `maxBeatHz`; 0 means locked. */
export function uncertaintyBeatHz(
  residualArcsec: number | null | undefined,
  params: UncertaintyParams = UNCERTAINTY
): number | null {
  if (!isFiniteNumber(residualArcsec) || residualArcsec < 0) return null;
  return params.maxBeatHz * Math.min(1, residualArcsec / params.fullScaleResidualArcsec);
}

/**
 * A just fifth (3:2) whose upper tone is raised by beatHz / 2, so the lower tone's third
 * harmonic and the upper tone's second harmonic beat at exactly beatHz. At zero the
 * interval is clean.
 */
export function beatingFifthHz(lowerHz: number, beatHz: number): { lowerHz: number; upperHz: number } {
  return { lowerHz, upperHz: 1.5 * lowerHz + beatHz / 2 };
}

// ---------------------------------------------------------------------------
// Pulsars: real spin rates, no scaling
// ---------------------------------------------------------------------------

export const PULSAR_MAX_SPIN_HZ = 2000;

/**
 * The pulse rate is the catalogue spin frequency itself (ATNF `F0`), so the Crab plays at
 * 29.95 Hz and Vela at 11.19 Hz. Values outside (0, 2000] Hz are treated as invalid.
 */
export function pulsarPulseRateHz(spinFrequencyHz: number | null | undefined): number | null {
  if (!isFiniteNumber(spinFrequencyHz) || spinFrequencyHz <= 0 || spinFrequencyHz > PULSAR_MAX_SPIN_HZ) {
    return null;
  }
  return spinFrequencyHz;
}

// ---------------------------------------------------------------------------
// Dust column: low-pass muffling from A_V
// ---------------------------------------------------------------------------

export interface DustParams {
  readonly openHz: number;
  /** Magnitudes of visual extinction that move the cutoff down one octave. */
  readonly magnitudesPerOctave: number;
  readonly floorHz: number;
}

export const DUST: DustParams = {
  openHz: 20000,
  magnitudesPerOctave: 1,
  floorHz: 120,
};

/** Low-pass cutoff for a sightline with the given A_V; `null` (no muffling layer) if unknown. */
export function dustLowpassHz(
  extinctionAvMag: number | null | undefined,
  params: DustParams = DUST
): number | null {
  if (!isFiniteNumber(extinctionAvMag) || extinctionAvMag < 0) return null;
  return clamp(
    params.openHz * Math.pow(2, -extinctionAvMag / params.magnitudesPerOctave),
    params.floorHz,
    params.openHz
  );
}
