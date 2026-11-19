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
import {
  DEEP_SKY_CATALOG_URL,
  DEEP_SKY_LIMIT,
  decisionMagnitude,
  deepSkyKindWords,
  deepSkySymbol,
  forgetDeepSkyTable,
  hasCommonName,
  parseDeepSkyTable,
  type DeepSkyTable,
} from '../deepSky.js';
import { revealMarks } from '../revealMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { starCardFacts } from '../starCardFacts.js';

// Deep-sky objects on the photo (D23, UIUX-deepsky-READY.md, decided by the lead 2026-11-19 06:54): which, where, with
// which symbol and which name, and the card of a picked one. The sessions are the calibrated fixture (960 by 540,
// drawn at 1.5 screen pixels a photo pixel from y 45) with deep-sky findings placed at photo pixels through its WCS.

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const VIEW = { w: 1440, h: 900 };
const onScreen = (u: number, v: number) => ({ xPx: (u + 0.5) * 1.5, yPx: 45 + (v + 0.5) * 1.5 });

const base = sample('plate_calibrated');
const star = base.findings.find((f) => f.title === 'Betelgeuse')!;
const wcs = base.wcs!;

/** OpenNGC rows as the shipped file holds them: name, type, V and B. */
const FILE = {
  format: 'albedian.deep-sky-catalog',
  formatVersion: 1,
  release: 'OpenNGC v20260501',
  columns: ['name', 'type', 'raDeg', 'decDeg', 'constellation', 'bMag', 'vMag', 'commonNames'],
  rows: [
    ['NGC5139', 'GCl', 201.7, -47.5, 'Cen', 6.12, 5.33, ['Omega Centauri']],
    ['IC2944', 'Cl+N', 174.3, -63.0, 'Cen', 4.5, null, ['lam Cen Nebula']],
    ['NGC3532', 'OCl', 166.4, -58.8, 'Car', 3.28, 3.0, ['Wishing Well Cluster']],
    ['NGC3918', 'PN', 177.8, -57.2, 'Cen', 8.5, 5.9, ['Blue Planetary']],
    ['NGC5128', 'G', 201.4, -43.0, 'Cen', 7.84, 6.0, ['Centaurus A']],
    ['NGC5460', 'OCl', 211.9, -48.3, 'Cen', 5.6, null, []],
    ['NGC6067', 'OCl', 243.3, -54.2, 'Nor', 6.5, 6.01, []],
    ['NGC5281', 'OCl', 206.6, -62.9, 'Cen', null, null, []],
  ],
};
const TABLE = parseDeepSkyTable(FILE)!;

/** A deep-sky finding as the server makes one (services/science/deep_sky_catalog.py), at a photo pixel through the solve. */
function deep(name: string, title: string, u: number, v: number, over: Partial<CelestialFinding> = {}): CelestialFinding {
  const sky = pixelToWorldWCS(u, v, wcs);
  const row = FILE.rows.find((r) => r[0] === name)!;
  return {
    ...star,
    id: `finding-openngc-${name}`,
    title,
    evidenceClass: 'predicted',
    sourceDetection: undefined,
    inferredDistancePc: undefined,
    skyPosition: { ...star.skyPosition!, raDeg: sky.raDeg, decDeg: sky.decDeg },
    catalogRef: {
      id: `openngc-${name}`,
      provider: 'OpenNGC',
      releaseVersion: 'OpenNGC v20260501',
      catalogSourceId: name,
      commonName: title,
      constellation: row[4] as string,
      objectType: 'deep_sky',
      coordinateEpoch: 'J2000.0',
      raDeg: sky.raDeg,
      decDeg: sky.decDeg,
      ...(row[6] === null ? {} : { apparentMagnitude: row[6] as number }),
    },
    evidence: {
      ...star.evidence!,
      evidenceClass: 'predicted',
      verificationMethod: 'OpenNGC catalog position projected through the solved WCS, which passed held-out verification; not measured in the image',
      limitations: ['Catalog centre from OpenNGC (J2000), projected through the solved WCS: nothing about the object is measured in the image.'],
    },
    ...over,
  } as CelestialFinding;
}
/** A catalog matched star at a photo pixel, to stand in a name's way. */
function matched(title: string, u: number, v: number, magnitude = 5.5): CelestialFinding {
  return {
    ...star,
    id: `finding-${title.toLowerCase()}`,
    title,
    sourceDetection: { ...star.sourceDetection!, xPx: u, yPx: v },
    catalogRef: { ...star.catalogRef!, commonName: title, catalogSourceId: `HIP ${u}${v}`, apparentMagnitude: magnitude },
  } as CelestialFinding;
}
function sessionOf(findings: CelestialFinding[], pickedId: string | null = null, over: Partial<ObservationSolution> = {}): ObservationSession {
  const session = poseSession(base);
  return { ...session, observationSolution: { ...session.observationSolution!, findings, ...over } as ObservationSolution, selectedFindingId: pickedId };
}
const marksOf = (session: ObservationSession, table: DeepSkyTable | null = TABLE, phone = false) => revealMarks(session, VIEW.w, VIEW.h, { deepSky: table, phone });
const byTitle = (session: ObservationSession, table: DeepSkyTable | null = TABLE) => Object.fromEntries(marksOf(session, table).map((m) => [m.finding.title, m]));

