// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { PredictedSkyScreen } from '../PredictedSkyScreen.js';
import { PHONE_STATUS_TOP_MIN_PX, phoneSheetTopPx, shortCardSheetTopPx } from '../shortCard.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// short-card-status-one (UIUX-predsky-READY.md, "One line for short upright windows with a card open", on AUDIT-C's O19;
// DECIDED-BY-LEAD at the line stamped 03:59 on 27 Nov): upright at 390 by 480 and 360 by 400 with a card open the
// predicted sky's status stood 16 px above the sheet, reaching y 9 under the screen's Close ([20, 60, 64, 104]). The status
// never rises above the top bar's foot plus 8 px (112 on the phone); where 16 px above the sheet would put it higher, the
// sheet gives the room: its top 16 px under the status's foot, its body scrolling inside, its tag row and Close at its
// top and Go there pinned at its foot. Nothing moves where the window is tall enough.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
/** The status's height with a card open (the two tags and the Milky Way line), as measured live at 390 wide. */
const STATUS_H = 93;

function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules, selector).toBe(1);
  return found;
}

/** The predicted-only sample opened into its predicted sky with its first predicted star's card open. */
function skyWithCard(): ObservationSession {
  const session = poseSession(sample('horizon_predicted_only'));
  const sky = dispatchAlbedianAction(session, {
    type: 'sky.show',
    requestId: 'test-sky-show',
    expectedViewRevision: session.viewRevision,
    mode: 'predicted',
    observationSolutionId: session.observationSolution!.id,
  }).session;
  const star = sky.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
  return { ...sky, selectedFindingId: star.id } as ObservationSession;
}

let container: HTMLDivElement;
let root: Root;
const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
const prevW = window.innerWidth;
const prevH = window.innerHeight;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains('predsky-status') ? STATUS_H : 0;
    },
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  if (offsetHeight) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', offsetHeight);
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: prevW });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: prevH });
  vi.restoreAllMocks();
});
/** Sets the window's size and tells the size hook, as a browser does. */
const resize = (w: number, h: number) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
  act(() => window.dispatchEvent(new Event('resize')));
};
const show = (w: number, h: number, extra: Record<string, unknown> = {}) => {
  resize(w, h);
  act(() => root.render(createElement(PredictedSkyScreen, { session: skyWithCard(), onHome: () => {}, phone: true, onCloseCard: () => {}, onGoThere: () => {}, ...extra })));
  return container.querySelector<HTMLElement>('[data-testid="predicted-sky"]')!;
};

describe('short-card-status-one: the status under the top bar in a short upright window with a card open', () => {
  it('reads the sheet\'s top as the phone sheet stands by its foot, and the room it gives only where the status would rise past 112', () => {
    expect(PHONE_STATUS_TOP_MIN_PX).toBe(112);
    expect(phoneSheetTopPx(844)).toBe(469);
    expect(phoneSheetTopPx(650)).toBe(275);
    expect(phoneSheetTopPx(480)).toBe(118);
    expect(phoneSheetTopPx(400)).toBe(118);
    // AUDIT-C's sizes: the status at 112, the sheet 16 px under its foot
    expect(shortCardSheetTopPx(480, STATUS_H)).toBe(112 + STATUS_H + 16);
    expect(shortCardSheetTopPx(400, STATUS_H)).toBe(221);
    // Tall enough: nothing moves (844, and 402 by 650, the app's Open sheet)
    expect(shortCardSheetTopPx(844, STATUS_H)).toBeNull();
    expect(shortCardSheetTopPx(650, STATUS_H)).toBeNull();
    // At the edge: a status whose top would stand at exactly 112 stays where it is; a pixel higher and the sheet gives
    expect(shortCardSheetTopPx(112 + STATUS_H + 16 + 375, STATUS_H)).toBeNull();
    expect(shortCardSheetTopPx(112 + STATUS_H + 16 + 374, STATUS_H)).toBe(112 + STATUS_H + 16);
    fc.assert(
      fc.property(fc.integer({ min: 320, max: 1200 }), fc.integer({ min: 20, max: 300 }), (h, statusH) => {
        const top = shortCardSheetTopPx(h, statusH);
        const natural = phoneSheetTopPx(h);
        if (top === null) expect(natural - 16 - statusH).toBeGreaterThanOrEqual(112);
        else {
          expect(natural - 16 - statusH).toBeLessThan(112);
          expect(top - 16 - statusH).toBe(112);
          // The sheet only ever gives room: it never stands higher than it would
          expect(top).toBeGreaterThan(natural);
        }
      }),
      SEED
    );
  });

  it('pins the status at 112 and sets the sheet\'s top at AUDIT-C\'s short upright sizes', () => {
    for (const [w, h] of [[390, 480], [360, 400]] as const) {
      const sky = show(w, h);
      expect(container.querySelector('[data-testid="star-card"]'), `${w}x${h}`).not.toBeNull();
      expect(sky.className, `${w}x${h}`).toBe('predsky is-phone is-card-short');
      expect(sky.style.getPropertyValue('--predsky-sheet-top'), `${w}x${h}`).toBe('221px');
      expect(container.querySelector('.predsky-status')!.className).toBe('reveal-status predsky-status is-above-card');
    }
  });

  it('moves nothing where the window is tall enough, held sideways, or with no card', () => {
    for (const [w, h] of [[390, 844], [402, 650], [375, 667], [874, 292], [874, 402], [667, 320]] as const) {
      const sky = show(w, h);
      expect(sky.className, `${w}x${h}`).toBe('predsky is-phone');
      expect(sky.style.getPropertyValue('--predsky-sheet-top'), `${w}x${h}`).toBe('');
    }
    // No card (a fresh screen: a card just closed is still leaving, in its own place): the status by its foot as at rest
    act(() => root.unmount());
    root = createRoot(container);
    resize(390, 480);
    act(() => root.render(createElement(PredictedSkyScreen, { session: { ...skyWithCard(), selectedFindingId: null } as ObservationSession, onHome: () => {}, phone: true })));
    expect(container.querySelector<HTMLElement>('[data-testid="predicted-sky"]')!.className).toBe('predsky is-phone');
    // The laptop in a short window keeps its card surface
    expect(show(800, 400, { phone: false }).className).toBe('predsky');
  });

  it('holds the rules as recorded: the status under the top bar, the sheet from its top to its foot, Go there pinned', () => {
    expect(declarationsOf('.predsky.is-phone.is-card-short .predsky-status.is-above-card')).toEqual({ top: '112px', bottom: 'auto' });
    expect(declarationsOf('.predsky.is-phone.is-card-short .reveal-card.is-phone')).toEqual({ top: 'var(--predsky-sheet-top)', height: 'auto' });
    // The body scrolls inside with no foot fade over the pinned row, as the sheet held sideways (phone-sideways-two)
    expect(declarationsOf('.predsky.is-phone.is-card-short .reveal-card.is-phone .reveal-card__scroll')).toEqual({ '-webkit-mask-image': 'none', 'mask-image': 'none' });
    expect(declarationsOf('.predsky.is-phone.is-card-short .reveal-card.is-phone .reveal-card__acts')).toEqual({
      position: 'sticky',
      bottom: '0',
      'z-index': '1',
      margin: '0',
      padding: '12px 0 14px',
      background: '#111112',
    });
  });
});
