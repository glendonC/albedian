// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample, POSE } from '../../components/__tests__/poseSessions.js';
import { ALIGN_RING_PX, placeMarks } from '../AlignLayer.js';
import type { AlignPose } from '../alignPose.js';
import type { EarlyPose } from '../earlyPose.js';
import { KEEP_CLEAR_BLOCKS, REVEAL_KEEP_CLEAR, useKeepClear } from '../keepClear.js';
import { lockOnPlan, rememberWorkingClear } from '../lockOn.js';
import { revealMarks, type RevealMark } from '../revealMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { WorkingScreen } from '../WorkingScreen.js';
import type { Arrival } from '../arrival.js';

// The rings and names drawn on the photo keep clear of the status column, the Sound circle and the foot's buttons by
// D33's rule (UIUX-predsky-READY.md lines 55 to 58, ruled 13:10 on 19 Nov), as asked in WEB-UX-keptnames-QUESTION.md
// (FIELD walk-0620: the kept alignment's names over the refused state's sentences and buttons).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FRAME = { leftPx: 0, topPx: 0, widthPx: 1000, heightPx: 800 };
// The status column as the laptop sets it: left 36, top 70, 300 wide
const COLUMN = { x0: 36, x1: 336, y0: 70, y1: 470 };

describe('placeMarks with places to keep clear of', () => {
  it('draws no ring and no name for a star under the column; one beside it keeps its ring, its name only where it clears', () => {
    const marks = placeMarks(
      [
        { id: 'under', name: 'Under', u: 0.2, v: 0.3 },
        { id: 'beside', name: 'Beside', u: 0.35, v: 0.3 },
        { id: 'away', name: 'Away', u: 0.8, v: 0.8 },
      ],
      FRAME,
      13,
      [COLUMN]
    );
    expect(marks.map((m) => m.id)).toEqual(['beside', 'away']);
    // Beside the column at x 350: its name goes right, since on the left it would run into the column
    expect(marks[0]).toMatchObject({ name: 'Beside', nameOnLeft: false });
    expect(marks[1]).toMatchObject({ name: 'Away', nameOnLeft: false });
  });

  it('leaves out a ring that only touches the column, as D33 does', () => {
    const x = COLUMN.x1 + ALIGN_RING_PX - 0.5;
    const marks = placeMarks([{ id: 'edge', name: '', u: x / FRAME.widthPx, v: 0.3 }], FRAME, 13, [COLUMN]);
    expect(marks).toEqual([]);
  });

  it('puts a name on the left, or none, when on the right it would run into a place kept clear', () => {
    const sound = { x0: 520, x1: 560, y0: 220, y1: 260 };
    const marks = placeMarks(
      [
        { id: 'left', name: 'Lefty', u: 0.5, v: 0.3 },
        { id: 'none', name: 'Boxed', u: 0.41, v: 0.4 },
      ],
      FRAME,
      13,
      [sound, { x0: 330, x1: 400, y0: 310, y1: 330 }, { x0: 418, x1: 480, y0: 310, y1: 330 }]
    );
    expect(marks.find((m) => m.id === 'left')).toMatchObject({ name: 'Lefty', nameOnLeft: true });
    expect(marks.find((m) => m.id === 'none')).toMatchObject({ name: null });
  });

  it('keeps every ring while aligning, but places no name over a place kept clear', () => {
    const marks = placeMarks([{ id: 'under', name: 'Under', u: 0.2, v: 0.3 }], FRAME, 13, [COLUMN], true);
    expect(marks).toEqual([{ id: 'under', xPx: 200, yPx: 240, name: null, nameOnLeft: false }]);
  });

  it('draws as before with nothing to keep clear of', () => {
    const placed = [{ id: 'under', name: 'Under', u: 0.2, v: 0.3 }];
    expect(placeMarks(placed, FRAME, 13)).toEqual([{ id: 'under', xPx: 200, yPx: 240, name: 'Under', nameOnLeft: false }]);
  });
});

