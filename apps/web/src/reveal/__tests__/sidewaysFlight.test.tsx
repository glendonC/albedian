// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { SIDEWAYS_QUERY } from '../../components/phoneSheet.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// phone-sideways-four (UIUX-phone-sideways-READY.md items 6a to 6e, drawn and measured on WEB-UX's question, DECIDED-BY-LEAD
// at the line stamped 00:11 on 27 Nov): the flight at rest on a phone held on its side. 6a the tag line under the top bar,
// whole inside the window from the flight's first frame to its last (on main the Simulated tag stood above the window);
// 6b Back to Moment centred at the foot; 6c the two pills at the trailing foot, stacked where they do not fit beside Back
// to Moment's 16 px gap; 6d the two numbers in the leading foot block, stacked where they do not fit; 6e the star resting
// in the middle, clear of all four (measured live, the star's place unchanged).

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const declsOf = (selector: string, inSideways: boolean) => {
  const found: Record<string, string> = {};
  postcss.parse(css).walk((node) => {
    if (node.type !== 'rule' || !node.selectors.map((s) => s.replace(/\s+/g, ' ')).includes(selector)) return;
    const media = node.parent?.type === 'atrule' ? (node.parent as postcss.AtRule).params : null;
    if (inSideways ? media !== SIDEWAYS_QUERY : node.parent?.type !== 'root') return;
    node.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
  });
  return found;
};

// Version two (AUDIT-D's FAIL on the tag rule): the tag line holds the Simulated tag, so it stands at full from the
// readout's first frame and stays while Back to Moment flies back, as the upright tag row does
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
describe('phone-sideways-four version two: the tag line keeps the tag rule through the motion sheet', () => {
  it('exempts the tag line from the readout words-in, as the tag row', () => {
    const still: string[] = [];
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.some((d) => d.type === 'decl' && d.prop === 'animation' && d.value === 'none')) still.push(...rule.selectors.map((x) => x.replace(/\s+/g, ' ')));
    });
    expect(still).toContain('.flight-readout > .flight-readout__tagrow');
    expect(still).toContain('.flight-readout > .flight-readout__line');
  });

  it('keeps the tag line, as the tag row and the first sentence, out of the readout words-out at Back to Moment', () => {
    const leaving: string[] = [];
    postcss.parse(motionCss).walkRules((rule) => {
      for (const sel of rule.selectors) if (sel.includes('.flight-readout.is-phone.is-returning')) leaving.push(sel.replace(/\s+/g, ' '));
    });
    expect(leaving).toEqual(['.flight-readout.is-phone.is-returning > :not(.flight-readout__tagrow, .flight-readout__note, .flight-readout__line)']);
  });
});

describe('phone-sideways-four: where each block of the flight at rest stands held on its side', () => {
  it('6a: stands the tag line at 64 at the leading margin, one line: the tag, the sentence 10 px after it, the caret 12 px after', () => {
    expect(declsOf('.flight-readout.is-phone .flight-readout__line', true)).toEqual({
      position: 'fixed',
      top: '64px',
      left: 'max(24px, env(safe-area-inset-left))',
      display: 'flex',
      'align-items': 'center',
      'white-space': 'nowrap',
    });
    expect(declsOf('.flight-readout.is-phone .flight-readout__line .flight-readout__note', true)).toEqual({ margin: '0 12px 0 10px', 'max-width': 'none' });
  });

  it("6a: opens the other source lines in place under the line, as the details sheet words them, scrolling inside", () => {
    expect(declsOf('.flight-readout.is-phone .flight-readout__details', true)).toEqual({
      position: 'fixed',
      top: '114px',
      left: 'max(24px, env(safe-area-inset-left))',
      'max-width': 'calc(100% - max(24px, env(safe-area-inset-left)) - max(20px, env(safe-area-inset-right)))',
      display: 'grid',
      gap: '9px',
      margin: '0',
      padding: '0',
      'list-style': 'none',
      'overflow-y': 'auto',
      'overscroll-behavior': 'contain',
      'pointer-events': 'auto',
      'font-size': '13px',
      'line-height': '1.45',
      color: 'var(--look-ink-2)',
    });
  });

  it('6d: stands the numbers block by its foot at the leading side, side by side 12 apart, else stacked 8 apart, 8 above the hint', () => {
    expect(declsOf('.flight-readout.is-phone', true)).toEqual({ left: 'max(24px, env(safe-area-inset-left))', right: 'auto', top: 'auto', bottom: 'max(12px, env(safe-area-inset-bottom))' });
    expect(declsOf('.flight-readout.is-phone .flight-readout__stats', true)).toEqual({ 'grid-template-columns': 'max-content max-content', 'column-gap': '12px' });
    expect(declsOf('.flight-readout.is-phone[data-stacked] .flight-readout__stats', true)).toEqual({ 'grid-template-columns': 'max-content', 'row-gap': '8px' });
    expect(declsOf('.flight-readout.is-phone .flight-readout__hint', true)).toEqual({ 'margin-top': '8px' });
  });

  it("6c: stands the pills at the trailing foot, stacked with Your photo's view on the foot line and the craft pill 8 above", () => {
    expect(declsOf('.flight-pills.is-phone', true)).toEqual({ left: 'auto', right: 'max(20px, env(safe-area-inset-right))', top: 'auto', bottom: 'max(12px, env(safe-area-inset-bottom))', 'align-items': 'flex-end' });
    expect(declsOf('.flight-pills-host[data-stacked] .flight-pills.is-phone', true)).toEqual({ 'flex-direction': 'column', gap: '8px' });
    // Upright, the row as it stood inline in ConePieces.tsx, unchanged
    expect(declsOf('.flight-pills.is-phone', false)).toEqual({ position: 'absolute', left: '24px', right: '24px', top: 'calc(100% - 132px)', display: 'flex', gap: '10px', 'pointer-events': 'auto' });
  });
});

