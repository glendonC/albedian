// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { leaderPath, revealMarks } from '../revealMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { StarCard } from '../StarCard.js';
import { starCardFacts } from '../starCardFacts.js';

// The picked star's card on the screen (reveal, approved 2026-11-12, laptop screen 3; numberglyph, cautions and
// original, decided the same night). Sizes are the approved pages' (#l-pick in .cache/uiux/reveal/index.html, #o456
// and #o7 in .cache/uiux/web2/index.html).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const VIEW = { w: 1440, h: 900 };
const session = poseSession(sample('plate_calibrated'));
const betelgeuse = session.observationSolution!.findings.find((f) => f.title === 'Betelgeuse')!;
const picked = (finding: CelestialFinding = betelgeuse): ObservationSession => ({ ...session, selectedFindingId: finding.id });

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
const noop = () => {};
const q = (selector: string) => container.querySelector(selector);
const all = (selector: string) => [...container.querySelectorAll(selector)];
function showCard(finding: CelestialFinding, props: Partial<Parameters<typeof StarCard>[0]> = {}) {
  act(() => {
    root.render(createElement(StarCard, { finding, originalPixels: false, onToggleOriginalPixels: noop, onGoThere: noop, onClose: noop, ...props }));
  });
}
function showReveal(s: ObservationSession, props: Partial<Parameters<typeof RevealPhoto>[0]> = {}) {
  act(() => {
    root.render(createElement(RevealPhoto, { session: s, onSelectFinding: noop, onHome: noop, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, ...props }));
  });
}
const key = (k: string, target: EventTarget = window, init: KeyboardEventInit = {}) => {
  const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
};
function tap(target: Element, moved = 0) {
  act(() => {
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 100, clientY: 100 }));
    target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 100 + moved, clientY: 100 }));
  });
}
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

