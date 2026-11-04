import {
  gaussianPulseTrainSeries,
  periodicWaveCoefficients,
  schroederPhases,
  whiteNoise,
} from '../dsp.js';
import { beatingFifthHz, dbToGain, harmonicCentroidHz, planckHarmonicAmplitudes } from '../mappings.js';
import { IDENTITY_PITCHES_HZ, type Vec3, type VoicePlan } from '../voicePlanning.js';
import { glideParam, setParamNow } from './createAudioContext.js';

const HARMONIC_CEILING_HZ = 16000;
const MAX_HARMONICS = 256;
const PARAM_TIME_CONSTANT_SEC = 0.06;
const POSITION_TIME_CONSTANT_SEC = 0.03;
const ATTACK_TIME_CONSTANT_SEC = 0.08;
const RELEASE_TIME_CONSTANT_SEC = 0.12;
/** About 8 release time constants: the envelope is ~70 dB down when the sources stop. */
const RELEASE_STOP_AFTER_SEC = 1.0;
const REUSE_FADE_TIME_CONSTANT_SEC = 0.012;
/** Uncertainty fifth sits 9 dB under the star tone. Not data; a balance for listening. */
const UNCERTAINTY_RELATIVE_GAIN = 0.35;
/** Harmonics 1..3 of each uncertainty tone; equal 2nd and 3rd so the beating pair matches. */
const UNCERTAINTY_HARMONICS = [1, 0.5, 0.5];
/** Generic pulse width: 3% of the period at half maximum. Only the rate is data. */
const PULSE_FWHM_FRACTION = 0.03;
const PULSAR_NOISE_SECONDS = 2;
const PULSAR_NOISE_RMS = 0.5;
/** Biquad lowpass Q is in dB in Web Audio: -3.01 dB is Butterworth, -6.02 dB is Q 0.5. */
const DUST_Q_DB = -3.0103;
const DISTANCE_Q_DB = -6.0206;

interface StarGenerator {
  readonly osc: OscillatorNode;
  teffK: number;
  pitchHz: number;
  centroidHz: number;
}

interface UncertaintyGenerator {
  readonly lower: OscillatorNode;
  readonly upper: OscillatorNode;
  readonly gain: GainNode;
}

interface PulsarGenerator {
  readonly noise: AudioBufferSourceNode;
  readonly envelopeOsc: OscillatorNode;
  readonly vca: GainNode;
}

interface Generators {
  readonly envelope: GainNode;
  star: StarGenerator | null;
  pulsar: PulsarGenerator | null;
  uncertainty: UncertaintyGenerator | null;
}

/** What one slot is doing, for snapshots and the dev harness. */
export interface SlotState {
  readonly slot: number;
  readonly id: string;
  readonly pitchHz: number;
  readonly centroidHz: number | null;
}

function setPannerPosition(panner: PannerNode, v: Vec3, now: number, immediate: boolean): void {
  if (panner.positionX) {
    const apply = immediate
      ? (param: AudioParam, value: number) => setParamNow(param, value, now)
      : (param: AudioParam, value: number) => glideParam(param, value, now, POSITION_TIME_CONSTANT_SEC);
    apply(panner.positionX, v[0]);
    apply(panner.positionY, v[1]);
    apply(panner.positionZ, v[2]);
  } else {
    panner.setPosition(v[0], v[1], v[2]);
  }
}

function stopAll(generators: Generators, when: number): void {
  const nodes: AudioScheduledSourceNode[] = [];
  if (generators.star) nodes.push(generators.star.osc);
  if (generators.uncertainty) nodes.push(generators.uncertainty.lower, generators.uncertainty.upper);
  if (generators.pulsar) nodes.push(generators.pulsar.noise, generators.pulsar.envelopeOsc);
  nodes.forEach((node, i) => stopNode(node, when, i === 0 ? () => generators.envelope.disconnect() : undefined));
}

interface Tail {
  readonly generators: Generators;
  readonly stopAt: number;
}

function stopNode(node: AudioScheduledSourceNode, when: number, onEnded?: () => void): void {
  try {
    node.stop(when);
  } catch {
    // already stopped
  }
  if (onEnded) node.onended = onEnded;
}