const W = 874;
const H = 292;
let container: HTMLDivElement;
let root: Root;
/** The ask bar holding Back to Moment: SpatialObservatoryViewport draws it beside the reveal, so a stand-in holds its place. */
let askBar: HTMLDivElement;
const saved = {
  rect: HTMLElement.prototype.getBoundingClientRect,
  offsetWidth: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth'),
};
/** Boxes as Chrome lays the blocks out at 874 by 292 (measured live on main), keyed by what each element is. */
let boxes: Record<string, [number, number, number, number]>;
let widths: Record<string, number>;
const keyOf = (el: Element): string | null =>
  el.getAttribute('data-testid') === 'ask-bar'
    ? 'ask'
    : el.getAttribute('data-testid') === 'flight-readout'
      ? 'readout'
      : el.classList.contains('flight-readout__line')
        ? 'line'
        : el.getAttribute('data-testid') === 'flight-pills'
          ? 'pills'
          : null;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  askBar = document.createElement('div');
  askBar.setAttribute('data-testid', 'ask-bar');
  document.body.appendChild(askBar);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
  boxes = { ask: [338, 226, 536, 272], readout: [24, 168, 214, 280], line: [24, 64, 640, 100], pills: [702, 164, 854, 280] };
  widths = { stat0: 89, stat1: 88, pill0: 143, pill1: 166 };
  HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
    const b = boxes[keyOf(this) ?? ''];
    return b ? ({ left: b[0], top: b[1], right: b[2], bottom: b[3], width: b[2] - b[0], height: b[3] - b[1], x: b[0], y: b[1], toJSON() {} } as DOMRect) : saved.rect.call(this);
  };
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get(this: HTMLElement) {
      if (this.classList.contains('flight-readout__stat')) return widths[`stat${[...this.parentElement!.children].indexOf(this)}`] ?? 0;
      if (this.parentElement?.getAttribute('data-testid') === 'flight-pills') return widths[`pill${[...this.parentElement.children].indexOf(this)}`] ?? 0;
      return 0;
    },
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  askBar.remove();
  vi.restoreAllMocks();
  HTMLElement.prototype.getBoundingClientRect = saved.rect;
  if (saved.offsetWidth) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', saved.offsetWidth);
});

const q = (s: string) => container.querySelector(s);
function there(vw = W, vh = H): ObservationSession {
  const session = poseSession(sample('plate_calibrated') as ObservationSolution);
  const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: { ...session, selectedFindingId: rigel.id },
        onSelectFinding: () => {},
        onHome: () => {},
        phone: true,
        flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 },
        viewportWidthPx: vw,
        viewportHeightPx: vh,
      })
    )
  );
  return session;
}

