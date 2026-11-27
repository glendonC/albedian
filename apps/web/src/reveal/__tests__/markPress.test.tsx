// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { pixelToWorldWCS } from '@albedian/camera-rig';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { DEEP_SKY_CATALOG_URL, forgetDeepSkyTable } from '../deepSky.js';
import { HINT_GAP_PX, MARK_TARGET_PX, hintBoxAt, hintPlace, isPredictedMark, markAtPress, overlapArea, type Box } from '../markPress.js';
import { phoneSlotIn } from '../phoneSlot.js';
import { REVEAL_ARRIVAL_MS, RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// mark-press-nearest-one (FIELD's break on main 927612bd3, FIELD-phone-hinted-star-tap-BREAK-NOTE.md; DECIDED-BY-LEAD 23:54
// on 26 Nov, as Stellarium's selection does it, the object nearest the pointer): where press targets overlap, a press opens
// the mark whose centre is nearest the press; at an equal distance a measured or matched mark before a predicted one; a
// matched star's ring is never hidden under a predicted symbol drawn above it; the marks keep their look and their 44 px
// targets; and the first-use hint stands clear of any other mark's ring by the side its record allows ("beside" the ring:
// its name's side, else the other).

const here = path.dirname(fileURLToPath(import.meta.url));
interface FixtureMark {
  kind: 'catalog' | 'predicted';
  name: string;
  xPx: number;
  yPx: number;
  predicted: boolean;
  deepSky: boolean;
}
const READ = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/rpb01LabPhoneMarks.json'), 'utf8')) as {
  sizes: { window: [number, number]; hint: [number, number, number, number]; marks: Omit<FixtureMark, 'kind'>[] }[];
};
// Each mark's kind as the reveal gives it: predicted for a predicted mark, a deep-sky one among them, else a catalog star
const FIXTURE = { sizes: READ.sizes.map((size) => ({ ...size, marks: size.marks.map((m) => ({ ...m, kind: m.predicted ? ('predicted' as const) : ('catalog' as const) })) })) };
/** What main did: the press lands on the last drawn button whose 44 px square holds it. */
const lastDrawnAt = (marks: readonly FixtureMark[], x: number, y: number) =>
  [...marks].reverse().find((m) => Math.abs(m.xPx - x) <= MARK_TARGET_PX / 2 && Math.abs(m.yPx - y) <= MARK_TARGET_PX / 2) ?? null;

