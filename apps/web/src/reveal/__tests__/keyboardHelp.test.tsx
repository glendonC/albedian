// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MOTION_TOKENS } from '@albedian/design-system';
import { KeyboardHelp } from '../KeyboardHelp.js';
import { blockLeaveMs } from '../../hooks/useLeaving.js';

// Keyboard help (INTERIM: .lead/uiux/UIUX-keys-QUESTION.md, waiting on the owner; values from #kh-open in
// .cache/uiux/keys/screen/kh-open.html): "?" opens a card listing the keys the app handles; Esc, the close circle or a
// click outside closes it. No movement (the lead's rule: none before the MOTION record's yes).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

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
});
const q = (selector: string) => container.querySelector(selector);
function show(props: Partial<Parameters<typeof KeyboardHelp>[0]> = {}) {
  act(() => {
    root.render(createElement(KeyboardHelp, { threeD: false, ...props }));
  });
}
function key(target: EventTarget, init: KeyboardEventInit) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}
const rows = () => [...container.querySelectorAll('.keys-help__row')].map((row) => [row.querySelector('span')!.textContent, [...row.querySelectorAll('kbd')].map((k) => k.textContent).join(' ')]);
function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let n = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    n += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(n, selector).toBe(1);
  return found;
}

describe('KeyboardHelp', () => {
  it('shows nothing until "?" is pressed, then the card titled Keyboard with each key the screen handles', () => {
    show();
    expect(q('[role="dialog"]')).toBeNull();
    expect(key(window, { key: '?', shiftKey: true }).defaultPrevented).toBe(true);
    const dialog = q('[role="dialog"]')!;
    expect(dialog.getAttribute('aria-label')).toBe('Keyboard');
    expect(dialog.querySelector('.keys-help__title')!.textContent).toBe('Keyboard');
    expect(rows()).toEqual([
      ['Ask out loud', 'Space'],
      ['Type a question', '/'],
      ['Go there', 'G'],
      ['Back to Moment', 'Esc'],
      ['Close', 'Esc'],
      ['Your photo, Real sky, 3D', '← →'],
      ['Keyboard', '?'],
    ]);
  });

  it('lists the 3D keys only where the 3D view is on screen', () => {
    show({ threeD: true });
    key(window, { key: '?' });
    expect(rows().map(([label]) => label)).toEqual([
      'Ask out loud',
      'Type a question',
      'Go there',
      'Back to Moment',
      'Close',
      'Your photo, Real sky, 3D',
      'Look around in 3D',
      'Move in 3D',
      'Keyboard',
    ]);
    expect(rows().find(([label]) => label === 'Look around in 3D')![1]).toBe('← ↑ ↓ →');
    expect(rows().find(([label]) => label === 'Move in 3D')![1]).toBe('W A S D');
  });

  it('closes with Esc before anything else hears it, with its close circle, and with a click outside the card', () => {
    show();
    const other = vi.fn((event: KeyboardEvent) => {
      if (!event.defaultPrevented) event.preventDefault();
    });
    window.addEventListener('keydown', other);
    try {
      key(window, { key: '?' });
      const esc = key(window, { key: 'Escape' });
      expect(esc.defaultPrevented).toBe(true);
      expect(q('[role="dialog"]')).toBeNull();
      // The other listener saw the key already answered
      expect(other.mock.calls.at(-1)![0].defaultPrevented).toBe(true);
    } finally {
      window.removeEventListener('keydown', other);
    }
    key(window, { key: '?' });
    act(() => (q('.keys-help button[aria-label="Close"]') as HTMLButtonElement).click());
    expect(q('[role="dialog"]')).toBeNull();
    key(window, { key: '?' });
    act(() => (q('.keys-help__veil') as HTMLElement).click());
    expect(q('[role="dialog"]')).toBeNull();
    // A click on the card itself keeps it
    key(window, { key: '?' });
    act(() => (q('.keys-help__card') as HTMLElement).click());
    expect(q('[role="dialog"]')).not.toBeNull();
  });

  it('leaves "?" typed into a field alone, and takes focus to the card while it is open', () => {
    show();
    const input = document.createElement('input');
    document.body.appendChild(input);
    try {
      expect(key(input, { key: '?' }).defaultPrevented).toBe(false);
      expect(q('[role="dialog"]')).toBeNull();
    } finally {
      input.remove();
    }
    key(window, { key: '?', ctrlKey: true });
    expect(q('[role="dialog"]')).toBeNull();
    key(window, { key: '?' });
    expect(document.activeElement).toBe(q('.keys-help__card'));
  });
});

