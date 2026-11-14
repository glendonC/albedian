import { dbToGain } from '../../mappings.js';
import type { VoicePlan } from '../../voicePlanning.js';
import { glideParam, setParamNow } from '../createAudioContext.js';
import { applyParam, stopVoiceGenerators } from './paramTransitions.js';
import { createVoicePanner, setPannerPosition } from './spatialPanner.js';
import { syncVoiceLayers } from './voiceLayers.js';
import {
  ATTACK_TIME_CONSTANT_SEC,
  DISTANCE_Q_DB,
  DUST_Q_DB,
  RELEASE_STOP_AFTER_SEC,
  RELEASE_TIME_CONSTANT_SEC,
  REUSE_FADE_TIME_CONSTANT_SEC,
  type ReleasingTail,
  type SharedVoiceResources,
  type VoiceGenerators,
} from './voiceTypes.js';

/**
 * One pre-allocated voice channel: dust and distance low-pass filters, level, dry HRTF panner,
 * reverb send, and click-free attack/release/reuse envelope management.
 */
export class VoiceSlot {
  id: string | null = null;
  pitchHz = 0;
  private generators: VoiceGenerators | null = null;
  private tails: ReleasingTail[] = [];
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
    private readonly shared: SharedVoiceResources
  ) {
    this.sum = ctx.createGain();
    this.dust = ctx.createBiquadFilter();
    this.distance = ctx.createBiquadFilter();
    this.level = ctx.createGain();
    this.direct = ctx.createGain();
    this.send = ctx.createGain();
    this.panner = createVoicePanner(ctx);

    const now = ctx.currentTime;
    this.dust.type = 'lowpass';
    setParamNow(this.dust.Q, DUST_Q_DB, now);
    this.distance.type = 'lowpass';
    setParamNow(this.distance.Q, DISTANCE_Q_DB, now);
    setParamNow(this.level.gain, 0, now);

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
    for (const tail of this.tails) {
      glideParam(tail.generators.envelope.gain, 0, now, REUSE_FADE_TIME_CONSTANT_SEC);
    }
    this.id = plan.id;
    this.pitchHz = pitchHz;
    this.applyParams(plan, now, !reusing);

    const envelope = this.ctx.createGain();
    setParamNow(envelope.gain, 0, now);
    glideParam(envelope.gain, 1, now, ATTACK_TIME_CONSTANT_SEC);
    envelope.connect(this.sum);
    this.generators = { envelope, star: null, pulsar: null, uncertainty: null };
    syncVoiceLayers(this.ctx, this.generators, plan, this.pitchHz, this.shared, now);
  }

  update(plan: VoicePlan, pitchHz: number, now: number): void {
    this.pitchHz = pitchHz;
    this.applyParams(plan, now, false);
    if (this.generators) {
      syncVoiceLayers(this.ctx, this.generators, plan, this.pitchHz, this.shared, now);
    }
  }

  release(now: number): void {
    if (this.generators) {
      const stopAt = now + RELEASE_STOP_AFTER_SEC;
      glideParam(this.generators.envelope.gain, 0, now, RELEASE_TIME_CONSTANT_SEC);
      stopVoiceGenerators(this.generators, stopAt);
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
    for (const tail of this.tails) stopVoiceGenerators(tail.generators, now);
    this.tails = [];
    this.panner.disconnect();
    this.send.disconnect();
  }

  private pruneTails(now: number): void {
    this.tails = this.tails.filter((tail) => now < tail.stopAt);
  }

  private applyParams(plan: VoicePlan, now: number, immediate: boolean): void {
    const nyquist = this.ctx.sampleRate / 2;
    const dustHz = plan.dustLowpassHz === null ? nyquist : Math.min(plan.dustLowpassHz, nyquist);
    applyParam(this.dust.frequency, dustHz, now, immediate);
    applyParam(this.distance.frequency, Math.min(plan.distanceLowpassHz, nyquist), now, immediate);
    applyParam(this.direct.gain, plan.directGain, now, immediate);
    applyParam(this.send.gain, plan.reverbGain, now, immediate);
    // On a clean slot the voice envelope fades in from 0, so slot level can land at once.
    applyParam(this.level.gain, dbToGain(plan.levelDb), now, immediate);
    setPannerPosition(this.panner, plan.directionListener, now, immediate);
  }
}
