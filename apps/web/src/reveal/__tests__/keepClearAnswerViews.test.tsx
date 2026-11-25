// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { dispatchAlbedianAction } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { fadeOf, REVEAL_KEEP_CLEAR, useKeepClear } from '../keepClear.js';
import type { Box } from '../predictedSkyMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
// The places the predicted sky hands its marks, as it hands them
const handed = vi.hoisted(() => ({ clear: [] as unknown[] }));
vi.mock('../predictedSkyMarks.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../predictedSkyMarks.js')>();
  return {
    ...original,
    predictedSkyMarks: (...args: Parameters<typeof original.predictedSkyMarks>) => {
      handed.clear = [...(args[5] ?? [])];
      return original.predictedSkyMarks(...args);
    },
  };
});
const { PredictedSkyScreen } = await import('../PredictedSkyScreen.js');

// keepclear-answer-views-one (DECIDED-BY-LEAD 21:07 on 24 Nov, UIUX on FIELD's two points): the open answer in the
// card's place and the laptop's views row are keep-clear boxes, as the keep-clear record's own words have it: no ring or
// name drawn while its star sits under them, the picked mark and leader kept; the Credits link joins them. The answer
// carries the card's fade and the views row the row's, so a name clears their reach (20:12).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const session = poseSession({ ...sample('plate_calibrated') } as ObservationSolution);
const matched = session.observationSolution!.findings.filter((f) => f.evidenceClass === 'catalog_matched');

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

const rectOf = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
const el = (tag: string, className: string) => {
  const e = document.createElement(tag);
  e.className = className;
  return e;
};

describe('the answer, the views row and the Credits link are places the marks keep clear of', () => {
  it('names the open answer on the laptop, the laptop views row and the Credits link; not the phone answer', () => {
    const matches = (e: Element) => REVEAL_KEEP_CLEAR.some((selector) => e.matches(selector));
    const answer = el('section', 'reveal-answer');
    const phoneAnswer = el('section', 'reveal-answer is-phone');
    const nav = el('div', 'reveal-nav');
    const views = el('div', 'look-views look-size-compact');
    nav.appendChild(views);
    const link = el('button', 'reveal-credits');
    expect(matches(answer)).toBe(true);
    expect(matches(phoneAnswer)).toBe(false);
    expect(matches(views)).toBe(true);
    expect(matches(link)).toBe(true);
  });

  it("measures the answer with the card's fade reach and the views row with the row's; the link has no fade", () => {
    const answer = el('section', 'reveal-answer');
    answer.getBoundingClientRect = () => rectOf(1088, 118, 1408, 400);
    expect(fadeOf(answer)).toEqual({ x0: 1044, x1: 1440, y0: 74, y1: 444 });
    const nav = el('div', 'reveal-nav');
    const views = el('div', 'look-views look-size-compact');
    nav.appendChild(views);
    nav.getBoundingClientRect = () => rectOf(1100, 26, 1408, 70);
    views.getBoundingClientRect = () => rectOf(1100, 26, 1356, 62);
    expect(fadeOf(views)).toEqual({ x0: 1056, x1: 1440, y0: 0, y1: 114 });
    const link = el('button', 'reveal-credits');
    link.getBoundingClientRect = () => rectOf(1369, 820, 1408, 868);
    expect(fadeOf(link)).toBeUndefined();
    const phoneAnswer = el('section', 'reveal-answer is-phone');
    phoneAnswer.getBoundingClientRect = () => rectOf(20, 400, 370, 700);
    expect(fadeOf(phoneAnswer)).toBeUndefined();
  });

  it('the reveal measures all three (useKeepClear)', () => {
    const answer = el('section', 'reveal-answer');
    const nav = el('div', 'reveal-nav');
    const views = el('div', 'look-views look-size-compact');
    nav.appendChild(views);
    const link = el('button', 'reveal-credits');
    answer.getBoundingClientRect = () => rectOf(1088, 118, 1408, 400);
    nav.getBoundingClientRect = () => rectOf(1100, 26, 1408, 70);
    views.getBoundingClientRect = () => rectOf(1100, 26, 1356, 62);
    link.getBoundingClientRect = () => rectOf(1369, 820, 1408, 868);
    document.body.append(answer, nav, link);
    const host = document.createElement('div');
    const r = createRoot(host);
    let boxes: Box[] = [];
    const Probe = () => {
      boxes = useKeepClear(true);
      return null;
    };
    try {
      act(() => r.render(createElement(Probe)));
      act(() => r.render(createElement(Probe)));
      const at = (x0: number) => boxes.find((b) => b.x0 === x0);
      expect(at(1088)?.fade).toEqual({ x0: 1044, x1: 1440, y0: 74, y1: 444 });
      expect(at(1100)?.fade).toEqual({ x0: 1056, x1: 1440, y0: 0, y1: 114 });
      expect(at(1369)).toEqual({ x0: 1369, x1: 1408, y0: 820, y1: 868 });
    } finally {
      act(() => r.unmount());
      answer.remove();
      nav.remove();
      link.remove();
    }
  });
});

