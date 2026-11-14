/**
 * The synthesized parts of the music directions (AUDIO_ENGINE.md section 13): a detuned-saw pad, a
 * sub bass, a kick, a noise riser, a synthetic hall response, a ping-pong delay and the pad's
 * ducking under the kick. All are composed sound design, not data. Every note opens and closes with
 * a ramp, so none starts or stops with a click.
 *
 * Public re-export coordinator over focused modules in `./synthModules/`.
 */

export type { MonoNote, PadNote } from './synthModules/types.js';
export { playPad, playSub } from './synthModules/tonalVoices.js';
export { playKick, playRiser } from './synthModules/percussionAndRiser.js';
export { duckUnderKicks, pingPongDelay, syntheticHallImpulse } from './synthModules/spatialEffects.js';