describe("keyboard help's rules are the design lane's page", () => {
  it('centres a 420 px dark card over the screen dimmed to 60 percent, its layout rules still (its movement is in motion.css)', () => {
    expect(declarationsOf('.keys-help')).toEqual({ position: 'fixed', inset: '0', 'z-index': '60', display: 'grid', 'place-items': 'center' });
    expect(declarationsOf('.keys-help__veil')).toEqual({ position: 'absolute', inset: '0', background: 'rgba(0, 0, 0, 0.6)' });
    expect(declarationsOf('.keys-help__card')).toEqual({
      position: 'relative',
      width: '420px',
      'box-sizing': 'border-box',
      padding: '18px 22px 10px',
      'border-radius': '18px',
      background: '#111112',
      border: '1px solid var(--look-hair)',
      color: 'var(--look-ink)',
      'font-family': 'var(--look-font-sans)',
      outline: 'none',
    });
    expect(declarationsOf('.keys-help__head')).toEqual({ display: 'flex', 'align-items': 'center', 'justify-content': 'space-between', 'margin-bottom': '8px' });
    expect(declarationsOf('.keys-help__title')).toEqual({ font: '400 20px / 1 var(--look-font-sans)', 'letter-spacing': '-0.025em' });
    expect(declarationsOf('.keys-help__row')).toEqual({ display: 'flex', 'align-items': 'center', 'justify-content': 'space-between', height: '38px', 'border-top': '1px solid var(--look-hair)', font: '400 15px / 1 var(--look-font-sans)', 'letter-spacing': '-0.01em' });
    expect(declarationsOf('.keys-help kbd')).toEqual({
      'min-width': '24px',
      height: '24px',
      'box-sizing': 'border-box',
      padding: '0 7px',
      'border-radius': '7px',
      border: '1px solid rgba(244, 243, 239, 0.28)',
      display: 'inline-grid',
      'place-items': 'center',
      font: '400 12px / 1 var(--look-font-sans)',
      color: 'var(--look-ink-2)',
    });
    expect(css).not.toMatch(/\.keys-help[^{]*\{[^}]*(transition|animation)/);
  });

  // The motion record (MOTION-READY-web.md, "Keyboard help"): the card's frame in, its title 120 ms later, each row
  // 20 ms after the line above; the dimmed screen behind it fades in with the frame
  const motionOf = (selector: string): Record<string, string> => {
    const found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    return found;
  };
  const WORDS_IN = 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std)';
  const FRAME_IN = 'look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both';

  it('arrives as the motion record says: the frame and the dim, then the title, then each row 20 ms apart', () => {
    expect(motionOf('.keys-help__veil')).toEqual({ animation: FRAME_IN });
    expect(motionOf('.keys-help__card')).toEqual({ animation: FRAME_IN });
    expect(motionOf('.keys-help__head')).toEqual({ animation: `${WORDS_IN} var(--look-mo-delay-words-in) both` });
    expect(motionOf('.keys-help__row')).toEqual({
      animation: `${WORDS_IN} calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger) * (var(--keys-i) + 1)) both`,
    });
  });

  it('numbers each row for its place in the stagger, and loads the motion with the card', () => {
    show();
    key(window, { key: '?' });
    const rows = [...container.querySelectorAll<HTMLElement>('.keys-help__row')];
    expect(rows.length).toBeGreaterThan(1);
    rows.forEach((row, i) => expect(row.style.getPropertyValue('--keys-i')).toBe(String(i)));
    expect(fs.readFileSync(path.resolve(here, '../KeyboardHelp.tsx'), 'utf8')).toContain("import '../styles/motion.css';");
  });
});

// The motion record (MOTION-READY-web.md, "Any card or block" and "Keyboard help (D5)"): out, the rows go last first,
// 20 ms apart, then the title, and the frame and the dim with the title. While it leaves it is no longer a dialog and
// takes no input, so a screen reader and the keys are done with it at once.
describe('keyboard help leaves as the motion record says', () => {
  const leaving = () => q('.keys-help.is-leaving');
  it('stays on screen, inert and no longer a dialog, for its leave time after Esc, then is gone', () => {
    vi.useFakeTimers();
    try {
      show();
      key(window, { key: '?' });
      const n = container.querySelectorAll('.keys-help__row').length;
      expect((q('.keys-help') as HTMLElement).style.getPropertyValue('--keys-n')).toBe(String(n));
      key(window, { key: 'Escape' });
      expect(q('[role="dialog"]')).toBeNull();
      expect(leaving()).not.toBeNull();
      expect(leaving()!.hasAttribute('inert')).toBe(true);
      act(() => vi.advanceTimersByTime(blockLeaveMs(n + 1, MOTION_TOKENS) - 1));
      expect(leaving()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q('.keys-help')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('is gone after the one 270 ms dissolve under reduced motion', () => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' }));
    try {
      show();
      key(window, { key: '?' });
      key(window, { key: 'Escape' });
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.dissolveMs - 1));
      expect(leaving()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q('.keys-help')).toBeNull();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('comes back whole when "?" is pressed while it leaves', () => {
    vi.useFakeTimers();
    try {
      show();
      key(window, { key: '?' });
      key(window, { key: 'Escape' });
      act(() => vi.advanceTimersByTime(100));
      key(window, { key: '?' });
      expect(leaving()).toBeNull();
      expect(q('[role="dialog"]')).not.toBeNull();
      expect(q('.keys-help')!.hasAttribute('inert')).toBe(false);
      act(() => vi.advanceTimersByTime(2000));
      expect(q('[role="dialog"]')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('times the leaving in motion.css: rows last first, the title and the frame with the last of them', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        rules[rule.selector.replace(/\s+/g, ' ')] = { ...(rules[rule.selector.replace(/\s+/g, ' ')] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    const FRAME_OUT = 'look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) calc(var(--look-mo-stagger) * var(--keys-n)) both';
    expect(rules['.keys-help.is-leaving .keys-help__veil']).toEqual({ animation: FRAME_OUT });
    expect(rules['.keys-help.is-leaving .keys-help__card']).toEqual({ animation: FRAME_OUT });
    expect(rules['.keys-help.is-leaving .keys-help__head']).toEqual({
      animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * var(--keys-n)) both',
    });
    expect(rules['.keys-help.is-leaving .keys-help__row']).toEqual({
      animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * (var(--keys-n) - 1 - var(--keys-i))) both',
    });
  });
});