describe('the shipped OpenNGC table, read by its own columns', () => {
  it('gives each row its type, V and B by name', () => {
    expect(TABLE.get('NGC5139')).toEqual({ type: 'GCl', vMag: 5.33, bMag: 6.12 });
    expect(TABLE.get('IC2944')).toEqual({ type: 'Cl+N', vMag: null, bMag: 4.5 });
    expect(TABLE.size).toBe(8);
  });

  it('gives no table for a file of another format or missing a column', () => {
    expect(parseDeepSkyTable({ ...FILE, format: 'other' })).toBeNull();
    expect(parseDeepSkyTable({ ...FILE, columns: FILE.columns.filter((c) => c !== 'bMag') })).toBeNull();
    expect(parseDeepSkyTable(null)).toBeNull();
  });

  it('chooses by V, else B where OpenNGC gives no V, at the naked-eye limit', () => {
    expect(DEEP_SKY_LIMIT).toBe(6.0);
    expect(decisionMagnitude(TABLE.get('NGC5139')!)).toBe(5.33);
    expect(decisionMagnitude(TABLE.get('IC2944')!)).toBe(4.5);
    expect(decisionMagnitude(TABLE.get('NGC5281')!)).toBeNull();
  });

  it('gives each type its symbol, the rest a circle', () => {
    expect(['OCl', 'GCl', 'Cl+N', 'PN', 'G', 'EmN', 'Neb', '*Ass'].map(deepSkySymbol)).toEqual(['circle', 'globular', 'cluster_nebula', 'planetary', 'galaxy', 'circle', 'circle', 'circle']);
  });

  it('words each type as decided, and none it does not word', () => {
    expect(['OCl', 'GCl', 'Cl+N', 'PN', 'G', 'HII', 'EmN', 'RfN', 'Neb'].map(deepSkyKindWords)).toEqual([
      'Open cluster',
      'Globular cluster',
      'Star cluster and nebula',
      'Planetary nebula',
      'Galaxy',
      'Emission nebula',
      'Emission nebula',
      'Reflection nebula',
      'Nebula',
    ]);
    expect(deepSkyKindWords('SNR')).toBeNull();
    expect(deepSkyKindWords('toString')).toBeNull();
  });

  it('counts only a title that is not "NGC n", "IC n" or "H n" as a common name', () => {
    expect(['Omega Centauri', 'lam Cen Nebula', 'NGC 5460', 'IC 2944', 'H 5', 'NGC5460', 'Hadar Nebula'].map(hasCommonName)).toEqual([true, true, false, false, false, false, true]);
  });
});

