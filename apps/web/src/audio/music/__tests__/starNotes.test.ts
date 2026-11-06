import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { decodeAstroTile, decodeCoreTile } from '../../../spatial/catalogStars/tileCodec';
import { receivedLevelDb } from '../../mappings';
import { directionInView, skySourcesInView, skyViewFromPose, type SkyTileData, type SkyView } from '../../skySources';
import type { AudioSourceInput, Quat, Vec3 } from '../../voicePlanning';
import { parseChord, pitchClassOf, pitchSet, scalePitchClasses } from '../harmony';
import {
  DEFAULT_STAR_NOTE_MAPPING,
  loudestStars,
  screenPosition,
  screenStars,
  starNote,
  starSweep,
  sweepOrder,
  temperaturePosition,
  type ScreenStar,
  type StarNoteMapping,
} from '../starNotes';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const params = (examples: unknown[] = []) => ({ ...(SEED !== undefined ? { seed: SEED } : {}), numRuns: 300, examples } as never);

/** Two octaves of D4 to D6 as a register, the rest of the default mapping. */
const MAPPING: StarNoteMapping = { ...DEFAULT_STAR_NOTE_MAPPING, lowMidi: 62, highMidi: 86 };

function star(id: string, teffK: number | null, luminositySolar: number | null, distancePc: number | null, screenX = 0, screenY = 0): ScreenStar {
  return { id, teffK, luminositySolar, distancePc, screenX, screenY };
}

describe('temperature position', () => {
  it('runs from 0 at the low bound to 1 at the high bound on a log scale, clamped', () => {
    expect(temperaturePosition(3000, MAPPING)).toBe(0);
    expect(temperaturePosition(30000, MAPPING)).toBeCloseTo(1, 12);
    expect(temperaturePosition(Math.sqrt(3000 * 30000), MAPPING)).toBeCloseTo(0.5, 12);
    expect(temperaturePosition(1500, MAPPING)).toBe(0);
    expect(temperaturePosition(60000, MAPPING)).toBe(1);
  });
});

