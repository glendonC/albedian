// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution, UploadJob } from '@albedian/contracts';
import { MOTION_TOKENS, resolveMotionTokens } from '@albedian/design-system';
import { poseSession, sample, POSE } from '../../components/__tests__/poseSessions.js';
import { blockLeaveMs } from '../../hooks/useLeaving.js';
import { EARLY_SOURCE_WORDS } from '../earlyPose.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// The lead's 17:38 rule on the photo's own reveal (its places decided 17:56, WEB-UX-refused-tag-QUESTION.md): wherever
// predicted rings are drawn on the photo (the estimate reveal, aligning, refused after an early pose), the status column leads
// with the Predicted tag and has no entrance, so the tag is fully visible from the rings' first frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
const revealCss = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

// The no-match sample with predicted stars above the horizon, as the align screen's tests draw it
const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const starRef = POSE.findings.find((f) => f.catalogRef?.objectType === 'star')!.catalogRef!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...starRef, apparentMagnitude: mag } }) as CelestialFinding;
function unmatched(): ObservationSession {
  const session = poseSession(base);
  return {
    ...session,
    sourceAsset: { ...session.sourceAsset!, width: 3024, height: 4032, displayOrientation: undefined, cameraMetadata: { focalLength35mmMm: 26 } },
    observationSolution: {
      ...session.observationSolution!,
      findings: [...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'), target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16), target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38)],
    } as ObservationSolution,
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
const status = () => q('[data-testid="reveal-status"]')!;
const leadsWithPredicted = () => {
  const first = status().firstElementChild!;
  return first.classList.contains('look-tag') && first.getAttribute('data-evidence-class') === 'predicted';
};

describe('predicted rings on the photo carry their tag from the first frame', () => {
  it('the estimate reveal: its status, which leads with the Predicted tag, has no entrance', () => {
    show(poseSession(POSE));
    expect(leadsWithPredicted()).toBe(true);
    expect(status().classList.contains('has-predicted')).toBe(true);
  });

  // UIUX-tagphone-READY.md item A (decided 13:08 on 21 Nov): on the phone, one block under the photo anchored by its foot
  // 16 px above Ask out loud (y 756), growing upward: the Predicted tag at full, 8 px under it the pose's first
  // sentence, 14 px under that the counts row as built
  it("the phone's estimate reveal: the Predicted tag and the pose's first sentence over its counts, in one block", () => {
    act(() => root.render(createElement(RevealPhoto, { session: poseSession(POSE), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true })));
    const block = q('[data-testid="reveal-phone-estimate"]')!;
    const [tag, source, counts] = [...block.children];
    expect(tag!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(source!.className).toBe('reveal-phone-estimate__source');
    // The app's own pose sentence, its first sentence only (C5's rule): what the sky rests on
    expect(source!.textContent).toMatch(/^Lined up by [^.]+\.$/);
    expect(counts!.getAttribute('data-testid')).toBe('reveal-legend');
    const rule = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(revealCss).walkRules((r) => {
        if (r.selector === selector) r.walkDecls((d) => void (found[d.prop] = d.value));
      });
      return found;
    };
    expect(rule('.reveal-phone-estimate')).toEqual({ position: 'absolute', left: '24px', right: '24px', bottom: 'calc(100% - 756px)', display: 'flex', 'flex-direction': 'column', 'align-items': 'flex-start', 'pointer-events': 'none' });
    expect(rule('.reveal-phone-estimate__source')).toEqual({ margin: '8px 0 0', 'font-size': '13px', 'line-height': '1.45', color: 'var(--look-ink-2)' });
    expect(rule('.reveal-phone-estimate .reveal-phone-counts')).toEqual({ 'margin-top': '14px' });
    // No entrance: nothing in the motion record animates the block or anything in it. It is named only inside :has(), as
    // the condition item D's card rules keep, and every such rule's subject is in the card (AUDIT-C's gap on tagd: a rule
    // like ':has(.reveal-phone-estimate) .look-tag' would reach the block's own tag)
    postcss.parse(motionCss).walkRules((r) => {
      r.selectors.forEach((one) => {
        const outside = one.replace(/:has\([^)]*\)/g, '');
        expect(outside.includes('reveal-phone-estimate'), one).toBe(false);
        if (one.includes('reveal-phone-estimate')) expect(outside, one).toMatch(/\.reveal-card/);
      });
    });
  });

  it("item B on a pose-estimated phone photo: an open answer keeps the Predicted tag and pose sentence in view, hides PhoneCounts, and anchors 16 px above .reveal-phone-estimate", () => {
    const origBcr = HTMLElement.prototype.getBoundingClientRect;
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute('data-testid') === 'reveal-phone-estimate') {
        return { left: 24, top: 688, right: 366, bottom: 756, width: 342, height: 68, x: 24, y: 688, toJSON: () => ({}) } as DOMRect;
      }
      return origBcr.call(this);
    });
    try {
      act(() =>
        root.render(
          createElement(RevealPhoto, {
            session: poseSession(POSE),
            onSelectFinding: () => {},
            onHome: () => {},
            viewportWidthPx: 390,
            viewportHeightPx: 844,
            phone: true,
            answer: { heard: 'Take me to Zorblax', sentence: 'Nothing named Zorblax is in the sky.', answer: null },
          })
        )
      );
      expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBeGreaterThan(0);
      const block = q('[data-testid="reveal-phone-estimate"]')!;
      expect(block).not.toBeNull();
      expect(block.className).toBe('reveal-phone-estimate');
      expect([...block.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-phone-estimate__source']);
      expect(block.querySelector('.reveal-phone-counts')).toBeNull();
      expect((q('[data-testid="reveal-answer"]') as HTMLElement).style.bottom).toBe(`${844 - 688 + 16}px`);
    } finally {
      spy.mockRestore();
    }
  });

  // UIUX-tagphone-READY.md item D (decided 13:08 on 21 Nov; Reduce Motion ruled 14:40): where the phone card sheet covers
  // item A's block, the covered tag stays drawn until the sheet covers it, and the card's own Predicted tag stands in for
  // it at full from the sheet's first frame to its last; with a star picked the counts go (item A)
  it("item D: with a star picked, the estimate block's Predicted tag stays drawn in its place under the card sheet, the counts gone", () => {
    const session = poseSession(POSE);
    const star = session.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const render = (selectedFindingId: string | null) =>
      act(() => root.render(createElement(RevealPhoto, { session: { ...session, selectedFindingId }, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true })));
    render(star.id);
    const block = q('[data-testid="reveal-phone-estimate"]')!;
    const card = q('[data-testid="star-card"]')!;
    expect(block).not.toBeNull();
    expect(card).not.toBeNull();
    const [tag, source, counts] = [...block.children] as HTMLElement[];
    expect(tag!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(source!.className).toBe('reveal-phone-estimate__source');
    // The counts go from view but keep their room, so the tag above them does not move while the sheet rises over it
    expect(counts!.getAttribute('data-testid')).toBe('reveal-legend');
    expect(counts!.style.visibility).toBe('hidden');
    // Drawn before the card, so the sheet (no stacking of its own) paints over the block as it rises
    expect(block.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The motion rules below reach this card: the block is in the phone reveal while the card is up
    expect(q('.reveal-photo.is-phone [data-testid="reveal-phone-estimate"]')).toBe(block);
    // Let go: the sheet goes down over the block, which is drawn throughout, the counts back in view
    render(null);
    expect(q('[data-testid="star-card"]')!.classList.contains('is-leaving')).toBe(true);
    expect(q('[data-testid="reveal-phone-estimate"]')).toBe(block);
    expect((block.children[2] as HTMLElement).style.visibility).toBe('');
  });

  it("item D: the card's own Predicted tag at full from the sheet's first frame to its last over the estimate block, under Reduce Motion too", () => {
    // Over item A's block, and over the same tag where the phone restoration carries it (its lead in the wait, or at the
    // head of a stop's block: phoneRestoreTag.test.tsx)
    const over = '.reveal-photo.is-phone:has(.reveal-phone-estimate, .restore-phone-lead, .restore-phone-why__lead)';
    const top: Record<string, Record<string, string>> = {};
    const reduced: Record<string, Record<string, string>> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      const sel = rule.selector.replace(/\s+/g, ' ');
      const into = rule.parent?.type === 'root' ? top : rule.parent?.type === 'atrule' && /prefers-reduced-motion:\s*reduce/.test((rule.parent as { params?: string }).params ?? '') ? reduced : null;
      if (!into) return;
      rule.walkDecls((d) => void (into[sel] = { ...(into[sel] ?? {}), [d.prop]: d.value.replace(/\s+/g, ' ') }));
    });
    // The tag row rides the sheet at full, in and out; the close beside it keeps the top row's own times
    expect(top[`${over} .reveal-card__top`]).toEqual({ animation: 'none' });
    expect(top[`${over} .reveal-card.is-leaving .reveal-card__top`]).toEqual({ animation: 'none' });
    expect(top[`${over} .reveal-card__top > :not(.look-tag)`]).toEqual({
      animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger) * 0) both',
    });
    expect(top[`${over} .reveal-card.is-leaving .reveal-card__top > :not(.look-tag)`]).toEqual({
      animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 6) both',
    });
    // Under Reduce Motion only the sheet's fill and hairline dissolve (MOTION's keyframes for the predicted sky), so the
    // card's tag is at full on every frame while the block's tag stays drawn under it
    expect(reduced[`${over} .reveal-card.is-phone`]).toEqual({ animation: 'reveal-sheet-fill-in var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both' });
    expect(reduced[`${over} .reveal-card.is-phone.is-leaving`]).toEqual({ animation: 'reveal-sheet-fill-out var(--look-mo-dur-sheet) var(--look-mo-ease-sheet) both' });
  });

  it("keeps the phone's calibrated counts where they were, outside any block, with no tag line over them", () => {
    act(() => root.render(createElement(RevealPhoto, { session: poseSession(sample('plate_calibrated')), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true })));
    expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
    expect(q('.reveal-phone-counts')!.parentElement!.getAttribute('data-testid')).not.toBe('reveal-phone-estimate');
  });

  it('aligning: the column over the rings the person drags leads with the Predicted tag', () => {
    show(unmatched());
    expect(leadsWithPredicted()).toBe(false);
    expect(status().classList.contains('has-predicted')).toBe(false);
    act(() => button('Line it up yourself')!.click());
    expect(q('[data-testid="align-layer"], .align-layer')).not.toBeNull();
    expect(leadsWithPredicted()).toBe(true);
    expect(status().classList.contains('has-predicted')).toBe(true);
    // The column's own words follow the tag, unchanged
    expect(status().children[1]!.textContent).toBe('Drag the stars onto the dots you see.');
    // Cancelled, the rings go and so does the tag
    act(() => button('Cancel')!.click());
    expect(leadsWithPredicted()).toBe(false);
  });

  it('refused after an early pose: the still amber early marks stay with the Predicted tag over the no-match sentence', () => {
    const refused = unmatched();
    const sky: ObservationSolution = { ...POSE, sourceSha256: refused.sourceAsset!.sha256 };
    show(refused, { lockOnSky: sky });
    expect(q('[data-testid="early-marks"]')).not.toBeNull();
    expect(leadsWithPredicted()).toBe(true);
    expect(status().classList.contains('has-predicted')).toBe(true);
    // Place 4 keeps the early marks' source line, under the no-match sentence (the 17:38 rule names it a tag)
    const lead = status().querySelector('.reveal-status__lead')!;
    expect(lead.nextElementSibling!.className).toBe('reveal-status__from');
    expect(lead.nextElementSibling!.textContent).toMatch(/^Placed from .*: predicted until matched\.$/);
  });

  it('a no-match reveal with no rings on the photo is as approved: no tag, its words on their entrance', () => {
    show(unmatched());
    expect(q('[data-testid="early-marks"]')).toBeNull();
    expect(status().querySelector('.look-tag')).toBeNull();
    expect(status().querySelector('.reveal-status__from')).toBeNull();
    expect(status().classList.contains('has-predicted')).toBe(false);
  });

  // UIUX-tagphone-READY.md item C (decided 13:08 on 21 Nov): the phone's aligning screen leads with the Predicted tag at
  // left 24, its foot 12 px above "Drag the stars onto the dots you see." (top 654): 614 to 642, at full, no entrance
  it('at phone width the aligning screen leads with the Predicted tag above its sentence, at full from the first frame', () => {
    act(() => root.render(createElement(RevealPhoto, { session: unmatched(), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onShowPredictedSky: () => {} })));
    act(() => button('Line it up yourself')!.click());
    const block = q('.align-phone')!;
    const tag = block.firstElementChild!;
    expect(tag.className).toBe('align-phone__tag');
    expect(tag.querySelector('.look-tag')!.getAttribute('data-evidence-class')).toBe('predicted');
    expect(tag.nextElementSibling!.className).toBe('align-phone__lead');
    const rule: Record<string, string> = {};
    postcss.parse(revealCss).walkRules((r) => {
      if (r.selector === '.align-phone__tag') r.walkDecls((d) => void (rule[d.prop] = d.value));
    });
    expect(rule).toEqual({ position: 'absolute', left: '24px', top: '614px', display: 'flex' });
    // No entrance: the block is not a reveal status column, and nothing in the motion record animates it as it comes; only
    // its copy leaving at Solve from here has rules (MOTION-READY-web.md section six, DECIDED-BY-LEAD 22 Nov 23:36)
    const naming: string[] = [];
    postcss.parse(motionCss).walkRules((r) => {
      if (r.selector.includes('align-phone')) naming.push(r.selector);
    });
    expect(naming.filter((selector) => !selector.startsWith('.align-phone.is-leaving '))).toEqual([]);
  });

  it('gives that column no entrance, arriving or not, and keeps its words out when it leaves', () => {
    const found: Array<{ selector: string; decls: Record<string, string> }> = [];
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.parent?.type !== 'root' || !rule.selector.includes('has-predicted')) return;
      const decls: Record<string, string> = {};
      rule.walkDecls((d) => {
        decls[d.prop] = d.value;
      });
      found.push({ selector: rule.selector.replace(/\s+/g, ' '), decls });
    });
    expect(found).toEqual([{ selector: '.reveal-status.has-predicted:not(.is-leaving), [data-arriving] .reveal-status.has-predicted:not(.is-leaving)', decls: { animation: 'none' } }]);
    // The arriving rule it overrides is the one that waits the marks' span
    let arriving = '';
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector === '[data-arriving] .reveal-status') rule.walkDecls((d) => {
        arriving = d.value;
      });
    });
    expect(arriving).toContain('--look-mo-span-marks');
    // No rule gives the tag inside the status an entrance of its own
    postcss.parse(motionCss).walkRules((rule) => {
      expect(/reveal-status[^,]*\.look-tag/.test(rule.selector), rule.selector).toBe(false);
    });
  });

  // UIUX-tagphone-READY.md item D (ruled 23 Nov, DECIDED-BY-LEAD): when a non-predicted StarCard opens on the phone
  // over drawn predicted rings, StarCard keeps its own EvidenceClass while .reveal-phone-estimate.is-above-card stands
  // 16 px above StarCard (y = 453) with D19's fade, animation: none, PhoneCounts omitted, and one Predicted tag total
  it('item D for non-predicted StarCard: keeps StarCard EvidenceClass and stands .reveal-phone-estimate.is-above-card 16 px above StarCard across idle, Original pixels, restore and refused alignment', async () => {
    const rule = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(revealCss).walkRules((r) => {
        if (r.selector === selector) r.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
      });
      return found;
    };
    expect(rule('.reveal-phone-estimate.is-above-card')).toEqual({
      bottom: 'calc(100% - 453px)',
      animation: 'none',
    });
    expect(rule('.reveal-phone-estimate.is-above-card::before')).toEqual(rule('.reveal-answer.is-phone::before'));

    const predictedTags = () => container.querySelectorAll('.look-tag[data-evidence-class="predicted"]');
    const estSession = poseSession(POSE);
    const moon = estSession.observationSolution!.findings.find((f) => f.evidenceClass === 'observed')!;
    expect(moon).toBeDefined();

    // Laptop is unaffected: no phone estimate block renders when an observed StarCard opens at laptop width
    show({ ...estSession, selectedFindingId: moon.id });
    expect(q('[data-testid="star-card"] .reveal-card__top > .look-tag')!.getAttribute('data-evidence-class')).toBe('observed');
    expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
    act(() => root.unmount());
    root = createRoot(container);

    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).includes('openngc.json')
        ? new Response(JSON.stringify({ format: 'albedian.deep-sky-catalog', columns: ['name', 'type', 'vMag', 'bMag'], rows: [['NGC0224', 'G', 3.44, 4.36]] }), { status: 200 })
        : new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 })
    );
    try {
      const renderPhone = (s: ObservationSession, extra: Record<string, unknown> = {}) =>
        act(() =>
          root.render(
            createElement(RevealPhoto, {
              session: s,
              onSelectFinding: () => {},
              onHome: () => {},
              viewportWidthPx: 390,
              viewportHeightPx: 844,
              phone: true,
              onRestore: () => {},
              onCancelRestore: () => {},
              onShowPredictedSky: () => {},
              ...extra,
            })
          )
        );

      // 1. Idle estimate photo & clip: Observed StarCard + .reveal-phone-estimate.is-above-card, PhoneCounts omitted, 1 Predicted tag
      renderPhone({ ...estSession, selectedFindingId: moon.id });
      const card = q('[data-testid="star-card"]')!;
      expect(card.querySelector('.reveal-card__top > .look-tag')!.getAttribute('data-evidence-class')).toBe('observed');
      const above = q('[data-testid="reveal-phone-estimate"]')!;
      expect(above.className).toBe('reveal-phone-estimate is-above-card');
      expect([...above.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted', 'reveal-phone-estimate__source']);
      expect(above.children[1]!.textContent).toMatch(/^Lined up by [^.]+\.$/);
      expect(q('.reveal-phone-counts')).toBeNull();
      expect(predictedTags()).toHaveLength(1);

      const estClip = poseSession(POSE, 'video');
      renderPhone({ ...estClip, selectedFindingId: moon.id });
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(predictedTags()).toHaveLength(1);

      // 2. Original pixels: stays until marksShown finishes leaving (wordsOutMs), then hides; returns on frame 0 on release
      renderPhone({ ...estSession, selectedFindingId: moon.id }, { originalPixels: true });
      expect(q('[data-testid="reveal-marks"]')!.classList.contains('is-leaving')).toBe(true);
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.wordsOutMs - 1));
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(predictedTags()).toHaveLength(1);
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="reveal-marks"]')).toBeNull();
      expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
      expect(q('[data-testid="reveal-photo"]')!.getAttribute('data-card-above-predicted')).toBe('true');
      expect(predictedTags()).toHaveLength(0);
      renderPhone({ ...estSession, selectedFindingId: moon.id }, { originalPixels: false });
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(predictedTags()).toHaveLength(1);

      // 3. Card leaving (normal and Reduce Motion): stays .is-above-card throughout leaving, then returns to foot with PhoneCounts
      const normalCardLeaveMs = Math.max(blockLeaveMs(7, resolveMotionTokens(false)), MOTION_TOKENS.sheetMs);
      renderPhone({ ...estSession, selectedFindingId: null });
      expect(q('[data-testid="star-card"]')!.classList.contains('is-leaving')).toBe(true);
      act(() => vi.advanceTimersByTime(normalCardLeaveMs - 1));
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(q('.reveal-phone-counts')).toBeNull();
      expect(predictedTags()).toHaveLength(1);
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('[data-testid="reveal-photo"]')!.getAttribute('data-card-above-predicted')).toBeNull();
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate');
      expect(q('.reveal-phone-counts')).not.toBeNull();

      (window as unknown as Record<string, unknown>).matchMedia = (query: string) => ({
        matches: query.includes('prefers-reduced-motion: reduce'),
        addEventListener: () => {},
        removeEventListener: () => {},
      });
      try {
        const reducedTokens = resolveMotionTokens(true);
        const reducedCardLeaveMs = Math.max(blockLeaveMs(7, reducedTokens), reducedTokens.sheetMs);
        renderPhone({ ...estSession, selectedFindingId: moon.id });
        expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
        renderPhone({ ...estSession, selectedFindingId: null });
        act(() => vi.advanceTimersByTime(reducedCardLeaveMs - 1));
        expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
        expect(predictedTags()).toHaveLength(1);
        act(() => vi.advanceTimersByTime(1));
        expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate');
      } finally {
        delete (window as unknown as Record<string, unknown>).matchMedia;
      }

      // Opening an answer while StarCard was open immediately hands back to item B's foot block
      renderPhone({ ...estSession, selectedFindingId: moon.id });
      renderPhone(
        { ...estSession, selectedFindingId: moon.id },
        { answer: { heard: 'Where is Zorblax?', sentence: 'Nothing named Zorblax is in the sky.', answer: null } }
      );
      expect(q('[data-testid="star-card"]')).toBeNull();
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate');

      // 4. phoneRestoring & phoneStopped: duplicate .restore-phone-lead / .restore-phone-why__lead hidden underneath
      const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
      const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
      const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
      const still = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
      const runningJob = { ...rest, state: 'running', stages: [still('decode'), still('measure')] } as unknown as UploadJob;
      const refusedJob = { ...rest, state: 'rejected', stages: [still('decode'), still('verify')] } as unknown as UploadJob;

      renderPhone({ ...estSession, selectedFindingId: moon.id }, { restoration: { phase: 'working', job: runningJob } });
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(q('.restore-phone-lead')).toBeNull();
      expect(q('.restore-phone-wait')).not.toBeNull();
      expect(predictedTags()).toHaveLength(1);

      renderPhone(
        { ...estSession, selectedFindingId: moon.id },
        { restoration: { phase: 'stopped', job: refusedJob, sentence: 'Stopped.', subline: UNCHANGED, canTryAgain: false } }
      );
      expect(q('[data-testid="reveal-phone-estimate"]')!.className).toBe('reveal-phone-estimate is-above-card');
      expect(q('.restore-phone-why__lead')).toBeNull();
      expect(q('.restore-phone-stopped')).not.toBeNull();
      expect(predictedTags()).toHaveLength(1);

      // 5. PhoneAlignRefused: observed StarCard over kept alignment rings shows EARLY_SOURCE_WORDS.alignment above card and hides duplicate tag below
      const refusedAlignBase = unmatched();
      const obsPoint: CelestialFinding = { ...moon, id: 'finding-observed-unmatched-1', title: 'Point source' };
      const refusedAlignSession = {
        ...refusedAlignBase,
        selectedFindingId: obsPoint.id,
        observationSolution: { ...refusedAlignBase.observationSolution!, findings: [...refusedAlignBase.observationSolution!.findings, obsPoint] },
      } as ObservationSession;
      const alignedFrom = {
        sha256: refusedAlignSession.sourceAsset!.sha256,
        pose: { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 3 / 4 },
        from: { kind: 'brightest' as const, name: 'Fomalhaut' },
      };
      renderPhone(refusedAlignSession, { alignedFrom });
      const alignAbove = q('[data-testid="reveal-phone-estimate"]')!;
      expect(alignAbove.className).toBe('reveal-phone-estimate is-above-card');
      expect(alignAbove.querySelector('.reveal-phone-estimate__source')!.textContent).toBe(EARLY_SOURCE_WORDS.alignment);
      expect(q('.reveal-status.is-unmatched.is-phone.is-refused .look-tag')).toBeNull();
      expect(predictedTags()).toHaveLength(1);

      // 6. Calibrated phone photo: without predicted marks -> no block; with naked-eye predicted DSO -> tag-only block above Catalog matched StarCard
      const calBase = poseSession(sample('plate_calibrated'));
      const noPredCal = {
        ...calBase,
        observationSolution: { ...calBase.observationSolution!, findings: calBase.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted') },
      } as ObservationSession;
      const rigel = noPredCal.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
      renderPhone({ ...noPredCal, selectedFindingId: rigel.id });
      expect(q('[data-testid="star-card"] .reveal-card__top > .look-tag')!.getAttribute('data-evidence-class')).toBe('catalog_matched');
      expect(q('[data-testid="reveal-phone-estimate"]')).toBeNull();
      expect(predictedTags()).toHaveLength(0);

      const wcs = noPredCal.observationSolution!.wcs!;
      const dso = {
        ...POSE.findings.find((f) => f.evidenceClass === 'predicted')!,
        id: 'finding-openngc-ngc0224',
        title: 'M31',
        evidenceClass: 'predicted' as const,
        skyPosition: { raDeg: wcs.crvalRaDeg, decDeg: wcs.crvalDecDeg, altDeg: 45, azDeg: 180 },
        catalogRef: { catalogName: 'OpenNGC', catalogSourceId: 'NGC0224', objectType: 'deep_sky', apparentMagnitude: 3.44 },
      };
      const calWithDso = {
        ...noPredCal,
        selectedFindingId: rigel.id,
        observationSolution: { ...noPredCal.observationSolution!, findings: [...noPredCal.observationSolution!.findings, dso] },
      } as unknown as ObservationSession;
      renderPhone(calWithDso);
      await act(async () => {
        await Promise.resolve();
      });
      const calAbove = q('[data-testid="reveal-phone-estimate"]')!;
      expect(calAbove.className).toBe('reveal-phone-estimate is-above-card');
      expect([...calAbove.children].map((el) => el.className)).toEqual(['look-tag look-tag--predicted']);
      expect(predictedTags()).toHaveLength(1);
      // Leaving on calibrated + predicted DSO keeps PhoneCounts hidden while StarCard leaves so 1 Predicted tag stands
      renderPhone({ ...calWithDso, selectedFindingId: null });
      act(() => vi.advanceTimersByTime(MOTION_TOKENS.sheetMs - 1));
      expect(q('.reveal-phone-counts')).toBeNull();
      expect(predictedTags()).toHaveLength(1);
    } finally {
      fetchSpy.mockRestore();
      vi.useRealTimers();
    }
  });
});

