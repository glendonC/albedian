// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { INSTRUMENTS, RECORDING, ambientBeats, organAndPulse, recordingArrangement, type Arrangement, type InstrumentDef, type RecordingAnalysis } from '../directions';
import {
  CHOSEN_MUSIC_DIRECTION,
  MUSIC_DIRECTIONS,
  musicCreditLines,
  resolveMusicDirection,
  type MusicDirectionId,
} from '../musicDirection';
import type { ScreenStar } from '../starNotes';

/**
 * Two made-up complete stars, enough for each direction to play its data part. The credits depend on
 * which instruments a direction uses, not on star values; musicLayer.test.ts plays real tile rows.
 */
const STARS: ScreenStar[] = [
  { id: 'test-a', teffK: 5_000, luminositySolar: 1, distancePc: 10, screenX: -0.4, screenY: 0.2 },
  { id: 'test-b', teffK: 12_000, luminositySolar: 500, distancePc: 100, screenX: 0.5, screenY: -0.3 },
];

/** A made-up analysis of the shape main.ts measures, enough to build direction C. */
const ANALYSIS: RecordingAnalysis = {
  file: RECORDING.file,
  bpm: 75,
  beatSec: 0.8,
  tempoStrength: 0.5,
  beatsPerBar: 4,
  key: { name: 'D major', tonic: 2, mode: 'major', correlation: 0.8 },
  fromSec: 203,
  bars: Array.from({ length: RECORDING.bars }, (_, i) => ({ bar: i + 1, chord: i % 2 ? 'D' : null, confident: i % 2 === 1, score: 0.8, margin: 0.1 })),
  statement: 'test analysis',
};

function arrangementOf(id: MusicDirectionId): Arrangement {
  if (id === 'organ-pulse') return organAndPulse(STARS);
  if (id === 'ambient-beats') return ambientBeats(STARS);
  return recordingArrangement(STARS, ANALYSIS);
}

describe('the music direction setting', () => {
  it('names the three directions of the demo page', () => {
    expect(MUSIC_DIRECTIONS).toEqual(['organ-pulse', 'ambient-beats', 'recording']);
    // The owner has not picked yet: the app plays no music until this one line names a direction
    expect(CHOSEN_MUSIC_DIRECTION === null || MUSIC_DIRECTIONS.includes(CHOSEN_MUSIC_DIRECTION)).toBe(true);
  });

  it('plays the chosen direction in production, whatever the address says', () => {
    fc.assert(
      fc.property(fc.constantFrom<MusicDirectionId | null>(null, ...MUSIC_DIRECTIONS), fc.string(), (chosen, query) => {
        expect(resolveMusicDirection({ chosen, dev: false, search: `?music=${query}` })).toBe(chosen);
        expect(resolveMusicDirection({ chosen, dev: false, search: '?music=recording' })).toBe(chosen);
      }),
      { seed: SEED, examples: [[null, 'organ-pulse'], ['ambient-beats', 'off']] }
    );
  });

  it('lets development pick any of the three, or none, with ?music=', () => {
    for (const chosen of [null, ...MUSIC_DIRECTIONS] as const) {
      for (const id of MUSIC_DIRECTIONS) expect(resolveMusicDirection({ chosen, dev: true, search: `?music=${id}` })).toBe(id);
      expect(resolveMusicDirection({ chosen, dev: true, search: '?music=off' })).toBeNull();
      expect(resolveMusicDirection({ chosen, dev: true, search: '' })).toBe(chosen);
      expect(resolveMusicDirection({ chosen, dev: true, search: '?renderer=webgpu&music=ambient-beats' })).toBe('ambient-beats');
      // An unknown value keeps the chosen direction rather than guessing one
      expect(resolveMusicDirection({ chosen, dev: true, search: '?music=organ' })).toBe(chosen);
    }
  });
});

describe('the credits of the direction that plays', () => {
  it('has none when no music plays', () => {
    expect(musicCreditLines(null)).toEqual([]);
  });

  it('credits every instrument source and recording each direction plays, and nothing it does not', () => {
    for (const id of MUSIC_DIRECTIONS) {
      const arrangement = arrangementOf(id);
      const used = new Set<string>();
      for (const event of arrangement.events) if (event.type === 'sample') used.add((INSTRUMENTS[event.instrument] as InstrumentDef).credit);
      if (arrangement.recording) used.add(arrangement.recording.credit);
      const lines = musicCreditLines(id);
      for (const credit of used) expect(lines, `${id} credits ${credit}`).toContain(credit);
      for (const line of lines) {
        if (line === 'Music: Albedian') continue;
        expect(used, `${id} plays what ${line} credits`).toContain(line);
      }
    }
  });

  it('credits A and B as Albedian music, and C as the recording, with no em dash', () => {
    expect(musicCreditLines('organ-pulse')[0]).toBe('Music: Albedian');
    expect(musicCreditLines('ambient-beats')[0]).toBe('Music: Albedian');
    expect(musicCreditLines('recording')).not.toContain('Music: Albedian');
    expect(musicCreditLines('recording')[0]).toBe(RECORDING.credit);
    expect(musicCreditLines('organ-pulse').join(' ')).toContain('Simon Dalzell of Ivy Audio');
    for (const id of MUSIC_DIRECTIONS) for (const line of musicCreditLines(id)) expect(line).not.toMatch(/\u2014/);
  });
});
