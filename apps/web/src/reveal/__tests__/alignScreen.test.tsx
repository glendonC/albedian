// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { alignMarks, ALIGN_RING_PX } from '../AlignLayer.js';
import { alignTargets, fovFrom35mm, type AlignPose } from '../alignPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import type { Restoration } from '../restoration.js';

// Line it up yourself on the laptop (D24, UIUX-align-READY.md; the al-entry and al-align screens): the entry under Show
// predicted sky, the aligning column and layer, Cancel and Esc. "Solve from here" waits on SOLVER's contract (lead 07:51).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const target = (id: string, title: string, alt: number, az: number, mag?: number) =>
  ({
    ...template,
    id,
    title,
    skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az },
    catalogRef: mag === undefined ? undefined : { ...(template.catalogRef ?? {}), apparentMagnitude: mag },
  }) as CelestialFinding;
const SKY = [
  target('finding-predicted-star-hip-113368', 'Fomalhaut (Predicted Ephemeris)', 20, 150, 1.16),
  target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38),
  target('finding-predicted-saturn', 'Saturn (Predicted Ephemeris)', 25, 160),
];
/** The no-match fixture as a 3024 by 4032 phone photo with a 26 mm equivalent lens, and the targets above. */
function unmatched(findings: CelestialFinding[] = SKY): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: { ...session.observationSolution!, findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), ...findings] } as ObservationSolution,
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
function show(session: ObservationSession, extra: Record<string, unknown> = {}) {
  act(() => {
    root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900, onShowPredictedSky: () => {}, ...extra }));
  });
}

describe('the entry on the no-match reveal', () => {
  it('puts "Line it up yourself" 16 px under Show predicted sky, and Restore 10 px under it', () => {
    show(unmatched(), { restoration: { phase: 'idle' } as Restoration, onRestore: () => {} });
    const act = q('.reveal-status.is-unmatched .reveal-status__act')!;
    expect([...act.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Line it up yourself', 'Restore']);
    expect(act.previousElementSibling!.textContent).toContain('Show predicted sky');
  });

  it('is not offered with no target above the horizon, or while Original pixels is pressed', () => {
    show(unmatched([target('finding-predicted-star-hip-1', 'Low', -4, 150, 1)]));
    expect(button('Line it up yourself')).toBeUndefined();
    show(unmatched(), { originalPixels: true });
    expect(button('Line it up yourself')).toBeUndefined();
  });
});

describe('at phone width (alp-align)', () => {
  it('offers the entry under Show predicted sky, and aligns with the sentence at 654 and the one line of hints at 700', () => {
    const told: boolean[] = [];
    show(unmatched(), { phone: true, onAligningChange: (active: boolean) => told.push(active) });
    const go = [...container.querySelectorAll('.reveal-phone-go')];
    expect(go.map((g) => g.textContent)).toEqual(['Show predicted sky', 'Line it up yourself']);
    expect(go[1]!.className).toBe('reveal-phone-go reveal-phone-go--more');
    act(() => button('Line it up yourself')!.click());
    expect(q('.align-phone__lead')!.textContent).toBe('Drag the stars onto the dots you see.');
    expect(q('.align-phone__help')!.textContent).toBe('Drag to move. Turn or pinch with two fingers.');
    expect(q('[data-testid="align-layer"]')!.className).toBe('align-layer is-phone');
    expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
    expect(told).toEqual([false, true]);
  });

  it('ends at the close circle: the viewport changes the token, and nothing is kept', () => {
    const told: boolean[] = [];
    const props = { phone: true, onAligningChange: (active: boolean) => told.push(active) };
    show(unmatched(), { ...props, cancelAligningToken: 0 });
    act(() => button('Line it up yourself')!.click());
    show(unmatched(), { ...props, cancelAligningToken: 1 });
    expect(q('[data-testid="align-layer"]')).toBeNull();
    expect(button('Line it up yourself')).toBeDefined();
    expect(told.at(-1)).toBe(false);
  });

  it('turns and scales with two fingers: the angle between them turns, their spread scales', () => {
    show(unmatched(), { phone: true });
    act(() => button('Line it up yourself')!.click());
    const layer = q('[data-testid="align-layer"]') as HTMLElement;
    const touch = (type: string, id: number, x: number, y: number) =>
      act(() => {
        const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y });
        Object.defineProperty(event, 'pointerId', { value: id });
        layer.dispatchEvent(event);
      });
    const fov = () => Number(/(\d+)° across/.exec(container.textContent ?? '')?.[1] ?? NaN);
    // The readout is not on the phone; read the pose through the rings' spread instead
    const spread = () => {
      const c = [...layer.querySelectorAll('circle')].map((el) => [Number(el.getAttribute('cx')), Number(el.getAttribute('cy'))] as const);
      return Math.hypot(c[0]![0] - c[1]![0], c[0]![1] - c[1]![1]);
    };
    expect(Number.isNaN(fov())).toBe(true);
    const before = spread();
    touch('pointerdown', 1, 100, 300);
    touch('pointerdown', 2, 200, 300);
    // Fingers apart, twice the spread: the sky grows twice as large
    touch('pointermove', 2, 300, 300);
    expect(spread() / before).toBeGreaterThan(1.8);
    touch('pointerup', 2, 300, 300);
    touch('pointerup', 1, 100, 300);
    // Both lifted, one finger only moves the sky: its size stays, but for the tangent plane's bend (under 2 percent)
    const after = spread();
    touch('pointerdown', 3, 150, 300);
    touch('pointermove', 3, 190, 330);
    touch('pointerup', 3, 190, 330);
    expect(Math.abs(spread() / after - 1)).toBeLessThan(0.02);
  });
});