class VoiceSlot {
  id: string | null = null;
  pitchHz = 0;
  private generators: Generators | null = null;
  private tails: Tail[] = [];
  private readonly sum: GainNode;
  private readonly dust: BiquadFilterNode;
  private readonly distance: BiquadFilterNode;
  private readonly level: GainNode;
  private readonly direct: GainNode;
  private readonly send: GainNode;
  private readonly panner: PannerNode;

  constructor(
    private readonly ctx: BaseAudioContext,
    readonly index: number,
    output: AudioNode,
    reverbInput: AudioNode,
    private readonly shared: SharedResources
  ) {
    this.sum = ctx.createGain();
    this.dust = ctx.createBiquadFilter();
    this.distance = ctx.createBiquadFilter();
    this.level = ctx.createGain();
    this.direct = ctx.createGain();
    this.send = ctx.createGain();
    this.panner = ctx.createPanner();

    const now = ctx.currentTime;
    this.dust.type = 'lowpass';
    setParamNow(this.dust.Q, DUST_Q_DB, now);
    this.distance.type = 'lowpass';
    setParamNow(this.distance.Q, DISTANCE_Q_DB, now);
    setParamNow(this.level.gain, 0, now);
    this.panner.panningModel = 'HRTF';
    // Distance is rendered by our own gains and filters; the panner only places direction.
    this.panner.distanceModel = 'inverse';
    this.panner.refDistance = 1;
    this.panner.rolloffFactor = 0;
    this.panner.coneInnerAngle = 360;
    this.panner.coneOuterAngle = 360;

    this.sum.connect(this.dust);
    this.dust.connect(this.distance);
    this.distance.connect(this.level);
    this.level.connect(this.direct);
    this.direct.connect(this.panner);
    this.panner.connect(output);
    this.level.connect(this.send);
    this.send.connect(reverbInput);
  }

  get centroidHz(): number | null {
    return this.generators?.star?.centroidHz ?? null;
  }

  assign(plan: VoicePlan, pitchHz: number, now: number): void {
    this.pruneTails(now);
    const reusing = this.tails.length > 0;
    for (const tail of this.tails) glideParam(tail.generators.envelope.gain, 0, now, REUSE_FADE_TIME_CONSTANT_SEC);
    this.id = plan.id;
    this.pitchHz = pitchHz;
    this.applyParams(plan, now, !reusing);

    const envelope = this.ctx.createGain();
    setParamNow(envelope.gain, 0, now);
    glideParam(envelope.gain, 1, now, ATTACK_TIME_CONSTANT_SEC);
    envelope.connect(this.sum);
    this.generators = { envelope, star: null, pulsar: null, uncertainty: null };
    this.syncLayers(plan, now);
  }

  update(plan: VoicePlan, pitchHz: number, now: number): void {
    this.pitchHz = pitchHz;
    this.applyParams(plan, now, false);
    this.syncLayers(plan, now);
  }

  release(now: number): void {
    if (this.generators) {
      const stopAt = now + RELEASE_STOP_AFTER_SEC;
      glideParam(this.generators.envelope.gain, 0, now, RELEASE_TIME_CONSTANT_SEC);
      stopAll(this.generators, stopAt);
      this.tails.push({ generators: this.generators, stopAt });
    }
    this.generators = null;
    this.id = null;
  }

  /** Released generators still fading out in this slot. */
  tailCount(now: number): number {
    this.pruneTails(now);
    return this.tails.length;
  }

  dispose(now: number): void {
    this.release(now);
    for (const tail of this.tails) stopAll(tail.generators, now);
    this.tails = [];
    this.panner.disconnect();
    this.send.disconnect();
  }

  private pruneTails(now: number): void {
    this.tails = this.tails.filter((tail) => now < tail.stopAt);
  }

  private applyParams(plan: VoicePlan, now: number, immediate: boolean): void {
    const set = immediate
      ? (param: AudioParam, value: number) => setParamNow(param, value, now)
      : (param: AudioParam, value: number) => glideParam(param, value, now, PARAM_TIME_CONSTANT_SEC);
    const nyquist = this.ctx.sampleRate / 2;
    set(this.dust.frequency, plan.dustLowpassHz === null ? nyquist : Math.min(plan.dustLowpassHz, nyquist));
    set(this.distance.frequency, Math.min(plan.distanceLowpassHz, nyquist));
    set(this.direct.gain, plan.directGain);
    set(this.send.gain, plan.reverbGain);
    if (immediate) {
      // The voice envelope fades in; the slot level can land at once.
      setParamNow(this.level.gain, dbToGain(plan.levelDb), now);
    } else {
      glideParam(this.level.gain, dbToGain(plan.levelDb), now, PARAM_TIME_CONSTANT_SEC);
    }
    setPannerPosition(this.panner, plan.directionListener, now, immediate);
  }

