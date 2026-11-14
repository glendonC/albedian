/** Highest harmonic a star tone carries by default; listening tests may lower it (VoicePool option). */
export const DEFAULT_HARMONIC_CEILING_HZ = 16000;

export const MAX_HARMONICS = 256;
export const NYQUIST_HARMONIC_FRACTION = 0.45;
export const PARAM_TIME_CONSTANT_SEC = 0.06;
export const POSITION_TIME_CONSTANT_SEC = 0.03;
export const ATTACK_TIME_CONSTANT_SEC = 0.08;
export const RELEASE_TIME_CONSTANT_SEC = 0.12;
/** About 8 release time constants: the envelope is ~70 dB down when the sources stop. */
export const RELEASE_STOP_AFTER_SEC = 1.0;
export const REUSE_FADE_TIME_CONSTANT_SEC = 0.012;
export const LAYER_STOP_AFTER_SEC = 0.02;

/** Uncertainty fifth sits 9 dB under the star tone. Not data; a balance for listening. */
export const UNCERTAINTY_RELATIVE_GAIN = 0.35;
/** Harmonics 1..3 of each uncertainty tone; equal 2nd and 3rd so the beating pair matches. */
export const UNCERTAINTY_HARMONICS: readonly number[] = [1, 0.5, 0.5];

/** Generic pulse width: 3% of the period at half maximum. Only the rate is data. */
export const PULSE_FWHM_FRACTION = 0.03;
export const PULSAR_NOISE_SECONDS = 2;
export const PULSAR_NOISE_RMS = 0.5;
export const PULSAR_NOISE_SEED = 0x9e3779b9;

/** Biquad lowpass Q is in dB in Web Audio: -3.01 dB is Butterworth, -6.02 dB is Q 0.5. */
export const DUST_Q_DB = -3.0103;
export const DISTANCE_Q_DB = -6.0206;

export const FALLBACK_PITCH_HZ = 110;

/** What one slot is doing, for snapshots and the dev harness. */
export interface SlotState {
  readonly slot: number;
  readonly id: string;
  readonly pitchHz: number;
  readonly centroidHz: number | null;
}

export interface StarGenerator {
  readonly osc: OscillatorNode;
  teffK: number;
  pitchHz: number;
  centroidHz: number;
}

export interface UncertaintyGenerator {
  readonly lower: OscillatorNode;
  readonly upper: OscillatorNode;
  readonly gain: GainNode;
}

export interface PulsarGenerator {
  readonly noise: AudioBufferSourceNode;
  readonly envelopeOsc: OscillatorNode;
  readonly vca: GainNode;
}

export interface VoiceGenerators {
  readonly envelope: GainNode;
  star: StarGenerator | null;
  pulsar: PulsarGenerator | null;
  uncertainty: UncertaintyGenerator | null;
}

export interface ReleasingTail {
  readonly generators: VoiceGenerators;
  readonly stopAt: number;
}

export interface SharedVoiceResources {
  readonly noise: AudioBuffer;
  readonly pulseWave: PeriodicWave;
  readonly pulseDc: number;
  readonly uncertaintyWave: PeriodicWave;
  readonly harmonicCeilingHz: number;
}