describe('a star note', () => {
  const dm = parseChord('Dm');

  it('takes the chord tone at its temperature: coolest lowest, hottest highest', () => {
    // Dm from D4 to D6 holds 7 tones: D4 F4 A4 D5 F5 A5 D6; each gets an equal share of log temperature
    expect(starNote(star('a', 3000, 1, 10), dm, MAPPING)!.midi).toBe(62);
    expect(starNote(star('b', 30000, 1, 10), dm, MAPPING)!.midi).toBe(86);
    expect(starNote(star('c', Math.sqrt(3000 * 30000), 1, 10), dm, MAPPING)!.midi).toBe(74);
  });

  it('plays nothing for a star missing a value, or a register with no chord tone', () => {
    expect(starNote(star('a', null, 1, 10), dm, MAPPING)).toBeNull();
    expect(starNote(star('a', 5000, null, 10), dm, MAPPING)).toBeNull();
    expect(starNote(star('a', 5000, 1, null), dm, MAPPING)).toBeNull();
    expect(starNote(star('a', Number.NaN, 1, 10), dm, MAPPING)).toBeNull();
    expect(starNote(star('a', 5000, 1, 10), dm, { ...MAPPING, lowMidi: 63, highMidi: 64 })).toBeNull();
  });

  it('sets its gain from the received level, 2 dB per magnitude, between the floor and 0 dB', () => {
    const near = starNote(star('a', 5772, 1, 10), dm, MAPPING)!;
    expect(near.receivedLevelDb).toBeCloseTo(receivedLevelDb(1, 10)!, 12);
    expect(near.gainDb).toBeCloseTo(
      Math.max(MAPPING.floorDb, Math.min(0, MAPPING.dbPerDb * (receivedLevelDb(1, 10)! - MAPPING.levelRefDb))),
      12
    );
    const far = starNote(star('b', 5772, 1, 1e6), dm, MAPPING)!;
    expect(far.gainDb).toBe(MAPPING.floorDb);
  });

  it('pans with its place on screen and keeps the values it was made from', () => {
    const note = starNote(star('a', 5000, 2, 30, -0.5, 0.2), dm, MAPPING)!;
    expect(note.pan).toBeCloseTo(-0.5 * MAPPING.panWidth, 12);
    expect(note).toMatchObject({ starId: 'a', teffK: 5000, luminositySolar: 2, distancePc: 30, screenX: -0.5, chord: 'Dm' });
  });

  const chords = ['Dm', 'Bb/D', 'C/D', 'Gm/D', 'D', 'Fm9', 'Dbmaj7', 'Eb6', 'Cm7', 'Asus4', 'Abadd9'];
  const anyStar = fc.record({
    teffK: fc.double({ min: 1000, max: 80000, noNaN: true }),
    luminositySolar: fc.double({ min: 1e-4, max: 1e6, noNaN: true }),
    distancePc: fc.double({ min: 1, max: 1e4, noNaN: true }),
    screenX: fc.double({ min: -1, max: 1, noNaN: true }),
  });
  const register = fc.integer({ min: 36, max: 84 }).chain((low) => fc.tuple(fc.constant(low), fc.integer({ min: low + 12, max: low + 40 })));

  it('always lands on a tone of its chord, inside the register', () => {
    fc.assert(
      fc.property(fc.constantFrom(...chords), anyStar, register, (name, s, [low, high]) => {
        const chord = parseChord(name);
        const note = starNote(star('s', s.teffK, s.luminositySolar, s.distancePc, s.screenX), chord, { ...MAPPING, lowMidi: low, highMidi: high });
        expect(note).not.toBeNull();
        expect(chord.pitchClasses).toContain(pitchClassOf(note!.midi));
        expect(note!.midi).toBeGreaterThanOrEqual(low);
        expect(note!.midi).toBeLessThanOrEqual(high);
        expect(note!.gainDb).toBeLessThanOrEqual(0);
        expect(note!.gainDb).toBeGreaterThanOrEqual(MAPPING.floorDb);
      }),
      params([['Dm', { teffK: 3000, luminositySolar: 1, distancePc: 10, screenX: 0 }, [62, 86]], ['Fm9', { teffK: 80000, luminositySolar: 1e6, distancePc: 1, screenX: 1 }, [36, 48]]])
    );
  });

  it('never gives a hotter star a lower note, nor a brighter one a lower gain, in the same chord', () => {
    fc.assert(
      fc.property(fc.constantFrom(...chords), anyStar, anyStar, register, (name, a, b, [low, high]) => {
        const chord = parseChord(name);
        const mapping = { ...MAPPING, lowMidi: low, highMidi: high };
        const na = starNote(star('a', a.teffK, a.luminositySolar, a.distancePc), chord, mapping)!;
        const nb = starNote(star('b', b.teffK, b.luminositySolar, b.distancePc), chord, mapping)!;
        if (a.teffK <= b.teffK) expect(na.midi).toBeLessThanOrEqual(nb.midi);
        if (na.receivedLevelDb <= nb.receivedLevelDb) expect(na.gainDb).toBeLessThanOrEqual(nb.gainDb);
      }),
      params()
    );
  });

  it('keeps to a pentatonic set as it would to a chord', () => {
    const pentatonic = pitchSet('D minor pentatonic', 2, scalePitchClasses(2, 'minorPentatonic'));
    const note = starNote(star('a', 12000, 1, 10), pentatonic, MAPPING)!;
    expect(pentatonic.pitchClasses).toContain(pitchClassOf(note.midi));
    expect(note.chord).toBe('D minor pentatonic');
  });
});

