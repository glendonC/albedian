import type { EvidenceClass } from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';
import type { HudRgba } from './hudGlass.js';

/**
 * The controls the owner approved on 2026-11-12 (main action, small main action, second action, view pills, icon
 * button, evidence tag, logo), one source for TypeScript and for the CSS custom properties in
 * apps/web/src/styles/look-tokens.css (a test keeps the two identical).
 *
 * Values read from the approved stylesheet (the UIUX lane's lib/albedian-ui.css, after boltz.com's live code with
 * the colours turned for the night) and the logo as apps/web draws it. The simulated tag (its colour, tint and corner
 * glyph) is the one the approved reveal draws (the UIUX lane's lib/ab.mjs, EVIDENCE_CSS and G.sim).
 */

const INK_RGB = [244, 243, 239] as const;

/** Ink on black: the text, the outlines and the filled pill. Each level is the ink at an alpha over black. */
export const LOOK_INK = '#f4f3ef';
/** The near-black text on a filled pill and the dark circle inside it. */
export const LOOK_NIGHT = '#0b0b0b';
/** The page behind every control. */
export const LOOK_FIELD = '#000000';

export const LOOK_INK_LEVELS = {
  /** Secondary text. */
  ink2: [...INK_RGB, 0.66],
  /** Quiet labels, never body text. */
  ink3: [...INK_RGB, 0.44],
  /** Hairlines between sections. */
  hair: [...INK_RGB, 0.14],
  /** A control's 1 px outline. */
  line: [...INK_RGB, 0.88],
  /** An outlined control's hover and pressed fill. */
  wash: [...INK_RGB, 0.1],
} as const satisfies Record<string, HudRgba>;

export interface LookEvidenceStyle {
  /** The word on the tag; the evidence package's label starts with it, case aside. */
  word: string;
  /** The glyph's stroke and the word's colour. */
  colorHex: string;
  /** The tag's tint: the colour at this alpha. */
  tintAlpha: number;
}

export const LOOK_EVIDENCE: Readonly<Record<EvidenceClass, LookEvidenceStyle>> = {
  observed: { word: 'Observed', colorHex: '#f4f3ef', tintAlpha: 0.1 },
  catalog_matched: { word: 'Catalog matched', colorHex: '#c9daff', tintAlpha: 0.14 },
  predicted: { word: 'Predicted', colorHex: '#f5b83d', tintAlpha: 0.14 },
  simulated: { word: 'Simulated', colorHex: '#a78bfa', tintAlpha: 0.15 },
};

/** The long label and the disclosure for a class, from the evidence package, for a title or a screen reader. */
export function lookEvidenceDescription(evidenceClass: EvidenceClass): string {
  const badge = getEpistemicBadgeDescriptor(evidenceClass);
  return `${badge.label}: ${badge.disclosure}`;
}

/**
 * One glyph per class, so colour is never the only cue, each in the box and stroke the approved mockups draw it with
 * (lib/ab.mjs, G): the first three in a 22 by 22 box centred on 0 at stroke 1.3, simulated's corner brackets in a
 * 12 by 12 box at stroke 1.1. All are shown 14 px square.
 */
export type LookGlyphPart =
  | { kind: 'circle'; r: number; filled?: boolean; dash?: string }
  | { kind: 'path'; d: string };

export interface LookGlyph {
  viewBox: string;
  strokeWidth: number;
  parts: readonly LookGlyphPart[];
}

export const LOOK_GLYPHS: Readonly<Record<EvidenceClass, LookGlyph>> = {
  observed: {
    viewBox: '-11 -11 22 22',
    strokeWidth: 1.3,
    parts: [
      { kind: 'circle', r: 5.5 },
      { kind: 'circle', r: 1.4, filled: true },
    ],
  },
  catalog_matched: {
    viewBox: '-11 -11 22 22',
    strokeWidth: 1.3,
    parts: [
      { kind: 'circle', r: 5.5 },
      { kind: 'path', d: 'M0 -10v4M0 6v4M-10 0h4M6 0h4' },
    ],
  },
  predicted: { viewBox: '-11 -11 22 22', strokeWidth: 1.3, parts: [{ kind: 'circle', r: 6, dash: '2.4 2.4' }] },
  simulated: {
    viewBox: '0 0 12 12',
    strokeWidth: 1.1,
    parts: [{ kind: 'path', d: 'M1 3.8V1H3.8M8.2 1H11V3.8M11 8.2V11H8.2M3.8 11H1V8.2' }],
  },
};