describe('on the photo: no ring or name under the open answer, the views row or the Credits link', () => {
  const show = (s: ObservationSession, extra: Record<string, unknown> = {}) =>
    act(() =>
      root.render(
        createElement(RevealPhoto, { session: s, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900, ...extra } as unknown as Parameters<typeof RevealPhoto>[0])
      )
    );
  const marks = () =>
    new Map([...container.querySelectorAll<HTMLElement>('button.reveal-mark')].map((m) => [m.dataset.testid!.replace('reveal-mark-', ''), { x: parseFloat(m.style.left), y: parseFloat(m.style.top) }]));
  const ANSWER = { heard: 'What is that bright one?', sentence: "Nothing in this photo's data answers that.", answer: null };

  it('drops the marks under the open answer and brings them back when it closes', () => {
    show(session);
    const all = marks();
    const [id, m] = [...all.entries()][0]!;
    const box = rectOf(m.x - 30, m.y - 30, m.x + 30, m.y + 30);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.matches('.reveal-answer') ? box : rectOf(0, 0, 0, 0);
    });
    show(session, { answer: ANSWER });
    show(session, { answer: ANSWER });
    expect(container.querySelector('.reveal-answer')).not.toBeNull();
    expect(marks().has(id)).toBe(false);
    show(session);
    show(session);
    expect(marks().has(id)).toBe(true);
  });

  it('keeps the star the answer is about, lit as the picked star, under the answer: its mark and leader drawn', () => {
    // An answer lights the first star it is about, in the picked star's place (RevealPhoto's lit)
    const index = session.observationSolution!.findings.findIndex((f) => f.id === matched[0]!.id);
    const star = session.observationSolution!.findings[index]!;
    const about = {
      heard: `How far away is ${star.title}?`,
      sentence: `${star.title} is far away.`,
      answer: {
        status: 'answered',
        question: 'distance',
        observationSolutionId: session.observationSolution!.id,
        sentence: `${star.title} is far away.`,
        statements: [
          {
            subject: { kind: 'finding', findingId: star.id, evidenceClass: 'catalog_matched' },
            quantity: 'detection_snr',
            value: { kind: 'number', amount: 87.2, unit: 'ratio' },
            evidenceClass: 'observed',
            source: { kind: 'solution_field', path: ['findings', index, 'sourceDetection', 'snr'] },
          },
        ],
      },
    };
    show(session);
    const p = marks().get(star.id)!;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.matches('.reveal-answer') ? rectOf(p.x - 30, p.y - 30, p.x + 30, p.y + 30) : rectOf(0, 0, 0, 0);
    });
    show(session, { answer: about });
    show(session, { answer: about });
    expect(container.querySelector('.reveal-answer')).not.toBeNull();
    expect(marks().has(star.id)).toBe(true);
  });

  it('drops a mark under the views row or the Credits link', () => {
    show(session);
    const entries = [...marks().entries()];
    const [idA, a] = entries[0]!;
    const [idB, b] = entries[1]!;
    const nav = el('div', 'reveal-nav');
    const views = el('div', 'look-views look-size-compact');
    nav.appendChild(views);
    const link = el('button', 'reveal-credits');
    document.body.append(nav, link);
    try {
      vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
        if (this === views) return rectOf(a.x - 20, a.y - 20, a.x + 20, a.y + 20);
        if (this === nav) return rectOf(a.x - 20, a.y - 20, a.x + 20, a.y + 20);
        if (this === link) return rectOf(b.x - 20, b.y - 20, b.x + 20, b.y + 20);
        return rectOf(0, 0, 0, 0);
      });
      show(session);
      show(session);
      expect(marks().has(idA)).toBe(false);
      expect(marks().has(idB)).toBe(false);
    } finally {
      nav.remove();
      link.remove();
    }
  });
});

describe('on the predicted sky the Credits link joins its places too', () => {
  it('hands the predicted sky marks the link box', () => {
    const link = el('button', 'reveal-credits');
    link.getBoundingClientRect = () => rectOf(1369, 820, 1408, 868);
    document.body.append(link);
    try {
      const start = poseSession(sample('horizon_predicted_only'));
      const sky = dispatchAlbedianAction(start, { type: 'sky.show', requestId: 'req_sky', expectedViewRevision: start.viewRevision, mode: 'predicted', observationSolutionId: start.observationSolution!.id }).session;
      const pose = { cameraWorldQuaternion: [0, 0, 0, 1], icrsWorldQuaternion: [0, 0, 0, 1], fovDeg: 60, aspect: 1440 / 900, limitingMagnitude: 6 } as const;
      act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => pose })));
      act(() => root.render(createElement(PredictedSkyScreen, { session: sky, onHome: () => {}, skyPose: () => pose })));
      expect((handed.clear as Box[]).some((b) => b.x0 === 1369 && b.y0 === 820)).toBe(true);
    } finally {
      link.remove();
    }
  });
});
