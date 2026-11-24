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
import { EARLY_SOURCE_WORDS } from '../earlyPose.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { WorkingScreen } from '../WorkingScreen.js';
import type { Arrival } from '../arrival.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// phone-solve-wait-1 (DECIDED-BY-LEAD 24 Nov 12:58, UIUX gap five; UIUX-align-READY.md beside D24 and
// UIUX-iphone-lockon-READY.md; MOTION-READY-web.md section six, the phone's wait, refused end and lock-on): at phone
// width the wait after Solve from here keeps the person's rings at their pose under their Predicted tag, with D24's
// source line, D2's one step row and Cancel below, laid out as D31 lays out the iPhone's wait; the lock-on then runs
// as on the laptop. Replaces the 22 Nov 23:36 phone hand-over (rings leaving, then arriving at the refused end).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const revealCss = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
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
// The photo's slot at 390 by 844 (y 118 to 638), as the aligning layer and the wait are given it
const PHONE_RECT = { leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 };
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

const session = unmatched();
const targets = alignTargets(session.observationSolution);
const placed = placedAtPose(targets, KEPT);
const align = () => act(() => root.render(createElement(AlignLayer, { rect: PHONE_RECT, pose: KEPT, targets, onPose: () => {}, phone: true })));
const workPhone = (extra: Record<string, unknown> = {}) =>
  act(() => root.render(createElement(WorkingScreen, { arrival, photoRect: PHONE_RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, phone: true, aligned: { marks: placed }, ...extra })));
const q = (selector: string) => container.querySelector(selector);
const all = (selector: string) => [...container.querySelectorAll(selector)];
const at = (el: Element) => {
  const layer = el.closest('.align-layer') as HTMLElement;
  return `${parseFloat(layer.style.left) + Number(el.getAttribute('cx'))},${parseFloat(layer.style.top) + Number(el.getAttribute('cy'))}`;
};
const sides = (selector: string) => all(selector).map((n) => `${n.textContent}:${n.classList.contains('is-left')}`);

