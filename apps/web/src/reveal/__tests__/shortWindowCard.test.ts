import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

// phone-sideways-one (DECIDED-BY-LEAD at the line stamped 04:47, rule one; FIELD's break: in a short window read as a
// mouse window, 874 by 370 or 402, the laptop's star card ran past the window's foot with Go there at 456 to 492 and
// nothing scrolled): at every window size nothing a person can press lies outside the window unless a scroll reaches
// it. The laptop's card stands from its top (118) to 16 px above the ask bar at most, its body scrolling inside under
// its tag row and Close, so Go there is inside the window or one scroll inside the card. At the design size it is
// shorter than that room, so nothing moves.

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
const px = (v: string) => Number(/^(-?[\d.]+)px$/.exec(v)![1]);

describe("the laptop's star card in a short window", () => {
  it('stands from its top to 16 px above the ask bar at most, its body scrolling inside', () => {
    expect(decls('.reveal-card:not(.is-phone)')).toEqual({ display: 'flex', 'flex-direction': 'column', 'max-height': 'calc(100% - 210px)' });
    expect(decls('.reveal-card:not(.is-phone) .reveal-card__scroll')).toEqual({ 'min-height': '0', 'overflow-y': 'auto', 'overscroll-behavior': 'contain' });
  });

  it('keeps its foot above the ask bar at FIELD\'s sideways sizes, the small phone\'s and the design size', () => {
    const top = px(decls('.reveal-card').top!);
    const askBottom = px(decls('.ask-bar').bottom!);
    const askHigh = 44;
    const cap = Number(/^calc\(100% - ([\d.]+)px\)$/.exec(decls('.reveal-card:not(.is-phone)')['max-height'] ?? '')?.[1]);
    for (const h of [370, 375, 402, 500, 650, 900]) {
      const foot = top + (h - cap);
      expect(foot, String(h)).toBe(h - askBottom - askHigh - 16);
    }
  });
});
