// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { ALBEDIAN_SPACING } from '@albedian/design-system';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import {
  LANDSCAPE_PHONE_QUERY,
  PHONE_QUERY,
  REDUCED_MOTION_QUERY,
  SHEET_MARGIN_PX,
  SHEET_SETTLE_MS,
  SHEET_TOP_FRACTION,
  TOP_CLEAR_PX,
  sheetLiftPx,
  sheetShiftPx,
  CAPTIONS_CHANGE_EVENT,
  LANDSCAPE_PILL_CLEAR_PX,
  LANDSCAPE_SHEET_SPAN_PX,
  SAFE_AREA_BOTTOM_VAR,
  SAFE_AREA_RIGHT_VAR,
  readSafeAreaInsetsPx,
} from '../phoneSheet.js';
import { CelestialTargetCard } from '../CelestialTargetCard.js';
import { VoiceCommandHud } from '../VoiceCommandHud.js';
import { projectFindingToViewport } from '../CelestialMarkerOverlay.js';

// One sheet at a time on a phone, after Apple Maps on iPhone: the selected star's sheet sits at the bottom and the
// photo lifts so the star stays visible above it.

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

const css = fs.readFileSync(path.resolve(__dirname, '../../styles/albedian.css'), 'utf8');

/** The selected marker's box (the design system's touch target), so its ring reaches half of it from the centre. */
const RING_PX = ALBEDIAN_SPACING.minTouchTargetPx;
/** Sideways, the sheet's left edge as albedian.css puts it: a 380 px card at calc(12px + env(safe-area-inset-right)). */
const sheetLeftPx = (widthPx: number, insetRightPx: number) => widthPx - 12 - insetRightPx - 380;

/** Stands in for the root's computed style, where the stylesheet resolves the safe-area insets for the script. */
function stubRootStyle(values: Record<string, string>) {
  const real = window.getComputedStyle.bind(window);
  return vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element, pseudo?: string | null) => {
    if (element !== document.documentElement) return real(element, pseudo);
    return { getPropertyValue: (name: string) => values[name] ?? '' } as unknown as CSSStyleDeclaration;
  });
}

