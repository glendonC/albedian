// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution, VoiceQuestionReply } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { SIDE_FADE, SourcePhotoStage } from '../../components/SourcePhotoStage.js';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { cornerPaths } from '../cornerPaths.js';
import { leaderPath, revealMarks } from '../revealMarks.js';
import { REAL_SKY_FADE_MS, REVEAL_ARRIVAL_MS, RevealPhoto } from '../RevealPhoto.js';
import { markArrivalRanks, revealMarks as marksOf } from '../revealMarks.js';
import { MOTION_TOKENS } from '@albedian/design-system';
import { linesLeaveMs } from '../../hooks/useLeaving.js';

// The Real sky layer draws with WebGL 2, which jsdom has not: a stand-in reports what each test says it drew
const sky = vi.hoisted(() => ({ drawn: true }));
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void }) => {
      useEffect(() => {
        props.onDrawn?.(sky.drawn);
      }, [props.onDrawn]);
      return h('div', { className: 'real-sky', 'data-testid': 'real-sky' });
    },
  };
});

// The reveal's "your photo" on the laptop (reveal, approved 2026-11-12; estimate and cautions, decided the same
// night). Sizes and colours are the approved pages' (#l-photo, #l-pick in .cache/uiux/reveal/index.html; #o3, #o456 in
// .cache/uiux/web2/index.html). The sessions are the Python side's own fixture solutions on a 960 by 540 frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
const VIEW = { w: 1440, h: 900 };
// A 960 by 540 frame fitted to 1440 by 900: full width, 810 high, 45 from the top; one photo pixel is 1.5 on screen
const calibrated = (over: Partial<ObservationSolution> = {}) => poseSession({ ...sample('plate_calibrated'), ...over } as ObservationSolution);
const onScreen = (xPx: number, yPx: number) => ({ xPx: Math.round((xPx + 0.5) * 1.5), yPx: Math.round(45 + (yPx + 0.5) * 1.5) });

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
const all = (selector: string) => [...container.querySelectorAll(selector)];
function show(session: ObservationSession, props: Partial<Parameters<typeof RevealPhoto>[0]> = {}) {
  act(() => {
    root.render(createElement(RevealPhoto, { session, onSelectFinding: noop, onHome: noop, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, ...props }));
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

describe('revealMarks: only what the approved picture marks', () => {
  it('marks each catalog matched star where it was measured, and no observed point', () => {
    const session = calibrated();
    const marks = revealMarks(session, VIEW.w, VIEW.h);
    expect(marks.map((mark) => [mark.finding.title, mark.kind])).toEqual([
      ['Betelgeuse', 'catalog'],
      ['Rigel', 'catalog'],
      ['Aldebaran', 'catalog'],
    ]);
    for (const mark of marks) {
      const det = mark.finding.sourceDetection!;
      expect({ xPx: mark.xPx, yPx: mark.yPx }).toEqual(onScreen(det.xPx, det.yPx));
      expect(mark.uncertaintyPx).toBeNull();
    }
    expect(session.observationSolution!.findings.filter((f) => f.evidenceClass === 'observed')).toHaveLength(3);
  });

  it('names a star by its proper name, and never writes a catalog number beside a mark that is not picked', () => {
    const solution = sample('plate_calibrated');
    const numbered = {
      ...solution,
      findings: solution.findings.map((f) =>
        f.title === 'Rigel' ? { ...f, title: 'HIP 24436', catalogRef: { ...f.catalogRef!, commonName: f.catalogRef!.catalogSourceId } } : f
      ),
    } as ObservationSolution;
    const names = (session: ObservationSession) => revealMarks(session, VIEW.w, VIEW.h).map((mark) => mark.name);
    expect(names(poseSession(numbered))).toEqual(['Betelgeuse', null, 'Aldebaran']);
    // Picked, a star with no name keeps its mark and no label: its card names it (the nameless card, INTERIM: UIUX-unnamed-QUESTION.md)
    const rigel = numbered.findings.find((f) => f.title === 'HIP 24436')!;
    const picked = revealMarks({ ...poseSession(numbered), selectedFindingId: rigel.id }, VIEW.w, VIEW.h);
    expect(picked.map((mark) => mark.name)).toEqual(['Betelgeuse', null, 'Aldebaran']);
    // Its line to the card starts at its ring
    const mark = picked.find((m) => m.picked)!;
    expect(leaderPath(mark, VIEW.w).startsWith(`M${Number((mark.xPx + 28).toFixed(1))} ${mark.yPx}`)).toBe(true);
  });

  it('marks nothing as catalog matched on a solve that did not pass its independent check', () => {
    // The contract refuses such a solution, so the session is given one after it is built: the screen checks for itself
    const session = calibrated();
    const solution = session.observationSolution!;
    const unverified = { ...session, observationSolution: { ...solution, quality: { ...solution.quality!, passedIndependentVerification: false } } };
    expect(revealMarks(session, VIEW.w, VIEW.h)).toHaveLength(3);
    expect(revealMarks(unverified, VIEW.w, VIEW.h)).toEqual([]);
  });

  it('marks nothing predicted over a photo no pose lines up, whatever the solution predicts', () => {
    expect(sample('horizon_predicted_only').findings.some((f) => f.evidenceClass === 'predicted')).toBe(true);
    expect(revealMarks(poseSession(sample('horizon_predicted_only')), VIEW.w, VIEW.h)).toEqual([]);
  });

  it('marks predicted objects as predicted, each with how far it may be off, when a pose estimate lines the sky up', () => {
    const marks = revealMarks(poseSession(POSE), VIEW.w, VIEW.h);
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) {
      expect(mark.kind).toBe('predicted');
      expect(mark.finding.evidenceClass).toBe('predicted');
      expect(mark.uncertaintyPx).toBeGreaterThan(0);
    }
  });

  it('marks the picked star, and puts a name to the left of a mark too near the right edge for it', () => {
    const session = calibrated();
    const aldebaran = session.observationSolution!.findings.find((f) => f.title === 'Aldebaran')!;
    const marks = revealMarks({ ...session, selectedFindingId: aldebaran.id }, VIEW.w, VIEW.h);
    expect(marks.map((mark) => mark.picked)).toEqual([false, false, true]);
    expect(marks.map((mark) => mark.nameOnLeft)).toEqual([false, false, false]);
    // The same star measured 30 px from the frame's right edge: its name would cross the edge
    const solution = sample('plate_calibrated');
    const nearEdge = {
      ...solution,
      findings: solution.findings.map((f) => (f.title === 'Aldebaran' ? { ...f, sourceDetection: { ...f.sourceDetection!, xPx: 930 } } : f)),
    } as ObservationSolution;
    expect(revealMarks(poseSession(nearEdge), VIEW.w, VIEW.h).map((mark) => mark.nameOnLeft)).toEqual([false, false, true]);
  });

  it('marks nothing outside the photo view, or before the photo has a measured size', () => {
    const session = calibrated();
    expect(revealMarks({ ...session, spatialView: { ...session.spatialView, mode: 'catalog_3d' } }, VIEW.w, VIEW.h)).toEqual([]);
    expect(revealMarks({ ...session, sourceAsset: { ...session.sourceAsset!, width: 1, height: 1 } }, VIEW.w, VIEW.h)).toEqual([]);
  });
});

describe('RevealPhoto: the approved screen, word for word', () => {
  it('shows the logo at 21 px, the solve in its own counts, and the legend, and no other word', () => {
    show(calibrated());
    const logo = q('[data-testid="reveal-home"] .look-logo') as HTMLElement;
    expect(logo.style.getPropertyValue('--look-logo-fs')).toBe('21px');
    const quality = sample('plate_calibrated').quality!;
    expect(all('.reveal-status__line').map((line) => line.textContent)).toEqual([
      `Calibrated · ${quality.matchedInlierCount} matched`,
      `${quality.heldOutInliers} of ${quality.heldOutPairCount} held-out stars landed`,
    ]);
    expect(q('.reveal-status .look-tag')).toBeNull();
    expect(q('.reveal-legend__title')!.textContent).toBe('In this photo');
    expect(all('.reveal-legend__row').map((row) => row.textContent)).toEqual(['3Observed', '3Catalog matched']);
    expect(all('.reveal-legend__row .look-tag').map((tag) => tag.getAttribute('data-evidence-class'))).toEqual(['observed', 'catalog_matched']);
    const warnings = sample('plate_calibrated').warnings ?? [];
    expect(q('[data-testid="reveal-photo"]')!.textContent).toBe(
      ['BetelgeuseRigelAldebaran', 'albedian', all('.reveal-status__line').map((l) => l.textContent).join(''), warnings.join(''), 'In this photo3Observed3Catalog matched'].join('')
    );
  });

  it("writes each of the solve's warnings under the status with the info icon, word for word, and none of its own", () => {
    show(calibrated({ warnings: ['The Sun was about 36 degrees below the horizon: night.', 'Second.'] }));
    const warnings = all('.reveal-status__warning');
    expect(warnings.map((w) => w.textContent)).toEqual(['The Sun was about 36 degrees below the horizon: night.', 'Second.']);
    for (const warning of warnings) expect(warning.querySelector('svg.look-i path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.info);
    show(calibrated({ warnings: [] }));
    expect(q('.reveal-status__warning')).toBeNull();
  });

  it('draws a ring with four ticks on each catalog matched star, as a real button that picks it', () => {
    const onSelectFinding = vi.fn();
    const session = calibrated();
    show(session, { onSelectFinding });
    const marks = all('button.reveal-mark') as HTMLButtonElement[];
    expect(marks.map((m) => m.className)).toEqual(['reveal-mark is-catalog', 'reveal-mark is-catalog', 'reveal-mark is-catalog']);
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    const mark = q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement;
    const at = onScreen(rigel.sourceDetection!.xPx, rigel.sourceDetection!.yPx);
    expect(mark.style.left).toBe(`${at.xPx}px`);
    expect(mark.style.top).toBe(`${at.yPx}px`);
    expect(mark.getAttribute('data-evidence-class')).toBe('catalog_matched');
    expect(mark.getAttribute('aria-pressed')).toBe('false');
    expect(mark.getAttribute('aria-label')).toContain('Rigel');
    expect(mark.querySelector('circle')!.getAttribute('r')).toBe('6');
    expect(mark.querySelector('path')!.getAttribute('d')).toBe('M0 -12V-9M0 9V12M-12 0H-9M9 0H12');
    expect(mark.querySelector('.reveal-mark__name')!.textContent).toBe('Rigel');
    act(() => mark.click());
    expect(onSelectFinding).toHaveBeenCalledTimes(1);
    expect(onSelectFinding).toHaveBeenCalledWith(rigel.id, at);
  });

  it('draws the picked star larger with an outer ring, and says it is pressed', () => {
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    show({ ...session, selectedFindingId: rigel.id });
    const mark = q(`[data-testid="reveal-mark-${rigel.id}"]`) as HTMLButtonElement;
    expect(mark.className).toBe('reveal-mark is-catalog is-picked');
    expect(mark.getAttribute('aria-pressed')).toBe('true');
    expect([...mark.querySelectorAll('circle')].map((c) => c.getAttribute('r'))).toEqual(['22', '10']);
    expect(mark.querySelector('path')!.getAttribute('d')).toBe('M0 -18V-13M0 13V18M-18 0H-13M13 0H18');
    expect(all('.reveal-mark.is-picked')).toHaveLength(1);
  });

  it('draws the corners at the photo, and neither corners nor marks when the browser turned the photo wrongly', () => {
    show(calibrated());
    expect(all('svg.photo-corners path').map((p) => p.getAttribute('d'))).toEqual(cornerPaths({ leftPx: 0, topPx: 45, widthPx: 1440, heightPx: 810 }));
    show(calibrated(), { frameMismatch: true });
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('.reveal-mark')).toBeNull();
    // The words stay: they are about the solve, not about places on screen
    expect(q('.reveal-status__line')).not.toBeNull();
  });

  it('returns to the home from the logo', () => {
    const onHome = vi.fn();
    show(calibrated(), { onHome });
    act(() => (q('[data-testid="reveal-home"]') as HTMLButtonElement).click());
    expect(onHome).toHaveBeenCalledTimes(1);
  });
});

describe('RevealPhoto: a sky that is not matched to stars', () => {
  it('leads with the Predicted tag and the estimate, and draws every mark as predicted with how far it may be off', () => {
    show(poseSession(POSE));
    const tag = q('.reveal-status .look-tag')!;
    expect(tag.getAttribute('data-evidence-class')).toBe('predicted');
    const lines = all('.reveal-status__line').map((line) => line.textContent);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain('Lined up by');
    expect(lines[0]).toContain('not checked against stars');
    expect(lines[1]).toBe('No star in it is matched yet; every mark is drawn from the catalog.');
    const marks = all('button.reveal-mark');
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) {
      expect(mark.className.startsWith('reveal-mark is-predicted')).toBe(true);
      expect(mark.getAttribute('data-evidence-class')).toBe('predicted');
      expect(mark.querySelector('circle')!.getAttribute('stroke-dasharray')).toBe('2.8 2.6');
      expect(mark.querySelector('path')).toBeNull();
    }
    expect(all('.reveal-mark__may-be-off')).toHaveLength(marks.length);
    expect(all('.reveal-legend__row .look-tag').map((t) => t.getAttribute('data-evidence-class'))).toEqual(['observed', 'predicted']);
  });

  it('keeps a picked predicted object drawn as predicted: the dashed amber ring, never the picked star\'s ticks and outer ring (verdict unit-1d)', () => {
    const session = poseSession(POSE);
    const object = revealMarks(session, VIEW.w, VIEW.h)[0]!.finding;
    show({ ...session, selectedFindingId: object.id });
    const mark = q(`[data-testid="reveal-mark-${object.id}"]`) as HTMLButtonElement;
    expect(mark.className).toBe('reveal-mark is-predicted is-picked');
    expect(mark.getAttribute('aria-pressed')).toBe('true');
    // Its own picked look (INTERIM: UIUX-predicted-pick-QUESTION.md): the dashed ring enlarged to 10 with an outer ring at
    // 22, never the star's solid ring with ticks
    expect([...mark.querySelectorAll('circle')].map((c) => [c.getAttribute('r'), c.getAttribute('stroke-dasharray')])).toEqual([
      ['22', null],
      ['10', '2.8 2.6'],
    ]);
    expect(mark.querySelector('path')).toBeNull();
    // Picked, it keeps its name: a predicted object is named by its title, catalog row or not
    expect(mark.querySelector('.reveal-mark__name')!.textContent).toBe(object.title);
    const rowless = {
      ...session,
      selectedFindingId: object.id,
      observationSolution: { ...session.observationSolution!, findings: session.observationSolution!.findings.map((f) => (f.id === object.id ? { ...f, catalogRef: undefined } : f)) },
    } as ObservationSession;
    expect(revealMarks(rowless, VIEW.w, VIEW.h).find((m) => m.picked)!.name).toBe(object.title);
  });

  it("says the app's own sentence with no tag and no mark when nothing lines the sky up, and lists only the classes the photo has", () => {
    show(poseSession(sample('horizon_predicted_only')));
    expect(q('.reveal-status .look-tag')).toBeNull();
    expect(all('.reveal-status__lead')).toHaveLength(1);
    expect(q('.reveal-mark')).toBeNull();
    expect(all('.reveal-legend__row .look-tag').map((t) => t.getAttribute('data-evidence-class'))).toEqual(['predicted']);
  });

  it('leads with the sentence at its own size, offers Show predicted sky as the one main button, and lists the warnings after a hairline (no-match reveal)', () => {
    const base = sample('horizon_predicted_only');
    const onShowPredictedSky = vi.fn();
    const solution = { ...base, warnings: ['First warning.', 'Second warning.'], quality: { ...base.quality!, detectedCandidateCount: 31371 } } as ObservationSolution;
    show(poseSession(solution), { onShowPredictedSky });
    expect(q('.reveal-status')!.className).toBe('reveal-status is-unmatched');
    expect(q('.reveal-status__lead')!.textContent).toBe('Found 31,371 points of light, but could not match them to stars for sure.');
    expect(q('.reveal-status__line')).toBeNull();
    expect(q('.reveal-status__warning')).toBeNull();
    const main = all('button.look-main');
    expect(main).toHaveLength(1);
    expect(main[0]!.textContent).toBe('Show predicted sky');
    expect(main[0]!.querySelector('.look-main__circle path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS['arrow-right']);
    act(() => (main[0] as HTMLButtonElement).click());
    expect(onShowPredictedSky).toHaveBeenCalledTimes(1);
    // The button sits between the sentence and the warnings
    const order = [...q('.reveal-status')!.children].map((el) => el.className);
    expect(order).toEqual(['reveal-status__lead', 'look-main look-size-compact', 'reveal-status__warns']);
    const warns = all('ul.reveal-status__warns > li');
    expect(warns.map((li) => li.textContent)).toEqual(['First warning.', 'Second warning.']);
    for (const li of warns) expect(li.querySelector('path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.info);
    expect(q('.reveal-mark')).toBeNull();
  });

  it('shows no Show predicted sky where no predicted sky can be opened, and no warnings list when there is none', () => {
    show(poseSession({ ...sample('plate_too_few_sources'), warnings: [] } as ObservationSolution));
    expect(q('.reveal-status')!.className).toBe('reveal-status is-unmatched');
    expect(q('.reveal-status__lead')).not.toBeNull();
    expect(q('button.look-main')).toBeNull();
    expect(q('.reveal-status__warns')).toBeNull();
  });

  it('keeps the calibrated status as it was, with no main button', () => {
    show(calibrated());
    expect(q('.reveal-status')!.className).toBe('reveal-status');
    expect(q('.reveal-status__lead')).toBeNull();
    expect(q('button.look-main')).toBeNull();
  });

  it('shows no legend for a photo with no finding', () => {
    show(poseSession(sample('pending_unsolved')));
    expect(q('.reveal-legend')).toBeNull();
  });
});

describe('RevealPhoto: an answer takes the card\'s place (ask, approved 2026-11-12)', () => {
  const ANSWER = '[data-testid="reveal-answer"]';
  const rigelOf = (session: ObservationSession) => {
    const index = session.observationSolution!.findings.findIndex((f) => f.title === 'Rigel');
    return { index, rigel: session.observationSolution!.findings[index]! };
  };
  function answered(session: ObservationSession): VoiceQuestionReply {
    const { index, rigel } = rigelOf(session);
    return {
      heard: 'How far away is Rigel?',
      answer: {
        status: 'answered',
        question: 'distance',
        observationSolutionId: 'sol-synthetic-1',
        sentence: 'Rigel is about 260 parsecs away, about 860 light years.',
        statements: [
          {
            subject: { kind: 'finding', findingId: rigel.id, evidenceClass: 'catalog_matched' },
            quantity: 'distance',
            value: { kind: 'number', amount: 264.55, unit: 'pc' },
            evidenceClass: 'catalog_matched',
            source: { kind: 'solution_field', path: ['findings', index, 'catalogRef', 'bailerJonesDistancePc', 'median'] },
          },
          {
            subject: { kind: 'finding', findingId: rigel.id, evidenceClass: 'observed' },
            quantity: 'detection_snr',
            value: { kind: 'number', amount: 87.2, unit: 'ratio' },
            evidenceClass: 'observed',
            source: { kind: 'solution_field', path: ['findings', index, 'sourceDetection', 'snr'] },
          },
        ],
      },
    } as unknown as VoiceQuestionReply;
  }
  const shown = (reply: VoiceQuestionReply) => ({ heard: reply.heard, sentence: reply.answer.sentence, answer: reply.answer });
  const refusal = { heard: 'Is that a plane?', answer: { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence: "Nothing in this photo's data answers that." } } as VoiceQuestionReply;
  /** The session with Rigel's catalog distance measured by its parallax, in the release the approved picture names. */
  function withParallax(session: ObservationSession): ObservationSession {
    const { index } = rigelOf(session);
    const solution = session.observationSolution!;
    const findings = solution.findings.map((f, i) =>
      i === index ? { ...f, catalogRef: { ...f.catalogRef!, releaseVersion: 'Hipparcos 2007', bailerJonesDistancePc: { lower: 250, median: 264.55, upper: 280, method: 'geometric_parallax' as const } } } : f
    );
    return { ...session, observationSolution: { ...solution, findings } } as ObservationSession;
  }

  it('shows the words as heard, the sentence, and under a hairline each statement as its tag, with each its value line "value unit · source" (C7)', () => {
    const session = withParallax(calibrated());
    show(session, { answer: shown(answered(session)) });
    expect(q(ANSWER)!.tagName).toBe('SECTION');
    expect(q('.reveal-answer__q')!.textContent).toBe('How far away is Rigel?');
    expect(q('.reveal-answer__s')!.textContent).toBe('Rigel is about 260 parsecs away, about 860 light years.');
    const parts = [...q('.reveal-answer__st')!.children].map((el) => (el.classList.contains('look-tag') ? `tag:${el.getAttribute('data-evidence-class')}` : el.textContent));
    expect(parts).toEqual(['tag:catalog_matched', '265 parsecs · Hipparcos 2007 parallax', 'tag:observed', "87.2 times · this photo's pixels"]);
  });

  it('lights up the star the answer is about as a picked star, with the line to the answer, and shows no card', () => {
    const session = calibrated();
    const { rigel } = rigelOf(session);
    const other = session.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.id !== rigel.id)!;
    show({ ...session, selectedFindingId: other.id }, { answer: shown(answered(session)) });
    expect(q('[data-testid="star-card"]')).toBeNull();
    expect(all('.reveal-mark.is-picked').map((m) => m.getAttribute('data-testid'))).toEqual([`reveal-mark-${rigel.id}`]);
    expect(q('[data-testid="reveal-leader"]')).not.toBeNull();
  });

  it('shows a refusal as the words and the sentence only: no statements, nothing lit, no line', () => {
    const session = calibrated();
    show(session, { answer: shown(refusal) });
    expect(q('.reveal-answer__q')!.textContent).toBe('Is that a plane?');
    expect(q('.reveal-answer__s')!.textContent).toBe("Nothing in this photo's data answers that.");
    expect(q('.reveal-answer__st')).toBeNull();
    expect(q('.reveal-mark.is-picked')).toBeNull();
    expect(q('[data-testid="reveal-leader"]')).toBeNull();
  });

  it('closes on Esc, and not on Esc something else already answered or while typing', () => {
    const onCloseAnswer = vi.fn();
    show(calibrated(), { answer: shown(refusal), onCloseAnswer });
    const press = (target: EventTarget, prevented = false) => {
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      if (prevented) event.preventDefault();
      act(() => {
        target.dispatchEvent(event);
      });
    };
    press(window, true);
    const field = document.createElement('input');
    document.body.appendChild(field);
    press(field);
    field.remove();
    expect(onCloseAnswer).not.toHaveBeenCalled();
    press(window);
    expect(onCloseAnswer).toHaveBeenCalledTimes(1);
  });

  it("shows a command's refusal the same way: the words and the app's sentence, nothing lit", () => {
    const session = calibrated();
    show(session, { answer: { heard: 'Take me to Zorblax', sentence: 'I could not find that in this photo\'s sky.', answer: null } });
    expect(q('.reveal-answer__q')!.textContent).toBe('Take me to Zorblax');
    expect(q('.reveal-answer__s')!.textContent).toBe("I could not find that in this photo's sky.");
    expect(q('.reveal-answer__st')).toBeNull();
    expect(q('.reveal-mark.is-picked')).toBeNull();
  });

  it('shows a capture that heard nothing as its sentence alone, with no heard line (the lead\'s answer 21:33)', () => {
    show(calibrated(), { answer: { heard: '', sentence: 'No speech was heard.', sub: 'Press Ask out loud and speak, or type your question.', answer: null } });
    expect(q('.reveal-answer__q')).toBeNull();
    expect(q('.reveal-answer__s')!.textContent).toBe('No speech was heard.');
    // The rest under it at 14 px in the second ink (C11)
    expect(q('.reveal-answer__sub')!.textContent).toBe('Press Ask out loud and speak, or type your question.');
    expect(declarationsOf('.reveal-answer__sub')).toEqual({ margin: '8px 0 0', 'font-size': '14px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
  });

  it('shows the card again once the answer is gone', () => {
    const session = calibrated();
    const { rigel } = rigelOf(session);
    show({ ...session, selectedFindingId: rigel.id }, { answer: null });
    expect(q(ANSWER)).toBeNull();
    expect(q('[data-testid="star-card"]')).not.toBeNull();
  });
});

describe('RevealPhoto: there, after Go there (fly, approved 2026-11-12)', () => {
  const READOUT = '[data-testid="flight-readout"]';
  const rigelPicked = () => {
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    return { session: { ...session, selectedFindingId: rigel.id } as ObservationSession, rigel };
  };

  // The motion record ("Go there", Back to Moment: there's words out at the press): on the laptop the readout leaves,
  // inert, while the status line keeps the Simulated disclosure; on the phone the disclosure rides in the readout, so it stays
  it('lets the readout leave when Back to Moment is pressed on the laptop, and keeps it on the phone', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, returning: true });
    expect(q(READOUT)!.classList.contains('is-leaving')).toBe(true);
    expect(q(READOUT)!.hasAttribute('inert')).toBe(true);
    expect(q('[data-testid="reveal-status"]')).not.toBeNull();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, returning: true, phone: true });
    expect(q(READOUT)!.classList.contains('is-leaving')).toBe(false);
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        rules[rule.selector] = { ...(rules[rule.selector] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.flight-readout.is-leaving']).toEqual({ animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' });
  });

  it('keeps only the picked star\'s mark, white and named, with the line to the readout, and no corners, card, legend or counts', () => {
    const { session, rigel } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    expect(all('button.reveal-mark').map((m) => m.getAttribute('data-testid'))).toEqual([`reveal-mark-${rigel.id}`]);
    expect(q('.reveal-mark')!.className).toContain('is-picked');
    expect(q('[data-testid="reveal-leader"]')).not.toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('[data-testid="star-card"]')).toBeNull();
    expect(q('[data-testid="reveal-legend"]')).toBeNull();
    // On the laptop the sentence is the status line's, not the readout's
    expect(q('.flight-readout__note')).toBeNull();
  });

  it('says what the view is: the Simulated tag and the flight\'s lines, word for word (D27 item 4, decided 19 Nov)', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    const status = q('[data-testid="reveal-status"]')!;
    expect(status.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    // No star layer draws here, so no star is counted and the count's sentence is left out
    expect([...status.querySelectorAll('.reveal-status__line')].map((p) => p.textContent)).toEqual([
      'Drawn or computed by the app; not measured in the source recording.',
      'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances, as bright as they would look from here.',
      'Rings: the stars your photo matched.',
      'Milky Way: ESO/S. Brunier, dimmed in flight, since its stars are as seen from Earth.',
    ]);
  });

  // UIUX-tagphone-READY.md item F1 (decided 13:08 on 21 Nov): while a craft is drawn the laptop's there line ends with D26's
  // own decided line, 13 px like the lines above it; it leaves when no craft is drawn (the phone keeps it in its sheet)
  it("ends the there line with the craft models' source while a craft is drawn, and drops it when none is", () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    const lines = () => [...q('[data-testid="reveal-status"]')!.querySelectorAll('.reveal-status__line')].map((p) => p.textContent);
    expect(lines()).not.toContain("NASA's own 3D models. Drawn by the app, not measured.");
    act(() => (q('[data-testid="craft-picker"] > button') as HTMLButtonElement).click());
    act(() => (q('[role="option"][data-craft="voyager-1"]') as HTMLButtonElement).click());
    expect(lines().at(-1)).toBe("NASA's own 3D models. Drawn by the app, not measured.");
    // Back to your own view: no craft is drawn, so no line
    act(() => (q('[data-testid="craft-picker"] > button') as HTMLButtonElement).click());
    const yours = [...container.querySelectorAll('[role="option"]')].find((o) => o.textContent?.startsWith('Your view')) as HTMLButtonElement;
    act(() => yours.click());
    expect(lines()).not.toContain("NASA's own 3D models. Drawn by the app, not measured.");
  });

  it('offers the craft picker there on the laptop, and the craft\'s real trip under the distances once one flies (D26)', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    const pill = q('[data-testid="craft-picker"] > button') as HTMLButtonElement;
    expect(pill.textContent).toBe('Your view');
    expect(q('[data-testid="pilot-trip"]')).toBeNull();
    act(() => pill.click());
    act(() => (q('[role="option"][data-craft="voyager-1"]') as HTMLButtonElement).click());
    expect(q('[data-testid="pilot-trip"]')!.textContent).toContain('to Rigel at its real speed, 17.0 km/s');
    // At phone width the pill row is D30's (phone-1a: phoneFlight.test.tsx)
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, phone: true });
    expect(q('[data-testid="flight-pills"]')!.className).toBe('flight-pills is-phone');
  });

  it('hands the camera to the person on a press on the sky, with D26\'s lines, and takes it back on another (pilot-1e)', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    const press = () => {
      const sky = q('[data-testid="flight-sky"]')!;
      act(() => {
        sky.dispatchEvent(new MouseEvent('pointerdown', { clientX: 400, clientY: 300 }));
        sky.dispatchEvent(new MouseEvent('pointerup', { clientX: 400, clientY: 300 }));
      });
    };
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    press();
    expect(all('[data-testid="steer-line"]').map((p) => p.textContent)).toEqual(['W and S: faster and slower', 'Drag or arrow keys: turn', 'Press the sky: autopilot again', 'Esc: Back to Moment']);
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(q('[data-testid="reveal-marks"]')).toBeNull();
    expect(q(READOUT)).not.toBeNull();
    expect(q('[data-testid="steer-speed"]')!.textContent).toBe('this flight: 1.00 ly each second');
    expect(all('[data-testid="reveal-status"] .reveal-status__line').map((p) => p.textContent)).not.toContain('Milky Way: ESO/S. Brunier, dimmed in flight, since its stars are as seen from Earth.');
    press();
    expect(q('[data-testid="steer-line"]')).toBeNull();
    expect(q('[data-testid="real-sky"]')).not.toBeNull();
    expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
  });

  it('flies on one\'s own with no star ahead: from Earth only, no trip, and a press on the sky ends it (pilot-1f)', () => {
    const { session } = rigelPicked();
    const onEndFreeFlight = vi.fn();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, freeFlight: true, onEndFreeFlight });
    expect(all('[data-testid="steer-line"]')).toHaveLength(4);
    expect(q(READOUT)!.textContent).toContain('from Earth');
    expect(q(READOUT)!.textContent).not.toContain('to Rigel');
    expect(q('[data-testid="pilot-trip"]')).toBeNull();
    const sky = q('[data-testid="flight-sky"]')!;
    act(() => {
      sky.dispatchEvent(new MouseEvent('pointerdown', { clientX: 400, clientY: 300 }));
      sky.dispatchEvent(new MouseEvent('pointerup', { clientX: 400, clientY: 300 }));
    });
    expect(onEndFreeFlight).toHaveBeenCalledTimes(1);
  });

  it('does not take the press on the phone, where steering waits for its own screen', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, phone: true });
    expect(q('[data-testid="flight-sky"]')).toBeNull();
  });

  it('leaves out what is only true from Earth in the cone view: the panorama, the marks, the readout, the rings and the panorama\'s line (cone-1)', () => {
    const { session } = rigelPicked();
    const flying = { ...session, spatialView: { ...session.spatialView, mode: 'catalog_3d' }, machines: { ...session.machines, navigation: 'space' } } as ObservationSession;
    (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext = class {};
    try {
      show(flying, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
      // D28 item 1: "Your photo's view" at the bottom left, right of the craft pill
      expect(q('[data-testid="cone-pill"]')!.getAttribute('aria-pressed')).toBe('false');
      act(() => (q('[data-testid="cone-pill"]') as HTMLButtonElement).click());
      expect(q('[data-testid="real-sky"]')).toBeNull();
      expect(q('[data-testid="reveal-marks"]')).toBeNull();
      expect(q('[data-testid="reveal-leader"]')).toBeNull();
      expect(q(READOUT)).toBeNull();
      expect(q('[data-testid="craft-picker"]')).toBeNull();
      // D28 item 7's lines, and item 6's readout in the readout's place
      expect(all('[data-testid="reveal-status"] .reveal-status__line').map((p) => p.textContent)).toEqual([
        'Drawn or computed by the app; not measured in the source recording.',
        "Your photo's view: the part of the sky your photo holds, from its solve, out to its farthest matched star with a measured distance.",
        'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances.',
        'Rings: the stars your photo matched.',
      ]);
      expect(q('[data-testid="cone-readout"] h1')!.textContent).toBe("Your photo's view");
      expect(q('[data-testid="cone-readout"]')!.textContent).toContain('of your stars inside');
      expect(q('[data-testid="cone-pill"]')!.getAttribute('aria-pressed')).toBe('true');
      // Pressed again, the flight view comes back at the same star
      act(() => (q('[data-testid="cone-pill"]') as HTMLButtonElement).click());
      expect(q('[data-testid="cone-readout"]')).toBeNull();
      expect(q(READOUT)).not.toBeNull();
      expect(q('[data-testid="craft-picker"]')).not.toBeNull();
    } finally {
      delete (globalThis as unknown as Record<string, unknown>).WebGL2RenderingContext;
    }
  });

  const answer = { heard: 'Is that a plane?', sentence: "Nothing in this photo's data answers that.", answer: { status: 'unanswerable', question: 'unanswerable', reason: 'outside_the_data', sentence: "Nothing in this photo's data answers that." } } as const;
  // AUDIT-C's tag sweep, break 3, laptop half (the lead, 12:59 on 21 Nov): an answer open in the flight hides the readout
  // and its Simulated tag; before the panorama has drawn the column was the photo's own, so no tag showed at all. With an
  // answer open the column is the flight's, at full, its Milky Way line waiting for the panorama it names
  it('keeps the Simulated tag with an answer open in the flight, the Milky Way line only once the panorama has drawn', () => {
    const { session } = rigelPicked();
    sky.drawn = false;
    try {
      show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, answer });
      expect(q(READOUT)).toBeNull();
      const status = q('[data-testid="reveal-status"]')!;
      expect(status.getAttribute('data-moment')).toBe('there');
      expect(status.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
      expect([...status.querySelectorAll('.reveal-status__line')].map((p) => p.textContent)).toEqual([
        'Drawn or computed by the app; not measured in the source recording.',
        'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances, as bright as they would look from here.',
        'Rings: the stars your photo matched.',
      ]);
    } finally {
      sky.drawn = true;
    }
  });

  // UIUX-tagphone-READY.md item B (decided 13:08 on 21 Nov): on the phone the readout keeps its tag row and D27's first
  // sentence while an answer is open, the rest hidden; the answer's foot stands 16 px above the readout's tag row
  it("keeps the phone readout's tag row and first sentence under an open answer, whose foot stands 16 px above the tag row", () => {
    const { session } = rigelPicked();
    const box = (left: number, top: number, right: number, bottom: number) =>
      ({ left, top, right, bottom, x: left, y: top, width: right - left, height: bottom - top, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('flight-readout__tagrow')) return box(24, 640, 366, 676);
      if (this.classList.contains('flight-readout')) return box(24, 640, 366, 696);
      return box(0, 0, 0, 0);
    });
    try {
      show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, phone: true, viewportWidthPx: 390, viewportHeightPx: 844, answer });
      const readout = q(READOUT)!;
      expect(readout.querySelector('.flight-readout__tagrow .look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
      expect(readout.querySelector('.flight-readout__note')!.textContent).toBe('Drawn or computed by the app; not measured in the source recording.');
      // The rest of the readout is hidden while the answer is open, as before
      expect(readout.querySelector('h1')).toBeNull();
      expect(readout.querySelector('.flight-readout__stats')).toBeNull();
      // 844 - 640 + 16: the answer's foot 16 px above the tag row's top
      expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe('220px');
    } finally {
      vi.restoreAllMocks();
    }
  });

  it('keeps the whole phone readout, and the answer where it was, when no answer is open', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, phone: true, viewportWidthPx: 390, viewportHeightPx: 844 });
    expect(q(READOUT)!.querySelector('h1')!.textContent).toBe('Rigel');
    show(session, { answer, viewportWidthPx: 390, viewportHeightPx: 844, phone: true });
    expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe('');
  });

  it('says the Milky Way line with an answer open once the panorama has drawn', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, answer });
    expect(q('[data-testid="reveal-status"]')!.textContent).toContain('Milky Way: ESO/S. Brunier');
  });

  it('keeps the photo\'s own status there while the sky is not drawn, the readout staying (the verdict on unit-realsky-1)', () => {
    sky.drawn = false;
    try {
      const { session } = rigelPicked();
      show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
      expect(q('[data-testid="reveal-status"] .look-tag')).toBeNull();
      expect(q('[data-testid="reveal-status"]')!.textContent).not.toContain('Drawn or computed by the app');
      expect(q(READOUT)).not.toBeNull();
    } finally {
      sky.drawn = true;
    }
  });

  it('puts the readout in the card\'s place: the Simulated tag, the name, then to the star and from Earth in light years to three figures, as the approved screens write them', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.43 } });
    const readout = q(READOUT)!;
    expect(readout.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    expect(readout.querySelector('h1')!.textContent).toBe('Rigel');
    expect([...readout.querySelectorAll('.flight-readout__stat')].map((s) => [s.querySelector('b')!.textContent, s.querySelector('span')!.textContent])).toEqual([
      ['1.00ly', 'to Rigel'],
      ['859ly', 'from Earth'],
    ]);
    // A star with no position in space: only how far the camera is from Earth
    show(session, { flight: { name: 'Rigel', toStarLy: null, fromEarthLy: 0 } });
    expect([...q(READOUT)!.querySelectorAll('.flight-readout__stat span')].map((s) => s.textContent)).toEqual(['from Earth']);
  });

  it('gives the readout\'s place to an answer while one shows', () => {
    const { session } = rigelPicked();
    show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, answer: { heard: 'Is that a plane?', sentence: "Nothing in this photo's data answers that.", answer: null } });
    expect(q(READOUT)).toBeNull();
    expect(q('[data-testid="reveal-answer"]')).not.toBeNull();
  });

  it('sets the readout as the approved page does, arriving at the flight\'s halfway point, as it mounts (the motion record)', () => {
    expect(declarationsOf('.flight-readout')).toEqual({
      position: 'absolute',
      right: '32px',
      top: '118px',
      width: '320px',
      'pointer-events': 'auto',
      // No entrance as a whole: it carries the there screen's Simulated tag, which stands at full as it mounts, while
      // its other parts word in (motion.css)
      animation: 'none',
    });
    expect(declarationsOf('.flight-readout h1')).toEqual({ margin: '22px 0 0', font: '400 66px / 1 var(--look-font-sans)', 'letter-spacing': '-0.05em' });
    expect(declarationsOf('.flight-readout__stats')).toEqual({ display: 'grid', 'grid-template-columns': '1fr 1fr', gap: '16px', 'margin-top': '30px', 'padding-top': '20px', 'border-top': '1px solid var(--look-hair)' });
    expect(declarationsOf('.flight-readout__stat b')).toEqual({ display: 'block', 'font-weight': '400', 'font-size': '44px', 'letter-spacing': '-0.045em', 'line-height': '1', 'font-variant-numeric': 'tabular-nums' });
    expect(declarationsOf('.flight-readout__stat small')).toEqual({ 'font-size': '16px', 'letter-spacing': '-0.01em', color: 'var(--look-ink-2)', 'margin-left': '4px' });
    expect(declarationsOf('.flight-readout__stat span')).toEqual({ display: 'block', 'margin-top': '8px', 'font-size': '13px', color: 'var(--look-ink-3)' });
  });
});

