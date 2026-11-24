import { describe, expect, it } from 'vitest';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { NAME_MAGNITUDE_LIMIT, revealMarks } from '../revealMarks.js';

// Which marks get a name on a crowded photo: the design lane's rule for the calibrated sample (UIUX-sample-b01-NOTE.md
// point 2, drawn by .cache/uiux/lib/sky.mjs), built as an interim for the owner's review. Every matched star is
// marked; a name is drawn only for a named star brighter than magnitude 3.2 whose name does not run into another mark.

const VIEW = { w: 1440, h: 900 };
// The fixture's frame is 960 by 540, drawn at 1.5 screen pixels a photo pixel from y 45: (px + 0.5) * 1.5
const base = sample('plate_calibrated');
const star = base.findings.find((f) => f.title === 'Betelgeuse')!;

/** A catalog matched star of the fixture's kind at a photo pixel, with its own title and magnitude. */
function at(title: string, xPx: number, yPx: number, magnitude: number | null = 1, over: Partial<CelestialFinding> = {}): CelestialFinding {
  const id = `finding-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  return {
    ...star,
    id,
    title,
    sourceDetection: { ...star.sourceDetection!, xPx, yPx },
    catalogRef: { ...star.catalogRef!, commonName: title, catalogSourceId: `HIP ${id.length}${xPx}`, apparentMagnitude: magnitude ?? undefined },
    ...over,
  } as CelestialFinding;
}
function sessionOf(findings: CelestialFinding[], pickedId: string | null = null): ObservationSession {
  // The stars are placed by hand here (one with no magnitude, which the contract refuses for a star), so the session
  // is built from the fixture and given them after
  const session = poseSession(base);
  return { ...session, observationSolution: { ...session.observationSolution!, findings } as ObservationSolution, selectedFindingId: pickedId };
}
const names = (session: ObservationSession) => Object.fromEntries(revealMarks(session, VIEW.w, VIEW.h).map((mark) => [mark.finding.title, mark.name === null ? null : mark.nameOnLeft ? 'left' : 'right']));

describe('a name is drawn only for a named star brighter than magnitude 3.2', () => {
  it('keeps the limit the design lane set', () => {
    expect(NAME_MAGNITUDE_LIMIT).toBe(3.2);
  });

  it('names a star just brighter than the limit and not one at it, fainter, or with no magnitude', () => {
    const session = sessionOf([at('Bright', 100, 100, 3.19), at('AtLimit', 300, 100, 3.2), at('Faint', 500, 100, 5.5), at('Unknown', 700, 100, null)]);
    expect(names(session)).toEqual({ Bright: 'right', AtLimit: null, Faint: null, Unknown: null });
  });

  it('marks every one of them all the same', () => {
    const session = sessionOf([at('Bright', 100, 100, 3.19), at('Faint', 500, 100, 5.5)]);
    expect(revealMarks(session, VIEW.w, VIEW.h)).toHaveLength(2);
  });

  it('never draws a catalog number as a name, however bright the star', () => {
    const numbered = at('HIP 24436', 100, 100, 0.1);
    const session = sessionOf([{ ...numbered, catalogRef: { ...numbered.catalogRef!, catalogSourceId: 'HIP 24436', commonName: 'HIP 24436' } }]);
    expect(names(session)).toEqual({ 'HIP 24436': null });
  });

  it('names a star titled by its designation, which is a name and not a catalog number', () => {
    const designated = at('Gamma Centauri', 100, 100, 2.2);
    const session = sessionOf([{ ...designated, catalogRef: { ...designated.catalogRef!, catalogSourceId: 'HIP 61932', commonName: 'HIP 61932', bayerDesignation: 'Gamma Centauri' } }]);
    expect(names(session)).toEqual({ 'Gamma Centauri': 'right' });
  });

  it('names the picked star whatever it is: faint, numbered, or among other marks', () => {
    const faint = at('Faint', 500, 100, 5.5);
    const crowd = at('Crowd', 520, 100, 1);
    expect(names(sessionOf([faint, crowd], faint.id)).Faint).toBe('right');
  });
});

describe("a name does not run into another mark", () => {
  // On screen a 13 px name starts 19 px from its mark and is 7.28 px a letter wide; a mark keeps 14 px around its centre
  it('goes to the left of its mark when another mark sits where it would go on the right', () => {
    // "Mimosa" (6 letters, about 44 px): a mark 30 photo px (45 on screen) to the right sits inside its box
    const session = sessionOf([at('Mimosa', 400, 200, 1.2), at('Blocker', 430, 200, 6)]);
    expect(names(session).Mimosa).toBe('left');
  });

  it('stays on the right when the other mark is clear of it, beside or above', () => {
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Far', 460, 200, 6)])).Mimosa).toBe('right');
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Above', 430, 170, 6)])).Mimosa).toBe('right');
  });

  it('keeps 14 px of room around each mark: a mark whose ticks reach the name moves it, one a little further up does not', () => {
    // 13 photo px above the name's line is 19.5 px on screen: the mark's room reaches down into the name's box
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Near', 430, 187, 6)])).Mimosa).toBe('left');
    // 17 photo px above is 25.5 px on screen: its room ends above the name
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Clear', 430, 183, 6)])).Mimosa).toBe('right');
  });

  it('is not drawn when marks sit on both sides of it', () => {
    const session = sessionOf([at('Mimosa', 400, 200, 1.2), at('Right', 430, 200, 6), at('Left', 370, 200, 6)]);
    expect(names(session).Mimosa).toBeNull();
    expect(revealMarks(session, VIEW.w, VIEW.h)).toHaveLength(3);
  });

  it("is not drawn when the other side would leave the photo", () => {
    // 20 photo px (30 on screen) from the photo's left edge: flipped left, the name would start before the edge
    const session = sessionOf([at('Mimosa', 20, 200, 1.2), at('Blocker', 50, 200, 6)]);
    expect(names(session).Mimosa).toBeNull();
  });

  it('goes left near the right edge as before, and is not drawn when a mark is there too', () => {
    expect(names(sessionOf([at('Aldebaran', 930, 72, 0.9)])).Aldebaran).toBe('left');
    expect(names(sessionOf([at('Aldebaran', 930, 72, 0.9), at('Blocker', 900, 72, 6)])).Aldebaran).toBeNull();
  });

  it('lets the picked star keep its name among other marks, on the side its place gives it', () => {
    const mimosa = at('Mimosa', 400, 200, 1.2);
    const session = sessionOf([mimosa, at('Right', 430, 200, 6), at('Left', 370, 200, 6)], mimosa.id);
    expect(names(session).Mimosa).toBe('right');
  });

  it('measures against the other marks only, never against its own', () => {
    expect(names(sessionOf([at('Alone', 400, 200, 1.2)])).Alone).toBe('right');
  });
});

// namebox-1 (UIUX-solvewords-READY.md item 4a, D20): non-picked Moon, planet and star names are placed brightest first
// (picked first; Moon and planets without apparentMagnitude ahead of stars, else ascending apparentMagnitude) and each
// placed nameBox joins taken so fainter names flip side or yield instead of colliding.
describe('a name yields to a brighter object or picked name (namebox-1, D20)', () => {
  it('places the brighter star name first even when the fainter star comes earlier in findings, flipping or dropping the fainter name', () => {
    // At x 400, y 200 and y 208 (same x, 12 screen px apart vertically): neither mark's 14 px room (x 586.75..614.75)
    // touches either right-side nameBox (x >= 616.75), while their right-side nameBoxes overlap vertically by 4.9 px
    const denebola = at('Denebola', 400, 200, 2.14);
    const regulus = at('Regulus', 400, 208, 1.36);
    const flipped = sessionOf([denebola, regulus]);
    expect(names(flipped)).toEqual({ Denebola: 'left', Regulus: 'right' });
    expect(revealMarks(flipped, VIEW.w, VIEW.h).map((m) => m.finding.title)).toEqual(['Denebola', 'Regulus']);

    // With a mark blocking Denebola's left side too, Denebola yields (null) while Regulus keeps right
    const blockedLeft = sessionOf([denebola, regulus, at('Blocker', 370, 200, 6)]);
    expect(names(blockedLeft)).toEqual({ Denebola: null, Regulus: 'right', Blocker: null });
  });

  it('places Moon and planets without apparentMagnitude ahead of stars, including negative-magnitude stars, on a pose estimate', () => {
    const poseBase = sample('pose_moon_level_head');
    const moonTemplate = poseBase.findings.find((f) => f.id === 'finding-predicted-moon')!;
    const starTemplate = poseBase.findings.find((f) => f.evidenceClass === 'predicted' && f.catalogRef?.objectType === 'star')!;
    const nominalWcs = poseBase.wcs!;
    const upper = pixelToWorldWCS(400, 200, nominalWcs)!;
    const lower = pixelToWorldWCS(400, 208, nominalWcs)!;

    const denebolaPred: CelestialFinding = {
      ...starTemplate,
      id: 'finding-predicted-hip-57632',
      title: 'Denebola',
      skyPosition: { ...starTemplate.skyPosition!, raDeg: upper.raDeg, decDeg: upper.decDeg },
      catalogRef: { ...starTemplate.catalogRef!, commonName: 'Denebola', catalogSourceId: 'HIP 57632', objectType: 'star', apparentMagnitude: 2.14 },
    };
    const siriusPred: CelestialFinding = {
      ...denebolaPred,
      id: 'finding-predicted-hip-32349',
      title: 'Sirius',
      catalogRef: { ...denebolaPred.catalogRef!, commonName: 'Sirius', catalogSourceId: 'HIP 32349', objectType: 'star', apparentMagnitude: -1.46 },
    };
    const moonPred: CelestialFinding = {
      ...moonTemplate,
      title: 'Moon',
      skyPosition: { ...moonTemplate.skyPosition!, raDeg: lower.raDeg, decDeg: lower.decDeg },
      catalogRef: undefined,
    };
    const planetNoMag: CelestialFinding = {
      ...starTemplate,
      id: 'finding-predicted-jupiter',
      title: 'Jupiter',
      skyPosition: { ...moonTemplate.skyPosition!, raDeg: lower.raDeg, decDeg: lower.decDeg },
      catalogRef: { ...starTemplate.catalogRef!, commonName: 'Jupiter', catalogSourceId: 'JPL 599', objectType: 'planet', apparentMagnitude: undefined },
    };

    const basePoseSession = poseSession(poseBase);
    const withPoseFindings = (findings: CelestialFinding[]): ObservationSession => ({
      ...basePoseSession,
      observationSolution: { ...basePoseSession.observationSolution!, findings } as ObservationSolution,
    });

    const withMoon = withPoseFindings([denebolaPred, moonPred]);
    const moonMarks = Object.fromEntries(revealMarks(withMoon, VIEW.w, VIEW.h).map((m) => [m.finding.title, m.name === null ? null : m.nameOnLeft ? 'left' : 'right']));
    expect(moonMarks['Moon']).toBe('right');
    expect(moonMarks['Denebola']).toBe('left');

    const withPlanetAndSirius = withPoseFindings([siriusPred, planetNoMag]);
    const planetMarks = Object.fromEntries(revealMarks(withPlanetAndSirius, VIEW.w, VIEW.h).map((m) => [m.finding.title, m.name === null ? null : m.nameOnLeft ? 'left' : 'right']));
    expect(planetMarks['Jupiter']).toBe('right');
    expect(planetMarks['Sirius']).toBe('left');
  });

  it('places the picked finding first and adds its nameBox to taken so even a brighter neighbour flips away from it', () => {
    const vega = at('Vega', 400, 200, 0.03);
    const pickedFaint = at('Subtle', 400, 208, 4.8);
    expect(names(sessionOf([vega, pickedFaint], pickedFaint.id))).toEqual({ Vega: 'left', Subtle: 'right' });
  });
});
