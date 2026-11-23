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
import { AlignLayer } from '../AlignLayer.js';
import { alignTargets, placedAtPose, type AlignPose } from '../alignPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { WorkingScreen } from '../WorkingScreen.js';
import type { Arrival } from '../arrival.js';

// MOTION-READY-web.md section six, DECIDED-BY-LEAD 22 Nov 23:36 (MOTION-handover-phone-NOTE.md): on the phone, whose
// working screen draws no rings, at the press of Solve from here every ring and name the aligning layer drew leaves where
// it stood on frame out, the Predicted tag standing at full until their last frame and the aligning words and button
// going on words out; at the refused end the kept rings come in as the reveal's marks do. Seen live at 390 by 844 on
// rp-b-06: all fifteen rings, the tag and the words went in one frame, and came back at full in one frame.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

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
      findings: [
        ...session.observationSolution!.findings.filter((f) => f.evidenceClass !== 'predicted'),
        target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16),
        target('finding-predicted-star-hip-102098', 'Deneb', 25, 175.8, 1.25),
        target('finding-predicted-star-hip-107315', 'Enif', 40, 145, 2.38),
        target('finding-predicted-star-hip-2', 'Two', 30, 160, 2.0),
      ],
    } as ObservationSolution,
  } as ObservationSession;
}
const KEPT: AlignPose = { azDeg: 150, altDeg: 30, rollDeg: 0, fovXDeg: 60, aspect: 4 / 3 };
// The photo's frame at 390 by 844 as the aligning layer is given it
const PHONE_RECT = { leftPx: 0, topPx: 100, widthPx: 390, heightPx: 520 };
const arrival = { fileName: 'night.jpg', byteSize: 10, stored: true, widthPx: 3024, heightPx: 4032, job: { id: 'job_0123456789abcdef', state: 'running', stages: [] }, failure: null } as Arrival;

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

const session = unmatched();
const targets = alignTargets(session.observationSolution);
const placed = placedAtPose(targets, KEPT);
const align = () => act(() => root.render(createElement(AlignLayer, { rect: PHONE_RECT, pose: KEPT, targets, onPose: () => {}, phone: true })));
const workPhone = (extra: Record<string, unknown>) =>
  act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: PHONE_RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, phone: true, ...extra })));
const all = (selector: string) => [...container.querySelectorAll(selector)];
// A ring's place on screen: its place in its layer plus the layer's own
const at = (el: Element) => {
  const layer = el.closest('.align-layer') as HTMLElement;
  return `${parseFloat(layer.style.left) + Number(el.getAttribute('cx'))},${parseFloat(layer.style.top) + Number(el.getAttribute('cy'))}`;
};
const endLeaving = () => {
  for (const ring of all('[data-testid="early-marks"] circle.is-leaving')) act(() => void ring.dispatchEvent(new Event('animationend', { bubbles: true })));
};

