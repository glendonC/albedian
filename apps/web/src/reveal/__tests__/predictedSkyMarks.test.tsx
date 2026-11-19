import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSolution } from '@albedian/contracts';
import { POSE } from '../../components/__tests__/poseSessions.js';
import { placeSkyMarks, predictedSkyMarks, SKY_NAMED_RING_PX, SKY_UNNAMED_RING_PX } from '../predictedSkyMarks.js';
import type { CameraSkyPose } from '../../spatial/cosmosRenderer.js';

// D33 item 4 (UIUX-predsky-READY.md, decided 12:45), D20's rule (UIUX-aimnames-DECIDED-BY-LEAD.md): every predicted star
// brighter than magnitude 3.2 in view keeps its amber dashed ring; a ring gets its name only when the star has a proper
// name and its name box fits on screen and runs into no brighter star's name and no ring; brightest first; right of
// the ring, else left; a named ring r 6.5, an unnamed one r 4.5.

const W = 1440;
const H = 900;
const at = (id: string, name: string | null, xPx: number, yPx: number) => ({ id, name, xPx, yPx });

describe('placeSkyMarks: names by D20', () => {
  it('names a lone star right of its ring at r 6.5, and a star with no proper name gets an r 4.5 ring', () => {
    const marks = placeSkyMarks([at('a', 'Vega', 700, 400), at('b', null, 300, 300)], W, H, 13);
    expect(marks).toEqual([
      { id: 'a', xPx: 700, yPx: 400, r: SKY_NAMED_RING_PX, name: 'Vega', nameOnLeft: false },
      { id: 'b', xPx: 300, yPx: 300, r: SKY_UNNAMED_RING_PX, name: null, nameOnLeft: false },
    ]);
    expect([SKY_NAMED_RING_PX, SKY_UNNAMED_RING_PX]).toEqual([6.5, 4.5]);
  });

  it('flips a name left near the right edge, and drops it where it fits neither side', () => {
    const [right] = placeSkyMarks([at('a', 'Fomalhaut', W - 20, 400)], W, H, 13);
    expect(right).toMatchObject({ name: 'Fomalhaut', nameOnLeft: true });
    const [tight] = placeSkyMarks([at('a', 'Fomalhaut and a much longer name than fits', 150, 400)], 300, H, 13);
    expect(tight).toMatchObject({ name: null, r: SKY_UNNAMED_RING_PX });
  });

  it('keeps every ring clear of names: a name that would cover any ring goes to the other side, or is not drawn', () => {
    // Arcturus's right box would cover Izar's ring, 40 px right of it, so its name goes left; Izar's own name fits right
    const marks = placeSkyMarks([at('bright', 'Arcturus', 600, 400), at('faint', 'Izar', 640, 402)], W, H, 13);
    expect(marks.map((m) => [m.name, m.nameOnLeft])).toEqual([['Arcturus', true], ['Izar', false]]);
    // Rings on both sides of the faintest: its name fits neither, so its ring is drawn unnamed and smaller
    const boxed = placeSkyMarks([at('a', 'Arcturus', 625, 400), at('b', 'Muphrid', 680, 400), at('c', 'Izar', 650, 402)], W, H, 13);
    expect(boxed.map((m) => [m.name, m.nameOnLeft])).toEqual([['Arcturus', true], ['Muphrid', false], [null, false]]);
    expect(boxed[2]).toMatchObject({ name: null, r: SKY_UNNAMED_RING_PX });
  });

  it("keeps a fainter star's name off a brighter star's name", () => {
    // Izar's right side is taken by an unnamed ring, its left by Arcturus's name just above it
    const marks = placeSkyMarks([at('a', 'Arcturus', 600, 400), at('b', 'Izar', 700, 406), at('c', null, 725, 406)], W, H, 13);
    expect(marks.map((m) => [m.name, m.nameOnLeft])).toEqual([['Arcturus', false], [null, false], [null, false]]);
  });

  it('keeps clear of the status column, the Sound circle and the foot\'s buttons (ruled 13:10)', () => {
    const status = { x0: 36, x1: 336, y0: 70, y1: 304 };
    // A star under the column gets no ring and no name; one beside it keeps its ring, its name only where it clears
    const marks = placeSkyMarks([at('under', 'Phecda', 120, 180), at('beside', 'Dubhe', 360, 208)], W, H, 13, [status]);
    expect(marks.map((m) => [m.id, m.name, m.nameOnLeft])).toEqual([['beside', 'Dubhe', false]]);
    // Its name would run into the column on the left, so where the right is taken too it is not drawn
    const hemmed = placeSkyMarks([at('a', 'Merak', 360, 260), at('b', null, 385, 260)], W, H, 13, [status]);
    expect(hemmed[0]).toMatchObject({ id: 'a', name: null, r: SKY_UNNAMED_RING_PX });
    // A ring that would touch the column's edge is under it too (Dubhe at the end of the second sentence, 13:23)
    expect(placeSkyMarks([at('edge', 'Dubhe', 340, 208)], W, H, 13, [status])).toEqual([]);
    // Out from under it, by a drag, the same star is ringed and named again
    expect(placeSkyMarks([at('under', 'Phecda', 520, 180)], W, H, 13, [status])[0]).toMatchObject({ name: 'Phecda' });
  });

  it('names only a proper name, never a catalogue number (D20): Gaia DR3 and HIP numbers get an unnamed ring', async () => {
    const { properName } = await import('../predictedSkyMarks.js');
    expect(['Vega', 'Gaia DR3 1621259036510505344', 'HIP 54539', 'HD 12345', 'HR 7001', 'TYC 1-2-3', '12345', '  ', undefined].map(properName)).toEqual(['Vega', null, null, null, null, null, null, null, null]);
  });

  it('draws nothing off screen', () => {
    expect(placeSkyMarks([at('a', 'Vega', -5, 400), at('b', 'Deneb', 700, H + 2)], W, H, 13)).toEqual([]);
  });
});

