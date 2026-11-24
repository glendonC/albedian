// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { AlignLayer, StillAlignMarks } from '../AlignLayer.js';
import { placedAtPose, startPose, alignTargets } from '../alignPose.js';
import { phoneSlotRect } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

// align-zoom-1 (UIUX-alignzoom-READY.md, DECIDED-BY-LEAD 12:41 on 24 Nov, beside D24): while aligning, a double-click
// (laptop) or a double-tap (phone) on the photo toggles between the whole photo and its own pixels (1:1) centred on that
// point, inside the photo's own frame (the phone's slot); the rings and names ride the zoom so a drag works the same;
// the same gesture or Esc returns; the pixels are only drawn larger; one step each way, no movement (MOTION, 13:00).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({
    ...template,
    id,
    title,
    skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az },
    catalogRef: { ...(template.catalogRef ?? {}), apparentMagnitude: mag, objectType: 'star' },
  }) as CelestialFinding;
const SKY = [
  target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16),
  target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38),
  target('finding-predicted-star-hip-112158', 'Matar', 30, 160, 2.9),
];
/** The no-match fixture as a 3024 by 4032 phone photo with a 26 mm equivalent lens, and the targets above. */
function unmatched(width = 3024, height = 4032): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width, height, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: { ...session.observationSolution!, findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), ...SKY] } as ObservationSolution,
  } as ObservationSession;
}

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
const button = (label: string) => [...container.querySelectorAll('button')].find((b) => b.textContent === label) as HTMLButtonElement | undefined;
function start(session: ObservationSession, phone = false) {
  act(() => {
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: phone ? 390 : 1440,
        viewportHeightPx: phone ? 844 : 900,
        phone,
        onShowPredictedSky: () => {},
        onSolveFromHere: () => {},
      }),
    );
  });
  act(() => button('Line it up yourself')!.click());
}
const layer = () => q('[data-testid="align-layer"]') as HTMLElement;
const boxOf = (el: HTMLElement) => ({ leftPx: parseFloat(el.style.left), topPx: parseFloat(el.style.top), widthPx: parseFloat(el.style.width), heightPx: parseFloat(el.style.height) });
/** Every ring on screen, in the viewport's pixels. */
const rings = () => {
  const box = boxOf(layer());
  return [...layer().querySelectorAll('circle')].map((c) => ({ x: box.leftPx + Number(c.getAttribute('cx')), y: box.topPx + Number(c.getAttribute('cy')) }));
};
const readout = () => q('[data-testid="align-readout"]')?.innerHTML.split('<br>')[0];
const dblclick = (x: number, y: number) =>
  act(() => {
    layer().dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
  });
const pointer = (type: string, x: number, y: number, pointerType = 'mouse', pointerId = 1) =>
  act(() => {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y });
    Object.defineProperty(event, 'pointerId', { value: pointerId });
    Object.defineProperty(event, 'pointerType', { value: pointerType });
    layer().dispatchEvent(event);
  });
const tap = (x: number, y: number) => {
  pointer('pointerdown', x, y, 'touch');
  pointer('pointerup', x, y, 'touch');
};

