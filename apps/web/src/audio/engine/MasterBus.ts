import { softClipCurve } from '../dsp.js';
import { clamp, dbToGain, gainToDb, isFiniteNumber } from '../mappings.js';
import { glideParam, setParamNow } from './createAudioContext.js';

/**
 * Master bus: safety compressor -> peak limiter -> soft clipper -> user volume -> enable fade.
 *
 * The bus is meant to be transparent in normal operation, so that level differences the
 * mappings compute (4 dB per magnitude) reach the ears intact. Both dynamics stages use a
 * hard knee and a high threshold, and the automatic makeup gain that Web Audio's
 * DynamicsCompressorNode applies is divided back out after each stage. The clipper's curve
 * tops out below full scale, so nothing past it can exceed `clip.ceiling`.
 */
export const MASTER_BUS_SETTINGS = {
  compressor: { thresholdDb: -10, ratio: 4, attackSec: 0.005, releaseSec: 0.2 },
  limiter: { thresholdDb: -2, ratio: 20, attackSec: 0.001, releaseSec: 0.1 },
  clip: { ceiling: 0.966, kneeStart: 0.75, curveSamples: 4097 },
  defaultVolume: 0.85,
  fadeTimeConstantSec: 0.18,
} as const;

/**
 * Makeup gain a hard-knee DynamicsCompressorNode applies, per the Web Audio specification:
 * (1 / curve(0 dBFS))^0.6, where a hard-knee curve maps 0 dBFS to threshold * (1 - 1/ratio).
 * Checked against Chrome 152 with OfflineAudioContext (docs/architecture/AUDIO_ENGINE.md).
 */
export function compressorMakeupGainDb(thresholdDb: number, ratio: number): number {
  if (!isFiniteNumber(thresholdDb) || !isFiniteNumber(ratio) || ratio < 1 || thresholdDb >= 0) return 0;
  return -0.6 * thresholdDb * (1 - 1 / ratio);
}

function makeCompressor(
  ctx: BaseAudioContext,
  settings: { thresholdDb: number; ratio: number; attackSec: number; releaseSec: number },
  now: number
): { compressor: DynamicsCompressorNode; compensation: GainNode } {
  const compressor = ctx.createDynamicsCompressor();
  setParamNow(compressor.threshold, settings.thresholdDb, now);
  setParamNow(compressor.knee, 0, now);
  setParamNow(compressor.ratio, settings.ratio, now);
  setParamNow(compressor.attack, settings.attackSec, now);
  setParamNow(compressor.release, settings.releaseSec, now);
  const compensation = ctx.createGain();
  setParamNow(compensation.gain, dbToGain(-compressorMakeupGainDb(settings.thresholdDb, settings.ratio)), now);
  compressor.connect(compensation);
  return { compressor, compensation };
}

export class MasterBus {
  readonly input: GainNode;
  private readonly limiter: DynamicsCompressorNode;
  private readonly clipper: WaveShaperNode;
  private readonly volume: GainNode;
  private readonly fade: GainNode;
  private readonly analysers: readonly AnalyserNode[];
  private readonly meterBuffer: Float32Array<ArrayBuffer>;

  constructor(
    private readonly ctx: BaseAudioContext,
    destination: AudioNode = ctx.destination
  ) {
    const now = ctx.currentTime;
    this.input = ctx.createGain();
    const compressor = makeCompressor(ctx, MASTER_BUS_SETTINGS.compressor, now);
    const limiter = makeCompressor(ctx, MASTER_BUS_SETTINGS.limiter, now);
    this.limiter = limiter.compressor;
    this.clipper = ctx.createWaveShaper();
    this.volume = ctx.createGain();
    this.fade = ctx.createGain();

    const { ceiling, kneeStart, curveSamples } = MASTER_BUS_SETTINGS.clip;
    this.clipper.curve = softClipCurve(curveSamples, ceiling, kneeStart);
    this.clipper.oversample = '4x';
    setParamNow(this.volume.gain, MASTER_BUS_SETTINGS.defaultVolume, now);
    setParamNow(this.fade.gain, 0, now);

    this.input.connect(compressor.compressor);
    compressor.compensation.connect(limiter.compressor);
    limiter.compensation.connect(this.clipper);
    this.clipper.connect(this.volume);
    this.volume.connect(this.fade);
    this.fade.connect(destination);

    // Per-ear meter: an AnalyserNode alone would downmix to mono, which under-reads
    // decorrelated or lateralized signals.
    const splitter = ctx.createChannelSplitter(2);
    this.fade.connect(splitter);
    this.analysers = [0, 1].map((channel) => {
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      splitter.connect(analyser, channel);
      return analyser;
    });
    this.meterBuffer = new Float32Array(2048);
  }

  setVolume(linear: number): void {
    if (!isFiniteNumber(linear)) return;
    glideParam(this.volume.gain, clamp(linear, 0, 1), this.ctx.currentTime, 0.05);
  }

  fadeIn(): void {
    glideParam(this.fade.gain, 1, this.ctx.currentTime, MASTER_BUS_SETTINGS.fadeTimeConstantSec);
  }

  fadeOut(): void {
    glideParam(this.fade.gain, 0, this.ctx.currentTime, MASTER_BUS_SETTINGS.fadeTimeConstantSec);
  }

  /** Mean per-ear power of the last analyser frames at the bus output, dBFS. */
  getOutputLevelDb(): number {
    let power = 0;
    for (const analyser of this.analysers) {
      analyser.getFloatTimeDomainData(this.meterBuffer);
      let sum = 0;
      for (let i = 0; i < this.meterBuffer.length; i++) sum += (this.meterBuffer[i] ?? 0) ** 2;
      power += sum / this.meterBuffer.length;
    }
    return gainToDb(Math.sqrt(power / Math.max(1, this.analysers.length)));
  }

  /** Gain reduction the limiter is applying right now, dB (0 or negative). */
  getLimiterReductionDb(): number {
    return this.limiter.reduction;
  }

  dispose(): void {
    this.fade.disconnect();
    this.input.disconnect();
  }
}
