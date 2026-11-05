/**
 * Renders the listening session's clips (items.ts) with the real SpatialAudioEngine on an
 * OfflineAudioContext, so a clip is exactly what the engine computes, sample for sample, with
 * nothing played aloud. Star values come from the release's tiles at render time.
 *
 * `window.__albedianListening.render(clipId)` returns the clip as a 16-bit WAV (base64) and its
 * facts: every source with its values and where each value comes from, and the engine's snapshot
 * at each step. `measureFirstListen()` measures item 0 per voice and summed (spectrum.ts).
 * render.mjs drives this page in headless Chrome; the buttons render one clip here.
 */

import { DEFAULT_IMPULSE_RESPONSE_URL } from '../../src/audio/engine/ConvolutionReverb';
import { SpatialAudioEngine, type EngineSnapshot } from '../../src/audio/engine/SpatialAudioEngine';
import { DEFAULT_HARMONIC_CEILING_HZ } from '../../src/audio/engine/VoicePool';
import { mulberry32 } from '../../src/audio/dsp';
import { DB_PER_MAGNITUDE, LOUDNESS, SUN_ABSOLUTE_BOLOMETRIC_MAG } from '../../src/audio/mappings';
import { skySourcesInView, skyViewFromPose, type SkyCameraPose, type SkyTileData } from '../../src/audio/skySources';
import { IDENTITY_QUAT, directionFromAzimuthElevation, yawQuat, type AudioSourceInput, type Quat } from '../../src/audio/voicePlanning';
import { ICRS_TO_SCENE_QUATERNION, STAR_CATALOG_BASE_URL } from '../../src/spatial/catalogStars/CatalogStarField';
import { CatalogStarTileLoader } from '../../src/spatial/catalogStars/CatalogStarTileLoader';
import { raDecDegToVec } from '../../src/spatial/catalogStars/healpix';
import type { CatalogStarAstroTile } from '../../src/spatial/catalogStars/tileCodec';
import {
  APP_VOLUME,
  LISTENING_ITEMS,
  SAMPLE_RATE,
  type Clip,
  type SkyStars,
  type StatedValue,
  type Step,
  type Voice,
} from './items';
import { AUDIBLE_BAND, onsetExcessDb, shareAbove, spectralFlatness, welchPowerSpectrum } from './spectrum';

const SCENE_ICRS: Quat = [ICRS_TO_SCENE_QUATERNION.x, ICRS_TO_SCENE_QUATERNION.y, ICRS_TO_SCENE_QUATERNION.z, ICRS_TO_SCENE_QUATERNION.w];
const LANDING_ASPECT = 16 / 9;
const NAKED_EYE_LIMIT = 6.5;

/** Where each input of a source comes from, for the page. */
interface SourceFacts {
  readonly id: string;
  readonly star?: string;
  readonly designation?: string;
  readonly pulsar?: string;
  readonly teffK: number | null;
  readonly luminositySolar: number | null;
  readonly distancePc: number | null;
  readonly catalogLuminositySolar?: number | null;
  readonly catalogDistancePc?: number | null;
  readonly devices: string[];
  readonly stated: StatedValue[];
}

interface StepFacts {
  readonly atSec: number;
  readonly sources: SourceFacts[] | 'unchanged';
  readonly skyBrightness: StatedValue | null | 'unchanged';
  readonly unveil: number | 'unchanged';
  readonly notes: string[];
  readonly snapshot: {
    readonly regime: string;
    readonly voices: {
      id: string;
      levelDb: number;
      teffK: number | null;
      perceptualDistance: number;
      drrDb: number;
      pitchHz: number | null;
      centroidHz: number | null;
      dustLowpassHz: number | null;
      beatHz: number | null;
      pulseRateHz: number | null;
    }[];
    readonly silent: { id: string; reason: string; missing: readonly string[] }[];
    readonly bed: EngineSnapshot['bed'];
  };
}

export interface ClipFacts {
  readonly item: string;
  readonly clip: string;
  readonly label: string;
  readonly durationSec: number;
  readonly sampleRate: number;
  readonly volume: number;
  readonly regime: string;
  readonly reverb: { readonly requested: boolean; readonly status: string; readonly error: string | null };
  readonly bed: { readonly requested: string; readonly renderer: string; readonly fallbackReason: string | null };
  readonly harmonicCeilingHz: number | null;
  readonly steps: StepFacts[];
  readonly output: { readonly peak: [number, number]; readonly rmsDb: [number, number] };
  readonly renderedWith: string;
}