describe('sheetLiftPx', () => {
  const clearBottom = (h: number) => h * SHEET_TOP_FRACTION - SHEET_MARGIN_PX;

  it('keeps the sheet top at 52% of the height, 24 px clear of it and 72 px clear of the top bar', () => {
    expect(SHEET_TOP_FRACTION).toBe(0.52);
    expect(SHEET_MARGIN_PX).toBe(24);
    expect(TOP_CLEAR_PX).toBe(72);
    expect(PHONE_QUERY).toBe('(max-width: 720px) and (min-height: 501px)');
    // Sideways means a short touch screen: a short desktop window keeps the desktop layout
    expect(LANDSCAPE_PHONE_QUERY).toBe('(max-height: 500px) and (pointer: coarse)');
  });

  it('picks exactly one layout for every screen: upright phone, sideways phone, or the desktop', () => {
    // The queries use only max-width, min-height, max-height and pointer: evaluate them for real screens
    const matches = (query: string, screen: { w: number; h: number; coarse: boolean }) =>
      query.split(' and ').every((part) => {
        const m = /\((max-width|min-height|max-height|pointer):\s*([a-z0-9]+)/.exec(part);
        if (!m) return false;
        const value = m[2]!;
        if (m[1] === 'pointer') return (value === 'coarse') === screen.coarse;
        const n = parseFloat(value);
        return m[1] === 'max-width' ? screen.w <= n : m[1] === 'min-height' ? screen.h >= n : screen.h <= n;
      });
    const screens: Array<[string, { w: number; h: number; coarse: boolean }, 'portrait' | 'landscape' | 'desktop']> = [
      ['iPhone 14 upright', { w: 390, h: 844, coarse: true }, 'portrait'],
      ['iPhone 14 sideways', { w: 844, h: 390, coarse: true }, 'landscape'],
      ['iPhone SE upright', { w: 375, h: 667, coarse: true }, 'portrait'],
      ['iPhone SE sideways', { w: 667, h: 375, coarse: true }, 'landscape'],
      ['760 by 390 sideways', { w: 760, h: 390, coarse: true }, 'landscape'],
      ['laptop', { w: 1440, h: 900, coarse: false }, 'desktop'],
      ['short desktop window', { w: 1200, h: 450, coarse: false }, 'desktop'],
    ];
    for (const [name, screen, want] of screens) {
      const got = [matches(PHONE_QUERY, screen) && 'portrait', matches(LANDSCAPE_PHONE_QUERY, screen) && 'landscape'].filter(Boolean);
      expect(got, name).toEqual(want === 'desktop' ? [] : [want]);
    }
  });

  it('keeps a sideways star clear of the captions over the voice element as well as the sheet', () => {
    expect(sheetShiftPx(300, 219, 844, 390, 'landscape', 0).y).toBe(0);
    const withCaptions = sheetShiftPx(300, 219, 760, 390, 'landscape', 138);
    // 24 px above the captions, as from the sheet, so the selected ring (about 25 px across its radius) clears them
    expect(withCaptions.y).toBeCloseTo(219 - (390 - (LANDSCAPE_PILL_CLEAR_PX + 138 + 24)), 9);
  });

  it('keeps a sideways star and its ring clear of the sheet at 844 by 390 whatever the right safe-area inset', () => {
    for (const inset of [0, 47, 59]) {
      const markerX = 800;
      const starX = markerX - sheetShiftPx(markerX, 200, 844, 390, 'landscape', 0, { right: inset, bottom: 0 }).x;
      const sheetLeft = sheetLeftPx(844, inset);
      // The centre stays SHEET_MARGIN_PX clear of the sheet's span, which is the sheet and the 12 px gap beside it
      const gap = LANDSCAPE_SHEET_SPAN_PX - 380 - 12;
      expect(sheetLeft - starX, `inset ${inset}: centre`).toBeCloseTo(gap + SHEET_MARGIN_PX, 9);
      // So the ring clears the sheet's edge by 12 + 24 - 22 = 14 px
      expect(sheetLeft - (starX + RING_PX / 2), `inset ${inset}: ring`).toBeCloseTo(gap + SHEET_MARGIN_PX - RING_PX / 2, 9);
      expect(sheetLeft - (starX + RING_PX / 2)).toBeGreaterThan(0);
    }
  });

  it('keeps a sideways star above the captions when the voice element rises by the bottom safe-area inset', () => {
    const s = sheetShiftPx(300, 219, 760, 390, 'landscape', 138, { right: 0, bottom: 21 });
    expect(s.y).toBeCloseTo(219 - (390 - 21 - (LANDSCAPE_PILL_CLEAR_PX + 138 + 24)), 9);
  });

  it('never shifts a sideways star the wrong way, and lands one right of the clear line with its ring clear of the sheet', () => {
    fc.assert(
      fc.property(fc.integer({ min: 568, max: 1000 }), fc.integer({ min: 0, max: 64 }), fc.double({ min: 0, max: 1, noNaN: true }), (w, inset, fx) => {
        const x = fx * w;
        const s = sheetShiftPx(x, 200, w, 390, 'landscape', 0, { right: inset, bottom: 0 });
        if (s.x < 0) return false;
        const line = w - inset - LANDSCAPE_SHEET_SPAN_PX - SHEET_MARGIN_PX;
        if (x <= line) return s.x === 0;
        const shown = x - s.x;
        return Math.abs(shown - line) < 1e-9 && sheetLeftPx(w, inset) - (shown + RING_PX / 2) >= 14 - 1e-9;
      }),
      { seed: SEED, examples: [[844, 0, 0.95], [844, 47, 0.95], [844, 59, 0.95], [667, 59, 0.9]] }
    );
  });

  it('reads the insets the stylesheet resolves on the root, and 0 for a missing or unresolved one', () => {
    let stub = stubRootStyle({ [SAFE_AREA_RIGHT_VAR]: ' 47px', [SAFE_AREA_BOTTOM_VAR]: ' 21px' });
    try {
      expect(readSafeAreaInsetsPx()).toEqual({ right: 47, bottom: 21 });
    } finally {
      stub.mockRestore();
    }
    stub = stubRootStyle({ [SAFE_AREA_RIGHT_VAR]: 'env(safe-area-inset-right, 0px)', [SAFE_AREA_BOTTOM_VAR]: '-3px' });
    try {
      expect(readSafeAreaInsetsPx()).toEqual({ right: 0, bottom: 0 });
    } finally {
      stub.mockRestore();
    }
    expect(readSafeAreaInsetsPx()).toEqual({ right: 0, bottom: 0 });
  });

  it('lifts nothing for a star already above the sheet and lifts a low star to just above it', () => {
    expect(sheetLiftPx(300, 844)).toBe(0);
    expect(sheetLiftPx(700, 844)).toBeCloseTo(700 - clearBottom(844), 9);
  });

  it('never lifts a negative amount, never lifts past the top bar, and leaves the star in the clear band', () => {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 2000, noNaN: true }), fc.integer({ min: 300, max: 1400 }), (y, h) => {
        const lift = sheetLiftPx(y, h);
        if (lift < 0) return false;
        const shown = y - lift;
        if (y <= clearBottom(h)) return lift === 0;
        // Lifted: as close to the sheet as the band allows, never above the top bar
        return Math.abs(shown - Math.max(clearBottom(h), Math.min(y, TOP_CLEAR_PX))) < 1e-9 && shown >= Math.min(y, TOP_CLEAR_PX) - 1e-9;
      }),
      { seed: SEED, examples: [[700, 844], [100, 390], [0, 844], [2000, 300]] }
    );
  });
});

/** A matchMedia stand-in per query, whose answers the test can change, as a rotation or resize would. */
function fakeMatchMedia(phone: boolean, opts: { landscape?: boolean; reducedMotion?: boolean } = {}) {
  type Listener = (e: { matches: boolean }) => void;
  const state = new Map<string, boolean>([
    [PHONE_QUERY, phone],
    [LANDSCAPE_PHONE_QUERY, opts.landscape ?? false],
    [REDUCED_MOTION_QUERY, opts.reducedMotion ?? false],
  ]);
  const listeners = new Map<string, Set<Listener>>();
  const mqlFor = (query: string) => ({
    get matches() {
      return state.get(query) ?? false;
    },
    media: query,
    addEventListener: (_: string, fn: Listener) => {
      if (!listeners.has(query)) listeners.set(query, new Set());
      listeners.get(query)!.add(fn);
    },
    removeEventListener: (_: string, fn: Listener) => listeners.get(query)?.delete(fn),
  });
  return {
    install: () => {
      window.matchMedia = vi.fn((query: string) => mqlFor(query)) as unknown as typeof window.matchMedia;
    },
    set: (next: boolean, query: string = PHONE_QUERY) => {
      state.set(query, next);
      listeners.get(query)?.forEach((fn) => fn({ matches: next }));
    },
    listenerCount: (query: string = PHONE_QUERY) => listeners.get(query)?.size ?? 0,
  };
}

