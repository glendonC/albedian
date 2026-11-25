// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { REVEAL_KEEP_CLEAR } from '../keepClear.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// card-backing-one (UIUX-reveal-READY.md, the addition of 24 Nov 18:13; DECIDED-BY-LEAD 18:14): on the laptop, behind
// the open star card and the answer in its place, the laptop column fade (D19's values, ruled 16:40): black at 82
// percent under it, clearing over 44 px beyond its open edges (left, top, bottom), the window's right edge closing it;
// no box, no edge line, no motion of its own. The open card's box joins the keep-clear boxes: no ring or name under it,
// the picked star's mark and leader kept. The phone is unchanged.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

const session = poseSession({ ...sample('plate_calibrated') } as ObservationSolution);
const matched = session.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched');

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const show = (s: ObservationSession, phone = false) =>
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: s,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: phone ? 390 : 1440,
        viewportHeightPx: phone ? 844 : 900,
        phone,
      } as unknown as Parameters<typeof RevealPhoto>[0]),
    ),
  );
/** Each mark on screen by its finding id, at its centre. */
const marks = () =>
  new Map([...container.querySelectorAll<HTMLElement>('button.reveal-mark')].map((m) => [m.dataset.testid!.replace('reveal-mark-', ''), { x: parseFloat(m.style.left), y: parseFloat(m.style.top) }]));

describe('the open card on the laptop keeps the marks clear', () => {
  it('names the open laptop card among the places kept clear, never the phone sheet or the card in the predicted sky', () => {
    const card = document.createElement('section');
    card.className = 'reveal-card';
    const phoneCard = document.createElement('section');
    phoneCard.className = 'reveal-card is-phone';
    const skyCard = document.createElement('section');
    skyCard.className = 'reveal-card is-sky';
    const matches = (el: Element) => REVEAL_KEEP_CLEAR.some((selector) => el.matches(selector));
    expect(matches(card)).toBe(true);
    expect(matches(phoneCard)).toBe(false);
    expect(matches(skyCard)).toBe(false);
  });

  it("draws no ring or name under the open card's box, keeps the picked star's mark and leader, and brings the rest back when it closes", async () => {
    const picked = matched[0]!;
    // Where the marks stand with no card open, to set the card's box over some of them and the picked one
    show(session);
    const all = marks();
    const others = [...all.entries()].filter(([id]) => id !== picked.id);
    expect(others.length).toBeGreaterThan(1);
    const p = all.get(picked.id)!;
    const under = others.slice(0, 1);
    const xs = [p.x, ...under.map(([, m]) => m.x)];
    const ys = [p.y, ...under.map(([, m]) => m.y)];
    const box = { left: Math.min(...xs) - 30, right: Math.max(...xs) + 30, top: Math.min(...ys) - 30, bottom: Math.max(...ys) + 30 };
    const rect = (b: typeof box) => ({ ...b, x: b.left, y: b.top, width: b.right - b.left, height: b.bottom - b.top, toJSON: () => ({}) }) as DOMRect;
    const zero = rect({ left: 0, right: 0, top: 0, bottom: 0 });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.matches('.reveal-card') ? rect(box) : zero;
    });
    show({ ...session, selectedFindingId: picked.id } as ObservationSession);
    show({ ...session, selectedFindingId: picked.id } as ObservationSession);
    expect(container.querySelector('.reveal-card')).not.toBeNull();
    const now = marks();
    for (const [id] of under) expect(now.has(id), id).toBe(false);
    expect(now.has(picked.id)).toBe(true);
    expect(container.querySelector('.reveal-leader')).not.toBeNull();
    // Marks clear of the card stay
    const away = others.find(([, m]) => m.x < box.left - 20 || m.x > box.right + 20 || m.y < box.top - 20 || m.y > box.bottom + 20);
    if (away) expect(now.has(away[0])).toBe(true);
    // The card closed: while it leaves it still stands there; once it is gone they come back
    show(session);
    expect(container.querySelector('.reveal-card.is-leaving')).not.toBeNull();
    expect(marks().has(under[0]![0])).toBe(false);
    await act(async () => await new Promise((r) => setTimeout(r, 900)));
    expect(container.querySelector('.reveal-card')).toBeNull();
    show(session);
    const back = marks();
    for (const [id] of under) expect(back.has(id), id).toBe(true);
  });

  it('leaves the phone as it is: its sheet keeps no mark clear (the marks under it are inert already)', () => {
    const picked = matched[0]!;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const full = { left: 0, right: 390, top: 0, bottom: 844 };
      const r = this.matches('.reveal-card') ? full : { left: 0, right: 0, top: 0, bottom: 0 };
      return { ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) } as DOMRect;
    });
    show(session, true);
    const before = marks().size;
    show({ ...session, selectedFindingId: picked.id } as ObservationSession, true);
    show({ ...session, selectedFindingId: picked.id } as ObservationSession, true);
    expect(container.querySelector('.reveal-card.is-phone')).not.toBeNull();
    expect(marks().size).toBe(before);
  });
});

describe('the fade behind the open card and the answer on the laptop', () => {
  const rules = () => {
    const found: Record<string, Record<string, string>> = {};
    postcss.parse(css).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const decls: Record<string, string> = {};
      rule.walkDecls((d) => void (decls[d.prop] = d.value.replace(/\s+/g, ' ')));
      found[rule.selectors.map((s) => s.replace(/\s+/g, ' ')).join(', ')] = decls;
    });
    return found;
  };

  it("stands D19's values behind them: 82 percent black, clearing over 44 px to the left, top and bottom, closed at the window's right edge", () => {
    const fade = rules()['.reveal-card:not(.is-phone):not(.is-sky)::before, .reveal-answer:not(.is-phone)::before'];
    expect(fade).toEqual({
      content: "''",
      position: 'absolute',
      left: '-44px',
      top: '-44px',
      bottom: '-44px',
      right: '-32px',
      'z-index': '-1',
      'pointer-events': 'none',
      background: 'rgba(0, 0, 0, 0.82)',
      '-webkit-mask-image': 'linear-gradient(to right, transparent 0, #000 44px), linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)',
      '-webkit-mask-composite': 'source-in',
      'mask-image': 'linear-gradient(to right, transparent 0, #000 44px), linear-gradient(to bottom, transparent 0, #000 44px, #000 calc(100% - 44px), transparent 100%)',
      'mask-composite': 'intersect',
    });
    // The card's right edge sits 32 px from the window's (right 32), so the fade reaches the window's edge
    expect(rules()['.reveal-card']!.right).toBe('32px');
    expect(rules()['.reveal-answer']!.right).toBe('32px');
    // Behind their words, inside their own stacking: no box and no edge line of its own
    expect(rules()['.reveal-card:not(.is-phone):not(.is-sky), .reveal-answer:not(.is-phone)']).toEqual({ isolation: 'isolate' });
  });

  it('has no motion of its own: none in its own rule, only its block\'s entrance, and no transition', () => {
    for (const sheet of [css, motion]) {
      postcss.parse(sheet).walkRules((rule) => {
        if (!rule.selector.includes('::before') || !/reveal-card|reveal-answer/.test(rule.selector)) return;
        if (rule.selector.includes('is-phone')) return;
        // fade-with-words-one (22:12): the fade's only motion is its block's own entrance, in motion.css, each pinned to its
        // block's first words by fadeWithWords.test.ts; reveal.css gives it none, and nothing transitions it
        rule.walkDecls((d) => expect(sheet === motion ? /^transition/.test(d.prop) : /^(animation|transition)/.test(d.prop), rule.selector).toBe(false));
      });
    }
  });
});
