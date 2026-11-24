import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { RECORDING, recordingArrangement, type RecordingAnalysis } from '../directions';

// music-facts-tracked-one (LEAD ANSWER 18:04 on 24 Nov): the recording piece's measured analysis is tracked beside the
// music's catalogue, so a clean clone's recording piece plays with no render (serveSources.mjs serves it at
// /recording-analysis.json when no render's facts.json holds one). It is the analysis of the render of 7 November at
// commit 1ef14db7, as .cache/music/facts.json recorded it.

const here = path.dirname(fileURLToPath(import.meta.url));
const tracked = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../spike/music/recording-analysis.json'), 'utf8')) as {
  about: string;
  render: { commit: string; renderedAt: string; uncommittedAudioChanges: boolean };
  analysis: RecordingAnalysis;
};

describe("the recording piece's tracked analysis", () => {
  it('is the analysis of the file the piece plays, from a named render of committed audio code', () => {
    const { analysis, render } = tracked;
    // MusicLayer takes an analysis only of RECORDING.file
    expect(analysis.file).toBe(RECORDING.file);
    expect(render).toEqual({ commit: '1ef14db7a6657f069572b6f58a2d0a0446972797', uncommittedAudioChanges: false, renderedAt: '2026-11-07' });
    expect(tracked.about).toContain('1ef14db7');
  });

  it('holds a whole analysis the piece can be arranged from: a tempo, a key, a downbeat and every bar', () => {
    const { analysis } = tracked;
    expect(analysis.bpm).toBeGreaterThan(0);
    expect(analysis.beatSec).toBeCloseTo(60 / analysis.bpm, 6);
    expect(analysis.beatsPerBar).toBe(4);
    expect(['major', 'minor']).toContain(analysis.key.mode);
    expect(analysis.fromSec).toBeGreaterThan(0);
    expect(analysis.bars.map((bar) => bar.bar)).toEqual(Array.from({ length: RECORDING.bars }, (_, i) => i + 1));
    const arrangement = recordingArrangement([], analysis);
    expect(arrangement.recording).toMatchObject({ file: RECORDING.file, fromSec: analysis.fromSec });
    expect(arrangement.durationSec).toBeGreaterThan(0);
  });
});