// The refused state after Solve from here (D24 step 4) on the reveal: a kept ring under the status column is not drawn
const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...(template.catalogRef ?? {}), apparentMagnitude: mag, objectType: 'star' } }) as CelestialFinding;
const SKY = [
  target('finding-predicted-star-hip-113368', 'Fomalhaut (Predicted Ephemeris)', 20, 150, 1.16),
  target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38),
  target('finding-predicted-star-hip-2', 'Two', 35, 110, 2.0),
];
function unmatched(): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: { ...session.observationSolution!, findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), ...SKY] } as ObservationSolution,
  } as ObservationSession;
}
const KEPT: AlignPose = { azDeg: 130, altDeg: 30, rollDeg: 0, fovXDeg: 45, aspect: 3 / 4 };

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
function show(session: ObservationSession) {
  act(() => {
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        onShowPredictedSky: () => {},
        alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest' as const, name: 'Fomalhaut' } },
      })
    );
  });
}
const rings = () => [...container.querySelectorAll('[data-testid="early-marks"] circle')].map((c) => ({ x: Number(c.getAttribute('cx')), y: Number(c.getAttribute('cy')) }));
const frameOffset = () => {
  const layer = container.querySelector('[data-testid="early-marks"]') as HTMLElement;
  return { x: parseFloat(layer.style.left), y: parseFloat(layer.style.top) };
};

describe('the refused state after Solve from here keeps its rings clear of the status column', () => {
  it('does not draw a kept ring while its star sits under the column, and draws it again when the column is clear of it', () => {
    const session = unmatched();
    show(session);
    const all = rings();
    expect(all.length).toBeGreaterThanOrEqual(2);
    // Lay the column over the first ring only (the ring's place on screen is its place in the layer plus the frame's)
    const off = frameOffset();
    const first = { x: all[0]!.x + off.x, y: all[0]!.y + off.y };
    const column = { left: first.x - 20, top: first.y - 20, right: first.x + 20, bottom: first.y + 20 };
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.matches('[data-testid="reveal-status"]') ? column : { left: 0, top: 0, right: 0, bottom: 0 };
      return { ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) } as DOMRect;
    });
    show(session);
    expect(rings()).toEqual(all.slice(1));
    spy.mockRestore();
    act(() => window.dispatchEvent(new Event('resize')));
    expect(rings()).toEqual(all);
  });
});

// The fifth place (WEB-UX-keptnames-addendum-NOTE.md): while a job runs, the early marks keep clear of the working
// column (its lead, source line, Cancel and rows) and the Sound circle
describe('the working screen keeps its early marks clear of the working column', () => {
  const arrival = { fileName: 'night.jpg', byteSize: 10, stored: true, widthPx: 3024, heightPx: 4032, job: { id: 'job_0123456789abcdef', state: 'running', stages: [] }, failure: null } as Arrival;
  const photoRect = { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 };
  const marks = [
    { id: 'finding-predicted-star-vega', name: 'Vega', u: 0.1, v: 0.2 },
    { id: 'finding-predicted-star-deneb', name: 'Deneb', u: 0.7, v: 0.6 },
  ];
  const work = () =>
    act(() => root.render(createElement(WorkingScreen, { arrival, photoRect, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, aligned: { marks } })));
  it('does not draw a ring while its star sits under the column', () => {
    work();
    expect(container.querySelectorAll('[data-testid="early-marks"] circle')).toHaveLength(2);
    // Vega at 382.5 + 0.1 * 675 = 450, 0.2 * 900 = 180 on screen; the column laid over it alone
    const column = { left: 400, top: 120, right: 500, bottom: 240 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.matches('[data-testid="work-early"]') ? column : { left: 0, top: 0, right: 0, bottom: 0 };
      return { ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) } as DOMRect;
    });
    work();
    const left = [...container.querySelectorAll('[data-testid="early-marks"] circle')].map((c) => Number(c.getAttribute('cx')) + photoRect.leftPx);
    expect(left).toEqual([382.5 + 0.7 * 675]);
  });
});

const rectOf = (r: { left: number; top: number; right: number; bottom: number }) =>
  ({ ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) }) as DOMRect;
const NONE = { left: 0, top: 0, right: 0, bottom: 0 };

