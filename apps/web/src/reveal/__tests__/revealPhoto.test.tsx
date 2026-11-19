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
import { revealMarks } from '../revealMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';

// The reveal's "your photo" on the laptop (reveal, approved 2026-11-12; estimate and cautions, decided the same
// night). Sizes and colours are the approved pages' (#l-photo, #l-pick in .cache/uiux/reveal/index.html; #o3, #o456 in
// .cache/uiux/web2/index.html). The sessions are the Python side's own fixture solutions on a 960 by 540 frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
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
    // Picked, it is named by its title whatever that is
    const rigel = numbered.findings.find((f) => f.title === 'HIP 24436')!;
    expect(names({ ...poseSession(numbered), selectedFindingId: rigel.id })).toEqual(['Betelgeuse', 'HIP 24436', 'Aldebaran']);
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
    expect([...mark.querySelectorAll('circle')].map((c) => [c.getAttribute('r'), c.getAttribute('stroke-dasharray')])).toEqual([['7', '2.8 2.6']]);
    expect(mark.querySelector('path')).toBeNull();
    expect(mark.querySelector('.reveal-mark__halo')).toBeNull();
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

  it('shows the words as heard, the sentence, and under a hairline each statement as its tag, with a value line only for the approved kind', () => {
    const session = withParallax(calibrated());
    show(session, { answer: shown(answered(session)) });
    expect(q(ANSWER)!.tagName).toBe('SECTION');
    expect(q('.reveal-answer__q')!.textContent).toBe('How far away is Rigel?');
    expect(q('.reveal-answer__s')!.textContent).toBe('Rigel is about 260 parsecs away, about 860 light years.');
    const parts = [...q('.reveal-answer__st')!.children].map((el) => (el.classList.contains('look-tag') ? `tag:${el.getAttribute('data-evidence-class')}` : el.textContent));
    expect(parts).toEqual(['tag:catalog_matched', '265 parsecs · Hipparcos 2007 parallax', 'tag:observed']);
    expect(q(ANSWER)!.textContent).not.toContain('87');
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
    show(calibrated(), { answer: { heard: '', sentence: 'No speech was heard. Press push-to-talk and speak, or type the command below.', answer: null } });
    expect(q('.reveal-answer__q')).toBeNull();
    expect(q('.reveal-answer__s')!.textContent).toBe('No speech was heard. Press push-to-talk and speak, or type the command below.');
  });

  it('shows the card again once the answer is gone', () => {
    const session = calibrated();
    const { rigel } = rigelOf(session);
    show({ ...session, selectedFindingId: rigel.id }, { answer: null });
    expect(q(ANSWER)).toBeNull();
    expect(q('[data-testid="star-card"]')).not.toBeNull();
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

  it('sets the answer in the card\'s place as the approved page does, rising 10 px as it fades in, and not at all with reduced motion', () => {
    expect(declarationsOf('.reveal-answer')).toEqual({
      position: 'absolute',
      right: '32px',
      top: '118px',
      width: '320px',
      'pointer-events': 'auto',
      animation: 'reveal-card-fade 500ms var(--look-ease-standard) both, reveal-answer-rise 900ms var(--look-ease-spring) both',
    });
    expect(declarationsOf('.reveal-answer__q')).toEqual({ margin: '0 0 14px', 'font-size': '14px', color: 'var(--look-ink-3)', 'letter-spacing': '-0.005em' });
    expect(declarationsOf('.reveal-answer__s')).toEqual({ margin: '0', font: '300 34px / 1.12 var(--look-font-sans)', 'letter-spacing': '-0.035em', 'text-wrap': 'balance' });
    expect(declarationsOf('.reveal-answer__st')).toEqual({ display: 'grid', gap: '10px', 'justify-items': 'start', 'margin-top': '22px', 'padding-top': '18px', 'border-top': '1px solid var(--look-hair)' });
    expect(declarationsOf('.reveal-answer__v')).toEqual({ 'font-size': '13px', color: 'var(--look-ink-2)', 'letter-spacing': '-0.005em', 'font-variant-numeric': 'tabular-nums' });
    expect(css.replace(/\s+/g, ' ')).toContain('@keyframes reveal-answer-rise { from { transform: translateY(10px); } }');
    expect(css.replace(/\s+/g, ' ')).toContain('@media (prefers-reduced-motion: reduce) { .reveal-answer { animation: none; } }');
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
      if (rule.selector.includes('is-picked') && !rule.selector.split(',').every((part) => part.includes('.is-catalog.is-picked'))) pickedRules += 1;
    });
    expect(pickedRules).toBe(0);
    expect(declarationsOf('.reveal-mark.is-predicted')).toEqual({ color: 'var(--look-ev-predicted)' });
    expect(declarationsOf('.reveal-mark.is-predicted .reveal-mark__name')).toEqual({ left: 'calc(50% + 14px)', color: 'var(--look-ev-predicted)' });
  });

  it('lets the pointer through to the photo everywhere but the logo and the marks', () => {
    expect(declarationsOf('.reveal-photo')).toMatchObject({ position: 'fixed', inset: '0', 'pointer-events': 'none' });
  });
});