describe("the phone's wait after Solve from here", () => {
  it('keeps every ring and name where the aligning layer drew them, still, in the 11 px phone layer', () => {
    align();
    const rings = all('[data-testid="align-layer"] circle').map(at);
    const names = sides('[data-testid="align-layer"] .align-layer__name');
    expect(rings.length).toBeGreaterThanOrEqual(2);
    workPhone();
    const layer = q('[data-testid="early-marks"]') as HTMLElement;
    expect(layer.className).toBe('align-layer is-still is-phone');
    expect([layer.style.left, layer.style.top, layer.style.width, layer.style.height]).toEqual(['0px', '118px', '390px', '520px']);
    expect(all('[data-testid="early-marks"] circle').map(at)).toEqual(rings);
    expect(sides('[data-testid="early-marks"] .align-layer__name')).toEqual(names);
    expect(all('[data-testid="early-marks"] .is-leaving, [data-testid="early-marks"] .is-arriving')).toHaveLength(0);
  });

  it("keeps them in the slot the aligning layer drew them in, though the wait is handed the whole screen's fit", () => {
    align();
    const rings = all('[data-testid="align-layer"] circle').map(at);
    // The app hands the phone's working screen the photo fitted to the whole screen (y 162 for a 3 by 4 photo), not the
    // slot it draws the photo in (y 118 to 638)
    workPhone({ photoRect: { leftPx: 0, topPx: 162, widthPx: 390, heightPx: 520 } });
    expect((q('[data-testid="early-marks"]') as HTMLElement).style.top).toBe('118px');
    expect(all('[data-testid="early-marks"] circle').map(at)).toEqual(rings);
  });

  it('stands the Predicted tag where the aligning tag stood, with the source line, the step row and Cancel, and no new word', () => {
    align();
    workPhone();
    const block = q('[data-testid="work-early"]')!;
    expect(block.className).toBe('work-phone-early');
    expect(block.querySelector('.work-phone-early__tag [data-evidence-class="predicted"]')).not.toBeNull();
    expect(block.querySelector('.work-phone-early__from')!.textContent).toBe(EARLY_SOURCE_WORDS.alignment);
    expect(block.querySelector('.work-phone-row')).not.toBeNull();
    expect([...block.querySelectorAll('button')].map((b) => b.textContent)).toContain('Cancel');
    // One Predicted tag on the wait, and the laptop's lead line is not the phone's
    expect(all('[data-testid="working-screen"] .look-tag[data-evidence-class="predicted"]')).toHaveLength(1);
    expect(container.textContent).not.toContain('Matching near your alignment.');
  });

  it('takes the aligning words and Solve from here out last first with no second tag, then draws them no more', () => {
    align();
    workPhone();
    const leaving = q('[data-testid="align-phone-leaving"]')!;
    expect(leaving.classList.contains('is-leaving')).toBe(true);
    expect(leaving.getAttribute('aria-hidden')).toBe('true');
    expect(leaving.querySelector('.align-phone__tag')).toBeNull();
    expect(leaving.querySelector('.align-phone__lead')?.textContent).toBe('Drag the stars onto the dots you see.');
    expect(leaving.querySelector('.align-phone__solve')?.textContent).toContain('Solve from here');
    const rings = all('[data-testid="early-marks"] circle').length;
    // The sentence goes last (words out, 40 ms after Solve from here): when it has gone, the block is drawn no more
    act(() => void leaving.querySelector('.align-phone__lead')!.dispatchEvent(new Event('animationend', { bubbles: true })));
    expect(q('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(all('[data-testid="early-marks"] circle')).toHaveLength(rings);
    expect(q('[data-testid="work-early"] [data-evidence-class="predicted"]')).not.toBeNull();
  });

  it("lets a ring its own block hides leave where it stood, as at the laptop's hand-over", () => {
    align();
    const first = { x: PHONE_RECT.leftPx + placed[0]!.u * PHONE_RECT.widthPx, y: PHONE_RECT.topPx + placed[0]!.v * PHONE_RECT.heightPx };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.closest('[data-testid="work-early"]') && this.matches('.work-phone-early__from') ? { left: first.x - 20, top: first.y - 20, right: first.x + 20, bottom: first.y + 20 } : { left: 0, top: 0, right: 0, bottom: 0 };
      return { ...r, x: r.left, y: r.top, width: r.right - r.left, height: r.bottom - r.top, toJSON: () => ({}) } as DOMRect;
    });
    workPhone();
    expect(all('[data-testid="early-marks"] circle.is-leaving')).toHaveLength(1);
    expect(all('[data-testid="early-marks"] circle:not(.is-leaving)')).toHaveLength(placed.length - 1);
  });

  it('draws no leaving aligning words when no screen drew rings before it (the page opened again during the job)', async () => {
    // A fresh module: nothing has been drawn yet in this page
    vi.resetModules();
    const fresh = await import('../WorkingScreen.js');
    act(() => root.render(createElement(fresh.WorkingScreen, { arrival, photoRect: PHONE_RECT, onTryAgain: () => {}, onUploadAnother: () => {}, onCancel: () => {}, phone: true, aligned: { marks: placed } })));
    expect(q('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(q('[data-testid="work-early"] .look-tag[data-evidence-class="predicted"]')).not.toBeNull();
  });

  it('draws no rings, tag or source line for a phone job not sent from the alignment', () => {
    align();
    workPhone({ aligned: null, early: { source: 'moon', marks: placed } });
    expect(q('[data-testid="work-early"]')).toBeNull();
    expect(q('[data-testid="align-phone-leaving"]')).toBeNull();
    expect(all('[data-testid="early-marks"] circle')).toHaveLength(0);
  });
});

describe("the phone's refused end after the wait", () => {
  it('keeps the rings still into the refused screen at the 11 px phone size, none arriving, the tag from the first frame', () => {
    align();
    workPhone();
    const waited = all('[data-testid="early-marks"] circle').map(at);
    act(() =>
      root.render(
        createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, onShowPredictedSky: () => {}, alignedFrom: { sha256: session.sourceAsset!.sha256, pose: KEPT, from: { kind: 'brightest', name: 'Fomalhaut' } } })
      )
    );
    const layer = q('[data-testid="early-marks"]') as HTMLElement;
    expect(layer.className).toBe('align-layer is-still is-phone');
    expect(all('[data-testid="early-marks"] circle').length).toBeGreaterThanOrEqual(2);
    expect(all('[data-testid="early-marks"] .is-arriving')).toHaveLength(0);
    // Where the wait drew them, not where the refused screen's own fit of the photo would (y 162 for 3 by 4)
    expect(all('[data-testid="early-marks"] circle').map(at)).toEqual(waited);
    expect(q('[data-testid="reveal-status"]')?.textContent).toContain('Predicted');
  });
});