describe('the card lifts the photo on a phone', () => {
  let container: HTMLDivElement;
  let root: Root;
  const lift = () => document.documentElement.style.getPropertyValue('--alb-sheet-lift');

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 844, configurable: true });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.documentElement.style.removeProperty('--alb-sheet-lift');
  });

  /** A still shown 1:1 at 390 by 844 with the selected star low on the screen, under where the sheet sits. */
  function lowStar(): { session: ObservationSession; star: CelestialFinding } {
    const base = createSyntheticCalibratedSession();
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const low = { ...star, sourceDetection: { ...star.sourceDetection!, xPx: 195, yPx: 700 } } as CelestialFinding;
    const session = {
      ...base,
      selectedFindingId: low.id,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 390, height: 844 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      machines: { ...base.machines, navigation: 'source' },
      observationSolution: { ...base.observationSolution!, findings: [low] },
    } as ObservationSession;
    return { session, star: low };
  }

  function render(session: ObservationSession, star: CelestialFinding | null, onRunUtterance = vi.fn()) {
    act(() => root.render(createElement(CelestialTargetCard, { session, selectedFinding: star, onRunUtterance, onPushToTalk: () => {} })));
    return onRunUtterance;
  }

  it('sets the lift that keeps the selected star above the sheet', () => {
    const media = fakeMatchMedia(true);
    media.install();
    const { session, star } = lowStar();
    render(session, star);
    const y = projectFindingToViewport(star, session, 390, 844).yPx;
    expect(lift()).toBe(`${sheetLiftPx(y, 844).toFixed(1)}px`);
    expect(parseFloat(lift())).toBeGreaterThan(0);
    // The sheet goes away: the photo drops back
    act(() => root.render(createElement('div')));
    expect(lift()).toBe('0px');
  });

  it('follows the phone query as it changes, and stops listening once gone', () => {
    const media = fakeMatchMedia(false);
    media.install();
    const { session, star } = lowStar();
    render(session, star);
    expect(lift()).toBe('0px');
    act(() => media.set(true));
    expect(parseFloat(lift())).toBeGreaterThan(0);
    act(() => media.set(false));
    expect(lift()).toBe('0px');
    expect(media.listenerCount()).toBe(1);
    act(() => root.render(createElement('div')));
    expect(media.listenerCount()).toBe(0);
    expect(lift()).toBe('0px');
  });

  it('drops the lift outside the photo view and when nothing is selected', () => {
    fakeMatchMedia(true).install();
    const { session, star } = lowStar();
    render({ ...session, machines: { ...session.machines, navigation: 'space' } } as ObservationSession, star);
    expect(lift()).toBe('0px');
    render(session, null);
    expect(lift()).toBe('0px');
  });

  it('drops the lift and waits for the photo to settle before a flight starts, so it anchors where the star is seen', () => {
    vi.useFakeTimers();
    try {
      fakeMatchMedia(true).install();
      const { session, star } = lowStar();
      const seen: string[] = [];
      const onRunUtterance = vi.fn(() => {
        seen.push(lift());
      });
      render(session, star, onRunUtterance);
      expect(parseFloat(lift())).toBeGreaterThan(0);
      act(() => (container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement).click());
      expect(lift()).toBe('0px');
      expect(onRunUtterance).not.toHaveBeenCalled();
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS - 1);
      });
      expect(onRunUtterance).not.toHaveBeenCalled();
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(onRunUtterance).toHaveBeenCalledWith(`Take me to ${star.title}`, 'pointer');
      expect(seen).toEqual(['0px']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('starts one flight for two Go there presses inside the settle, and another for a press after it', () => {
    vi.useFakeTimers();
    try {
      fakeMatchMedia(true).install();
      const { session, star } = lowStar();
      const onRunUtterance = render(session, star);
      const go = container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement;
      act(() => go.click());
      act(() => go.click());
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS * 2);
      });
      expect(onRunUtterance).toHaveBeenCalledTimes(1);
      act(() => go.click());
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS * 2);
      });
      expect(onRunUtterance).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('starts the flight at once when nothing is lifted or motion is reduced', () => {
    fakeMatchMedia(true, { reducedMotion: true }).install();
    const { session, star } = lowStar();
    const onRunUtterance = render(session, star);
    act(() => (container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement).click());
    expect(onRunUtterance).toHaveBeenCalledTimes(1);
  });

  it("waits the same way for the palette's Take me there", () => {
    vi.useFakeTimers();
    try {
      fakeMatchMedia(true).install();
      const { star } = lowStar();
      document.documentElement.style.setProperty('--alb-sheet-lift', '40.0px');
      const onRunUtterance = vi.fn();
      act(() =>
        root.render(
          createElement(VoiceCommandHud, {
            voiceState: 'off',
            commandText: '',
            setCommandText: () => {},
            onRunUtterance,
            onPushToTalk: () => {},
            selectedFinding: star,
          })
        )
      );
      act(() => (container.querySelector('[data-testid="voice-type-btn"]') as HTMLButtonElement).click());
      const take = [...container.querySelectorAll('[data-testid="ask-suggestions"] [role="option"]')].find((o) => o.textContent?.includes('Take me there')) as HTMLElement;
      act(() => take.click());
      expect(lift()).toBe('0px');
      expect(onRunUtterance).not.toHaveBeenCalled();
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS);
      });
      expect(onRunUtterance).toHaveBeenCalledWith('Take me there', 'text');
    } finally {
      vi.useRealTimers();
    }
  });

  it('shifts the photo sideways in a landscape phone so the star clears the sheet on the right', () => {
    Object.defineProperty(window, 'innerWidth', { value: 844, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 390, configurable: true });
    fakeMatchMedia(false, { landscape: true }).install();
    const base = createSyntheticCalibratedSession();
    const star0 = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const right = { ...star0, sourceDetection: { ...star0.sourceDetection!, xPx: 700, yPx: 200 } } as CelestialFinding;
    const session = {
      ...base,
      selectedFindingId: right.id,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 844, height: 390 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      machines: { ...base.machines, navigation: 'source' },
      observationSolution: { ...base.observationSolution!, findings: [right] },
    } as ObservationSession;
    render(session, right);
    const pos = projectFindingToViewport(right, session, 844, 390);
    const want = sheetShiftPx(pos.xPx, pos.yPx, 844, 390, 'landscape');
    expect(want.x).toBeGreaterThan(0);
    expect(document.documentElement.style.getPropertyValue('--alb-sheet-shift-x')).toBe(`${want.x.toFixed(1)}px`);
  });

  it('reads the right safe-area inset sideways, so the star clears a sheet moved in from the notch', () => {
    Object.defineProperty(window, 'innerWidth', { value: 844, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 390, configurable: true });
    fakeMatchMedia(false, { landscape: true }).install();
    const base = createSyntheticCalibratedSession();
    const star0 = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const right = { ...star0, sourceDetection: { ...star0.sourceDetection!, xPx: 700, yPx: 200 } } as CelestialFinding;
    const session = {
      ...base,
      selectedFindingId: right.id,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 844, height: 390 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      machines: { ...base.machines, navigation: 'source' },
      observationSolution: { ...base.observationSolution!, findings: [right] },
    } as ObservationSession;
    const stub = stubRootStyle({ [SAFE_AREA_RIGHT_VAR]: '47px', [SAFE_AREA_BOTTOM_VAR]: '0px' });
    try {
      render(session, right);
      const pos = projectFindingToViewport(right, session, 844, 390);
      const shiftX = parseFloat(document.documentElement.style.getPropertyValue('--alb-sheet-shift-x'));
      const starX = pos.xPx - shiftX;
      expect(starX).toBeCloseTo(844 - 47 - LANDSCAPE_SHEET_SPAN_PX - SHEET_MARGIN_PX, 0);
      expect(sheetLeftPx(844, 47) - (starX + RING_PX / 2)).toBeGreaterThan(0);
    } finally {
      stub.mockRestore();
    }
  });

  /** Go there, then close the sheet before the photo settles: no flight starts, and a later Go there still works. */
  function goThereThenClose(close: () => void) {
    vi.useFakeTimers();
    try {
      fakeMatchMedia(true).install();
      const { session, star } = lowStar();
      const onRunUtterance = vi.fn();
      const onClose = vi.fn();
      act(() => root.render(createElement(CelestialTargetCard, { session, selectedFinding: star, onRunUtterance, onPushToTalk: () => {}, onClose })));
      expect(parseFloat(lift())).toBeGreaterThan(0);
      const go = () => container.querySelector('[data-testid="target-card-go-there"]') as HTMLButtonElement;
      act(() => go().click());
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS - 1);
      });
      close();
      expect(onClose).toHaveBeenCalledTimes(1);
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS * 2);
      });
      expect(onRunUtterance).not.toHaveBeenCalled();
      act(() => go().click());
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS * 2);
      });
      expect(onRunUtterance).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  }

  it('starts no flight when Close is pressed while the photo settles after Go there', () => {
    goThereThenClose(() => act(() => (container.querySelector('[data-testid="target-card-close"]') as HTMLButtonElement).click()));
  });

  it('starts no flight when Esc closes the sheet while the photo settles after Go there', () => {
    goThereThenClose(() =>
      act(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      })
    );
  });

  it("starts no flight for the palette's Take me there either once the sheet closes during the settle", () => {
    vi.useFakeTimers();
    try {
      fakeMatchMedia(true).install();
      const { session, star } = lowStar();
      const onRunUtterance = vi.fn();
      const onClose = vi.fn();
      act(() =>
        root.render(
          createElement(
            'div',
            null,
            createElement(CelestialTargetCard, { session, selectedFinding: star, onRunUtterance, onPushToTalk: () => {}, onClose }),
            createElement(VoiceCommandHud, { voiceState: 'off', commandText: '', setCommandText: () => {}, onRunUtterance, onPushToTalk: () => {}, selectedFinding: star })
          )
        )
      );
      expect(parseFloat(lift())).toBeGreaterThan(0);
      act(() => (container.querySelector('[data-testid="voice-type-btn"]') as HTMLButtonElement).click());
      const take = [...container.querySelectorAll('[data-testid="ask-suggestions"] [role="option"]')].find((o) => o.textContent?.includes('Take me there')) as HTMLElement;
      act(() => take.click());
      act(() => (container.querySelector('[data-testid="target-card-close"]') as HTMLButtonElement).click());
      act(() => {
        vi.advanceTimersByTime(SHEET_SETTLE_MS * 2);
      });
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onRunUtterance).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('captions and the sideways shift', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.documentElement.style.removeProperty('--alb-captions-h');
  });

  it('tells the page the captions height, follows it, and clears it to 0px once they go or the voice element unmounts', () => {
    document.documentElement.style.removeProperty('--alb-captions-h');
    // jsdom lays nothing out: give the captions a height, as a browser would
    const realRect = HTMLElement.prototype.getBoundingClientRect;
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('alb-voice-captions') ? new DOMRect(12, 194, 330, 54) : realRect.call(this);
    });
    const seen = vi.fn();
    window.addEventListener(CAPTIONS_CHANGE_EVENT, seen);
    const captionsH = () => document.documentElement.style.getPropertyValue('--alb-captions-h');
    const hud = (banner: string | null) =>
      act(() =>
        root.render(
          createElement(VoiceCommandHud, {
            voiceState: 'off',
            lastStatusBanner: banner,
            lastUtterance: 'how far away is that one',
            commandText: '',
            setCommandText: () => {},
            onRunUtterance: () => {},
            onPushToTalk: () => {},
          })
        )
      );
    try {
      hud('Tabit is about 26 light-years away.');
      expect(seen).toHaveBeenCalled();
      expect(captionsH()).toBe('54.0px');
      seen.mockClear();
      hud(null);
      expect(seen).toHaveBeenCalled();
      expect(captionsH()).toBe('0px');
      hud('Tabit is about 26 light-years away.');
      expect(captionsH()).toBe('54.0px');
      seen.mockClear();
      act(() => root.unmount());
      root = createRoot(container);
      expect(seen).toHaveBeenCalled();
      expect(captionsH()).toBe('0px');
    } finally {
      rect.mockRestore();
      window.removeEventListener(CAPTIONS_CHANGE_EVENT, seen);
    }
  });

  it('publishes the captions height again when the browser resizes them', () => {
    document.documentElement.style.removeProperty('--alb-captions-h');
    let heightPx = 54;
    const realRect = HTMLElement.prototype.getBoundingClientRect;
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('alb-voice-captions') ? new DOMRect(12, 194, 330, heightPx) : realRect.call(this);
    });
    // jsdom has no ResizeObserver: a stand-in that records what it watches, so the test can fire a resize
    const observers: FakeResizeObserver[] = [];
    class FakeResizeObserver {
      callback: ResizeObserverCallback;
      watched: Element[] = [];
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        observers.push(this);
      }
      observe(node: Element) {
        this.watched.push(node);
      }
      unobserve() {}
      disconnect() {
        this.watched = [];
      }
    }
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    const seen = vi.fn();
    window.addEventListener(CAPTIONS_CHANGE_EVENT, seen);
    const captionsH = () => document.documentElement.style.getPropertyValue('--alb-captions-h');
    try {
      act(() =>
        root.render(
          createElement(VoiceCommandHud, {
            voiceState: 'off',
            lastStatusBanner: 'Tabit is about 26 light-years away.',
            lastUtterance: 'how far away is that one',
            commandText: '',
            setCommandText: () => {},
            onRunUtterance: () => {},
            onPushToTalk: () => {},
          })
        )
      );
      expect(captionsH()).toBe('54.0px');
      const watching = observers.filter((o) => o.watched.some((node) => node.classList.contains('alb-voice-captions')));
      expect(watching).toHaveLength(1);
      // The captions grow by a line (a wrap, a font load): the browser reports it, nothing in React changes
      heightPx = 81;
      seen.mockClear();
      act(() => watching.forEach((o) => o.callback([], o as unknown as ResizeObserver)));
      expect(captionsH()).toBe('81.0px');
      expect(seen).toHaveBeenCalled();
    } finally {
      rect.mockRestore();
      vi.unstubAllGlobals();
      window.removeEventListener(CAPTIONS_CHANGE_EVENT, seen);
    }
  });

  it('moves a sideways star above the captions as their height changes', () => {
    Object.defineProperty(window, 'innerWidth', { value: 760, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 390, configurable: true });
    fakeMatchMedia(false, { landscape: true }).install();
    const base = createSyntheticCalibratedSession();
    const star0 = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const star = { ...star0, sourceDetection: { ...star0.sourceDetection!, xPx: 332, yPx: 219 } } as CelestialFinding;
    const session = {
      ...base,
      selectedFindingId: star.id,
      sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 760, height: 390 },
      spatialView: { ...base.spatialView, mode: 'source_2d' },
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      machines: { ...base.machines, navigation: 'source' },
      observationSolution: { ...base.observationSolution!, findings: [star] },
    } as ObservationSession;
    act(() => root.render(createElement(CelestialTargetCard, { session, selectedFinding: star, onRunUtterance: () => {}, onPushToTalk: () => {} })));
    const pos = projectFindingToViewport(star, session, 760, 390);
    const lift = () => document.documentElement.style.getPropertyValue('--alb-sheet-lift');
    expect(lift()).toBe('0px');
    act(() => {
      document.documentElement.style.setProperty('--alb-captions-h', '138px');
      window.dispatchEvent(new Event(CAPTIONS_CHANGE_EVENT));
    });
    const want = sheetShiftPx(pos.xPx, pos.yPx, 760, 390, 'landscape', 138);
    expect(want.y).toBeGreaterThan(0);
    expect(lift()).toBe(`${want.y.toFixed(1)}px`);
  });
});