// Place 3 (UIUX-keepclear-READY.md): the estimate reveal's predicted marks keep clear of the status column
describe('the estimate reveal keeps its marks clear of the status column', () => {
  const marksOn = () =>
    [...container.querySelectorAll('[data-testid^="reveal-mark-"]')].map((el) => ({ id: el.getAttribute('data-testid')!.slice('reveal-mark-'.length), x: parseFloat((el as HTMLElement).style.left), y: parseFloat((el as HTMLElement).style.top) }));
  const estimate = () =>
    act(() => root.render(createElement(RevealPhoto, { session: poseSession(POSE), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900 })));
  it('does not draw a mark while it sits under the column, and draws it again once clear', () => {
    estimate();
    expect(container.querySelector('[data-testid="reveal-status"]')!.getAttribute('data-moment')).toBe('photo');
    const all = marksOn();
    expect(all.length).toBeGreaterThanOrEqual(2);
    const first = all[0]!;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.matches('[data-testid="reveal-status"]') ? { left: first.x - 30, top: first.y - 30, right: first.x + 30, bottom: first.y + 30 } : NONE);
    });
    estimate();
    // A star mark keeps 14 px round its place (revealMarks.ts MARK_ROOM_PX): every mark whose room touches the column
    // goes, every other stays
    const clearOfColumn = (m: { x: number; y: number }) => Math.abs(m.x - first.x) >= 30 + 14 || Math.abs(m.y - first.y) >= 30 + 14;
    expect(marksOn().map((m) => m.id)).toEqual(all.filter(clearOfColumn).map((m) => m.id));
    expect(marksOn().length).toBeLessThan(all.length);
    vi.restoreAllMocks();
    act(() => window.dispatchEvent(new Event('resize')));
    expect(marksOn().map((m) => m.id)).toEqual(all.map((m) => m.id));
  });
});

// Place 1 on the phone: the refused state's block and the close circle (the viewport's, stood in for here)
describe("the phone's refused state keeps its kept rings clear of the close circle", () => {
  it('does not draw a kept ring under the close circle', () => {
    const close = document.createElement('div');
    close.className = 'reveal-phone-top';
    close.innerHTML = '<button type="button" aria-label="Close"></button>';
    document.body.appendChild(close);
    const session = unmatched();
    const phone = () =>
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session,
            phone: true,
            onSelectFinding: () => {},
            onHome: () => {},
            viewportWidthPx: 390,
            viewportHeightPx: 844,
            onShowPredictedSky: () => {},
            alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest' as const, name: 'Fomalhaut' } },
          })
        )
      );
    phone();
    expect(container.querySelector('.reveal-status.is-phone.is-refused')).not.toBeNull();
    const all = rings();
    expect(all.length).toBeGreaterThanOrEqual(2);
    const off = frameOffset();
    const first = { x: all[0]!.x + off.x, y: all[0]!.y + off.y };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.matches('button[aria-label="Close"]') ? { left: first.x - 22, top: first.y - 22, right: first.x + 22, bottom: first.y + 22 } : NONE);
    });
    phone();
    expect(rings()).toEqual(all.slice(1));
    close.remove();
  });
});

