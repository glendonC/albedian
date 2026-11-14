/**
 * Plays recorded instrument samples (VCSL, CC0 1.0) for the music directions (AUDIO_ENGINE.md
 * section 13): the nearest recorded note shifted to the wanted pitch, the nearest velocity layer,
 * round robins in turn, and every note opened and closed with a ramp so none starts or stops with a
 * click. Each pitched sample's tuning is measured once when it loads, and the playback rate corrects
 * it, so a sample recorded a few cents sharp still sounds at its nominal pitch.
 *
 * The zone choice, the playback rate and the tuning measure are pure; the bank and `playSample`
 * need a Web Audio context.
 */

export type { Instrument, SampleNote, SampleZone } from './samplerModules/types.js';
export { chooseZone, playbackRate, vcslRootMidi } from './samplerModules/zoneSelection.js';
export { measureTuningCents } from './samplerModules/tuningAnalysis.js';
export { SampleBank } from './samplerModules/sampleBank.js';
export { playSample } from './samplerModules/samplePlayer.js';
