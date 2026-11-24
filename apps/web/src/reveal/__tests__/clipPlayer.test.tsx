// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { ClipPlayer, clipTime, timeAtX } from '../ClipPlayer.js';

// A clip's player on the reveal (INTERIM: .lead/uiux/UIUX-clip-QUESTION.md, waiting on the owner; Vimeo's player, read
// live on 18 Nov; values from .cache/uiux/clip/screen/cl-pause.html): a play circle apart from one thin timeline, the
// time riding above it.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

/** A clip element as the player sees it: its time, whether it plays, and its events. */
class FakeVideo extends EventTarget {
  currentTime = 0;
  paused = true;
  play = vi.fn(async () => {
    this.paused = false;
    this.dispatchEvent(new Event('play'));
  });
  pause = vi.fn(() => {
    this.paused = true;
    this.dispatchEvent(new Event('pause'));
  });
  at(sec: number) {
    this.currentTime = sec;
    this.dispatchEvent(new Event('timeupdate'));
  }
}

let container: HTMLDivElement;
let root: Root;
let video: FakeVideo;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  video = new FakeVideo();
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});
const q = (selector: string) => container.querySelector(selector);
function show(props: Partial<Parameters<typeof ClipPlayer>[0]> = {}) {
  act(() => {
    root.render(createElement(ClipPlayer, { video: video as unknown as HTMLVideoElement, durationSec: 10.9, onSeek: () => {}, ...props }));
  });
}
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

describe('clipTime and timeAtX', () => {
  it('writes the time as minutes and seconds, with no total', () => {
    expect(clipTime(0)).toBe('0:00');
    expect(clipTime(5.4)).toBe('0:05');
    expect(clipTime(65.9)).toBe('1:05');
    expect(clipTime(600)).toBe('10:00');
  });

  it('reads a place on the line as a time inside the clip, the line running 18 px inside the pill', () => {
    const pill = { left: 100, width: 236 };
    expect(timeAtX(118, pill, 10)).toBe(0);
    expect(timeAtX(318, pill, 10)).toBe(10);
    expect(timeAtX(218, pill, 10)).toBe(5);
    expect(timeAtX(50, pill, 10)).toBe(0);
    expect(timeAtX(400, pill, 10)).toBe(10);
  });
});