describe("the phone's lock-on after the wait", () => {
  const calibrated = poseSession(sample('plate_calibrated'));
  const sha = calibrated.sourceAsset!.sha256;
  const matched = sample('plate_calibrated').findings.filter((f) => f.evidenceClass === 'catalog_matched');
  const keys = matched.slice(0, 2).map((f) => f.catalogRef!.catalogSourceId);
  const marks = [
    { id: 'finding-predicted-star-a', name: 'A', u: 0.3, v: 0.4, key: keys[0] },
    { id: 'finding-predicted-star-b', name: 'B', u: 0.6, v: 0.5, key: keys[1] },
  ];
  const show = (phone: boolean) =>
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session: calibrated, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: phone ? 390 : 1440, viewportHeightPx: phone ? 844 : 900, phone, lockOnSky: null, lockOnAligned: { sha256: sha, marks },
        })
      )
    );

  it('locks on from the alignment rings at phone size, under the Predicted tag and the source line in their wait places', () => {
    show(true);
    expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(true);
    const travelling = all('.lockon-mark.is-travelling');
    expect(travelling.length).toBeGreaterThan(0);
    for (const mark of travelling) expect(mark.classList.contains('is-phone')).toBe(true);
    const from = q('[data-testid="lockon-from"]')!;
    expect(from.className).toBe('lockon-from is-phone');
    expect(from.querySelector('.work-phone-early__tag [data-evidence-class="predicted"]')).not.toBeNull();
    expect(from.querySelector('.work-phone-early__from')!.textContent).toBe(EARLY_SOURCE_WORDS.alignment);
  });

  it("keeps each name on the side the wait's 11 px drawing put it, not the laptop's 13 px", () => {
    // 'Alphardium' at 79 percent across a 390 px frame fits right of its ring at 11 px (to 382 px) but not at 13 px
    const near = [{ id: 'finding-predicted-star-a', name: 'Alphardium', u: 0.79, v: 0.4, key: keys[0] }, marks[1]!];
    act(() =>
      root.render(
        createElement(RevealPhoto, { session: calibrated, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, lockOnSky: null, lockOnAligned: { sha256: sha, marks: near } })
      )
    );
    const name = [...container.querySelectorAll('.lockon-mark .align-layer__name')].find((n) => n.textContent === 'Alphardium')!;
    expect(name.classList.contains('is-left')).toBe(false);
  });

  it('keeps the laptop lock-on as built: the source line alone at the status place', () => {
    show(false);
    const from = q('[data-testid="lockon-from"]')!;
    expect(from.className).toBe('lockon-from');
    expect(from.querySelector('[data-evidence-class="predicted"]')).toBeNull();
    for (const mark of all('.lockon-mark')) expect(mark.classList.contains('is-phone')).toBe(false);
  });
});

describe("the phone wait's rules", () => {
  function declarationsOf(css: string, selector: string): Record<string, string> {
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
  function reducedDeclarationsOf(selector: string): Record<string, string> {
    const found: Record<string, string> = {};
    postcss.parse(motionCss).walkAtRules('media', (media) => {
      if (!media.params.includes('prefers-reduced-motion: reduce')) return;
      media.walkRules((rule) => {
        if (rule.selector.replace(/\s+/g, ' ') !== selector) return;
        rule.walkDecls((decl) => {
          found[decl.prop] = decl.value.replace(/\s+/g, ' ');
        });
      });
    });
    return found;
  }

  it("puts the tag in the aligning tag's place and the source line 14 px under the step row's line, in D31's type", () => {
    expect(declarationsOf(revealCss, '.work-phone-early__tag')).toEqual(declarationsOf(revealCss, '.align-phone__tag'));
    expect(declarationsOf(revealCss, '.work-phone-early__tag')).toEqual({ position: 'absolute', left: '24px', top: '614px', display: 'flex' });
    expect(declarationsOf(revealCss, '.work-phone-early')).toEqual({ position: 'absolute', inset: '0', 'pointer-events': 'none' });
    const from = declarationsOf(revealCss, '.work-phone-early__from');
    expect(from).toEqual({ position: 'absolute', left: '24px', right: '24px', top: '724px', margin: '0', 'font-size': '12px', 'line-height': '1.4', color: 'var(--look-ink-3)' });
    // The step row's line (its bar): the row's top, its word's 28 px line, the 12 px row gap, the 2 px bar; then 14 px
    const row = declarationsOf(revealCss, '.work-phone-row');
    const word = declarationsOf(revealCss, '.work-phone-row > b');
    const bar = declarationsOf(revealCss, '.work-phone-row__bar');
    const barFoot = parseFloat(row.top!) + parseFloat(/(\d+)px \/ 1 /.exec(word.font!)![1]!) + parseFloat(row['row-gap']!) + parseFloat(bar.height!);
    expect(parseFloat(from.top!) - barFoot).toBe(14);
  });

  it('stands the lock-on source block over the whole screen on the phone, so its tag and line keep their wait places', () => {
    expect(declarationsOf(revealCss, '.lockon-from.is-phone')).toEqual({ left: '0', top: '0', right: '0', bottom: '0', width: 'auto' });
  });

  it("has the wait's block and the phone lock-on block no entrance, and words the phone counts in on the lock-on's 720 row", () => {
    let waitRules = 0;
    postcss.parse(motionCss).walkRules((rule) => {
      if (rule.selector.includes('work-phone-early')) waitRules += 1;
    });
    expect(waitRules).toBe(0);
    const counts = '.is-locking .reveal-phone-counts b, .is-locking .reveal-phone-counts .look-tag:not([data-evidence-class="predicted"], [data-evidence-class="simulated"])';
    expect(declarationsOf(motionCss, counts)).toEqual({
      animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-dur-frame-in) + var(--look-mo-dur-colour) + var(--look-mo-delay-words-in) + var(--look-mo-stagger)) both',
    });
    expect(reducedDeclarationsOf(counts)).toEqual({ 'animation-delay': 'calc(var(--look-mo-dur-dissolve) + var(--look-mo-delay-words-in) + var(--look-mo-stagger))' });
  });
});
