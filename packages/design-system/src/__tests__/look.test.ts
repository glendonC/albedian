import { describe, expect, it } from 'vitest';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import type { EvidenceClass } from '@albedian/contracts';
import * as designSystem from '../index.js';
import { compositeOver, computeContrastRatio } from '../index.js';
import {
  LOOK_CSS_CUSTOM_PROPERTIES,
  LOOK_EVIDENCE,
  LOOK_FIELD,
  LOOK_GLYPHS,
  LOOK_INK,
  LOOK_INK_LEVELS,
  LOOK_INNER_CIRCLE_INSET_PX,
  LOOK_MIN_TARGET_PX,
  LOOK_MOTION,
  LOOK_NIGHT,
  LOOK_REDUCED_MOTION_CSS_OVERRIDES,
  LOOK_SIZES,
  lookEvidenceDescription,
  meetsTargetSize,
  MOTION_TOKENS,
  resolveLookMotion,
  resolveMotionTokens,
} from '../look.js';

const CLASSES: EvidenceClass[] = ['observed', 'catalog_matched', 'predicted', 'simulated'];
const hexRgb = (hex: string) => [0, 1, 2].map((i) => parseInt(hex.replace('#', '').slice(i * 2, i * 2 + 2), 16));
const overBlack = (level: readonly [number, number, number, number]) => compositeOver(level, LOOK_FIELD);