describe('closing the star sheet', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    fakeMatchMedia(false).install();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function open(navigation: 'source' | 'space' = 'source') {
    const base = createSyntheticCalibratedSession();
    const star = base.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    const session = { ...base, selectedFindingId: star.id, machines: { ...base.machines, navigation } } as ObservationSession;
    const onClose = vi.fn();
    act(() => root.render(createElement(CelestialTargetCard, { session, selectedFinding: star, onRunUtterance: () => {}, onPushToTalk: () => {}, onClose })));
    return onClose;
  }

  const key = (k: string, target: EventTarget = window) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
    });

  const tap = (target: Element, from: [number, number], to: [number, number] = from) =>
    act(() => {
      target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: from[0], clientY: from[1] }));
      target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: to[0], clientY: to[1] }));
    });

  it('has a Close button with its Esc key shown, and closes on it', () => {
    const onClose = open();
    const close = container.querySelector('[data-testid="target-card-close"]') as HTMLButtonElement;
    expect(close.textContent).toContain('Close');
    expect(close.querySelector('kbd')?.textContent).toBe('Esc');
    act(() => close.click());
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on Esc in the photo view, but not from a field or in 3D, where Esc goes back to the moment', () => {
    let onClose = open();
    key('Escape');
    expect(onClose).toHaveBeenCalledTimes(1);
    const field = document.createElement('input');
    document.body.append(field);
    key('Escape', field);
    field.remove();
    expect(onClose).toHaveBeenCalledTimes(1);
    onClose = open('space');
    key('Escape');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on a tap on empty sky, not on a HUD element and not at the end of a drag', () => {
    const onClose = open();
    const sky = document.createElement('div');
    const hud = document.createElement('div');
    hud.setAttribute('data-hud-interactive', 'true');
    document.body.append(sky, hud);
    tap(hud, [10, 10]);
    tap(sky, [100, 100], [130, 100]);
    expect(onClose).not.toHaveBeenCalled();
    tap(sky, [100, 100]);
    expect(onClose).toHaveBeenCalledTimes(1);
    sky.remove();
    hud.remove();
  });
});