// ---------------------------------------------------------------------------
// Catalog
// ---------------------------------------------------------------------------

interface CatalogStar {
  readonly name: string;
  readonly designation: string;
  readonly teffK: number | null;
  readonly luminositySolar: number | null;
  readonly distancePc: number | null;
}

let loaderPromise: Promise<CatalogStarTileLoader> | null = null;
const loader = () => (loaderPromise ??= CatalogStarTileLoader.open(STAR_CATALOG_BASE_URL));
const starCache = new Map<string, Promise<CatalogStar>>();
let skyTilesPromise: Promise<SkyTileData[]> | null = null;
let namedPromise: Promise<Map<string, string>> | null = null;

/** IAU names by source id (`gaia-dr3:L0/0#31`), for the stars of a sky view that have one. */
function namesById(): Promise<Map<string, string>> {
  namedPromise ??= (async () => {
    const l = await loader();
    const names = await l.loadNames();
    return new Map(names.names.map((n) => [`${l.manifest.releaseId}:L${n.layer}/${n.pix}#${n.index}`, n.nameAscii]));
  })();
  return namedPromise;
}

function catalogStar(name: string): Promise<CatalogStar> {
  let promise = starCache.get(name);
  if (!promise) {
    promise = (async () => {
      const l = await loader();
      const entry = (await l.loadNames()).names.find((n) => n.nameAscii === name);
      if (!entry) throw new Error(`no IAU name ${name} in the release`);
      const record = await l.getStarRecord(entry.layer, entry.pix, entry.index);
      const astro = await l.getStarAstrophysics(entry.layer, entry.pix, entry.index);
      return {
        name,
        designation: record.designation,
        teffK: astro.temperatureK?.median ?? null,
        luminositySolar: astro.luminositySolar?.median ?? null,
        distancePc: record.distance?.medianPc ?? null,
      };
    })();
    starCache.set(name, promise);
  }
  return promise;
}

/** Every naked-eye tile (layer 0) with its astro columns: the stars a landing-scale view draws. */
function skyTiles(): Promise<SkyTileData[]> {
  skyTilesPromise ??= (async () => {
    const l = await loader();
    l.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: NAKED_EYE_LIMIT });
    for (let i = 0; i < 600; i++) {
      const stats = l.getStats();
      if (stats.tilesInFlight === 0 && stats.tilesLoaded >= stats.tilesWanted) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    const cores = l.getLoadedTiles();
    const astros = await Promise.all(cores.map((core) => l.loadAstro(core.layer, core.pix)));
    return cores.map((core, i): SkyTileData => ({ core, astro: astros[i] as CatalogStarAstroTile }));
  })();
  return skyTilesPromise;
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------

function lookAtIcrs(raDeg: number, decDeg: number): Quat {
  // Camera -z toward the direction, +y toward celestial north; rows of the rotation are its axes
  const f = raDecDegToVec(raDeg, decDeg);
  const hint = Math.abs(f[2]) > 0.999 ? [1, 0, 0] : [0, 0, 1];
  const cross = (a: readonly number[], b: readonly number[]) => [
    a[1]! * b[2]! - a[2]! * b[1]!,
    a[2]! * b[0]! - a[0]! * b[2]!,
    a[0]! * b[1]! - a[1]! * b[0]!,
  ];
  const norm = (v: number[]) => {
    const n = Math.hypot(v[0]!, v[1]!, v[2]!);
    return v.map((x) => x / n);
  };
  const right = norm(cross(f, hint));
  const up = norm(cross(right, f));
  const back = [-f[0], -f[1], -f[2]];
  // Rotation matrix with columns right, up, back, to a quaternion (x, y, z, w)
  const m = [right, up, back];
  const m00 = m[0]![0]!, m01 = m[1]![0]!, m02 = m[2]![0]!;
  const m10 = m[0]![1]!, m11 = m[1]![1]!, m12 = m[2]![1]!;
  const m20 = m[0]![2]!, m21 = m[1]![2]!, m22 = m[2]![2]!;
  const trace = m00 + m11 + m22;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    return [(m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s];
  }
  if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    return [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s];
  }
  if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    return [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s];
  }
  const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
  return [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s];
}

function multiply(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz];
}