describe('ClipPlayer', () => {
  it('shows a play circle and the timeline at the clip\'s time, the chip riding above the line', () => {
    show();
    act(() => video.at(5));
    const button = q('.clip-player button.look-icon') as HTMLButtonElement;
    expect(button.getAttribute('aria-label')).toBe('Play');
    expect(button.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.play);
    const line = q('.clip-player__line') as HTMLElement;
    expect(line.getAttribute('role')).toBe('slider');
    expect(line.getAttribute('aria-label')).toBe('Time in the clip');
    expect(line.getAttribute('aria-valuemin')).toBe('0');
    expect(line.getAttribute('aria-valuemax')).toBe('10.9');
    expect(line.getAttribute('aria-valuenow')).toBe('5');
    expect(line.getAttribute('aria-valuetext')).toBe('0:05');
    expect((q('.clip-player__fill') as HTMLElement).style.width).toBe(`${(5 / 10.9) * 100}%`);
    const chip = q('.clip-player__time') as HTMLElement;
    expect(chip.textContent).toBe('0:05');
    expect(chip.style.left).toBe(`${(5 / 10.9) * 100}%`);
  });

  it('sets the clock where the clip stops on its own at its end, once (the re-read of unit-clip-1)', () => {
    const onSeek = vi.fn();
    show({ onSeek });
    act(() => (q('.clip-player button.look-icon') as HTMLButtonElement).click());
    // A browser at the clip's end: paused, then timeupdate, pause and ended
    act(() => {
      video.currentTime = 10.9;
      video.paused = true;
      video.dispatchEvent(new Event('timeupdate'));
      video.dispatchEvent(new Event('pause'));
      video.dispatchEvent(new Event('ended'));
    });
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenLastCalledWith(10.9);
    expect(q('.clip-player button.look-icon')!.getAttribute('aria-label')).toBe('Play');
  });

  it('plays and pauses from its circle, the icon and name following, and keeps the clock where it paused', () => {
    const onSeek = vi.fn();
    show({ onSeek });
    act(() => (q('.clip-player button.look-icon') as HTMLButtonElement).click());
    expect(video.play).toHaveBeenCalledTimes(1);
    expect(q('.clip-player button.look-icon')!.getAttribute('aria-label')).toBe('Pause');
    expect(q('.clip-player button.look-icon path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.pause);
    act(() => video.at(3.2));
    act(() => (q('.clip-player button.look-icon') as HTMLButtonElement).click());
    expect(video.pause).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenLastCalledWith(3.2);
    expect(q('.clip-player button.look-icon')!.getAttribute('aria-label')).toBe('Play');
  });

  it('goes to a place pressed on the line and drags through the clip while held', () => {
    const onSeek = vi.fn();
    show({ onSeek });
    const line = q('.clip-player__line') as HTMLElement;
    vi.spyOn(line, 'getBoundingClientRect').mockReturnValue({ left: 100, width: 236, top: 0, height: 36, right: 336, bottom: 36, x: 100, y: 0, toJSON: () => ({}) });
    line.setPointerCapture = vi.fn();
    const pointer = (type: string, clientX: number) =>
      act(() => {
        line.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX }) as PointerEvent);
      });
    pointer('pointerdown', 218);
    expect(video.currentTime).toBeCloseTo(5.45, 6);
    pointer('pointermove', 318);
    expect(video.currentTime).toBeCloseTo(10.9, 6);
    // The clock is set where the drag ends, not at every step of it
    expect(onSeek).not.toHaveBeenCalled();
    pointer('pointerup', 318);
    expect(onSeek).toHaveBeenLastCalledWith(10.9);
    // Not held: moving does nothing
    pointer('pointermove', 118);
    expect(video.currentTime).toBeCloseTo(10.9, 6);

    // Secondary mouse button does not start a scrub
    act(() => {
      line.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 118, button: 2 }) as PointerEvent);
    });
    expect(video.currentTime).toBeCloseTo(10.9, 6);

    // pointercancel mid-scrub clears held and settles onSeek at the frame shown (P-SKY-9); unheld pointercancel is a no-op
    onSeek.mockClear();
    pointer('pointerdown', 168);
    expect(video.currentTime).toBeCloseTo(2.725, 6);
    expect(onSeek).not.toHaveBeenCalled();
    pointer('pointercancel', 168);
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenLastCalledWith(video.currentTime);
    pointer('pointermove', 318);
    expect(video.currentTime).toBeCloseTo(2.725, 6);
    pointer('pointercancel', 318);
    expect(onSeek).toHaveBeenCalledTimes(1);
  });

  it('steps one second with the left and right arrows, never past either end, and leaves other keys alone', () => {
    const onSeek = vi.fn();
    show({ onSeek });
    act(() => video.at(0.4));
    const line = q('.clip-player__line') as HTMLElement;
    const press = (key: string) => {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
      act(() => {
        line.dispatchEvent(event);
      });
      return event;
    };
    expect(press('ArrowRight').defaultPrevented).toBe(true);
    expect(video.currentTime).toBeCloseTo(1.4, 6);
    expect(onSeek).toHaveBeenLastCalledWith(1.4);
    press('ArrowLeft');
    press('ArrowLeft');
    expect(video.currentTime).toBe(0);
    act(() => video.at(10.5));
    press('ArrowRight');
    expect(video.currentTime).toBe(10.9);
    expect(press('a').defaultPrevented).toBe(false);
  });

  it("goes to the clip's start with Home and its end with End, setting the clock there (the slider pattern's own keys)", () => {
    const onSeek = vi.fn();
    show({ onSeek });
    act(() => video.at(4.2));
    const line = q('.clip-player__line') as HTMLElement;
    const press = (key: string) => {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
      act(() => {
        line.dispatchEvent(event);
      });
      return event;
    };
    expect(press('End').defaultPrevented).toBe(true);
    expect(video.currentTime).toBe(10.9);
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenLastCalledWith(10.9);
    expect(line.getAttribute('aria-valuenow')).toBe('10.9');
    expect(line.getAttribute('aria-valuetext')).toBe('0:10');
    expect(press('Home').defaultPrevented).toBe(true);
    expect(video.currentTime).toBe(0);
    expect(onSeek).toHaveBeenCalledTimes(2);
    expect(onSeek).toHaveBeenLastCalledWith(0);
    expect(line.getAttribute('aria-valuenow')).toBe('0');
    expect(line.getAttribute('aria-valuetext')).toBe('0:00');
    // Neither key goes anywhere from the button beside the line
    const circle = container.querySelector('button') as HTMLButtonElement;
    const fromCircle = new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true });
    act(() => {
      circle.dispatchEvent(fromCircle);
    });
    expect(fromCircle.defaultPrevented).toBe(false);
    expect(video.currentTime).toBe(0);
    expect(onSeek).toHaveBeenCalledTimes(2);
  });
});