describe('look tokens: legibility on black (WCAG 2.2: 4.5:1 for text, 3:1 for a control outline or focus ring)', () => {
  it('keeps the ink and the secondary ink at 4.5:1 or more as text', () => {
    expect(computeContrastRatio(LOOK_INK, LOOK_FIELD)).toBeGreaterThanOrEqual(4.5);
    expect(computeContrastRatio(overBlack(LOOK_INK_LEVELS.ink2), LOOK_FIELD)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps a control outline and the focus ring at 3:1 or more', () => {
    expect(computeContrastRatio(overBlack(LOOK_INK_LEVELS.line), LOOK_FIELD)).toBeGreaterThanOrEqual(3);
    expect(computeContrastRatio(LOOK_INK, LOOK_FIELD)).toBeGreaterThanOrEqual(3);
  });

  it('keeps the near-black words on a filled pill at 4.5:1 or more', () => {
    expect(computeContrastRatio(LOOK_NIGHT, LOOK_INK)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(CLASSES)('keeps the %s tag word at 4.5:1 or more over its own tint on black', (evidenceClass) => {
    const style = LOOK_EVIDENCE[evidenceClass];
    const [r, g, b] = hexRgb(style.colorHex) as [number, number, number];
    const tag = compositeOver([r, g, b, style.tintAlpha], LOOK_FIELD);
    expect(computeContrastRatio(style.colorHex, tag)).toBeGreaterThanOrEqual(4.5);
  });
});

describe('look tokens: every evidence class as a glyph plus a word', () => {
  it.each(CLASSES)('starts the evidence label of %s with its tag word, case aside', (evidenceClass) => {
    const label = getEpistemicBadgeDescriptor(evidenceClass).label.toLowerCase();
    expect(label.startsWith(LOOK_EVIDENCE[evidenceClass].word.toLowerCase())).toBe(true);
  });

  it('uses the approved words, colours and tints, the simulated tag as the approved reveal draws it', () => {
    expect(CLASSES.map((c) => [LOOK_EVIDENCE[c].word, LOOK_EVIDENCE[c].colorHex, LOOK_EVIDENCE[c].tintAlpha])).toEqual([
      ['Observed', '#f4f3ef', 0.1],
      ['Catalog matched', '#c9daff', 0.14],
      ['Predicted', '#f5b83d', 0.14],
      ['Simulated', '#a78bfa', 0.15],
    ]);
  });

  it('draws the four glyphs as the approved mockups do, each in its own box and stroke (lib/ab.mjs, G)', () => {
    const box22 = { viewBox: '-11 -11 22 22', strokeWidth: 1.3 };
    expect(LOOK_GLYPHS.observed).toEqual({ ...box22, parts: [{ kind: 'circle', r: 5.5 }, { kind: 'circle', r: 1.4, filled: true }] });
    expect(LOOK_GLYPHS.catalog_matched).toEqual({ ...box22, parts: [{ kind: 'circle', r: 5.5 }, { kind: 'path', d: 'M0 -10v4M0 6v4M-10 0h4M6 0h4' }] });
    expect(LOOK_GLYPHS.predicted).toEqual({ ...box22, parts: [{ kind: 'circle', r: 6, dash: '2.4 2.4' }] });
    expect(LOOK_GLYPHS.simulated).toEqual({
      viewBox: '0 0 12 12',
      strokeWidth: 1.1,
      parts: [{ kind: 'path', d: 'M1 3.8V1H3.8M8.2 1H11V3.8M11 8.2V11H8.2M3.8 11H1V8.2' }],
    });
  });

  it('gives the four classes four words, four colours and four glyphs', () => {
    expect(new Set(CLASSES.map((c) => LOOK_EVIDENCE[c].word)).size).toBe(4);
    expect(new Set(CLASSES.map((c) => LOOK_EVIDENCE[c].colorHex)).size).toBe(4);
    expect(new Set(CLASSES.map((c) => JSON.stringify(LOOK_GLYPHS[c]))).size).toBe(4);
  });

  it('describes each class in the evidence package words, label and disclosure', () => {
    for (const c of CLASSES) {
      const badge = getEpistemicBadgeDescriptor(c);
      expect(lookEvidenceDescription(c)).toBe(`${badge.label}: ${badge.disclosure}`);
    }
  });
});

describe('look tokens: sizes', () => {
  // One table for the two minimums: each at its edge, and one step under it
  const TARGET_EDGES: ReadonlyArray<[height: number, input: keyof typeof LOOK_MIN_TARGET_PX, meets: boolean]> = [
    [24, 'pointer', true],
    [23, 'pointer', false],
    [44, 'touch', true],
    [43, 'touch', false],
  ];
  it.each(TARGET_EDGES)('a %i px control for a %s meets the minimum: %s', (height, input, meets) => {
    expect(meetsTargetSize(height, input)).toBe(meets);
  });

  it('makes the laptop controls large enough for a pointer and the iPhone ones for a finger', () => {
    expect(meetsTargetSize(LOOK_SIZES.compact.heightPx, 'pointer')).toBe(true);
    expect(meetsTargetSize(LOOK_SIZES.home.heightPx, 'pointer')).toBe(true);
    expect(meetsTargetSize(LOOK_SIZES.phone.heightPx, 'touch')).toBe(true);
  });

  it('fits each icon inside the circle that holds it', () => {
    for (const size of Object.values(LOOK_SIZES)) {
      expect(size.iconPx).toBeLessThan(size.heightPx - LOOK_INNER_CIRCLE_INSET_PX);
    }
  });
});

describe('look tokens: motion', () => {
  it('keeps the approved spring, curve and press when motion is allowed', () => {
    expect(resolveLookMotion(false)).toEqual(LOOK_MOTION);
    expect(LOOK_MOTION.springMs).toBe(1200);
    expect(LOOK_MOTION.pressScale).toBe(0.97);
  });

  it('turns every duration to 0 under reduced motion, and a press still scales to 0.97, at once, as the approved file has it', () => {
    const reduced = resolveLookMotion(true);
    expect([reduced.springMs, reduced.colourMs, reduced.pressMs]).toEqual([0, 0, 0]);
    expect(reduced.pressScale).toBe(0.97);
    expect(reduced.spring).toBe(LOOK_MOTION.spring);
  });
});

describe('motion tokens: the MOTION record (decided by the lead, 2026-11-19; .lead/uiux/motion/MOTION-APPROVED.md)', () => {
  it('keeps every value of the record when motion is allowed', () => {
    expect(resolveMotionTokens(false)).toEqual(MOTION_TOKENS);
    expect(MOTION_TOKENS).toEqual({
      easeStandard: 'cubic-bezier(0.4, 0, 0.2, 1)',
      easeFrameIn: 'cubic-bezier(0.16, 1, 0.3, 1)',
      easeFrameOut: 'cubic-bezier(0.4, 0, 1, 1)',
      easeCamera: 'cubic-bezier(0.27, 1.07, 0.56, 0.99)',
      colourMs: 200,
      frameInMs: 400,
      wordsInMs: 300,
      wordsInDelayMs: 120,
      wordsOutMs: 100,
      frameOutMs: 300,
      staggerMs: 20,
      risePx: 6,
      viewMs: 400,
      cameraHalfMs: 750,
      markMs: 67,
      marksSpanMs: 330,
      dissolveMs: 270,
      easeSheet: 'cubic-bezier(0.37, 0.37, 0.08, 1.05)',
      sheetMs: 500,
      sheetTravelPct: 110,
      sheetFromOpacity: 1,
      lineFromOffset: 1,
      lineFromOpacity: 1,
      barMs: 150,
    });
  });

  it('leaves faster than it arrives: words out before words in, frame out before frame in', () => {
    expect(MOTION_TOKENS.wordsOutMs).toBeLessThan(MOTION_TOKENS.wordsInMs);
    expect(MOTION_TOKENS.frameOutMs).toBeLessThan(MOTION_TOKENS.frameInMs);
    expect(MOTION_TOKENS.markMs).toBeLessThan(MOTION_TOKENS.marksSpanMs);
  });

  it('under reduced motion nothing travels and every change is the same 270 ms ease-in-out dissolve', () => {
    const reduced = resolveMotionTokens(true);
    expect(reduced.risePx).toBe(0);
    expect(reduced.staggerMs).toBe(0);
    expect(reduced.wordsInDelayMs).toBe(0);
    expect(reduced.marksSpanMs).toBe(0);
    expect(reduced.cameraHalfMs).toBe(0);
    for (const ms of [reduced.frameInMs, reduced.wordsInMs, reduced.wordsOutMs, reduced.frameOutMs, reduced.viewMs, reduced.markMs]) {
      expect(ms).toBe(MOTION_TOKENS.dissolveMs);
    }
    for (const ease of [reduced.easeStandard, reduced.easeFrameIn, reduced.easeFrameOut, reduced.easeSheet]) expect(ease).toBe('ease-in-out');
    // the phone card and the leader line dissolve in place: no travel, no drawing
    expect([reduced.sheetMs, reduced.sheetTravelPct, reduced.sheetFromOpacity]).toEqual([MOTION_TOKENS.dissolveMs, 0, 0]);
    expect([reduced.lineFromOffset, reduced.lineFromOpacity]).toEqual([0, 0]);
    // a progress bar's width snaps to each report (boltz.com: motion-reduce:transition-none)
    expect(reduced.barMs).toBe(0);
    expect(reduced.colourMs).toBe(MOTION_TOKENS.colourMs);
  });

  it('writes them as --look-mo-* custom properties, and overrides them under reduced motion', () => {
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-mo-dur-frame-in']).toBe('400ms');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-mo-delay-words-in']).toBe('120ms');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-mo-rise']).toBe('6px');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-mo-ease-camera']).toBe('cubic-bezier(0.27, 1.07, 0.56, 0.99)');
    expect(LOOK_REDUCED_MOTION_CSS_OVERRIDES['--look-mo-rise']).toBe('0px');
    expect(LOOK_REDUCED_MOTION_CSS_OVERRIDES['--look-mo-dur-words-in']).toBe('270ms');
    expect(LOOK_REDUCED_MOTION_CSS_OVERRIDES['--look-mo-ease-frame-in']).toBe('ease-in-out');
  });
});