describe('align-zoom-1 on the laptop', () => {
  it('a double-click shows the photo at its own pixels centred on the point, inside its frame, the rings riding it, the pose unchanged', () => {
    start(unmatched());
    const whole = boxOf(layer());
    const posed = readout();
    const before = rings();
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    const at = { x: whole.leftPx + 0.3 * whole.widthPx, y: whole.topPx + 0.45 * whole.heightPx };
    dblclick(at.x, at.y);
    const zoom = q('[data-testid="align-zoom"]') as HTMLElement;
    expect(zoom).not.toBeNull();
    // The upload itself, only drawn larger: its own address, upright by its own tag, 1:1, no filter
    const photo = zoom.querySelector('img')!;
    expect(photo.getAttribute('src')).toBe(unmatched().sourceAsset!.uri);
    expect(photo.style.imageOrientation).toBe('from-image');
    expect(photo.style.filter).toBe('');
    expect(parseFloat(photo.style.width)).toBeCloseTo(3024, 6);
    expect(parseFloat(photo.style.height)).toBeCloseTo(4032, 6);
    // Centred on the point: the photo's point under the press sits at the frame's centre
    const zoomed = boxOf(layer());
    expect(zoomed.widthPx).toBeCloseTo(3024, 6);
    const u = (at.x - whole.leftPx) / whole.widthPx;
    const v = (at.y - whole.topPx) / whole.heightPx;
    expect(zoomed.leftPx + u * zoomed.widthPx).toBeCloseTo(whole.leftPx + whole.widthPx / 2, 6);
    expect(zoomed.topPx + v * zoomed.heightPx).toBeCloseTo(whole.topPx + whole.heightPx / 2, 6);
    expect(parseFloat(photo.style.left)).toBeCloseTo(zoomed.leftPx, 6);
    expect(parseFloat(photo.style.top)).toBeCloseTo(zoomed.topPx, 6);
    // Cut to the photo's own frame, the layer and its rings inside the cut
    expect(zoom.style.clipPath).toBe(`inset(${whole.topPx}px ${1440 - whole.leftPx - whole.widthPx}px ${900 - whole.topPx - whole.heightPx}px ${whole.leftPx}px)`);
    expect(zoom.contains(layer())).toBe(true);
    // The rings ride the same transform: each one's place in the photo is unchanged
    const after = rings();
    expect(after).toHaveLength(before.length);
    after.forEach((ring, i) => {
      expect((ring.x - zoomed.leftPx) / zoomed.widthPx).toBeCloseTo((before[i]!.x - whole.leftPx) / whole.widthPx, 6);
      expect((ring.y - zoomed.topPx) / zoomed.heightPx).toBeCloseTo((before[i]!.y - whole.topPx) / whole.heightPx, 6);
    });
    // The sky the person set is untouched, and the Predicted tag stands
    expect(readout()).toBe(posed);
    expect(q('.reveal-status .look-tag[data-evidence-class="predicted"]')).not.toBeNull();
    // The same gesture returns to the whole photo
    dblclick(at.x, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    expect(boxOf(layer())).toEqual(whole);
  });

  it("counts a mouse's double-click once: its two presses are no double-tap", () => {
    start(unmatched());
    const whole = boxOf(layer());
    const at = { x: whole.leftPx + whole.widthPx / 2, y: whole.topPx + whole.heightPx / 2 };
    // As a browser sends a double-click: two presses and releases, then dblclick
    pointer('pointerdown', at.x, at.y, 'mouse');
    pointer('pointerup', at.x, at.y, 'mouse');
    pointer('pointerdown', at.x, at.y, 'mouse');
    pointer('pointerup', at.x, at.y, 'mouse');
    dblclick(at.x, at.y);
    expect(q('[data-testid="align-zoom"]')).not.toBeNull();
  });

  it("takes a mouse's double-click by the system's own double-click time, not the finger's 300 ms", () => {
    start(unmatched());
    const whole = boxOf(layer());
    const at = { x: whole.leftPx + whole.widthPx / 2, y: whole.topPx + whole.heightPx / 2 };
    let now = 50_000;
    const clock = vi.spyOn(performance, 'now').mockImplementation(() => now);
    try {
      pointer('pointerdown', at.x, at.y, 'mouse');
      pointer('pointerup', at.x, at.y, 'mouse');
      now += 450;
      pointer('pointerdown', at.x, at.y, 'mouse');
      pointer('pointerup', at.x, at.y, 'mouse');
      dblclick(at.x, at.y);
      expect(q('[data-testid="align-zoom"]')).not.toBeNull();
    } finally {
      clock.mockRestore();
    }
  });

  it('keeps the frame covered near an edge: the 1:1 photo never leaves a gap inside the frame', () => {
    start(unmatched());
    const whole = boxOf(layer());
    dblclick(whole.leftPx + 2, whole.topPx + whole.heightPx - 2);
    expect(q('[data-testid="align-zoom"]')).not.toBeNull();
    const zoomed = boxOf(layer());
    expect(zoomed.widthPx).toBeCloseTo(3024, 6);
    expect(zoomed.leftPx).toBeCloseTo(whole.leftPx, 6);
    expect(zoomed.topPx + zoomed.heightPx).toBeCloseTo(whole.topPx + whole.heightPx, 6);
  });

  it('Esc returns to the whole photo first and leaves aligning only on a second Esc', () => {
    start(unmatched());
    const whole = boxOf(layer());
    dblclick(whole.leftPx + whole.widthPx / 2, whole.topPx + whole.heightPx / 2);
    expect(q('[data-testid="align-zoom"]')).not.toBeNull();
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    expect(button('Solve from here')).toBeDefined();
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(q('[data-testid="align-layer"]')).toBeNull();
  });

  it('at 1:1 a drag keeps the star under the pointer and the wheel still scales the sky, not the photo', () => {
    start(unmatched());
    const whole = boxOf(layer());
    const ring = rings()[0]!;
    dblclick(ring.x, ring.y);
    const zoomed = boxOf(layer());
    const held = rings()[0]!;
    pointer('pointerdown', held.x, held.y);
    pointer('pointermove', held.x + 30, held.y + 20);
    pointer('pointerup', held.x + 30, held.y + 20);
    const moved = rings()[0]!;
    expect(Math.hypot(moved.x - (held.x + 30), moved.y - (held.y + 20))).toBeLessThan(0.5);
    // A pinch on the trackpad scales the sky: the readout's field changes, the photo stays at its own pixels
    const field = readout();
    act(() => {
      layer().dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 200, ctrlKey: true }));
    });
    expect(readout()).not.toBe(field);
    expect(boxOf(layer())).toEqual(zoomed);
    expect(whole.widthPx).toBeLessThan(zoomed.widthPx);
  });

  it('ends with aligning: Cancel leaves no zoom behind, and aligning again starts on the whole photo', () => {
    start(unmatched());
    const whole = boxOf(layer());
    dblclick(whole.leftPx + 100, whole.topPx + 100);
    expect(q('[data-testid="align-zoom"]')).not.toBeNull();
    act(() => button('Cancel')!.click());
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    act(() => button('Line it up yourself')!.click());
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    expect(boxOf(layer())).toEqual(whole);
  });

  it("draws a clip's own frame at the clock's time, the upload itself, muted", () => {
    const photo = unmatched();
    const clip = {
      ...photo,
      currentSourceTimeSec: 2.5,
      sourceAsset: { ...photo.sourceAsset!, mediaKind: 'video', durationMs: 6000, uri: '/api/media/clip/file' },
    } as ObservationSession;
    start(clip);
    const whole = boxOf(layer());
    dblclick(whole.leftPx + whole.widthPx / 2, whole.topPx + whole.heightPx / 2);
    const video = q('[data-testid="align-zoom"] video') as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video.getAttribute('src')).toBe('/api/media/clip/file');
    expect(video.muted).toBe(true);
    expect(q('[data-testid="align-zoom"] img')).toBeNull();
    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(video.currentTime).toBe(2.5);
  });

  it('does nothing on a photo already drawn at or above its own pixels', () => {
    start(unmatched(400, 533));
    const whole = boxOf(layer());
    expect(whole.widthPx).toBeGreaterThanOrEqual(400);
    dblclick(whole.leftPx + 50, whole.topPx + 50);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
  });

  it("says it under D24's three lines, in their form", () => {
    start(unmatched());
    expect([...container.querySelectorAll('.align-help > span')].map((s) => s.textContent)).toEqual([
      'Drag to move',
      'Ctrl and drag to turn',
      'Scroll or pinch to scale',
      'Double-click to see the photo full size',
    ]);
  });
});