describe('predictedSkyMarks: the predicted stars through the renderer camera', () => {
  // The camera at the identity looks along ICRS -z, the south celestial pole, 60 degrees high
  const POLE: CameraSkyPose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: W / H, limitingMagnitude: 6 };
  const star = (id: string, magnitude: number, decDeg: number, commonName?: string, evidenceClass: CelestialFinding['evidenceClass'] = 'predicted') =>
    ({ ...POSE.findings.find((f) => f.id.startsWith('finding-predicted-star-'))!, id, evidenceClass, skyPosition: { raDeg: 10, decDeg, uncertaintyArcsec: 1 }, catalogRef: { ...POSE.findings.find((f) => f.id.startsWith('finding-predicted-star-'))!.catalogRef!, apparentMagnitude: magnitude, commonName } }) as CelestialFinding;
  const sky = (findings: CelestialFinding[]): ObservationSolution => ({ ...POSE, findings });

  it('rings the predicted stars of magnitude 3.2 or brighter in view, brightest first, and names them by their proper name', () => {
    const marks = predictedSkyMarks(sky([star('finding-predicted-star-b', 2.0, -85, 'Beta'), star('finding-predicted-star-a', 0.5, -80, 'Alpha'), star('finding-predicted-star-faint', 4.0, -86, 'Faint')]), POLE, W, H);
    expect(marks.map((m) => [m.id, m.name])).toEqual([['finding-predicted-star-a', 'Alpha'], ['finding-predicted-star-b', 'Beta']]);
  });

  it('takes every predicted star by its kind, whatever its id: a Hipparcos and a Gaia star are ringed and named (FIELD 13:30)', () => {
    const hip = { ...star('finding-predicted-hip-30438', 0.7, -80, 'Canopus'), catalogRef: { ...star('x', 0.7, -80).catalogRef!, objectType: 'star', commonName: 'Canopus', apparentMagnitude: -0.7 } } as CelestialFinding;
    const gaia = { ...star('finding-predicted-gaia-1', 1.5, -86, 'Mimosa'), catalogRef: { ...star('y', 1.5, -86).catalogRef!, objectType: 'star', commonName: 'Mimosa', apparentMagnitude: 1.25 } } as CelestialFinding;
    const planet = { ...star('finding-predicted-star-not', 1, -84, 'Notastar'), catalogRef: { ...star('z', 1, -84).catalogRef!, objectType: 'planet', commonName: 'Notastar', apparentMagnitude: 1 } } as CelestialFinding;
    const marks = predictedSkyMarks(sky([hip, gaia, planet]), POLE, W, H);
    expect(marks.map((m) => m.id)).toEqual(['finding-predicted-hip-30438', 'finding-predicted-gaia-1']);
  });

  it('draws no star behind the camera or out of view, and nothing that is not predicted', () => {
    const marks = predictedSkyMarks(sky([star('finding-predicted-star-n', 1, 80, 'North'), star('finding-predicted-star-m', 1, -85, 'Matched', 'catalog_matched')]), POLE, W, H);
    expect(marks).toEqual([]);
  });

  it('is none without a pose the renderer gives', () => {
    expect(predictedSkyMarks(sky([star('finding-predicted-star-a', 0.5, -80, 'Alpha')]), null, W, H)).toEqual([]);
  });
});
