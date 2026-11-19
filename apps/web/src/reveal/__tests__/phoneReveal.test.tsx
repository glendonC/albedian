// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { revealMarks } from '../revealMarks.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { cornerPaths } from '../cornerPaths.js';
import { phoneRealSkyShade } from '../RealSkyLayer.js';
import { phoneSlotCrop } from '../phoneSlot.js';

// The Real sky layer draws with WebGL 2, which jsdom has not: a stand-in reports it drew, and keeps what it was given
const sky = vi.hoisted(() => ({ phone: undefined as boolean | undefined, there: undefined as boolean | undefined }));
vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  const { createElement: h, useEffect } = await import('react');
  return {
    ...original,
    RealSkyLayer: (props: { onDrawn?: (drawn: boolean) => void; phone?: boolean; there?: boolean }) => {
      sky.phone = props.phone;
      sky.there = props.there;
      useEffect(() => {
        props.onDrawn?.(true);
      }, [props.onDrawn]);
      return h('div', { className: 'real-sky', 'data-testid': 'real-sky' });
    },
  };
});

// The reveal at phone width (reveal, approved 2026-11-12, iPhone 1; reference .cache/uiux/reveal-b01/out/rbp-photo.jpg,
// rules #rbp-photo in .cache/uiux/reveal-b01/screen/rbp-photo.html): smaller marks named only brighter than magnitude
// 2 or picked, and under the photo the catalog matched count large with its tag, then the others.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const VIEW = { w: 390, h: 844 };
const calibrated = (over: Partial<ObservationSolution> = {}) => poseSession({ ...sample('plate_calibrated'), ...over } as ObservationSolution);

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
const all = (selector: string) => [...container.querySelectorAll(selector)];
function show(session: ObservationSession) {
  act(() => {
    root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, phone: true, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
  });
}
function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let n = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    n += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(n, selector).toBe(1);
  return found;
}

describe('revealMarks at phone width', () => {
  it('names only a named star brighter than magnitude 2, as the design lane\'s phone pages do, or the picked one', () => {
    const solution = sample('plate_calibrated');
    // Betelgeuse at 0.45 stays named; Aldebaran made 2.5, named on the laptop (under 3.2) and not on the phone
    const tuned = {
      ...solution,
      findings: solution.findings.map((f) => (f.title === 'Aldebaran' ? { ...f, catalogRef: { ...f.catalogRef!, apparentMagnitude: 2.5 } } : f)),
    } as ObservationSolution;
    const session = poseSession(tuned);
    const names = (marks: ReturnType<typeof revealMarks>) => Object.fromEntries(marks.map((m) => [m.finding.title, m.name]));
    expect(names(revealMarks(session, 1440, 900))['Aldebaran']).toBe('Aldebaran');
    expect(names(revealMarks(session, VIEW.w, VIEW.h, { phone: true }))['Aldebaran']).toBeNull();
    const aldebaran = tuned.findings.find((f) => f.title === 'Aldebaran')!;
    expect(names(revealMarks({ ...session, selectedFindingId: aldebaran.id }, VIEW.w, VIEW.h, { phone: true }))['Aldebaran']).toBe('Aldebaran');
  });
});

describe('RevealPhoto at phone width: an answer (ask, approved 2026-11-12, iPhone)', () => {
  it('puts the answer under the photo in place of the counts', () => {
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: calibrated(),
          onSelectFinding: () => {},
          onHome: () => {},
          phone: true,
          viewportWidthPx: VIEW.w,
          viewportHeightPx: VIEW.h,
          answer: { heard: 'Is that a plane?', sentence: "Nothing in this photo's data answers that.", answer: null },
        })
      );
    });
    expect(q('[data-testid="reveal-answer"]')!.className).toBe('reveal-answer is-phone');
    expect(q('.reveal-phone-counts')).toBeNull();
  });
});

