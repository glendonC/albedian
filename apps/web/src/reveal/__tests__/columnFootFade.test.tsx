// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { FlightPills } from '../../spatial/flight/ConePieces.js';
import { fadeOf, KEEP_CLEAR_BLOCKS, REVEAL_KEEP_CLEAR } from '../keepClear.js';
import { RevealPhoto } from '../RevealPhoto.js';

// The Real sky layer draws with WebGL 2, which jsdom has not: a stand-in says it drew, so Real sky's column shows
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  const { useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => props.onDrawn?.(true), [props.onDrawn]);
      return null;
    },
  };
});

// fade-off-photo-one (DECIDED-BY-LEAD on VIDEO's frame of the hero photo at rest, .lead/video/fade-over-moon-a74f11b0-vs-57e2a9f1.jpg:
// the person's photograph comes first; no drawn fade hides the subject of the person's own photo, and a block over the
// photograph is never a panel): the fades behind the legend and the ask bar come off the reveal at rest, as before batch
// one hundred and twenty-five, and stay over the drawn views (Real sky; for the ask bar there and the predicted sky too).
// The keep-clear rules for marks and names stand.
const LEGEND_FADE = ".reveal-photo:has(.reveal-status[data-moment='real-sky']) .reveal-legend::before";
const ASK_FADE = ":root:has(.reveal-status:is([data-moment='real-sky'], [data-moment='there']), .predsky:not(.is-phone)) .ask-bar:not(.is-phone)::before";
// The views row's too (the lead's widening, on VIDEO's whole frame: its fade darkened the photo's top right corner); it
// stays wherever no photo is under it, as on the modeled scene
const VIEWS_DRAWN = ":root:has(.reveal-status:is([data-moment='real-sky'], [data-moment='there']), .predsky:not(.is-phone)) .reveal-nav::before";
// fade-off-photo-two (DECIDED-BY-LEAD on WEB-UX's flag: the same rule at phone width): the phone's top bar fade stands over
// Real sky and the flight only
const PHONE_TOP_FADE = ":root:has(.reveal-status[data-moment='real-sky'], .flight-readout) .reveal-phone-top::before";
const VIEWS_NO_PHOTO = ":root:not(:has(.reveal-photo)) .reveal-nav::before";

// column-foot-fade-one (UIUX-reveal-READY.md, the addition of 24 Nov 21:47; DECIDED-BY-LEAD 21:49, on WEB-UX's walk at
// 3840): on the laptop, D19's fade (82 percent black under the block, clearing over 44 px beyond its open edges, the
// window's edges closing the rest; no box, no edge line, no motion of its own) behind Real sky's status column, the
// legend "In this photo", the ask bar and the flight's pill row; pills, tags and words unchanged. Names clear each new
// reach and rings keep the place's own box (20:12): Real sky's column and the ask bar join FADE_REACH, and the laptop's
// legend joins the places kept clear with its reach.

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
/** The rule whose selector list holds this selector, and its declarations. */
const ruleWith = (selector: string) => {
  const all = rules();
  const key = Object.keys(all).find((k) => k === selector || k.split(', ').includes(selector));
  return key === undefined ? undefined : all[key];
};
const D19 = 'rgba(0, 0, 0, 0.82)';
const base = { content: "''", position: 'absolute', 'z-index': '-1', 'pointer-events': 'none' };
const masked = (horizontal: string, vertical: string) => ({
  '-webkit-mask-image': `${horizontal}, ${vertical}`,
  '-webkit-mask-composite': 'source-in',
  'mask-image': `${horizontal}, ${vertical}`,
  'mask-composite': 'intersect',
});