describe('mark-press-nearest-one: a press opens the nearest mark whose target holds it', () => {
  it('keeps the 44 px target', () => {
    expect(MARK_TARGET_PX).toBe(44);
  });

  it("opens Hadar at Hadar's centre at the phone's three sizes on the Southern Cross photo, where main opened NGC 5316", () => {
    expect(FIXTURE.sizes.map((s) => s.window)).toEqual([
      [402, 714],
      [375, 667],
      [390, 844],
    ]);
    for (const size of FIXTURE.sizes) {
      const hadar = size.marks.find((m) => m.name === 'Hadar')!;
      const ngc = size.marks.find((m) => m.name === 'NGC 5316')!;
      expect(ngc.predicted && ngc.deepSky, size.window.join('x')).toBe(true);
      expect(lastDrawnAt(size.marks, hadar.xPx, hadar.yPx)?.name, size.window.join('x')).toBe('NGC 5316');
      expect(markAtPress(size.marks, hadar)?.name, size.window.join('x')).toBe('Hadar');
    }
  });

  it("puts Hadar's name inside NGC 5316's square, nearer the cluster, at 402 by 714: so a name pressed must open its own mark", () => {
    const names = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/rpb01LabPhoneMarkNames.json'), 'utf8')) as { sizes: { window: [number, number]; marks: { name: string; xPx: number; yPx: number; label: [number, number, number, number] | null }[] }[] };
    const size = names.sizes.find((z) => z.window[0] === 402)!;
    const hadar = size.marks.find((m) => m.name === 'Hadar')!;
    const ngc = size.marks.find((m) => m.name === 'NGC 5316')!;
    const press = { xPx: (hadar.label![0] + hadar.label![2]) / 2, yPx: (hadar.label![1] + hadar.label![3]) / 2 };
    // The squares alone would open the cluster at the middle of Hadar's own name (main, before version two)
    expect(markAtPress(size.marks.map((m) => ({ ...m, kind: m.name.startsWith('NGC') ? 'predicted' : 'catalog' })), press)?.name).toBe('NGC 5316');
    expect(Math.abs(press.xPx - ngc.xPx)).toBeLessThanOrEqual(22);
  });

  it('opens every mark pressed at its own centre, at all three sizes', () => {
    for (const size of FIXTURE.sizes) {
      for (const mark of size.marks) {
        const twins = size.marks.filter((m) => m.xPx === mark.xPx && m.yPx === mark.yPx);
        const expected = twins.find((m) => !m.predicted) ?? twins[0]!;
        expect(markAtPress(size.marks, mark)?.name, `${size.window.join('x')} ${mark.name}`).toBe(expected.name);
      }
    }
  });

  it('chooses by distance, then a measured or matched mark before a predicted one, whatever the drawing order', () => {
    const star = { kind: 'catalog', name: 'star', xPx: 100, yPx: 100, predicted: false, deepSky: false };
    const cluster = { kind: 'predicted', name: 'cluster', xPx: 120, yPx: 100, predicted: true, deepSky: true };
    expect(markAtPress([star, cluster], { xPx: 109, yPx: 100 })?.name).toBe('star');
    expect(markAtPress([star, cluster], { xPx: 111, yPx: 100 })?.name).toBe('cluster');
    expect(markAtPress([star, cluster], { xPx: 110, yPx: 100 })?.name).toBe('star');
    expect(markAtPress([cluster, star], { xPx: 110, yPx: 100 })?.name).toBe('star');
    // Two of one kind at an equal distance: the one drawn first
    const other = { ...star, name: 'other', xPx: 120 };
    expect(markAtPress([star, other], { xPx: 110, yPx: 100 })?.name).toBe('star');
  });

  it("reads a reveal mark's kind: predicted last, a matched star first", () => {
    expect(isPredictedMark({ kind: 'predicted' })).toBe(true);
    expect(isPredictedMark({ kind: 'catalog' })).toBe(false);
  });

  it('counts only targets that hold the press: inside the 44 px square, its edge included', () => {
    const star = { kind: 'catalog', name: 'star', xPx: 100, yPx: 100, predicted: false, deepSky: false };
    expect(markAtPress([star], { xPx: 122, yPx: 122 })?.name).toBe('star');
    expect(markAtPress([star], { xPx: 122.5, yPx: 100 })).toBeNull();
    expect(markAtPress([star], { xPx: 100, yPx: 77.5 })).toBeNull();
    expect(markAtPress([], { xPx: 100, yPx: 100 })).toBeNull();
  });
});

// hint-place-one (UIUX-first-use-wow-READY.md, its addition on WEB-UX's QUESTION, DECIDED-BY-LEAD 00:44 on 27 Nov; Floating
// UI's flip with its best fit): the hint tries its name's side, the other side, centred under the ring, centred over it;
// the first whose box meets no other mark (its glyph and its name) inside the photo wins; else the least covering place,
// the earlier on a tie, never over the hinted star's own ring or name
interface NamedMark extends FixtureMark {
  label: [number, number, number, number] | null;
}
const NAMES = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/rpb01LabPhoneMarkNames.json'), 'utf8')) as {
  sizes: { window: [number, number]; hint: [number, number]; marks: Omit<NamedMark, 'kind'>[] }[];
};
const glyphBox = (m: { xPx: number; yPx: number; predicted: boolean; deepSky: boolean }): Box => {
  const r = m.deepSky ? 6 : m.predicted ? 7 : 9;
  return { x0: m.xPx - r, y0: m.yPx - r, x1: m.xPx + r, y1: m.yPx + r };
};
const labelBox = (m: { label: [number, number, number, number] | null }): Box[] => (m.label ? [{ x0: m.label[0], y0: m.label[1], x1: m.label[2], y1: m.label[3] }] : []);