describe('a predicted object picked on a matched photo (INTERIM: UIUX-predicted-pick-QUESTION.md)', () => {
  /** The calibrated fixture with a predicted twin of Rigel: no measurement, its place only from the catalog. */
  function withPredicted() {
    const base = sample('plate_calibrated');
    const rigel = base.findings.find((f) => f.title === 'Rigel')!;
    const predicted = {
      ...rigel,
      id: 'finding-predicted-hip-24436',
      title: 'Rigel B',
      evidenceClass: 'predicted',
      sourceDetection: undefined,
      skyPosition: { ...rigel.skyPosition, raDeg: rigel.catalogRef!.raDeg, decDeg: rigel.catalogRef!.decDeg },
      evidence: { ...rigel.evidence, evidenceClass: 'predicted', verificationMethod: 'Predicted for the given time and place; not measured in the image' },
    };
    const session = calibrated();
    const s = { ...session, observationSolution: { ...session.observationSolution!, findings: [...session.observationSolution!.findings, predicted] } } as ObservationSession;
    return { session: s, rigel, predicted };
  }

  it('is not marked until picked; picked, it is placed through the solve, named, and drawn as predicted', () => {
    const { session, rigel, predicted } = withPredicted();
    expect(revealMarks(session, VIEW.w, VIEW.h).some((m) => m.finding.id === predicted.id)).toBe(false);
    const marks = revealMarks({ ...session, selectedFindingId: predicted.id }, VIEW.w, VIEW.h);
    const mark = marks.find((m) => m.finding.id === predicted.id)!;
    expect(mark.kind).toBe('predicted');
    expect(mark.picked).toBe(true);
    expect(mark.name).toBe('Rigel B');
    expect(mark.uncertaintyPx).toBeNull();
    // Through the solve it lands on the matched star it shares a catalog position with, within a few pixels
    const star = marks.find((m) => m.finding.id === rigel.id)!;
    expect(Math.hypot(mark.xPx - star.xPx, mark.yPx - star.yPx)).toBeLessThan(6);
  });

  it('is not placed where the solve puts it outside the photo', () => {
    const { session, predicted } = withPredicted();
    // Thirty degrees from the frame's centre: on the sky, but past the photo's edge
    const wcs = session.observationSolution!.wcs!;
    const far = { ...predicted, skyPosition: { ...predicted.skyPosition, raDeg: (wcs.crvalRaDeg + 30) % 360, decDeg: wcs.crvalDecDeg } };
    const outside = { ...session, selectedFindingId: far.id, observationSolution: { ...session.observationSolution!, findings: session.observationSolution!.findings.map((f) => (f.id === far.id ? far : f)) } } as ObservationSession;
    expect(revealMarks(outside, VIEW.w, VIEW.h).some((m) => m.finding.id === far.id)).toBe(false);
  });

  it('is not placed over a photo whose solve did not pass its check', () => {
    const { session, predicted } = withPredicted();
    const unverified = { ...session, selectedFindingId: predicted.id, observationSolution: { ...session.observationSolution!, quality: { ...session.observationSolution!.quality!, passedIndependentVerification: false } } } as ObservationSession;
    expect(revealMarks(unverified, VIEW.w, VIEW.h).some((m) => m.finding.id === predicted.id)).toBe(false);
  });

  it('draws its line to the card in amber', () => {
    const { session, predicted } = withPredicted();
    show({ ...session, selectedFindingId: predicted.id });
    expect(q('[data-testid="reveal-leader"]')!.getAttribute('class')).toBe('reveal-leader is-predicted');
    show({ ...session, selectedFindingId: session.observationSolution!.findings.find((f) => f.title === 'Rigel')!.id });
    expect(q('[data-testid="reveal-leader"]')!.getAttribute('class')).toBe('reveal-leader');
  });

  it('sets its picked look as the record draws it: amber, the ring at 1.4, the outer ring at 40 percent, the name at 15 px', () => {
    expect(declarationsOf('.reveal-mark.is-predicted.is-picked .reveal-mark__glyph')).toEqual({ 'stroke-width': '1.4' });
    expect(declarationsOf('.reveal-mark.is-predicted.is-picked .reveal-mark__halo')).toEqual({ stroke: 'rgba(245, 184, 61, 0.4)', 'stroke-width': '1' });
    expect(declarationsOf('.reveal-mark.is-predicted.is-picked .reveal-mark__name')).toEqual({ left: 'calc(50% + 25px)', 'font-size': '15px', 'font-weight': '500', color: 'var(--look-ev-predicted)' });
    expect(declarationsOf('.reveal-leader.is-predicted path')).toEqual({ stroke: 'rgba(245, 184, 61, 0.5)' });
  });
});