describe('the sweep', () => {
  it('takes the loudest complete stars and plays them left to right across the screen', () => {
    const stars = [
      star('far-right-quiet', 5000, 1, 100, 0.9),
      star('left-loud', 5000, 1000, 10, -0.8),
      star('missing', null, 1e6, 1, 0),
      star('centre', 5000, 100, 10, 0),
      star('right', 5000, 50, 10, 0.5),
    ];
    expect(loudestStars(stars, 3).map((s) => s.id)).toEqual(['left-loud', 'centre', 'right']);
    expect(starSweep(stars, parseChord('Dm'), MAPPING, 3).map((n) => n.starId)).toEqual(['left-loud', 'centre', 'right']);
    expect(starSweep(stars, parseChord('Dm'), MAPPING, 10).map((n) => n.starId)).toEqual(['left-loud', 'centre', 'right', 'far-right-quiet']);
  });

  it('does not depend on the order the stars arrive in', () => {
    const many = fc.array(
      fc.record({
        id: fc.string({ minLength: 1, maxLength: 6 }),
        teffK: fc.option(fc.double({ min: 2000, max: 40000, noNaN: true }), { nil: null }),
        luminositySolar: fc.double({ min: 0.01, max: 1e4, noNaN: true }),
        distancePc: fc.double({ min: 1, max: 3000, noNaN: true }),
        screenX: fc.double({ min: -1, max: 1, noNaN: true }),
        screenY: fc.double({ min: -1, max: 1, noNaN: true }),
      }),
      { maxLength: 30 }
    );
    fc.assert(
      fc.property(many, fc.integer({ min: 0, max: 12 }), (rows, count) => {
        const unique = [...new Map(rows.map((r) => [r.id, r])).values()];
        const forward = starSweep(unique, parseChord('Fm9'), MAPPING, count);
        const backward = starSweep([...unique].reverse(), parseChord('Fm9'), MAPPING, count);
        expect(backward).toEqual(forward);
        expect(forward.length).toBeLessThanOrEqual(count);
        for (let i = 1; i < forward.length; i++) expect(forward[i]!.screenX).toBeGreaterThanOrEqual(forward[i - 1]!.screenX);
      }),
      params()
    );
  });

  it('orders a column of stars from the top down', () => {
    const column = [star('low', 5000, 1, 10, 0, -0.5), star('high', 5000, 1, 10, 0, 0.5)];
    expect(sweepOrder(column).map((s) => s.id)).toEqual(['high', 'low']);
  });
});

describe('screen positions', () => {
  const view = skyViewFromPose({ cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 40, aspect: 16 / 9, limitingMagnitude: 6.5 })!;

  it('puts the view centre at 0, the right edge at 1 and the top edge at 1', () => {
    expect(screenPosition([0, 0, -1], view)).toEqual({ x: 0, y: 0 });
    const right = screenPosition([view.tanHalfFovX, 0, -1], view)!;
    expect(right.x).toBeCloseTo(1, 12);
    const top = screenPosition([0, view.tanHalfFovY, -1], view)!;
    expect(top.y).toBeCloseTo(1, 12);
    expect(screenPosition([0, 0, 1], view)).toBeNull();
  });

  it('agrees with the voice feed on what is on screen', () => {
    const direction = fc
      .tuple(fc.double({ min: -1, max: 1, noNaN: true }), fc.double({ min: -1, max: 1, noNaN: true }), fc.double({ min: -1, max: 1, noNaN: true }))
      .filter(([x, y, z]) => Math.hypot(x, y, z) > 1e-3)
      .map(([x, y, z]) => {
        const n = Math.hypot(x, y, z);
        return [x / n, y / n, z / n] as Vec3;
      });
    fc.assert(
      fc.property(direction, (d) => {
        const p = screenPosition(d, view);
        const onScreen = p !== null && Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1;
        expect(onScreen).toBe(directionInView(d, view));
      }),
      params([[[0, 0, -1]], [[0, 0, 1]]])
    );
  });
});