describe('hint-place-one: the first hint stands at the first clear place of its record, else where it covers least', () => {
  it('keeps the 14 px reach, and centres the hint under and over its ring', () => {
    expect(HINT_GAP_PX).toBe(14);
    const size = { widthPx: 140, heightPx: 17 };
    expect(hintBoxAt({ xPx: 200, yPx: 300 }, 'right', size)).toEqual({ x0: 214, y0: 314, x1: 354, y1: 331 });
    expect(hintBoxAt({ xPx: 200, yPx: 300 }, 'left', size)).toEqual({ x0: 46, y0: 314, x1: 186, y1: 331 });
    expect(hintBoxAt({ xPx: 200, yPx: 300 }, 'under', size)).toEqual({ x0: 130, y0: 314, x1: 270, y1: 331 });
    expect(hintBoxAt({ xPx: 200, yPx: 300 }, 'over', size)).toEqual({ x0: 130, y0: 269, x1: 270, y1: 286 });
  });

  it('counts boxes that only touch as clear, and the shared area otherwise', () => {
    expect(overlapArea({ x0: 0, y0: 0, x1: 10, y1: 10 }, { x0: 10, y0: 0, x1: 20, y1: 10 })).toBe(0);
    expect(overlapArea({ x0: 0, y0: 0, x1: 10, y1: 10 }, { x0: 5, y0: 6, x1: 20, y1: 20 })).toBe(20);
  });

  it('tries the name side, the other side, under, then over: the first clear place inside the photo wins', () => {
    const size = { widthPx: 140, heightPx: 17 };
    const at = { xPx: 200, yPx: 300 };
    const photo = { x0: 0, y0: 0, x1: 400, y1: 600 };
    const box = (place: 'right' | 'left' | 'under' | 'over') => hintBoxAt(at, place, size);
    expect(hintPlace(at, false, size, [], [], photo)).toBe('right');
    expect(hintPlace(at, true, size, [], [], photo)).toBe('left');
    expect(hintPlace(at, false, size, [box('right')], [], photo)).toBe('left');
    expect(hintPlace(at, true, size, [box('left')], [], photo)).toBe('right');
    // Under overlaps both sides' boxes, so a mark there blocks them all three
    const middleLow = { x0: 180, y0: 318, x1: 220, y1: 328 };
    expect(hintPlace(at, false, size, [box('right'), box('left'), middleLow], [], photo)).toBe('over');
    expect(hintPlace(at, false, size, [{ x0: 300, y0: 320, x1: 310, y1: 325 }, { x0: 60, y0: 320, x1: 70, y1: 325 }], [], photo)).toBe('under');
    // A place that leaves the photo is not clear, whatever it meets
    expect(hintPlace(at, false, size, [], [], { x0: 0, y0: 0, x1: 350, y1: 600 })).toBe('left');
    expect(hintPlace(at, false, size, [], [], { x0: 100, y0: 0, x1: 350, y1: 600 })).toBe('under');
    expect(hintPlace(at, false, size, [], [], { x0: 100, y0: 0, x1: 350, y1: 320 })).toBe('over');
    // Over leaves the photo's top and both sides leave its sides: with under blocked, the best fit among the places inside
    // the photo is under itself (version two: never a place past the photo)
    expect(hintPlace(at, false, size, [{ x0: 200, y0: 320, x1: 210, y1: 325 }], [], { x0: 100, y0: 280, x1: 350, y1: 600 })).toBe('under');
  });

  it('takes the least covering place where none is clear, the earlier on a tie, never over the hinted star itself', () => {
    const size = { widthPx: 140, heightPx: 17 };
    const at = { xPx: 200, yPx: 300 };
    const photo = { x0: 0, y0: 0, x1: 400, y1: 600 };
    // Right covers 100, left 20, under 120 (both), over 50
    const marks = [{ x0: 260, y0: 316, x1: 270, y1: 326 }, { x0: 100, y0: 316, x1: 102, y1: 326 }, { x0: 190, y0: 275, x1: 200, y1: 280 }];
    expect(hintPlace(at, false, size, marks, [], photo)).toBe('left');
    // Left over the hinted star's own name: the next least, over
    expect(hintPlace(at, false, size, marks, [{ x0: 90, y0: 310, x1: 110, y1: 320 }], photo)).toBe('over');
    // A tie between right and left (100 each): the earlier, its name's side
    const even = [{ x0: 260, y0: 316, x1: 270, y1: 326 }, { x0: 100, y0: 316, x1: 110, y1: 326 }, { x0: 190, y0: 270, x1: 220, y1: 285 }, { x0: 195, y0: 320, x1: 205, y1: 330 }];
    expect(hintPlace(at, false, size, even, [], photo)).toBe('right');
    expect(hintPlace(at, true, size, even, [], photo)).toBe('left');
    // Every place over the hinted star: its name's side
    expect(hintPlace(at, true, size, marks, [{ x0: 0, y0: 260, x1: 400, y1: 340 }], photo)).toBe('left');
  });

  it('picks its best fit only among places inside the photo as shown in the window (version two, AUDIT-C D5)', () => {
    const size = { widthPx: 140, heightPx: 17 };
    const at = { xPx: 60, yPx: 300 };
    // Every place meets a mark; the left covers least but leaves the window (its box from -94): the next least inside wins
    const marks = [{ x0: 75, y0: 316, x1: 200, y1: 330 }, { x0: -50, y0: 318, x1: -40, y1: 320 }, { x0: 0, y0: 270, x1: 120, y1: 285 }];
    expect(hintPlace(at, false, size, marks, [], { x0: 0, y0: 0, x1: 400, y1: 600 })).toBe('right');
    // With no place inside at all, its name's side
    expect(hintPlace(at, false, size, marks, [], { x0: 0, y0: 0, x1: 100, y1: 600 })).toBe('right');
    expect(hintPlace(at, true, size, marks, [], { x0: 0, y0: 0, x1: 100, y1: 600 })).toBe('left');
  });

  it("keeps the hint whole inside the window on rp-b-12 at the two narrowest phone sizes (AUDIT-C's case)", () => {
    const TWELVE = JSON.parse(fs.readFileSync(path.join(here, 'fixtures/rpb12PhoneMarkNames.json'), 'utf8')) as typeof NAMES;
    for (const size of TWELVE.sizes) {
      const where = size.window.join('x');
      const named = size.marks.filter((m) => m.label && !m.predicted && !m.deepSky);
      expect(named.length, where).toBe(1);
      const star = named[0]!;
      const others = size.marks.filter((m) => m !== star).flatMap((m) => [glyphBox(m), ...labelBox(m)]);
      const own = [glyphBox(star), ...labelBox(star)];
      const slot = phoneSlotIn(size.window[1], size.window[0]);
      const photo = { x0: 0, y0: slot.topPx, x1: size.window[0], y1: slot.topPx + slot.heightPx };
      const hint = { widthPx: size.hint[0], heightPx: size.hint[1] };
      const place = hintPlace(star, false, hint, others, own, photo);
      const box = hintBoxAt(star, place, hint);
      expect(box.x0, `${where} ${place}`).toBeGreaterThanOrEqual(0);
      expect(box.x1, `${where} ${place}`).toBeLessThanOrEqual(size.window[0]);
      // Version one's best fit, held to no bound, stood past the window's left edge here
      const cover = (p: 'right' | 'left' | 'under' | 'over') => others.reduce((sum, o) => sum + overlapArea(hintBoxAt(star, p, hint), o), 0);
      const unbounded = (['right', 'left', 'under', 'over'] as const).reduce((best, p) => (cover(p) < cover(best) ? p : best));
      expect(hintBoxAt(star, unbounded, hint).x0, where).toBeLessThan(0);
    }
  });

  it('stands beside Hadar on its other side at the three phone sizes of the Southern Cross photo, by the record', () => {
    const expected: Record<string, 'left'> = { '402x714': 'left', '375x667': 'left', '390x844': 'left' };
    for (const size of NAMES.sizes) {
      const where = size.window.join('x');
      const hadar = size.marks.find((m) => m.name === 'Hadar')!;
      const others = size.marks.filter((m) => m !== hadar).flatMap((m) => [glyphBox(m), ...labelBox(m)]);
      const own = [glyphBox(hadar), ...labelBox(hadar)];
      const slot = phoneSlotIn(size.window[1], size.window[0]);
      const photo = { x0: 0, y0: slot.topPx, x1: size.window[0], y1: slot.topPx + slot.heightPx };
      const hint = { widthPx: size.hint[0], heightPx: size.hint[1] };
      expect(hadar.label, where).not.toBeNull();
      expect(hintPlace(hadar, false, hint, others, own, photo), where).toBe(expected[where]);
      const cover = (place: 'right' | 'left' | 'under' | 'over') => others.reduce((sum, o) => sum + overlapArea(hintBoxAt(hadar, place, hint), o), 0);
      // Its name's side covers a ring at every size
      expect(cover('right'), where).toBeGreaterThan(0);
      if (where === '375x667') {
        // No place is clear there (Zeta Centauri's tick reaches the left box's corner): the left covers least
        for (const place of ['left', 'under', 'over'] as const) expect(cover(place), `${where} ${place}`).toBeGreaterThan(0);
        expect(cover('left'), where).toBeLessThan(Math.min(cover('right'), cover('under'), cover('over')));
      } else {
        expect(cover('left'), where).toBe(0);
      }
    }
  });
});