describe("the photo's side edges fade, and nothing else of it changes", () => {
  it('fades 56 px at each side on the reveal and not otherwise', () => {
    expect(SIDE_FADE).toBe('linear-gradient(to right, transparent 0, #000 56px, #000 calc(100% - 56px), transparent 100%)');
    act(() => root.render(createElement(SourcePhotoStage, { session: calibrated(), viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, bare: true, fadeSides: true })));
    const img = q('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    expect(img.style.maskImage || img.style.getPropertyValue('-webkit-mask-image')).toContain('56px');
    expect(img.style.opacity).toBe('');
    expect(img.style.filter).toBe('');
    act(() => root.render(createElement(SourcePhotoStage, { session: calibrated(), viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h })));
    const plain = q('[data-testid="source-photo-stage"] img') as HTMLImageElement;
    expect(plain.style.maskImage).toBe('');
    expect(plain.style.getPropertyValue('-webkit-mask-image')).toBe('');
  });

  // The motion record (MOTION-READY-web.md, "Your photo and Real sky": "Original pixels: marks and edge fade
  // --mo-words-out timing (100ms) on press, back with --mo-words-in on release"): the fade's string stays; its
  // transparent edges move 56 px past each side of the photo, so the photo is whole, and back
  it('opens its edges past the photo for Original pixels on the words-out time, and brings them back on words in', () => {
    const stage = (fadeOpen: boolean) => {
      act(() => root.render(createElement(SourcePhotoStage, { session: calibrated(), viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, bare: true, fadeSides: true, fadeOpen })));
      return q('[data-testid="source-photo-stage"] img') as HTMLImageElement & { style: Record<string, string> };
    };
    const closed = stage(false);
    expect(closed.style.maskImage || closed.style.getPropertyValue('-webkit-mask-image')).toContain('56px');
    expect(closed.style.maskSize).toBe('100% 100%');
    expect(closed.style.maskPosition).toBe('center');
    expect(closed.style.maskRepeat).toBe('no-repeat');
    expect(closed.style.transition).toBe('mask-size var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in)');
    const open = stage(true);
    expect(open.style.maskImage || open.style.getPropertyValue('-webkit-mask-image')).toContain('56px');
    expect(open.style.maskSize).toBe('calc(100% + 112px) 100%');
    expect(open.style.transition).toBe('mask-size var(--look-mo-dur-words-out) var(--look-mo-ease-std)');
  });
});

describe("the reveal's rules are the approved pages'", () => {
  it('places the logo, the status and the legend where the approved page has them', () => {
    expect(declarationsOf('.reveal-word')).toMatchObject({ position: 'absolute', left: '36px', top: '30px', 'pointer-events': 'auto' });
    expect(declarationsOf('.reveal-status')).toEqual({
      position: 'absolute',
      left: '36px',
      top: '70px',
      width: '300px',
      font: '400 13px / 1.55 var(--look-font-sans)',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-2)',
    });
    expect(declarationsOf('.reveal-status .look-tag')).toEqual({ 'margin-bottom': '12px' });
    expect(declarationsOf('.reveal-legend')).toEqual({ position: 'absolute', left: '36px', bottom: '36px', display: 'grid', gap: '14px' });
    expect(declarationsOf('.reveal-legend__title')).toEqual({ margin: '0 0 2px', 'font-size': '13px', color: 'var(--look-ink-3)' });
    expect(declarationsOf('.reveal-legend__row')).toEqual({ display: 'flex', 'align-items': 'center', gap: '14px', margin: '0' });
    expect(declarationsOf('.reveal-legend__count')).toEqual({ width: '92px', 'font-size': '26px', 'letter-spacing': '-0.04em', 'font-variant-numeric': 'tabular-nums' });
  });

  it('sets the no-match sentence, its button and its warnings as the approved page does', () => {
    expect(declarationsOf('.reveal-status.is-unmatched')).toEqual({ top: '74px', width: '306px' });
    // The reveal takes no pointer but its own controls: Show predicted sky takes the mouse (FIELD field-2, finding 2)
    expect(declarationsOf('.reveal-status .look-main')).toEqual({ 'pointer-events': 'auto' });
    expect(declarationsOf('.reveal-status__lead')).toEqual({
      margin: '0 0 18px',
      font: '300 24px / 1.2 var(--look-font-sans)',
      'letter-spacing': '-0.03em',
      color: 'var(--look-ink)',
      'text-wrap': 'balance',
    });
    expect(declarationsOf('.reveal-status__warns')).toEqual({
      'list-style': 'none',
      margin: '26px 0 0',
      padding: '16px 0 0',
      'border-top': '1px solid var(--look-hair)',
      display: 'grid',
      gap: '9px',
    });
    expect(declarationsOf('.reveal-status__warns li')).toEqual({
      '--look-ic': '13px',
      display: 'flex',
      gap: '7px',
      'font-size': '12px',
      'line-height': '1.45',
      color: 'var(--look-ink-3)',
    });
    expect(declarationsOf('.reveal-status__warns .look-i')).toEqual({ flex: 'none', 'margin-top': '2px' });
  });

  it('sets the answer in the card\'s place as the approved page does, with no motion of its own (its lines arrive by motion.css)', () => {
    expect(declarationsOf('.reveal-answer')).toEqual({
      position: 'absolute',
      right: '32px',
      top: '118px',
      width: '320px',
      'pointer-events': 'auto',
    });
    expect(declarationsOf('.reveal-answer__q')).toEqual({ margin: '0 0 14px', 'font-size': '14px', color: 'var(--look-ink-3)', 'letter-spacing': '-0.005em' });
    expect(declarationsOf('.reveal-answer__s')).toEqual({ margin: '0', font: '300 34px / 1.12 var(--look-font-sans)', 'letter-spacing': '-0.035em', 'text-wrap': 'balance' });
    expect(declarationsOf('.reveal-answer__st')).toEqual({ display: 'grid', gap: '10px', 'justify-items': 'start', 'margin-top': '22px', 'padding-top': '18px', 'border-top': '1px solid var(--look-hair)' });
    expect(declarationsOf('.reveal-answer__v')).toEqual({ 'font-size': '13px', color: 'var(--look-ink-2)', 'letter-spacing': '-0.005em', 'font-variant-numeric': 'tabular-nums' });
    // the 10 px rise on the button spring is gone (the motion record: a spring is a button's own)
    expect(css).not.toContain('reveal-answer-rise');
  });

  it('brings the answer in as the motion record says: the question as heard, the sentence 20 ms later, then its statements', () => {
    const motionOf = (selector: string): Record<string, string> => {
      const found: Record<string, string> = {};
      postcss.parse(motionCss).walkRules((rule) => {
        if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
        rule.walkDecls((decl) => {
          found[decl.prop] = decl.value.replace(/\s+/g, ' ');
        });
      });
      return found;
    };
    const lineIn = (n: number) => `look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * ${n}) both`;
    expect(motionOf('.reveal-answer__q')).toEqual({ animation: lineIn(0) });
    expect(motionOf('.reveal-answer__s')).toEqual({ animation: lineIn(1) });
    expect(motionOf('.reveal-answer__st')).toEqual({ animation: lineIn(2) });
    expect(fs.readFileSync(path.resolve(here, '../AnswerPanel.tsx'), 'utf8')).toContain("import '../styles/motion.css';");
  });

  it('sets a warning in 12 px at the quietest ink with a 13 px icon', () => {
    expect(declarationsOf('.reveal-status__warning')).toEqual({
      '--look-ic': '13px',
      display: 'flex',
      gap: '7px',
      margin: '12px 0 0',
      'font-size': '12px',
      'line-height': '1.45',
      color: 'var(--look-ink-3)',
    });
  });

  it("draws a mark in the catalog's colour at 1.1, its name 19 px away at 13 px with a dark outline, on a 44 px target", () => {
    expect(declarationsOf('.reveal-mark')).toMatchObject({ width: '44px', height: '44px', transform: 'translate(-50%, -50%)', 'pointer-events': 'auto', color: 'var(--look-ev-catalog)' });
    expect(declarationsOf('.reveal-mark__glyph')).toMatchObject({ fill: 'none', stroke: 'currentColor', 'stroke-width': '1.1' });
    expect(declarationsOf('.reveal-mark__name')).toEqual({
      position: 'absolute',
      left: 'calc(50% + 19px)',
      top: '50%',
      transform: 'translateY(-50%)',
      font: '400 13px / 1 var(--look-font-sans)',
      'letter-spacing': '-0.01em',
      'white-space': 'nowrap',
      color: 'rgba(244, 243, 239, 0.84)',
      'paint-order': 'stroke',
      '-webkit-text-stroke': '3px rgba(0, 0, 0, 0.75)',
    });
    expect(declarationsOf('.reveal-mark.is-left .reveal-mark__name')).toEqual({ left: 'auto', right: 'calc(50% + 19px)' });
  });

  it('draws the picked star in ink at 1.4 with a faint outer ring and its name at 15 px, and a predicted one in amber, picked or not', () => {
    // The picked look is a star's (reveal record): a picked predicted object keeps its predicted drawing (verdict unit-1d)
    expect(declarationsOf('.reveal-mark.is-catalog.is-picked')).toEqual({ color: 'var(--look-ink)' });
    expect(declarationsOf('.reveal-mark.is-catalog.is-picked .reveal-mark__glyph')).toEqual({ 'stroke-width': '1.4' });
    expect(declarationsOf('.reveal-mark.is-catalog.is-picked .reveal-mark__halo')).toEqual({ stroke: 'rgba(244, 243, 239, 0.35)', 'stroke-width': '1' });
    expect(declarationsOf('.reveal-mark.is-catalog.is-picked .reveal-mark__name')).toEqual({ left: 'calc(50% + 25px)', 'font-size': '15px', 'font-weight': '500', color: 'var(--look-ink)' });
    let pickedRules = 0;
    postcss.parse(css).walkRules((rule) => {
      // Each picked rule names its kind: a star's look, or a predicted object's own (INTERIM), never one for both
      if (rule.selector.includes('is-picked') && !rule.selector.split(',').every((part) => part.includes('.is-catalog.is-picked') || part.includes('.is-predicted.is-picked'))) pickedRules += 1;
    });
    expect(pickedRules).toBe(0);
    expect(declarationsOf('.reveal-mark.is-predicted')).toEqual({ color: 'var(--look-ev-predicted)' });
    expect(declarationsOf('.reveal-mark.is-predicted .reveal-mark__name')).toEqual({ left: 'calc(50% + 14px)', color: 'var(--look-ev-predicted)' });
  });

  it('lets the pointer through to the photo everywhere but the logo and the marks', () => {
    expect(declarationsOf('.reveal-photo')).toMatchObject({ position: 'fixed', inset: '0', 'pointer-events': 'none' });
  });
});

