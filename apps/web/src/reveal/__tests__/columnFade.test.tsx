// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { PredictedSkyScreen } from '../PredictedSkyScreen.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { FADE_REACH, fadeOf, nameClearOf, REVEAL_KEEP_CLEAR, useKeepClear } from '../keepClear.js';
import type { Box } from '../predictedSkyMarks.js';
import { placeSkyMarks, SKY_NAMED_RING_PX } from '../predictedSkyMarks.js';
import { revealMarks } from '../revealMarks.js';
import { ALIGN_RING_PX, placeMarks } from '../AlignLayer.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
vi.mock('../../spatial/flight/FlightStarsLayer.js', () => ({ FlightStarsLayer: () => null }));
// The places the predicted sky hands its marks, as it hands them
const handed = vi.hoisted(() => ({ clear: [] as unknown[] }));
vi.mock('../predictedSkyMarks.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../predictedSkyMarks.js')>();
  return {
    ...original,
    predictedSkyMarks: (...args: Parameters<typeof original.predictedSkyMarks>) => {
      handed.clear = [...(args[5] ?? [])];
      return original.predictedSkyMarks(...args);
    },
  };
});

// laptop-column-fade-one (gap five, ruled 16:40; UIUX-look5to10-units-READY.md item 1): D19's fade, the card backing's
// values (82 percent black under the words, clearing over 44 px beyond their open edges, the window's edges closing the
// rest; no box, no edge line, no motion of its own), behind the flight's disclosure column and readout and the
// predicted sky's status column on the laptop. Folded in (DECIDED-BY-LEAD 19:48, UIUX's addition of 19:46): the same
// fade behind the views row and the Sound circle, and on the phone behind the whole top bar, clearing below its foot.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

const rules = () => {
  const found: Record<string, Record<string, string>> = {};
  postcss.parse(css).walkRules((rule) => {
    if (rule.parent?.type !== 'root') return;
    const decls: Record<string, string> = {};
    rule.walkDecls((d) => void (decls[d.prop] = d.value.replace(/\s+/g, ' ')));
    const key = rule.selectors.map((s) => s.replace(/\s+/g, ' ')).join(', ');
    found[key] = { ...(found[key] ?? {}), ...decls };
  });
  return found;
};
const D19 = 'rgba(0, 0, 0, 0.82)';
/** Clearing over 44 px at the top and the bottom, as the card backing's */
const VERTICAL = 'linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)';
const masked = (horizontal: string, vertical: string) => ({
  '-webkit-mask-image': `${horizontal}, ${vertical}`,
  '-webkit-mask-composite': 'source-in',
  'mask-image': `${horizontal}, ${vertical}`,
  'mask-composite': 'intersect',
});
const base = { content: "''", position: 'absolute', 'z-index': '-1', 'pointer-events': 'none' };

const COLUMNS = ".reveal-status[data-moment='there']::before, .reveal-status[data-moment='real-sky']:not(.is-phone)::before, .predsky:not(.is-phone) .predsky-status::before";
const READOUT = '.flight-readout:not(.is-phone)::before';
// fade-off-photo-one: the views row's fade stands over a drawn view or where no photo is (the modeled scene), never over
// the photo at rest
const VIEWS = ":root:has(.reveal-status:is([data-moment='real-sky'], [data-moment='there']), .predsky:not(.is-phone)) .reveal-nav::before, :root:not(:has(.reveal-photo)) .reveal-nav::before";
// fade-off-photo-two: the phone's top bar fade stands over Real sky and the flight only, never over the photo at rest
const PHONE_TOP = ":root:has(.reveal-status[data-moment='real-sky'], .flight-readout) .reveal-phone-top::before";