/** Control sizes: the laptop's compact controls, the laptop home's one action, the iPhone's. */
export interface LookSize {
  heightPx: number;
  fontPx: number;
  iconPx: number;
}
export const LOOK_SIZES = {
  compact: { heightPx: 36, fontPx: 15, iconPx: 14 },
  home: { heightPx: 48, fontPx: 17, iconPx: 17 },
  phone: { heightPx: 44, fontPx: 16, iconPx: 17 },
} as const satisfies Record<string, LookSize>;

/**
 * The smallest a control may be: 24 CSS px for a pointer (WCAG 2.2, success criterion 2.5.8, Target Size Minimum)
 * and 44 pt for a finger (Apple's Human Interface Guidelines, hit targets).
 */
export const LOOK_MIN_TARGET_PX = { pointer: 24, touch: 44 } as const;
export const meetsTargetSize = (heightPx: number, input: keyof typeof LOOK_MIN_TARGET_PX) =>
  heightPx >= LOOK_MIN_TARGET_PX[input];

/** The circle inside a filled or outlined pill is the pill's height less this; the split main action's circle is the full height. */
export const LOOK_INNER_CIRCLE_INSET_PX = 8;

export const LOOK_TAG = { heightPx: 28, fontPx: 13, padStartPx: 9, padEndPx: 12, gapPx: 7, glyphPx: 14, trackingEm: -0.005 } as const;

export const LOOK_TYPE = {
  sans: "'Geist', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif",
  weight: 400,
  trackingEm: -0.01,
} as const;

/**
 * The logo as apps/web draws it: "albedian" in Geist 600 in white, and the moon with its star cut out at the
 * shoulder, 0.857 of the type's size, 0.048 of it to the left and 0.238 of it up (an 18 px mark at 21 px type).
 */
export const LOOK_LOGO = {
  inkHex: '#ffffff',
  weight: 600,
  trackingEm: -0.048,
  markScale: 0.857,
  markLeftEm: -0.048,
  markUpEm: -0.238,
  defaultSizePx: 21,
} as const;

export const LOOK_FOCUS = { widthPx: 2, offsetPx: 2 } as const;

export interface LookMotion {
  spring: string;
  standard: string;
  springMs: number;
  colourMs: number;
  pressMs: number;
  pressScale: number;
  arrowStartDeg: number;
}

/** One spring for the circle docking and the arrow turning; colour changes on the standard curve; a press scales. */
export const LOOK_MOTION: Readonly<LookMotion> = {
  spring: 'cubic-bezier(0.175, 1.4, 0.32, 1)',
  standard: 'cubic-bezier(0.4, 0, 0.2, 1)',
  springMs: 1200,
  colourMs: 200,
  pressMs: 150,
  pressScale: 0.97,
  arrowStartDeg: -45,
};

/**
 * Under `prefers-reduced-motion: reduce` every duration is 0 ms, so nothing travels; a press still scales to 0.97,
 * at once, as the approved stylesheet has it (it only turns transitions off).
 */
export function resolveLookMotion(reducedMotion: boolean): LookMotion {
  if (!reducedMotion) return { ...LOOK_MOTION };
  return { ...LOOK_MOTION, springMs: 0, colourMs: 0, pressMs: 0 };
}

const rgba = (fill: HudRgba) => `rgba(${fill.join(', ')})`;
const ms = (value: number) => `${value}ms`;
const tint = (hex: string, alpha: number) => {
  const clean = hex.replace('#', '');
  const channels = [0, 1, 2].map((i) => parseInt(clean.slice(i * 2, i * 2 + 2), 16));
  return `rgba(${channels.join(', ')}, ${alpha})`;
};

function motionDurations(motion: LookMotion) {
  return {
    '--look-dur-spring': ms(motion.springMs),
    '--look-dur-colour': ms(motion.colourMs),
    '--look-dur-press': ms(motion.pressMs),
  } as const;
}

function motionProperties(motion: LookMotion) {
  return { ...motionDurations(motion), '--look-press-scale': String(motion.pressScale) } as const;
}