// The motion record (MOTION-READY-web.md, "Your photo, Real sky"): a 400 ms dissolve on the standard curve, replacing the
// earlier 0.9 s crossfade that named no source; under reduced motion the token makes it the 270 ms dissolve
describe('Your photo and Real sky dissolve into each other in the motion record\'s 400 ms', () => {
  it('fades the panorama on the view token', () => {
    expect(declarationsOf('.real-sky')).toMatchObject({ transition: 'opacity var(--look-mo-dur-view) var(--look-mo-ease-std)' });
  });

  it('keeps the layer mounted as long as the fade, and no longer', () => {
    expect(REAL_SKY_FADE_MS).toBe(MOTION_TOKENS.viewMs);
    expect(REAL_SKY_FADE_MS).toBe(400);
  });
});

// The motion record (MOTION-READY-web.md, "Matched marks with names"): the solve is over when the marks appear, so they
// must not look like stars being found; they come in brightest first, each in 67 ms, all within 330 ms (Death
// Stranding's pace, measured), never as a sweep across the photo
describe('the matched marks arrive brightest first, all within a third of a second', () => {
  it('ranks the marks by catalog magnitude, brightest first, a mark with no magnitude last in its own order', () => {
    const marks = marksOf(calibrated(), VIEW.w, VIEW.h);
    expect(marks.length).toBeGreaterThan(1);
    const ranks = markArrivalRanks(marks);
    expect([...ranks.values()].sort((a, b) => a - b)).toEqual(marks.map((_, i) => i));
    const byRank = [...marks].sort((a, b) => ranks.get(a.finding.id)! - ranks.get(b.finding.id)!);
    const mags = byRank.map((m) => m.finding.catalogRef?.apparentMagnitude ?? Number.POSITIVE_INFINITY);
    for (let i = 1; i < mags.length; i++) expect(mags[i]!).toBeGreaterThanOrEqual(mags[i - 1]!);
    // a mark with no magnitude keeps its place among the others with none
    const fake = marks.slice(0, 3).map((m, i) => ({ ...m, finding: { ...m.finding, id: `none-${i}`, catalogRef: undefined } }));
    const mixed = markArrivalRanks([...fake, marks[0]!]);
    expect([mixed.get('none-0'), mixed.get('none-1'), mixed.get('none-2'), mixed.get(marks[0]!.finding.id)]).toEqual([1, 2, 3, 0]);
  });

  it('numbers each mark for its place and tells the layer how many there are', () => {
    show(calibrated());
    const layer = container.querySelector<HTMLElement>('[data-testid="reveal-marks"]')!;
    const buttons = [...layer.querySelectorAll<HTMLElement>('button')];
    expect(layer.style.getPropertyValue('--mark-n')).toBe(String(buttons.length));
    const ranks = markArrivalRanks(marksOf(calibrated(), VIEW.w, VIEW.h));
    for (const b of buttons) expect(b.style.getPropertyValue('--mark-i')).toBe(String(ranks.get(b.dataset.testid!.replace('reveal-mark-', ''))));
  });

  it('spaces the marks so the last is in by 330 ms, and pops each in 67 ms', () => {
    const motionOf = (selector: string): Record<string, string> => {
      const found: Record<string, string> = {};
      postcss.parse(motionCss).walkRules((rule) => {
        if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
        rule.walkDecls((decl) => {
          found[decl.prop] = decl.value.replace(/\s+/g, ' ');
        });
      });
      return found;
    };
    expect(motionOf('.reveal-marks')).toEqual({
      '--mark-step': 'max(0ms, calc((var(--look-mo-span-marks) - var(--look-mo-dur-mark)) / max(1, var(--mark-n) - 1)))',
    });
    expect(motionOf('.reveal-marks .reveal-mark')).toMatchObject({
      animation: 'look-mo-frame-in var(--look-mo-dur-mark) linear calc(var(--mark-i) * var(--mark-step)) both',
    });
  });
});