describe('Real sky at phone width (reveal, approved 2026-11-12, iPhone 2; #p-real)', () => {
  it('draws the layer for the phone, the corners on the slot, the Simulated note under it and no counts', () => {
    // Slotted as the viewport slots it
    const base = calibrated();
    const asset = base.sourceAsset!;
    const slotted = { ...base, cropTransform: { ...base.cropTransform, ...phoneSlotCrop(asset.width, asset.height, VIEW.w, VIEW.h) } } as ObservationSession;
    act(() => {
      root.render(createElement(RevealPhoto, { session: slotted, onSelectFinding: () => {}, onHome: () => {}, phone: true, realSky: true, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    expect(sky.phone).toBe(true);
    expect(all('svg.photo-corners path').map((p) => p.getAttribute('d'))).toEqual(cornerPaths({ leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 }));
    const status = q('[data-testid="reveal-status"]')!;
    expect(status.className).toBe('reveal-status is-phone');
    expect(status.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    expect(status.querySelector('.reveal-status__line')!.textContent).toMatch(/^ESO's photograph of this part of the sky, placed by your photo's solve to within \d+′\. Not light your camera recorded\.$/);
    expect(q('.reveal-phone-counts')).toBeNull();
  });

  it('shades the window above and below the slot as the page does, none over the photo', () => {
    expect(phoneRealSkyShade(844)).toBe(
      'linear-gradient(to bottom, rgba(0, 0, 0, 0.7) 0px, rgba(0, 0, 0, 0.3) 112px, rgba(0, 0, 0, 0) 150px, rgba(0, 0, 0, 0) 600px, rgba(0, 0, 0, 0.55) 660px, rgba(0, 0, 0, 0.78) 844px)'
    );
    expect(phoneRealSkyShade(900)).toBe(
      'linear-gradient(to bottom, rgba(0, 0, 0, 0.7) 0px, rgba(0, 0, 0, 0.3) 112px, rgba(0, 0, 0, 0) 150px, rgba(0, 0, 0, 0) 600px, rgba(0, 0, 0, 0.55) 660px, rgba(0, 0, 0, 0.78) 900px)'
    );
  });

  it('draws no corners and no status over the photo itself at phone width', () => {
    show(calibrated());
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('[data-testid="reveal-status"]')).toBeNull();
  });
});

describe('there at phone width (fly, approved 2026-11-12, iPhone; #p-there)', () => {
  it('shows the readout under the photo with its Simulated tag and no name, only the picked mark, and no status or corners', () => {
    const session = calibrated();
    const rigel = session.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    act(() => {
      root.render(
        createElement(RevealPhoto, {
          session: { ...session, selectedFindingId: rigel.id },
          onSelectFinding: () => {},
          onHome: () => {},
          phone: true,
          flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 },
          viewportWidthPx: VIEW.w,
          viewportHeightPx: VIEW.h,
        })
      );
    });
    const readout = q('[data-testid="flight-readout"]')!;
    expect(readout.className).toBe('flight-readout is-phone');
    expect(readout.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('simulated');
    // The view says what it is, its sources and its limit, as on the laptop (AGENTS.md 1.2; the lead's answer 00:32)
    expect(readout.querySelector('.flight-readout__note')!.textContent).toBe(
      'Drawn or computed by the app; not measured in the source recording. Stars at their Hipparcos 2007 and Gaia DR3 distances. Milky Way: ESO/S. Brunier.'
    );
    expect(all('.flight-readout__stat span').map((s) => s.textContent)).toEqual(['to Rigel', 'from Earth']);
    expect(sky.phone).toBe(true);
    expect(sky.there).toBe(true);
    expect(all('button.reveal-mark').length).toBe(1);
    expect(q('[data-testid="reveal-status"]')).toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('[data-testid="star-card"]')).toBeNull();
    expect(q('.reveal-phone-counts')).toBeNull();
  });

  it('shades the window there as the page does: clear to 78 px before the slot\'s foot, darker under it', () => {
    expect(phoneRealSkyShade(844, { there: true })).toBe(
      'linear-gradient(to bottom, rgba(0, 0, 0, 0.7) 0px, rgba(0, 0, 0, 0.3) 112px, rgba(0, 0, 0, 0) 150px, rgba(0, 0, 0, 0) 560px, rgba(0, 0, 0, 0.6) 640px, rgba(0, 0, 0, 0.85) 844px)'
    );
  });
});

// The no-match reveal at phone width (no-match, approved 2026-11-18, iPhone; #ump-photo), its warnings in one list
// under Show predicted sky, reached by scrolling the whole screen (the lead's answer 23:50, INTERIM)
describe('the no-match reveal at phone width', () => {
  const unmatched = () => {
    const base = sample('horizon_predicted_only');
    return poseSession({ ...base, warnings: ['First warning.', 'Second warning.'], quality: { ...base.quality!, detectedCandidateCount: 31371 } } as ObservationSolution);
  };
  afterEach(() => {
    delete document.documentElement.dataset.phoneScroll;
    document.documentElement.style.removeProperty('--phone-scroll');
  });

  it('puts the sentence, Show predicted sky at the phone size, then the warnings, in one scrolling screen with no counts', () => {
    let opened = 0;
    act(() => {
      root.render(createElement(RevealPhoto, { session: unmatched(), onSelectFinding: () => {}, onHome: () => {}, phone: true, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, onShowPredictedSky: () => (opened += 1) }));
    });
    const screen = q('[data-testid="reveal-phone-scroll"]')!;
    const status = screen.querySelector('[data-testid="reveal-status"]')!;
    expect(status.className).toBe('reveal-status is-unmatched is-phone');
    expect(status.querySelector('.reveal-phone-lead .reveal-status__lead')!.textContent).toBe('Found 31,371 points of light, but could not match them to stars for sure.');
    const button = status.querySelector('.reveal-phone-go button.look-main') as HTMLButtonElement;
    expect(button.textContent).toBe('Show predicted sky');
    expect(button.className).toContain('look-size-phone');
    expect([...status.querySelectorAll('.reveal-status__warns li')].map((li) => li.textContent)).toEqual(['First warning.', 'Second warning.']);
    expect(q('.reveal-phone-counts')).toBeNull();
    act(() => button.click());
    expect(opened).toBe(1);
  });

  it('moves the photo with the screen as it scrolls, and lets it go when the screen leaves', () => {
    act(() => {
      root.render(createElement(RevealPhoto, { session: unmatched(), onSelectFinding: () => {}, onHome: () => {}, phone: true, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h }));
    });
    expect(document.documentElement.dataset.phoneScroll).toBe('');
    const screen = q('[data-testid="reveal-phone-scroll"]') as HTMLElement;
    screen.scrollTop = 120;
    act(() => {
      screen.dispatchEvent(new Event('scroll'));
    });
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('120px');
    act(() => root.render(createElement('div')));
    expect(document.documentElement.dataset.phoneScroll).toBeUndefined();
    expect(document.documentElement.style.getPropertyValue('--phone-scroll')).toBe('');
  });
});

describe('RevealPhoto at phone width', () => {
  it('draws each mark smaller, the approved phone ring with its ticks, and no logo, status, corners or line', () => {
    show(calibrated());
    expect(q('.reveal-photo')!.className).toBe('reveal-photo is-phone');
    const mark = q('button.reveal-mark') as HTMLButtonElement;
    expect(mark.querySelector('circle')!.getAttribute('r')).toBe('4.5');
    expect(mark.querySelector('path')!.getAttribute('d')).toBe('M0 -9V-7.5M0 7.5V9M-9 0H-7.5M7.5 0H9');
    expect(q('[data-testid="reveal-home"]')).toBeNull();
    expect(q('[data-testid="reveal-status"]')).toBeNull();
    expect(q('svg.photo-corners')).toBeNull();
    expect(q('[data-testid="reveal-leader"]')).toBeNull();
  });

  it('puts the catalog matched count large with its tag, then the observed and predicted counts with theirs', () => {
    show(calibrated());
    const big = q('.reveal-phone-counts__big')!;
    expect(big.querySelector('b')!.textContent).toMatch(/^\d/);
    expect(big.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('catalog_matched');
    const more = all('.reveal-phone-counts__more > span.reveal-phone-counts__item').map((item) => item.querySelector('.look-tag')!.getAttribute('data-evidence-class'));
    expect(more).toEqual(['observed']);
    expect(q('.reveal-legend')).toBeNull();
  });

  it('opens the phone card for a picked star (reveal, approved, iPhone 3), the counts giving way to it', () => {
    const session = calibrated();
    show({ ...session, selectedFindingId: session.observationSolution!.findings.find((f) => f.title === 'Rigel')!.id });
    expect(q('[data-testid="star-card"]')!.className).toBe('reveal-card is-phone');
    expect(q('.reveal-card__name')!.textContent).toBe('Rigel');
    expect(q('.reveal-mark.is-picked')).not.toBeNull();
    expect(q('.reveal-phone-counts')).toBeNull();
  });
});

describe("the phone reveal's rules are the approved page's", () => {
  it('sets the counts and the names as #rbp-photo does', () => {
    expect(declarationsOf('.reveal-phone-counts__big')).toEqual({ position: 'absolute', left: '24px', top: '660px', display: 'flex', 'align-items': 'center', gap: '14px' });
    expect(declarationsOf('.reveal-phone-counts__big b')).toEqual({ font: '300 40px / 1 var(--look-font-sans)', 'letter-spacing': '-0.045em', 'font-variant-numeric': 'tabular-nums' });
    expect(declarationsOf('.reveal-phone-counts__more')).toEqual({ position: 'absolute', left: '24px', top: '714px', display: 'flex', gap: '16px', 'align-items': 'center', font: '400 15px / 1 var(--look-font-sans)', 'letter-spacing': '-0.01em', 'font-variant-numeric': 'tabular-nums' });
    expect(declarationsOf('.reveal-phone-counts__item b')).toEqual({ 'font-weight': '400', 'margin-right': '8px' });
    expect(declarationsOf('.reveal-photo.is-phone .reveal-mark__name')).toEqual({ left: 'calc(50% + 16px)', 'font-size': '11px' });
    expect(declarationsOf('.reveal-photo.is-phone .reveal-mark.is-left .reveal-mark__name')).toEqual({ left: 'auto', right: 'calc(50% + 16px)' });
  });

  it('sets the phone answer as #p-answer does, and Ask out loud alone at the phone size at 772', () => {
    // A longer answer grows upward from where the picture's one-line answer ends, 764 px down, so it never runs under
    // Ask out loud at 772 (the lead's answer 23:50 to WEB-UX-unit-phone-1-QUESTION.md, option A, INTERIM)
    expect(declarationsOf('.reveal-answer.is-phone')).toEqual({ left: '24px', right: '24px', top: 'auto', bottom: 'calc(100% - 764px)', width: 'auto' });
    expect(declarationsOf('.reveal-answer.is-phone .reveal-answer__q')).toEqual({ 'font-size': '13px', margin: '0 0 8px' });
    expect(declarationsOf('.reveal-answer.is-phone .reveal-answer__s')).toEqual({ font: '300 22px / 1.15 var(--look-font-sans)', 'letter-spacing': '-0.03em' });
    expect(declarationsOf('.reveal-answer.is-phone .reveal-answer__st')).toEqual({ display: 'flex', 'flex-wrap': 'wrap', 'align-items': 'center', gap: '8px', 'margin-top': '12px', 'padding-top': '0', 'border-top': '0' });
    expect(declarationsOf('.reveal-answer.is-phone .reveal-answer__v')).toEqual({ 'font-size': '12px' });
    expect(declarationsOf('.ask-bar.is-phone')).toEqual({ top: '772px', bottom: 'auto' });
    expect(declarationsOf('.ask-bar.is-phone .look-main, .ask-bar.is-phone .look-second')).toEqual({ '--look-h': 'var(--look-h-phone)', '--look-fs': 'var(--look-fs-phone)' });
  });

  it('sets the phone card as #p-pick does: 12 from the sides, 30 from the foot, its type and buttons at the phone sizes', () => {
    expect(declarationsOf('.reveal-card.is-phone')).toEqual({
      left: '12px',
      right: '12px',
      top: 'auto',
      bottom: '30px',
      width: 'auto',
      padding: '22px 20px 20px',
      'border-radius': '28px',
      background: '#111112',
      border: '1px solid var(--look-hair)',
    });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__name')).toEqual({ margin: '16px 0 0', 'font-size': '46px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__sub')).toEqual({ margin: '8px 0 0' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__numbers')).toEqual({ gap: '12px', 'margin-top': '20px', 'padding-top': '16px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__number b')).toEqual({ 'font-size': '26px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__number b small')).toEqual({ 'font-size': '14px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__label')).toEqual({ 'margin-top': '6px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__source')).toEqual({ margin: '14px 0 0', 'padding-top': '0', 'border-top': '0' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__acts')).toEqual({ 'margin-top': '20px' });
    expect(declarationsOf('.reveal-card.is-phone .reveal-card__acts .look-main, .reveal-card.is-phone .reveal-card__acts .look-second')).toEqual({
      '--look-h': 'var(--look-h-phone)',
      '--look-fs': 'var(--look-fs-phone)',
    });
  });

  it('sets the Real sky note as #p-real does', () => {
    expect(declarationsOf('.reveal-status.is-phone')).toEqual({ left: '24px', right: '24px', top: '660px', width: 'auto', font: '400 14px / 1.5 var(--look-font-sans)' });
  });

  it('sets the readout there as #p-there does', () => {
    // It ends where the approved readout ends (709 px) and grows upward with the disclosure, clear of Back to Moment
    expect(declarationsOf('.flight-readout.is-phone')).toEqual({ left: '24px', right: '24px', top: 'auto', bottom: 'calc(100% - 709px)', width: 'auto' });
    expect(declarationsOf('.flight-readout__note')).toEqual({ margin: '0 0 14px', 'font-size': '13px', 'line-height': '1.5', color: 'var(--look-ink-2)' });
    expect(declarationsOf('.flight-readout.is-phone h1')).toEqual({ display: 'none' });
    expect(declarationsOf('.flight-readout.is-phone .look-tag')).toEqual({ 'margin-bottom': '14px' });
    expect(declarationsOf('.flight-readout.is-phone .flight-readout__stats')).toEqual({ gap: '12px', 'margin-top': '0', 'padding-top': '0', 'border-top': '0' });
    expect(declarationsOf('.flight-readout.is-phone .flight-readout__stat b')).toEqual({ 'font-weight': '300', 'font-size': '40px' });
    expect(declarationsOf('.flight-readout.is-phone .flight-readout__stat small')).toEqual({ 'font-size': '15px' });
    expect(declarationsOf('.flight-readout.is-phone .flight-readout__stat span')).toEqual({ 'margin-top': '6px' });
  });

  it('sets the no-match screen as #ump-photo does, its warnings under the button, the photo moving with it', () => {
    expect(declarationsOf('.reveal-phone-scroll')).toEqual({ position: 'fixed', inset: '0', 'overflow-y': 'auto', 'overscroll-behavior': 'contain', 'pointer-events': 'auto' });
    expect(declarationsOf('.reveal-status.is-unmatched.is-phone')).toEqual({ position: 'relative', left: 'auto', right: 'auto', top: 'auto', width: 'auto', padding: '654px 24px 40px' });
    expect(declarationsOf('.reveal-phone-lead')).toEqual({ 'min-height': '118px' });
    expect(declarationsOf('.reveal-status.is-phone .reveal-status__lead')).toEqual({ margin: '0', font: '300 22px / 1.2 var(--look-font-sans)' });
    expect(declarationsOf('.reveal-phone-go')).toEqual({ display: 'flex', 'justify-content': 'center' });
    expect(declarationsOf('.reveal-status.is-phone .reveal-status__warns')).toEqual({ margin: '24px 0 0' });
    expect(declarationsOf(':root[data-phone-scroll] .alb-source-stage > img, :root[data-phone-scroll] .alb-source-stage > video')).toEqual({ translate: '0 calc(-1 * var(--phone-scroll, 0px))' });
  });

  it('puts the close circle and the views in the top row, 20 px in, 60 px down', () => {
    expect(declarationsOf('.reveal-phone-top')).toEqual({ position: 'fixed', left: '20px', right: '20px', top: '60px', height: '44px', 'z-index': '35', display: 'flex', 'align-items': 'center', 'justify-content': 'space-between' });
    expect(declarationsOf('.reveal-phone-top .look-views__item')).toEqual({ padding: '0 14px' });
  });
});