describe('aligning', () => {
  function start() {
    show(unmatched());
    act(() => button('Line it up yourself')!.click());
  }

  it('replaces the status with the sentence, the three hints, Cancel and the readout, and draws the targets as amber rings', () => {
    start();
    const status = q('.reveal-status.is-unmatched')!;
    expect(status.querySelector('.reveal-status__lead')!.textContent).toBe('Drag the stars onto the dots you see.');
    expect([...status.querySelectorAll('.align-help span')].map((s) => s.textContent)).toEqual(['Drag to move', 'Ctrl and drag to turn', 'Scroll or pinch to scale']);
    expect([...status.querySelectorAll('.align-acts button')].map((b) => b.textContent)).toEqual(['Cancel']);
    // No recorded heading reaches the web yet: it starts facing the brightest star, and says so
    expect(q('[data-testid="align-readout"]')!.innerHTML).toBe('150° · 34° up · turned 0° · 53° across<br>Started from the brightest star, Fomalhaut.');
    expect(status.querySelector('.reveal-status__warns')).toBeNull();
    expect(status.querySelector('.reveal-status__act')).toBeNull();
    expect(q('[data-testid="align-layer"] svg')!.querySelectorAll('circle')).toHaveLength(3);
    expect([...container.querySelectorAll('.align-layer__name')].map((n) => n.textContent).sort()).toEqual(['Enif', 'Fomalhaut', 'Saturn']);
    // The counts stay
    expect(q('[data-testid="reveal-legend"]')).not.toBeNull();
  });

  it('moves with a drag, turns with Ctrl and drag, and scales with the wheel, the readout following', () => {
    start();
    const layer = q('[data-testid="align-layer"]') as HTMLElement;
    const readout = () => q('[data-testid="align-readout"]')!.innerHTML.split('<br>')[0];
    const pointer = (type: string, x: number, y: number, ctrlKey = false) =>
      act(() => {
        layer.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, ctrlKey }) as PointerEvent);
      });
    pointer('pointerdown', 700, 400);
    pointer('pointermove', 700, 480);
    pointer('pointerup', 700, 480);
    expect(readout()).toMatch(/^150° · 4\d° up · turned 0° · 53° across$/);
    // A quarter turn swept about the frame's centre (720, 450)
    pointer('pointerdown', 820, 450, true);
    pointer('pointermove', 720, 550, true);
    pointer('pointerup', 720, 550, true);
    expect(readout()).toMatch(/turned 90°/);
    act(() => {
      layer.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 200 }));
    });
    expect(readout()).toMatch(/· 7\d° across$/);
  });

  it('keeps nothing after Cancel or Esc: the entry comes back and a new start is the file start', () => {
    start();
    act(() => button('Cancel')!.click());
    expect(q('[data-testid="align-layer"]')).toBeNull();
    expect(button('Line it up yourself')).toBeDefined();
    act(() => button('Line it up yourself')!.click());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(q('[data-testid="align-layer"]')).toBeNull();
    expect(q('.reveal-status__lead')!.textContent).not.toBe('Drag the stars onto the dots you see.');
  });
});