describe("the clip player's rules are the design lane's page", () => {
  it('sits under the frame\'s middle, 675 wide, 96 px from the bottom, the circle 8 px apart from the pill', () => {
    expect(declarationsOf('.clip-player')).toEqual({ position: 'fixed', left: '50%', bottom: '96px', width: '675px', transform: 'translateX(-50%)', 'z-index': '30', display: 'flex', 'align-items': 'center', gap: '8px', 'pointer-events': 'auto' });
    expect(declarationsOf('.clip-player .look-icon')).toEqual({ '--look-ic': '15px', background: 'rgba(10, 10, 12, 0.55)', '-webkit-backdrop-filter': 'blur(14px)', 'backdrop-filter': 'blur(14px)' });
    expect(declarationsOf('.clip-player__line')).toEqual({
      position: 'relative',
      flex: '1',
      height: '36px',
      'box-sizing': 'border-box',
      border: '1px solid var(--look-line)',
      'border-radius': '9999px',
      padding: '0 18px',
      display: 'flex',
      'align-items': 'center',
      cursor: 'pointer',
      background: 'rgba(10, 10, 12, 0.55)',
      '-webkit-backdrop-filter': 'blur(14px)',
      'backdrop-filter': 'blur(14px)',
      'touch-action': 'none',
    });
    expect(declarationsOf('.clip-player__track')).toEqual({ position: 'relative', display: 'block', width: '100%', height: '2px', 'border-radius': '2px', background: 'var(--look-hair)' });
    expect(declarationsOf('.clip-player__fill')).toEqual({ position: 'absolute', left: '0', top: '0', bottom: '0', 'border-radius': '2px', background: 'var(--look-ink)' });
    expect(declarationsOf('.clip-player__time')).toEqual({
      position: 'absolute',
      bottom: 'calc(100% + 8px)',
      transform: 'translateX(-50%)',
      padding: '3px 7px',
      'border-radius': '6px',
      background: 'var(--look-ink)',
      color: 'var(--look-night)',
      font: '500 11px / 1 var(--look-font-sans)',
      'font-variant-numeric': 'tabular-nums',
      'white-space': 'nowrap',
    });
    expect(declarationsOf('.clip-player__line:focus-visible')).toEqual({ outline: 'var(--look-focus-width) solid var(--look-ink)', 'outline-offset': 'var(--look-focus-offset)' });
  });

  it('places D16 at phone width at top 676 with 24 px gutters, 44 px height, 18 px icon, and scroll translation', () => {
    show({ phone: true });
    expect(q('.clip-player')!.className).toBe('clip-player is-phone');
    expect(q('.clip-player button.look-icon')!.className).toContain('look-size-phone');
    expect(declarationsOf('.clip-player.is-phone')).toEqual({
      left: '24px',
      right: '24px',
      top: '676px',
      bottom: 'auto',
      width: 'auto',
      transform: 'none',
    });
    expect(declarationsOf('.clip-player.is-phone .look-icon')).toEqual({ '--look-ic': '18px' });
    expect(declarationsOf('.clip-player.is-phone .clip-player__line')).toEqual({ height: '44px' });
    expect(declarationsOf(':root[data-phone-scroll] .clip-player.is-phone')).toEqual({
      translate: '0 calc(-1 * var(--phone-scroll, 0px))',
    });
  });
});
