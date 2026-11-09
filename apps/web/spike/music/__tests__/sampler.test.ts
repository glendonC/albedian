import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { chooseZone, measureTuningCents, playbackRate, vcslRootMidi, type SampleZone } from '../../../src/audio/music/sampler';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 100, examples } as never);

const zone = (file: string, rootMidi: number, layer = 0, roundRobin = 0): SampleZone => ({ file, rootMidi, layer, roundRobin });

describe('VCSL note names', () => {
  it('put middle C at C3, as measured on the organ, chimes and vibraphone samples', () => {
    // NT5_Man3Quiet_C3 sounds at 261.6 Hz (C4, MIDI 60) and sus_C4 at 522.7 Hz (C5, MIDI 72)
    expect(vcslRootMidi('C3')).toBe(60);
    expect(vcslRootMidi('C4')).toBe(72);
    expect(vcslRootMidi('D#1')).toBe(39);
    expect(vcslRootMidi('A#3')).toBe(70);
  });
});

describe('choosing a sample', () => {
  const organ = [zone('C', 60), zone('D#', 63), zone('F#', 66), zone('A', 69)];

  it('takes the nearest root and shifts it, a tie going to the lower root', () => {
    expect(chooseZone(organ, 60, 0, 0)).toMatchObject({ zone: { file: 'C' }, semitones: 0 });
    expect(chooseZone(organ, 62, 0, 0)).toMatchObject({ zone: { file: 'D#' }, semitones: -1 });
    expect(chooseZone(organ, 61, 0, 0)).toMatchObject({ zone: { file: 'C' }, semitones: 1 });
    expect(chooseZone(organ, 72, 0, 0)).toMatchObject({ zone: { file: 'A' }, semitones: 3 });
  });

  it('takes the nearest velocity layer, then turns through round robins', () => {
    const vibes = [zone('soft-a', 60, 0, 0), zone('soft-b', 60, 0, 1), zone('loud', 60, 1, 0)];
    expect(chooseZone(vibes, 60, 0, 0).zone.file).toBe('soft-a');
    expect(chooseZone(vibes, 60, 0, 1).zone.file).toBe('soft-b');
    expect(chooseZone(vibes, 60, 0, 2).zone.file).toBe('soft-a');
    expect(chooseZone(vibes, 60, 1, 5).zone.file).toBe('loud');
    expect(chooseZone(vibes, 60, 3, 0).zone.file).toBe('loud');
  });

  it('never shifts further than half the widest gap between roots', () => {
    fc.assert(
      fc.property(fc.integer({ min: 40, max: 100 }), (midi) => {
        const { zone: z, semitones } = chooseZone(organ, midi, 0, 0);
        expect(z.rootMidi! + semitones).toBe(midi);
        const nearest = Math.min(...organ.map((o) => Math.abs(o.rootMidi! - midi)));
        expect(Math.abs(semitones)).toBe(nearest);
      }),
      params([[60], [100]])
    );
  });
});

describe('playback rate', () => {
  it('shifts by equal-tempered semitones and corrects a measured tuning offset', () => {
    expect(playbackRate(12, 0)).toBeCloseTo(2, 12);
    expect(playbackRate(0, 0)).toBe(1);
    // a sample measured 11 cents sharp plays 11 cents slower to sound at its nominal pitch
    expect(playbackRate(0, 11)).toBeCloseTo(2 ** (-11 / 1200), 12);
  });
});

describe('measured tuning', () => {
  const SR = 48000;
  function tone(hz: number, partials: number[], seconds = 1.6): Float32Array {
    const out = new Float32Array(Math.round(seconds * SR));
    for (let i = 0; i < out.length; i++) {
      let v = 0;
      partials.forEach((a, k) => {
        v += a * Math.sin(2 * Math.PI * hz * (k + 1) * (i / SR));
      });
      out[i] = 0.4 * v;
    }
    return out;
  }

  it('finds how many cents a recorded note lies from its nominal pitch', () => {
    const nominal = 1046.5; // C6
    expect(measureTuningCents(tone(nominal * 2 ** (11 / 1200), [1]), SR, nominal)).toBeCloseTo(11, 0);
    expect(measureTuningCents(tone(130.81 * 2 ** (-14 / 1200), [1, 0.8, 0.5, 0.3]), SR, 130.81)).toBeCloseTo(-14, 0);
  });

  it('reads a note whose fundamental is weaker than its octave', () => {
    expect(measureTuningCents(tone(261.63 * 2 ** (5 / 1200), [0.2, 1, 0.6]), SR, 261.63)).toBeCloseTo(5, 0);
  });

  it('stays within a cent over offsets of +/-40 cents (property)', () => {
    fc.assert(
      fc.property(fc.double({ min: -40, max: 40, noNaN: true }), fc.double({ min: 110, max: 2000, noNaN: true }), (cents, nominal) => {
        expect(Math.abs(measureTuningCents(tone(nominal * 2 ** (cents / 1200), [1, 0.5]), SR, nominal) - cents)).toBeLessThan(1);
      }),
      { ...(params([[0, 440], [40, 110], [-40, 2000]]) as object), numRuns: 25 } as never
    );
  });

  it('measures nothing in silence rather than guessing', () => {
    expect(measureTuningCents(new Float32Array(SR), SR, 440)).toBeNull();
  });
});