// The rule in the reveal itself, on the calibrated fixture (960 by 540 drawn at 1.5 screen pixels a photo pixel from y 45):
// a matched star and a deep-sky cluster drawn 21 px apart, as FIELD found them 25 px apart on the phone
const VIEW = { w: 1440, h: 900 };
const base = sample('plate_calibrated');
const template = base.findings.find((f) => f.title === 'Betelgeuse')!;
const FILE = {
  format: 'albedian.deep-sky-catalog',
  formatVersion: 1,
  release: 'OpenNGC v20260501',
  columns: ['name', 'type', 'raDeg', 'decDeg', 'constellation', 'bMag', 'vMag', 'commonNames'],
  rows: [['NGC5460', 'OCl', 211.9, -48.3, 'Cen', 5.6, null, []]],
};
function sessionWithStarAndCluster(neighbour = false, withCluster = true, bothSides = false, farNamed = false): ObservationSession {
  const sky = pixelToWorldWCS(310, 190, base.wcs!);
  const star = {
    ...template,
    id: 'finding-hadar',
    title: 'Hadar',
    sourceDetection: { ...template.sourceDetection!, xPx: 300, yPx: 200 },
    catalogRef: { ...template.catalogRef!, commonName: 'Hadar', catalogSourceId: 'HIP 68702', apparentMagnitude: 0.6 },
  } as CelestialFinding;
  const cluster = {
    ...template,
    id: 'finding-openngc-NGC5460',
    title: 'NGC 5460',
    evidenceClass: 'predicted',
    sourceDetection: undefined,
    inferredDistancePc: undefined,
    skyPosition: { ...template.skyPosition!, raDeg: sky.raDeg, decDeg: sky.decDeg },
    catalogRef: {
      id: 'openngc-NGC5460',
      provider: 'OpenNGC',
      releaseVersion: 'OpenNGC v20260501',
      catalogSourceId: 'NGC5460',
      objectType: 'deep_sky',
      coordinateEpoch: 'J2000.0',
      raDeg: sky.raDeg,
      decDeg: sky.decDeg,
    },
    evidence: { ...template.evidence!, evidenceClass: 'predicted' },
  } as CelestialFinding;
  const session = poseSession(base);
  // A fainter matched star where the hint's box stands on Hadar's name side (right of and under the ring)
  const near = {
    ...template,
    id: 'finding-epsilon',
    title: 'Epsilon Centauri',
    sourceDetection: { ...template.sourceDetection!, xPx: 346, yPx: 215 },
    catalogRef: { ...template.catalogRef!, commonName: 'Epsilon Centauri', catalogSourceId: 'HIP 66657', apparentMagnitude: 2.3 },
  } as CelestialFinding;
  // Two fainter stars, one in each side's box and clear of the box under Hadar (hint-place-one)
  const flank = (id: string, title: string, u: number) =>
    ({ ...template, id, title, sourceDetection: { ...template.sourceDetection!, xPx: u, yPx: 215 }, catalogRef: { ...template.catalogRef!, commonName: title, catalogSourceId: `HIP ${u}`, apparentMagnitude: 3.5 } }) as CelestialFinding;
  const flanks = bothSides ? [flank('finding-flank-right', 'Flank right', 373), flank('finding-flank-left', 'Flank left', 219)] : [];
  // A bright named star far from Hadar, whose name a test lays over Hadar's name side
  const far = farNamed
    ? [{ ...template, id: 'finding-far', title: 'Far star', sourceDetection: { ...template.sourceDetection!, xPx: 80, yPx: 450 }, catalogRef: { ...template.catalogRef!, commonName: 'Far star', catalogSourceId: 'HIP 1', apparentMagnitude: 1.0 } } as CelestialFinding]
    : [];
  const findings = [star, ...(withCluster ? [cluster] : []), ...(neighbour ? [near] : []), ...flanks, ...far];
  return { ...session, observationSolution: { ...session.observationSolution!, findings } as ObservationSolution, selectedFindingId: null };
}

