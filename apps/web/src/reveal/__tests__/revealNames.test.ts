import { describe, expect, it } from 'vitest';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { NAME_MAGNITUDE_LIMIT, revealMarks } from '../revealMarks.js';

// Which marks get a name on a crowded photo: the design lane's rule for the calibrated sample (UIUX-sample-b01-NOTE.md
// point 2, drawn by .cache/uiux/lib/sky.mjs), built as an interim for the owner's review. Every matched star is
// marked; a name is drawn only for a named star brighter than magnitude 3.2, and since names-brightest-first-one (N1,
// N2) it gives way only to a brighter star's name, the photo's edge or a kept place, standing over fainter rings.

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

  // names-brightest-first-one (N1, N2): a fainter mark's ring no longer removes a brighter star's name; it stays drawn
  // under the name, which takes its first side when no side clears the rings
  it('stands on its first side over the rings when marks sit on both sides of it, every mark still drawn', () => {
    const session = sessionOf([at('Mimosa', 400, 200, 1.2), at('Right', 430, 200, 6), at('Left', 370, 200, 6)]);
    expect(names(session).Mimosa).toBe('right');
    expect(revealMarks(session, VIEW.w, VIEW.h)).toHaveLength(3);
  });

  it('stays on its side over a ring when the other side would leave the photo', () => {
    // 20 photo px (30 on screen) from the photo's left edge: flipped left, the name would start before the edge
    const session = sessionOf([at('Mimosa', 20, 200, 1.2), at('Blocker', 50, 200, 6)]);
    expect(names(session).Mimosa).toBe('right');
  });

  it('goes left near the right edge as before, and stays there over a mark on that side', () => {
    expect(names(sessionOf([at('Aldebaran', 930, 72, 0.9)])).Aldebaran).toBe('left');
    expect(names(sessionOf([at('Aldebaran', 930, 72, 0.9), at('Blocker', 900, 72, 6)])).Aldebaran).toBe('left');
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
  it('places the brighter star name first even when the fainter star comes earlier in findings, flipping the fainter name', () => {
    // At x 400, y 200 and y 205 (same x, 7.5 screen px apart vertically): neither mark's 14 px room (x 586.75..614.75)
    // touches either right-side nameBox (x >= 616.75), while their right-side nameBoxes overlap vertically by 9.4 px,
    // past the 8 px a name may overlap an earlier one (names-brightest-first-one, N1)
    const denebola = at('Denebola', 400, 200, 2.14);
    const regulus = at('Regulus', 400, 205, 1.36);
    const flipped = sessionOf([denebola, regulus]);
    expect(names(flipped)).toEqual({ Denebola: 'left', Regulus: 'right' });
    expect(revealMarks(flipped, VIEW.w, VIEW.h).map((m) => m.finding.title)).toEqual(['Denebola', 'Regulus']);

    // A fainter mark's ring on Denebola's left no longer makes it yield: it stands there over the ring (N1)
    const ringLeft = sessionOf([denebola, regulus, at('Blocker', 370, 200, 6)]);
    expect(names(ringLeft)).toEqual({ Denebola: 'left', Regulus: 'right', Blocker: null });
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
    // 9 screen px apart: the picked 15 px name and Vega's 13 px name overlap 9.5 px vertically, past the 8 px (N1)
    const vega = at('Vega', 400, 200, 0.03);
    const pickedFaint = at('Subtle', 400, 206, 4.8);
    expect(names(sessionOf([vega, pickedFaint], pickedFaint.id))).toEqual({ Vega: 'left', Subtle: 'right' });
  });
});

