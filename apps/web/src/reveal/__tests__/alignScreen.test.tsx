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
    catalogRef: mag === undefined ? (/(mercury|venus|mars|jupiter|saturn|uranus|neptune)$/.test(id) ? { ...(template.catalogRef ?? {}), objectType: 'planet', apparentMagnitude: undefined } : undefined) : { ...(template.catalogRef ?? {}), apparentMagnitude: mag, objectType: 'star' },
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
    // About the frame's centre: the sky turns and scales about the point between the fingers (MOTION-READY-web.md
    // section 6 A), so both fingers move out and that point stays where it was
    const cx = parseFloat(layer.style.left) + parseFloat(layer.style.width) / 2;
    const cy = parseFloat(layer.style.top) + parseFloat(layer.style.height) / 2;
    touch('pointerdown', 1, cx - 50, cy);
    touch('pointerdown', 2, cx + 50, cy);
    // Fingers apart, twice the spread: the sky grows twice as large
    touch('pointermove', 2, cx + 100, cy);
    touch('pointermove', 1, cx - 100, cy);
    expect(spread() / before).toBeGreaterThan(1.8);
    touch('pointerup', 2, cx + 100, cy);
    touch('pointerup', 1, cx - 100, cy);
    // Both lifted, one finger only moves the sky: its size stays, but for the tangent plane's bend (under 2 percent)
    const after = spread();
    touch('pointerdown', 3, 150, 300);
    touch('pointermove', 3, 190, 330);
    touch('pointerup', 3, 190, 330);
    expect(Math.abs(spread() / after - 1)).toBeLessThan(0.02);
  });

  it('holds the star between two fingers between them as they spread (MOTION-READY-web.md section 6 A)', () => {
    show(unmatched(), { phone: true });
    act(() => button('Line it up yourself')!.click());
    const layer = q('[data-testid="align-layer"]') as HTMLElement;
    const left = parseFloat(layer.style.left);
    const top = parseFloat(layer.style.top);
    const rings = () => [...layer.querySelectorAll('circle')].map((el) => ({ x: left + Number(el.getAttribute('cx')), y: top + Number(el.getAttribute('cy')) }));
    const touch = (type: string, id: number, x: number, y: number) =>
      act(() => {
        const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y });
        Object.defineProperty(event, 'pointerId', { value: id });
        layer.dispatchEvent(event);
      });
    // Fingers 50 px either side of a ring; the second moves 100 px further out in 10 moves: the point between them
    // goes 50 px right, and the ring with it
    const ring = rings()[0]!;
    touch('pointerdown', 1, ring.x - 50, ring.y);
    touch('pointerdown', 2, ring.x + 50, ring.y);
    for (let k = 1; k <= 10; k += 1) touch('pointermove', 2, ring.x + 50 + 10 * k, ring.y);
    touch('pointerup', 2, ring.x + 150, ring.y);
    touch('pointerup', 1, ring.x - 50, ring.y);
    const nearest = Math.min(...rings().map((r) => Math.hypot(r.x - (ring.x + 50), r.y - ring.y)));
    expect(nearest).toBeLessThan(0.5);
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

  it('offers Solve from here before Cancel, 10 px apart, which sends the pose as it stands (D24 step 2)', async () => {
    const { personAlignmentOf, startPose } = await import('../alignPose.js');
    const sent: unknown[] = [];
    const session = unmatched();
    show(session, { onSolveFromHere: (alignment: unknown) => sent.push(alignment) });
    act(() => button('Line it up yourself')!.click());
    const acts = [...container.querySelectorAll('.reveal-status.is-unmatched .align-acts button')];
    expect(acts.map((b) => b.textContent)).toEqual(['Solve from here', 'Cancel']);
    act(() => (acts[0] as HTMLButtonElement).click());
    expect(sent).toEqual([personAlignmentOf(startPose(session)!.pose)]);
  });

  it('offers Solve from here at phone width, centred at the foot (alp-align, top 772)', () => {
    const sent: unknown[] = [];
    show(unmatched(), { phone: true, onSolveFromHere: (alignment: unknown) => sent.push(alignment) });
    act(() => button('Line it up yourself')!.click());
    const solve = q('.align-phone__solve button') as HTMLButtonElement;
    expect(solve.textContent).toBe('Solve from here');
    // C30 (UIUX-webuxpieces-READY.md item 6): the approved phone button, 44 px high, as alp-refused's Line it up again
    expect(solve.className).toContain('look-size-phone');
    act(() => solve.click());
    expect(sent).toHaveLength(1);
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

  it('keeps the context menu off the aligning layer only, so Ctrl and drag turns on a Mac, where Ctrl and click is the secondary click', () => {
    // macOS turns Ctrl and click into the secondary click: Chrome and Safari send pointerdown, then contextmenu, whose
    // menu would take the pointer and end the turn (the lead's yes, 13:48 on 21 Nov)
    start();
    const layer = q('[data-testid="align-layer"]') as HTMLElement;
    const menu = (target: Element) => {
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, ctrlKey: true });
      act(() => {
        target.dispatchEvent(event);
      });
      return event.defaultPrevented;
    };
    expect(menu(layer)).toBe(true);
    // Elsewhere on the screen the menu stays the browser's
    expect(menu(container.querySelector('[data-testid="align-readout"]')!)).toBe(false);
  });

  it('keeps the ring taken under the pointer through a drag, wherever it was taken (MOTION-READY-web.md section 6 A)', () => {
    start();
    const layer = q('[data-testid="align-layer"]') as HTMLElement;
    const left = parseFloat(layer.style.left);
    const top = parseFloat(layer.style.top);
    const rings = () => [...layer.querySelectorAll('circle')].map((el) => ({ x: left + Number(el.getAttribute('cx')), y: top + Number(el.getAttribute('cy')) }));
    const pointer = (type: string, x: number, y: number) =>
      act(() => {
        layer.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }) as PointerEvent);
      });
    // The ring farthest from the frame's centre (720, 450), taken and moved 150 px across and 100 px down in 15 moves
    const taken = rings().reduce((a, b) => (Math.hypot(b.x - 720, b.y - 450) > Math.hypot(a.x - 720, a.y - 450) ? b : a));
    pointer('pointerdown', taken.x, taken.y);
    for (let k = 1; k <= 15; k += 1) pointer('pointermove', taken.x + 10 * k, taken.y + (100 * k) / 15);
    pointer('pointerup', taken.x + 150, taken.y + 100);
    const nearest = Math.min(...rings().map((r) => Math.hypot(r.x - (taken.x + 150), r.y - (taken.y + 100))));
    expect(nearest).toBeLessThan(0.5);
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