/** The pose a renderer would report for this view. */
function poseFor(sky: SkyStars): SkyCameraPose {
  const yaw = yawQuat(sky.yawDeg ?? 0);
  const camera = sky.view === 'landing' ? yaw : multiply(SCENE_ICRS, multiply(lookAtIcrs(sky.view.raDeg, sky.view.decDeg), yaw));
  return { cameraWorldQuaternion: camera, icrsWorldQuaternion: SCENE_ICRS, fovDeg: sky.fovDeg, aspect: LANDING_ASPECT, limitingMagnitude: NAKED_EYE_LIMIT };
}

/** Luminosity that makes a star at `distancePc` arrive at `levelDb` (the loudness mapping inverted). */
function luminosityForLevel(levelDb: number, distancePc: number): number {
  const magnitude = LOUDNESS.referenceMag - (levelDb - LOUDNESS.referenceLevelDb) / (DB_PER_MAGNITUDE * LOUDNESS.compression);
  return 10 ** ((SUN_ABSOLUTE_BOLOMETRIC_MAG + 5 * Math.log10(distancePc / 10) - magnitude) / 2.5);
}

async function voiceSource(voice: Voice): Promise<{ input: AudioSourceInput; facts: SourceFacts }> {
  const directionWorld = directionFromAzimuthElevation(voice.placement.azimuthDeg, voice.placement.elevationDeg);
  const devices: string[] = [];
  if (voice.placement.azimuthDeg !== 0 || voice.placement.elevationDeg !== 0) {
    devices.push(`placed at azimuth ${voice.placement.azimuthDeg.toFixed(1)} deg, elevation ${voice.placement.elevationDeg} deg`);
  } else {
    devices.push('placed straight ahead');
  }
  if (voice.kind === 'pulsar') {
    const input: AudioSourceInput = {
      id: voice.pulsar,
      kind: 'pulsar',
      directionWorld,
      spinFrequencyHz: voice.spinFrequencyHz.value,
      distancePc: voice.distancePc.value,
      luminositySolar: voice.luminositySolar.value,
    };
    return {
      input,
      facts: {
        id: voice.pulsar,
        pulsar: voice.pulsar,
        teffK: null,
        luminositySolar: voice.luminositySolar.value,
        distancePc: voice.distancePc.value,
        devices,
        stated: [voice.spinFrequencyHz, voice.distancePc, voice.luminositySolar],
      },
    };
  }
  const star = await catalogStar(voice.star);
  let distancePc = star.distancePc;
  let luminositySolar = star.luminositySolar;
  if (voice.movedToPc !== undefined) {
    distancePc = voice.movedToPc;
    devices.push(`moved from its catalog ${star.distancePc?.toFixed(2)} pc to ${voice.movedToPc} pc`);
  }
  if (voice.holdLevelDb !== undefined && distancePc !== null && luminositySolar !== null) {
    luminositySolar = luminosityForLevel(voice.holdLevelDb, distancePc);
    devices.push(`luminosity scaled from its catalog ${star.luminositySolar?.toPrecision(4)} Lsun so it plays at ${voice.holdLevelDb} dBFS`);
  }
  const stated = [voice.extinctionAvMag, voice.residualArcsec].filter((v): v is StatedValue => v !== undefined);
  const id = voice.idSuffix ? `${voice.star} (${voice.idSuffix})` : voice.star;
  const input: AudioSourceInput = {
    id,
    kind: 'star',
    directionWorld,
    distancePc,
    luminositySolar,
    teffK: star.teffK,
    ...(voice.extinctionAvMag ? { extinctionAvMag: voice.extinctionAvMag.value } : {}),
    ...(voice.residualArcsec ? { residualArcsec: voice.residualArcsec.value } : {}),
  };
  return {
    input,
    facts: {
      id,
      star: star.name,
      designation: star.designation,
      teffK: star.teffK,
      luminositySolar,
      distancePc,
      catalogLuminositySolar: star.luminositySolar,
      catalogDistancePc: star.distancePc,
      devices,
      stated,
    },
  };
}

interface Resolved {
  readonly inputs: AudioSourceInput[];
  readonly facts: SourceFacts[];
  readonly orientation: Quat;
}

