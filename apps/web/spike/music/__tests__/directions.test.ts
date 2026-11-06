import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { pitchClassOf } from '../../../src/audio/music/harmony';
import type { ScreenStar } from '../../../src/audio/music/starNotes';
import {
  INSTRUMENTS,
  ambientBeats,
  dataNotesInChords,
  organAndPulse,
  RECORDING,
  recordingArrangement,
  recordingBarSet,
  type Arrangement,
  type InstrumentDef,
  type RecordingAnalysis,
} from '../directions';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 40, examples } as never);

const sources = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../sources.json'), 'utf8')) as {
  files: { path: string; licence: string; credit: string; sha256: string }[];
};
const byPath = new Map(sources.files.map((f) => [f.path, f]));

/** Stars with all three values, spread across the screen and the temperature scale. */
const anyStars = fc.array(
  fc.record({
    id: fc.uuid(),
    teffK: fc.double({ min: 2500, max: 40000, noNaN: true }),
    luminositySolar: fc.double({ min: 0.05, max: 5e4, noNaN: true }),
    distancePc: fc.double({ min: 2, max: 2000, noNaN: true }),
    screenX: fc.double({ min: -1, max: 1, noNaN: true }),
    screenY: fc.double({ min: -1, max: 1, noNaN: true }),
  }),
  { minLength: 0, maxLength: 40 }
) as fc.Arbitrary<ScreenStar[]>;

const analysis = (confident: boolean[]): RecordingAnalysis => ({
  file: RECORDING.file,
  bpm: 66.3,
  beatSec: 60 / 66.3,
  tempoStrength: 0.8,
  beatsPerBar: 4,
  key: { name: 'A minor', tonic: 9, mode: 'minor', correlation: 0.8 },
  fromSec: 203.1,
  bars: confident.map((c, i) => ({ bar: i + 1, chord: ['Am', 'F', 'C', 'G', 'E'][i % 5]!, confident: c, score: 0.8, margin: 0.1 })),
  statement: 'synthetic analysis for the test',
});

const arrangements = (stars: ScreenStar[]): Arrangement[] => [
  organAndPulse(stars),
  ambientBeats(stars),
  recordingArrangement(stars, analysis(Array.from({ length: RECORDING.bars }, (_, i) => i % 3 !== 1))),
];

describe('the three directions', () => {
  it('each last 45 to 60 seconds', () => {
    for (const a of arrangements([])) {
      expect(a.durationSec, a.id).toBeGreaterThanOrEqual(45);
      expect(a.durationSec, a.id).toBeLessThanOrEqual(60);
    }
  });

  it('put every star note on a tone of its bar\'s chord or pentatonic set, whatever the stars', () => {
    fc.assert(
      fc.property(anyStars, (stars) => {
        for (const a of arrangements(stars)) {
          const { notes, outside } = dataNotesInChords(a);
          expect(outside, a.id).toBe(0);
          if (stars.length > 0) expect(notes, a.id).toBeGreaterThan(0);
          for (const e of a.events) {
            if (e.type === 'sample' && e.kind === 'data') {
              expect(e.star, a.id).toBeDefined();
              expect(e.timeSec).toBeGreaterThanOrEqual(0);
              expect(e.timeSec).toBeLessThan(a.durationSec);
            }
          }
        }
      }),
      params([[[]]])
    );
  });

  it('play nothing for stars with a value missing, and nothing composed is marked as data', () => {
    const missing: ScreenStar[] = [{ id: 'm', teffK: null, luminositySolar: 1, distancePc: 10, screenX: 0, screenY: 0 }];
    for (const a of arrangements(missing)) {
      expect(dataNotesInChords(a).notes, a.id).toBe(0);
      for (const e of a.events) if (e.type === 'sample' && e.kind === 'composed') expect(e.star).toBeUndefined();
    }
  });

  it('use only files that sources.json records under CC0 1.0 or CC BY 4.0', () => {
    for (const a of arrangements([])) {
      const instruments = new Set(a.events.flatMap((e) => (e.type === 'sample' ? [e.instrument] : [])));
      instruments.add(a.id === 'organ-pulse' ? 'chimes' : a.id === 'ambient-beats' ? 'vibes' : 'glock');
      for (const id of instruments) {
        for (const z of (INSTRUMENTS[id] as InstrumentDef).zones) {
          const record = byPath.get(z.file);
          expect(record, z.file).toBeDefined();
          expect(['CC0 1.0 Universal', 'CC BY 4.0']).toContain(record!.licence);
        }
      }
      if (a.recording) {
        expect(byPath.get(a.recording.file)?.licence).toBe('CC BY 4.0');
        expect(a.recording.credit).toBe(byPath.get(a.recording.file)!.credit);
      }
    }
  });

  it('label every part composed, data or recording, with one data part', () => {
    for (const a of arrangements([])) {
      expect(a.parts.filter((p) => p.kind === 'data')).toHaveLength(1);
      expect(a.parts.filter((p) => p.kind === 'recording')).toHaveLength(a.recording ? 1 : 0);
      for (const p of a.parts) expect(p.source.length).toBeGreaterThan(0);
    }
  });
});

describe('the recording direction', () => {
  it('uses a bar\'s measured chord only when it is confident, and the key\'s pentatonic notes otherwise', () => {
    expect(recordingBarSet({ chord: 'F', confident: true }, 9, 'minor', 'A minor')).toMatchObject({ source: 'measured', set: { name: 'F' } });
    const fallback = recordingBarSet({ chord: 'F', confident: false }, 9, 'minor', 'A minor');
    expect(fallback.source).toBe('pentatonic fallback');
    expect([...fallback.set.pitchClasses].sort((a, b) => a - b)).toEqual([0, 2, 4, 7, 9]);
    expect(recordingBarSet({ chord: null, confident: true }, 9, 'minor', 'A minor').source).toBe('pentatonic fallback');
  });

  it('puts the stars on the measured beat: every note time is a whole number of eighths from the excerpt start', () => {
    const stars: ScreenStar[] = Array.from({ length: 12 }, (_, i) => ({ id: `s${i}`, teffK: 3000 + 1500 * i, luminositySolar: 10 + i, distancePc: 50, screenX: -1 + i / 6, screenY: 0 }));
    const a = recordingArrangement(stars, analysis(Array.from({ length: RECORDING.bars }, () => true)));
    const eighth = 60 / 66.3 / 2;
    for (const e of a.events) {
      if (e.type !== 'sample') continue;
      const steps = e.timeSec / eighth;
      expect(Math.abs(steps - Math.round(steps))).toBeLessThan(1e-9);
      expect([9, 0, 4, 5, 7, 2, 11, 8]).toContain(pitchClassOf(e.midi));
    }
  });
});