describe('align-zoom-1 on the phone', () => {
  it('a double-tap toggles 1:1 inside the slot; one tap, or two far apart or slow, does not; a drag is no tap', () => {
    start(unmatched(), true);
    const whole = boxOf(layer());
    const slot = phoneSlotRect(whole, 390);
    const at = { x: slot.leftPx + slot.widthPx / 2, y: slot.topPx + slot.heightPx / 2 };
    tap(at.x, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    tap(at.x, at.y);
    // Chrome follows a finger's double-tap with a dblclick of its own (seen live on rp-b-06): it is the same gesture, once
    dblclick(at.x, at.y);
    const zoom = q('[data-testid="align-zoom"]') as HTMLElement;
    expect(zoom).not.toBeNull();
    expect(zoom.style.clipPath).toBe(`inset(${slot.topPx}px ${390 - slot.leftPx - slot.widthPx}px ${844 - slot.topPx - slot.heightPx}px ${slot.leftPx}px)`);
    expect(boxOf(layer()).widthPx).toBeCloseTo(3024, 6);
    tap(at.x, at.y);
    tap(at.x, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    // Two taps 80 px apart are two taps, not a double-tap
    tap(at.x, at.y);
    tap(at.x + 80, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    // A drag between two taps is no tap
    pointer('pointerdown', at.x, at.y, 'touch');
    pointer('pointermove', at.x + 40, at.y, 'touch');
    pointer('pointerup', at.x + 40, at.y, 'touch');
    tap(at.x + 40, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    // A drag out and back to where it came down is still a drag, not a tap
    pointer('pointerdown', at.x, at.y, 'touch');
    pointer('pointermove', at.x + 40, at.y, 'touch');
    pointer('pointermove', at.x, at.y, 'touch');
    pointer('pointerup', at.x, at.y, 'touch');
    tap(at.x, at.y);
    expect(q('[data-testid="align-zoom"]')).toBeNull();
    // Two taps 400 ms apart are two taps
    let now = 10_000;
    const clock = vi.spyOn(performance, 'now').mockImplementation(() => now);
    try {
      tap(at.x, at.y);
      now += 400;
      tap(at.x, at.y);
      expect(q('[data-testid="align-zoom"]')).toBeNull();
      now += 100;
      tap(at.x, at.y);
      expect(q('[data-testid="align-zoom"]')).not.toBeNull();
    } finally {
      clock.mockRestore();
    }
  });

  it("does not hand the double-tap to the browser's own zoom", () => {
    const rule: Record<string, string> = {};
    postcss.parse(css).walkRules((r) => {
      if (r.selector === '.align-layer') r.walkDecls((d) => void (rule[d.prop] = d.value));
    });
    expect(rule['touch-action']).toBe('none');
    start(unmatched(), true);
    const at = boxOf(layer());
    pointer('pointerdown', at.leftPx + 50, at.topPx + 50, 'touch');
    pointer('pointerup', at.leftPx + 50, at.topPx + 50, 'touch');
    const second = new MouseEvent('pointerdown', { bubbles: true, cancelable: true, clientX: at.leftPx + 50, clientY: at.topPx + 50 });
    Object.defineProperty(second, 'pointerId', { value: 1 });
    Object.defineProperty(second, 'pointerType', { value: 'touch' });
    act(() => {
      layer().dispatchEvent(second);
    });
    expect(second.defaultPrevented).toBe(true);
  });

  it('says it under the one line of hints, in its form', () => {
    start(unmatched(), true);
    expect(q('.align-phone__help')!.innerHTML).toBe('Drag to move. Turn or pinch with two fingers.<br>Double-tap to see the photo full size.');
  });
});

describe('align-zoom-1 draws in one step', () => {
  it('gives the zoom and its photo no transition and no animation (MOTION, 13:00: no glide, no fade)', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(css).walkRules((r) => {
      if (!r.selector.includes('align-zoom')) return;
      rules[r.selector] = {};
      r.walkDecls((d) => void (rules[r.selector]![d.prop] = d.value));
    });
    expect(rules['.align-zoom']).toEqual({ position: 'absolute', inset: '0', 'pointer-events': 'none' });
    for (const decls of Object.values(rules)) {
      expect(Object.keys(decls).some((p) => p.startsWith('transition') || p.startsWith('animation'))).toBe(false);
    }
  });
});

describe('align-zoom-1 at a Solve from here hand-over', () => {
  // The phone's wait draws the rings since phone-solve-wait-1 (DECIDED-BY-LEAD 24 Nov 12:58), in the frame handed over
  it("hands over the marks where the whole photo puts them, so the phone's wait rings stand over the whole photo", () => {
    const session = unmatched();
    const targets = alignTargets(session.observationSolution);
    const pose = startPose(session)!.pose;
    const whole = { leftPx: 0, topPx: 120, widthPx: 390, heightPx: 520 };
    const zoomed = { leftPx: -900, topPx: -1000, widthPx: 3024, heightPx: 4032 };
    act(() => root.render(createElement(AlignLayer, { rect: zoomed, wholeRect: whole, pose, targets, onPose: () => {}, phone: true })));
    act(() => root.render(createElement(StillAlignMarks, { rect: whole, placed: placedAtPose(targets, pose), handOver: true, phone: true })));
    const still = q('[data-testid="early-marks"]') as HTMLElement;
    expect(boxOf(still)).toEqual(whole);
    expect(still.querySelectorAll('circle').length).toBeGreaterThan(0);
  });
});