/** `onlySourceId` keeps one source of a sky view, unchanged, for measuring a voice alone. */
async function resolveSounds(sounds: NonNullable<Step['sounds']>, incumbents: ReadonlySet<string>, onlySourceId?: string): Promise<Resolved> {
  if (!Array.isArray(sounds)) {
    const sky = sounds as SkyStars;
    const view = skyViewFromPose(poseFor(sky))!;
    const tiles = await skyTiles();
    const result = skySourcesInView(tiles, view, { releaseId: (await loader()).manifest.releaseId, evidenceClass: 'simulated', incumbents });
    let inputs = result.sources;
    if (sky.loudestOnly) inputs = inputs.filter((s) => s.teffK != null && s.luminositySolar != null && s.distancePc != null).slice(0, 1);
    if (onlySourceId !== undefined) {
      inputs = inputs.filter((s) => s.id === onlySourceId);
      if (inputs.length !== 1) throw new Error(`${onlySourceId} is not a source of this view`);
    }
    const named = await namesById();
    const facts = inputs.map(
      (s): SourceFacts => ({
        id: s.id,
        ...(named.has(s.id) ? { star: named.get(s.id)! } : {}),
        teffK: s.teffK ?? null,
        luminositySolar: s.luminositySolar ?? null,
        distancePc: s.distancePc ?? null,
        devices: [],
        stated: [],
      })
    );
    return { inputs, facts, orientation: view.orientationIcrs };
  }
  const resolved = await Promise.all((sounds as readonly Voice[]).map(voiceSource));
  return { inputs: resolved.map((r) => r.input), facts: resolved.map((r) => r.facts), orientation: IDENTITY_QUAT };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function snapshotFacts(engine: SpatialAudioEngine): StepFacts['snapshot'] {
  const s = engine.getSnapshot();
  return {
    regime: s.regime,
    voices: s.voices.map((v) => ({
      id: v.id,
      levelDb: v.levelDb,
      teffK: v.star?.teffK ?? null,
      perceptualDistance: v.perceptualDistance,
      drrDb: v.drrDb,
      pitchHz: v.pitchHz,
      centroidHz: v.centroidHz,
      dustLowpassHz: v.dustLowpassHz,
      beatHz: v.uncertainty?.beatHz ?? null,
      pulseRateHz: v.pulsar?.pulseRateHz ?? null,
    })),
    silent: s.silent.map((o) => ({ id: o.id, reason: o.reason, missing: o.missing })),
    bed: s.bed,
  };
}

async function until(condition: () => boolean, timeoutMs: number): Promise<boolean> {
  const start = performance.now();
  while (!condition()) {
    if (performance.now() - start > timeoutMs) return false;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return true;
}

/** The 16-bit samples a WAV master holds: the render with seeded triangular dither, rounded. */
function pcm16(buffer: AudioBuffer): Int16Array[] {
  const channels = buffer.numberOfChannels;
  const frames = buffer.length;
  const data = Array.from({ length: channels }, (_, c) => buffer.getChannelData(c));
  const out = Array.from({ length: channels }, () => new Int16Array(frames));
  const random = mulberry32(0x5eed);
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      const dither = (random() - random()) / 32768;
      const sample = Math.max(-1, Math.min(1, (data[c]![i] ?? 0) + dither));
      out[c]![i] = Math.round(sample * 32767);
    }
  }
  return out;
}

/**
 * 16-bit PCM WAV of pcm16's samples. The dither repeats, but Chrome's render does not quite: two
 * renders of clip A differed by one 16-bit step in 166 of 768,000 samples (2026-11-04), so two WAVs of a
 * clip are not byte for byte the same.
 */
function encodeWav(buffer: AudioBuffer): Uint8Array {
  const channels = buffer.numberOfChannels;
  const frames = buffer.length;
  const bytes = new Uint8Array(44 + frames * channels * 2);
  const view = new DataView(bytes.buffer);
  const write = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + frames * channels * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, frames * channels * 2, true);
  const samples = pcm16(buffer);
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      view.setInt16(offset, samples[c]![i]!, true);
      offset += 2;
    }
  }
  return bytes;
}

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function outputStats(buffer: AudioBuffer): ClipFacts['output'] {
  const peak: [number, number] = [0, 0];
  const rmsDb: [number, number] = [Number.NEGATIVE_INFINITY, Number.NEGATIVE_INFINITY];
  for (let c = 0; c < Math.min(2, buffer.numberOfChannels); c++) {
    const data = buffer.getChannelData(c);
    let p = 0;
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = data[i] ?? 0;
      p = Math.max(p, Math.abs(v));
      sum += v * v;
    }
    peak[c] = p;
    rmsDb[c] = sum > 0 ? 10 * Math.log10(sum / data.length) : Number.NEGATIVE_INFINITY;
  }
  return { peak, rmsDb };
}