// The motion record (MOTION-READY-web.md, "Reveal parts"): the reveal's words arrive only once its marks are in (the
// marks' span), as words in, 20 ms apart: the status first (its own unit), then In this photo, then Ask out loud
describe('the reveal\'s words arrive after its marks', () => {
  it('brings In this photo in after the marks, one stagger after the status\'s place', () => {
    let found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') === '.reveal-legend' && rule.parent?.type === 'root') {
        rule.walkDecls((decl) => {
          found = { ...found, [decl.prop]: decl.value.replace(/\s+/g, ' ') };
        });
      }
    });
    expect(found).toEqual({
      animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-span-marks) + var(--look-mo-delay-words-in) + var(--look-mo-stagger)) both',
    });
  });
});

// The motion record (MOTION-READY-web.md, "Your photo, Real sky" and "Go there"): the status line comes in after the
// marks when the reveal arrives, 120 ms after the old line went on a view change, and between the flight's two halves
// there. Each state is its own element (keyed), so a change brings the new line in.
describe('the status line comes in at its moment\'s time', () => {
  const momentOf = () => q('[data-testid="reveal-status"]')!.getAttribute('data-moment');
  it('names its moment: your photo, Real sky, there, and a photo nothing was matched on', () => {
    show(calibrated());
    expect(momentOf()).toBe('photo');
    act(() => root.unmount()); root = createRoot(container);
    show(calibrated(), { realSky: true });
    expect(momentOf()).toBe('real-sky');
    act(() => root.unmount()); root = createRoot(container);
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    show({ ...session, selectedFindingId: rigel.id } as ObservationSession, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    expect(momentOf()).toBe('there');
    act(() => root.unmount()); root = createRoot(container);
    show(poseSession(sample('horizon_predicted_only')));
    expect(momentOf()).toBe('unmatched');
  });

  it('is a new element when the moment changes, so its arrival plays again', () => {
    show(calibrated());
    const before = q('[data-testid="reveal-status"]');
    show(calibrated(), { realSky: true });
    expect(q('[data-testid="reveal-status"]')).not.toBe(before);
  });

  it('marks the reveal as arriving until its words are in, and then no longer', () => {
    vi.useFakeTimers();
    try {
      show(calibrated());
      expect(q('[data-testid="reveal-photo"]')!.getAttribute('data-arriving')).toBe('true');
      act(() => vi.advanceTimersByTime(REVEAL_ARRIVAL_MS));
      expect(q('[data-testid="reveal-photo"]')!.hasAttribute('data-arriving')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
    expect(REVEAL_ARRIVAL_MS).toBe(MOTION_TOKENS.marksSpanMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs);
  });

  it('times the line in motion.css: 120 ms after a change, after the marks on arrival, and there as it mounts between the halves', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-status']).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in) both' });
    expect(rules['[data-arriving] .reveal-status']).toEqual({ 'animation-delay': 'calc(var(--look-mo-span-marks) + var(--look-mo-delay-words-in))' });
    // The there line mounts as the second half begins: no wait of its own, so the disclosure shows from then
    expect(rules['.reveal-status[data-moment="there"]']).toBeUndefined();
  });
});

