// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { SoundControl, levelAtHeight } from '../SoundControl.js';

// The sound control after the home (sound, approved by the owner on 2026-11-18 20:39; build note
// .lead/uiux/UIUX-sound-READY.md; rules from #so-on in .cache/uiux/sound/screen/so-on.html): the approved icon circle,
// and while sound is on, on hover or focus, a volume capsule with the app's own disclosure sentence beside it.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const SENTENCE = 'No sound reaches us from space. The star notes are made from catalog data and stated design choices. Composed music plays alongside them. None of it is a recording of space.';

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
function show(props: Partial<Parameters<typeof SoundControl>[0]> = {}) {
  act(() => {
    root.render(createElement(SoundControl, { on: true, onToggle: noop, level: 0.7, onLevel: noop, disclosure: SENTENCE, ...props }));
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

describe('SoundControl: the circle', () => {
  it('is the approved icon circle named Sound, with the speaker while on, and says it is pressed without filling', () => {
    show();
    const button = q('.sound-control button.look-icon') as HTMLButtonElement;
    expect(button.getAttribute('aria-label')).toBe('Sound');
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(button.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['speaker-high']);
    // The icon says on or off; the circle fills only on hover or press, as every approved icon circle
    expect(button.classList.contains('is-on')).toBe(false);
    // The phone lifecycle ignores a tap on this control, and only on it
    expect(button.getAttribute('data-audio-control')).toBe('toggle');
    expect(q('.sound-control')!.className).toBe('sound-control is-on');
  });

  it('shows the struck speaker while off, says it is not pressed, and shows nothing else', () => {
    show({ on: false });
    const button = q('.sound-control button.look-icon') as HTMLButtonElement;
    expect(button.getAttribute('aria-pressed')).toBe('false');
    expect(button.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['speaker-slash']);
    expect(q('.sound-control')!.className).toBe('sound-control is-off');
    expect(q('.sound-control__pop')).toBeNull();
    expect(button.hasAttribute('aria-describedby')).toBe(false);
    expect(q('.sound-control')!.textContent).toBe('');
  });

  it('switches sound once per press', () => {
    const onToggle = vi.fn();
    show({ onToggle });
    act(() => (q('.sound-control button.look-icon') as HTMLButtonElement).click());
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe('SoundControl: what the sound is, and its volume, while on', () => {
  it("carries the app's own sentence, word for word, as the circle's description", () => {
    show();
    const sentence = q('.sound-control__pop p')!;
    expect(sentence.textContent).toBe(SENTENCE);
    expect(sentence.getAttribute('role')).toBe('note');
    expect(q('.sound-control button.look-icon')!.getAttribute('aria-describedby')).toBe(sentence.id);
    expect(sentence.id).not.toBe('');
    show({ disclosure: 'Another sentence.' });
    expect(q('.sound-control__pop p')!.textContent).toBe('Another sentence.');
  });

  it('has a volume slider at the level, filled from the bottom to it', () => {
    show({ level: 0.7 });
    const volume = q('.sound-control__volume') as HTMLElement;
    expect(volume.getAttribute('role')).toBe('slider');
    expect(volume.getAttribute('aria-label')).toBe('Volume');
    expect(volume.getAttribute('aria-valuemin')).toBe('0');
    expect(volume.getAttribute('aria-valuemax')).toBe('100');
    expect(volume.getAttribute('aria-valuenow')).toBe('70');
    expect(volume.tabIndex).toBe(0);
    expect((volume.querySelector('i') as HTMLElement).style.height).toBe('70%');
    show({ level: 0.424 });
    expect(q('.sound-control__volume')!.getAttribute('aria-valuenow')).toBe('42');
    expect((q('.sound-control__volume i') as HTMLElement).style.height).toBe('42%');
  });

  it('reads a height on the capsule as a level: the 64 px line sits 16 px inside its 96 px', () => {
    const capsule = { top: 100, height: 96 };
    expect(levelAtHeight(116, capsule)).toBe(1);
    expect(levelAtHeight(180, capsule)).toBe(0);
    expect(levelAtHeight(148, capsule)).toBe(0.5);
    expect(levelAtHeight(132, capsule)).toBe(0.75);
    // Above the line's top or below its foot: full or none, never past either
    expect(levelAtHeight(100, capsule)).toBe(1);
    expect(levelAtHeight(196, capsule)).toBe(0);
  });

  it('sets the level to the height of a click anywhere on the capsule, with no drag', () => {
    const onLevel = vi.fn();
    show({ onLevel });
    const volume = q('.sound-control__volume') as HTMLElement;
    vi.spyOn(volume, 'getBoundingClientRect').mockReturnValue({ top: 100, height: 96, left: 0, width: 36, right: 36, bottom: 196, x: 0, y: 100, toJSON: () => ({}) });
    act(() => {
      volume.dispatchEvent(new MouseEvent('click', { bubbles: true, clientY: 132 }));
    });
    expect(onLevel).toHaveBeenCalledTimes(1);
    expect(onLevel).toHaveBeenCalledWith(0.75);
  });

  it('changes by 10 of 100 with the up and down arrows, never past either end, and leaves other keys alone', () => {
    const onLevel = vi.fn();
    show({ level: 0.7, onLevel });
    const press = (key: string) => {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
      act(() => {
        q('.sound-control__volume')!.dispatchEvent(event);
      });
      return event;
    };
    expect(press('ArrowUp').defaultPrevented).toBe(true);
    expect(onLevel).toHaveBeenLastCalledWith(0.8);
    expect(press('ArrowDown').defaultPrevented).toBe(true);
    expect(onLevel).toHaveBeenLastCalledWith(0.6);
    expect(press('a').defaultPrevented).toBe(false);
    expect(onLevel).toHaveBeenCalledTimes(2);
    show({ level: 0.95, onLevel });
    press('ArrowUp');
    expect(onLevel).toHaveBeenLastCalledWith(1);
    show({ level: 0.05, onLevel });
    press('ArrowDown');
    expect(onLevel).toHaveBeenLastCalledWith(0);
  });
});

describe("the sound control's rules are the approved page's", () => {
  it('opens the group under the circle only while on, on hover or focus, in a quarter of a second, and never moves with reduced motion', () => {
    expect(declarationsOf('.sound-control')).toEqual({ position: 'relative' });
    expect(declarationsOf('.sound-control__pop')).toEqual({
      position: 'absolute',
      right: '0',
      top: '44px',
      display: 'flex',
      gap: '14px',
      'align-items': 'flex-start',
      opacity: '0',
      transform: 'translateY(-4px)',
      'pointer-events': 'none',
      transition: 'opacity 0.25s ease-out, transform 0.25s ease-out',
    });
    expect(declarationsOf('.sound-control.is-on:hover .sound-control__pop, .sound-control.is-on:focus-within .sound-control__pop')).toEqual({
      opacity: '1',
      transform: 'none',
      'pointer-events': 'auto',
    });
    expect(css.replace(/\s+/g, ' ')).toContain('@media (prefers-reduced-motion: reduce) { .sound-control__pop { transition: none; } }');
  });

  it('sets the sentence 252 px wide, right aligned, 12 px at the second ink', () => {
    expect(declarationsOf('.sound-control__pop p')).toEqual({
      width: '252px',
      margin: '6px 0 0',
      'text-align': 'right',
      font: '400 12px / 1.5 var(--look-font-sans)',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-2)',
      'text-wrap': 'pretty',
    });
  });

  it('draws the capsule 36 by 96 with a 64 px line and a 10 px dot at the level', () => {
    expect(declarationsOf('.sound-control__volume')).toEqual({
      flex: 'none',
      width: '36px',
      height: '96px',
      'box-sizing': 'border-box',
      display: 'grid',
      'place-items': 'center',
      border: '1px solid var(--look-line)',
      'border-radius': '18px',
      background: 'rgba(10, 10, 12, 0.55)',
      '-webkit-backdrop-filter': 'blur(14px)',
      'backdrop-filter': 'blur(14px)',
      cursor: 'pointer',
    });
    expect(declarationsOf('.sound-control__volume span')).toEqual({ position: 'relative', display: 'block', width: '2px', height: '64px', 'border-radius': '2px', background: 'var(--look-hair)' });
    expect(declarationsOf('.sound-control__volume i')).toEqual({ position: 'absolute', left: '0', right: '0', bottom: '0', 'border-radius': '2px', background: 'var(--look-ink)' });
    expect(declarationsOf('.sound-control__volume i::after')).toEqual({
      content: "''",
      position: 'absolute',
      left: '50%',
      top: '0',
      width: '10px',
      height: '10px',
      margin: '-5px 0 0 -5px',
      'border-radius': '50%',
      background: 'var(--look-ink)',
    });
    expect(declarationsOf('.sound-control__volume:focus-visible')).toEqual({ outline: 'var(--look-focus-width) solid var(--look-ink)', 'outline-offset': 'var(--look-focus-offset)' });
  });

  it('puts the row at the top right, 8 px between its parts, and draws the speaker at 16 px', () => {
    expect(declarationsOf('.reveal-nav')).toEqual({ position: 'fixed', right: '32px', top: '26px', 'z-index': '35', display: 'flex', gap: '8px', 'align-items': 'flex-start', 'font-family': 'var(--look-font-sans)', color: 'var(--look-ink)' });
    expect(declarationsOf('.sound-control .look-icon')).toEqual({ '--look-ic': '16px' });
  });
});
