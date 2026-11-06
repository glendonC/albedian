/**
 * Renders the music directions (directions.ts) on an OfflineAudioContext at 48 kHz, with the real
 * landing's catalog stars as the data layer, so a clip is exactly what the arrangement plays, with
 * nothing played aloud. render.mjs drives this page in headless Chrome:
 * `window.__albedianMusic.render(id, { sourcesBase })` renders a direction and returns its facts;
 * `chunk(i)` then returns its samples, interleaved 32-bit floats in base64, a piece at a time.
 *
 * The landing is the real-moment landing of the WebGPU host (landingScene.ts: 2025-03-07 11:00 UTC,
 * the camera on the line from the galactic centre through the Earth), drawn with a 26 degree
 * vertical field at 16:9 and stars to magnitude 6.5, as that host draws them.
 */

import { DEFAULT_IMPULSE_RESPONSE_URL } from '../../src/audio/engine/ConvolutionReverb';
import { compressorMakeupGainDb } from '../../src/audio/engine/MasterBus';
import { energyNormalize } from '../../src/audio/dsp';
import { noteName } from '../../src/audio/music/harmony';
import { screenStars, type ScreenStar } from '../../src/audio/music/starNotes';
import { skySourcesInView, skyViewFromPose, type SkyTileData } from '../../src/audio/skySources';
import type { Quat } from '../../src/audio/voicePlanning';
import { ICRS_TO_SCENE_QUATERNION, STAR_CATALOG_BASE_URL } from '../../src/spatial/catalogStars/CatalogStarField';
import { CatalogStarTileLoader } from '../../src/spatial/catalogStars/CatalogStarTileLoader';
import type { CatalogStarAstroTile } from '../../src/spatial/catalogStars/tileCodec';
import { landingPose } from '../../src/spatial/webgpu/cislunar/landingScene';
import { barChords, beatGrid, chromaFrames, estimateKey, estimateTempo, onsetEnvelope } from './analysis';
import {
  INSTRUMENTS,
  RECORDING,
  ambientBeats,
  dataNotesInChords,
  organAndPulse,
  recordingArrangement,
  type Arrangement,
  type BusId,
  type DirectionId,
  type InstrumentDef,
  type RecordingAnalysis,
} from './directions';
import { SampleBank, chooseZone, playSample, type SampleZone } from './sampler';
import { duckUnderKicks, pingPongDelay, playKick, playPad, playRiser, playSub, syntheticHallImpulse } from './synth';

export const SAMPLE_RATE = 48000;
/** The WebGPU host's landing field (CislunarWebGpuRenderer.ts, LANDING_FOV_DEG) and frame. */
export const LANDING_FOV_DEG = 26;
export const LANDING_ASPECT = 16 / 9;
export const NAKED_EYE_LIMIT = 6.5;
/** The master's glue compressor: hard knee, so its automatic makeup gain has a closed form and is divided out. */
const GLUE = { thresholdDb: -18, ratio: 2, attackSec: 0.03, releaseSec: 0.25 } as const;

const dbToGain = (db: number) => 10 ** (db / 20);

// ---------------------------------------------------------------------------
// The landing's stars
// ---------------------------------------------------------------------------

export interface StarFacts {
  readonly id: string;
  readonly name: string | null;
  readonly designation: string | null;
  readonly teffK: number | null;
  readonly luminositySolar: number | null;
  readonly distancePc: number | null;
  readonly screenX: number;
  readonly screenY: number;
}

export interface LandingSky {
  readonly utc: string;
  readonly centreRaDeg: number;
  readonly centreDecDeg: number;
  readonly fovDeg: number;
  readonly aspect: number;
  readonly limitingMagnitude: number;
  readonly starsOnScreen: number;
  readonly completeOnScreen: number;
  readonly stars: ScreenStar[];
  /** The brightest stars on screen with a value missing: they play nothing. */
  readonly silent: StarFacts[];
}

let loaderPromise: Promise<CatalogStarTileLoader> | null = null;
const loader = () => (loaderPromise ??= CatalogStarTileLoader.open(STAR_CATALOG_BASE_URL, { maxResidentStars: 400_000 }));