describe('phone-sideways-four: the flight at rest held on its side, in the reveal', () => {
  it('6a: draws the Simulated tag, the first sentence and the caret as one line, and no upright tag row', () => {
    there();
    const line = q('.flight-readout__line')!;
    expect([...line.children].map((c) => c.className.split(' ')[0] || c.tagName)).toEqual(['look-tag', 'flight-readout__note', 'look-icon']);
    expect(line.querySelector('.flight-readout__note')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
    expect(line.querySelector('[data-testid="details-circle"]')).not.toBeNull();
    expect(q('.flight-readout__tagrow')).toBeNull();
    expect(q('[data-testid="flight-readout"] > .flight-readout__note')).toBeNull();
  });

  it('6a: the caret opens the other lines in place under the line and closes them, with no sheet, the numbers still shown', () => {
    there();
    const caret = q('[data-testid="details-circle"]') as HTMLButtonElement;
    act(() => caret.click());
    const list = q('[data-testid="flight-details"]')!;
    expect(list.tagName).toBe('UL');
    expect(list.querySelector('li')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
    expect(document.querySelector('[data-testid="details-sheet"]')).toBeNull();
    expect((q('[data-testid="flight-readout"]') as HTMLElement).style.visibility).toBe('');
    // Down to 8 px above the highest foot block (the pills at 164): 164 - 8 - (100 + 14)
    expect((list as HTMLElement).style.maxHeight).toBe('42px');
    expect(caret.getAttribute('aria-expanded')).toBe('true');
    act(() => caret.click());
    expect(q('[data-testid="flight-details"]')).toBeNull();
  });

  it("6c, 6d: stacks the pills that do not fit beside Back to Moment's gap, keeps the numbers that do side by side", () => {
    there();
    // Numbers: 24 + 89 + 12 + 88 = 213 <= 338 - 16; pills: 854 - (143 + 10 + 166) = 535 < 536 + 16
    expect(q('[data-testid="flight-readout"]')!.hasAttribute('data-stacked')).toBe(false);
    expect(q('.flight-pills-host')!.hasAttribute('data-stacked')).toBe(true);
    // Stacked as a column, the row's last pill stands on the foot line: Your photo's view, the craft pill above it
    expect(q('[data-testid="flight-pills"]')!.lastElementChild!.textContent).toContain("Your photo's view");
  });

  it('6c, 6d: stacks the numbers where they reach into the gap, and stands the pills side by side where they fit', () => {
    widths = { stat0: 160, stat1: 150, pill0: 100, pill1: 120 };
    there();
    // Numbers: 24 + 160 + 12 + 150 = 346 > 322; pills: 854 - 230 = 624 >= 552
    expect(q('[data-testid="flight-readout"]')!.hasAttribute('data-stacked')).toBe(true);
    expect(q('.flight-pills-host')!.hasAttribute('data-stacked')).toBe(false);
  });

  it("fits each pair with Back to Moment's 16 px gap exactly kept, and stacks it a pixel short of that", () => {
    // Numbers end at 24 + 143 + 12 + 143 = 322 = 338 - 16; pills start at 854 - (146 + 10 + 146) = 552 = 536 + 16
    widths = { stat0: 143, stat1: 143, pill0: 146, pill1: 146 };
    there();
    expect(q('[data-testid="flight-readout"]')!.hasAttribute('data-stacked')).toBe(false);
    expect(q('.flight-pills-host')!.hasAttribute('data-stacked')).toBe(false);
    act(() => root.unmount());
    root = createRoot(container);
    widths = { stat0: 143, stat1: 144, pill0: 146, pill1: 147 };
    there();
    expect(q('[data-testid="flight-readout"]')!.hasAttribute('data-stacked')).toBe(true);
    expect(q('.flight-pills-host')!.hasAttribute('data-stacked')).toBe(true);
  });

  it("keeps the steering line held sideways, in the rest hint's place at ink 66 percent, never on the tag line (version two)", () => {
    const session = poseSession(sample('plate_calibrated') as ObservationSolution);
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session: { ...session, selectedFindingId: rigel.id },
          onSelectFinding: () => {},
          onHome: () => {},
          phone: true,
          phoneSteering: true,
          flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 },
          viewportWidthPx: W,
          viewportHeightPx: H,
        })
      )
    );
    const steer = q('[data-testid="steer-line"]') as HTMLElement;
    expect(steer.textContent).toBe('Drag: turn');
    expect(steer.className).toBe('flight-readout__hint');
    expect(steer.style.color).toBe('var(--look-ink-2)');
    expect(steer.parentElement!.getAttribute('data-testid')).toBe('flight-readout');
    expect(q('.flight-readout__line [data-testid="steer-line"]')).toBeNull();
    expect(q('[data-testid="drag-hint"]')).toBeNull();
  });

  it('keeps the upright flight as it was: the tag row, the sentence under it, the sheet on the caret, nothing stacked', () => {
    for (const [vw, vh] of [[390, 844], [402, 650]]) {
      there(vw, vh);
      expect(q('.flight-readout__line'), `${vw}x${vh}`).toBeNull();
      expect(q('.flight-readout__tagrow'), `${vw}x${vh}`).not.toBeNull();
      expect(q('[data-testid="flight-readout"] > .flight-readout__note'), `${vw}x${vh}`).not.toBeNull();
      expect(q('[data-testid="flight-readout"]')!.hasAttribute('data-stacked'), `${vw}x${vh}`).toBe(false);
      expect(q('.flight-pills-host')!.hasAttribute('data-stacked'), `${vw}x${vh}`).toBe(false);
      act(() => (q('[data-testid="details-circle"]') as HTMLButtonElement).click());
      expect(document.querySelector('[data-testid="details-sheet"]'), `${vw}x${vh}`).not.toBeNull();
      expect(q('[data-testid="flight-details"]'), `${vw}x${vh}`).toBeNull();
      act(() => root.unmount());
      root = createRoot(container);
    }
  });
});
