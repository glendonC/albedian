// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RestoreCompare } from '../RestoreParts.js';

// restored-disclosure-one (UIUX-cliprestore-READY.md, "When the Restored disclosure shows", ruled by the lead 00:44 on
// 27 Nov): the compare says when its restored picture has its first drawn frame, a still's image loaded or a clip's
// first frame, and that it is gone when the compare goes, so the Restored sentence stands only while a restored
// version is drawn.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const rect = { leftPx: 0, topPx: 120, widthPx: 800, heightPx: 600 };
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

describe("the compare's restored picture, drawn", () => {
  it('is not drawn until a still loads, is drawn after, and is gone when the compare goes', () => {
    const told: boolean[] = [];
    act(() => root.render(createElement(RestoreCompare, { rect, src: '/v.png', originalSrc: '/o.png', onRestoredDrawn: (d: boolean) => told.push(d) })));
    expect(told.at(-1)).toBe(false);
    act(() => void container.querySelector('.restore-compare__pic img')!.dispatchEvent(new Event('load')));
    expect(told.at(-1)).toBe(true);
    act(() => root.unmount());
    expect(told.at(-1)).toBe(false);
    root = createRoot(container);
  });

  it('is drawn at once for a still already decoded before it is listened to', () => {
    vi.spyOn(HTMLImageElement.prototype, 'complete', 'get').mockReturnValue(true);
    vi.spyOn(HTMLImageElement.prototype, 'naturalWidth', 'get').mockReturnValue(1600);
    const told: boolean[] = [];
    act(() => root.render(createElement(RestoreCompare, { rect, src: '/v.png', originalSrc: '/o.png', onRestoredDrawn: (d: boolean) => told.push(d) })));
    expect(told.at(-1)).toBe(true);
  });

  it("is drawn from a clip's first frame, at once when the clip already has it", () => {
    const parent = document.createElement('video');
    const told: boolean[] = [];
    act(() => root.render(createElement(RestoreCompare, { rect, src: '/c.mp4', originalSrc: '/o.mp4', clipParent: parent, onRestoredDrawn: (d: boolean) => told.push(d) })));
    expect(told.at(-1)).toBe(false);
    act(() => void container.querySelector('.restore-compare__pic video')!.dispatchEvent(new Event('loadeddata')));
    expect(told.at(-1)).toBe(true);
    act(() => root.unmount());
    root = createRoot(container);
    vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(2);
    const ready: boolean[] = [];
    act(() => root.render(createElement(RestoreCompare, { rect, src: '/c.mp4', originalSrc: '/o.mp4', clipParent: parent, onRestoredDrawn: (d: boolean) => ready.push(d) })));
    expect(ready.at(-1)).toBe(true);
  });
});
