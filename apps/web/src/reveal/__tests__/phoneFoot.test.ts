import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { PHONE_DESIGN_HEIGHT_PX, PHONE_SLOT, phoneEndsMask, phoneSlotCrop, phoneSlotIn, phoneSlotRect } from '../phoneSlot.js';

// phone-foot-one (DECIDED-BY-LEAD at the line stamped 00:31, on AUDIT-C's note: at phone width the ask bar stood at a fixed
// top of 772, so in any window shorter than about 820 px Ask out loud and Back to Moment lay below the window's foot;
// UIUX-phone-textsize-READY.md item 2 at the default text size): the reveal's foot block (the counts with their tags, the
// estimate's and the answer's blocks, Ask out loud and Back to Moment, the clip's player, and the Restore reached by
// scrolling under them) stands by its foot from the window's own foot at every window height, where it stood at 390 by
// 844; the photo above gives the room, its slot's foot rising with the window's in a shorter window and keeping its 520
// in a taller one. Nothing a person must press is below the window.

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
/** A length as the page lays it out in a window this tall: px, or a calc() or max() of 100% and px. */
function at(value: string, heightPx: number): number {
  const px = /^(-?[\d.]+)px$/.exec(value);
  if (px) return Number(px[1]);
  const calc = /^calc\(100% ([+-]) ([\d.]+)px\)$/.exec(value);
  if (calc) return calc[1] === '-' ? heightPx - Number(calc[2]) : heightPx + Number(calc[2]);
  const max = /^max\(([^,]+), (.+)\)$/.exec(value);
  if (max) return Math.max(at(max[1]!.trim(), heightPx), at(max[2]!.trim(), heightPx));
  throw new Error(`not a length this test reads: ${value}`);
}
const HEIGHTS = [550, 600, 640, 667, 700, 740, 780, 800, 820, 844, 870, 900];

describe("the photo's slot gives the room in a shorter window", () => {
  it('is 390 by 520 from y 118 at 844 and in a taller window, its foot rising with the window in a shorter one', () => {
    expect(phoneSlotIn(PHONE_DESIGN_HEIGHT_PX, 390)).toEqual(PHONE_SLOT);
    expect(phoneSlotIn(900, 390)).toEqual(PHONE_SLOT);
    expect(phoneSlotIn(700, 390)).toEqual({ topPx: 118, heightPx: 376 });
    expect(phoneSlotIn(550, 390)).toEqual({ topPx: 118, heightPx: 226 });
  });

  it('covers the shorter slot with the photo, centred on it, and fades and bounds it there', () => {
    const crop = phoneSlotCrop(3024, 4032, 390, 700);
    const rect = sourcePhotoDisplayRect(3024, 4032, 390, 700, crop);
    expect(rect.leftPx).toBeCloseTo(0, 6);
    expect(rect.widthPx).toBeCloseTo(390, 6);
    expect(rect.topPx + rect.heightPx / 2).toBeCloseTo(118 + 376 / 2, 6);
    expect(rect.topPx).toBeLessThanOrEqual(118 + 1e-6);
    expect(rect.topPx + rect.heightPx).toBeGreaterThanOrEqual(494 - 1e-6);
    expect(phoneSlotRect(rect, 390, 700)).toMatchObject({ topPx: 118, heightPx: 376 });
    // The mask clears at the slot's foot in this window, not the design's
    const mask = phoneEndsMask(rect, 700);
    const bottomInBox = 494 - rect.topPx;
    expect(mask.endsWith(`transparent ${Math.round(bottomInBox * 10) / 10}px)`)).toBe(true);
  });
});

describe("the foot block stands by its foot from the window's foot", () => {
  it('stands exactly where it stood at 390 by 844', () => {
    expect(at(decls('.reveal-phone-counts__big').top!, 844)).toBe(660);
    expect(at(decls('.reveal-phone-counts__more').top!, 844)).toBe(714);
    expect(844 - at(decls('.reveal-phone-estimate').bottom!, 844)).toBe(756);
    expect(844 - at(decls('.reveal-phone-estimate.is-clip').bottom!, 844)).toBe(638);
    expect(844 - at(decls('.reveal-answer.is-phone').bottom!, 844)).toBe(756);
    expect(at(decls('.ask-bar.is-phone').top!, 844)).toBe(772);
    expect(at(decls('.clip-player.is-phone').top!, 844)).toBe(676);
    expect(at(decls('.restore-phone-rest').top!, 844)).toBe(832);
    expect(at(decls('.reveal-phone-room').height!, 844)).toBe(936);
  });

  it('keeps Ask out loud and Back to Moment whole inside every window from 550 to 900, 28 px above its foot', () => {
    for (const h of HEIGHTS) {
      const askTop = at(decls('.ask-bar.is-phone').top!, h);
      // The phone's buttons are 44 px high (--look-h-phone)
      expect(askTop + 44, String(h)).toBe(h - 28);
      expect(askTop, String(h)).toBeGreaterThan(0);
    }
  });

  it('keeps the counts under the photo and over Ask out loud, the estimate and the answer 16 px above it, at every height', () => {
    for (const h of HEIGHTS) {
      const slot = phoneSlotIn(h, 390);
      const slotFoot = slot.topPx + slot.heightPx;
      const askTop = at(decls('.ask-bar.is-phone').top!, h);
      const big = at(decls('.reveal-phone-counts__big').top!, h);
      const more = at(decls('.reveal-phone-counts__more').top!, h);
      expect(big, String(h)).toBeGreaterThanOrEqual(slotFoot + 22);
      // The second row's 15 px line under the first's 40 px one, both above Ask out loud
      expect(more, String(h)).toBe(big + 54);
      expect(more + 15, String(h)).toBeLessThan(askTop);
      expect(h - at(decls('.reveal-phone-estimate').bottom!, h), String(h)).toBe(askTop - 16);
      expect(h - at(decls('.reveal-answer.is-phone').bottom!, h), String(h)).toBe(askTop - 16);
      // A clip's estimate stands at the clip slot's foot, and its player under the slot, above Ask out loud
      expect(h - at(decls('.reveal-phone-estimate.is-clip').bottom!, h), String(h)).toBe(slotFoot);
      const player = at(decls('.clip-player.is-phone').top!, h);
      expect(player, String(h)).toBeGreaterThanOrEqual(slotFoot + 38);
      expect(player + 44, String(h)).toBeLessThan(askTop);
    }
  });

  it('keeps the offered Restore just under the window, reached by the screen\'s scroll, as at 844', () => {
    for (const h of HEIGHTS) {
      const rest = at(decls('.restore-phone-rest').top!, h);
      const room = at(decls('.reveal-phone-room').height!, h);
      expect(rest, String(h)).toBe(h - 12);
      // The scroll's room holds Restore whole with the design's 60 px under it
      expect(room - (rest + 44), String(h)).toBe(60);
    }
  });
});
