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

  it('at phone width the aligning screen is unchanged until a record places the tag there', () => {
    act(() => root.render(createElement(RevealPhoto, { session: unmatched(), onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onShowPredictedSky: () => {} })));
    act(() => button('Line it up yourself')!.click());
    expect(q('.align-phone')).not.toBeNull();
    expect(q('.look-tag')).toBeNull();
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