async function nakedEyeTiles(): Promise<SkyTileData[]> {
  const l = await loader();
  l.setView({ centerIcrs: [1, 0, 0], radiusRad: Math.PI, limitingMagnitude: NAKED_EYE_LIMIT });
  for (let i = 0; i < 600; i++) {
    const stats = l.getStats();
    if (stats.tilesInFlight === 0 && stats.tilesLoaded >= stats.tilesWanted) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const cores = l.getLoadedTiles().filter((core) => core.layer === 0);
  const astros = await Promise.all(cores.map((core) => l.loadAstro(core.layer, core.pix)));
  return cores.map((core, i): SkyTileData => ({ core, astro: astros[i] as CatalogStarAstroTile }));
}

let landingPromise: Promise<LandingSky> | null = null;

export function landingSky(): Promise<LandingSky> {
  landingPromise ??= (async () => {
    const pose = landingPose();
    const camera: Quat = [pose.camera.quaternion.x, pose.camera.quaternion.y, pose.camera.quaternion.z, pose.camera.quaternion.w];
    const icrs: Quat = [ICRS_TO_SCENE_QUATERNION.x, ICRS_TO_SCENE_QUATERNION.y, ICRS_TO_SCENE_QUATERNION.z, ICRS_TO_SCENE_QUATERNION.w];
    const view = skyViewFromPose({ cameraWorldQuaternion: camera, icrsWorldQuaternion: icrs, fovDeg: LANDING_FOV_DEG, aspect: LANDING_ASPECT, limitingMagnitude: NAKED_EYE_LIMIT });
    if (!view) throw new Error('the landing pose gives no view');
    const l = await loader();
    const result = skySourcesInView(await nakedEyeTiles(), view, { releaseId: l.manifest.releaseId, maxAudible: 64, maxSilent: 16 });
    const all = screenStars(result.sources, view);
    const stars = all.filter((s) => s.teffK != null && s.luminositySolar != null && s.distancePc != null);
    const silentStars = all.filter((s) => !stars.includes(s));
    const [x, y, z] = view.centerIcrs;
    return {
      utc: pose.utc,
      centreRaDeg: ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360,
      centreDecDeg: (Math.asin(z) * 180) / Math.PI,
      fovDeg: LANDING_FOV_DEG,
      aspect: LANDING_ASPECT,
      limitingMagnitude: NAKED_EYE_LIMIT,
      starsOnScreen: result.starsInView,
      completeOnScreen: result.completeInView,
      stars,
      silent: await Promise.all(silentStars.map((s) => starFacts(s))),
    };
  })();
  return landingPromise;
}

let namesPromise: Promise<Map<string, string>> | null = null;

/** A star's IAU name (WGSN, from names.json) and catalog designation (from its detail tile). */
export async function starFacts(star: ScreenStar): Promise<StarFacts> {
  const l = await loader();
  namesPromise ??= l.loadNames().then((index) => new Map(index.names.map((n) => [`${l.manifest.releaseId}:L${n.layer}/${n.pix}#${n.index}`, n.nameAscii])));
  const match = /:L(\d+)\/(\d+)#(\d+)$/.exec(star.id);
  const record = match ? await l.getStarRecord(Number(match[1]), Number(match[2]), Number(match[3])).catch(() => null) : null;
  return {
    id: star.id,
    name: (await namesPromise).get(star.id) ?? null,
    designation: record?.designation ?? null,
    teffK: star.teffK,
    luminositySolar: star.luminositySolar,
    distancePc: star.distancePc,
    screenX: star.screenX,
    screenY: star.screenY,
  };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/** Every zone a note may use: the root and layer chooseZone picks, with all of that pair's round robins. */
function neededZones(arrangement: Arrangement): SampleZone[] {
  const out = new Map<string, SampleZone>();
  for (const e of arrangement.events) {
    if (e.type !== 'sample') continue;
    const zones = (INSTRUMENTS[e.instrument] as InstrumentDef).zones;
    const { zone } = chooseZone(zones, e.midi, e.layer, 0);
    for (const z of zones) if (z.rootMidi === zone.rootMidi && z.layer === zone.layer) out.set(z.file, z);
  }
  return [...out.values()];
}

async function chapelImpulse(ctx: BaseAudioContext): Promise<AudioBuffer> {
  const response = await fetch(DEFAULT_IMPULSE_RESPONSE_URL);
  if (!response.ok) throw new Error(`chapel response unavailable (${response.status})`);
  const decoded = await ctx.decodeAudioData(await response.arrayBuffer()).catch((error: unknown) => {
    throw new Error(`chapel response ${String(DEFAULT_IMPULSE_RESPONSE_URL)} could not be decoded: ${error instanceof Error ? error.message : String(error)}`);
  });
  const channels = energyNormalize([decoded.getChannelData(0), decoded.getChannelData(decoded.numberOfChannels > 1 ? 1 : 0)]);
  const buffer = ctx.createBuffer(2, decoded.length, decoded.sampleRate);
  channels.forEach((data, c) => buffer.copyToChannel(data, c));
  return buffer;
}

export interface RenderedClip {
  readonly arrangement: Arrangement;
  readonly channels: Float32Array[];
  readonly samplesUsed: { file: string; tuningCents: number | null; seconds: number }[];
}

export async function renderArrangement(arrangement: Arrangement, sourcesBase: string): Promise<RenderedClip> {
  const length = Math.ceil(arrangement.durationSec * SAMPLE_RATE);
  const ctx = new OfflineAudioContext(2, length, SAMPLE_RATE);

  const master = ctx.createGain();
  const glue = ctx.createDynamicsCompressor();
  glue.threshold.value = GLUE.thresholdDb;
  glue.knee.value = 0;
  glue.ratio.value = GLUE.ratio;
  glue.attack.value = GLUE.attackSec;
  glue.release.value = GLUE.releaseSec;
  const makeup = ctx.createGain();
  makeup.gain.value = dbToGain(-compressorMakeupGainDb(GLUE.thresholdDb, GLUE.ratio));
  master.connect(glue);
  glue.connect(makeup);
  makeup.connect(ctx.destination);

  const convolver = ctx.createConvolver();
  convolver.normalize = false;
  convolver.buffer = arrangement.room === 'chapel' ? await chapelImpulse(ctx) : syntheticHallImpulse(ctx);
  const roomReturn = ctx.createGain();
  roomReturn.gain.value = dbToGain(arrangement.roomReturnDb);
  convolver.connect(roomReturn);
  roomReturn.connect(master);

  const delay = arrangement.delay ? pingPongDelay(ctx, arrangement.delay) : null;
  delay?.output.connect(master);

  const kickTimes = arrangement.events.flatMap((e) => (e.type === 'kick' ? [e.timeSec] : []));
  const buses = new Map<BusId, AudioNode>();
  for (const bus of arrangement.buses) {
    const input = ctx.createGain();
    input.gain.value = dbToGain(bus.gainDb);
    const duck = ctx.createGain();
    input.connect(duck);
    duck.connect(master);
    if (bus.duckUnderKick) duckUnderKicks(duck.gain, kickTimes, bus.duckUnderKick.depthDb, bus.duckUnderKick.releaseSec);
    if (bus.reverbSendDb !== null) {
      const send = ctx.createGain();
      send.gain.value = dbToGain(bus.reverbSendDb);
      duck.connect(send);
      send.connect(convolver);
    }
    if (bus.delaySendDb !== null && delay) {
      const send = ctx.createGain();
      send.gain.value = dbToGain(bus.delaySendDb);
      duck.connect(send);
      send.connect(delay.input);
    }
    buses.set(bus.id, input);
  }
  const busOf = (id: BusId): AudioNode => {
    const node = buses.get(id);
    if (!node) throw new Error(`${arrangement.id} uses bus ${id} but does not define it`);
    return node;
  };

  const bank = new SampleBank(ctx, sourcesBase);
  const zones = neededZones(arrangement);
  console.log(`[music] ${arrangement.id}: loading ${zones.length} samples`);
  await bank.load(zones);
  console.log(`[music] ${arrangement.id}: samples loaded`);

  if (arrangement.recording) {
    const rec = arrangement.recording;
    const response = await fetch(sourcesBase + rec.file.split('/').map(encodeURIComponent).join('/'));
    if (!response.ok) throw new Error(`recording ${rec.file} unavailable (${response.status})`);
    const decoded = await ctx.decodeAudioData(await response.arrayBuffer());
    const source = ctx.createBufferSource();
    source.buffer = decoded;
    const fade = ctx.createGain();
    const g = dbToGain(rec.gainDb);
    fade.gain.setValueAtTime(0, 0);
    fade.gain.setValueAtTime(0, Math.min(0.3, rec.fadeInSec * 0.25));
    fade.gain.linearRampToValueAtTime(g, rec.fadeInSec);
    fade.gain.setValueAtTime(g, rec.durationSec - rec.fadeOutSec);
    fade.gain.linearRampToValueAtTime(0, rec.durationSec);
    source.connect(fade);
    fade.connect(busOf('recording'));
    source.start(0, rec.fromSec, rec.durationSec + 0.05);
  }

  for (const e of arrangement.events) {
    switch (e.type) {
      case 'sample': {
        const def = INSTRUMENTS[e.instrument] as InstrumentDef;
        playSample(ctx, bank, def, busOf(e.bus), { timeSec: e.timeSec, midi: e.midi, durationSec: e.durationSec, gainDb: e.gainDb, pan: e.pan, layer: e.layer });
        break;
      }
      case 'pad':
        playPad(ctx, busOf(e.bus), e);
        break;
      case 'sub':
        playSub(ctx, busOf(e.bus), e);
        break;
      case 'kick':
        playKick(ctx, busOf(e.bus), e.timeSec, e.gainDb);
        break;
      case 'riser':
        playRiser(ctx, busOf(e.bus), e.timeSec, e.durationSec, e.gainDb);
        break;
    }
  }

  console.log(`[music] ${arrangement.id}: ${arrangement.events.length} events scheduled, rendering ${arrangement.durationSec.toFixed(1)} s`);
  const rendered = await ctx.startRendering();
  console.log(`[music] ${arrangement.id}: rendered`);
  return { arrangement, channels: [rendered.getChannelData(0), rendered.getChannelData(1)], samplesUsed: bank.report() };
}

// ---------------------------------------------------------------------------
// Facts and the page bridge
// ---------------------------------------------------------------------------

export interface ClipFacts {
  readonly id: DirectionId;
  readonly title: string;
  readonly summary: string;
  readonly bpm: number;
  readonly key: string;
  readonly bars: number;
  readonly durationSec: number;
  readonly chords: Arrangement['chords'];
  readonly parts: Arrangement['parts'];
  readonly dataMapping: Arrangement['dataMapping'] & { readonly lowNote: string; readonly highNote: string };
  readonly inChord: { readonly notes: number; readonly outside: number };
  readonly landing: Omit<LandingSky, 'stars'>;
  readonly dataNotes: {
    readonly timeSec: number;
    readonly note: string;
    readonly midi: number;
    readonly chord: string;
    readonly starId: string;
    readonly gainDb: number;
    readonly pan: number;
  }[];
  readonly stars: (StarFacts & { readonly receivedLevelDb: number; readonly temperaturePosition: number; readonly notes: number })[];
  readonly percussionOnsetsSec: readonly number[];
  readonly samplesUsed: RenderedClip['samplesUsed'];
  readonly recording: Arrangement['recording'] | null;
  readonly analysis: RecordingAnalysis | null;
}

const PERCUSSION_INSTRUMENTS = new Set<string>(['claves', 'cymbalSwell', 'hats', 'clap', 'shaker']);

async function clipFacts(clip: RenderedClip, sky: LandingSky, analysis: RecordingAnalysis | null): Promise<ClipFacts> {
  const a = clip.arrangement;
  const percussionOnsetsSec = [
    ...new Set(
      a.events.flatMap((e) =>
        e.type === 'kick' || (e.type === 'sample' && PERCUSSION_INSTRUMENTS.has(e.instrument)) ? [Number(e.timeSec.toFixed(4))] : []
      )
    ),
  ].sort((p, q) => p - q);
  const dataEvents = a.events.flatMap((e) => (e.type === 'sample' && e.kind === 'data' && e.star ? [{ e, star: e.star }] : []));
  const byStar = new Map<string, { star: NonNullable<(typeof dataEvents)[number]['star']>; notes: number }>();
  for (const { star } of dataEvents) {
    const entry = byStar.get(star.starId);
    if (entry) entry.notes++;
    else byStar.set(star.starId, { star, notes: 1 });
  }
  const stars = await Promise.all(
    [...byStar.values()].map(async ({ star, notes }) => ({
      ...(await starFacts(sky.stars.find((s) => s.id === star.starId)!)),
      receivedLevelDb: star.receivedLevelDb,
      temperaturePosition: star.temperaturePosition,
      notes,
    }))
  );
  stars.sort((p, q) => q.receivedLevelDb - p.receivedLevelDb);
  const { stars: _unused, ...landing } = sky;
  return {
    id: a.id,
    title: a.title,
    summary: a.summary,
    bpm: a.bpm,
    key: a.key,
    bars: a.bars,
    durationSec: a.durationSec,
    chords: a.chords,
    parts: a.parts,
    dataMapping: { ...a.dataMapping, lowNote: noteName(a.dataMapping.lowMidi), highNote: noteName(a.dataMapping.highMidi) },
    inChord: dataNotesInChords(a),
    landing,
    dataNotes: dataEvents.map(({ e, star }) => ({
      timeSec: Number(e.timeSec.toFixed(4)),
      note: noteName(e.midi),
      midi: e.midi,
      chord: star.chord,
      starId: star.starId,
      gainDb: Number(e.gainDb.toFixed(2)),
      pan: Number(e.pan.toFixed(3)),
    })),
    stars,
    percussionOnsetsSec,
    samplesUsed: clip.samplesUsed,
    recording: a.recording ?? null,
    analysis,
  };
}

let lastClip: RenderedClip | null = null;

const ANALYSIS_RATE = 22050;

/**
 * Measures the recording around its excerpt with analysis.ts: the tempo from the whole track's onset
 * autocorrelation (between 50 and 100 beats a minute), the beat grid on the excerpt slice within 4
 * percent of that tempo, the downbeat nearest RECORDING.approxFromSec, each bar's chord with its
 * confidence, and the key of the excerpt.
 */
async function analyzeRecording(sourcesBase: string): Promise<RecordingAnalysis> {
  const response = await fetch(sourcesBase + RECORDING.file.split('/').map(encodeURIComponent).join('/'));
  if (!response.ok) throw new Error(`recording ${RECORDING.file} unavailable (${response.status})`);
  const decoded = await new OfflineAudioContext(1, 1, ANALYSIS_RATE).decodeAudioData(await response.arrayBuffer());
  const mono = new Float32Array(decoded.length);
  for (let c = 0; c < decoded.numberOfChannels; c++) {
    const data = decoded.getChannelData(c);
    for (let i = 0; i < mono.length; i++) mono[i] = mono[i]! + data[i]! / decoded.numberOfChannels;
  }
  const sr = decoded.sampleRate;
  const whole = estimateTempo(onsetEnvelope(mono, sr), 50, 100);
  const offset = Math.max(0, RECORDING.approxFromSec - 20);
  const slice = mono.subarray(Math.round(offset * sr), Math.min(mono.length, Math.round((RECORDING.approxFromSec + 75) * sr)));
  const grid = beatGrid(slice, sr, { beatsPerBar: 4, minBpm: 0.96 * whole.bpm, maxBpm: 1.04 * whole.bpm });
  const barSec = grid.beatSec * grid.beatsPerBar;
  const k = Math.round((RECORDING.approxFromSec - (offset + grid.firstDownbeatSec)) / barSec);
  const fromInSlice = grid.firstDownbeatSec + k * barSec;
  const estimates = barChords(slice, sr, grid, { startSec: fromInSlice - 0.05, endSec: fromInSlice + RECORDING.bars * barSec + 0.05 })
    .sort((a, b) => a.startSec - b.startSec)
    .slice(0, RECORDING.bars);
  const frames = chromaFrames(slice.subarray(Math.round(fromInSlice * sr), Math.round((fromInSlice + RECORDING.bars * barSec) * sr)), sr);
  const total = new Float64Array(12);
  for (const f of frames.frames) for (let p = 0; p < 12; p++) total[p] = total[p]! + f[p]!;
  const key = estimateKey(total);
  const confident = estimates.filter((e) => e.confident).length;
  return {
    file: RECORDING.file,
    bpm: grid.bpm,
    beatSec: grid.beatSec,
    tempoStrength: whole.strength,
    beatsPerBar: grid.beatsPerBar,
    key: { name: key.name, tonic: key.tonic, mode: key.mode, correlation: key.correlation },
    fromSec: offset + fromInSlice,
    bars: estimates.map((e, i) => ({ bar: i + 1, chord: e.chord, confident: e.confident, score: e.score, margin: e.margin })),
    statement:
      `Measured from the recording (analysis.ts): ${grid.bpm.toFixed(1)} beats a minute (whole-track onset autocorrelation ${whole.strength.toFixed(2)}), ` +
      `excerpt key ${key.name} (Krumhansl and Kessler profiles, correlation ${key.correlation.toFixed(2)}); ` +
      `${confident} of ${estimates.length} bars' chords measured with confidence; the other bars use the key's pentatonic notes.`,
  };
}

async function arrangementFor(id: DirectionId, sky: LandingSky, sourcesBase: string): Promise<{ arrangement: Arrangement; analysis: RecordingAnalysis | null }> {
  if (id === 'organ-pulse') return { arrangement: organAndPulse(sky.stars), analysis: null };
  if (id === 'ambient-beats') return { arrangement: ambientBeats(sky.stars), analysis: null };
  const analysis = await analyzeRecording(sourcesBase);
  return { arrangement: recordingArrangement(sky.stars, analysis), analysis };
}

// 131,072 frames (1 MB, 1.4 MB in base64) a piece: larger DevTools replies did not arrive (an 11 MB one stalled the render)
const CHUNK_SAMPLES = 1 << 17;

function base64OfFloats(data: Float32Array): string {
  const bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

declare global {
  interface Window {
    __albedianMusic?: {
      render(id: DirectionId, options: { sourcesBase: string }): Promise<{ facts: ClipFacts; frames: number; sampleRate: number; chunks: number }>;
      chunk(index: number): string;
      landing(): Promise<Omit<LandingSky, 'stars'> & { stars: number }>;
    };
  }
}

window.__albedianMusic = {
  async render(id, { sourcesBase }) {
    const sky = await landingSky();
    const { arrangement, analysis } = await arrangementFor(id, sky, sourcesBase);
    lastClip = await renderArrangement(arrangement, sourcesBase);
    const frames = lastClip.channels[0]!.length;
    const facts = await clipFacts(lastClip, sky, analysis);
    console.log(`[music] ${id}: facts ready (${facts.dataNotes.length} star notes, ${JSON.stringify(facts).length} bytes)`);
    return { facts, frames, sampleRate: SAMPLE_RATE, chunks: Math.ceil(frames / CHUNK_SAMPLES) };
  },
  chunk(index) {
    if (!lastClip) throw new Error('nothing rendered');
    const [left, right] = lastClip.channels as [Float32Array, Float32Array];
    const from = index * CHUNK_SAMPLES;
    const to = Math.min(left.length, from + CHUNK_SAMPLES);
    const out = new Float32Array(2 * (to - from));
    for (let i = from; i < to; i++) {
      out[2 * (i - from)] = left[i]!;
      out[2 * (i - from) + 1] = right[i]!;
    }
    return base64OfFloats(out);
  },
  async landing() {
    const sky = await landingSky();
    const { stars, ...rest } = sky;
    return { ...rest, stars: stars.length };
  },
};

document.getElementById('status')!.textContent = 'ready';
