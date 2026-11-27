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
import { HINT_GAP_PX, MARK_TARGET_PX, hintSide, isPredictedMark, markAtPress, type HintBox } from '../markPress.js';
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

describe("mark-press-nearest-one: the hint stands clear of every other mark's ring, by the side its record allows", () => {
  const ringsOf = (marks: readonly FixtureMark[], except: string) =>
    marks.filter((m) => m.name !== except).map((m) => ({ xPx: m.xPx, yPx: m.yPx, rPx: m.deepSky ? 6 : m.predicted ? 7 : 9 }));
  const boxAt = (anchor: { xPx: number; yPx: number }, side: 'right' | 'left', w: number, h: number): HintBox =>
    side === 'right'
      ? { x0: anchor.xPx + HINT_GAP_PX, y0: anchor.yPx + HINT_GAP_PX, x1: anchor.xPx + HINT_GAP_PX + w, y1: anchor.yPx + HINT_GAP_PX + h }
      : { x0: anchor.xPx - HINT_GAP_PX - w, y0: anchor.yPx + HINT_GAP_PX, x1: anchor.xPx - HINT_GAP_PX, y1: anchor.yPx + HINT_GAP_PX + h };
  const clears = (box: HintBox, rings: ReturnType<typeof ringsOf>) =>
    rings.every((r) => {
      const dx = Math.max(box.x0 - r.xPx, 0, r.xPx - box.x1);
      const dy = Math.max(box.y0 - r.yPx, 0, r.yPx - box.y1);
      return dx * dx + dy * dy > r.rPx * r.rPx;
    });

  it('keeps the 14 px gap the hint is drawn at', () => {
    expect(HINT_GAP_PX).toBe(14);
  });

  it("moves to Hadar's other side where its own side lies over a matched ring and the other is clear: 402 by 714 and 390 by 844", () => {
    for (const size of FIXTURE.sizes) {
      const hadar = size.marks.find((m) => m.name === 'Hadar')!;
      const [, , w, h] = size.hint;
      const rings = ringsOf(size.marks, 'Hadar');
      const where = size.window.join('x');
      expect(clears(boxAt(hadar, 'right', w, h), rings), where).toBe(false);
      const side = hintSide(hadar, false, { widthPx: w, heightPx: h }, rings, size.window[0]);
      if (where === '375x667') {
        // No free side: Zeta Centauri's tick (its ring 4.5, its ticks to 9) reaches under a pixel into the left box's
        // corner, so the hint keeps its name's side (WEB-UX-mark-press-nearest-one-QUESTION.md asks UIUX)
        expect(clears(boxAt(hadar, 'left', w, h), rings), where).toBe(false);
        expect(side, where).toBe('right');
      } else {
        expect(side, where).toBe('left');
        expect(clears(boxAt(hadar, 'left', w, h), rings), where).toBe(true);
      }
    }
  });

  it("keeps its name's side when that side is clear, and keeps it when neither side is (then it is UIUX's to rule)", () => {
    const anchor = { xPx: 200, yPx: 200 };
    const size = { widthPx: 140, heightPx: 17 };
    expect(hintSide(anchor, false, size, [], 400)).toBe('right');
    expect(hintSide(anchor, true, size, [], 400)).toBe('left');
    const onRight = { xPx: 260, yPx: 222, rPx: 9 };
    const onLeft = { xPx: 140, yPx: 222, rPx: 9 };
    expect(hintSide(anchor, true, size, [onLeft], 400)).toBe('right');
    expect(hintSide(anchor, false, size, [onRight, onLeft], 400)).toBe('right');
    expect(hintSide(anchor, true, size, [onRight, onLeft], 400)).toBe('left');
  });

  it('never takes a side that leaves the window, and reads a ring that only touches the box as clear', () => {
    const size = { widthPx: 140, heightPx: 17 };
    expect(hintSide({ xPx: 100, yPx: 200 }, false, size, [{ xPx: 160, yPx: 222, rPx: 9 }], 400)).toBe('right');
    expect(hintSide({ xPx: 300, yPx: 200 }, true, size, [{ xPx: 240, yPx: 222, rPx: 9 }], 400)).toBe('left');
    // The box's top at 214; a ring of 9 centred 9 above it only touches
    expect(hintSide({ xPx: 200, yPx: 200 }, false, size, [{ xPx: 260, yPx: 205, rPx: 9 }], 400)).toBe('right');
    expect(hintSide({ xPx: 200, yPx: 200 }, false, size, [{ xPx: 260, yPx: 205.5, rPx: 9 }], 400)).toBe('left');
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
function sessionWithStarAndCluster(neighbour = false, withCluster = true): ObservationSession {
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
  const findings = [star, ...(withCluster ? [cluster] : []), ...(neighbour ? [near] : [])];
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
  async function arrive(neighbour: boolean) {
    await act(async () => {
      root.render(createElement(RevealPhoto, { session: sessionWithStarAndCluster(neighbour, false), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
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
