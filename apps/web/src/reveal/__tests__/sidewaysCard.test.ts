// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { dispatchAlbedianAction } from '@albedian/observation';
import { describe, expect, it } from 'vitest';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { SIDEWAYS_QUERY } from '../../components/phoneSheet.js';
import { PredictedSkyScreen } from '../PredictedSkyScreen.js';

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
    if (at.params !== SIDEWAYS_QUERY) return;
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
    // By the foot, as Ask out loud: the smaller counts on the foot's line, the big count 54 px above them (their upright gap)
    expect(inSideways('.reveal-phone-counts__big')).toEqual({ left: 'max(24px, env(safe-area-inset-left))', top: 'auto', bottom: 'calc(max(12px, env(safe-area-inset-bottom)) + 54px)' });
    expect(inSideways('.reveal-phone-counts__more')).toEqual({ left: 'max(24px, env(safe-area-inset-left))', top: 'auto', bottom: 'max(12px, env(safe-area-inset-bottom))' });
    expect(inSideways('.ask-bar.is-phone')).toEqual({ top: 'auto', bottom: 'max(12px, env(safe-area-inset-bottom))' });
    expect(inSideways('.ask-bar.is-phone:not(:has(> .look-main))')).toEqual({ left: 'auto', right: 'max(20px, env(safe-area-inset-right))', transform: 'none' });
  });
});

// phone-sideways-two version two (AUDIT-C's FAIL; the lead's line stamped 07:39: the tag rule decides it): on the
// predicted sky held sideways its own top bar stands at 12 like the reveal's, so it never crosses the Predicted tag; and
// with a star's card open the sheet stands at the trailing side (the card's place on that sky, as the laptop's), while
// the status block (the Predicted tag, its sentence and the Simulated Milky Way's line) stands whole by the foot at the
// leading side, 16 px clear of the sheet
describe('the predicted sky on a phone held on its side', () => {
  it('stands its top bar 12 px from the top inside the side inset', () => {
    expect(inSideways('.predsky-top')).toEqual({ top: '12px', left: 'max(20px, env(safe-area-inset-left))' });
  });

  // With no card, the status stands by its foot inside the side safe areas, capped 8 px under the top bar's foot
  // (12 + 44 + 8 = 64; 64 + 88 = 152): taller than its room, it takes the pointer and scrolls inside itself (item 7)
  // with the Predicted and Simulated tag row held at its top over field black, so both tags stay whole on every frame
  it('stands the status under the top bar, scrollable by touch with its tag row held whole at its top', () => {
    expect(inSideways('.predsky.is-phone .predsky-status:not(.is-above-card)')).toEqual({
      left: 'max(24px, env(safe-area-inset-left))',
      right: 'max(24px, env(safe-area-inset-right))',
      'max-height': 'calc(100% - 152px)',
      'overflow-x': 'hidden',
      'overflow-y': 'auto',
      'overscroll-behavior': 'contain',
      'pointer-events': 'auto',
      background: 'rgba(0, 0, 0, 0.82)',
    });
    expect(inSideways('.predsky.is-phone .predsky-status:not(.is-above-card)::before')).toEqual({ content: 'none' });
    expect(inSideways('.predsky.is-phone .predsky-status:not(.is-above-card) > .predsky-status__tags')).toEqual({
      position: 'sticky',
      top: '0',
      'z-index': '1',
      'padding-bottom': '8px',
      'margin-bottom': '-8px',
      background: 'var(--look-field)',
    });
    expect(inSideways('.predsky.is-phone .predsky-status:not(.is-above-card) > .look-tag')).toEqual({});

    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const base = poseSession(sample('horizon_predicted_only'));
    const sky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'req_sideways_sky',
      expectedViewRevision: base.viewRevision,
      mode: 'predicted',
      observationSolutionId: base.observationSolution!.id,
    }).session;
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    try {
      act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, phone: true })));
      expect(host.querySelector('.predsky.is-phone .predsky-status:not(.is-above-card) > .look-tag')).toBeNull();
      const heldTags = host.querySelector('.predsky.is-phone .predsky-status:not(.is-above-card) > .predsky-status__tags');
      expect(heldTags).not.toBeNull();
      expect([...heldTags!.querySelectorAll('.look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual(['predicted', 'simulated']);
    } finally {
      act(() => root.unmount());
      host.remove();
    }
  });

  it('with a card open, stands the sheet at the trailing side and the status whole beside it, by the foot', () => {
    expect(inSideways('.predsky.is-phone .reveal-card.is-phone')).toEqual({ left: 'auto', right: 'max(12px, env(safe-area-inset-right))' });
    expect(inSideways('.predsky.is-phone .predsky-status.is-above-card')).toEqual({
      left: 'max(24px, env(safe-area-inset-left))',
      right: 'calc(max(12px, env(safe-area-inset-right)) + 390px + 16px)',
      bottom: 'max(12px, env(safe-area-inset-bottom))',
    });
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
