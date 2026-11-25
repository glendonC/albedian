// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LOOK_INK_LEVELS } from '@albedian/design-system';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';

// The Real sky layer draws with WebGL 2, which jsdom has not
vi.mock('../RealSkyLayer.js', async (importOriginal) => ({ ...(await importOriginal<typeof import('../RealSkyLayer.js')>()), RealSkyLayer: () => null }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// words-on-photo-one (UIUX-words-over-photo-READY.md, DECIDED-BY-LEAD at the line stamped 23:32; not the owner's own yes).
// Kind one, the design system's: the faint ink from 0.44 to 0.48, the least alpha that measures 4.5 to 1 over the app's
// field. Kind two (B): while the person's photograph shows at rest, each word over it stands on its own body, drawn as it
// looks over the app's black, so a bright part of the photo never shows through behind its letters: the tags' pills solid
// with their class tint kept, an outlined pill's body black inside its outline, the ask bar's body opaque, the legend's
// title on its own line box; no body wider than its word, nothing that reads as a panel. Over the drawn views (Real sky,
// there, the predicted sky) their fades stand, and nothing here changes. The phone's top bar fade comes off the photograph
// at rest too (fade-off-photo-two, taken in).

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const tokens = fs.readFileSync(path.resolve(here, '../../styles/look-tokens.css'), 'utf8');

/** The photograph at rest: a reveal on screen and none of the drawn views. */
const AT_REST = ":root:has(.reveal-photo):not(:has(.reveal-status:is([data-moment='real-sky'], [data-moment='there']), .flight-readout, .predsky))";
const CLASSES = ['observed', 'catalog', 'predicted', 'simulated'] as const;
const tagRule = (cls: string) => `${AT_REST} .reveal-photo .look-tag--${cls}:not(.reveal-card .look-tag, .reveal-answer .look-tag)`;
const PILL = [
  `${AT_REST} .reveal-photo .look-second:not(.reveal-card .look-second, .is-on)`,
  `${AT_REST} .reveal-nav .look-views__item:not(.is-current)`,
  `${AT_REST} .reveal-phone-top .look-views__item:not(.is-current)`,
];
const PILL_WASH = [
  `${AT_REST} .reveal-photo .look-second:is(:hover:not(:disabled), .is-on):not(.reveal-card .look-second)`,
  `${AT_REST} .reveal-nav .look-views__item:hover:not(:disabled):not(.is-current)`,
  `${AT_REST} .reveal-phone-top .look-views__item:hover:not(:disabled):not(.is-current)`,
];
const ASK = [`${AT_REST} .ask-bar__ask:not(.is-listening)`, `${AT_REST} .ask-bar__type`];
const ASK_WASH = [`${AT_REST} .ask-bar__ask:hover:not(:disabled):not(.is-listening)`, `${AT_REST} .ask-bar__type:hover:not(:disabled)`];
const TITLE = `${AT_REST} .reveal-legend__word`;

/** The declarations of the rule whose selector list is exactly these selectors. */
function ruleOf(selectors: string[]): Record<string, string> | undefined {
  let found: Record<string, string> | undefined;
  postcss.parse(css).walkRules((rule) => {
    if (rule.parent?.type !== 'root') return;
    const list = rule.selectors.map((s) => s.replace(/\s+/g, ' ').trim());
    if (list.length !== selectors.length || !selectors.every((s) => list.includes(s))) return;
    found = {};
    rule.walkDecls((d) => void (found![d.prop] = d.value.replace(/\s+/g, ' ')));
  });
  return found;
}

describe('kind one: the faint ink at 0.48, in the design system and the web token alike', () => {
  it('raises ink3 from 0.44 to 0.48, the least alpha that measures 4.5 to 1 over the field', () => {
    expect(LOOK_INK_LEVELS.ink3).toEqual([244, 243, 239, 0.48]);
    expect(tokens).toContain('--look-ink-3: rgba(244, 243, 239, 0.48);');
    // WCAG's contrast of the ink composited over #000000
    const lin = (c: number) => (c / 255 <= 0.03928 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
    const [r, g, b, a] = LOOK_INK_LEVELS.ink3;
    const lum = 0.2126 * lin(r * a) + 0.7152 * lin(g * a) + 0.0722 * lin(b * a);
    expect((lum + 0.05) / 0.05).toBeGreaterThanOrEqual(4.5);
    const [r0, g0, b0] = [244 * 0.47, 243 * 0.47, 239 * 0.47];
    expect((0.2126 * lin(r0) + 0.7152 * lin(g0) + 0.0722 * lin(b0) + 0.05) / 0.05).toBeLessThan(4.5);
  });
});

describe('kind two: each word over the photograph at rest on its own body, drawn as over the app\'s black', () => {
  it('makes each tag pill solid with its class tint kept, the tint laid over the field', () => {
    for (const cls of CLASSES) {
      expect(ruleOf([tagRule(cls)]), cls).toEqual({ background: `linear-gradient(var(--look-ev-${cls}-tint), var(--look-ev-${cls}-tint)), var(--look-field)` });
    }
  });

  it("gives an outlined pill a black body inside its outline, and its hover wash over the black", () => {
    expect(ruleOf(PILL)).toEqual({ 'background-color': 'var(--look-field)' });
    expect(ruleOf(PILL_WASH)).toEqual({ background: 'linear-gradient(var(--look-wash), var(--look-wash)), var(--look-field)' });
  });

  it("makes the ask bar's body opaque, its approved 55 percent body as it looks over black, its hover wash likewise", () => {
    expect(ruleOf(ASK)).toEqual({
      background: 'linear-gradient(rgba(10, 10, 12, 0.55), rgba(10, 10, 12, 0.55)), var(--look-field)',
      '-webkit-backdrop-filter': 'none',
      'backdrop-filter': 'none',
    });
    expect(ruleOf(ASK_WASH)).toEqual({ background: 'linear-gradient(var(--look-wash), var(--look-wash)), var(--look-field)' });
  });

  // The title's rule as ruled at the line stamped 00:40: no body, the least dark rim round its letters that still measures
  // the bar (measured live: .lanetools/unit-column-fade/walk_rim.mjs)
  it("gives the legend's title no body, only a thin dark rim round its letters", () => {
    expect(ruleOf([TITLE])).toEqual({ 'text-shadow': '1px 0 #000, -1px 0 #000, 0 1px #000, 0 -1px #000, 1px 1px #000, -1px -1px #000, 1px -1px #000, -1px 1px #000, 0 0 2px #000' });
  });
});

describe('where the bodies stand: the photograph at rest, never a drawn view, never a card or an answer', () => {
  afterEach(() => void (document.body.innerHTML = ''));
  const el = (parent: Element, className: string, attrs: Record<string, string> = {}) => {
    const e = document.createElement('div');
    e.className = className;
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    parent.appendChild(e);
    return e;
  };
  /** A screen: the reveal with a status of this moment (or a drawn view's marker), and one of each thing under test. */
  const screen = (view: 'photo' | 'unmatched' | 'real-sky' | 'there' | 'phone-flight' | 'predsky') => {
    document.body.innerHTML = '';
    const photo = el(document.body, 'reveal-photo');
    if (view === 'predsky') el(document.body, 'predsky');
    else if (view === 'phone-flight') el(photo, 'flight-readout is-phone');
    else el(photo, 'reveal-status', { 'data-moment': view });
    const legend = el(photo, 'reveal-legend');
    const title = el(el(legend, 'reveal-legend__title'), 'reveal-legend__word');
    const tag = el(legend, 'look-tag look-tag--catalog');
    const restore = el(el(photo, 'reveal-status'), 'look-second');
    const card = el(photo, 'reveal-card');
    const cardTag = el(card, 'look-tag look-tag--catalog');
    const cardButton = el(card, 'look-second');
    const answerTag = el(el(photo, 'reveal-answer'), 'look-tag look-tag--predicted');
    const nav = el(document.body, 'reveal-nav');
    const pill = el(nav, 'look-views__item');
    const current = el(nav, 'look-views__item is-current');
    const phonePill = el(el(document.body, 'reveal-phone-top'), 'look-views__item');
    const bar = el(document.body, 'ask-bar');
    const ask = el(bar, 'look-second ask-bar__ask');
    const listening = el(bar, 'look-second ask-bar__ask is-listening');
    const type = el(bar, 'look-second ask-bar__type');
    return { title, tag, restore, cardTag, cardButton, answerTag, pill, current, phonePill, ask, listening, type };
  };
  const bodied = (e: Element) =>
    [TITLE, tagRule('catalog'), tagRule('predicted'), ...PILL, ...ASK].filter((s) => e.matches(s));

  it('stands every body on the photograph at rest, whatever its status says', () => {
    for (const view of ['photo', 'unmatched'] as const) {
      const s = screen(view);
      expect(bodied(s.title), view).toEqual([TITLE]);
      expect(bodied(s.tag), view).toEqual([tagRule('catalog')]);
      expect(bodied(s.restore), view).toEqual([PILL[0]]);
      expect(bodied(s.pill), view).toEqual([PILL[1]]);
      expect(bodied(s.phonePill), view).toEqual([PILL[2]]);
      expect(bodied(s.ask), view).toEqual([ASK[0]]);
      expect(bodied(s.type), view).toEqual([ASK[1]]);
      // The current pill is solid ink already, the listening button fills; a card and an answer have their own ground
      for (const kept of [s.current, s.listening, s.cardTag, s.cardButton, s.answerTag]) expect(bodied(kept), view).toEqual([]);
    }
  });

  it('stands none over a drawn view, where its fade stands', () => {
    for (const view of ['real-sky', 'there', 'phone-flight', 'predsky'] as const) {
      const s = screen(view);
      for (const e of Object.values(s)) expect(bodied(e), `${view}: ${e.className}`).toEqual([]);
    }
  });
});

describe("the legend's title draws its word in its own box", () => {
  it('wraps the title in its word, which the rule above gives its line box', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    try {
      act(() =>
        root.render(
          createElement(RevealPhoto, { session: poseSession(sample('plate_calibrated')), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900 } as unknown as Parameters<typeof RevealPhoto>[0])
        )
      );
      const word = host.querySelector('.reveal-legend__title > .reveal-legend__word');
      expect(word?.textContent).toBe('In this photo');
      expect(host.querySelector('.reveal-legend__title')!.textContent).toBe('In this photo');
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });
});