describe("D19's fade behind the laptop's columns, the flight's readout and the views row", () => {
  it("stands behind the flight's disclosure column and the predicted sky's status column, reaching the window's left edge", () => {
    expect(rules()[COLUMNS]).toEqual({
      ...base,
      left: '-36px',
      top: '-44px',
      bottom: '-44px',
      right: '-44px',
      background: D19,
      ...masked('linear-gradient(to left, transparent 0, #000 44px)', VERTICAL),
    });
    // The columns stand 36 px from the window's left edge, so the fade reaches it
    expect(rules()['.reveal-status']!.left).toBe('36px');
  });

  it("stands behind the flight's readout as behind the open card, closed at the window's right edge", () => {
    expect(rules()[READOUT]).toEqual({
      ...base,
      left: '-44px',
      top: '-44px',
      bottom: '-44px',
      right: '-32px',
      background: D19,
      ...masked('linear-gradient(to right, transparent 0, #000 44px)', VERTICAL),
    });
    expect(rules()['.flight-readout']!.right).toBe('32px');
  });

  it('stands behind the views row and the Sound circle over the drawn views, closed by the window above and to the right', () => {
    expect(rules()[VIEWS]).toEqual({
      ...base,
      left: '-44px',
      top: '-26px',
      bottom: '-44px',
      right: '-32px',
      background: D19,
      ...masked('linear-gradient(to right, transparent 0, #000 44px)', 'linear-gradient(to bottom, #000 calc(100% - 44px), transparent 100%)'),
    });
    expect(rules()['.reveal-nav']!).toMatchObject({ right: '32px', top: '26px' });
  });

  it("stands behind the phone's whole top bar over the drawn views, from the window's top and sides, clearing over 44 px below its foot", () => {
    expect(rules()[PHONE_TOP]).toEqual({
      ...base,
      left: '-20px',
      right: '-20px',
      top: '-60px',
      bottom: '-44px',
      background: 'linear-gradient(to bottom, rgba(0, 0, 0, 0.82) calc(100% - 44px), rgba(0, 0, 0, 0) 100%)',
    });
    expect(rules()['.reveal-phone-top']!).toMatchObject({ left: '20px', right: '20px', top: '60px' });
  });

  it('keeps each fade inside its own stacking, behind the words: no box and no edge line of its own', () => {
    const all = rules();
    for (const selector of ["reveal-status[data-moment='there']", 'predsky:not(.is-phone) .predsky-status', 'flight-readout:not(.is-phone)']) {
      const entry = Object.entries(all).find(([key]) => !key.includes('::before') && key.includes(selector) && 'isolation' in all[key]!);
      expect(entry?.[1], selector).toEqual({ isolation: 'isolate' });
    }
    // The views row and the phone's top bar are fixed with their own z-index, so each is its own stacking already
    for (const bar of ['.reveal-nav', '.reveal-phone-top']) {
      expect(all[bar], bar).toMatchObject({ position: 'fixed', 'z-index': '35' });
      expect(all[bar]!.isolation, bar).toBeUndefined();
    }
  });

  it('has no motion of its own: none in their own rules, only their blocks\' entrances, and no transition', () => {
    for (const sheet of [css, motion]) {
      postcss.parse(sheet).walkRules((rule) => {
        if (!rule.selector.includes('::before') || !/reveal-status|predsky-status|flight-readout|reveal-nav|reveal-phone-top/.test(rule.selector)) return;
        // fade-with-words-one (22:12): the fade's only motion is its block's own entrance, in motion.css, each pinned to its
        // block's first words by fadeWithWords.test.ts; reveal.css gives it none, and nothing transitions it
        rule.walkDecls((d) => expect(sheet === motion ? /^transition/.test(d.prop) : /^(animation|transition)/.test(d.prop), rule.selector).toBe(false));
      });
    }
  });
});

describe("the fade's selectors are the elements the screens draw", () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });
  const show = (session: ObservationSession, phone: boolean) =>
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session,
          onSelectFinding: () => {},
          onHome: () => {},
          viewportWidthPx: phone ? 390 : 1440,
          viewportHeightPx: phone ? 844 : 900,
          phone,
          flight: { name: 'Betelgeuse', toStarLy: 1, fromEarthLy: 548 },
        } as unknown as Parameters<typeof RevealPhoto>[0])
      )
    );
  const target = (selector: string) => container.querySelector(selector.replace('::before', ''));

  it("on the laptop's flight: the disclosure column and the readout are the ones faded", () => {
    show(poseSession(sample('plate_calibrated')), false);
    expect(target(COLUMNS.split(', ')[0]!)).not.toBeNull();
    expect(target(READOUT)).not.toBeNull();
  });

  it("on the phone's flight: the readout is the phone's own and takes no laptop fade", () => {
    show(poseSession(sample('plate_calibrated')), true);
    expect(container.querySelector('.flight-readout.is-phone')).not.toBeNull();
    expect(target(READOUT)).toBeNull();
    expect(target(COLUMNS.split(', ')[0]!)).toBeNull();
  });

  it("on the laptop's predicted sky: its status column is the one faded; on the phone it keeps its own fade", () => {
    const start = poseSession(sample('horizon_predicted_only'));
    const sky = dispatchAlbedianAction(start, { type: 'sky.show', requestId: 'req_sky', expectedViewRevision: start.viewRevision, mode: 'predicted', observationSolutionId: start.observationSolution!.id }).session;
    act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => null })));
    expect(target(COLUMNS.split(', ')[2]!)).not.toBeNull();
    act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => null, phone: true })));
    expect(target(COLUMNS.split(', ')[2]!)).toBeNull();
    expect(container.querySelector('.predsky.is-phone .predsky-status')).not.toBeNull();
  });
});

