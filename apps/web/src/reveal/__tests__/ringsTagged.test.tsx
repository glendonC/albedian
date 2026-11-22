// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample, POSE } from '../../components/__tests__/poseSessions.js';
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
    // No entrance: the block is not a reveal status column, and nothing in the motion record animates it
    expect(motionCss.includes('align-phone')).toBe(false);
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
});