function findClip(clipId: string): { item: string; clip: Clip } {
  for (const item of LISTENING_ITEMS) {
    const clip = item.clips.find((c) => c.id === clipId);
    if (clip) return { item: item.id, clip };
  }
  throw new Error(`no clip ${clipId}`);
}

interface RenderOptions {
  /** Keep only this source of the clip's sky views: one voice of a chord, measured alone. */
  readonly onlySourceId?: string;
}

/** Renders a clip with the real engine on an OfflineAudioContext, each step applied on time. */
async function renderOffline(item: string, clip: Clip, options: RenderOptions = {}): Promise<{ rendered: AudioBuffer; facts: ClipFacts }> {
  const ctx = new OfflineAudioContext({ numberOfChannels: 2, length: Math.ceil(clip.durationSec * SAMPLE_RATE), sampleRate: SAMPLE_RATE });
  const engine = new SpatialAudioEngine({
    contextFactory: () => ctx as unknown as AudioContext,
    impulseResponseUrl: clip.reverb ? DEFAULT_IMPULSE_RESPONSE_URL : null,
    bedPreference: clip.bed,
    ...(clip.harmonicCeilingHz !== undefined ? { harmonicCeilingHz: clip.harmonicCeilingHz } : {}),
  });
  engine.setRegime(clip.regime);
  engine.setMasterVolume(APP_VOLUME);

  const steps: StepFacts[] = [];
  let incumbents: ReadonlySet<string> = new Set();
  const apply = async (step: Step): Promise<void> => {
    const notes: string[] = [];
    let sourceFacts: StepFacts['sources'] = 'unchanged';
    if (step.sounds !== undefined) {
      const resolved = await resolveSounds(step.sounds, incumbents, options.onlySourceId);
      engine.setListenerOrientation(resolved.orientation);
      engine.setSources(resolved.inputs);
      incumbents = new Set(resolved.inputs.map((s) => s.id));
      sourceFacts = resolved.facts;
    }
    if (step.skyBrightness !== undefined) engine.setSkyBrightness(step.skyBrightness?.value ?? null);
    if (step.unveil !== undefined) {
      engine.setUnveil(step.unveil);
      notes.push(`reveal control at ${step.unveil} (a listening device: the app has no Unveil gesture yet)`);
    }
    if (step.reverbOff) {
      engine.setReverbReturnDb(-120);
      notes.push('reverb return turned down to silence');
    }
    if (step.detent) {
      engine.playDetentClick();
      notes.push('interface detent click');
    }
    steps.push({
      atSec: step.atSec,
      sources: sourceFacts,
      skyBrightness: step.skyBrightness === undefined ? 'unchanged' : step.skyBrightness,
      unveil: step.unveil ?? 'unchanged',
      notes,
      snapshot: snapshotFacts(engine),
    });
  };

  // The first step's data is set before the engine starts, as the app's data arrives before a gesture
  const [first, ...rest] = clip.steps;
  if (first) await apply({ ...first, detent: false });
  engine.start();
  if (first?.detent) engine.playDetentClick();
  await until(() => {
    const s = engine.getSnapshot();
    return (!clip.reverb || s.reverb.status === 'ready' || s.reverb.status === 'failed') && s.bed.renderer !== 'loading';
  }, 30000);
  // The first step's snapshot again, now that the graph exists (voice slots, centroids, the bed)
  if (steps[0]) steps[0] = { ...steps[0], snapshot: snapshotFacts(engine) };

  // Every later step is applied when rendering reaches its time. Sky views and stars are resolved
  // first, so the offline clock never waits on a fetch.
  for (const step of rest) if (step.sounds !== undefined) await resolveSounds(step.sounds, incumbents, options.onlySourceId);
  for (const step of rest) {
    void ctx.suspend(step.atSec).then(async () => {
      await apply(step);
      await ctx.resume();
    });
  }
  const rendered = await ctx.startRendering();
  const snapshot = engine.getSnapshot();
  const facts: ClipFacts = {
    item,
    clip: clip.id,
    label: clip.label,
    durationSec: clip.durationSec,
    sampleRate: SAMPLE_RATE,
    volume: APP_VOLUME,
    regime: clip.regime,
    reverb: { requested: clip.reverb, status: snapshot.reverb.status, error: snapshot.reverb.error },
    bed: { requested: clip.bed, renderer: snapshot.bed.renderer, fallbackReason: snapshot.bed.fallbackReason },
    harmonicCeilingHz: clip.harmonicCeilingHz ?? null,
    steps,
    output: outputStats(rendered),
    renderedWith: navigator.userAgent,
  };
  // No dispose: an OfflineAudioContext has no close(); the finished context is simply dropped
  return { rendered, facts };
}