// The motion record: under prefers-reduced-motion every travel becomes the 270 ms dissolve, never a jump with no
// transition (REQ-DSA-2.1). The tokens do that by themselves (--look-mo-dur-view and the words' times become 270 ms), so
// no reduced-motion rule may switch the Real sky layer's dissolve or the readout's words off (measured live 21 Nov:
// the layer jumped to its 30 percent and the readout stood at full on its first frame)
describe('under reduced motion the Real sky layer and the readout dissolve, never jump', () => {
  it('has no reduced-motion rule in reveal.css that turns off their transition or animation', () => {
    const off: string[] = [];
    postcss.parse(css).walkAtRules('media', (media) => {
      if (!/prefers-reduced-motion:\s*reduce/.test(media.params)) return;
      media.walkRules((rule) => {
        if (!/(^|[\s,])\.(real-sky|flight-readout)(?![\w-])/.test(rule.selector)) return;
        rule.walkDecls(/^(transition|animation)/, (decl) => {
          if (/none/.test(decl.value)) off.push(`${rule.selector} ${decl.prop}: ${decl.value}`);
        });
      });
    });
    expect(off).toEqual([]);
    // Their own rules run on the tokens the reduced motion turns into the dissolve
    expect(declarationsOf('.real-sky').transition).toContain('var(--look-mo-dur-view)');
    expect(motionCss.replace(/\s+/g, ' ')).toContain('.flight-readout > * { animation: look-mo-words-in var(--look-mo-dur-words-in)');
  });
});