describe('which deep-sky objects are drawn on a calibrated photo, and where', () => {
  it('draws those no fainter than V 6.0 (B with no V) and none fainter or with neither', () => {
    const session = sessionOf([
      deep('NGC5128', 'Centaurus A', 100, 100),
      deep('NGC6067', 'NGC 6067', 200, 100),
      deep('IC2944', 'lam Cen Nebula', 300, 100),
      deep('NGC5281', 'NGC 5281', 400, 100),
    ]);
    expect(marksOf(session).map((m) => m.finding.title)).toEqual(['Centaurus A', 'lam Cen Nebula']);
  });

  it('places each at its catalog centre through the solve, as predicted, with no ring of how far it may be off', () => {
    const [mark] = marksOf(sessionOf([deep('NGC5139', 'Omega Centauri', 321, 222)]));
    const at = onScreen(321, 222);
    expect(Math.abs(mark!.xPx - at.xPx)).toBeLessThan(0.5);
    expect(Math.abs(mark!.yPx - at.yPx)).toBeLessThan(0.5);
    expect(mark!.kind).toBe('predicted');
    expect(mark!.uncertaintyPx).toBeNull();
    expect(mark!.deepSky).toBe('globular');
  });

  it('draws none before the table is read, nor over a solve that did not pass its check', () => {
    const findings = [deep('NGC5139', 'Omega Centauri', 321, 222)];
    expect(marksOf(sessionOf(findings), null)).toEqual([]);
    const unverified = sessionOf(findings, null, { quality: { ...base.quality!, passedIndependentVerification: false } });
    expect(marksOf(unverified)).toEqual([]);
  });

  it('never draws a place the projection cannot give, picked or not', () => {
    const lost = deep('NGC5139', 'Omega Centauri', 321, 222);
    const nan = { ...lost, skyPosition: { ...lost.skyPosition!, raDeg: Number.NaN } };
    expect(marksOf(sessionOf([nan]))).toEqual([]);
    expect(marksOf(sessionOf([nan], nan.id))).toEqual([]);
  });

  it('draws a picked object however faint, in the picked look: named, with its symbol known', () => {
    const faint = deep('NGC6067', 'NGC 6067', 500, 300);
    const [mark] = marksOf(sessionOf([faint], faint.id));
    expect(mark).toMatchObject({ picked: true, name: 'NGC 6067', deepSky: 'circle', uncertaintyPx: null });
  });

  it('gives each its symbol by its OpenNGC type', () => {
    const session = sessionOf([
      deep('NGC5139', 'Omega Centauri', 100, 100),
      deep('IC2944', 'lam Cen Nebula', 100, 300),
      deep('NGC3532', 'Wishing Well Cluster', 100, 500),
      deep('NGC3918', 'Blue Planetary', 600, 100),
      deep('NGC5128', 'Centaurus A', 600, 300),
    ]);
    expect(Object.fromEntries(marksOf(session).map((m) => [m.finding.title, m.deepSky]))).toEqual({
      'Omega Centauri': 'globular',
      'lam Cen Nebula': 'cluster_nebula',
      'Wishing Well Cluster': 'circle',
      'Blue Planetary': 'planetary',
      'Centaurus A': 'galaxy',
    });
  });

  it('leaves every star mark as it was', () => {
    const stars = [matched('Alpha', 200, 200), matched('Beta', 700, 400)];
    const without = revealMarks(sessionOf(stars), VIEW.w, VIEW.h);
    const withDeep = marksOf(sessionOf([...stars, deep('NGC5139', 'Omega Centauri', 450, 100)])).filter((m) => m.deepSky === null);
    expect(withDeep).toEqual(without.map((m) => ({ ...m, deepSky: null })));
  });
});