describe('the press of Solve from here on the phone', () => {
  it('lets every ring and name the aligning layer drew leave where it stood, under the tag at full and the words going', () => {
    align();
    const rings = all('[data-testid="align-layer"] circle').map(at);
    const names = all('[data-testid="align-layer"] .align-layer__name').map((n) => n.textContent);
    expect(rings.length).toBeGreaterThanOrEqual(2);
    // The phone's working screen may be given no frame of its own: the rings leave in the frame they stood in
    workPhone({ aligned: { marks: placed }, photoRect: null });
    expect(all('[data-testid="early-marks"] circle.is-leaving').map(at)).toEqual(rings);
    // in the very frame and 11 px phone class the aligning layer drew them in, not the working screen's or 13 px
    const layer = container.querySelector('[data-testid="early-marks"]') as HTMLElement;
    expect(layer.className).toBe('align-layer is-still is-phone');
    expect([layer.style.left, layer.style.top, layer.style.width, layer.style.height]).toEqual(['0px', '100px', '390px', '520px']);
    expect(all('[data-testid="early-marks"] circle:not(.is-leaving)')).toHaveLength(0);
    expect(all('[data-testid="early-marks"] .align-layer__name.is-leaving').map((n) => n.textContent)).toEqual(names);
    // The aligning block, leaving: its tag at full (no class or animation of its own), its words and button on words out
    const block = container.querySelector('[data-testid="align-phone-leaving"]')!;
    expect(block.classList.contains('is-leaving')).toBe(true);
    expect(block.getAttribute('aria-hidden')).toBe('true');
    expect(block.querySelector('.align-phone__tag')?.textContent).toContain('Predicted');
    expect(block.querySelector('.align-phone__lead')?.textContent).toBe('Drag the stars onto the dots you see.');
    expect(block.querySelector('.align-phone__solve')?.textContent).toContain('Solve from here');
    // The tag goes with the rings' last frame, and nothing of the aligning screen stays
    endLeaving();
    expect(container.querySelector('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(all('[data-testid="early-marks"] circle')).toHaveLength(0);
    expect(container.querySelector('[data-testid="working-screen"]')).not.toBeNull();
  });

  it('draws nothing leaving for a job that was not sent from the alignment', () => {
    align();
    workPhone({ early: { source: 'moon', marks: placed } });
    expect(container.querySelector('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(all('[data-testid="early-marks"] circle')).toHaveLength(0);
  });
});

describe('the refused end of Solve from here on the phone', () => {
  it('brings the kept rings in as the reveal marks come, brightest first, at the 11 px phone size, with the tag from the first', () => {
    align();
    const alignNameSides = all('[data-testid="align-layer"] .align-layer__name').map((n) => `${n.textContent}:${n.classList.contains('is-left')}`);
    expect(alignNameSides).toContain('Deneb:false');
    workPhone({ aligned: { marks: placed } });
    endLeaving();
    act(() =>
      root.render(
        createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onShowPredictedSky: () => {}, alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } } })
      )
    );
    const layer = container.querySelector('[data-testid="early-marks"]') as HTMLElement;
    expect(layer.className).toBe('align-layer is-still is-phone');
    const rings = all('[data-testid="early-marks"] circle');
    expect(rings.length).toBeGreaterThanOrEqual(2);
    expect(rings.every((ring) => ring.classList.contains('is-arriving'))).toBe(true);
    // Brightest first: the rings in the order the targets come (brightest first), each its own rank
    expect(rings.map((ring) => (ring as SVGElement).style.getPropertyValue('--mark-i'))).toEqual(rings.map((_, i) => String(i)));
    expect(layer.style.getPropertyValue('--mark-n')).toBe(String(rings.length));
    // Each name comes with its ring at the phone's 11 px size (Deneb stays right of its ring, inside 390 px, as aligned)
    const names = all('[data-testid="early-marks"] .align-layer__name');
    expect(names.map((n) => `${n.textContent}:${n.classList.contains('is-left')}`)).toEqual(alignNameSides);
    expect(names.map((n) => (n as HTMLElement).style.getPropertyValue('--mark-i'))).toEqual(names.map((_, i) => String(i)));
    for (const name of names) expect(name.classList.contains('is-arriving')).toBe(true);
    expect(container.querySelector('[data-testid="reveal-status"]')?.textContent).toContain('Predicted');
  });

  it('brings nothing in on the laptop, not even a ring the working column hid, which comes back as it was', () => {
    act(() => root.render(createElement(AlignLayer, { rect: PHONE_RECT, pose: KEPT, targets, onPose: () => {} })));
    // The working column laid over the first ring alone
    const first = { x: PHONE_RECT.leftPx + placed[0]!.u * PHONE_RECT.widthPx, y: PHONE_RECT.topPx + placed[0]!.v * PHONE_RECT.heightPx };
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.matches('[data-testid="work-early"]') ? { left: first.x - 20, top: first.y - 20, right: first.x + 20, bottom: first.y + 20 } : { left: 0, top: 0, right: 0, bottom: 0 };
      return { ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) } as DOMRect;
    });
    act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: PHONE_RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, aligned: { marks: placed } })));
    expect(all('[data-testid="early-marks"] circle:not(.is-leaving)')).toHaveLength(placed.length - 1);
    expect(all('[data-testid="early-marks"] .align-layer__name').map((n) => `${n.textContent}:${n.classList.contains('is-left')}`)).toContain('Deneb:true');
    spy.mockRestore();
    act(() =>
      root.render(
        createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900, onShowPredictedSky: () => {}, alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } } })
      )
    );
    expect((container.querySelector('[data-testid="early-marks"]') as HTMLElement).className).toBe('align-layer is-still');
    expect(all('[data-testid="early-marks"] .is-arriving')).toHaveLength(0);
  });
});

describe('the phone hand-over styles', () => {
  function rulesOf(selector: string): Record<string, string> {
    const found: Record<string, string> = {};
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    return found;
  }
  it('brings rings in on the reveal marks\' own arrival, and takes the aligning words out last first', () => {
    expect(rulesOf('.align-layer .is-arriving').animation).toBe('look-mo-frame-in var(--look-mo-dur-mark) linear calc(var(--mark-i) * var(--mark-step)) both');
    expect(rulesOf('.align-layer.is-still')['--mark-step']).toBe('max(0ms, calc((var(--look-mo-span-marks) - var(--look-mo-dur-mark)) / max(1, var(--mark-n) - 1)))');
    expect(rulesOf('.align-phone.is-leaving .align-phone__solve').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 0) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__help').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 1) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__lead').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 2) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__tag')).toEqual({});
  });
});
