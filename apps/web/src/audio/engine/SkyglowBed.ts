import type { OmnitoneStatic } from 'omnitone';
import { binauralDiffuseNoise, diffuseFoaNoise } from '../dsp.js';
import { dbToGain } from '../mappings.js';
import { glideParam, setParamNow } from './createAudioContext.js';

/**
 * The skyglow bed: an isotropic broadband noise field around the listener whose level comes
 * from `skyglowBedLevelDb`. Two renderers share one interface:
 *
 * - `omnitone-foa`: four uncorrelated noise channels weighted as a diffuse field in
 *   first-order ambisonics (ACN/SN3D), decoded binaurally by Omnitone with the Google/SADIE
 *   HRIRs. This is the default.
 * - `stereo-diffuse`: plain Web Audio, two channels sharing their content below 700 Hz and
 *   independent above it. Used when Omnitone cannot load, and for A/B listening.
 *
 * The reasoning for the default is in docs/architecture/AUDIO_ENGINE.md.
 */
export type BedRenderer = 'omnitone-foa' | 'stereo-diffuse';
export type BedPreference = 'auto' | BedRenderer;

export interface SkyglowBed {
  readonly renderer: BedRenderer;
  /** Level in dBFS before the master bus; -Infinity is silence. */
  setLevelDb(levelDb: number): void;
  dispose(): void;
}

export type OmnitoneLoader = () => Promise<{ default: OmnitoneStatic }>;

export const loadOmnitoneModule: OmnitoneLoader = () => import('omnitone');

const BED_NOISE_SECONDS = 10;
const BED_HIGHPASS_HZ = 180;
const BED_SEED = 0x5ca1ab1e;
const LEVEL_TIME_CONSTANT_SEC = 0.35;

/**
 * Per-renderer trim so both renderers play the same `levelDb` at the same per-ear power.
 * Measured in Chrome 152 at a requested -20.2 dB: the Omnitone decode read 2.8 dB below the
 * stereo bed per ear. Equal power is not equal loudness (the HRIR decode colours the noise),
 * so this still needs a listening check; see AUDIO_ENGINE.md.
 */
export const BED_RENDERER_TRIM_DB: Readonly<Record<BedRenderer, number>> = {
  'omnitone-foa': 2.8,
  'stereo-diffuse': 0,
};

function makeLoopingSource(ctx: BaseAudioContext, channels: readonly Float32Array<ArrayBuffer>[]): AudioBufferSourceNode {
  const frames = channels[0]?.length ?? 0;
  const buffer = ctx.createBuffer(channels.length, frames, ctx.sampleRate);
  channels.forEach((data, channel) => buffer.copyToChannel(data, channel));
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.loop = true;
  return source;
}

function configureChannels(node: AudioNode, count: number): void {
  node.channelCount = count;
  node.channelCountMode = 'explicit';
  node.channelInterpretation = 'discrete';
}

class BufferBed implements SkyglowBed {
  constructor(
    readonly renderer: BedRenderer,
    private readonly ctx: BaseAudioContext,
    private readonly source: AudioBufferSourceNode,
    private readonly level: GainNode,
    private readonly output: AudioNode
  ) {}

  setLevelDb(levelDb: number): void {
    const target = levelDb === -Infinity ? 0 : dbToGain(levelDb + BED_RENDERER_TRIM_DB[this.renderer]);
    glideParam(this.level.gain, target, this.ctx.currentTime, LEVEL_TIME_CONSTANT_SEC);
  }

  /** Fades out before stopping, so switching renderers does not click. */
  dispose(): void {
    const now = this.ctx.currentTime;
    glideParam(this.level.gain, 0, now, 0.05);
    this.source.onended = () => this.output.disconnect();
    try {
      this.source.stop(now + 0.4);
    } catch {
      this.output.disconnect();
    }
  }
}

/**
 * Omnitone 2.0 patches `window.AudioContext` with `webkitAudioContext` when it detects Safari
 * and both prefixed constructors exist. If the prefixed constructor is a different class,
 * that patch would change the global for the rest of the app, so the bed declines Omnitone.
 */
function hasDistinctPrefixedAudioContext(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as unknown as {
    AudioContext?: unknown;
    webkitAudioContext?: unknown;
    webkitOfflineAudioContext?: unknown;
  };
  return (
    w.webkitAudioContext !== undefined &&
    w.webkitOfflineAudioContext !== undefined &&
    w.webkitAudioContext !== w.AudioContext
  );
}

async function createOmnitoneBed(
  ctx: BaseAudioContext,
  destination: AudioNode,
  loadOmnitone: OmnitoneLoader
): Promise<SkyglowBed> {
  if (hasDistinctPrefixedAudioContext()) {
    throw new Error('a distinct prefixed webkitAudioContext is present; Omnitone would replace window.AudioContext');
  }
  const { default: Omnitone } = await loadOmnitone();
  const renderer = Omnitone.createFOARenderer(ctx, { renderingMode: 'ambisonic' });
  await renderer.initialize();

  const frames = Math.round(BED_NOISE_SECONDS * ctx.sampleRate);
  const source = makeLoopingSource(ctx, diffuseFoaNoise(frames, BED_SEED));
  const level = ctx.createGain();
  const highpass = ctx.createBiquadFilter();
  configureChannels(level, 4);
  configureChannels(highpass, 4);
  setParamNow(level.gain, 0, ctx.currentTime);
  highpass.type = 'highpass';
  setParamNow(highpass.frequency, BED_HIGHPASS_HZ, ctx.currentTime);

  source.connect(level);
  level.connect(highpass);
  highpass.connect(renderer.input);
  renderer.output.connect(destination);
  source.start();
  return new BufferBed('omnitone-foa', ctx, source, level, renderer.output);
}

function createStereoBed(ctx: BaseAudioContext, destination: AudioNode): SkyglowBed {
  const frames = Math.round(BED_NOISE_SECONDS * ctx.sampleRate);
  const source = makeLoopingSource(ctx, binauralDiffuseNoise(frames, ctx.sampleRate, BED_SEED));
  const level = ctx.createGain();
  const highpass = ctx.createBiquadFilter();
  configureChannels(level, 2);
  configureChannels(highpass, 2);
  setParamNow(level.gain, 0, ctx.currentTime);
  highpass.type = 'highpass';
  setParamNow(highpass.frequency, BED_HIGHPASS_HZ, ctx.currentTime);

  source.connect(level);
  level.connect(highpass);
  highpass.connect(destination);
  source.start();
  return new BufferBed('stereo-diffuse', ctx, source, level, highpass);
}

export interface BedCreation {
  readonly bed: SkyglowBed;
  /** Why the preferred renderer was not used, or `null` when it was. */
  readonly fallbackReason: string | null;
}

export async function createSkyglowBed(
  ctx: BaseAudioContext,
  destination: AudioNode,
  preference: BedPreference = 'auto',
  loadOmnitone: OmnitoneLoader = loadOmnitoneModule
): Promise<BedCreation> {
  if (preference === 'stereo-diffuse') {
    return { bed: createStereoBed(ctx, destination), fallbackReason: null };
  }
  try {
    return { bed: await createOmnitoneBed(ctx, destination, loadOmnitone), fallbackReason: null };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return { bed: createStereoBed(ctx, destination), fallbackReason: `Omnitone unavailable: ${reason}` };
  }
}