describe('a deep-sky name: a common name only, 6 px beside its symbol, where it has room', () => {
  // At 13 px a name is 7.28 px a letter; it starts 12 px from the centre (the symbol's 6 and the gap's 6)
  it('names a common name on the right and never an NGC number', () => {
    const marks = byTitle(sessionOf([deep('NGC5139', 'Omega Centauri', 300, 200), deep('NGC5460', 'NGC 5460', 300, 400)]));
    expect([marks['Omega Centauri']!.name, marks['Omega Centauri']!.nameOnLeft]).toEqual(['Omega Centauri', false]);
    expect(marks['NGC 5460']!.name).toBeNull();
  });

  it('goes left where the right runs past the photo, and none where neither side fits', () => {
    // 950 of 960: the right would pass the photo's edge at 1440
    expect(byTitle(sessionOf([deep('NGC5139', 'Omega Centauri', 950, 200)]))['Omega Centauri']!.nameOnLeft).toBe(true);
  });

  it('gives way to a star mark on the right, and to one on each side altogether', () => {
    // A star 40 photo px (60 on screen) right of the symbol sits in the name's way
    const right = byTitle(sessionOf([deep('NGC5139', 'Omega Centauri', 300, 200), matched('Blocker', 340, 200)]));
    expect([right['Omega Centauri']!.name, right['Omega Centauri']!.nameOnLeft]).toEqual(['Omega Centauri', true]);
    const both = byTitle(sessionOf([deep('NGC5139', 'Omega Centauri', 300, 200), matched('Blocker', 340, 200), matched('Other', 260, 200)]));
    expect(both['Omega Centauri']!.name).toBeNull();
  });

  it("keeps 9 px round a star's centre, as the design page's code measures it (Pearl Cluster at phone width)", () => {
    // The name's box ends at 352.25 on screen; a star 12 px below that keeps clear of it at 9 px (it would not at 14)
    const marks = byTitle(sessionOf([deep('NGC5139', 'Omega Centauri', 300, 200), matched('Below', 332.8, 212.3)]));
    expect([marks['Omega Centauri']!.name, marks['Omega Centauri']!.nameOnLeft]).toEqual(['Omega Centauri', false]);
  });

  it('is not stopped by a star mark over its own centre, as the design page has it (lam Cen Nebula)', () => {
    // A star 3 photo px from the symbol's centre: its room holds the centre, and the name runs on to the right
    const marks = byTitle(sessionOf([deep('IC2944', 'lam Cen Nebula', 300, 200), matched('Held', 303, 202)]));
    expect([marks['lam Cen Nebula']!.name, marks['lam Cen Nebula']!.nameOnLeft]).toEqual(['Lambda Centauri Nebula', false]);
  });

  it("gives way to a star's name", () => {
    // Alpha (450.75, 345.75 on screen) is named to its right, its name's box down to 352.25. The symbol at (470, 361)
    // keeps clear of that box, but its own name on the right (from 350.6) would cross it; on the left it would cross
    // Alpha's mark, so it has none
    const marks = byTitle(sessionOf([matched('Alpha', 300, 200, 1), deep('NGC5139', 'Omega Centauri', 312.8, 210.2)]));
    expect([marks.Alpha!.name, marks.Alpha!.nameOnLeft]).toEqual(['Alpha', false]);
    expect(marks['Omega Centauri']!.name).toBeNull();
  });

  it('places the brighter name first; a fainter one gives way to it', () => {
    // Two symbols 4 photo px apart in height: Wishing Well (V 3.0) keeps the right, Centaurus A (V 6.0) gives way
    const marks = byTitle(sessionOf([deep('NGC5128', 'Centaurus A', 300, 206), deep('NGC3532', 'Wishing Well Cluster', 300, 200)]));
    expect(marks['Wishing Well Cluster']!.nameOnLeft).toBe(false);
    expect(marks['Centaurus A']!.nameOnLeft).toBe(true);
  });

  it('is kept off the predicted names budget the pose path gives', () => {
    const many = Array.from({ length: 6 }, (_, i) => deep('NGC3532', `Cluster ${'abcdef'[i]}`, 100 + i * 150, 100 + i * 60, { id: `finding-openngc-NGC3532-${i}` }));
    expect(marksOf(sessionOf(many)).filter((m) => m.name !== null)).toHaveLength(6);
  });
});