// names-brightest-first-one (UIUX's N1 to N3, .lead/kiro/reports/UIUX/first-minute-phone-walk.md, DECIDED-BY-LEAD at
// the line stamped 13:09 on 27 Nov), from Stellarium Web Engine's labels.c: a label is tested only against labels placed
// before it, and shows while its overlap with each, the smaller of the overlap's width and height, is at most 8 px.
// N1: a matched star's name gives way only to an earlier (brighter) name past 8 px, the photo's edge or a kept place.
// N2: of the sides left, the one whose name covers no drawn ring; else the first. N3: the press is unchanged (D4).
describe('a star\'s name gives way only to a brighter star\'s name (names-brightest-first-one, N1 and N2)', () => {
  // Regulus at (400, 200) names to the right at screen y 345.75; its box runs 10.4 px above and 6.5 below that line
  const regulus = at('Regulus', 400, 200, 1.36);

  it('gives way to a brighter name only past 8 px of overlap: 8.5 px flips it, 7.9 px leaves it on its side', () => {
    // 5.6 photo px below is 8.4 screen px: the right boxes overlap 16.9 - 8.4 = 8.5 px in height
    expect(names(sessionOf([regulus, at('Denebola', 400, 205.6, 2.14)]))).toEqual({ Regulus: 'right', Denebola: 'left' });
    // 6 photo px below is 9 screen px: they overlap 7.9 px, at most 8, so both stand on the right
    expect(names(sessionOf([regulus, at('Denebola', 400, 206, 2.14)]))).toEqual({ Regulus: 'right', Denebola: 'right' });
  });

  it('measures the overlap as its smaller side: names on one line give way only when they cross by more than 8 px', () => {
    // On one line the height overlap is the whole 16.9 px, so the width decides. Denebola left of Regulus: its right box
    // ends 5 px into Regulus's (which starts at x 616.75), so it stands; 9 px in, it goes left. A fainter ring on its
    // left and Regulus's ring on its right keep N2 from choosing by rings
    const line = (xPx: number) => names(sessionOf([regulus, at('Denebola', xPx, 200, 2.14), at('Blocker', 332.83, 200, 6)])).Denebola;
    expect(line(360.51)).toBe('right');
    expect(line(363.17)).toBe('left');
  });

  it('is left out when brighter names take both its sides, its ring still drawn', () => {
    // Spica (brighter) at screen x 514.75 names to the right over x 530.75..573.15 on Denebola's line; Regulus takes
    // Denebola's right 9.4 px over; Denebola's left box (x 520.51..584.75) meets Spica's name across its whole height
    const spica = at('Spica', 342.67, 205, 0.97);
    const denebola = at('Denebola', 400, 205, 2.14);
    const session = sessionOf([denebola, regulus, spica]);
    expect(names(session)).toEqual({ Denebola: null, Regulus: 'right', Spica: 'right' });
    expect(revealMarks(session, VIEW.w, VIEW.h)).toHaveLength(3);
  });

  it('takes the side clear of rings when both sides are free of names (N2, today\'s flip)', () => {
    // A fainter mark 30 photo px right sits in the right box; the left is clear of rings
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Blocker', 430, 200, 6)])).Mimosa).toBe('left');
    // A fainter mark on each side: no side clears the rings, so the first
    expect(names(sessionOf([at('Mimosa', 400, 200, 1.2), at('Right', 430, 200, 6), at('Left', 370, 200, 6)])).Mimosa).toBe('right');
  });

  it('never stands over a kept place, as today: a place over the right sends it left, one over both sides leaves it out', () => {
    // Mimosa at screen (600.75, 345.75): its right box runs x 616.75..666.43, its left x 535.07..584.75, y 335.35..352.25
    const mimosa = at('Mimosa', 400, 200, 1.2);
    const overRight = { x0: 630, x1: 700, y0: 330, y1: 360 };
    const overLeft = { x0: 540, x1: 570, y0: 330, y1: 360 };
    const keep = (clear: { x0: number; x1: number; y0: number; y1: number }[]) =>
      revealMarks(sessionOf([mimosa]), VIEW.w, VIEW.h, { clear }).find((m) => m.finding.id === mimosa.id)!;
    expect(keep([overRight])).toMatchObject({ name: 'Mimosa', nameOnLeft: true });
    expect(keep([overRight, overLeft])).toMatchObject({ name: null });
  });
});

describe('There: the picked star moved to its resting place (arrival-star-1, look gap two, 15:34)', () => {
  it('moves the picked mark alone by the flight\'s move, its name taking its side there by the same rule', () => {
    const edge = at('Edgestar', 940, 200, 1);
    const other = at('Otherstar', 300, 300, 1);
    const session = sessionOf([edge, other], edge.id);
    const still = Object.fromEntries(revealMarks(session, VIEW.w, VIEW.h).map((m) => [m.finding.title, m]));
    // At the photo's right edge the picked name goes to the left
    expect(still.Edgestar!.nameOnLeft).toBe(true);
    const moved = Object.fromEntries(revealMarks(session, VIEW.w, VIEW.h, { pickedShiftPx: [-600, 100] }).map((m) => [m.finding.title, m]));
    expect(moved.Edgestar!.xPx).toBeCloseTo(still.Edgestar!.xPx - 600, 9);
    expect(moved.Edgestar!.yPx).toBeCloseTo(still.Edgestar!.yPx + 100, 9);
    // Mid-frame there is room on the right, so the same rule puts it there
    expect(moved.Edgestar!.nameOnLeft).toBe(false);
    expect([moved.Otherstar!.xPx, moved.Otherstar!.yPx]).toEqual([still.Otherstar!.xPx, still.Otherstar!.yPx]);
    // No move, no change
    expect(revealMarks(session, VIEW.w, VIEW.h, { pickedShiftPx: null })).toEqual(revealMarks(session, VIEW.w, VIEW.h));
  });
});
