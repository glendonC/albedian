import { energyNormalize } from '../dsp.js';
import { dbToGain, isFiniteNumber } from '../mappings.js';
import { glideParam, setParamNow } from './createAudioContext.js';

/**
 * Measured stereo impulse response (CC BY 4.0): St. Nicholas Chapel, Aachen Cathedral,
 * source S1 to receiver R12, from Zerwas and Kayku (2026), Zenodo record 23312556,
 * decoded from B-format with the direct sound removed. Provenance and the exact changes are
 * in ../assets/ATTRIBUTION.txt and docs/architecture/AUDIO_ENGINE.md.
 */
export const DEFAULT_IMPULSE_RESPONSE_URL = new URL(
  '../assets/aachen-nikolauskapelle-s1-r12-stereo.wav',
  import.meta.url
).href;

export type ReverbStatus = 'idle' | 'loading' | 'ready' | 'failed';

/**
 * Return trim that makes the delivered direct-to-reverberant ratio match the planned one.
 * Measured per ear with OfflineAudioContext in Chrome 152 on pink noise: the HRTF panner
 * passes a frontal source at -2.1 dB and this unit-energy response returns +4.6 dB, because
 * the chapel's energy sits below 2 kHz, where pink noise and the star voices have theirs.
 * With the trim, star voices land within about 2 dB of the planned DRR (see AUDIO_ENGINE.md).
 * Re-measure whenever the impulse response changes.
 */
export const REVERB_RETURN_CALIBRATION_DB = -6.7;

/**
 * Shared convolution reverb. Voices feed `input` through their distance-controlled sends.
 * The impulse response is energy-normalized, `normalize` is off and the return carries
 * `REVERB_RETURN_CALIBRATION_DB`, so the send gains from `directReverbGains` set the DRR.
 * Until the response loads the wet path is silent.
 */
export class ConvolutionReverb {
  readonly input: GainNode;
  private readonly convolver: ConvolverNode;
  private readonly returnGain: GainNode;
  private statusValue: ReverbStatus = 'idle';
  private errorValue: string | null = null;
  private durationSec: number | null = null;

  constructor(
    private readonly ctx: BaseAudioContext,
    output: AudioNode,
    trimDb = 0
  ) {
    this.input = ctx.createGain();
    this.convolver = ctx.createConvolver();
    this.convolver.normalize = false;
    this.returnGain = ctx.createGain();
    setParamNow(this.returnGain.gain, dbToGain(REVERB_RETURN_CALIBRATION_DB + trimDb), ctx.currentTime);
    this.input.connect(this.convolver);
    this.convolver.connect(this.returnGain);
    this.returnGain.connect(output);
  }

  get status(): ReverbStatus {
    return this.statusValue;
  }

  get error(): string | null {
    return this.errorValue;
  }

  get impulseDurationSec(): number | null {
    return this.durationSec;
  }

  async load(url: string, fetchImpl: typeof fetch = fetch): Promise<void> {
    this.statusValue = 'loading';
    this.errorValue = null;
    try {
      const response = await fetchImpl(url);
      if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
      const decoded = await this.ctx.decodeAudioData(await response.arrayBuffer());
      const channels: Float32Array[] = [];
      for (let c = 0; c < decoded.numberOfChannels; c++) channels.push(decoded.getChannelData(c));
      const normalized = energyNormalize(channels);
      const buffer = this.ctx.createBuffer(normalized.length, decoded.length, decoded.sampleRate);
      normalized.forEach((data, channel) => buffer.copyToChannel(data, channel));
      this.convolver.buffer = buffer;
      this.durationSec = decoded.duration;
      this.statusValue = 'ready';
    } catch (err) {
      this.statusValue = 'failed';
      this.errorValue = err instanceof Error ? err.message : String(err);
    }
  }

  /** Trim relative to the calibrated return, dB; -Infinity mutes the wet path. */
  setReturnLevelDb(trimDb: number): void {
    if (!isFiniteNumber(trimDb) && trimDb !== -Infinity) return;
    glideParam(this.returnGain.gain, dbToGain(REVERB_RETURN_CALIBRATION_DB + trimDb), this.ctx.currentTime, 0.05);
  }

  dispose(): void {
    this.returnGain.disconnect();
    this.input.disconnect();
  }
}