  private syncLayers(plan: VoicePlan, now: number): void {
    const g = this.generators;
    if (!g) return;

    // Star tone: Planck-shaped harmonic spectrum at the identity pitch.
    if (plan.star) {
      if (!g.star) {
        const osc = this.ctx.createOscillator();
        g.star = { osc, teffK: Number.NaN, pitchHz: Number.NaN, centroidHz: 0 };
        osc.connect(g.envelope);
        this.retuneStar(g.star, plan.star.teffK, now, true);
        osc.start(now);
      } else if (g.star.teffK !== plan.star.teffK || g.star.pitchHz !== this.pitchHz) {
        this.retuneStar(g.star, plan.star.teffK, now, false);
      }
    } else if (g.star) {
      stopNode(g.star.osc, now + 0.02);
      g.star = null;
    }

    if (!plan.pulsar && g.pulsar) {
      stopNode(g.pulsar.noise, now + 0.02);
      stopNode(g.pulsar.envelopeOsc, now + 0.02);
      g.pulsar = null;
    }

    // Pulsar: broadband noise gated at the catalogue spin frequency.
    if (plan.pulsar) {
      if (!g.pulsar) {
        const noise = this.ctx.createBufferSource();
        noise.buffer = this.shared.noise;
        noise.loop = true;
        const vca = this.ctx.createGain();
        setParamNow(vca.gain, this.shared.pulseDc, now);
        const envelopeOsc = this.ctx.createOscillator();
        envelopeOsc.setPeriodicWave(this.shared.pulseWave);
        setParamNow(envelopeOsc.frequency, plan.pulsar.pulseRateHz, now);
        envelopeOsc.connect(vca.gain);
        noise.connect(vca);
        vca.connect(g.envelope);
        noise.start(now, Math.random() * PULSAR_NOISE_SECONDS);
        envelopeOsc.start(now);
        g.pulsar = { noise, envelopeOsc, vca };
      } else {
        // Exact rate, no glide: a glide would play rates the pulsar never had.
        setParamNow(g.pulsar.envelopeOsc.frequency, plan.pulsar.pulseRateHz, now);
      }
    }

    // Uncertainty: a fifth that beats at a rate proportional to the residual.
    if (plan.uncertainty) {
      const { lowerHz, upperHz } = beatingFifthHz(2 * this.pitchHz, plan.uncertainty.beatHz);
      if (!g.uncertainty) {
        const lower = this.ctx.createOscillator();
        const upper = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        lower.setPeriodicWave(this.shared.uncertaintyWave);
        upper.setPeriodicWave(this.shared.uncertaintyWave);
        setParamNow(lower.frequency, lowerHz, now);
        setParamNow(upper.frequency, upperHz, now);
        setParamNow(gain.gain, 0, now);
        glideParam(gain.gain, UNCERTAINTY_RELATIVE_GAIN, now, ATTACK_TIME_CONSTANT_SEC);
        lower.connect(gain);
        upper.connect(gain);
        gain.connect(g.envelope);
        lower.start(now);
        upper.start(now);
        g.uncertainty = { lower, upper, gain };
      } else {
        glideParam(g.uncertainty.lower.frequency, lowerHz, now, PARAM_TIME_CONSTANT_SEC);
        glideParam(g.uncertainty.upper.frequency, upperHz, now, PARAM_TIME_CONSTANT_SEC);
      }
    } else if (g.uncertainty) {
      // Residual withdrawn: the layer goes silent rather than resolving to a clean fifth.
      const layer = g.uncertainty;
      glideParam(layer.gain.gain, 0, now, RELEASE_TIME_CONSTANT_SEC);
      stopNode(layer.lower, now + RELEASE_STOP_AFTER_SEC, () => layer.gain.disconnect());
      stopNode(layer.upper, now + RELEASE_STOP_AFTER_SEC);
      g.uncertainty = null;
    }
  }

