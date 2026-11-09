import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import * as designSystem from '../index.js';
import { computeContrastRatio } from '../index.js';
import {
  HUD_CSS_CUSTOM_PROPERTIES,
  HUD_EVIDENCE_COLORS,
  HUD_FONTS,
  HUD_GLASS,
  HUD_HIGHLIGHT,
  HUD_INK,
  HUD_MOTION,
  HUD_REDUCED_MOTION_CSS_OVERRIDES,
  compositeOver,
  resolveHudMotion,
} from '../hudGlass.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const SURFACES = Object.entries(HUD_GLASS);
const greyHex = (level: number): string => `#${level.toString(16).padStart(2, '0').repeat(3)}`;
// A night photo's sky sits near black; a lit cloud or the Moon can reach white. Mid-grey stands for a bright sky.
const MID_GREY = '#808080';
const BLACK = '#000000';

describe('HUD black glass legibility', () => {
  it('keeps ink text at 4.5:1 or more on every glass over any grey backdrop, white included', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 255 }), (level) => {
        for (const [name, surface] of SURFACES) {
          const ratio = computeContrastRatio(HUD_INK, compositeOver(surface.fill, greyHex(level)));
          if (ratio < 4.5) throw new Error(`${name} over ${greyHex(level)}: ${ratio.toFixed(2)}:1`);
        }
      }),
      { seed: SEED, examples: [[255], [0], [128], [200]] }
    );
  });

  it('reaches 4.5:1 for caption ink over pure white only because the caption glass is 0.64 opaque', () => {
    // The equipped-test caption glass is 0.56 opaque. Over a white backdrop that gives 3.94:1, under AA.
    expect(HUD_GLASS.caption.fill[3]).toBe(0.64);
    const atReference = computeContrastRatio(HUD_INK, compositeOver([20, 22, 36, 0.56], '#ffffff'));
    expect(atReference).toBeLessThan(4.5);
    expect(computeContrastRatio(HUD_INK, compositeOver(HUD_GLASS.caption.fill, '#ffffff'))).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the highlight text at 4.5:1 or more on every glass over mid-grey and black', () => {
    for (const [name, surface] of SURFACES) {
      for (const backdrop of [MID_GREY, BLACK]) {
        const ratio = computeContrastRatio(HUD_HIGHLIGHT, compositeOver(surface.fill, backdrop));
        expect(ratio, `${name} over ${backdrop}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it('keeps every evidence colour at 3:1 or more as a glyph stroke on glass, and 4.5:1 against its black shadow', () => {
    for (const [evidenceClass, color] of Object.entries(HUD_EVIDENCE_COLORS)) {
      for (const [name, surface] of SURFACES) {
        for (const backdrop of [MID_GREY, BLACK]) {
          const ratio = computeContrastRatio(color, compositeOver(surface.fill, backdrop));
          expect(ratio, `${evidenceClass} on ${name} over ${backdrop}`).toBeGreaterThanOrEqual(3);
        }
      }
      expect(computeContrastRatio(color, BLACK), `${evidenceClass} against its shadow`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('gives each of the four evidence classes its own colour', () => {
    expect(Object.keys(HUD_EVIDENCE_COLORS).sort()).toEqual(
      ['catalog_matched', 'observed', 'predicted', 'simulated']
    );
    expect(new Set(Object.values(HUD_EVIDENCE_COLORS)).size).toBe(4);
  });

  it('composites a glass fill over a backdrop by its alpha', () => {
    expect(compositeOver([12, 14, 22, 1], '#ffffff')).toBe('#0c0e16');
    expect(compositeOver([12, 14, 22, 0], '#ffffff')).toBe('#ffffff');
    expect(compositeOver([0, 0, 0, 0.5], '#ffffff')).toBe('#808080');
  });
});

describe('HUD type', () => {
  it('sets Geist first with a system fallback that ends in a generic family', () => {
    expect(HUD_FONTS.sans.startsWith("'Geist',")).toBe(true);
    expect(HUD_FONTS.sans).toContain('-apple-system');
    expect(HUD_FONTS.sans.endsWith('sans-serif')).toBe(true);
    expect(HUD_FONTS.mono.startsWith("'Geist Mono',")).toBe(true);
    expect(HUD_FONTS.mono).toContain('ui-monospace');
    expect(HUD_FONTS.mono.endsWith('monospace')).toBe(true);
  });

  it('makes the package typography the same stacks', () => {
    expect(designSystem.ALBEDIAN_TYPOGRAPHY.fontSans).toBe(HUD_FONTS.sans);
    expect(designSystem.ALBEDIAN_TYPOGRAPHY.fontMono).toBe(HUD_FONTS.mono);
  });
});

describe('HUD motion', () => {
  it('keeps the reference easings and durations when motion is allowed', () => {
    expect(resolveHudMotion(false)).toEqual(HUD_MOTION);
    expect(HUD_MOTION.easeStandard).toBe('cubic-bezier(0.35, 0, 0, 1)');
    expect(HUD_MOTION.easeSpring).toBe('cubic-bezier(0.22, 1, 0.36, 1)');
    expect(HUD_MOTION.easeOutExpo).toBe('cubic-bezier(0.16, 1, 0.3, 1)');
  });

  it('sets every duration to 0 ms under reduced motion', () => {
    const reduced = resolveHudMotion(true);
    const durations = Object.entries(reduced).filter(([key]) => key.endsWith('Ms'));
    expect(durations.length).toBeGreaterThan(0);
    for (const [key, value] of durations) expect(value, key).toBe(0);
  });
});

describe('HUD CSS custom properties', () => {
  it('names every property --hud-* and gives it a value', () => {
    for (const [name, value] of Object.entries(HUD_CSS_CUSTOM_PROPERTIES)) {
      expect(name).toMatch(/^--hud-[a-z0-9-]+$/);
      expect(value.trim().length, name).toBeGreaterThan(0);
    }
  });

  it('writes each glass fill and filter from the token numbers', () => {
    const rgba = (fill: readonly number[]) => `rgba(${fill.join(', ')})`;
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-glass-fill']).toBe(rgba(HUD_GLASS.palette.fill));
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-chip-fill']).toBe(rgba(HUD_GLASS.chip.fill));
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-caption-fill']).toBe(rgba(HUD_GLASS.caption.fill));
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-glass-filter']).toBe('blur(40px) saturate(140%)');
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-font-sans']).toBe(HUD_FONTS.sans);
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-ev-predicted']).toBe(HUD_EVIDENCE_COLORS.predicted);
    expect(HUD_CSS_CUSTOM_PROPERTIES['--hud-dur-fast']).toBe(`${HUD_MOTION.fastMs}ms`);
  });

  it('overrides only durations under reduced motion, each to 0ms', () => {
    const overrides = Object.entries(HUD_REDUCED_MOTION_CSS_OVERRIDES);
    expect(overrides.length).toBeGreaterThan(0);
    for (const [name, value] of overrides) {
      expect(HUD_CSS_CUSTOM_PROPERTIES).toHaveProperty(name);
      expect(HUD_CSS_CUSTOM_PROPERTIES[name as keyof typeof HUD_CSS_CUSTOM_PROPERTIES]).toMatch(/^\d+ms$/);
      expect(value, name).toBe('0ms');
    }
    const durationNames = Object.keys(HUD_CSS_CUSTOM_PROPERTIES).filter((name) =>
      /ms$/.test(HUD_CSS_CUSTOM_PROPERTIES[name as keyof typeof HUD_CSS_CUSTOM_PROPERTIES])
    );
    expect(Object.keys(HUD_REDUCED_MOTION_CSS_OVERRIDES).sort()).toEqual(durationNames.sort());
  });

  it('is exported from the package entry', () => {
    expect(designSystem.HUD_GLASS).toBe(HUD_GLASS);
    expect(designSystem.HUD_CSS_CUSTOM_PROPERTIES).toBe(HUD_CSS_CUSTOM_PROPERTIES);
  });
});