// AGENTS.md 1.2 with the motion record: a simulated layer on screen always shows its Simulated tag. Measured live
// (MOTION motion-36's note, 17:31): the tag rode with its line's words, 120 ms after the Real sky layer began to show,
// and went 100 ms after the line gave way while the layer took 400 ms to go. So a line that names a simulated layer
// (Real sky, there) brings its tag in at once, with the layer, while its words keep their 120 ms; and the Real sky line
// stays until the layer has gone.
describe('the Simulated tag shows whenever a simulated layer does', () => {
  const momentOf = () => q('[data-testid="reveal-status"]')?.getAttribute('data-moment') ?? null;
  // The tag stands at full as its line mounts, with no fade of its own: a fade starts only at the next drawn frame,
  // and the first Real sky draw can hold the page for 200 ms while the layer's own fade runs on (measured at phone
  // width, 17:40)
  it('shows a simulated line\'s tag at full as it mounts while its words wait their 120 ms, and lets a leaving line go whole', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    const line = '.reveal-status:is([data-moment="real-sky"], [data-moment="there"]):not(.is-leaving)';
    expect(rules[line]).toEqual({ animation: 'none' });
    expect(rules[`${line} > *`]).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in) both' });
    expect(rules[`${line} > [data-evidence-class="simulated"]`]).toEqual({ animation: 'none' });
    // The readout carries the there screen's disclosure too (its tag on the laptop, its tag row at phone width, where no
    // status line shows): the tag at full as it mounts, the rest on its words' time; under reduced motion the readout is
    // the only disclosure until the sky layer has drawn (measured 21 Nov)
    expect(rules['.flight-readout > *']).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger)) both' });
    expect(rules['.flight-readout > [data-evidence-class="simulated"], .flight-readout > .flight-readout__tagrow']).toEqual({ animation: 'none' });
    // Its first sentence (D27's, and D28's in the cone view) is the disclosure's first sentence: on the words-in time, no
    // later (the 17:38 rule; AUDIT-C's tag sweep of 21 Nov, item 8, found it 20 ms late)
    expect(rules['.flight-readout > .flight-readout__note']).toEqual({ animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in) both' });
    expect(rules['.reveal-status.is-leaving']).toEqual({ animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' });
  });

  for (const phone of [false, true])
  it(`keeps the Real sky line, its tag with it, while the layer fades out, then gives way to the photo's${phone ? ', at phone width' : ''}`, () => {
    vi.useFakeTimers();
    try {
      show(calibrated(), { realSky: true, phone });
      expect(momentOf()).toBe('real-sky');
      const line = q('[data-testid="reveal-status"]');
      show(calibrated(), { realSky: false, phone });
      // The layer is still on screen leaving: the same line stays, its tag in it
      expect(q('[data-testid="real-sky"]')).not.toBeNull();
      expect(q('[data-testid="reveal-status"]')).toBe(line);
      expect(line!.querySelector('[data-evidence-class="simulated"]')).not.toBeNull();
      act(() => vi.advanceTimersByTime(REAL_SKY_FADE_MS - 1));
      expect(momentOf()).toBe('real-sky');
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="real-sky"]')).toBeNull();
      // The photo's line on the laptop; at phone width none shows over the photo
      expect(momentOf()).toBe(phone ? null : 'photo');
    } finally {
      vi.useRealTimers();
    }
  });

  // Leaving there at once (reduced motion: the flight back does not travel), the sky layer still fades out on the view
  // time; no Real sky line came before it (there shows its own line, and on the phone only the readout), so one is
  // shown for the fade, its Simulated tag with it (measured at phone width under reduced motion, 21 Nov: six frames)
  for (const phone of [false, true])
  it(`shows the Simulated tag while the layer fades out after there, though no Real sky line came before${phone ? ', at phone width' : ''}`, () => {
    vi.useFakeTimers();
    try {
      const session = calibrated();
      const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
      const there = { ...session, selectedFindingId: rigel.id } as ObservationSession;
      show(there, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 }, phone });
      show(session, { phone });
      expect(q('[data-testid="real-sky"]')).not.toBeNull();
      expect(momentOf()).toBe('real-sky');
      expect(q('[data-testid="reveal-status"] [data-evidence-class="simulated"]')).not.toBeNull();
      act(() => vi.advanceTimersByTime(REAL_SKY_FADE_MS));
      expect(q('[data-testid="real-sky"]')).toBeNull();
      expect(momentOf()).toBe(phone ? null : 'photo');
    } finally {
      vi.useRealTimers();
    }
  });

  it('lets the line go at once with the layer when Original pixels takes the layer away at once', () => {
    show(calibrated(), { realSky: true });
    expect(momentOf()).toBe('real-sky');
    show(calibrated(), { realSky: true, originalPixels: true });
    expect(q('[data-testid="real-sky"]')).toBeNull();
    expect(momentOf()).toBe('photo');
    // Real sky let go while Original pixels is held: no layer is drawn, so no Simulated line is kept for it
    show(calibrated(), { realSky: true });
    show(calibrated(), { realSky: false, originalPixels: true });
    expect(momentOf()).toBe('photo');
  });
});

