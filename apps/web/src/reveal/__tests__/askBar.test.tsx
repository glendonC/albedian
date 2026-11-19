// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { AskBar } from '../AskBar.js';

// Asking on the laptop reveal (ask, approved 2026-11-12: "Ask out loud" alone, filling while it listens, the words as
// heard with the unsettled one at 42 percent; typed, decided the same night: the keyboard button and the 440 px field;
// a browser that cannot listen reads "Type to ask"). Values from #l-listen in .cache/uiux/ask/index.html and #o8a, #o8b,
// #o9 in .cache/uiux/web2/index.html.

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
  vi.restoreAllMocks();
});
const noop = () => {};
const q = (selector: string) => container.querySelector(selector);
function show(props: Partial<Parameters<typeof AskBar>[0]> = {}) {
  act(() => {
    root.render(createElement(AskBar, { canListen: true, listening: false, words: null, onListen: noop, onSend: noop, ...props }));
  });
}
function key(target: EventTarget, init: KeyboardEventInit) {
  const event = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
  act(() => {
    target.dispatchEvent(event);
  });
  return event;
}
function type(words: string) {
  const input = q('.ask-bar__field input') as HTMLInputElement;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, words);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  return input;
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

describe('AskBar: Ask out loud and the keyboard button', () => {
  it('shows "Ask out loud" with the microphone in its circle at 48 px, and the keyboard button named "Type a question"', () => {
    show();
    const ask = q('.ask-bar__ask') as HTMLButtonElement;
    expect(ask.className).toBe('look-second look-second--with-circle look-size-home ask-bar__ask');
    expect(ask.textContent).toBe('Ask out loud');
    expect(ask.querySelector('.look-second__circle path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.microphone);
    const typeButton = q('.ask-bar__type') as HTMLButtonElement;
    expect(typeButton.getAttribute('aria-label')).toBe('Type a question');
    expect(typeButton.className).toContain('look-size-home');
    expect(typeButton.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.keyboard);
    expect(q('.ask-bar__field')).toBeNull();
  });

  it('starts listening once per press, and with Space where no control holds the key', () => {
    const onListen = vi.fn();
    show({ onListen });
    act(() => (q('.ask-bar__ask') as HTMLButtonElement).click());
    expect(onListen).toHaveBeenCalledTimes(1);
    expect(key(window, { key: ' ', code: 'Space' }).defaultPrevented).toBe(true);
    expect(onListen).toHaveBeenCalledTimes(2);
    // A held key, a modifier, a focused button or field, or a key already answered start nothing
    key(window, { key: ' ', code: 'Space', repeat: true });
    key(window, { key: ' ', code: 'Space', metaKey: true });
    key(q('.ask-bar__type')!, { key: ' ', code: 'Space' });
    const done = new KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true, cancelable: true });
    done.preventDefault();
    act(() => {
      window.dispatchEvent(done);
    });
    expect(onListen).toHaveBeenCalledTimes(2);
  });

  it('fills while listening and shows the words as heard, the unsettled last word at its own weight', () => {
    show({ listening: true, words: null });
    const ask = q('.ask-bar__ask') as HTMLButtonElement;
    expect(ask.classList.contains('is-listening')).toBe(true);
    // Nothing heard yet: its own label
    expect(ask.textContent).toBe('Ask out loud');
    show({ listening: true, words: { text: 'How far away is Vega', settled: false } });
    expect(q('.ask-bar__ask')!.innerHTML).toContain('How far away is <i>Vega</i>');
    // One element for the words, so the pill's gap falls only before the circle
    expect(q('.ask-bar__ask')!.firstElementChild!.className).toBe('ask-bar__words');
    expect([...q('.ask-bar__ask')!.children].map((c) => c.className)).toEqual(['ask-bar__words', 'look-second__circle']);
    show({ listening: true, words: { text: 'How far away is Vega', settled: true } });
    expect(q('.ask-bar__ask i')).toBeNull();
    expect(q('.ask-bar__ask')!.textContent).toBe('How far away is Vega');
    // Words left from a capture that ended are not shown
    show({ listening: false, words: { text: 'How far away is Vega', settled: true } });
    expect(q('.ask-bar__ask')!.textContent).toBe('Ask out loud');
    show({ listening: true, words: { text: 'Vega', settled: false } });
    expect(q('.ask-bar__ask')!.innerHTML).toContain('<i>Vega</i>');
  });
});