// DECIDED-BY-LEAD 12:40 on 24 Nov (MOTION-legend-predtag-QUESTION.md): In this photo's Predicted tag stands at full from
// the predicted rings' first frame, at the reveal and after Back to Moment, normal and Reduce Motion, while the legend's
// words keep their words in. The legend mounts with the marks layer, so a tag with no entrance, under no ancestor with
// one, is at full from the rings' first frame.
describe("In this photo's Predicted tag stands with the rings it names", () => {
  /** Every animation declaration motion.css gives this element now: top level, and under Reduce Motion when asked. */
  function animationsOf(el: Element, reduced: boolean): Array<{ selector: string; decls: Record<string, string> }> {
    const found: Array<{ selector: string; decls: Record<string, string> }> = [];
    postcss.parse(motionCss).walkRules((rule) => {
      const parent = rule.parent;
      const atTop = parent?.type === 'root';
      const underReduced = parent?.type === 'atrule' && (parent as postcss.AtRule).name === 'media' && (parent as postcss.AtRule).params.includes('prefers-reduced-motion: reduce');
      if (!atTop && !(reduced && underReduced)) return;
      const hit = rule.selectors.some((selector) => {
        try {
          return el.matches(selector);
        } catch {
          // A selector jsdom cannot read never names the legend (checked below)
          expect(selector.includes('reveal-legend'), selector).toBe(false);
          return false;
        }
      });
      if (!hit) return;
      const decls: Record<string, string> = {};
      rule.walkDecls((d) => {
        if (d.prop.startsWith('animation')) decls[d.prop] = d.value.replace(/\s+/g, ' ');
      });
      if (Object.keys(decls).length > 0) found.push({ selector: rule.selector.replace(/\s+/g, ' '), decls });
    });
    return found;
  }
  const WORDS_IN_AFTER_MARKS =
    'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-span-marks) + var(--look-mo-delay-words-in) + var(--look-mo-stagger)) both';
  const legendParts = () => {
    const legend = q('.reveal-legend')!;
    const predictedTag = legend.querySelector('.reveal-legend__row > .look-tag[data-evidence-class="predicted"]')!;
    const measuredTag = legend.querySelector('.reveal-legend__row > .look-tag:not([data-evidence-class="predicted"])')!;
    return { legend, predictedTag, predictedRow: predictedTag.parentElement!, measuredTag, title: legend.querySelector('.reveal-legend__title')!, counts: [...legend.querySelectorAll('.reveal-legend__count')] };
  };

  for (const reduced of [false, true]) {
    it(`gives the Predicted tag and everything around it in the legend no entrance, its words their words in after the marks${reduced ? ', under Reduce Motion too' : ''}`, () => {
      show(poseSession(POSE));
      expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBeGreaterThan(0);
      const { legend, predictedTag, predictedRow, measuredTag, title, counts } = legendParts();
      expect(predictedTag).not.toBeNull();
      expect(measuredTag).not.toBeNull();
      // The tag itself, its row and the legend: nothing but "none"
      for (const el of [predictedTag, predictedRow, legend]) {
        for (const { selector, decls } of animationsOf(el, reduced)) expect(decls, selector).toEqual({ animation: 'none' });
      }
      expect(animationsOf(predictedTag, reduced).map((r) => r.decls)).toEqual([{ animation: 'none' }]);
      // The words, and a tag for what was measured, keep their words in after the marks' span
      for (const el of [title, ...counts, measuredTag]) {
        expect(animationsOf(el, reduced).map((r) => r.decls.animation), el.className).toContain(WORDS_IN_AFTER_MARKS);
      }
    });
  }

  for (const reduced of [false, true]) {
    it(`keeps the lock-on's later words in on the legend's words and none on its Predicted tag${reduced ? ', under Reduce Motion too' : ''}`, () => {
      show(poseSession(POSE));
      const { legend, predictedTag, predictedRow, title, counts } = legendParts();
      // The lock-on's class sits on the reveal's own root
      const photo = container.querySelector('.reveal-photo')!;
      photo.classList.add('is-locking');
      const delay = reduced
        ? 'calc(var(--look-mo-dur-dissolve) + var(--look-mo-delay-words-in) + var(--look-mo-stagger))'
        : 'calc(var(--look-mo-dur-frame-in) + var(--look-mo-dur-colour) + var(--look-mo-delay-words-in) + var(--look-mo-stagger))';
      for (const el of [title, ...counts]) {
        const rules = animationsOf(el, reduced);
        expect(rules.map((r) => r.decls['animation-delay']), el.className).toContain(delay);
        // The lock-on rule comes after the arrival rule, so its delay is the one that holds
        const last = rules.filter((r) => r.decls['animation-delay'] !== undefined || r.decls.animation !== undefined).at(-1)!;
        expect(last.decls['animation-delay'], el.className).toBe(delay);
      }
      for (const el of [predictedTag, predictedRow, legend]) {
        for (const { selector, decls } of animationsOf(el, reduced)) expect(decls, selector).toEqual({ animation: 'none' });
      }
      photo.classList.remove('is-locking');
    });
  }

  it("gives a Simulated row's tag no entrance either (simulated is in the legend's order)", () => {
    const session = poseSession(POSE);
    const predicted = session.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted')!;
    const withSimulated = {
      ...session,
      observationSolution: { ...session.observationSolution!, findings: [...session.observationSolution!.findings, { ...predicted, id: 'finding-simulated-one', evidenceClass: 'simulated' }] },
    } as ObservationSession;
    show(withSimulated);
    const tag = q('.reveal-legend .reveal-legend__row > .look-tag[data-evidence-class="simulated"]')!;
    expect(tag).not.toBeNull();
    for (const reduced of [false, true]) {
      expect(animationsOf(tag, reduced).map((r) => r.decls)).toEqual([{ animation: 'none' }]);
      for (const { selector, decls } of animationsOf(tag.parentElement!, reduced)) expect(decls, selector).toEqual({ animation: 'none' });
    }
  });

  it('mounts again at Back to Moment with the rings, its Predicted tag at full and its words in after the marks', () => {
    vi.useFakeTimers();
    try {
      const session = poseSession(POSE);
      show(session, { flight: { name: 'Rigel', toStarLy: 1, fromEarthLy: 859.4 } });
      act(() => vi.advanceTimersByTime(5000));
      expect(q('.reveal-legend')).toBeNull();
      show(session);
      expect(container.querySelectorAll('button.reveal-mark.is-predicted').length).toBeGreaterThan(0);
      const { predictedTag, predictedRow, legend, title } = legendParts();
      for (const el of [predictedTag, predictedRow, legend]) {
        for (const { selector, decls } of animationsOf(el, false)) expect(decls, selector).toEqual({ animation: 'none' });
      }
      expect(animationsOf(title, false).map((r) => r.decls.animation)).toContain(WORDS_IN_AFTER_MARKS);
    } finally {
      vi.useRealTimers();
    }
  });

  it('still leaves at Go there as recorded: the rows last first, then the title', () => {
    show(poseSession(POSE));
    const { legend, predictedRow, title } = legendParts();
    legend.classList.add('is-leaving');
    expect(animationsOf(predictedRow, false).map((r) => r.decls.animation).at(-1)).toContain('look-mo-words-out');
    expect(animationsOf(title, false).map((r) => r.decls.animation).at(-1)).toContain('look-mo-words-out');
  });
});