describe('the marks on the photo and the card of a picked one', () => {
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
  const q = (selector: string) => container.querySelector(selector);
  async function show(session: ObservationSession) {
    await act(async () => {
      root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  const glyph = (id: string) => q(`[data-testid="reveal-mark-finding-openngc-${id}"] .reveal-mark__glyph`)!.innerHTML;

  it('reads the table and draws each symbol 12 px across, outlines dashed 2.6 2.4, the cross and ticks solid', async () => {
    await show(
      sessionOf([
        deep('NGC5139', 'Omega Centauri', 100, 100),
        deep('IC2944', 'lam Cen Nebula', 100, 300),
        deep('NGC3532', 'Wishing Well Cluster', 100, 500),
        deep('NGC3918', 'Blue Planetary', 600, 100),
        deep('NGC5128', 'Centaurus A', 600, 300),
      ])
    );
    expect(fetch).toHaveBeenCalledWith(DEEP_SKY_CATALOG_URL);
    expect(glyph('NGC5139')).toBe('<circle r="6" stroke-dasharray="2.6 2.4"></circle><path d="M-6 0H6M0 -6V6"></path>');
    expect(glyph('IC2944')).toBe('<rect x="-6" y="-6" width="12" height="12" stroke-dasharray="2.6 2.4"></rect><circle r="4.8" stroke-dasharray="2.6 2.4"></circle>');
    expect(glyph('NGC3532')).toBe('<circle r="6" stroke-dasharray="2.6 2.4"></circle>');
    expect(glyph('NGC3918')).toBe('<circle r="6" stroke-dasharray="2.6 2.4"></circle><path d="M-10.5 0H-6M6 0H10.5M0 -10.5V-6M0 6V10.5"></path>');
    expect(glyph('NGC5128')).toBe('<ellipse rx="3" ry="6" stroke-dasharray="2.6 2.4"></ellipse>');
    const mark = q('[data-testid="reveal-mark-finding-openngc-NGC5139"]')!;
    expect(mark.getAttribute('class')).toBe('reveal-mark is-predicted is-deepsky');
    expect(mark.querySelector('.reveal-mark__name')!.textContent).toBe('Omega Centauri');
  });

  it('draws a picked one in the picked prediction look, and its card: type and constellation, V as magnitude, the OpenNGC row', async () => {
    const omega = deep('NGC5139', 'Omega Centauri', 300, 200);
    await show(sessionOf([omega], omega.id));
    const mark = q('[data-testid="reveal-mark-finding-openngc-NGC5139"]')!;
    expect(mark.getAttribute('class')).toBe('reveal-mark is-predicted is-picked');
    expect(mark.querySelector('.reveal-mark__glyph')!.innerHTML).toBe('<circle class="reveal-mark__halo" r="22"></circle><circle r="10" stroke-dasharray="2.8 2.6"></circle>');
    expect(q('.reveal-card__sub')!.textContent).toBe('Globular cluster · Centaurus');
    expect(q('.reveal-card__number b')!.textContent).toBe('5.33');
    expect(q('.reveal-card__label')!.textContent).toBe('magnitude');
    expect(q('.reveal-card__number')!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(q('.reveal-card__source')!.textContent).toBe('OpenNGC v20260501 · NGC5139');
    expect(container.textContent).not.toContain('above the noise');
    expect(container.textContent).not.toContain('Go there');
  });

  it('asks for no table on a photo with no deep-sky finding', async () => {
    await show(sessionOf([matched('Alpha', 200, 200)]));
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("a picked deep-sky object's card facts", () => {
  it('gives the constellation alone with no row, and no number with no V', () => {
    const lam = deep('IC2944', 'lam Cen Nebula', 300, 200);
    expect(starCardFacts(lam, null)).toMatchObject({ title: 'Lambda Centauri Nebula', subLine: 'Centaurus', numbers: [], sourceLine: 'OpenNGC v20260501 · IC2944', canGoThere: false });
    expect(starCardFacts(lam, TABLE.get('IC2944')!).subLine).toBe('Star cluster and nebula · Centaurus');
  });

  it('never takes an NGC-titled object for a nameless star', () => {
    const plain = deep('NGC5460', 'NGC5460', 300, 200);
    expect(starCardFacts(plain, TABLE.get('NGC5460')!)).toMatchObject({ title: 'NGC5460', subLine: 'Open cluster · Centaurus' });
  });
});

describe('the deep-sky name rules', () => {
  function declarationsOf(selector: string): Record<string, string> {
    const found: Record<string, string> = {};
    let rules = 0;
    postcss.parse(css).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
      rules += 1;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    expect(rules, selector).toBe(1);
    return found;
  }

  it('sets the name 12 px from the centre, on either side, at the size of the screen it is on', () => {
    expect(declarationsOf('.reveal-photo .reveal-mark.is-deepsky .reveal-mark__name')).toEqual({ left: 'calc(50% + 12px)' });
    expect(declarationsOf('.reveal-photo .reveal-mark.is-deepsky.is-left .reveal-mark__name')).toEqual({ left: 'auto', right: 'calc(50% + 12px)' });
    // After the phone's own name rules, which they must outrank at equal weight
    const order: string[] = [];
    postcss.parse(css).walkRules((rule) => {
      order.push(rule.selector.replace(/\s+/g, ' '));
    });
    expect(order.indexOf('.reveal-photo .reveal-mark.is-deepsky.is-left .reveal-mark__name')).toBeGreaterThan(order.indexOf('.reveal-photo.is-phone .reveal-mark.is-left .reveal-mark__name'));
  });
});

describe("the deep-sky marks and MOTION's arrival (D23: no movement where MOTION states none)", () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    forgetDeepSkyTable();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(FILE))));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it("gives deep-sky marks no place in the matched marks' arrival, and counts only the others", async () => {
    const session = sessionOf([matched('Alpha', 200, 200, 1), deep('NGC3532', 'Wishing Well Cluster', 400, 300), matched('Beta', 700, 400, 2)]);
    await act(async () => {
      root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const layer = container.querySelector('[data-testid="reveal-marks"]') as HTMLElement;
    expect(layer.style.getPropertyValue('--mark-n')).toBe('2');
    const rank = (id: string) => (container.querySelector(`[data-testid="reveal-mark-${id}"]`) as HTMLElement).style.getPropertyValue('--mark-i');
    expect(rank('finding-alpha')).toBe('0');
    expect(rank('finding-beta')).toBe('1');
    expect(rank('finding-openngc-NGC3532')).toBe('');
  });

  it('draws them with no animation', () => {
    const found: Record<string, string> = {};
    postcss.parse(css).walkRules((rule) => {
      if (rule.selector !== '.reveal-marks .reveal-mark.is-deepsky') return;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value;
      });
    });
    expect(found).toEqual({ animation: 'none' });
  });
});