// Folded in (DECIDED-BY-LEAD 20:12 on 24 Nov, UIUX on VIDEO's picture of a name reaching into the open card's fade): a
// name is placed only where its whole box clears the open card together with the fade's reach, and the same for every
// keep-clear box that carries the ruled fade, so no word stands half in the darkening; a ring keeps today's box
describe("names clear the fade's reach; rings keep the place's own box", () => {
  it("sets each fade's reach as the stylesheet draws it", () => {
    const all = rules();
    for (const { faded, reach } of FADE_REACH) {
      // A fade drawn only over some views (fade-off-photo-one: the legend's and the ask bar's, over the drawn views alone)
      // keeps its reach there: its rule names the views first, then the faded block
      const key = Object.keys(all).find((k) => k.split(', ').includes(`${faded}::before`) || k.endsWith(` ${faded}::before`));
      expect(key, faded).toBeDefined();
      const d = all[key!]!;
      expect([d.left, d.top, d.right, d.bottom], faded).toEqual([`-${reach.left}px`, `-${reach.top}px`, `-${reach.right}px`, `-${reach.bottom}px`]);
    }
    // Every place with a fade is one the marks keep clear of, on the reveal or on the predicted sky
    for (const { place } of FADE_REACH.filter((f) => !f.place.includes('predsky'))) {
      const el = document.createElement('div');
      if (place.includes('reveal-card')) el.className = 'reveal-card';
      if (place.includes('reveal-answer')) el.className = 'reveal-answer';
      if (place.includes('look-views')) {
        const nav = document.createElement('div');
        nav.className = 'reveal-nav';
        el.className = 'look-views';
        nav.appendChild(el);
      }
      if (place.includes('reveal-status')) {
        el.className = 'reveal-status';
        el.dataset.testid = 'reveal-status';
        el.dataset.moment = place.includes('real-sky') ? 'real-sky' : 'there';
      }
      if (place.includes('ask-bar')) el.className = 'ask-bar';
      if (place.includes('reveal-legend')) el.className = 'reveal-legend';
      if (place.includes('sound-control')) {
        const nav = document.createElement('div');
        nav.className = 'reveal-nav';
        el.className = 'sound-control';
        nav.appendChild(el);
      }
      expect(REVEAL_KEEP_CLEAR.some((selector) => el.matches(selector)), place).toBe(true);
      expect(el.matches(place), place).toBe(true);
    }
  });

  it("measures the reach from the element that draws the fade: the Sound circle's is the views row's", () => {
    const rect = (left: number, top: number, right: number, bottom: number) => () => ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) }) as DOMRect;
    const nav = document.createElement('div');
    nav.className = 'reveal-nav';
    const sound = document.createElement('button');
    sound.className = 'sound-control';
    nav.appendChild(sound);
    nav.getBoundingClientRect = rect(1100, 26, 1408, 70);
    sound.getBoundingClientRect = rect(1364, 26, 1408, 70);
    expect(fadeOf(sound)).toEqual({ x0: 1056, x1: 1440, y0: 0, y1: 114 });
    const card = document.createElement('section');
    card.className = 'reveal-card';
    card.getBoundingClientRect = rect(1088, 118, 1408, 600);
    expect(fadeOf(card)).toEqual({ x0: 1044, x1: 1440, y0: 74, y1: 644 });
    // A place with no fade has none; a phone sheet has its own ground
    const ask = document.createElement('div');
    ask.className = 'ask-bar';
    ask.getBoundingClientRect = rect(500, 800, 900, 860);
    // The ask bar carries its own fade since column-foot-fade-one (21:49)
    expect(fadeOf(ask)).toEqual({ x0: 456, x1: 944, y0: 756, y1: 892 });
    card.className = 'reveal-card is-phone';
    expect(fadeOf(card)).toBeUndefined();
    expect(nameClearOf({ x0: 1, x1: 2, y0: 3, y1: 4 })).toEqual({ x0: 1, x1: 2, y0: 3, y1: 4 });
  });

  // A place 40 px right of a star's name with a fade reaching 44 px to its left: the name's right side would end in the
  // darkening, so it takes the left; with no fade the name keeps the right; the ring stays either way
  const W = 1440;
  const H = 900;
  it('on the predicted sky (placeSkyMarks)', () => {
    const star = { id: 'a', name: 'Vega', xPx: 700, yPx: 400 };
    const [free] = placeSkyMarks([star], W, H, 13);
    expect(free).toMatchObject({ name: 'Vega', nameOnLeft: false });
    const nameEnd = 700 + SKY_NAMED_RING_PX + 4 + 4 * 13 * 0.56;
    const place = { x0: nameEnd + 40, x1: nameEnd + 300, y0: 300, y1: 500 };
    expect(placeSkyMarks([star], W, H, 13, [place])[0]).toMatchObject({ name: 'Vega', nameOnLeft: false });
    const faded = { ...place, fade: { x0: place.x0 - 44, x1: place.x1 + 44, y0: place.y0 - 44, y1: place.y1 + 44 } };
    expect(placeSkyMarks([star], W, H, 13, [faded])[0]).toMatchObject({ name: 'Vega', nameOnLeft: true, xPx: 700 });
    // A ring beside the place but inside its reach keeps its ring
    expect(placeSkyMarks([{ id: 'b', name: null, xPx: place.x0 - 20, yPx: 400 }], W, H, 13, [faded])).toHaveLength(1);
  });

  it('on the photo (revealMarks)', () => {
    const session = poseSession(sample('plate_calibrated'));
    const free = revealMarks(session, W, H).find((m) => m.name !== null && !m.nameOnLeft)!;
    expect(free).toBeDefined();
    // Where its name ends, read from where it can stand: a place starting 4 px past the name's end leaves it alone
    const at = (x0: number) => revealMarks(session, W, H, { clear: [{ x0, x1: x0 + 200, y0: free.yPx - 60, y1: free.yPx + 60 }] }).find((m) => m.finding.id === free.finding.id);
    const rightOf = (x0: number) => {
      const m = at(x0);
      return m !== undefined && m.name !== null && !m.nameOnLeft;
    };
    let x0 = free.xPx + 10;
    while (!rightOf(x0)) {
      x0 += 2;
      expect(x0).toBeLessThan(W);
    }
    const place = { x0: x0 + 4, x1: x0 + 204, y0: free.yPx - 60, y1: free.yPx + 60 };
    const kept = revealMarks(session, W, H, { clear: [place] }).find((m) => m.finding.id === free.finding.id)!;
    expect(kept).toMatchObject({ name: free.name, nameOnLeft: false });
    const faded = { ...place, fade: { x0: place.x0 - 44, x1: place.x1 + 44, y0: place.y0 - 44, y1: place.y1 + 44 } };
    const moved = revealMarks(session, W, H, { clear: [faded] }).find((m) => m.finding.id === free.finding.id)!;
    // The mark stays where it was measured; its name leaves the darkening, to the left or not drawn
    expect([moved.xPx, moved.yPx]).toEqual([free.xPx, free.yPx]);
    expect(moved.name === null || moved.nameOnLeft).toBe(true);
  });

  it('on the aligning screen (placeMarks)', () => {
    const rect = { leftPx: 0, topPx: 0, widthPx: W, heightPx: H };
    const target = [{ id: 'a', name: 'Vega', u: 700 / W, v: 400 / H }];
    const free = placeMarks(target, rect, 13)[0]!;
    expect(free).toMatchObject({ name: 'Vega', nameOnLeft: false });
    const nameEnd = 700 + ALIGN_RING_PX + 6 + 4 * 13 * 0.56;
    const place = { x0: nameEnd + 8, x1: nameEnd + 300, y0: 300, y1: 500 };
    expect(placeMarks(target, rect, 13, [place])[0]).toMatchObject({ name: 'Vega', nameOnLeft: false });
    const faded = { ...place, fade: { x0: place.x0 - 44, x1: place.x1 + 44, y0: place.y0 - 44, y1: place.y1 + 44 } };
    expect(placeMarks(target, rect, 13, [faded])[0]).toMatchObject({ name: 'Vega', nameOnLeft: true });
  });

  const rectOf = (left: number, top: number, right: number, bottom: number) => () => ({ left, top, right, bottom, width: right - left, height: bottom - top, x: left, y: top, toJSON: () => ({}) }) as DOMRect;

  it('the reveal measures each faded place with its reach (useKeepClear)', () => {
    const nav = document.createElement('div');
    nav.className = 'reveal-nav';
    const sound = document.createElement('button');
    sound.className = 'sound-control';
    nav.appendChild(sound);
    const card = document.createElement('section');
    card.className = 'reveal-card';
    const ask = document.createElement('div');
    ask.className = 'ask-bar';
    nav.getBoundingClientRect = rectOf(1100, 26, 1408, 70);
    sound.getBoundingClientRect = rectOf(1364, 26, 1408, 70);
    card.getBoundingClientRect = rectOf(1088, 118, 1408, 600);
    ask.getBoundingClientRect = rectOf(500, 800, 900, 860);
    document.body.append(nav, card, ask);
    const host = document.createElement('div');
    const r = createRoot(host);
    let boxes: Box[] = [];
    const Probe = () => {
      boxes = useKeepClear(true);
      return null;
    };
    try {
      act(() => r.render(createElement(Probe)));
      act(() => r.render(createElement(Probe)));
      const byLeft = (x0: number) => boxes.find((b) => b.x0 === x0)!;
      expect(byLeft(1364).fade).toEqual({ x0: 1056, x1: 1440, y0: 0, y1: 114 });
      expect(byLeft(1088).fade).toEqual({ x0: 1044, x1: 1440, y0: 74, y1: 644 });
      expect(byLeft(500).fade).toEqual({ x0: 456, x1: 944, y0: 756, y1: 892 });
    } finally {
      act(() => r.unmount());
      nav.remove();
      card.remove();
      ask.remove();
    }
  });

  it("the predicted sky hands its marks the status column's and the Sound circle's reach", () => {
    const nav = document.createElement('div');
    nav.className = 'reveal-nav';
    const sound = document.createElement('button');
    sound.className = 'sound-control';
    nav.appendChild(sound);
    nav.getBoundingClientRect = rectOf(1100, 26, 1408, 70);
    sound.getBoundingClientRect = rectOf(1364, 26, 1408, 70);
    document.body.append(nav);
    const original = HTMLElement.prototype.getBoundingClientRect;
    try {
      HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
        if (this.matches('.predsky-status')) return rectOf(36, 70, 336, 300)();
        return original.call(this);
      };
      const start = poseSession(sample('horizon_predicted_only'));
      const sky = dispatchAlbedianAction(start, { type: 'sky.show', requestId: 'req_sky', expectedViewRevision: start.viewRevision, mode: 'predicted', observationSolutionId: start.observationSolution!.id }).session;
      const pose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: 1440 / 900, limitingMagnitude: 6 } as const;
      const host = document.createElement('div');
      document.body.appendChild(host);
      const r = createRoot(host);
      act(() => r.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => pose })));
      act(() => r.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => pose })));
      act(() => r.unmount());
      host.remove();
      const clear = handed.clear as Box[];
      expect(clear.find((b) => b.x0 === 36)?.fade).toEqual({ x0: 0, x1: 380, y0: 26, y1: 344 });
      expect(clear.find((b) => b.x0 === 1364)?.fade).toEqual({ x0: 1056, x1: 1440, y0: 0, y1: 114 });
    } finally {
      HTMLElement.prototype.getBoundingClientRect = original;
      nav.remove();
    }
  });
});