// Item 6: the lock-on travels only from the early marks the working screen drew
describe('the lock-on and an early mark the working screen did not draw', () => {
  const FRAME_L = { leftPx: 0, topPx: 45, widthPx: 1440, heightPx: 810 };
  const EARLY: EarlyPose = {
    source: 'moon',
    marks: [
      { id: 'finding-predicted-star-betelgeuse', name: 'Betelgeuse', u: 0.25, v: 0.5, key: 'HIP 27989' },
      { id: 'finding-predicted-star-rigel', name: 'Rigel', u: 0.5, v: 0.75, key: 'HIP 24436' },
    ],
  };
  const matched = (id: string, key: string, xPx: number, yPx: number): RevealMark =>
    ({ finding: { id, evidenceClass: 'catalog_matched', title: id, catalogRef: { catalogSourceId: key } } as unknown as CelestialFinding, kind: 'catalog', xPx, yPx, name: null, nameOnLeft: false, picked: false, uncertaintyPx: null, deepSky: null });
  const MARKS = [matched('finding-star-betelgeuse', 'HIP 27989', 400, 500), matched('finding-star-rigel', 'HIP 24436', 700, 600)];
  afterEach(() => rememberWorkingClear(EARLY.marks, []));

  it('neither travels nor leaves; its matched mark comes in at its solve place as the reveal marks do', () => {
    // Betelgeuse's early place is (360, 450); the working column lay over it
    rememberWorkingClear(EARLY.marks, [{ x0: 300, x1: 420, y0: 400, y1: 500 }]);
    const plan = lockOnPlan(EARLY, FRAME_L, MARKS);
    expect(plan.travelling.map((t) => t.id)).toEqual(['finding-predicted-star-rigel']);
    expect(plan.leaving).toEqual([]);
    expect([...plan.lockedIds]).toEqual(['finding-star-rigel']);
  });

  it('applies only to the early marks the working screen drew, not to another pose', () => {
    rememberWorkingClear([{ id: 'other', name: 'Other', u: 0.1, v: 0.1 }], [{ x0: 300, x1: 420, y0: 400, y1: 500 }]);
    expect(lockOnPlan(EARLY, FRAME_L, MARKS).travelling).toHaveLength(2);
  });

  it('is told by the working screen what its early marks kept clear of', () => {
    const arrival = { fileName: 'night.jpg', byteSize: 10, stored: true, widthPx: 3024, heightPx: 4032, job: { id: 'job_0123456789abcdef', state: 'running', stages: [] }, failure: null } as Arrival;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.matches('[data-testid="work-early"]') ? { left: 300, top: 400, right: 420, bottom: 500 } : NONE);
    });
    act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: FRAME_L, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, early: EARLY })));
    expect(container.querySelectorAll('[data-testid="early-marks"] circle')).toHaveLength(1);
    expect(lockOnPlan(EARLY, FRAME_L, MARKS).travelling.map((t) => t.id)).toEqual(['finding-predicted-star-rigel']);
  });
});

describe('revealMarks with places to keep clear of (place 3)', () => {
  const session = poseSession(POSE);
  const free = revealMarks(session, 1440, 900);
  it("keeps the picked star's mark under a place kept clear, as D33 keeps it (ruled 17:05); any other mark there goes", () => {
    const m = free[0]!;
    const box = { x0: m.xPx - 5, x1: m.xPx + 5, y0: m.yPx - 5, y1: m.yPx + 5 };
    expect(revealMarks(session, 1440, 900, { clear: [box] }).some((k) => k.finding.id === m.finding.id)).toBe(false);
    const picked = { ...session, selectedFindingId: m.finding.id } as ObservationSession;
    expect(revealMarks(picked, 1440, 900, { clear: [box] }).some((k) => k.finding.id === m.finding.id)).toBe(true);
  });

  it('places a name on its other side, or none, when its box would run into a place kept clear', () => {
    const m = free.find((k) => k.name !== null && !k.nameOnLeft && !k.picked)!;
    // Right of the mark's 14 px room, where its name sits
    const box = { x0: m.xPx + 15, x1: m.xPx + 60, y0: m.yPx - 2, y1: m.yPx + 2 };
    const got = revealMarks(session, 1440, 900, { clear: [box] }).find((k) => k.finding.id === m.finding.id)!;
    expect(got).toBeDefined();
    expect(got.name === null || got.nameOnLeft).toBe(true);
  });
});

describe('the phone block scrolls', () => {
  it('measures again on a scroll, so a ring the block scrolls over goes', () => {
    const session = unmatched();
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session,
          phone: true,
          onSelectFinding: () => {},
          onHome: () => {},
          viewportWidthPx: 390,
          viewportHeightPx: 844,
          onShowPredictedSky: () => {},
          alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest' as const, name: 'Fomalhaut' } },
        })
      )
    );
    const all = rings();
    const off = frameOffset();
    const first = { x: all[0]!.x + off.x, y: all[0]!.y + off.y };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.matches('[data-testid="reveal-status"]') ? { left: first.x - 20, top: first.y - 20, right: first.x + 20, bottom: first.y + 20 } : NONE);
    });
    act(() => container.querySelector('[data-testid="reveal-phone-scroll"]')!.dispatchEvent(new Event('scroll')));
    expect(rings()).toEqual(all.slice(1));
  });
});