export async function renderClip(clipId: string): Promise<{ wavBase64: string; facts: ClipFacts }> {
  const { item, clip } = findClip(clipId);
  const { rendered, facts } = await renderOffline(item, clip);
  return { wavBase64: toBase64(encodeWav(rendered)), facts };
}

// ---------------------------------------------------------------------------
// Item 0, measured (AUDIO_ENGINE.md section 10)
// ---------------------------------------------------------------------------

/** How item 0's landing is measured; every number of section 10's table depends on these. */
export const FIRST_LISTEN_METHOD = {
  clip: 'first-listen-a',
  samples: '16-bit, as the WAV masters hold them (the render with its seeded triangular dither)',
  spectrum: 'Welch: periodic Hann segments of 4096 samples (85 ms, 11.7 Hz bins), half overlap, left and right power summed',
  // The chapel response is 4.2 s long, so from 4.5 s on its tail no longer builds
  steadyFromSec: 4.5,
  band: AUDIBLE_BAND,
  splitHz: 4000,
  onset: { fromHz: 12000, onsetSec: 0.3 },
  ceilingsHz: [DEFAULT_HARMONIC_CEILING_HZ, 4000],
} as const;

interface Measured {
  /** Over the steady window and the audible band. */
  readonly flatness: number | null;
  readonly shareAbove4kHz: number | null;
  /** Power above 12 kHz in the first 0.3 s against the steady window's, in dB. */
  readonly onsetAbove12kHzDb: number | null;
  /** Of the whole render, left and right. */
  readonly peak: [number, number];
  /** Over the steady window, left and right, in dBFS. */
  readonly steadyRmsDb: [number, number];
}

export interface FirstListenVoice {
  readonly id: string;
  /** IAU name, where the release has one. */
  readonly star: string | null;
  /** Catalog designation (Gaia DR3 source_id), from the release's detail tile. */
  readonly designation: string | null;
  readonly teffK: number | null;
  readonly luminositySolar: number | null;
  readonly distancePc: number | null;
  readonly levelDb: number;
  readonly pitchHz: number | null;
  /** The harmonic centroid the engine reports, at each harmonic ceiling (keys in Hz). */
  readonly centroidHz: Record<string, number | null>;
}

export interface FirstListenRender extends Measured {
  /** 'chord' for the landing as the app plays it, else the id of the one voice playing. */
  readonly subject: string;
  readonly reverb: boolean;
  readonly harmonicCeilingHz: number;
  readonly voices: number;
}

export interface FirstListenMeasurements {
  readonly method: typeof FIRST_LISTEN_METHOD;
  readonly voices: FirstListenVoice[];
  readonly renders: FirstListenRender[];
  readonly renderedWith: string;
}

/** The catalog designation of a sky source (`gaia-dr3:L0/4#51`), from its detail tile. */
async function designationOf(sourceId: string): Promise<string | null> {
  const match = /:L(\d+)\/(\d+)#(\d+)$/.exec(sourceId);
  if (!match) return null;
  const record = await (await loader()).getStarRecord(Number(match[1]), Number(match[2]), Number(match[3]));
  return record.designation;
}

function measure(rendered: AudioBuffer): Measured {
  const m = FIRST_LISTEN_METHOD;
  const rate = rendered.sampleRate;
  const channels = pcm16(rendered).map((c) => Float64Array.from(c, (v) => v / 32768));
  const from = Math.round(m.steadyFromSec * rate);
  const steady = welchPowerSpectrum(channels, rate, { startSample: from });
  const rms = channels.slice(0, 2).map((c) => {
    let sum = 0;
    for (let i = from; i < c.length; i++) sum += c[i]! * c[i]!;
    return sum > 0 ? 10 * Math.log10(sum / (c.length - from)) : Number.NEGATIVE_INFINITY;
  });
  return {
    flatness: spectralFlatness(steady, m.band),
    shareAbove4kHz: shareAbove(steady, m.splitHz, m.band),
    onsetAbove12kHzDb: onsetExcessDb(channels, rate, { ...m.onset, steadyFromSec: m.steadyFromSec }),
    peak: outputStats(rendered).peak,
    steadyRmsDb: [rms[0]!, rms[1] ?? rms[0]!],
  };
}