// D24 steps 4 and 5 (UIUX-align-READY.md): after a solve sent from the person's alignment, a refusal keeps the
// alignment, drawn as predicted at its pose, with its two sentences and Line it up again, Show predicted sky and
// Restore in a column; a match is the calibrated reveal with one more status line, "Found near your alignment".
describe('after Solve from here', () => {
  const KEPT: AlignPose = { azDeg: 120, altDeg: 40, rollDeg: 10, fovXDeg: 45, aspect: 4 / 3 };
  const keptOf = (session: ObservationSession) => ({ sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest' as const, name: 'Fomalhaut' } });

  it('refused: says no stars matched near it and that it is kept, drawn as predicted, with the three actions in a column', () => {
    const session = unmatched();
    show(session, { alignedFrom: keptOf(session), onRestore: () => {}, restoration: { phase: 'idle' } as Restoration });
    const status = q('.reveal-status.is-unmatched')!;
    // The Predicted tag above the refusal, in a column with no entrance (UIUX-tagplaces-DECIDED-BY-LEAD.md place 1)
    expect(status.firstElementChild!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(status.firstElementChild!.nextElementSibling!.className).toBe('align-refused');
    expect(status.classList.contains('has-predicted')).toBe(true);
    expect([...status.querySelectorAll('.align-refused p')].map((p) => p.textContent)).toEqual(['No stars matched near your alignment.', 'Your alignment is kept, drawn as predicted.']);
    expect([...status.querySelectorAll('.align-refused__acts button')].map((b) => b.textContent)).toEqual(['Line it up again', 'Show predicted sky', 'Restore']);
    // The rings stay amber and dashed at the kept pose
    expect(q('[data-testid="early-marks"]')!.className).toBe('align-layer is-still');
    expect(q('[data-testid="early-marks"] circle')).not.toBeNull();
  });

  it("refused outright for no time or place (C15): the route's sentence, the marks gone, Restore only, no tag", () => {
    const session = unmatched();
    const sentence = 'Your photo does not say when it was taken, so its sky cannot be lined up by hand.';
    show(session, { alignedFrom: { ...keptOf(session), refusal: sentence }, onRestore: () => {}, restoration: { phase: 'idle' } as Restoration });
    const status = q('.reveal-status.is-unmatched')!;
    expect([...status.querySelectorAll('.align-refused p')].map((p) => p.textContent)).toEqual([sentence]);
    expect([...status.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Restore']);
    expect(q('[data-testid="early-marks"]')).toBeNull();
    expect(status.querySelector('.look-tag')).toBeNull();
  });

  it('Line it up again reopens aligning at the kept pose', () => {
    const session = unmatched();
    show(session, { alignedFrom: keptOf(session) });
    act(() => button('Line it up again')!.click());
    expect(q('[data-testid="align-readout"]')!.innerHTML.split('<br>')[0]).toBe('120° · 40° up · turned 10° · 45° across');
  });

  it('matched: the calibrated reveal with one more status line, "Found near your alignment"', () => {
    const calibrated = poseSession(sample('plate_calibrated'));
    show(calibrated, { alignedFrom: keptOf(calibrated) });
    const lines = [...container.querySelectorAll('[data-testid="reveal-status"] .reveal-status__line')].map((p) => p.textContent);
    expect(lines.at(-1)).toBe('Found near your alignment');
    expect(q('[data-testid="early-marks"]')).toBeNull();
  });

  it('says nothing of an alignment for another upload', () => {
    const session = unmatched();
    show(session, { alignedFrom: { ...keptOf(session), sha256: 'f'.repeat(64) } });
    expect(q('.align-refused')).toBeNull();
  });
});

describe("the refused state's rules", () => {
  it('sets the second sentence at 14 px and the actions in a column 10 px apart', () => {
    const found: Record<string, Record<string, string>> = {};
    postcss.parse(css).walkRules((rule) => {
      if (!rule.selector.startsWith('.align-refused')) return;
      found[rule.selector] = Object.fromEntries((rule.nodes ?? []).map((d) => [(d as postcss.Declaration).prop, (d as postcss.Declaration).value]));
    });
    expect(found['.align-refused__kept']).toMatchObject({ 'font-size': '14px' });
    expect(found['.align-refused__acts']).toMatchObject({ display: 'flex', 'flex-direction': 'column', 'align-items': 'flex-start', gap: '10px' });
  });
});