describe("D19's fade behind Real sky's column, the legend, the ask bar and the flight's pill row (laptop)", () => {
  it("stands behind Real sky's status column with the flight's and the predicted sky's columns' own reach", () => {
    const real = ruleWith(".reveal-status[data-moment='real-sky']:not(.is-phone)::before");
    expect(real).toBeDefined();
    expect(real).toEqual(ruleWith(".reveal-status[data-moment='there']::before"));
    expect(ruleWith(".reveal-status[data-moment='real-sky']:not(.is-phone)")).toEqual({ isolation: 'isolate' });
  });

  it("stands behind the legend over Real sky, closed by the window's left and bottom edges, clearing over 44 px above and to the right", () => {
    expect(ruleWith(LEGEND_FADE)).toEqual({
      ...base,
      left: '-36px',
      top: '-44px',
      right: '-44px',
      bottom: '-36px',
      background: D19,
      ...masked('linear-gradient(to left, transparent 0, #000 44px)', 'linear-gradient(to bottom, transparent 0, #000 44px)'),
    });
    expect(rules()['.reveal-legend']).toMatchObject({ left: '36px', bottom: '36px' });
    expect(rules()['.reveal-legend, .flight-pills:not(.is-phone)']).toEqual({ isolation: 'isolate' });
  });

  it("stands behind the ask bar over the drawn views, closed by the window's bottom edge, clearing over 44 px above, left and right", () => {
    expect(ruleWith(ASK_FADE)).toEqual({
      ...base,
      left: '-44px',
      top: '-44px',
      right: '-44px',
      bottom: '-32px',
      background: D19,
      ...masked(
        'linear-gradient(to right, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)',
        'linear-gradient(to bottom, transparent 0, #000 44px)'
      ),
    });
    // Fixed with its own z-index, the bar is its own stacking already: its body and blur stay as approved
    expect(rules()['.ask-bar']).toMatchObject({ position: 'fixed', bottom: '32px', 'z-index': '30' });
  });

  it("stands behind the flight's pill row: closed by the window's left and bottom edges at rest, clearing below too when stepped up", () => {
    expect(ruleWith('.flight-pills:not(.is-phone)::before')).toEqual({
      ...base,
      left: '-36px',
      top: '-44px',
      right: '-44px',
      bottom: '-32px',
      background: D19,
      ...masked('linear-gradient(to left, transparent 0, #000 44px)', 'linear-gradient(to bottom, transparent 0, #000 44px)'),
    });
    const stepped = 'linear-gradient(to left, transparent 0, #000 44px), linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)';
    expect(ruleWith('.flight-pills[data-stepped]:not(.is-phone)::before')).toEqual({ bottom: '-44px', '-webkit-mask-image': stepped, 'mask-image': stepped });
  });

  it('has no motion of its own: none in their own rules, only their blocks\' entrances, and no transition', () => {
    for (const sheet of [css, motion]) {
      postcss.parse(sheet).walkRules((rule) => {
        if (!rule.selector.includes('::before') || !/real-sky|reveal-legend|ask-bar|flight-pills/.test(rule.selector)) return;
        // fade-with-words-one (22:12): the fade's only motion is its block's own entrance, in motion.css, each pinned to its
        // block's first words by fadeWithWords.test.ts; reveal.css gives it none, and nothing transitions it
        rule.walkDecls((d) => expect(sheet === motion ? /^transition/.test(d.prop) : /^(animation|transition)/.test(d.prop), rule.selector).toBe(false));
      });
    }
  });
});

describe('no fade over the photo at rest: the legend, the ask bar and the views row stand on the photo without one', () => {
  const fadeRules = () => {
    const found: string[] = [];
    postcss.parse(css).walkRules((rule) => {
      for (const selector of rule.selectors) {
        const s = selector.replace(/\s+/g, ' ').trim();
        if (/::before$/.test(s) && /reveal-legend|ask-bar|reveal-nav|reveal-phone-top/.test(s)) found.push(s);
      }
    });
    return found;
  };
  const screen = (moment: string | null, predsky = false, withPhoto = true) => {
    document.body.innerHTML = '';
    const photo = document.createElement('div');
    photo.className = withPhoto ? 'reveal-photo' : 'alb-observatory';
    if (moment) {
      const status = document.createElement('div');
      status.className = 'reveal-status';
      status.dataset.moment = moment;
      photo.appendChild(status);
    }
    const legend = document.createElement('div');
    legend.className = 'reveal-legend';
    photo.appendChild(legend);
    document.body.appendChild(photo);
    if (predsky) {
      const sky = document.createElement('div');
      sky.className = 'predsky';
      document.body.appendChild(sky);
    }
    const ask = document.createElement('div');
    ask.className = 'ask-bar';
    document.body.appendChild(ask);
    const nav = document.createElement('div');
    nav.className = 'reveal-nav';
    document.body.appendChild(nav);
    return { legend, ask, nav };
  };
  const faded = (el: Element) => fadeRules().filter((s) => el.matches(s.replace(/::before$/, '')));
  afterEach(() => void (document.body.innerHTML = ''));

  it('draws no fade behind the legend, the ask bar or the views row on the photo, whatever its status says', () => {
    expect(fadeRules().length).toBeGreaterThan(0);
    for (const moment of ['photo', 'unmatched', null]) {
      const { legend, ask, nav } = screen(moment);
      expect(faded(legend), String(moment)).toEqual([]);
      expect(faded(ask), String(moment)).toEqual([]);
      expect(faded(nav), String(moment)).toEqual([]);
    }
  });

  it("draws no fade behind the phone's top bar on the photo at rest; keeps it over Real sky and the flight", () => {
    const phone = (moment: string | null, flight = false) => {
      document.body.innerHTML = '';
      const photo = document.createElement('div');
      photo.className = 'reveal-photo is-phone';
      if (moment) {
        const status = document.createElement('div');
        status.className = 'reveal-status is-phone';
        status.dataset.moment = moment;
        photo.appendChild(status);
      }
      if (flight) {
        const readout = document.createElement('div');
        readout.className = 'flight-readout is-phone';
        photo.appendChild(readout);
      }
      document.body.appendChild(photo);
      const top = document.createElement('div');
      top.className = 'reveal-phone-top';
      document.body.appendChild(top);
      return top;
    };
    for (const moment of ['photo', 'unmatched', null]) expect(faded(phone(moment)), String(moment)).toEqual([]);
    expect(faded(phone('real-sky'))).toEqual([PHONE_TOP_FADE]);
    expect(faded(phone(null, true))).toEqual([PHONE_TOP_FADE]);
  });

  it("keeps the views row's over Real sky, there and the predicted sky, and where no photo is under it", () => {
    expect(faded(screen('real-sky').nav)).toEqual([VIEWS_DRAWN]);
    expect(faded(screen('there').nav)).toEqual([VIEWS_DRAWN]);
    expect(faded(screen(null, true).nav)).toEqual([VIEWS_DRAWN]);
    expect(faded(screen(null, false, false).nav)).toEqual([VIEWS_NO_PHOTO]);
  });

  it('keeps both over Real sky, and the ask bar\'s over there and the predicted sky', () => {
    const real = screen('real-sky');
    expect(faded(real.legend)).toEqual([LEGEND_FADE]);
    expect(faded(real.ask)).toEqual([ASK_FADE]);
    expect(faded(screen('there').ask)).toEqual([ASK_FADE]);
    expect(faded(screen(null, true).ask)).toEqual([ASK_FADE]);
  });
});

