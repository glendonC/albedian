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
import { FADE_REACH } from '../keepClear.js';
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

/** A rule's declarations at the top level of reveal.css. */
const atRoot = (selector: string) => {
  const found: Record<string, string> = {};
  postcss.parse(css).walkRules((rule) => {
    if (rule.parent?.type !== 'root' || !rule.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(selector)) return;
    rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
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

  // predsky-sideways-status-one (UIUX-predsky-READY.md, "The predicted sky held sideways with no card: its status",
  // DECIDED-BY-LEAD 00:44 on 27 Nov; it replaces version two's flat box): with no card the status holds the card state's own
  // content, the two tags and the Milky Way line, in the card state's own column, standing by its foot 16 px above Back to
  // Moment, on D19's fade clearing over 44 px past its open edges, no box, no edge line; names under it and its fade leave
  it('stands the no-card status in the card state column, 16 px above Back to Moment, on the fade, with the card state content', () => {
    expect(atRoot('.predsky.is-phone .predsky-status.is-sideways')).toEqual({
      left: 'max(24px, env(safe-area-inset-left))',
      right: 'calc(max(12px, env(safe-area-inset-right)) + 390px + 16px)',
      bottom: 'calc(max(12px, env(safe-area-inset-bottom)) + 44px + 16px)',
      isolation: 'isolate',
    });
    const mask = 'linear-gradient(to left, transparent 0, #000 44px), linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)';
    expect(atRoot('.predsky.is-phone .predsky-status.is-sideways::before')).toEqual({
      left: '-24px',
      top: '-44px',
      bottom: '-44px',
      right: '-44px',
      background: 'rgba(0, 0, 0, 0.82)',
      '-webkit-mask-image': mask,
      '-webkit-mask-composite': 'source-in',
      'mask-image': mask,
      'mask-composite': 'intersect',
    });
    // Version two's box, its scroll and its held tag row are gone
    for (const gone of ['.predsky.is-phone .predsky-status:not(.is-above-card)', '.predsky.is-phone .predsky-status:not(.is-above-card)::before', '.predsky.is-phone .predsky-status:not(.is-above-card) > .predsky-status__tags']) {
      expect(inSideways(gone), gone).toEqual({});
    }
    expect(FADE_REACH.find((f) => f.place === '.predsky.is-phone .predsky-status.is-sideways')).toEqual({
      place: '.predsky.is-phone .predsky-status.is-sideways',
      faded: '.predsky.is-phone .predsky-status.is-sideways',
      reach: { left: 24, top: 44, right: 44, bottom: 44 },
    });

    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const base = poseSession(sample('horizon_predicted_only'));
    const sky = dispatchAlbedianAction(base, {
      type: 'sky.show',
      requestId: 'req_sideways_sky',
      expectedViewRevision: base.viewRevision,
      mode: 'predicted',
      observationSolutionId: base.observationSolution!.id,
    }).session;
    const window0 = { w: window.innerWidth, h: window.innerHeight };
    const at = (w: number, h: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
    };
    const host = document.createElement('div');
    document.body.appendChild(host);
    let root = createRoot(host);
    try {
      for (const [w, h] of [[874, 292], [874, 402], [667, 320]] as const) {
        at(w, h);
        act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, phone: true })));
        const status = host.querySelector('[data-testid="reveal-status"]')!;
        expect(status.className, `${w}x${h}`).toBe('reveal-status predsky-status is-sideways');
        expect([...status.children].map((c) => c.className), `${w}x${h}`).toEqual(['predsky-status__tags', 'predsky-status__sky']);
        expect([...status.querySelectorAll('.look-tag')].map((el) => el.getAttribute('data-evidence-class'))).toEqual(['predicted', 'simulated']);
        act(() => root.unmount());
        root = createRoot(host);
      }
      // With a card open on its side the status keeps the card state's own place, at the foot as built
      at(874, 292);
      const picked = sky.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!.id;
      act(() => root.render(createElement(PredictedSkyScreen, { session: { ...sky, selectedFindingId: picked }, onHome: () => {}, phone: true })));
      expect(host.querySelector('[data-testid="star-card"]')).not.toBeNull();
      expect(host.querySelector('[data-testid="reveal-status"]')!.className).toBe('reveal-status predsky-status is-above-card');
      act(() => root.unmount());
      root = createRoot(host);
      // Version two (AUDIT-C's D6): a phone held upright in a short window (a small iPhone in Safari, a phone with its
      // keyboard open) keeps the upright status, all its lines, no sideways class
      for (const [w, h] of [[390, 480], [360, 400]] as const) {
        at(w, h);
        act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, phone: true })));
        const upright = host.querySelector('[data-testid="reveal-status"]')!;
        expect(upright.className, `${w}x${h}`).toBe('reveal-status predsky-status');
        expect(upright.querySelector('.predsky-status__lead'), `${w}x${h}`).not.toBeNull();
        act(() => root.unmount());
        root = createRoot(host);
      }
      // A short laptop window is no phone on its side: the status as today
      at(874, 400);
      act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, phone: false })));
      expect(host.querySelector('[data-testid="reveal-status"]')!.className).toBe('reveal-status predsky-status');
      act(() => root.unmount());
      root = createRoot(host);
      // Upright, as today: the title, the disclosure sentences and the facts, no sideways class
      at(390, 844);
      act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, phone: true })));
      const upright = host.querySelector('[data-testid="reveal-status"]')!;
      expect(upright.className).toBe('reveal-status predsky-status');
      expect(upright.querySelector('.predsky-status__lead')).not.toBeNull();
      expect(upright.querySelector('.predsky-status__note')).not.toBeNull();
      expect(upright.querySelector('.predsky-status__facts')).not.toBeNull();
    } finally {
      act(() => root.unmount());
      host.remove();
      at(window0.w, window0.h);
    }
  });

  it('keeps the names clear of the status column from the first frame, reading its box after layout (at any size)', async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const base = poseSession(sample('horizon_predicted_only'));
    const sky = dispatchAlbedianAction(base, { type: 'sky.show', requestId: 'req_sideways_names', expectedViewRevision: base.viewRevision, mode: 'predicted', observationSolutionId: base.observationSolution!.id }).session;
    const template = sky.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const alpha = { ...template, id: 'finding-predicted-star-a', title: 'Alpha', skyPosition: { raDeg: 10, decDeg: -80, uncertaintyArcsec: 1 }, catalogRef: { id: 'a', provider: 'Hipparcos', objectType: 'star', releaseVersion: 'Hipparcos 2007', catalogSourceId: 'HIP 1', commonName: 'Alpha', apparentMagnitude: 0.5 } } as unknown as typeof template;
    const session = { ...sky, observationSolution: { ...sky.observationSolution!, findings: [alpha] } } as typeof sky;
    const pose = { cameraWorldQuaternion: [0, 0, 0, 1] as [number, number, number, number], icrsWorldQuaternion: [0, 0, 0, 1] as [number, number, number, number], fovDeg: 60, aspect: innerWidth / innerHeight, limitingMagnitude: 6 };
    const rect = HTMLElement.prototype.getBoundingClientRect;
    const host = document.createElement('div');
    document.body.appendChild(host);
    let root = createRoot(host);
    const names = () => host.querySelectorAll('.predsky-name').length;
    const show = async () => {
      act(() => root.render(createElement(PredictedSkyScreen, { session, onHome: () => {}, phone: true, skyPose: () => pose })));
      await act(async () => await new Promise((r) => setTimeout(r, 30)));
    };
    const window0 = { w: window.innerWidth, h: window.innerHeight };
    const at = (w: number, h: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
    };
    // The status's box follows its class as laid out: in its sideways place (as if) over the whole window, else nowhere
    HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
      if (this.getAttribute('data-testid') !== 'reveal-status') return rect.call(this);
      const r = this.classList.contains('is-sideways') ? [0, 0, innerWidth, innerHeight] : [0, 0, 0, 0];
      return { left: r[0], top: r[1], right: r[2], bottom: r[3], width: r[2]! - r[0]!, height: r[3]! - r[1]!, x: r[0], y: r[1], toJSON() {} } as DOMRect;
    };
    try {
      at(1024, 768);
      await show();
      expect(names()).toBe(1);
      // Turned on its side: the render that places the names still reads the upright box; the sideways class lands on the
      // commit, and the names must leave the column it now covers
      at(874, 400);
      act(() => void window.dispatchEvent(new Event('resize')));
      expect(host.querySelector('[data-testid="reveal-status"]')!.className).toBe('reveal-status predsky-status is-sideways');
      expect(names()).toBe(0);
    } finally {
      HTMLElement.prototype.getBoundingClientRect = rect;
      at(window0.w, window0.h);
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