describe("the rings and names, the design's own rule", () => {
  const rect = { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 };
  const fov = fovFrom35mm(26, 4 / 3);
  const pose: AlignPose = { azDeg: 150, altDeg: fov.y / 2, rollDeg: 0, fovXDeg: fov.x, aspect: 4 / 3 };

  it("places a name 6 px past the ring on the right, else on the left, and none where both run off the frame or into a name", () => {
    const targets = alignTargets(unmatched([target('finding-predicted-star-hip-1', 'Fomalhaut', fov.y / 2, 150, 1), target('finding-predicted-star-hip-2', 'Near the right edge', fov.y / 2, 150 + 25, 2)]).observationSolution);
    const marks = alignMarks(targets, pose, rect, 13);
    const centre = marks.find((m) => m.id === 'finding-predicted-star-hip-1')!;
    expect(centre).toMatchObject({ name: 'Fomalhaut', nameOnLeft: false });
    expect(centre.xPx).toBeCloseTo(382.5 + 337.5, 6);
    expect(centre.yPx).toBeCloseTo(450, 6);
    const edge = marks.find((m) => m.id === 'finding-predicted-star-hip-2')!;
    expect(edge.nameOnLeft).toBe(true);
    expect(ALIGN_RING_PX).toBe(6.5);
  });

  it('draws nothing outside the frame', () => {
    const targets = alignTargets(unmatched([target('finding-predicted-star-hip-1', 'Behind', 20, 330, 1)]).observationSolution);
    expect(alignMarks(targets, pose, rect, 13)).toEqual([]);
  });

  it('gives a second name that would cross the first no place on that side', () => {
    const targets = alignTargets(unmatched([target('finding-predicted-star-hip-1', 'First', fov.y / 2, 150, 1), target('finding-predicted-star-hip-2', 'Second', fov.y / 2 + 0.3, 150.2, 2)]).observationSolution);
    const [first, second] = alignMarks(targets, pose, rect, 13);
    expect(first!.nameOnLeft).toBe(false);
    expect(second!.nameOnLeft).toBe(true);
  });
});

describe('the aligning rules are the al-align screen\'s', () => {
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
  it('sets the column, the readout, the rings and the names as drawn', () => {
    expect(declarationsOf('.reveal-status__act')).toEqual({ display: 'flex', 'flex-direction': 'column', 'align-items': 'flex-start', gap: '10px', 'margin-top': '16px', 'pointer-events': 'auto' });
    expect(declarationsOf('.reveal-status__lead.align-lead')).toEqual({ margin: '0 0 10px' });
    expect(declarationsOf('.align-help')).toEqual({ display: 'grid', gap: '4px', margin: '0 0 18px', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.align-readout')).toEqual({ margin: '14px 0 0', 'padding-top': '12px', 'border-top': '1px solid var(--look-hair)', 'font-size': '12px', 'line-height': '1.45', color: 'var(--look-ink-3)', 'font-variant-numeric': 'tabular-nums' });
    expect(declarationsOf('.align-layer__rings')).toMatchObject({ fill: 'none', stroke: 'var(--look-ev-predicted)', 'stroke-width': '1.1', 'stroke-dasharray': '2.6 2.4' });
    expect(declarationsOf('.align-layer__name')).toMatchObject({ color: 'var(--look-ev-predicted)', font: '400 13px / 1 var(--look-font-sans)', '-webkit-text-stroke': '3px rgba(0, 0, 0, 0.75)' });
  });
});