describe('look tokens: CSS custom properties', () => {
  it('names every property --look-* and gives it a value', () => {
    for (const [name, value] of Object.entries(LOOK_CSS_CUSTOM_PROPERTIES)) {
      expect(name).toMatch(/^--look-[a-z0-9-]+$/);
      expect(String(value).trim().length).toBeGreaterThan(0);
    }
  });

  it('writes each ink level and tag tint from the token numbers', () => {
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-line']).toBe('rgba(244, 243, 239, 0.88)');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-ev-catalog-tint']).toBe('rgba(201, 218, 255, 0.14)');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-ev-observed-tint']).toBe('rgba(244, 243, 239, 0.1)');
    expect(LOOK_CSS_CUSTOM_PROPERTIES['--look-ev-simulated-tint']).toBe('rgba(167, 139, 250, 0.15)');
  });

  it('overrides only the durations, the travel and the curves under reduced motion, never the press scale', () => {
    expect(LOOK_REDUCED_MOTION_CSS_OVERRIDES).toEqual({
      '--look-dur-spring': '0ms',
      '--look-dur-colour': '0ms',
      '--look-dur-press': '0ms',
      '--look-mo-ease-std': 'ease-in-out',
      '--look-mo-ease-frame-in': 'ease-in-out',
      '--look-mo-ease-frame-out': 'ease-in-out',
      '--look-mo-dur-frame-in': '270ms',
      '--look-mo-dur-words-in': '270ms',
      '--look-mo-delay-words-in': '0ms',
      '--look-mo-dur-words-out': '270ms',
      '--look-mo-dur-frame-out': '270ms',
      '--look-mo-stagger': '0ms',
      '--look-mo-rise': '0px',
      '--look-mo-dur-view': '270ms',
      '--look-mo-dur-camera-half': '0ms',
      '--look-mo-dur-mark': '270ms',
      '--look-mo-span-marks': '0ms',
      '--look-mo-ease-sheet': 'ease-in-out',
      '--look-mo-dur-sheet': '270ms',
      '--look-mo-sheet-travel': '0%',
      '--look-mo-sheet-from-opacity': '0',
      '--look-mo-line-from': '0',
      '--look-mo-line-from-opacity': '0',
      '--look-mo-dur-bar': '0ms',
    });
    for (const name of Object.keys(LOOK_REDUCED_MOTION_CSS_OVERRIDES)) {
      expect(name in LOOK_CSS_CUSTOM_PROPERTIES).toBe(true);
    }
  });

  it('is exported from the package entry', () => {
    expect(designSystem.LOOK_CSS_CUSTOM_PROPERTIES).toBe(LOOK_CSS_CUSTOM_PROPERTIES);
  });
});
