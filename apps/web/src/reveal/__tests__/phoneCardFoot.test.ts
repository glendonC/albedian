import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { phoneCardTopPx } from '../RevealPhoto.js';

// phone-card-foot-one (DECIDED-BY-LEAD at the line stamped 02:11, on FIELD's check in Safari on an iPhone: a window of 402
// by 714 that does not scroll, the picked star's card's Go there at 753 to 797, below its foot; UIUX-phone-textsize-READY.md
// item 4): the card is a sheet standing by its foot from the window's foot, 30 px up, never at a fixed top; its height the
// approved 345 px, up to the room under the top bar; past that its body scrolls inside it under its tag row and Close, its
// buttons at the scroll's foot; its button row wraps rather than passing the window's edge. Nothing moves at 390 by 844.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const decls = (selector: string) => {
  const found: Record<string, string> = {};
  postcss.parse(css).walkRules((rule) => {
    if (rule.parent?.type !== 'root' || !rule.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(selector)) return;
    rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
  });
  return found;
};
/** A length as laid out in a window this tall: px, or calc(), min() or max() of 100% and px. */
function at(value: string, h: number): number {
  const px = /^(-?[\d.]+)px$/.exec(value);
  if (px) return Number(px[1]);
  const calc = /^calc\(100% ([+-]) ([\d.]+)px\)$/.exec(value);
  if (calc) return calc[1] === '-' ? h - Number(calc[2]) : h + Number(calc[2]);
  const fn = /^(min|max)\((.+), (calc\(.+\)|[\d.]+px)\)$/.exec(value);
  if (fn) {
    const a = at(fn[2]!.trim(), h);
    const b = at(fn[3]!.trim(), h);
    return fn[1] === 'min' ? Math.min(a, b) : Math.max(a, b);
  }
  throw new Error(`not a length this test reads: ${value}`);
}
// Upright and sideways: FIELD's Safari window (402 by 714), MOBILE's Open sheet (402 by 650), both turned, and the design
const HEIGHTS = [402, 500, 550, 650, 714, 800, 844, 900];
/** Go there's row in the approved card: 284 px under its top, 44 high (#p-pick: the card at 469, the row at 753). */
const ACTS_TOP_IN_CARD = 753 - 469;

describe("the picked star's card stands by its foot", () => {
  it('stands 30 px above the window\'s foot, 345 high, at 469 in 390 by 844 as approved', () => {
    const card = decls('.reveal-card.is-phone');
    expect(card.top).toBe('auto');
    expect(at(card.bottom!, 844)).toBe(30);
    expect(844 - 30 - at(card.height!, 844)).toBe(469);
    expect(phoneCardTopPx(844)).toBe(469);
  });

  it('never stands above the room under the top bar, nor past the window\'s foot, at any height', () => {
    for (const h of HEIGHTS) {
      const card = decls('.reveal-card.is-phone');
      const bottom = h - at(card.bottom!, h);
      const height = at(card.height!, h);
      const top = bottom - height;
      expect(bottom, String(h)).toBe(h - 30);
      expect(top, String(h)).toBeGreaterThanOrEqual(118);
      expect(height, String(h)).toBeLessThanOrEqual(345);
      expect(phoneCardTopPx(h), String(h)).toBe(top);
    }
  });

  it("shows Go there without a scroll in every upright window from 500 up, and holds it at its body's foot below that", () => {
    for (const h of HEIGHTS) {
      const height = at(decls('.reveal-card.is-phone').height!, h);
      const goThereBottom = phoneCardTopPx(h) + ACTS_TOP_IN_CARD + 44;
      if (h >= 500) expect(goThereBottom, String(h)).toBeLessThan(h - 30);
      // Shorter (a phone turned sideways), the body scrolls inside the card to it; the card itself stays in the window
      else expect(height, String(h)).toBeLessThan(ACTS_TOP_IN_CARD + 44);
    }
    expect(decls('.reveal-card.is-phone .reveal-card__scroll')['overflow-y']).toBe('auto');
  });

  it("wraps the button row rather than passing the window's edge", () => {
    expect(decls('.reveal-card.is-phone .reveal-card__acts')).toEqual({ 'margin-top': '20px', 'flex-wrap': 'wrap' });
  });

  it('stands the blocks that step above the card 16 px above its top, wherever that is', () => {
    for (const selector of ['.reveal-phone-estimate.is-above-card', '.predsky.is-phone .predsky-status.is-above-card']) {
      for (const h of HEIGHTS) {
        const foot = h - at(decls(selector).bottom!, h);
        expect(foot, `${selector} ${h}`).toBe(phoneCardTopPx(h) - 16);
      }
    }
  });
});