describe('useKeepClear', () => {
  const Probe = ({ active }: { active: boolean }) => {
    const boxes = useKeepClear(active, ['#probe-column', '#probe-unlaid']);
    return createElement('output', { 'data-boxes': JSON.stringify(boxes) });
  };
  const boxes = () => JSON.parse(container.querySelector('output')!.getAttribute('data-boxes')!);
  it('measures each place laid out, none that is not, again on a resize, and none while nothing is drawn', () => {
    const column = document.createElement('div');
    column.id = 'probe-column';
    const unlaid = document.createElement('div');
    unlaid.id = 'probe-unlaid';
    document.body.append(column, unlaid);
    let at = { left: 10, top: 20, right: 110, bottom: 220 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.id === 'probe-column' ? at : NONE);
    });
    act(() => root.render(createElement(Probe, { active: true })));
    expect(boxes()).toEqual([{ x0: 10, x1: 110, y0: 20, y1: 220 }]);
    at = { left: 10, top: 20, right: 110, bottom: 320 };
    act(() => window.dispatchEvent(new Event('resize')));
    expect(boxes()).toEqual([{ x0: 10, x1: 110, y0: 20, y1: 320 }]);
    act(() => root.render(createElement(Probe, { active: false })));
    expect(boxes()).toEqual([]);
    column.remove();
    unlaid.remove();
  });
});

// keepcal (UIUX-keepclear-READY.md, last section, decided 13:34): the calibrated reveal's matched marks keep clear too,
// the picked star excepted; MOTION's first line follows: an early mark whose matched place lies under the column has
// no target, so it leaves on frame out
describe('the calibrated reveal keeps its matched marks clear of the status column', () => {
  const calibrated = () => poseSession(sample('plate_calibrated'));
  const marksOn = () => [...container.querySelectorAll('[data-testid^="reveal-mark-"]')].map((el) => ({ id: el.getAttribute('data-testid')!.slice('reveal-mark-'.length), x: parseFloat((el as HTMLElement).style.left), y: parseFloat((el as HTMLElement).style.top) }));
  const reveal = (session: ObservationSession) =>
    act(() => root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900 })));

  it('does not draw a matched mark while it sits under the column', () => {
    const session = calibrated();
    reveal(session);
    const all = marksOn();
    expect(all.length).toBeGreaterThanOrEqual(2);
    const first = all[0]!;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return rectOf(this.matches('[data-testid="reveal-status"]') ? { left: first.x - 30, top: first.y - 30, right: first.x + 30, bottom: first.y + 30 } : NONE);
    });
    reveal(session);
    const clearOfColumn = (m: { x: number; y: number }) => Math.abs(m.x - first.x) >= 30 + 14 || Math.abs(m.y - first.y) >= 30 + 14;
    expect(marksOn().map((m) => m.id)).toEqual(all.filter(clearOfColumn).map((m) => m.id));
    // Picked, it stays, with its card's leader
    reveal({ ...session, selectedFindingId: first.id } as ObservationSession);
    expect(marksOn().some((m) => m.id === first.id)).toBe(true);
  });

  it('sends no early mark to a matched place under the column: that early mark leaves', () => {
    const session = calibrated();
    const free = revealMarks(session, 1440, 900);
    const target = free.find((m) => m.kind === 'catalog' && m.finding.catalogRef?.catalogSourceId)!;
    const frame = { leftPx: 0, topPx: 0, widthPx: 1440, heightPx: 900 };
    const early: EarlyPose = { source: 'moon', marks: [{ id: 'finding-predicted-star-early', name: 'Early', u: 0.9, v: 0.9, key: target.finding.catalogRef!.catalogSourceId }] };
    expect(lockOnPlan(early, frame, free).travelling.map((t) => t.id)).toEqual(['finding-predicted-star-early']);
    const column = { x0: target.xPx - 5, x1: target.xPx + 5, y0: target.yPx - 5, y1: target.yPx + 5 };
    const cleared = revealMarks(session, 1440, 900, { clear: [column] });
    const plan = lockOnPlan(early, frame, cleared);
    expect(plan.travelling).toEqual([]);
    expect(plan.leaving.map((l) => l.id)).toEqual(['finding-predicted-star-early']);
  });
});

