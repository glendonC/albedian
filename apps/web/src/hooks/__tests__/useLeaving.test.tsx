// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MOTION_TOKENS, resolveMotionTokens } from '@albedian/design-system';
import { blockLeaveMs, linesLeaveMs, reducedMotion, useLeaving } from '../useLeaving.js';

// The motion record (MOTION-READY-web.md, "Any card or block"): a block's lines leave last first, 20 ms apart, in
// words out, and its frame leaves with the last line in frame out; so a block of n lines is gone after
// 20 ms x (n - 1) + 300 ms, and under reduced motion after the 270 ms dissolve.
describe('blockLeaveMs', () => {
  it('is the stagger for every line after the first, then the frame out', () => {
    expect(blockLeaveMs(8, MOTION_TOKENS)).toBe(20 * 7 + 300);
    expect(blockLeaveMs(1, MOTION_TOKENS)).toBe(300);
    expect(blockLeaveMs(0, MOTION_TOKENS)).toBe(300);
  });
  it('is the one dissolve under reduced motion, however many lines', () => {
    expect(blockLeaveMs(8, resolveMotionTokens(true))).toBe(MOTION_TOKENS.dissolveMs);
    expect(blockLeaveMs(1, resolveMotionTokens(true))).toBe(MOTION_TOKENS.dissolveMs);
  });
});

// Lines with no frame of their own (a legend): only the lines, last first, so gone after 20 ms x (n - 1) + 100 ms.
describe('linesLeaveMs', () => {
  it('is the stagger for every line after the first, then the words out', () => {
    expect(linesLeaveMs(4, MOTION_TOKENS)).toBe(20 * 3 + 100);
    expect(linesLeaveMs(1, MOTION_TOKENS)).toBe(100);
    expect(linesLeaveMs(4, resolveMotionTokens(true))).toBe(MOTION_TOKENS.dissolveMs);
  });
});

describe('reducedMotion', () => {
  afterEach(() => vi.unstubAllGlobals());
  it("reads the person's setting, and is false where the browser cannot say", () => {
    vi.stubGlobal('matchMedia', undefined);
    expect(reducedMotion()).toBe(false);
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' }));
    expect(reducedMotion()).toBe(true);
  });
});

describe('useLeaving', () => {
  let container: HTMLDivElement;
  let root: Root;
  const seen: Array<{ mounted: boolean; leaving: boolean }> = [];
  const Probe = ({ open, ms }: { open: boolean; ms: number }) => {
    seen.push(useLeaving(open, ms));
    return null;
  };
  const render = (open: boolean, ms = 440) => act(() => root.render(createElement(Probe, { open, ms })));
  const now = () => seen.at(-1)!;
  beforeEach(() => {
    vi.useFakeTimers();
    seen.length = 0;
    container = document.createElement('div');
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.useRealTimers();
  });

  it('mounts nothing while closed, and mounts at once when opened', () => {
    render(false);
    expect(now()).toEqual({ mounted: false, leaving: false });
    render(true);
    expect(now()).toEqual({ mounted: true, leaving: false });
  });

  it('keeps it mounted and leaving for the leave time after closing, then unmounts it', () => {
    render(true);
    render(false);
    expect(now()).toEqual({ mounted: true, leaving: true });
    act(() => vi.advanceTimersByTime(439));
    expect(now()).toEqual({ mounted: true, leaving: true });
    act(() => vi.advanceTimersByTime(1));
    expect(now()).toEqual({ mounted: false, leaving: false });
  });

  it('takes it back without unmounting when opened again while leaving', () => {
    render(true);
    render(false);
    act(() => vi.advanceTimersByTime(200));
    render(true);
    expect(now()).toEqual({ mounted: true, leaving: false });
    act(() => vi.advanceTimersByTime(1000));
    expect(now()).toEqual({ mounted: true, leaving: false });
  });
});