describe('the solve status on the star sheet', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    fakeMatchMedia(true).install();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function show(session: ObservationSession) {
    const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    act(() => root.render(createElement(CelestialTargetCard, { session: { ...session, selectedFindingId: star.id }, selectedFinding: star, onRunUtterance: () => {}, onPushToTalk: () => {} })));
    return container.querySelector('[data-testid="target-card-solve-status"]')?.textContent;
  }

  it('says the photo solve class and how it was verified, in one line', () => {
    const base = createSyntheticCalibratedSession();
    const q = base.observationSolution!.quality!;
    expect(show(base)).toBe(`Photo solve: calibrated · CAT · verified on ${q.heldOutInliers} of ${q.heldOutPairCount} held-out stars`);
  });

  it('says plainly when the solve is not verified', () => {
    const base = createSyntheticCalibratedSession();
    const unverified = {
      ...base,
      observationSolution: { ...base.observationSolution!, status: 'approximate', quality: { ...base.observationSolution!.quality!, passedIndependentVerification: false } },
    } as ObservationSession;
    expect(show(unverified)).toBe('Photo solve: approximate · PRD · not verified');
  });

  it('says not verified for a calibrated solve whose held-out check failed, and names an unresolved one', () => {
    const base = createSyntheticCalibratedSession();
    const failed = {
      ...base,
      observationSolution: { ...base.observationSolution!, quality: { ...base.observationSolution!.quality!, passedIndependentVerification: false } },
    } as ObservationSession;
    expect(show(failed)).toBe('Photo solve: calibrated · OBS · not verified');
    const unresolved = { ...base, observationSolution: { ...base.observationSolution!, status: 'unresolved', quality: undefined } } as ObservationSession;
    expect(show(unresolved)).toBe('Photo solve: unresolved · OBS · not verified');
    const predictedOnly = { ...base, observationSolution: { ...base.observationSolution!, status: 'predicted_only', quality: undefined } } as ObservationSession;
    expect(show(predictedOnly)).toBe('Photo solve: predicted only · PRD · not verified');
  });
});

