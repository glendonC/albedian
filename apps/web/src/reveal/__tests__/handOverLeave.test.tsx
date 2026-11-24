// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CelestialFinding, ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { AlignLayer, leavingMarks } from '../AlignLayer.js';
import { alignTargets, placedAtPose, type AlignPose } from '../alignPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { WorkingScreen } from '../WorkingScreen.js';
import type { Arrival } from '../arrival.js';

// MOTION-READY-web.md section six, DECIDED-BY-LEAD 22 Nov 05:52 (MOTION-handover-NOTE.md): at both Solve from here
// hand-overs, the start and the refused end, a ring and its name that the next screen's keep-clear hides leave on frame
// out (300 ms; the 270 ms dissolve under reduced motion); everything else stays put. Seen live at 1024 by 768 on rp-b-06:
// Okab vanished in the working screen's first frame, Sarin and Zeta Herculis in the refused screen's.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');

const FRAME = { leftPx: 0, topPx: 0, widthPx: 1000, heightPx: 800 };

describe('leavingMarks', () => {
  const drawn = (id: string, xPx: number, yPx: number) => ({ id, xPx, yPx, name: id, nameOnLeft: false });
  const placed = [
    { id: 'still', name: 'still', u: 0.5, v: 0.5 },
    { id: 'hidden', name: 'hidden', u: 0.2, v: 0.25 },
    { id: 'moved', name: 'moved', u: 0.8, v: 0.8 },
  ];
  it('gives the marks drawn before, at the place this screen would draw them, that this screen does not draw', () => {
    const before = [drawn('still', 500, 400), drawn('hidden', 200, 200), drawn('moved', 100, 100)];
    const now = [drawn('still', 500, 400)];
    expect(leavingMarks(before, placed, FRAME, now)).toEqual([drawn('hidden', 200, 200)]);
  });
  it('gives none that was not drawn before, so a ring hidden on both screens is never drawn again', () => {
    expect(leavingMarks([drawn('still', 500, 400)], placed, FRAME, [drawn('still', 500, 400)])).toEqual([]);
  });
});

// The sky and the kept alignment of keptClear.test.tsx: three stars on a 3:4 photo
const base = sample('horizon_predicted_only');
const template = base.findings.find((f) => f.evidenceClass === 'predicted')!;
const target = (id: string, title: string, alt: number, az: number, mag: number) =>
  ({ ...template, id, title, skyPosition: { ...template.skyPosition!, altDeg: alt, azDeg: az }, catalogRef: { ...(template.catalogRef ?? {}), apparentMagnitude: mag, objectType: 'star' } }) as CelestialFinding;
const SKY = [
  target('finding-predicted-star-hip-113368', 'Fomalhaut', 20, 150, 1.16),
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
// The photo's frame at 1440 by 900, as the reveal and the working screen draw it
const RECT = { leftPx: 382.5, topPx: 0, widthPx: 675, heightPx: 900 };
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
  vi.restoreAllMocks();
});

const rect = (r: { left: number; top: number; right: number; bottom: number }) =>
  ({ ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) }) as DOMRect;
/** A column laid over one place on screen, measured as `selector`'s box; everything else measures nothing. */
const columnOver = (selector: string, x: number, y: number) =>
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return rect(this.matches(selector) ? { left: x - 20, top: y - 20, right: x + 20, bottom: y + 20 } : { left: 0, top: 0, right: 0, bottom: 0 });
  });
const still = () => container.querySelector('[data-testid="early-marks"]')!;
const ringsOf = (sel: string) => [...still().querySelectorAll(`circle${sel}`)].map((c) => Math.round(Number(c.getAttribute('cx')) + RECT.leftPx));
const namesOf = (sel: string) => [...still().querySelectorAll(`.align-layer__name${sel}`)].map((n) => n.textContent);

const session = unmatched();
const targets = alignTargets(session.observationSolution);
const placed = placedAtPose(targets, KEPT);
const at = (i: number) => ({ x: RECT.leftPx + placed[i]!.u * RECT.widthPx, y: RECT.topPx + placed[i]!.v * RECT.heightPx });
const work = (marks = placed) =>
  act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, aligned: { marks } })));

describe('the start of Solve from here: the aligning layer to the working screen', () => {
  it('lets a ring and its name the working column hides leave on frame out, the rest standing still, then draws it no more', () => {
    expect(placed.length).toBeGreaterThanOrEqual(2);
    act(() => root.render(createElement(AlignLayer, { rect: RECT, pose: KEPT, targets, onPose: () => {} })));
    const first = at(0);
    columnOver('[data-testid="work-early"]', first.x, first.y);
    work();
    expect(ringsOf('.is-leaving')).toEqual([Math.round(first.x)]);
    expect(namesOf('.is-leaving')).toEqual([placed[0]!.name]);
    expect(ringsOf(':not(.is-leaving)')).toEqual(placed.slice(1).map((_, i) => Math.round(at(i + 1).x)));
    // Gone once its frame out ends
    act(() => still().querySelector('circle.is-leaving')!.dispatchEvent(new Event('animationend', { bubbles: true })));
    expect(ringsOf('.is-leaving')).toEqual([]);
    expect(namesOf('.is-leaving')).toEqual([]);
    expect(ringsOf('')).toEqual(placed.slice(1).map((_, i) => Math.round(at(i + 1).x)));
  });

  it('is the Solve from here hand-over alone: early marks from another source go at once under the column, as before', () => {
    act(() => root.render(createElement(AlignLayer, { rect: RECT, pose: KEPT, targets, onPose: () => {} })));
    columnOver('[data-testid="work-early"]', at(0).x, at(0).y);
    act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, early: { source: 'moon', marks: placed } })));
    expect(container.querySelectorAll('[data-testid="early-marks"] .is-leaving')).toHaveLength(0);
    expect(ringsOf('')).toEqual(placed.slice(1).map((_, i) => Math.round(at(i + 1).x)));
  });
});

describe('the refused end of Solve from here: the working screen to the refused screen', () => {
  it('lets a ring and its name the status column hides leave on frame out, the rest standing still', () => {
    work();
    expect(ringsOf('')).toHaveLength(placed.length);
    const first = at(0);
    columnOver('[data-testid="reveal-status"]', first.x, first.y);
    act(() =>
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
      )
    );
    expect(ringsOf('.is-leaving')).toEqual([Math.round(first.x)]);
    expect(ringsOf(':not(.is-leaving)')).toEqual(placed.slice(1).map((_, i) => Math.round(at(i + 1).x)));
  });

  it('never draws again a ring the working screen already hid, when the status column hides it too', () => {
    const first = at(0);
    columnOver('[data-testid="work-early"]', first.x, first.y);
    work();
    expect(ringsOf('')).toHaveLength(placed.length - 1);
    columnOver('[data-testid="reveal-status"]', first.x, first.y);
    act(() =>
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
      )
    );
    expect(ringsOf('.is-leaving')).toEqual([]);
    expect(ringsOf('')).toEqual(placed.slice(1).map((_, i) => Math.round(at(i + 1).x)));
  });
});

describe('the leaving style', () => {
  it('is frame out on the record tokens, which reduced motion makes the 270 ms dissolve', () => {
    const found: string[] = [];
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.replace(/\s+/g, ' ') !== '.align-layer .is-leaving' || rule.parent?.type !== 'root') return;
      rule.walkDecls('animation', (d) => {
        found.push(d.value.replace(/\s+/g, ' '));
      });
    });
    expect(found).toEqual(['look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) both']);
  });
});