// The real-moment landing's camera in ICRS, as landingPose() computes it (view centre RA 270.04,
// Dec -31.81, galactic north up; 26 degree vertical field, 16:9). Pinned so this test does not move with the scene.
const LANDING_ORIENTATION_ICRS: Quat = [-0.24501256854084003, 0.42005652250084013, 0.7550861442785153, 0.43973432192646345];

describe('the landing on real catalog rows', () => {
  const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../../${rel}`, import.meta.url));
  const readBuffer = (rel: string): ArrayBuffer => {
    const bytes = fs.readFileSync(repoFile(rel));
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  };
  const RELEASE = 'apps/web/public/catalog/stars/gaia-dr3';
  const manifest = JSON.parse(fs.readFileSync(repoFile(`${RELEASE}/manifest.json`), 'utf8')) as {
    releaseId: string;
    layers: { order: number; tileStarCounts: number[] }[];
  };
  const order = manifest.layers[0]!.order;
  const tiles: SkyTileData[] = manifest.layers[0]!.tileStarCounts.flatMap((count, pix) =>
    count > 0
      ? [{
          core: decodeCoreTile(readBuffer(`${RELEASE}/L0/${pix}.core.bin`), { layer: 0, order, pix }),
          astro: decodeAstroTile(readBuffer(`${RELEASE}/L0/${pix}.astro.bin`), { layer: 0, order, pix }),
        }]
      : []
  );
  const view: SkyView = skyViewFromPose({
    cameraWorldQuaternion: LANDING_ORIENTATION_ICRS,
    icrsWorldQuaternion: [0, 0, 0, 1],
    fovDeg: 26,
    aspect: 16 / 9,
    limitingMagnitude: 6.5,
  })!;
  const sources: AudioSourceInput[] = skySourcesInView(tiles, view, { releaseId: manifest.releaseId, maxAudible: 64, maxSilent: 0 }).sources;

  it('plays its eight loudest Gaia DR3 stars left to right as tones of D minor', () => {
    const notes = starSweep(screenStars(sources, view), parseChord('Dm'), MAPPING, 8);
    const id = (s: string) => `${manifest.releaseId}:${s}`;
    // [id, Gaia DR3 GSP-Phot Teff (K), note]; Dm from D4 to D6 is D4 F4 A4 D5 F5 A5 D6
    const expected: [string, number, number][] = [
      [id('L0/7#47'), 7421, 69],
      [id('L0/7#164'), 3457, 62],
      [id('L0/7#54'), 4499, 65],
      [id('L0/7#73'), 5374, 65],
      [id('L0/7#83'), 6114, 69],
      [id('L0/11#125'), 17136, 81],
      [id('L0/11#512'), 5147, 65],
      [id('L0/10#48'), 6423, 69],
    ];
    expect(notes.map((n) => n.starId)).toEqual(expected.map((e) => e[0]));
    notes.forEach((note, i) => {
      expect(note.teffK).toBeCloseTo(expected[i]![1], 0);
      expect(note.midi).toBe(expected[i]![2]);
    });
    // The loudest, L0/7#54 (4,499 K, 2,754 Lsun at 222 pc), sits about 4.3 dB above the eighth
    const gains = new Map(notes.map((n) => [n.starId, n.gainDb]));
    expect(gains.get(id('L0/7#54'))! - gains.get(id('L0/7#47'))!).toBeCloseTo(0.5 * (31.812 - 27.488), 1);
  });

  it('leaves out the Teapot stars that Gaia DR3 gives no temperature or luminosity', () => {
    const all = skySourcesInView(tiles, view, { releaseId: manifest.releaseId, maxAudible: 64, maxSilent: 64 }).sources;
    const silent = all.filter((s) => s.teffK == null || s.luminositySolar == null);
    expect(silent.length).toBeGreaterThan(0);
    const played = new Set(starSweep(screenStars(all, view), parseChord('Dm'), MAPPING, 200).map((n) => n.starId));
    for (const s of silent) expect(played.has(s.id)).toBe(false);
  });
});