describe('StarCard: the approved card, word for word', () => {
  it("shows the star's tag and a close button, its name, its numbers each with its own glyph, the catalog row and the two actions", () => {
    showCard(betelgeuse);
    const facts = starCardFacts(betelgeuse);
    const card = q('[data-testid="star-card"]')!;
    expect(card.querySelector('.reveal-card__top .look-tag')!.getAttribute('data-evidence-class')).toBe('catalog_matched');
    const close = card.querySelector('.reveal-card__top button.look-icon')!;
    expect(close.getAttribute('aria-label')).toBe('Close');
    expect(close.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.x);
    expect(card.querySelector('h1')!.textContent).toBe('Betelgeuse');
    expect(all('.reveal-card__number').map((n) => [n.querySelector('b')!.textContent, n.querySelector('.reveal-card__label')!.textContent, n.getAttribute('data-evidence-class')])).toEqual(
      facts.numbers.map((n) => [`${n.value}${n.unit}`, n.label, n.evidenceClass])
    );
    // Each number's words lead with the glyph of the number's own class
    expect(all('.reveal-card__label .look-glyph').map((g) => g.getAttribute('data-evidence-class'))).toEqual(facts.numbers.map((n) => n.evidenceClass));
    expect(facts.numbers.map((n) => n.evidenceClass)).toEqual(['catalog_matched', 'observed']);
    expect(q('.reveal-card__source')!.textContent).toBe(facts.sourceLine);
    expect(all('.reveal-card__acts button').map((b) => [b.className.split(' ')[0], b.textContent])).toEqual([
      ['look-main', 'Go there'],
      ['look-second', 'Original pixels'],
    ]);
    // The arrow of the one main action turns on hover, as approved
    expect(q('.reveal-card__acts .look-main svg')!.classList.contains('look-turn')).toBe(true);
  });

  it("writes the star's limitations at its foot, each with the info icon, and no list when it has none", () => {
    showCard({ ...betelgeuse, evidence: { ...betelgeuse.evidence, limitations: ['First limit.', 'Second limit.'] } });
    const notes = all('.reveal-card__notes li');
    expect(notes.map((n) => n.textContent)).toEqual(['First limit.', 'Second limit.']);
    for (const note of notes) expect(note.querySelector('svg.look-i path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.info);
    showCard({ ...betelgeuse, evidence: { ...betelgeuse.evidence, limitations: [] } });
    expect(q('.reveal-card__notes')).toBeNull();
  });

  it('offers no Go there for a star the app cannot fly to, and keeps Original pixels', () => {
    showCard({ ...betelgeuse, evidenceClass: 'predicted', sourceDetection: undefined } as CelestialFinding);
    expect(all('.reveal-card__acts button').map((b) => b.textContent)).toEqual(['Original pixels']);
    expect(q('.look-main')).toBeNull();
  });

  it('goes there once per press, and with G, but not while typing, with a modifier, or from a focused button', () => {
    const onGoThere = vi.fn();
    showCard(betelgeuse, { onGoThere });
    act(() => (q('.reveal-card__acts .look-main') as HTMLButtonElement).click());
    expect(onGoThere).toHaveBeenCalledTimes(1);
    expect(key('g').defaultPrevented).toBe(true);
    key('G');
    expect(onGoThere).toHaveBeenCalledTimes(3);
    key('g', window, { metaKey: true });
    key('g', window, { ctrlKey: true });
    key('g', window, { altKey: true });
    key('g', window, { repeat: true });
    key('g', q('.reveal-card__acts .look-second')!);
    const input = document.createElement('input');
    container.appendChild(input);
    key('g', input);
    key('h');
    expect(onGoThere).toHaveBeenCalledTimes(3);
  });

  it('does not go there with G for a star it cannot fly to', () => {
    const onGoThere = vi.fn();
    showCard({ ...betelgeuse, evidenceClass: 'predicted', sourceDetection: undefined } as CelestialFinding, { onGoThere });
    expect(key('g').defaultPrevented).toBe(false);
    expect(onGoThere).not.toHaveBeenCalled();
  });

  it('closes from its button, from Esc, and from a tap on empty sky, but not from a drag, a tap on the card or on another control', () => {
    const onClose = vi.fn();
    showCard(betelgeuse, { onClose });
    act(() => (q('.reveal-card__top button') as HTMLButtonElement).click());
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(key('Escape').defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(2);
    const sky = document.createElement('div');
    const other = document.createElement('button');
    container.append(sky, other);
    tap(sky);
    expect(onClose).toHaveBeenCalledTimes(3);
    tap(sky, 7);
    tap(q('.reveal-card__name')!);
    tap(other);
    const input = document.createElement('input');
    container.appendChild(input);
    key('Escape', input);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('stops listening for keys and taps once it is gone', () => {
    const onClose = vi.fn();
    showCard(betelgeuse, { onClose });
    act(() => root.render(createElement('div')));
    key('Escape');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('says whether Original pixels is pressed, and toggles it once per press', () => {
    const onToggleOriginalPixels = vi.fn();
    showCard(betelgeuse, { onToggleOriginalPixels });
    const button = () => q('.reveal-card__acts .look-second') as HTMLButtonElement;
    expect(button().getAttribute('aria-pressed')).toBe('false');
    act(() => button().click());
    expect(onToggleOriginalPixels).toHaveBeenCalledTimes(1);
    showCard(betelgeuse, { originalPixels: true });
    expect(button().getAttribute('aria-pressed')).toBe('true');
  });
});

describe('the reveal with a star picked', () => {
  it('shows the card for the picked star and a line from its name to the card, and neither with no star picked', () => {
    showReveal(session);
    expect(q('[data-testid="star-card"]')).toBeNull();
    expect(q('[data-testid="reveal-leader"]')).toBeNull();
    showReveal(picked());
    expect(q('[data-testid="star-card"] h1')!.textContent).toBe('Betelgeuse');
    const mark = revealMarks(picked(), VIEW.w, VIEW.h).find((m) => m.picked)!;
    expect(q('[data-testid="reveal-leader"] path')!.getAttribute('d')).toBe(leaderPath(mark, VIEW.w));
  });

  it("draws the leader as the approved page does for Vega: level from past the name, then down to the card's corner", () => {
    const vega = { xPx: 796.3, yPx: 91.5, name: 'Vega', nameOnLeft: false } as Parameters<typeof leaderPath>[0];
    expect(leaderPath(vega, 1440)).toBe('M876.3 91.5H1048L1078 132');
    // From the outer ring when the name is on the left, and straight to the card past the turn
    expect(leaderPath({ ...vega, nameOnLeft: true }, 1440)).toBe('M824.3 91.5H1048L1078 132');
    expect(leaderPath({ ...vega, xPx: 1040, nameOnLeft: true }, 1440)).toBe('M1068 91.5L1078 132');
    // The card keeps its place from the window's right edge
    expect(leaderPath(vega, 1600)).toBe('M876.3 91.5H1208L1238 132');
  });

  it('passes the card its actions: Go there with the star, close, and Original pixels', () => {
    const onGoThere = vi.fn();
    const onCloseCard = vi.fn();
    const onToggleOriginalPixels = vi.fn();
    showReveal(picked(), { onGoThere, onCloseCard, onToggleOriginalPixels });
    act(() => (q('.reveal-card__acts .look-main') as HTMLButtonElement).click());
    expect(onGoThere).toHaveBeenCalledWith(betelgeuse.id);
    act(() => (q('.reveal-card__acts .look-second') as HTMLButtonElement).click());
    expect(onToggleOriginalPixels).toHaveBeenCalledTimes(1);
    act(() => (q('.reveal-card__top button') as HTMLButtonElement).click());
    expect(onCloseCard).toHaveBeenCalledTimes(1);
  });

  it('with Original pixels pressed shows no mark, corner or line, and keeps the card, the status and the legend', () => {
    showReveal(picked(), { originalPixels: true });
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('[data-testid="reveal-leader"]')).toBeNull();
    expect(q('[data-testid="star-card"]')).not.toBeNull();
    expect(q('.reveal-card__acts .look-second')!.getAttribute('aria-pressed')).toBe('true');
    expect(q('.reveal-status__line')).not.toBeNull();
    expect(q('.reveal-legend')).not.toBeNull();
  });
});

describe("the card's rules are the approved pages'", () => {
  it('sits in the right column, 320 wide, and rises 14 px on the spring as it fades in, with no motion when reduced', () => {
    expect(declarationsOf('.reveal-card')).toEqual({
      position: 'absolute',
      right: '32px',
      top: '118px',
      width: '320px',
      'pointer-events': 'auto',
      animation: 'reveal-card-fade 500ms var(--look-ease-standard) both, reveal-card-rise 900ms var(--look-ease-spring) both',
    });
    const text = css.replace(/\s+/g, ' ');
    expect(text).toContain('@keyframes reveal-card-rise { from { transform: translateY(14px); } }');
    expect(text).toContain('@keyframes reveal-card-fade { from { opacity: 0; } }');
    expect(text).toContain('@media (prefers-reduced-motion: reduce) { .reveal-card { animation: none; } }');
  });

  it('sets the name at 66 px, the line under it at 15 px, the numbers at 30 px with a 15 px unit', () => {
    expect(declarationsOf('.reveal-card__name')).toEqual({ margin: '22px 0 0', font: '400 66px / 1 var(--look-font-sans)', 'letter-spacing': '-0.05em', 'overflow-wrap': 'anywhere' });
    expect(declarationsOf('.reveal-card__sub')).toEqual({ margin: '10px 0 0', 'font-size': '15px', 'letter-spacing': '-0.01em', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.reveal-card__numbers')).toEqual({
      display: 'grid',
      'grid-template-columns': '1fr 1fr',
      gap: '16px',
      'margin-top': '30px',
      'padding-top': '20px',
      'border-top': '1px solid var(--look-hair)',
    });
    expect(declarationsOf('.reveal-card__number b')).toMatchObject({ 'font-size': '30px', 'font-weight': '400', 'letter-spacing': '-0.04em' });
    expect(declarationsOf('.reveal-card__number b small')).toEqual({ 'margin-left': '3px', 'font-size': '15px', 'letter-spacing': '-0.01em', color: 'var(--look-ink-2)' });
  });

  it("leads each number's words with a 12 px glyph 6 px before them, in its class's own colour", () => {
    expect(declarationsOf('.reveal-card__label')).toEqual({ display: 'flex', 'align-items': 'center', gap: '6px', 'margin-top': '8px', 'font-size': '13px', color: 'var(--look-ink-3)' });
    expect(declarationsOf('.reveal-card__label .look-glyph')).toEqual({ width: '12px', height: '12px' });
    expect(declarationsOf(".reveal-card__number[data-evidence-class='observed'] .look-glyph")).toEqual({ color: 'var(--look-ev-observed)' });
    expect(declarationsOf(".reveal-card__number[data-evidence-class='catalog_matched'] .look-glyph")).toEqual({ color: 'var(--look-ev-catalog)' });
    expect(declarationsOf(".reveal-card__number[data-evidence-class='predicted'] .look-glyph")).toEqual({ color: 'var(--look-ev-predicted)' });
    expect(declarationsOf(".reveal-card__number[data-evidence-class='simulated'] .look-glyph")).toEqual({ color: 'var(--look-ev-simulated)' });
  });

  it('fills Original pixels while it is pressed, and sets the notes in 12 px at the quietest ink under a hairline', () => {
    expect(declarationsOf(".reveal-card__acts .look-second[aria-pressed='true']")).toEqual({
      background: 'var(--look-ink)',
      'border-color': 'var(--look-ink)',
      color: 'var(--look-night)',
    });
    expect(declarationsOf('.reveal-card__acts')).toEqual({ display: 'flex', 'align-items': 'center', gap: '10px', 'margin-top': '28px' });
    expect(declarationsOf('.reveal-card__notes')).toEqual({
      display: 'grid',
      gap: '8px',
      margin: '22px 0 0',
      padding: '14px 0 0',
      'border-top': '1px solid var(--look-hair)',
      'list-style': 'none',
    });
    expect(declarationsOf('.reveal-card__notes li')).toEqual({ '--look-ic': '13px', display: 'flex', gap: '7px', 'font-size': '12px', 'line-height': '1.45', color: 'var(--look-ink-3)' });
    expect(declarationsOf('.reveal-card__source')).toEqual({
      margin: '20px 0 0',
      'padding-top': '14px',
      'border-top': '1px solid var(--look-hair)',
      'font-size': '12px',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-3)',
    });
  });

  it('draws the leader as a hairline at 35 percent ink', () => {
    expect(declarationsOf('.reveal-leader')).toMatchObject({ fill: 'none', stroke: 'rgba(244, 243, 239, 0.35)', 'stroke-width': '1' });
  });
});