/**
 * Renders item 0's landing (clip A) as the app plays it and each of its voiced stars alone, at the
 * same pose, direction and level, with and without the chapel reverb, at the default and the 4 kHz
 * harmonic ceilings, and measures every render: 36 renders, about 15 s in headless Chrome.
 */
export async function measureFirstListen(): Promise<FirstListenMeasurements> {
  const { item, clip } = findClip(FIRST_LISTEN_METHOD.clip);
  const voices = new Map<string, FirstListenVoice>();
  const renders: FirstListenRender[] = [];
  let voiced: string[] | null = null;
  for (const harmonicCeilingHz of FIRST_LISTEN_METHOD.ceilingsHz) {
    for (const reverb of [true, false]) {
      const variant: Clip = { ...clip, reverb, harmonicCeilingHz };
      const chord = await renderOffline(item, variant);
      const step = chord.facts.steps[0]!;
      const ids = step.snapshot.voices.map((v) => v.id);
      if (voiced === null) voiced = ids;
      if (ids.join() !== voiced.join()) throw new Error(`the chord voiced ${ids.join(', ')} here and ${voiced.join(', ')} before`);
      const sources = step.sources === 'unchanged' ? [] : step.sources;
      for (const v of step.snapshot.voices) {
        const source = sources.find((s) => s.id === v.id);
        const known = voices.get(v.id);
        voices.set(v.id, {
          id: v.id,
          star: source?.star ?? null,
          designation: known?.designation ?? (await designationOf(v.id)),
          teffK: v.teffK,
          luminositySolar: source?.luminositySolar ?? null,
          distancePc: source?.distancePc ?? null,
          levelDb: v.levelDb,
          pitchHz: v.pitchHz,
          centroidHz: { ...known?.centroidHz, [harmonicCeilingHz]: v.centroidHz },
        });
      }
      const record = (subject: string, count: number, rendered: AudioBuffer) => {
        const result: FirstListenRender = { subject, reverb, harmonicCeilingHz, voices: count, ...measure(rendered) };
        renders.push(result);
        console.info('[measure]', JSON.stringify(result));
      };
      record('chord', ids.length, chord.rendered);
      for (const v of step.snapshot.voices) {
        const alone = await renderOffline(item, variant, { onlySourceId: v.id });
        const sounding = alone.facts.steps[0]!.snapshot.voices;
        if (sounding.length !== 1 || sounding[0]!.id !== v.id) throw new Error(`${v.id} alone sounded ${sounding.map((s) => s.id).join(', ')}`);
        if (Math.abs(sounding[0]!.levelDb - v.levelDb) > 1e-9) throw new Error(`${v.id} alone plays at ${sounding[0]!.levelDb} dBFS, in the chord at ${v.levelDb}`);
        record(v.id, 1, alone.rendered);
      }
    }
  }
  return { method: FIRST_LISTEN_METHOD, voices: [...voices.values()], renders, renderedWith: navigator.userAgent };
}

declare global {
  interface Window {
    __albedianListening?: {
      readonly items: typeof LISTENING_ITEMS;
      readonly clipIds: readonly string[];
      render(clipId: string): Promise<{ wavBase64: string; facts: ClipFacts }>;
      measureFirstListen(): Promise<FirstListenMeasurements>;
    };
  }
}

window.__albedianListening = {
  items: LISTENING_ITEMS,
  clipIds: LISTENING_ITEMS.flatMap((item) => item.clips.map((clip) => clip.id)),
  render: renderClip,
  measureFirstListen,
};

// Manual use: one button per clip renders it and plays the result
const list = document.getElementById('clips');
if (list) {
  for (const item of LISTENING_ITEMS) {
    for (const clip of item.clips) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${item.section10}. ${clip.label}`;
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const { wavBase64, facts } = await renderClip(clip.id);
          const audio = new Audio(`data:audio/wav;base64,${wavBase64}`);
          void audio.play();
          console.info('[listening]', facts);
        } finally {
          button.disabled = false;
        }
      });
      const row = document.createElement('li');
      row.append(button);
      list.append(row);
    }
  }
}