// A status block is measured by what it holds, from its first line's top to its buttons' foot (UIUX-keepclear-READY.md:
// "the block of words and buttons ... from its top to the buttons' foot"), not by its own box: the phone's aligning
// block spans the whole screen (inset 0) and the phone's refused block is 970 px tall from the top, and either, taken
// whole, would hide every ring and name
describe('useKeepClear measures a status block by what it holds', () => {
  const Probe = () => createElement('output', { 'data-boxes': JSON.stringify(useKeepClear(true, ['[data-testid="probe-block"]'], ['[data-testid="probe-block"]'])) });
  const boxes = () => JSON.parse(container.querySelector('output')!.getAttribute('data-boxes')!);
  it('takes the union of its laid-out children, and the block itself only when none is laid out', () => {
    const block = document.createElement('div');
    block.setAttribute('data-testid', 'probe-block');
    block.innerHTML = '<span id="probe-tag"></span><p id="probe-lead"></p><div id="probe-go"></div><i id="probe-unlaid"></i>';
    document.body.append(block);
    const at: Record<string, { left: number; top: number; right: number; bottom: number }> = {
      'probe-block': { left: 0, top: 0, right: 390, bottom: 844 },
      'probe-tag': { left: 24, top: 614, right: 120, bottom: 642 },
      'probe-lead': { left: 24, top: 654, right: 366, bottom: 690 },
      'probe-go': { left: 120, top: 772, right: 270, bottom: 816 },
    };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const id = this.id || this.getAttribute('data-testid') || '';
      return rectOf(at[id] ?? NONE);
    });
    act(() => root.render(createElement(Probe)));
    expect(boxes()).toEqual([{ x0: 24, x1: 366, y0: 614, y1: 816 }]);
    // Nothing inside laid out: the block's own box
    for (const id of ['probe-tag', 'probe-lead', 'probe-go']) delete at[id];
    act(() => window.dispatchEvent(new Event('resize')));
    expect(boxes()).toEqual([{ x0: 0, x1: 390, y0: 0, y1: 844 }]);
    block.remove();
  });
});

describe("the phone's counts are a control too", () => {
  it('measures the counts block and the estimate block, each by what it holds', () => {
    for (const selector of ['[data-testid="reveal-phone-estimate"]', '.reveal-phone-counts']) {
      expect(REVEAL_KEEP_CLEAR).toContain(selector);
      expect(KEEP_CLEAR_BLOCKS).toContain(selector);
    }
  });

  // AUDIT-C's owed mutants (22 Nov 03:55): either entry dropped from KEEP_CLEAR_BLOCKS survived, as the screens' tests
  // stub a block's own box with nothing laid out inside it. Without the entry the phone's aligning block (inset 0) and
  // its refused block (970 px tall) are kept clear whole and hide every ring and name (FIELD's walk-0621)
  it('measures the reveal status column and the working screen column by what they hold, with the lists as built', () => {
    for (const testid of ['reveal-status', 'work-early']) {
      const selector = `[data-testid="${testid}"]`;
      expect(KEEP_CLEAR_BLOCKS).toContain(selector);
      const block = document.createElement('div');
      block.setAttribute('data-testid', testid);
      block.innerHTML = '<p id="probe-lead"></p>';
      document.body.append(block);
      const at: Record<string, { left: number; top: number; right: number; bottom: number }> = {
        [testid]: { left: 0, top: 0, right: 390, bottom: 844 },
        'probe-lead': { left: 24, top: 654, right: 366, bottom: 690 },
      };
      vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
        return rectOf(at[this.id || this.getAttribute('data-testid') || ''] ?? NONE);
      });
      // The hook's own list of blocks, as every screen calls it
      const Probe = () => createElement('output', { 'data-boxes': JSON.stringify(useKeepClear(true, [selector])) });
      act(() => root.render(createElement(Probe)));
      expect(JSON.parse(container.querySelector('output')!.getAttribute('data-boxes')!), testid).toEqual([{ x0: 24, x1: 366, y0: 654, y1: 690 }]);
      act(() => root.render(createElement('div')));
      block.remove();
      vi.restoreAllMocks();
    }
  });
});
