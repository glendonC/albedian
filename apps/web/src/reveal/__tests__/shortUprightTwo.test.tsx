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
import { PHONE_STATUS_FOOT_PX, PHONE_STATUS_TOP_MIN_PX, restStatusRisesPastTop } from '../shortCard.js';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// short-upright-two (UIUX-predsky-READY.md U1 and U2, DECIDED-BY-LEAD at the line stamped 12:39 on 27 Nov; AUDIT-C's O22
// and O23 in reports/AUDIT-C/short-card-status-one.md). U1: at 360 by 400 upright with no card the predicted sky's status
// grew up from its foot to y 42, its Predicted tag on Close; where the whole status would rise above the top bar's foot
// plus 8 px (112) it takes the sideways form (the two tags and, 8 px under them, the Milky Way line) by its foot 16 px
// above Back to Moment on D19's fade, and returns whole when the window has room. U2: at 402 by 650 and 375 by 667 Go
// there sat below the sheet's visible foot; whenever the card's body is taller than the sheet shows, Go there is pinned
// at the sheet's foot, the body scrolling inside above it; when the body fits, nothing changes.

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
/** The whole status's height at 360 wide (AUDIT-C: from its foot at 312 up to y 42), and the tags and the line alone. */
const WHOLE_H = 270;
const TAGS_H = 70;

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

/** The predicted-only sample opened into its predicted sky, with its first predicted star's card open or not. */
function sky(withCard: boolean): ObservationSession {
  const session = poseSession(sample('horizon_predicted_only'));
  const shown = dispatchAlbedianAction(session, {
    type: 'sky.show',
    requestId: 'test-sky-show',
    expectedViewRevision: session.viewRevision,
    mode: 'predicted',
    observationSolutionId: session.observationSolution!.id,
  }).session;
  const star = shown.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
  return { ...shown, selectedFindingId: withCard ? star.id : null } as ObservationSession;
}

let container: HTMLDivElement;
let root: Root;
const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
const prevW = window.innerWidth;
const prevH = window.innerHeight;
/** The card's body as the browser would lay it out: its content and the part the sheet shows. */
const body = { content: 0, shown: 0 };
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  body.content = 0;
  body.shown = 0;
  // The status laid out: whole while it holds its title, the tags and the line alone otherwise
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get(this: HTMLElement) {
      if (!this.classList.contains('predsky-status')) return 0;
      return this.querySelector('.predsky-status__lead') ? WHOLE_H : TAGS_H;
    },
  });
  vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
    return this.classList.contains('reveal-card__scroll') ? body.content : 0;
  });
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: Element) {
    return this.classList.contains('reveal-card__scroll') ? body.shown : 0;
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
const resize = (w: number, h: number) => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: h });
  act(() => window.dispatchEvent(new Event('resize')));
};
const show = (w: number, h: number, withCard: boolean, extra: Record<string, unknown> = {}) => {
  resize(w, h);
  act(() => root.render(createElement(PredictedSkyScreen, { session: sky(withCard), onHome: () => {}, phone: true, onCloseCard: () => {}, onGoThere: () => {}, ...extra })));
  return container.querySelector<HTMLElement>('[data-testid="predicted-sky"]')!;
};
const status = () => container.querySelector<HTMLElement>('.predsky-status')!;
const whole = () => status().querySelector('.predsky-status__lead') !== null;