describe('the fades stand on the elements the screens draw', () => {
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
    vi.restoreAllMocks();
  });
  const show = (session: ObservationSession, phone: boolean, extra: Record<string, unknown> = {}) =>
    act(() =>
      root.render(
        createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: phone ? 390 : 1440, viewportHeightPx: phone ? 844 : 900, phone, ...extra } as unknown as Parameters<typeof RevealPhoto>[0])
      )
    );

  it("Real sky's column on the laptop takes the fade; the phone's keeps its own ground", () => {
    const session = poseSession(sample('plate_calibrated'));
    show(session, false, { realSky: true });
    expect(container.querySelector(".reveal-status[data-moment='real-sky']:not(.is-phone)")).not.toBeNull();
    expect(container.querySelector('.reveal-legend')).not.toBeNull();
    show(session, true, { realSky: true });
    expect(container.querySelector(".reveal-status[data-moment='real-sky']:not(.is-phone)")).toBeNull();
  });

  it('the pill row says when it has stepped up above the ask bar, and not at rest', () => {
    const ask = document.createElement('div');
    ask.dataset.testid = 'ask-bar';
    document.body.appendChild(ask);
    const rectOf = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
    let askLeft = 900;
    ask.getBoundingClientRect = () => rectOf(askLeft, 820, askLeft + 400, 868);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.matches('[data-testid="flight-pills"]') ? rectOf(36, 832, 336, 868) : rectOf(0, 0, 0, 0);
    });
    try {
      act(() => root.render(createElement(FlightPills, { craftPill: null, cone: false, onCone: () => {} })));
      expect(container.querySelector('[data-testid="flight-pills"]')!.hasAttribute('data-stepped')).toBe(false);
      // The ask bar now starts left of where the row ends: the row steps up onto its own row
      askLeft = 200;
      act(() => void window.dispatchEvent(new Event('resize')));
      expect(container.querySelector('[data-testid="flight-pills"]')!.hasAttribute('data-stepped')).toBe(true);
    } finally {
      ask.remove();
    }
  });
});

describe('names clear the new reaches; the legend is a place the marks keep clear of', () => {
  const rectOf = (left: number, top: number, right: number, bottom: number) => () => ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
  const el = (tag: string, className: string, data: Record<string, string> = {}) => {
    const e = document.createElement(tag);
    e.className = className;
    Object.assign(e.dataset, data);
    return e;
  };

  it("measures Real sky's column, the ask bar and the legend with their reach", () => {
    const real = el('div', 'reveal-status', { testid: 'reveal-status', moment: 'real-sky' });
    real.getBoundingClientRect = rectOf(36, 70, 336, 170);
    expect(fadeOf(real)).toEqual({ x0: 0, x1: 380, y0: 26, y1: 214 });
    const phoneReal = el('div', 'reveal-status is-phone', { testid: 'reveal-status', moment: 'real-sky' });
    phoneReal.getBoundingClientRect = rectOf(20, 600, 370, 700);
    expect(fadeOf(phoneReal)).toBeUndefined();
    const ask = el('div', 'ask-bar', { testid: 'ask-bar' });
    ask.getBoundingClientRect = rectOf(560, 820, 880, 868);
    expect(fadeOf(ask)).toEqual({ x0: 516, x1: 924, y0: 776, y1: 900 });
    const phoneAsk = el('div', 'ask-bar is-phone', { testid: 'ask-bar' });
    phoneAsk.getBoundingClientRect = rectOf(20, 760, 370, 804);
    expect(fadeOf(phoneAsk)).toBeUndefined();
    const legend = el('div', 'reveal-legend', { testid: 'reveal-legend' });
    legend.getBoundingClientRect = rectOf(36, 760, 220, 864);
    expect(fadeOf(legend)).toEqual({ x0: 0, x1: 264, y0: 716, y1: 900 });
  });

  it('keeps the legend clear on the laptop, measured by its words as a block', () => {
    const legend = el('div', 'reveal-legend', { testid: 'reveal-legend' });
    expect(REVEAL_KEEP_CLEAR.some((s) => legend.matches(s))).toBe(true);
    expect(KEEP_CLEAR_BLOCKS.some((s) => legend.matches(s))).toBe(true);
  });
});