  private retuneStar(star: StarGenerator, teffK: number, now: number, immediate: boolean): void {
    const pitch = this.pitchHz;
    const ceiling = Math.min(HARMONIC_CEILING_HZ, this.ctx.sampleRate * 0.45);
    const count = Math.max(1, Math.min(MAX_HARMONICS, Math.floor(ceiling / pitch)));
    const amplitudes = planckHarmonicAmplitudes(teffK, pitch, count);
    if (!amplitudes) return;
    const powers = amplitudes.map((a) => a * a);
    const { real, imag } = periodicWaveCoefficients(amplitudes, schroederPhases(powers));
    star.osc.setPeriodicWave(this.ctx.createPeriodicWave(real, imag, { disableNormalization: true }));
    if (immediate) setParamNow(star.osc.frequency, pitch, now);
    else glideParam(star.osc.frequency, pitch, now, PARAM_TIME_CONSTANT_SEC);
    star.teffK = teffK;
    star.pitchHz = pitch;
    star.centroidHz = harmonicCentroidHz(amplitudes, pitch);
  }
}

interface SharedResources {
  readonly noise: AudioBuffer;
  readonly pulseWave: PeriodicWave;
  readonly pulseDc: number;
  readonly uncertaintyWave: PeriodicWave;
}

/**
 * A fixed number of HRTF voices. `apply` takes plans that `selectVoices` already chose
 * (at most `capacity`) and realizes them: matching ids keep their slot, departed ids fade
 * out, new ids take a free slot.
 */
export class VoicePool {
  private readonly slots: VoiceSlot[];

  constructor(
    private readonly ctx: BaseAudioContext,
    readonly capacity: number,
    output: AudioNode,
    reverbInput: AudioNode
  ) {
    const noiseData = whiteNoise(Math.round(PULSAR_NOISE_SECONDS * ctx.sampleRate), 0x9e3779b9, PULSAR_NOISE_RMS);
    const noise = ctx.createBuffer(1, noiseData.length, ctx.sampleRate);
    noise.copyToChannel(noiseData, 0);

    const series = gaussianPulseTrainSeries(PULSE_FWHM_FRACTION);
    const pulse = periodicWaveCoefficients(series.cosine);
    const uncertainty = periodicWaveCoefficients(UNCERTAINTY_HARMONICS);
    const shared: SharedResources = {
      noise,
      pulseWave: ctx.createPeriodicWave(pulse.real, pulse.imag, { disableNormalization: true }),
      pulseDc: series.dc,
      uncertaintyWave: ctx.createPeriodicWave(uncertainty.real, uncertainty.imag, { disableNormalization: true }),
    };
    this.slots = Array.from({ length: Math.max(0, capacity) }, (_, index) => new VoiceSlot(ctx, index, output, reverbInput, shared));
  }

  apply(selected: readonly VoicePlan[], pitches: ReadonlyMap<string, number>): void {
    const now = this.ctx.currentTime;
    const wanted = new Set(selected.map((plan) => plan.id));
    for (const slot of this.slots) {
      if (slot.id !== null && !wanted.has(slot.id)) slot.release(now);
    }
    for (const plan of selected.slice(0, this.slots.length)) {
      const pitch = pitches.get(plan.id) ?? IDENTITY_PITCHES_HZ[0] ?? 110;
      const current = this.slots.find((slot) => slot.id === plan.id);
      if (current) {
        current.update(plan, pitch, now);
        continue;
      }
      const free =
        this.slots.find((slot) => slot.id === null && slot.tailCount(now) === 0) ??
        this.slots.find((slot) => slot.id === null);
      free?.assign(plan, pitch, now);
    }
  }

  states(): SlotState[] {
    const states: SlotState[] = [];
    for (const slot of this.slots) {
      if (slot.id !== null) {
        states.push({ slot: slot.index, id: slot.id, pitchHz: slot.pitchHz, centroidHz: slot.centroidHz });
      }
    }
    return states;
  }

  dispose(): void {
    const now = this.ctx.currentTime;
    for (const slot of this.slots) slot.dispose(now);
  }
}