// The motion record (MOTION-READY-web.md, row "Original pixels (A6)" and "Your photo and Real sky"): pressing Original
// pixels, the marks go on the words-out timing (100 ms; a fade, no sinking: they are marks, not words); released, they
// come back on the words-in timing (300 ms, 120 ms after the release, as a status line after a view change), all
// together: not one by one as they first arrived.
describe('pressing Original pixels, the marks leave as the motion record says', () => {
  it('keeps the marks for the words-out time, leaving and inert, then removes them', () => {
    vi.useFakeTimers();
    try {
      show(calibrated());
      show(calibrated(), { originalPixels: true });
      const marks = q('[data-testid="reveal-marks"]')!;
      expect(marks.classList.contains('is-leaving')).toBe(true);
      expect(marks.hasAttribute('inert')).toBe(true);
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.wordsOutMs - 1));
      expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="reveal-marks"]')).toBeNull();
      show(calibrated());
      expect(q('[data-testid="reveal-marks"]')!.classList.contains('is-leaving')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('lets them go in the one 270 ms dissolve under reduced motion', () => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      show(calibrated());
      show(calibrated(), { originalPixels: true });
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.dissolveMs - 1));
      expect(q('[data-testid="reveal-marks"]')).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="reveal-marks"]')).toBeNull();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('fades them out in motion.css on the words-out time, with no sinking', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-marks.is-leaving']).toEqual({ animation: 'look-mo-frame-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' });
  });

  it('marks them as coming back once released, and not on their first arrival', () => {
    vi.useFakeTimers();
    try {
      show(calibrated());
      expect(q('[data-testid="reveal-marks"]')!.classList.contains('is-back')).toBe(false);
      show(calibrated(), { originalPixels: true });
      expect(q('[data-testid="reveal-marks"]')!.classList.contains('is-back')).toBe(false);
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.wordsOutMs));
      show(calibrated());
      const back = q('[data-testid="reveal-marks"]')!;
      expect(back.classList.contains('is-back')).toBe(true);
      expect(back.classList.contains('is-leaving')).toBe(false);
      expect(back.hasAttribute('inert')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('takes them back as coming back when released while they still leave', () => {
    vi.useFakeTimers();
    try {
      show(calibrated());
      show(calibrated(), { originalPixels: true });
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.wordsOutMs - 1));
      show(calibrated());
      const back = q('[data-testid="reveal-marks"]')!;
      expect(back.classList.contains('is-back')).toBe(true);
      expect(back.classList.contains('is-leaving')).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('fades them back in motion.css on the words-in time, all together, no rise, the ring with them', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-marks.is-back']).toEqual({
      animation: 'look-mo-frame-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in) both',
    });
    expect(rules['.reveal-marks.is-back .reveal-mark, .reveal-marks.is-back .reveal-mark__may-be-off']).toEqual({ animation: 'none' });
    const order = Object.keys(rules);
    expect(order.indexOf('.reveal-marks.is-back .reveal-mark, .reveal-marks.is-back .reveal-mark__may-be-off')).toBeGreaterThan(order.indexOf('.reveal-marks .reveal-mark__may-be-off'));
  });
});

// The motion record (MOTION-READY-web.md, "Reveal parts ... words out (Go there)"): at Go there In this photo's rows
// go last first, 20 ms apart, then its title; it has no frame, so it is gone after 20 ms x rows + 100 ms.
describe('at Go there, In this photo leaves as the motion record says', () => {
  const thereOn = () => {
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    return { ...session, selectedFindingId: rigel.id } as ObservationSession;
  };
  it('numbers its rows, then leaves for its lines\' time once the flight starts, then is gone', () => {
    vi.useFakeTimers();
    try {
      const session = thereOn();
      show(session);
      const legend = q('.reveal-legend') as HTMLElement;
      const rows = all('.reveal-legend__row') as HTMLElement[];
      expect(legend.style.getPropertyValue('--legend-n')).toBe(String(rows.length));
      rows.forEach((row, i) => expect(row.style.getPropertyValue('--legend-i')).toBe(String(i)));
      show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
      expect(q('.reveal-legend')!.classList.contains('is-leaving')).toBe(true);
      const ms = linesLeaveMs(rows.length + 1, MOTION_TOKENS);
      act(() => vi.advanceTimersByTime(ms - 1));
      expect(q('.reveal-legend')).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q('.reveal-legend')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('times the leaving in motion.css: the rows last first, then the title', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-legend.is-leaving .reveal-legend__row']).toEqual({
      animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * (var(--legend-n) - 1 - var(--legend-i))) both',
    });
    expect(rules['.reveal-legend.is-leaving .reveal-legend__title']).toEqual({
      animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * var(--legend-n)) both',
    });
  });
});

// The motion record (MOTION-READY-web.md, row "Estimate status and its may-be-off ring (estimate)"): the ring frames
// in; it says how far its mark may be off, so it comes once the marks are in (their 330 ms span), never before them.
describe("a predicted mark's may-be-off ring arrives as the motion record says", () => {
  it('frames in after the marks\' span, in motion.css', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-marks .reveal-mark__may-be-off']).toEqual({
      animation: 'look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) var(--look-mo-span-marks) both',
    });
  });
});

// The motion record (MOTION-READY-web.md, "Your photo and Real sky" and row "Reveal parts ... words out (Go there)"):
// the old status line goes in words out (100 ms) while the new one waits its 120 ms; the leaving line is no longer a
// status for a screen reader and takes no input.
describe('the status line that gives way leaves as the motion record says', () => {
  const LEAVING = '[data-testid="reveal-status-leaving"]';
  it('keeps the old moment\'s line for the words-out time on a view change, then removes it', () => {
    vi.useFakeTimers();
    try {
      show(calibrated());
      expect(q(LEAVING)).toBeNull();
      show(calibrated(), { realSky: true });
      const old = q(LEAVING)!;
      expect(old.getAttribute('data-moment')).toBe('photo');
      expect(old.classList.contains('is-leaving')).toBe(true);
      expect(old.hasAttribute('inert')).toBe(true);
      expect(old.getAttribute('role')).toBeNull();
      expect(q('[data-testid="reveal-status"]')!.getAttribute('data-moment')).toBe('real-sky');
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.wordsOutMs - 1));
      expect(q(LEAVING)).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(q(LEAVING)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('does the same at Go there, the old line going while the flight starts', () => {
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    const picked = { ...session, selectedFindingId: rigel.id } as ObservationSession;
    show(picked);
    show(picked, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
    expect(q(LEAVING)!.getAttribute('data-moment')).toBe('photo');
    expect(q('[data-testid="reveal-status"]')!.getAttribute('data-moment')).toBe('there');
  });

  it("lets only a moment's line leave: the phone's no-match block goes as before", () => {
    const session = poseSession(sample('horizon_predicted_only'));
    show(session, { phone: true });
    expect(q('[data-testid="reveal-phone-scroll"]')).not.toBeNull();
    show(session, { phone: true, realSky: true });
    expect(q('[data-testid="reveal-status"]')!.getAttribute('data-moment')).toBe('real-sky');
    expect(q(LEAVING)).toBeNull();
    expect(q('[data-testid="reveal-phone-scroll"]')).toBeNull();
  });

  it('times the leaving in motion.css: words out, at once', () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root') return;
      const sel = rule.selector.replace(/\s+/g, ' ');
      rule.walkDecls((decl) => {
        rules[sel] = { ...(rules[sel] ?? {}), [decl.prop]: decl.value.replace(/\s+/g, ' ') };
      });
    });
    expect(rules['.reveal-status.is-leaving']).toEqual({ animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' });
    // after the status rules it overrides, so the leaving wins over the arriving delays
    const order = [...motionCss.matchAll(/^(\.reveal-status\.is-leaving|\.reveal-status\[data-moment="there"\]|\[data-arriving\] \.reveal-status) \{/gm)].map((m) => m[1]);
    expect(order.at(-1)).toBe('.reveal-status.is-leaving');
  });
});
