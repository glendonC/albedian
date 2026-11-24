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

// MOTION-READY-web.md section six. The phone's hand-over of 22 Nov 23:36 (the rings leaving at the press, coming back at
// the refused end) is REPLACED by DECIDED-BY-LEAD 24 Nov 12:58: the phone's wait keeps the rings, tested in
// phoneSolveWait.test.tsx (phone-solve-wait-1). What stays here: a phone job not sent from the alignment draws nothing
// of it, the laptop's refused end brings nothing in, and the aligning block's words still go last first.

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

describe('the press of Solve from here on the phone', () => {
  it('draws nothing leaving for a job that was not sent from the alignment', () => {
    align();
    workPhone({ early: { source: 'moon', marks: placed } });
    expect(container.querySelector('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(all('[data-testid="early-marks"] circle')).toHaveLength(0);
  });
});

describe('the refused end of Solve from here on the laptop', () => {
  it('brings nothing in, not even a ring the working column hid, which comes back as it was', () => {
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
  it('takes the aligning words out last first, and gives the still rings no arrival of their own', () => {
    expect(rulesOf('.align-phone.is-leaving .align-phone__solve').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 0) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__help').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 1) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__lead').animation).toBe('look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) calc(var(--look-mo-stagger) * 2) both');
    expect(rulesOf('.align-phone.is-leaving .align-phone__tag')).toEqual({});
    // The 23:36 refused-end arrival is gone with the phone's wait that keeps the rings (24 Nov 12:58)
    expect(rulesOf('.align-layer .is-arriving')).toEqual({});
    expect(rulesOf('.align-layer.is-still')).toEqual({});
  });
});