describe('mark-press-nearest-one: in the reveal', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    forgetDeepSkyTable();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === DEEP_SKY_CATALOG_URL ? new Response(JSON.stringify(FILE)) : new Response('', { status: 404 }))));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  async function show(onSelectFinding: (id: string, at?: { xPx: number; yPx: number }) => void, neighbour = false) {
    await act(async () => {
      root.render(createElement(RevealPhoto, { session: sessionWithStarAndCluster(neighbour), onSelectFinding, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  const at = (el: Element) => ({ xPx: parseFloat((el as HTMLElement).style.left), yPx: parseFloat((el as HTMLElement).style.top) });

  it("opens the mark whose own name is pressed, whatever square holds the press (AUDIT-C's D4, version two)", async () => {
    const picked: string[] = [];
    await show((id) => picked.push(id));
    const star = container.querySelector('[data-testid="reveal-mark-finding-hadar"]')!;
    const name = star.querySelector('.reveal-mark__name')!;
    expect(name).not.toBeNull();
    const s = at(star);
    const c = at(container.querySelector('[data-testid="reveal-mark-finding-openngc-NGC5460"]')!);
    // A point on Hadar's name, 19 px right of its ring, that lies in both squares and nearer the cluster's centre
    const press = { xPx: s.xPx + 21.25, yPx: s.yPx - 5.75 };
    expect(Math.hypot(press.xPx - c.xPx, press.yPx - c.yPx)).toBeLessThan(Math.hypot(press.xPx - s.xPx, press.yPx - s.yPx));
    // Its name laid out where the press lands (jsdom lays nothing out): the press opens Hadar, whichever button it lands on
    const rect = HTMLElement.prototype.getBoundingClientRect;
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      return this === name ? ({ left: press.xPx - 4, top: press.yPx - 6, right: press.xPx + 8, bottom: press.yPx + 6, width: 12, height: 12, x: press.xPx - 4, y: press.yPx - 6, toJSON() {} } as DOMRect) : rect.call(this);
    };
    try {
      act(() => void name.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: press.xPx, clientY: press.yPx })));
      // Another mark's square drawn over the name takes the press first in the browser: the name still opens its own mark
      const cluster = container.querySelector('[data-testid="reveal-mark-finding-openngc-NGC5460"]')!;
      act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: press.xPx, clientY: press.yPx })));
      // Just past the name's trailing edge, in the cluster's square only: the squares decide, the cluster
      const past = { xPx: s.xPx + 33, yPx: press.yPx };
      expect(Math.abs(past.xPx - c.xPx)).toBeLessThanOrEqual(22);
      act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: past.xPx, clientY: past.yPx })));
      // A key press on the focused cluster carries no pointer: the cluster, though its point lies on Hadar's name
      act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0, clientX: press.xPx, clientY: press.yPx })));
    } finally {
      HTMLElement.prototype.getBoundingClientRect = rect;
    }
    expect(picked).toEqual(['finding-hadar', 'finding-hadar', 'finding-openngc-NGC5460', 'finding-openngc-NGC5460']);
  });

  it("opens the star when the press lands on the cluster's target at the star's centre; a key press opens the mark it is on", async () => {
    const picked: string[] = [];
    await show((id) => picked.push(id));
    const star = container.querySelector('[data-testid="reveal-mark-finding-hadar"]')!;
    const cluster = container.querySelector('[data-testid="reveal-mark-finding-openngc-NGC5460"]')!;
    const s = at(star);
    const c = at(cluster);
    // The cluster's 44 px square holds the star's centre; the star is drawn after the cluster, so it stands over it; a
    // press landing on the cluster's target there still opens the star
    expect(Math.abs(c.xPx - s.xPx)).toBeLessThanOrEqual(22);
    expect(Math.abs(c.yPx - s.yPx)).toBeLessThanOrEqual(22);
    expect(cluster.compareDocumentPosition(star) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The layer stands at the window's corner in jsdom, so a client point is a layer point
    act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: s.xPx, clientY: s.yPx })));
    expect(picked).toEqual(['finding-hadar']);
    act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1, clientX: c.xPx, clientY: c.yPx })));
    expect(picked).toEqual(['finding-hadar', 'finding-openngc-NGC5460']);
    // Enter or Space on a focused mark comes as a click with no pointer (detail 0), whatever point it carries: that mark
    act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0, clientX: 0, clientY: 0 })));
    act(() => void cluster.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 0, clientX: s.xPx, clientY: s.yPx })));
    expect(picked).toEqual(['finding-hadar', 'finding-openngc-NGC5460', 'finding-openngc-NGC5460', 'finding-openngc-NGC5460']);
  });
});