/** The `:root` custom properties of apps/web/src/styles/look-tokens.css. */
export const LOOK_CSS_CUSTOM_PROPERTIES = {
  '--look-field': LOOK_FIELD,
  '--look-ink': LOOK_INK,
  '--look-night': LOOK_NIGHT,
  '--look-ink-2': rgba(LOOK_INK_LEVELS.ink2),
  '--look-ink-3': rgba(LOOK_INK_LEVELS.ink3),
  '--look-hair': rgba(LOOK_INK_LEVELS.hair),
  '--look-line': rgba(LOOK_INK_LEVELS.line),
  '--look-wash': rgba(LOOK_INK_LEVELS.wash),
  '--look-ev-observed': LOOK_EVIDENCE.observed.colorHex,
  '--look-ev-catalog': LOOK_EVIDENCE.catalog_matched.colorHex,
  '--look-ev-predicted': LOOK_EVIDENCE.predicted.colorHex,
  '--look-ev-simulated': LOOK_EVIDENCE.simulated.colorHex,
  '--look-ev-observed-tint': tint(LOOK_EVIDENCE.observed.colorHex, LOOK_EVIDENCE.observed.tintAlpha),
  '--look-ev-catalog-tint': tint(LOOK_EVIDENCE.catalog_matched.colorHex, LOOK_EVIDENCE.catalog_matched.tintAlpha),
  '--look-ev-predicted-tint': tint(LOOK_EVIDENCE.predicted.colorHex, LOOK_EVIDENCE.predicted.tintAlpha),
  '--look-ev-simulated-tint': tint(LOOK_EVIDENCE.simulated.colorHex, LOOK_EVIDENCE.simulated.tintAlpha),
  '--look-font-sans': LOOK_TYPE.sans,
  '--look-tracking': `${LOOK_TYPE.trackingEm}em`,
  '--look-h-compact': `${LOOK_SIZES.compact.heightPx}px`,
  '--look-fs-compact': `${LOOK_SIZES.compact.fontPx}px`,
  '--look-ic-compact': `${LOOK_SIZES.compact.iconPx}px`,
  '--look-h-home': `${LOOK_SIZES.home.heightPx}px`,
  '--look-fs-home': `${LOOK_SIZES.home.fontPx}px`,
  '--look-ic-home': `${LOOK_SIZES.home.iconPx}px`,
  '--look-h-phone': `${LOOK_SIZES.phone.heightPx}px`,
  '--look-fs-phone': `${LOOK_SIZES.phone.fontPx}px`,
  '--look-ic-phone': `${LOOK_SIZES.phone.iconPx}px`,
  '--look-inner-inset': `${LOOK_INNER_CIRCLE_INSET_PX}px`,
  '--look-tag-h': `${LOOK_TAG.heightPx}px`,
  '--look-tag-fs': `${LOOK_TAG.fontPx}px`,
  '--look-tag-pad-start': `${LOOK_TAG.padStartPx}px`,
  '--look-tag-pad-end': `${LOOK_TAG.padEndPx}px`,
  '--look-tag-gap': `${LOOK_TAG.gapPx}px`,
  '--look-tag-glyph': `${LOOK_TAG.glyphPx}px`,
  '--look-tag-tracking': `${LOOK_TAG.trackingEm}em`,
  '--look-logo-ink': LOOK_LOGO.inkHex,
  '--look-logo-weight': String(LOOK_LOGO.weight),
  '--look-logo-tracking': `${LOOK_LOGO.trackingEm}em`,
  '--look-logo-mark-scale': String(LOOK_LOGO.markScale),
  '--look-logo-mark-left': String(LOOK_LOGO.markLeftEm),
  '--look-logo-mark-up': String(LOOK_LOGO.markUpEm),
  '--look-logo-fs': `${LOOK_LOGO.defaultSizePx}px`,
  '--look-focus-width': `${LOOK_FOCUS.widthPx}px`,
  '--look-focus-offset': `${LOOK_FOCUS.offsetPx}px`,
  '--look-ease-spring': LOOK_MOTION.spring,
  '--look-ease-standard': LOOK_MOTION.standard,
  '--look-arrow-start': `${LOOK_MOTION.arrowStartDeg}deg`,
  ...motionProperties(LOOK_MOTION),
} as const;

/** The custom properties look-tokens.css overrides under `prefers-reduced-motion: reduce`: the durations only. */
export const LOOK_REDUCED_MOTION_CSS_OVERRIDES = motionDurations(resolveLookMotion(true));