describe('short-upright-two U1: the status takes the sideways form where it would rise past the top bar', () => {
  it('reads the rise from the whole status standing by its foot at 88, 16 px above Back to Moment', () => {
    expect(PHONE_STATUS_FOOT_PX).toBe(88);
    expect(declarationsOf('.predsky.is-phone .predsky-status')).toMatchObject({ bottom: '88px' });
    // AUDIT-C's O22: at 360 by 400 the whole status reached y 42
    expect(restStatusRisesPastTop(400, WHOLE_H)).toBe(true);
    expect(restStatusRisesPastTop(844, WHOLE_H)).toBe(false);
    // At the edge: a top at exactly 112 stays whole; a pixel higher and it takes the sideways form
    expect(restStatusRisesPastTop(112 + 88 + WHOLE_H, WHOLE_H)).toBe(false);
    expect(restStatusRisesPastTop(112 + 88 + WHOLE_H - 1, WHOLE_H)).toBe(true);
    fc.assert(
      fc.property(fc.integer({ min: 320, max: 1200 }), fc.integer({ min: 0, max: 600 }), (h, statusH) => {
        expect(restStatusRisesPastTop(h, statusH)).toBe(h - PHONE_STATUS_FOOT_PX - statusH < PHONE_STATUS_TOP_MIN_PX);
      }),
      SEED
    );
  });

  it('at 360 by 400 with no card stands the two tags and the Milky Way line alone, in the upright place and fade', () => {
    show(360, 400, false);
    const el = status();
    expect(whole()).toBe(false);
    expect(el.className).toBe('reveal-status predsky-status');
    expect([...el.querySelectorAll('.predsky-status__tags > [data-evidence-class]')].map((tag) => tag.getAttribute('data-evidence-class'))).toEqual(['predicted', 'simulated']);
    expect(el.querySelector('.predsky-status__sky')!.textContent).not.toBe('');
    expect(el.querySelector('.predsky-status__note')).toBeNull();
    expect(el.querySelector('.predsky-status__facts')).toBeNull();
    // The tags and the line in the same order as held sideways: tags, then the line
    expect([...el.children].map((child) => child.className)).toEqual(['predsky-status__tags', 'predsky-status__sky']);
    // It stands 400 - 88 - 70 = 242 from the top, clear of Close ([20, 60, 64, 104])
    expect(400 - PHONE_STATUS_FOOT_PX - TAGS_H).toBe(242);
  });

  it('keeps the status whole where the window has room, and returns it whole when the window grows', () => {
    for (const [w, h] of [[390, 844], [402, 650], [375, 667], [390, 480]] as const) {
      show(w, h, false);
      expect(whole(), `${w}x${h}`).toBe(true);
      expect(status().querySelector('.predsky-status__facts'), `${w}x${h}`).not.toBeNull();
    }
    show(360, 400, false);
    expect(whole()).toBe(false);
    show(390, 844, false);
    expect(whole()).toBe(true);
    show(360, 400, false);
    expect(whole()).toBe(false);
    // The same width, taller: whole again; a shorter one still: decided from the whole status, never from the tags alone
    show(360, 844, false);
    expect(whole()).toBe(true);
    show(360, 400, false);
    expect(whole()).toBe(false);
    show(360, 380, false);
    expect(whole()).toBe(false);
  });

  it('decides nothing while a card is open, so the status takes the sideways form once the card has gone', () => {
    vi.useFakeTimers();
    try {
      show(360, 400, true);
      expect(status().className).toBe('reveal-status predsky-status is-above-card');
      show(360, 400, false);
      act(() => vi.advanceTimersByTime(3000));
      expect(container.querySelector('[data-testid="star-card"]')).toBeNull();
      expect(status().className).toBe('reveal-status predsky-status');
      expect(whole()).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('leaves the card state, the sideways form and the laptop as they were', () => {
    // A card open in the short window: the status above it as short-card-status-one built it
    show(360, 400, true);
    expect(status().className).toBe('reveal-status predsky-status is-above-card');
    act(() => root.unmount());
    root = createRoot(container);
    // Held sideways: its own form and column
    show(874, 292, false);
    expect(status().className).toBe('reveal-status predsky-status is-sideways');
    act(() => root.unmount());
    root = createRoot(container);
    // The laptop in a short window keeps its whole status
    show(800, 330, false, { phone: false });
    expect(whole()).toBe(true);
  });
});

describe('short-upright-two U2: Go there pinned at the sheet\'s foot whenever the card\'s body is taller than the sheet shows', () => {
  it('pins the row where the body overflows at 402 by 650 and 375 by 667, and changes nothing where it fits', () => {
    for (const [w, h] of [[402, 650], [375, 667], [390, 844]] as const) {
      body.content = 420;
      body.shown = 317;
      act(() => root.unmount());
      root = createRoot(container);
      expect(show(w, h, true).className, `${w}x${h} overflows`).toBe('predsky is-phone is-card-pinned');
      expect(container.querySelector('[data-testid="star-card"]'), `${w}x${h}`).not.toBeNull();
      body.content = 300;
      act(() => root.unmount());
      root = createRoot(container);
      expect(show(w, h, true).className, `${w}x${h} fits`).toBe('predsky is-phone');
    }
  });

  it('reads the overflow with half a pixel of layout slack, and lets the pin go once the pinned body fits', () => {
    body.shown = 317;
    body.content = 317.5;
    expect(show(402, 650, true).className).toBe('predsky is-phone');
    body.content = 318;
    expect(show(402, 650, true).className).toBe('predsky is-phone is-card-pinned');
    // Pinned, the body that still overflows stays pinned; once it fits, the row is let go
    body.content = 320;
    expect(show(402, 650, true).className).toBe('predsky is-phone is-card-pinned');
    body.content = 310;
    expect(show(402, 650, true).className).toBe('predsky is-phone');
  });

  it('leaves the short case, the sideways sheet, no card and the laptop as they were', () => {
    body.content = 420;
    body.shown = 200;
    // The short case keeps its own class (its rules pin the row already)
    expect(show(390, 480, true).className).toBe('predsky is-phone is-card-short');
    act(() => root.unmount());
    root = createRoot(container);
    // Held sideways the sheet pins its row by its own rules
    expect(show(874, 292, true).className).toBe('predsky is-phone');
    act(() => root.unmount());
    root = createRoot(container);
    expect(show(402, 650, false).className).toBe('predsky is-phone');
    act(() => root.unmount());
    root = createRoot(container);
    expect(show(1280, 800, true, { phone: false }).className).toBe('predsky');
  });

  it('holds the pinned rules as the short case\'s, the sheet keeping its place and size', () => {
    const shortScroll = declarationsOf('.predsky.is-phone.is-card-short .reveal-card.is-phone .reveal-card__scroll');
    const shortActs = declarationsOf('.predsky.is-phone.is-card-short .reveal-card.is-phone .reveal-card__acts');
    expect(declarationsOf('.predsky.is-phone.is-card-pinned .reveal-card.is-phone .reveal-card__scroll')).toEqual(shortScroll);
    expect(declarationsOf('.predsky.is-phone.is-card-pinned .reveal-card.is-phone .reveal-card__acts')).toEqual(shortActs);
    expect(shortActs).toEqual({ position: 'sticky', bottom: '0', 'z-index': '1', margin: '0', padding: '12px 0 14px', background: '#111112' });
    let sheetRules = 0;
    postcss.parse(css).walkRules((rule) => {
      if (rule.selector.includes('is-card-pinned') && !rule.selector.includes('.reveal-card__')) sheetRules += 1;
    });
    expect(sheetRules).toBe(0);
  });
});