describe('mark-press-nearest-one: the hint in the reveal', () => {
  let container: HTMLDivElement;
  let root: Root;
  const sizes = { offsetWidth: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth'), offsetHeight: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight') };
  beforeEach(() => {
    forgetDeepSkyTable();
    window.localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === DEEP_SKY_CATALOG_URL ? new Response(JSON.stringify(FILE)) : new Response('', { status: 404 }))));
    // The hint's words as Chrome lays them out at 14 px (141 by 17, measured live on the phone)
    for (const [prop, px] of [['offsetWidth', 141], ['offsetHeight', 17]] as const) {
      Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get(this: HTMLElement) { return this.getAttribute('data-testid') === 'reveal-hint' ? px : 0; } });
    }
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    for (const [prop, d] of Object.entries(sizes)) if (d) Object.defineProperty(HTMLElement.prototype, prop, d);
  });
  async function arrive(neighbour: boolean, bothSides = false, farNamed = false) {
    await act(async () => {
      root.render(createElement(RevealPhoto, { session: sessionWithStarAndCluster(neighbour, false, bothSides, farNamed), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, REVEAL_ARRIVAL_MS + 50));
    });
  }

  it("stands on Hadar's name side when it is clear, and on the other side when a ring lies there", async () => {
    await arrive(false);
    expect(container.querySelector('[data-testid="reveal-mark-finding-hadar"]')?.classList.contains('is-left')).toBe(false);
    expect(container.querySelector('[data-testid="reveal-hint"]')?.className).toBe('reveal-hint');
    act(() => root.unmount());
    root = createRoot(container);
    await arrive(true);
    const hint = container.querySelector('[data-testid="reveal-hint"]')!;
    const hadar = container.querySelector('[data-testid="reveal-mark-finding-hadar"]') as HTMLElement;
    expect([(hint as HTMLElement).style.left, (hint as HTMLElement).style.top]).toEqual([hadar.style.left, hadar.style.top]);
    expect(hint.className).toBe('reveal-hint is-left');
  });

  it('stands under Hadar, centred, when each side holds a ring and the place under it is clear (hint-place-one)', async () => {
    await arrive(false, true);
    expect(container.querySelector('[data-testid="reveal-hint"]')?.className).toBe('reveal-hint is-under');
  });

  it("counts another mark's name as the mark: a name laid over Hadar's name side moves the hint across (hint-place-one)", async () => {
    const rect = HTMLElement.prototype.getBoundingClientRect;
    // The far star's name, as if laid out across the box on Hadar's name side (right of and under its ring)
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      return this.classList.contains('reveal-mark__name') && this.closest('[data-testid="reveal-mark-finding-far"]')
        ? ({ left: 480, top: 362, right: 560, bottom: 375, width: 80, height: 13, x: 480, y: 362, toJSON() {} } as DOMRect)
        : rect.call(this);
    };
    try {
      await arrive(false, false, true);
      expect(container.querySelector('[data-testid="reveal-mark-finding-far"] .reveal-mark__name')).not.toBeNull();
      expect(container.querySelector('[data-testid="reveal-hint"]')?.className).toBe('reveal-hint is-left');
    } finally {
      HTMLElement.prototype.getBoundingClientRect = rect;
    }
  });

  it('draws the hint centred under or over its ring at the same 14 px reach (hint-place-one)', () => {
    const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(css).walkRules((rule) => {
      if (rule.parent?.type !== 'root' || !['.reveal-hint.is-under', '.reveal-hint.is-over'].includes(rule.selector)) return;
      rule.walkDecls((d) => void ((rules[rule.selector] ??= {})[d.prop] = d.value));
    });
    expect(rules).toEqual({
      '.reveal-hint.is-under': { translate: '-50% 14px', 'text-align': 'center' },
      '.reveal-hint.is-over': { translate: '-50% calc(-100% - 14px)', 'text-align': 'center' },
    });
  });
});

describe('mark-press-nearest-one: a matched star is drawn above a predicted symbol', () => {
  it('draws the matched marks after the predicted ones, by order alone: no z-index lifts a mark over the compare labels', () => {
    const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
    const lifted: string[] = [];
    postcss.parse(css).walkDecls('z-index', (d) => {
      const sel = (d.parent as postcss.Rule).selector ?? '';
      if (/reveal-mark/.test(sel)) lifted.push(sel);
    });
    expect(lifted).toEqual([]);
  });
});
