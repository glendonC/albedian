import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { LANDSCAPE_PHONE_QUERY } from '../../components/phoneSheet.js';

// phone-sideways-two (UIUX-phone-sideways-READY.md item 5, RULED at the lead's line stamped 06:27; Apple's sheet
// attached to the bottom edge only in a compact-height window): on a phone held on its side the star card is the phone's
// foot sheet attached to the bottom edge, centred, its width the upright sheet's 390, its height its content's up to the
// window less the top bar's foot (12 + 44) and 8 px; past that its body scrolls inside under its tag row and Close, and
// Go there and Original pixels stay pinned at its foot, always inside; the foot's safe area (20 px on the 17 Pro) is kept.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const inSideways = (selector: string) => {
  const found: Record<string, string> = {};
  postcss.parse(css).walkAtRules('media', (at) => {
    if (at.params !== LANDSCAPE_PHONE_QUERY) return;
    at.walkRules((rule) => {
      if (!rule.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(selector)) return;
      rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
    });
  });
  return found;
};

describe('the top bar on a phone held on its side', () => {
  // Item 3: the close circle at the leading top and the views pills at the trailing top, 12 px from the top (the island
  // stands at the side, not the top), inside the side insets (62 px on the 17 Pro held sideways)
  it('stands 12 px from the top, inside the side safe areas', () => {
    expect(inSideways('.reveal-phone-top')).toEqual({ top: '12px', left: 'max(20px, env(safe-area-inset-left))', right: 'max(20px, env(safe-area-inset-right))' });
  });
});

describe('the foot row on a phone held on its side', () => {
  // Item 4: the counts with their tags at the leading foot, stacked (the record's fallback where they and Ask out loud
  // do not share a row), Ask out loud at the trailing foot; item 6: Back to Moment centred at the foot; every pinned
  // control inside the safe areas (62 px at each side and 20 at the foot on the 17 Pro)
  it('stands the counts at the leading side and Ask out loud at the trailing foot, Back to Moment centred', () => {
    expect(inSideways('.reveal-phone-counts__big')).toEqual({ left: 'max(24px, env(safe-area-inset-left))' });
    expect(inSideways('.reveal-phone-counts__more')).toEqual({ left: 'max(24px, env(safe-area-inset-left))' });
    expect(inSideways('.ask-bar.is-phone')).toEqual({ top: 'auto', bottom: 'max(12px, env(safe-area-inset-bottom))' });
    expect(inSideways('.ask-bar.is-phone:not(:has(> .look-main))')).toEqual({ left: 'auto', right: 'max(20px, env(safe-area-inset-right))', transform: 'none' });
  });
});

describe('the star card on a phone held on its side', () => {
  it('is a sheet on the bottom edge, centred, 390 wide, as tall as its content up to the window less 64 px', () => {
    expect(inSideways('.reveal-card.is-phone')).toEqual({
      left: 'calc(50% - 195px)',
      right: 'auto',
      width: '390px',
      bottom: '0',
      height: 'auto',
      'max-height': 'calc(100% - 64px)',
      'border-radius': '28px 28px 0 0',
      'padding-bottom': 'env(safe-area-inset-bottom)',
    });
  });

  it('pins Go there and Original pixels at its foot while its body scrolls, on the sheet\'s own colour', () => {
    expect(inSideways('.reveal-card.is-phone .reveal-card__acts')).toEqual({ position: 'sticky', bottom: '0', 'z-index': '1', margin: '0', padding: '12px 0 14px', background: '#111112' });
    // Nothing fades the pinned row away
    expect(inSideways('.reveal-card.is-phone .reveal-card__scroll')).toEqual({ '-webkit-mask-image': 'none', 'mask-image': 'none' });
  });
});
