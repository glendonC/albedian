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
import { lockOnPlan } from '../lockOn.js';
import { EARLY_SOURCE_WORDS, earlyPoseOf, type EarlyPose } from '../earlyPose.js';
import { revealMarks, type RevealMark } from '../revealMarks.js';
import { LOCK_ON_ARRIVAL_MS, RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// The lock-on (D25 point 2 and 5; MOTION-READY-web.md section 6, decided by the lead 07:51): when the check passes,
// each early mark the solve places travels from its pose place to its solve place, all together, then its matched mark
// takes its place at rest; an early mark the solve does not place leaves. When the check is refused nothing moves and
// the early marks stay amber at the early pose under the no-match sentence.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const motionCss = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
const VIEW = { w: 1440, h: 900 };
const FRAME = { leftPx: 0, topPx: 45, widthPx: 1440, heightPx: 810 };

const finding = (id: string, evidenceClass: CelestialFinding['evidenceClass'], catalogSourceId: string | null) =>
  ({ id, evidenceClass, title: id, catalogRef: catalogSourceId === null ? undefined : { catalogSourceId } }) as unknown as CelestialFinding;
const mark = (f: CelestialFinding, xPx: number, yPx: number, over: Partial<RevealMark> = {}): RevealMark => ({
  finding: f, kind: f.evidenceClass === 'catalog_matched' ? 'catalog' : 'predicted', xPx, yPx, name: null, nameOnLeft: false, picked: false, uncertaintyPx: null, deepSky: null, ...over,
});
const EARLY: EarlyPose = {
  source: 'moon',
  marks: [
    { id: 'finding-predicted-star-betelgeuse', name: 'Betelgeuse', u: 0.25, v: 0.5, key: 'HIP 27989' },
    { id: 'finding-predicted-star-rigel', name: 'Rigel', u: 0.5, v: 0.75, key: 'HIP 24436' },
    { id: 'finding-predicted-star-sirius', name: 'Sirius', u: 0.9, v: 0.9, key: 'HIP 32349' },
  ],
};

describe('lockOnPlan', () => {
  const marks = [
    mark(finding('finding-star-betelgeuse', 'catalog_matched', 'HIP 27989'), 400, 500),
    mark(finding('finding-star-rigel', 'catalog_matched', 'HIP 24436'), 700, 600),
    mark(finding('finding-star-aldebaran', 'catalog_matched', 'HIP 21421'), 100, 100),
  ];
  it('sends each early mark whose catalog object the solve places from its pose place to that mark, and the rest away', () => {
    const plan = lockOnPlan(EARLY, FRAME, marks);
    expect(plan.travelling.map((t) => [t.id, t.fromXPx, t.fromYPx, t.toXPx, t.toYPx])).toEqual([
      ['finding-predicted-star-betelgeuse', 360, 450, 400, 500],
      ['finding-predicted-star-rigel', 720, 652.5, 700, 600],
    ]);
    expect(plan.leaving.map((l) => [l.id, l.xPx, l.yPx])).toEqual([['finding-predicted-star-sirius', 1296, 774]]);
    expect([...plan.lockedIds].sort()).toEqual(['finding-star-betelgeuse', 'finding-star-rigel']);
  });

  it('never sends a mark to a deep-sky object, and keeps names where the early drawing put them', () => {
    const deep = [mark(finding('finding-predicted-m42', 'predicted', 'HIP 27989'), 10, 10, { deepSky: 'nebula' as RevealMark['deepSky'] })];
    const plan = lockOnPlan(EARLY, FRAME, deep);
    expect(plan.travelling).toHaveLength(0);
    expect(plan.leaving).toHaveLength(3);
    expect(lockOnPlan(EARLY, FRAME, marks).travelling[0]!.name).toBe('Betelgeuse');
  });

  it('sends one early mark only to a matched mark, the second of the same object leaving', () => {
    const twice: EarlyPose = { source: 'moon', marks: [EARLY.marks[0]!, { ...EARLY.marks[0]!, id: 'again', u: 0.3 }] };
    const plan = lockOnPlan(twice, FRAME, marks);
    expect(plan.travelling.map((t) => t.id)).toEqual(['finding-predicted-star-betelgeuse']);
    expect(plan.leaving.map((l) => l.id)).toEqual(['again']);
  });

  it('places nothing outside the frame and nothing without a catalog object', () => {
    const outside: EarlyPose = { source: 'moon', marks: [{ id: 'a', name: 'A', u: 1.2, v: 0.5, key: 'HIP 27989' }, { id: 'b', name: 'B', u: 0.5, v: 0.5 }] };
    const plan = lockOnPlan(outside, FRAME, marks);
    expect(plan.travelling).toHaveLength(0);
    expect(plan.leaving.map((l) => l.id)).toEqual(['b']);
  });
});

describe('the reveal locking on', () => {
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
  const q = (s: string) => container.querySelector(s);
  const show = (session: ObservationSession, lockOnSky: ObservationSolution | null) =>
    act(() => root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, lockOnSky })));

  // The served early sky, on the calibrated sample's own upload, naming two of its matched stars
  const calibrated = poseSession(sample('plate_calibrated'));
  const sha = calibrated.sourceAsset!.sha256;
  const matched = sample('plate_calibrated').findings.filter((f) => f.evidenceClass === 'catalog_matched');
  const early = (): ObservationSolution => {
    const stars = POSE.findings.filter((f) => f.id.startsWith('finding-predicted-star-')).slice(0, 2);
    return { ...POSE, sourceSha256: sha, findings: POSE.findings.map((f) => { const i = stars.indexOf(f); return i < 0 ? f : { ...f, catalogRef: { ...f.catalogRef!, catalogSourceId: matched[i]!.catalogRef!.catalogSourceId } }; }) };
  };

  it('draws the early marks travelling to the matched marks, which wait for them, on the first arrival', () => {
    const sky = early();
    const plan = lockOnPlan(earlyPoseOf(sky, calibrated.sourceAsset)!, { leftPx: 0, topPx: 45, widthPx: 1440, heightPx: 810 }, revealMarks(calibrated, VIEW.w, VIEW.h));
    expect(plan.travelling.length).toBeGreaterThan(0);
    show(calibrated, sky);
    expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(true);
    const travelling = [...container.querySelectorAll('.lockon-mark.is-travelling')] as HTMLElement[];
    expect(travelling).toHaveLength(plan.travelling.length);
    const first = plan.travelling[0]!;
    expect(travelling[0]!.style.left).toBe(`${first.fromXPx}px`);
    expect(travelling[0]!.style.getPropertyValue('--lock-dx')).toBe(`${first.toXPx - first.fromXPx}px`);
    expect(travelling[0]!.style.getPropertyValue('--lock-dy')).toBe(`${first.toYPx - first.fromYPx}px`);
    expect(container.querySelectorAll('.lockon-mark.is-leaving')).toHaveLength(plan.leaving.length);
    for (const id of plan.lockedIds) expect(q(`[data-testid="reveal-mark-${id}"]`)!.classList.contains('is-locked-on')).toBe(true);
    // Drawn as the early marks were: amber dashed rings, no pointer
    expect(travelling[0]!.className).toContain('align-layer is-still');
  });

  it('never leaves the still amber early marks on a calibrated reveal, during the lock-on or after it (AUDIT-C, 13:30)', () => {
    vi.useFakeTimers();
    try {
      show(calibrated, early());
      expect(q('[data-testid="early-marks"]')).toBeNull();
      act(() => vi.advanceTimersByTime(LOCK_ON_ARRIVAL_MS + 10));
      expect(q('[data-testid="early-marks"]')).toBeNull();
      expect(q('.lockon-mark')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('lasts until the words are in, the travel, the colour and the words after them, then is the reveal as approved', () => {
    expect(LOCK_ON_ARRIVAL_MS).toBe(400 + 200 + 120 + 300 + 2 * 20);
    vi.useFakeTimers();
    try {
      show(calibrated, early());
      act(() => vi.advanceTimersByTime(LOCK_ON_ARRIVAL_MS - 1));
      expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(true);
      act(() => vi.advanceTimersByTime(1));
      expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(false);
      expect(q('.lockon-mark')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // AUDIT-C's tag sweep, break 1 (.lead/kiro/verdicts/tagsweep-21nov.md) by UIUX-lockon-READY.md item 1 ("no early
  // marks are ever drawn without a source line") and the 17:38 tag rule: the early source line stays at full in the
  // status column, with no entrance, from the reveal's first frame until the last lock-on mark has gone
  const fromLine = () => q('[data-testid="lockon-from"]');
  it('keeps the early source line at full from the first frame until the travelling marks have gone (400 + 200 ms)', () => {
    vi.useFakeTimers();
    try {
      show(calibrated, early());
      expect(container.querySelectorAll('.lockon-mark.is-travelling').length).toBeGreaterThan(0);
      expect(fromLine()!.textContent).toBe(EARLY_SOURCE_WORDS[earlyPoseOf(early(), calibrated.sourceAsset)!.source]);
      // Its own element, outside the status column whose words wait for the colour
      expect(fromLine()!.closest('[data-testid="reveal-status"]')).toBeNull();
      act(() => vi.advanceTimersByTime(400 + 200 - 1));
      expect(fromLine()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(fromLine()).toBeNull();
      // The lock-on itself lasts on until the words are in
      expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps it until the leaving marks have gone (300 ms) when no early mark travels', () => {
    vi.useFakeTimers();
    try {
      // The early sky as served, naming none of the matched stars: every early mark leaves
      show(calibrated, { ...POSE, sourceSha256: sha });
      expect(q('.lockon-mark.is-travelling')).toBeNull();
      expect(container.querySelectorAll('.lockon-mark.is-leaving').length).toBeGreaterThan(0);
      act(() => vi.advanceTimersByTime(300 - 1));
      expect(fromLine()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(fromLine()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps it through the 270 ms dissolve under reduced motion', () => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)', addEventListener: () => {}, removeEventListener: () => {} }));
    try {
      show(calibrated, early());
      act(() => vi.advanceTimersByTime(270 - 1));
      expect(fromLine()).not.toBeNull();
      act(() => vi.advanceTimersByTime(1));
      expect(fromLine()).toBeNull();
    } finally {
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('draws no source line without a lock-on: no served sky, or the phone', () => {
    show(calibrated, null);
    expect(fromLine()).toBeNull();
    act(() => root.render(createElement(RevealPhoto, { session: calibrated, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, lockOnSky: early() })));
    expect(fromLine()).toBeNull();
  });

  it('draws no lock-on at phone width, as the record draws none there', () => {
    act(() => root.render(createElement(RevealPhoto, { session: calibrated, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 390, viewportHeightPx: 844, phone: true, lockOnSky: early() })));
    expect(q('.lockon-mark')).toBeNull();
    expect(q('[data-testid="early-marks"]')).toBeNull();
  });

  it('draws no lock-on without a served sky, for another upload, or on a later view of the same reveal', () => {
    show(calibrated, null);
    expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(false);
    expect(q('.lockon-mark')).toBeNull();
    act(() => root.unmount());
    root = createRoot(container);
    show(calibrated, { ...early(), sourceSha256: 'f'.repeat(64) });
    expect(q('.lockon-mark')).toBeNull();
  });

  it('keeps the early marks still and amber at the early pose when the check is refused', () => {
    const refused = poseSession(sample('horizon_predicted_only'));
    const sky = { ...early(), sourceSha256: refused.sourceAsset!.sha256 };
    show(refused, sky);
    expect(q('.lockon-mark')).toBeNull();
    expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(false);
    const still = q('[data-testid="early-marks"]')!;
    expect(still.className).toBe('align-layer is-still');
    expect(still.querySelectorAll('circle').length).toBe(earlyPoseOf(sky, refused.sourceAsset)!.marks.filter((m) => m.u >= 0 && m.u <= 1 && m.v >= 0 && m.v <= 1).length);
  });

  // AUDIT-C's owed mutants (22 Nov 03:46), survivor 2: the alignment's rings are kept by the photo's sha256, the only
  // guard when another upload is opened (by its address, as the iPhone's Open writes it) while they are still held
  it("locks on from an alignment's rings only on the photo they were lined up on, never on another upload", () => {
    const keys = matched.slice(0, 2).map((f) => f.catalogRef!.catalogSourceId);
    const marks = [
      { id: 'finding-predicted-star-a', name: 'A', u: 0.3, v: 0.4, key: keys[0] },
      { id: 'finding-predicted-star-b', name: 'B', u: 0.6, v: 0.5, key: keys[1] },
    ];
    const render = (alignedSha: string) =>
      act(() => root.render(createElement(RevealPhoto, { session: calibrated, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: VIEW.w, viewportHeightPx: VIEW.h, lockOnSky: null, lockOnAligned: { sha256: alignedSha, marks } })));
    // Lined up on another photo: nothing of them is drawn here, and no lock-on
    render('f'.repeat(64));
    expect(q('.lockon-mark')).toBeNull();
    expect(fromLine()).toBeNull();
    expect(q('[data-testid="reveal-photo"]')!.classList.contains('is-locking')).toBe(false);
    // On this photo the same rings lock on, under their own source line
    act(() => root.unmount());
    root = createRoot(container);
    render(sha);
    expect(container.querySelectorAll('.lockon-mark.is-travelling').length).toBeGreaterThan(0);
    expect(fromLine()!.textContent).toBe(EARLY_SOURCE_WORDS.alignment);
  });
});

describe("the lock-on's motion is the record's", () => {
  function rulesOf(selector: string, inReduce = false): Record<string, string> {
    const found: Record<string, string> = {};
    let rules = 0;
    postcss.parse(motionCss).walkRules((rule) => {
      const reduce = rule.parent?.type === 'atrule' && (rule.parent as postcss.AtRule).params.includes('prefers-reduced-motion');
      if (rule.selector.replace(/\s+/g, ' ') !== selector || reduce !== inReduce || (!inReduce && rule.parent?.type !== 'root')) return;
      rules += 1;
      rule.walkDecls((decl) => {
        found[decl.prop] = decl.value.replace(/\s+/g, ' ');
      });
    });
    expect(rules, selector).toBe(1);
    return found;
  }
  it('gives the early source line no entrance and no exit of its own: it is at full while it is there', () => {
    expect(rulesOf('.lockon-from')).toEqual({ animation: 'none' });
  });

  it('travels in the frame-in time on its curve, then the matched mark comes in the colour time at rest', () => {
    expect(rulesOf('.lockon-mark.is-travelling').animation).toBe(
      'lockon-travel var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both, look-mo-frame-out var(--look-mo-dur-colour) var(--look-mo-ease-std) var(--look-mo-dur-frame-in) both'
    );
    expect(rulesOf('.lockon-mark.is-leaving').animation).toBe('look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) both');
    expect(rulesOf('.is-locking .reveal-marks .reveal-mark.is-locked-on').animation).toBe(
      'look-mo-frame-in var(--look-mo-dur-colour) var(--look-mo-ease-std) var(--look-mo-dur-frame-in) both'
    );
    expect(rulesOf('.is-locking[data-arriving] .reveal-status')['animation-delay']).toBe('calc(var(--look-mo-dur-frame-in) + var(--look-mo-dur-colour) + var(--look-mo-delay-words-in))');
  });
  it('travels nowhere under reduced motion: the early marks dissolve where they are and the matched ones where they are, together', () => {
    expect(rulesOf('.lockon-mark.is-travelling', true).animation).toBe('look-mo-frame-out var(--look-mo-dur-dissolve) var(--look-mo-ease-dissolve) both');
    expect(rulesOf('.is-locking .reveal-marks .reveal-mark.is-locked-on', true).animation).toBe('look-mo-frame-in var(--look-mo-dur-dissolve) var(--look-mo-ease-dissolve) both');
    expect(rulesOf('.is-locking[data-arriving] .reveal-status', true)['animation-delay']).toBe('calc(var(--look-mo-dur-dissolve) + var(--look-mo-delay-words-in))');
  });
  it('moves a mark only between its two real places: the travel ends on the solve place, with no keyframe between', () => {
    let frames: string[] = [];
    postcss.parse(motionCss).walkAtRules('keyframes', (at) => {
      if (at.params !== 'lockon-travel') return;
      frames = at.nodes!.map((n) => (n as postcss.Rule).selector);
      expect((at.nodes![0] as postcss.Rule).nodes!.map((d) => (d as postcss.Declaration).toString())).toEqual(['translate: var(--lock-dx) var(--lock-dy)']);
    });
    expect(frames).toEqual(['to']);
  });
});
