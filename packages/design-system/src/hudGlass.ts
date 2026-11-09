import type { EvidenceClass } from '@albedian/contracts';

/**
 * HUD black glass tokens, one source for TypeScript and for the CSS custom properties in
 * apps/web/src/styles/hud-tokens.css (a test keeps the two identical).
 *
 * The glass, type and motion values come from the owner's equipped-test house style, read at its source
 * (design-system.md, motion.md, tokens.css, classroom-shell.css), as used in the approved HUD mockups.
 * One change from that source: the caption glass is 0.64 opaque instead of 0.56, so white caption text
 * keeps 4.5:1 over a white part of a photo such as a bright Moon (0.56 gives 3.94:1).
 */

/** A glass fill as straight sRGB 0 to 255 and an alpha from 0 to 1. */
export type HudRgba = readonly [r: number, g: number, b: number, a: number];

export interface HudGlassSurface {
  fill: HudRgba;
  blurPx: number;
  saturatePct: number;
  radiusPx: number;
}

export const HUD_RADII = {
  pill: 100,
  palette: 12,
  item: 8,
  key: 5,
} as const;

export const HUD_GLASS = {
  /** Panels, cards, the ask palette and the receipt. */
  palette: { fill: [12, 14, 22, 0.72], blurPx: 40, saturatePct: 140, radiusPx: HUD_RADII.palette },
  /** Label chips on the photo. */
  chip: { fill: [12, 14, 22, 0.6], blurPx: 10, saturatePct: 140, radiusPx: HUD_RADII.key },
  /** Spoken captions. */
  caption: { fill: [20, 22, 36, 0.64], blurPx: 14, saturatePct: 140, radiusPx: HUD_RADII.key },
} as const satisfies Record<string, HudGlassSurface>;

export const HUD_EDGES = {
  hairline: 'inset 0 0 0 1px rgba(255, 255, 255, 0.08)',
  captionHairline: 'inset 0 0 0 1px rgba(255, 255, 255, 0.09)',
  specular: 'inset 0 1px 0 rgba(255, 255, 255, 0.12)',
  shadow: '0 24px 60px rgba(0, 0, 0, 0.35)',
  /** CSS twin of the mockups' SVG feDropShadow (stdDeviation 1.2, so a 2.4px blur radius), under every glyph. */
  glyphShadow: 'drop-shadow(0 0 2.4px rgba(0, 0, 0, 0.95))',
} as const;

export const HUD_INK = '#f8fafc';
export const HUD_HIGHLIGHT = '#caddff';

/** Stroke colour of each evidence glyph. The glyph shape carries the class too, so colour is never the only cue. */
export const HUD_EVIDENCE_COLORS: Readonly<Record<EvidenceClass, string>> = {
  observed: '#f8fafc',
  catalog_matched: '#caddff',
  predicted: '#fbbf24',
  simulated: '#a78bfa',
};

/** Geist first; with no network the page paints in the system stack. */
export const HUD_FONTS = {
  sans: "'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  mono: "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
} as const;

export interface HudMotion {
  easeStandard: string;
  easeSpring: string;
  easeOutExpo: string;
  fastMs: number;
  baseMs: number;
  enterMs: number;
  captionWordMs: number;
  captionStaggerMs: number;
}

export const HUD_MOTION: Readonly<HudMotion> = {
  easeStandard: 'cubic-bezier(0.35, 0, 0, 1)',
  easeSpring: 'cubic-bezier(0.22, 1, 0.36, 1)',
  easeOutExpo: 'cubic-bezier(0.16, 1, 0.3, 1)',
  fastMs: 180,
  baseMs: 320,
  enterMs: 420,
  captionWordMs: 480,
  captionStaggerMs: 45,
};

/** Under `prefers-reduced-motion: reduce` every HUD duration is 0 ms; the easings stay so nothing else changes. */
export function resolveHudMotion(reducedMotion: boolean): HudMotion {
  if (!reducedMotion) return { ...HUD_MOTION };
  return {
    ...HUD_MOTION,
    fastMs: 0,
    baseMs: 0,
    enterMs: 0,
    captionWordMs: 0,
    captionStaggerMs: 0,
  };
}

const rgba = (fill: HudRgba) => `rgba(${fill.join(', ')})`;
const glassFilter = (surface: HudGlassSurface) => `blur(${surface.blurPx}px) saturate(${surface.saturatePct}%)`;
const ms = (value: number) => `${value}ms`;

function motionProperties(motion: HudMotion) {
  return {
    '--hud-dur-fast': ms(motion.fastMs),
    '--hud-dur-base': ms(motion.baseMs),
    '--hud-dur-enter': ms(motion.enterMs),
    '--hud-dur-caption-word': ms(motion.captionWordMs),
    '--hud-caption-stagger': ms(motion.captionStaggerMs),
  } as const;
}

/** The `:root` custom properties of apps/web/src/styles/hud-tokens.css. */
export const HUD_CSS_CUSTOM_PROPERTIES = {
  '--hud-font-sans': HUD_FONTS.sans,
  '--hud-font-mono': HUD_FONTS.mono,
  '--hud-ink': HUD_INK,
  '--hud-highlight': HUD_HIGHLIGHT,
  '--hud-glass-fill': rgba(HUD_GLASS.palette.fill),
  '--hud-glass-filter': glassFilter(HUD_GLASS.palette),
  '--hud-chip-fill': rgba(HUD_GLASS.chip.fill),
  '--hud-chip-filter': glassFilter(HUD_GLASS.chip),
  '--hud-caption-fill': rgba(HUD_GLASS.caption.fill),
  '--hud-caption-filter': glassFilter(HUD_GLASS.caption),
  '--hud-hairline': HUD_EDGES.hairline,
  '--hud-caption-hairline': HUD_EDGES.captionHairline,
  '--hud-specular': HUD_EDGES.specular,
  '--hud-shadow': HUD_EDGES.shadow,
  '--hud-glyph-shadow': HUD_EDGES.glyphShadow,
  '--hud-radius-pill': `${HUD_RADII.pill}px`,
  '--hud-radius-palette': `${HUD_RADII.palette}px`,
  '--hud-radius-item': `${HUD_RADII.item}px`,
  '--hud-radius-key': `${HUD_RADII.key}px`,
  '--hud-ev-observed': HUD_EVIDENCE_COLORS.observed,
  '--hud-ev-catalog': HUD_EVIDENCE_COLORS.catalog_matched,
  '--hud-ev-predicted': HUD_EVIDENCE_COLORS.predicted,
  '--hud-ev-simulated': HUD_EVIDENCE_COLORS.simulated,
  '--hud-ease-standard': HUD_MOTION.easeStandard,
  '--hud-ease-spring': HUD_MOTION.easeSpring,
  '--hud-ease-out-expo': HUD_MOTION.easeOutExpo,
  ...motionProperties(HUD_MOTION),
} as const;

/** The custom properties hud-tokens.css overrides under `prefers-reduced-motion: reduce`. */
export const HUD_REDUCED_MOTION_CSS_OVERRIDES = motionProperties(resolveHudMotion(true));

/** The opaque colour a glass fill shows over a `#rrggbb` backdrop, rounded to `#rrggbb`. */
export function compositeOver(fill: HudRgba, backdropHex: string): string {
  const clean = backdropHex.replace('#', '');
  const alpha = fill[3];
  return `#${[0, 1, 2]
    .map((i) => {
      const under = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
      const over = fill[i] ?? 0;
      return Math.round(alpha * over + (1 - alpha) * under)
        .toString(16)
        .padStart(2, '0');
    })
    .join('')}`;
}