describe('AskBar: Back to Moment, there (fly, approved 2026-11-12)', () => {
  it('puts "Back to Moment" first as the screen\'s one main button, with its return arrow, and goes back once per press', () => {
    const onBack = vi.fn();
    show({ onBack });
    const back = q('.ask-bar > button.look-main') as HTMLButtonElement;
    expect(back.textContent).toBe('Back to Moment');
    expect(back.querySelector('.look-main__circle path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['arrow-u-up-left']);
    expect([...q('.ask-bar')!.children].map((c) => c.className.split(' ')[0])).toEqual(['look-main', 'look-second', 'look-icon']);
    act(() => back.click());
    expect(onBack).toHaveBeenCalledTimes(1);
    show();
    expect(q('.ask-bar button.look-main')).toBeNull();
  });
});

describe('AskBar: Fly on your own, there after arrival (D26 item 6, pilot-1f)', () => {
  it('puts the outlined "Fly on your own" right after Back to Moment, once per press, and not on the phone', () => {
    const onFlyOnYourOwn = vi.fn();
    show({ onBack: noop, onFlyOnYourOwn });
    const fly = q('.ask-bar__fly') as HTMLButtonElement;
    expect(fly.textContent).toBe('Fly on your own');
    expect([...q('.ask-bar')!.children].map((c) => c.className.split(' ')[0])).toEqual(['look-main', 'look-second', 'look-second', 'look-icon']);
    expect(q('.ask-bar')!.children[1]).toBe(fly);
    act(() => fly.click());
    expect(onFlyOnYourOwn).toHaveBeenCalledTimes(1);
    show({ onBack: noop, onFlyOnYourOwn, phone: true });
    expect(q('.ask-bar__fly')).toBeNull();
    show({ onFlyOnYourOwn });
    expect(q('.ask-bar__fly')).toBeNull();
  });
});

describe('AskBar: typing a question', () => {
  it('turns the ask button into the field in place, focused, from the keyboard button or "/"', () => {
    show();
    act(() => (q('.ask-bar__type') as HTMLButtonElement).click());
    expect(q('.ask-bar__ask')).toBeNull();
    expect(q('.ask-bar__type')).toBeNull();
    const input = q('.ask-bar__field input') as HTMLInputElement;
    expect(document.activeElement).toBe(input);
    expect(input.getAttribute('aria-label')).toBe('Type a question');
    const send = q('.ask-bar__send') as HTMLButtonElement;
    expect(send.getAttribute('aria-label')).toBe('Send');
    expect(send.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['arrow-up']);
    // Esc closes it and the buttons come back
    key(input, { key: 'Escape' });
    expect(q('.ask-bar__field')).toBeNull();
    expect(key(window, { key: '/' }).defaultPrevented).toBe(true);
    expect(q('.ask-bar__field input')).not.toBeNull();
  });

  it('opens the field from "/" while a button has focus, which keeps Space for its own press (FIELD walk-0900 item 3)', () => {
    show();
    const button = q('.ask-bar__type') as HTMLButtonElement;
    button.focus();
    expect(key(button, { key: '/' }).defaultPrevented).toBe(true);
    expect(q('.ask-bar__field input')).not.toBeNull();
    // A field keeps "/" as a character
    const input = q('.ask-bar__field input') as HTMLInputElement;
    expect(key(input, { key: '/' }).defaultPrevented).toBe(false);
  });

  it('sends the words with Enter or the arrow, then closes, and sends nothing empty', () => {
    const onSend = vi.fn();
    show({ onSend });
    act(() => (q('.ask-bar__type') as HTMLButtonElement).click());
    expect((q('.ask-bar__send') as HTMLButtonElement).disabled).toBe(true);
    type('   ');
    act(() => (q('.ask-bar__field') as HTMLFormElement).requestSubmit());
    expect(onSend).not.toHaveBeenCalled();
    type('How far away is Vega?');
    act(() => (q('.ask-bar__send') as HTMLButtonElement).click());
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('How far away is Vega?');
    expect(q('.ask-bar__field')).toBeNull();
    act(() => (q('.ask-bar__type') as HTMLButtonElement).click());
    expect((q('.ask-bar__field input') as HTMLInputElement).value).toBe('');
    type('Is that a plane?');
    act(() => (q('.ask-bar__field') as HTMLFormElement).requestSubmit());
    expect(onSend).toHaveBeenLastCalledWith('Is that a plane?');
  });

  it('reads "Type to ask" with the keyboard where the browser cannot listen, opens the field, and drops the keyboard button and Space', () => {
    const onListen = vi.fn();
    show({ canListen: false, onListen });
    const ask = q('.ask-bar__ask') as HTMLButtonElement;
    expect(ask.textContent).toBe('Type to ask');
    expect(ask.querySelector('.look-second__circle path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.keyboard);
    expect(q('.ask-bar__type')).toBeNull();
    key(window, { key: ' ', code: 'Space' });
    expect(onListen).not.toHaveBeenCalled();
    act(() => ask.click());
    expect(q('.ask-bar__field input')).not.toBeNull();
  });
});

describe("the ask bar's rules are the approved pages'", () => {
  it('sits bottom centre, 10 px between its parts, each on the dark glass', () => {
    expect(declarationsOf('.ask-bar')).toEqual({
      position: 'fixed',
      left: '50%',
      bottom: '32px',
      transform: 'translateX(-50%)',
      'z-index': '30',
      display: 'flex',
      gap: '10px',
      'align-items': 'center',
      'pointer-events': 'auto',
    });
    expect(declarationsOf('.ask-bar__ask, .ask-bar__type')).toEqual({
      background: 'rgba(10, 10, 12, 0.55)',
      '-webkit-backdrop-filter': 'blur(14px)',
      'backdrop-filter': 'blur(14px)',
    });
    expect(declarationsOf('.ask-bar .look-i')).toEqual({ '--look-ic': '18px' });
    expect(declarationsOf('.ask-bar__type .look-i')).toEqual({ '--look-ic': '19px' });
  });

  it('fills the ask button in ink while listening, its circle dark with an ink microphone, the unsettled word at 42 percent', () => {
    expect(declarationsOf('.ask-bar__ask')).toEqual({
      transition: 'background-color 0.2s var(--look-ease-standard), color 0.2s var(--look-ease-standard), border-color 0.2s var(--look-ease-standard)',
    });
    expect(declarationsOf('.ask-bar__ask.is-listening')).toEqual({ background: 'var(--look-ink)', color: 'var(--look-night)', 'border-color': 'var(--look-ink)' });
    expect(declarationsOf('.ask-bar__ask.is-listening .look-second__circle')).toEqual({ background: 'var(--look-night)', color: 'var(--look-ink)' });
    expect(declarationsOf('.ask-bar__ask i')).toEqual({ 'font-style': 'normal', opacity: '0.42' });
  });

  it('draws the field 440 by 48 in a 1 px ink outline with the words at 17 px, and a 40 px filled circle that sends', () => {
    expect(declarationsOf('.ask-bar__field')).toEqual({
      height: '48px',
      width: '440px',
      'box-sizing': 'border-box',
      'border-radius': '9999px',
      border: '1px solid var(--look-ink)',
      background: 'rgba(10, 10, 12, 0.7)',
      '-webkit-backdrop-filter': 'blur(14px)',
      'backdrop-filter': 'blur(14px)',
      display: 'flex',
      'align-items': 'center',
      padding: '0 4px 0 22px',
      font: '400 17px / 1 var(--look-font-sans)',
      'letter-spacing': '-0.01em',
      color: 'var(--look-ink)',
    });
    expect(declarationsOf('.ask-bar__field input')).toMatchObject({ flex: '1', 'min-width': '0', border: '0', background: 'transparent', font: 'inherit', color: 'inherit', outline: 'none' });
    expect(declarationsOf('.ask-bar__send')).toMatchObject({ width: '40px', height: '40px', 'border-radius': '50%', background: 'var(--look-ink)', color: 'var(--look-night)', display: 'grid', 'place-items': 'center' });
  });
});

describe('AskBar at phone width (ask, approved 2026-11-12, iPhone; #p-answer)', () => {
  it('shows Ask out loud alone, with no keyboard button', () => {
    show({ phone: true });
    expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar is-phone');
    expect(q('.ask-bar__ask')!.textContent).toContain('Ask out loud');
    expect(q('.ask-bar__type')).toBeNull();
  });

  it('shows Back to Moment alone there, at the phone size (#p-there .bar)', () => {
    show({ phone: true, onBack: () => {} });
    const buttons = [...container.querySelectorAll('[data-testid="ask-bar"] button')];
    expect(buttons.map((button) => button.textContent)).toEqual(['Back to Moment']);
    expect(q('.ask-bar__ask')).toBeNull();
  });

  it('keeps the keyboard button on the laptop', () => {
    show();
    expect(q('[data-testid="ask-bar"]')!.className).toBe('ask-bar');
    expect(q('.ask-bar__type')).not.toBeNull();
  });
});

// The motion record (MOTION-READY-web.md, "Go there and Back to Moment"): Back to Moment arrives once the camera is
// still, after both halves of the flight; under reduced motion the camera half is 0, so it comes at once
describe('Back to Moment arrives when the camera is at rest', () => {
  it('is the bar\'s only main button, there and only there', () => {
    show();
    expect(container.querySelector('.ask-bar > .look-main')).toBeNull();
    act(() => root.unmount());
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    show({ onBack: () => {} });
    expect(container.querySelector('.ask-bar > .look-main')!.textContent).toBe('Back to Moment');
  });

  it('words in after the two halves of the flight, and loads the motion with the bar', () => {
    let found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') === '.ask-bar > .look-main' && rule.parent?.type === 'root') {
        rule.walkDecls((decl) => {
          found = { ...found, [decl.prop]: decl.value.replace(/\s+/g, ' ') };
        });
      }
    });
    expect(found).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-dur-camera-half) * 2) both' });
    expect(fs.readFileSync(path.resolve(here, '../AskBar.tsx'), 'utf8')).toContain("import '../styles/motion.css';");
  });
});

describe('Ask out loud arrives after the reveal\'s marks', () => {
  it('words in after the marks, two staggers after the status\'s place', () => {
    let found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') === '.ask-bar' && rule.parent?.type === 'root') {
        rule.walkDecls((decl) => {
          found = { ...found, [decl.prop]: decl.value.replace(/\s+/g, ' ') };
        });
      }
    });
    expect(found).toEqual({
      animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-span-marks) + var(--look-mo-delay-words-in) + var(--look-mo-stagger) * 2) both',
    });
  });
});
