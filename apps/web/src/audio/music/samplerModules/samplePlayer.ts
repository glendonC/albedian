/**
 * Web Audio note scheduling for recorded sample playback with click-free linear attack/release
 * ramps, stereo panning, and sample-duration bounding (AUDIO_ENGINE.md section 13).
 */

import type { SampleBank } from './sampleBank.js';
import type { Instrument, SampleNote } from './types.js';
import { chooseZone, playbackRate } from './zoneSelection.js';

const DB_PER_AMPLITUDE_DECADE = 20;
const MIN_STEREO_PAN = -1;
const MAX_STEREO_PAN = 1;
const STOP_TAIL_PADDING_SEC = 0.01;

const dbToGain = (db: number): number => 10 ** (db / DB_PER_AMPLITUDE_DECADE);
const clampPan = (pan: number): number => Math.max(MIN_STEREO_PAN, Math.min(MAX_STEREO_PAN, pan));

/**
 * Schedules one note: a fresh source, a gain that ramps up from 0 over the attack and back to 0
 * over the release, and a stereo panner. The source never runs past the end of its sample.
 */
export function playSample(
  ctx: BaseAudioContext,
  bank: SampleBank,
  instrument: Instrument,
  destination: AudioNode,
  note: SampleNote
): { file: string; rate: number } {
  const { zone, semitones } = chooseZone(
    instrument.zones,
    note.midi,
    note.layer,
    bank.nextCount(instrument.id)
  );
  const buffer = bank.buffer(zone.file);
  const rate = playbackRate(semitones, bank.tuningCents(zone.file) ?? 0);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.playbackRate.value = rate;
  const env = ctx.createGain();
  const panner = ctx.createStereoPanner();
  panner.pan.value = clampPan(note.pan);
  const peak = dbToGain(note.gainDb + instrument.gainDb);
  const t0 = Math.max(0, note.timeSec);
  // The sample's own length at this rate bounds the note, release included
  const available = buffer.duration / rate;
  const release = Math.min(instrument.releaseSec, available / 2);
  const hold = Math.max(instrument.attackSec, Math.min(note.durationSec, available - release));
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(peak, t0 + instrument.attackSec);
  env.gain.setValueAtTime(peak, t0 + hold);
  env.gain.linearRampToValueAtTime(0, t0 + hold + release);
  source.connect(env);
  env.connect(panner);
  panner.connect(destination);
  source.start(t0);
  source.stop(t0 + hold + release + STOP_TAIL_PADDING_SEC);
  return { file: zone.file, rate };
}