describe('phone layout CSS', () => {
  const start = css.indexOf('PHONE LAYOUT: ONE SHEET AT A TIME');
  const block = css.slice(start, css.indexOf('/* ====', css.indexOf('*/', start) + 2));

  function mediaBody(header: RegExp): string {
    const m = header.exec(block);
    if (!m) return '';
    let depth = 0;
    for (let i = m.index; i < block.length; i++) {
      if (block[i] === '{') depth++;
      else if (block[i] === '}' && --depth === 0) return block.slice(m.index, i + 1);
    }
    return '';
  }

  function rule(selector: string, within: string): Record<string, string> {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = new RegExp(`(^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(within);
    const out: Record<string, string> = {};
    for (const raw of (match?.[2] ?? '').split(';')) {
      const colon = raw.indexOf(':');
      if (colon > 0) out[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim();
    }
    return out;
  }

  const phone = mediaBody(/@media\s*\(max-width:\s*720px\) and \(min-height:\s*501px\)/);

  it('has its own block with a phone media query', () => {
    expect(start).toBeGreaterThan(0);
    expect(phone.length).toBeGreaterThan(0);
  });

  it('puts the star sheet above the receipt and clear of the home indicator', () => {
    const card = rule('.alb-target-card', phone);
    expect(Number(card['z-index'])).toBeGreaterThan(29);
    expect(card['padding-bottom']).toMatch(/env\(safe-area-inset-bottom\)/);
    expect(card['max-height']).toBe('48vh');
  });

  it('lets the solve receipt step aside whole while a star sheet is open or the view is in 3D', () => {
    expect(phone).toMatch(/\.alb-root:has\(\.alb-target-card\) \.albedian-solve-compare-hud,\s*\.alb-root:has\(\.alb-restore-hud-shell\) \.albedian-solve-compare-hud\s*\{[^}]*display:\s*none;/);
    const short = mediaBody(/@media\s*\(max-height:\s*500px\) and \(pointer:\s*coarse\)/);
    expect(rule('.alb-root:has(.alb-target-card) .albedian-solve-compare-hud', short).display).toBe('none');
  });

  it('turns the 3D panels into pills along the top and the star sheet into a short peek', () => {
    expect(rule('.alb-root:has(.alb-restore-hud-shell) .alb-target-card', phone)['max-height']).toBe('26vh');
    expect(rule('.alb-restore-hud-shell', phone).top).toMatch(/env\(safe-area-inset-top\)/);
  });

  it('keeps the audio notes to a strip with the credits one tap away behind Notices', () => {
    expect(rule('.alb-root .alb-audio-credits', phone).display).toBe('none');
    expect(rule('.alb-root .alb-audio-notices', phone).display).toBe('inline-block');
    expect(rule('.alb-root:has(.alb-audio-notes) .alb-restore-hud-shell', phone).top).toBe('calc(248px + env(safe-area-inset-top))');
    expect(rule('.alb-root:has(.alb-restore-hud-shell):has(.alb-audio-notes) .alb-layer-disclosure', phone).top).toBe('calc(180px + env(safe-area-inset-top))');
  });

  it('never cuts a disclosure: no ellipsis, clamp, hiding or single-line overflow on the phone', () => {
    for (const selector of ['.albedian-epoch-disclosure-hud__disclosure', '.alb-audio-disclosure', '.alb-flight-scale-disclosure']) {
      const rules = [...phone.matchAll(new RegExp(`[^{}]*${selector.replace('.', '\\.')}[^{}]*\\{([^}]*)\\}`, 'g'))].map((m) => m[1] ?? '');
      for (const body of rules) {
        expect(body, selector).not.toMatch(/text-overflow|line-clamp|display:\s*none|white-space:\s*nowrap|overflow:\s*hidden/);
      }
    }
    expect(phone).not.toMatch(/(epoch-disclosure-hud__disclosure|alb-audio-disclosure|alb-flight-scale-disclosure)[^{]*\{[^}]*(text-overflow|line-clamp)/);
  });

  it('lifts the photo and its markers together, but not the photo layer label', () => {
    const shift = /translate:\s*calc\(-1 \* var\(--alb-sheet-shift-x, 0px\)\) calc\(-1 \* var\(--alb-sheet-lift, 0px\)\);/;
    expect(phone).toMatch(new RegExp(`\\.alb-source-stage > img,\\s*\\.alb-source-stage > video,\\s*\\.alb-marker-overlay\\s*\\{[^}]*${shift.source}`));
    // The stage itself (which holds the layer label) and the label never move
    expect(block).not.toMatch(/(^|[,}\s])\.alb-source-stage\s*[,{]/);
    expect(block).not.toMatch(/\.alb-layer-disclosure[^{]*\{[^}]*(translate|transform)/);
  });

  it('keeps every epoch toggle label whole: the row wraps and nothing scrolls or clips it', () => {
    const btn = rule('.alb-root:has(.alb-restore-hud-shell) .albedian-epoch-disclosure-hud__btn', phone);
    expect(btn['white-space']).toBe('nowrap');
    const row = rule('.alb-root:has(.alb-restore-hud-shell) .albedian-epoch-disclosure-hud__actions', phone);
    expect(row['flex-wrap']).toBe('wrap');
    expect(Object.keys(row).filter((k) => k.startsWith('overflow'))).toEqual([]);
  });

  it('uses the same queries in the stylesheet as the card does', () => {
    expect(block).toContain(`@media ${PHONE_QUERY} {`);
    expect(block).toContain(`@media ${LANDSCAPE_PHONE_QUERY} {`);
    const card = css.slice(css.indexOf('MOMENT 05 CELESTIAL TARGET CARD'), css.indexOf('/* ====', css.indexOf('*/', css.indexOf('MOMENT 05 CELESTIAL TARGET CARD')) + 2));
    expect(card).toContain(`@media ${PHONE_QUERY} {`);
    expect(card).not.toMatch(/@media \(max-width: 720px\) \{/);
  });

  it('shows the solve status line on the phone sheet only', () => {
    expect(rule('.alb-scan-solve', phone).display).toBe('block');
    const card = css.slice(css.indexOf('MOMENT 05 CELESTIAL TARGET CARD'));
    expect(/\.alb-scan-solve\s*\{([^}]*)\}/.exec(card)?.[1]).toMatch(/display:\s*none;/);
  });

  it('fits a landscape phone only (not a short desktop window): the card runs the height clear of the edges and the notch', () => {
    expect(block).not.toMatch(/@media\s*\(max-height:\s*500px\)\s*\{/);
    const short = mediaBody(/@media\s*\(max-height:\s*500px\) and \(pointer:\s*coarse\)/);
    const card = rule('.alb-target-card', short);
    expect(card.top).toMatch(/env\(safe-area-inset-top\)/);
    expect(card.right).toBe('calc(12px + env(safe-area-inset-right))');
    expect(card['max-height']).toMatch(/^calc\(100vh - /);
    expect(short).toMatch(/\.alb-source-stage > img,\s*\.alb-source-stage > video,\s*\.alb-marker-overlay\s*\{[^}]*translate:/);
  });

  it('keeps the sideways left column from overlapping: the layer label spans it and the captions are smaller', () => {
    const short = mediaBody(/@media\s*\(max-height:\s*500px\) and \(pointer:\s*coarse\)/);
    const label = rule('.alb-root:has(.alb-target-card) .alb-layer-disclosure', short);
    expect(label.right).toBe('calc(416px + env(safe-area-inset-right))');
    expect(label['max-width']).toBe('none');
    const caption = rule('.alb-root:has(.alb-target-card) .alb-voice-caption', short);
    expect(caption['font-size']).toBe('14px');
    // A smaller size, never a cut: the captions still wrap whole
    expect(Object.keys(caption).filter((k) => /overflow|clamp|white-space|max-height|display/.test(k))).toEqual([]);
  });

  it('resolves the right and bottom safe-area insets on the root, where the card script reads them', () => {
    const root = rule(':root', block);
    expect(root[SAFE_AREA_RIGHT_VAR]).toBe('env(safe-area-inset-right, 0px)');
    expect(root[SAFE_AREA_BOTTOM_VAR]).toBe('env(safe-area-inset-bottom, 0px)');
  });

  it('holds the numbers the script copies: the sideways sheet span, the voice element beside it, the pill and the settle', () => {
    const short = mediaBody(/@media\s*\(max-height:\s*500px\) and \(pointer:\s*coarse\)/);
    const cardCss = css.slice(css.indexOf('MOMENT 05 CELESTIAL TARGET CARD'));
    const width = /^(\d+)px$/.exec(rule('.alb-target-card', cardCss).width ?? '');
    const edge = /^calc\((\d+)px \+ env\(safe-area-inset-right\)\)$/.exec(rule('.alb-target-card', short).right ?? '');
    // The span: the card, its offset from the screen edge, and a 12 px gap beside it
    expect(Number(width?.[1]) + Number(edge?.[1]) + 12).toBe(LANDSCAPE_SHEET_SPAN_PX);
    // The voice element stops at the span and moves in with the sheet
    const voice = rule('.alb-root:has(.alb-target-card) .alb-voice-hud', short);
    expect(voice.right).toBe(`calc(${LANDSCAPE_SHEET_SPAN_PX}px + env(safe-area-inset-right))`);
    // The captions sit on the listen pill: its bottom offset, the pill (padding, key, padding) and the gap above it
    const voiceCss = css.slice(css.indexOf('MOMENT 07 VOICE'));
    const bottom = /^calc\((\d+)px \+ env\(safe-area-inset-bottom\)\)$/.exec(voice.bottom ?? '');
    const pad = parseFloat(rule('.alb-listen-pill', voiceCss).padding ?? '');
    const key = parseFloat(rule('.alb-type-key', voiceCss).height ?? '');
    const gap = parseFloat(rule('.alb-voice-hud', voiceCss).gap ?? '');
    expect(Number(bottom?.[1]) + 2 * pad + key + gap).toBe(LANDSCAPE_PILL_CLEAR_PX);
    // The settle: the shift's transition runs on --hud-dur-base, which the tokens set to SHEET_SETTLE_MS
    for (const media of [phone, short]) expect(media).toMatch(/transition:\s*translate var\(--hud-dur-base\) /);
    const tokens = fs.readFileSync(path.resolve(__dirname, '../../styles/hud-tokens.css'), 'utf8');
    expect(Number(/:root\s*\{[^}]*--hud-dur-base:\s*(\d+)ms;/.exec(tokens)?.[1])).toBe(SHEET_SETTLE_MS);
  });

  it('keeps the evidence line clear of Close on the card', () => {
    const card = css.slice(css.indexOf('MOMENT 05 CELESTIAL TARGET CARD'));
    const pad = /\.alb-scan-close \+ \.alb-scan-head \.alb-scan-kicker\s*\{[^}]*padding-right:\s*(\d+)px/.exec(card);
    expect(Number(pad?.[1])).toBeGreaterThanOrEqual(88);
  });
});
