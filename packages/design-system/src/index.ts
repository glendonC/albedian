import {
  EvidenceClass,
  NavigationMachineState,
  VoiceMachineState,
} from '@albedian/contracts';
import { getEpistemicBadgeDescriptor } from '@albedian/evidence';

/**
 * ============================================================================
 * @albedian/design-system
 * Observational Instrument Visual Tokens, Non-Color Marker Grammar,
 * Motion Timing Contracts, Accessibility & Web Audio Gain Policy
 * ============================================================================
 */

export const ALBEDIAN_COLORS = {
  // Primary Lunar Regolith & CyberLife Obsidian tokens (Detroit: Become Human / ARC Raiders / SpaceX Crew Dragon / GRU Space)
  voidObsidian: '#040507',
  instrumentSlate: '#090B10',
  elevatedHairline: '#1A1E26',
  starlightBone: '#F4F2ED',
  tungstenMuted: '#9499A3',
  dimGraphite: '#585E6B',
  accentObserved: '#F4F2ED',
  accentCatalog: '#88C0D0',
  accentPredicted: '#D9A05B',
  accentSimulated: '#A39EC4',
  semanticVerified: '#78A890',
  semanticCaution: '#D49A54',
  semanticDanger: '#E04F33',

  // Foundational surface & contrast aliases
  space0: '#040507',
  space1: '#090B10',
  surfaceRaised: '#1A1E26',
  borderSubtle: 'rgba(244, 242, 237, 0.14)',
  textPrimary: '#F4F2ED',
  textSecondary: '#D6D3CD',
  textMuted: '#8E929B',
  accentObservation: '#F4F2ED',
  accentWarning: '#D49A54',
  accentSimulation: '#A39EC4',
} as const;

export type AlbedianThemeMode = 'dark' | 'light';

export interface AlbedianThemeTokenSet {
  mode: AlbedianThemeMode;
  label: string;
  bg: string;
  surface: string;
  surfaceElevated: string;
  border: string;
  borderStrong: string;
  text: string;
  textSecondary: string;
  textMuted: string;
  accentIce: string;
  accentGold: string;
  accentVermilion: string;
  accentLavender: string;
}

/**
 * Dual-Theme Observational Instrument Palettes:
 * - `dark`: CyberLife Obsidian (#040507 / #090B10) with Lunar Bone (#F4F2ED)
 * - `light`: Lunar Albedo (#F4F2ED / #EAE6DF) with Obsidian Ink (#090B10),
 *   inspired by Starfield Constellation, Oblivion Lighttable, Detroit: Become Human & Destiny 2 Director.
 */
export const ALBEDIAN_THEMES: Record<AlbedianThemeMode, AlbedianThemeTokenSet> = {
  dark: {
    mode: 'dark',
    label: 'CyberLife Obsidian',
    bg: '#040507',
    surface: 'rgba(9, 11, 16, 0.78)',
    surfaceElevated: 'rgba(26, 30, 38, 0.88)',
    border: 'rgba(244, 242, 237, 0.14)',
    borderStrong: 'rgba(244, 242, 237, 0.38)',
    text: '#F4F2ED',
    textSecondary: '#D6D3CD',
    textMuted: '#9499A3',
    accentIce: '#88C0D0',
    accentGold: '#D9A05B',
    accentVermilion: '#E04F33',
    accentLavender: '#A39EC4',
  },
  light: {
    mode: 'light',
    label: 'Lunar Albedo',
    bg: '#F4F2ED',
    surface: 'rgba(234, 230, 223, 0.84)',
    surfaceElevated: 'rgba(248, 246, 242, 0.94)',
    border: 'rgba(9, 11, 16, 0.14)',
    borderStrong: 'rgba(9, 11, 16, 0.42)',
    text: '#090B10',
    textSecondary: '#2C313C',
    textMuted: '#585E6B',
    accentIce: '#2B6E82',
    accentGold: '#9E6820',
    accentVermilion: '#C8371B',
    accentLavender: '#5B5482',
  },
} as const;

export const ALBEDIAN_TYPOGRAPHY = {
  fontSans:
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif',
  fontMono:
    '"SF Mono", "JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, monospace',
  numericVariant: 'tabular-nums',
  sizesPx: {
    microMeta: 12,
    control: 14,
    selectedObject: 19,
    sectionTitle: 24,
  },
} as const;

export const ALBEDIAN_SPACING = {
  gridUnitPx: 8,
  microOffsetPx: 4,
  desktopGutterPx: 24,
  mobileGutterPx: 16,
  minTouchTargetPx: 44,
  inspectorWidthPx: 328,
} as const;

export type MotionInteractionKind =
  | 'import_place'
  | 'analyze_badge'
  | 'restoration_crossfade'
  | 'compare_wipe'
  | 'detection_reveal'
  | 'inspector_dock'
  | 'phase_a_photo_to_sky'
  | 'phase_b_sky_to_space'
  | 'viewpoint_orbit'
  | 'return_to_moment';

export interface TransitionTimingSpec {
  interaction: MotionInteractionKind;
  durationMs: number;
  easing: string;
  usesSpatialCameraTravel: boolean;
  interruptible: boolean;
}

const STANDARD_TIMINGS: Record<MotionInteractionKind, Omit<TransitionTimingSpec, 'interaction'>> = {
  import_place: {
    durationMs: 240,
    easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  analyze_badge: {
    durationMs: 150,
    easing: 'ease-out',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  restoration_crossfade: {
    durationMs: 220,
    easing: 'ease-in-out',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  compare_wipe: {
    durationMs: 0,
    easing: 'linear',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  detection_reveal: {
    durationMs: 200,
    easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  inspector_dock: {
    durationMs: 200,
    easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    usesSpatialCameraTravel: false,
    interruptible: true,
  },
  phase_a_photo_to_sky: {
    durationMs: 680,
    easing: 'cubic-bezier(0.33, 1, 0.68, 1)',
    usesSpatialCameraTravel: true,
    interruptible: true,
  },
  phase_b_sky_to_space: {
    durationMs: 2200,
    easing: 'cubic-bezier(0.45, 0, 0.2, 1)',
    usesSpatialCameraTravel: true,
    interruptible: true,
  },
  viewpoint_orbit: {
    durationMs: 650,
    easing: 'cubic-bezier(0.25, 1, 0.5, 1)',
    usesSpatialCameraTravel: true,
    interruptible: true,
  },
  return_to_moment: {
    durationMs: 680,
    easing: 'cubic-bezier(0.33, 1, 0.68, 1)',
    usesSpatialCameraTravel: true,
    interruptible: true,
  },
};

/**
 * Resolves functional motion timing while honoring `prefers-reduced-motion: reduce`.
 * In reduced-motion mode, all spatial z-depth camera flights are replaced by a short
 * 160ms opacity crossfade or 0ms instant state commit.
 */
export function resolveTransitionTiming(
  interaction: MotionInteractionKind,
  reducedMotion: boolean
): TransitionTimingSpec {
  const base = STANDARD_TIMINGS[interaction];
  if (!reducedMotion) {
    return { interaction, ...base };
  }

  return {
    interaction,
    durationMs: interaction === 'compare_wipe' ? 0 : 140,
    easing: 'ease-out',
    usesSpatialCameraTravel: false,
    interruptible: true,
  };
}

export interface MarkerVisualSpec {
  evidenceClass: EvidenceClass;
  colorHex: string;
  shape: 'solid_dot_tick' | 'concentric_ring_elbow' | 'dashed_circle' | 'bracket_reticle';
  shortCode: 'OBS' | 'CAT' | 'PRD' | 'SIM';
  badgeLabel: string;
  visibleRadiusPx: number;
  hitTargetSizePx: number;
  strokeDasharray: string;
  showElbowConnector: boolean;
  showKeyboardFocusRing: boolean;
  showHoverHalo: boolean;
  ariaDescription: string;
}

/**
 * Returns a complete, accessible visual specification for a celestial marker.
 * Differentiates hover, keyboard focus, and selection states distinctly (Apple HIG [S18]),
 * and differentiates all four EvidenceClasses via geometry, stroke pattern, and text badge.
 */
export function getMarkerVisualSpec(params: {
  evidenceClass: EvidenceClass;
  title: string;
  isSelected: boolean;
  isFocused: boolean;
  isHovered: boolean;
}): MarkerVisualSpec {
  const badge = getEpistemicBadgeDescriptor(params.evidenceClass);

  const colorMap: Record<EvidenceClass, string> = {
    observed: ALBEDIAN_COLORS.accentObservation,
    catalog_matched: ALBEDIAN_COLORS.accentCatalog,
    predicted: ALBEDIAN_COLORS.accentPredicted,
    simulated: ALBEDIAN_COLORS.accentSimulation,
  };

  const dashMap: Record<EvidenceClass, string> = {
    observed: 'none',
    catalog_matched: 'none',
    predicted: '4 3',
    simulated: '8 4',
  };

  const baseRadius =
    params.evidenceClass === 'observed'
      ? 4.5
      : params.evidenceClass === 'catalog_matched'
        ? 7.5
        : 8.5;

  return {
    evidenceClass: params.evidenceClass,
    colorHex: colorMap[params.evidenceClass],
    shape: badge.markerShape,
    shortCode: badge.shortCode,
    badgeLabel: badge.label,
    visibleRadiusPx: params.isSelected ? baseRadius + 3.5 : baseRadius,
    hitTargetSizePx: ALBEDIAN_SPACING.minTouchTargetPx,
    strokeDasharray: dashMap[params.evidenceClass],
    showElbowConnector: params.isSelected,
    showKeyboardFocusRing: params.isFocused,
    showHoverHalo: params.isHovered && !params.isSelected,
    ariaDescription: `${params.title} (${badge.label}${params.isSelected ? ', Selected' : ''})`,
  };
}

/**
 * Default baseline visual specifications indexed by `EvidenceClass`.
 */
export const EVIDENCE_MARKER_SPECS: Record<EvidenceClass, MarkerVisualSpec> = {
  observed: getMarkerVisualSpec({
    evidenceClass: 'observed',
    title: 'Observed Source',
    isSelected: false,
    isFocused: false,
    isHovered: false,
  }),
  catalog_matched: getMarkerVisualSpec({
    evidenceClass: 'catalog_matched',
    title: 'Catalog Match',
    isSelected: false,
    isFocused: false,
    isHovered: false,
  }),
  predicted: getMarkerVisualSpec({
    evidenceClass: 'predicted',
    title: 'Predicted Ephemeris',
    isSelected: false,
    isFocused: false,
    isHovered: false,
  }),
  simulated: getMarkerVisualSpec({
    evidenceClass: 'simulated',
    title: 'Simulated Model',
    isSelected: false,
    isFocused: false,
    isHovered: false,
  }),
};

/**
 * Computes a clamped [0, 100] split percentage for the synchronized A/B comparison wipe.
 */
export function computeWipeSplitPercentage(
  pointerClientX: number,
  containerLeftPx: number,
  containerWidthPx: number
): number {
  if (containerWidthPx <= 0) {
    return 50;
  }
  const rawPct = ((pointerClientX - containerLeftPx) / containerWidthPx) * 100;
  return Math.max(0, Math.min(100, rawPct));
}

/**
 * Computes WCAG 2.1 relative luminance and contrast ratio between two `#RRGGBB` colors.
 */
export function computeContrastRatio(hexA: string, hexB: string): number {
  const relLum = (hex: string): number => {
    const clean = hex.replace('#', '');
    const r = parseInt(clean.slice(0, 2), 16) / 255;
    const g = parseInt(clean.slice(2, 4), 16) / 255;
    const b = parseInt(clean.slice(4, 6), 16) / 255;
    const toLinear = (c: number) =>
      c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
  };

  const l1 = relLum(hexA);
  const l2 = relLum(hexB);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Web Audio Bus Policy (D8 of Spatial Experience Research):
 * - In `source` / `comparison` / `sky_registered`: source audio plays at user gain;
 *   if voice is actively speaking/listening, monitor source gain gently ducks by 65%.
 * - In `space`: source audio playback is paused (`sourceGain = 0`) so authentic earthside
 *   recording audio is never falsely implied to be deep-space sound.
 * - Returning (`source.return`) restores exact `userSourceGain`.
 */
export function computeAudioBusGains(params: {
  navigationState: NavigationMachineState;
  voiceState: VoiceMachineState;
  userSourceGain: number;
  isMuted: boolean;
}): {
  sourceMonitorGain: number;
  voiceFeedbackGain: number;
  uiCueGain: number;
  sourcePlaybackAllowed: boolean;
} {
  if (params.isMuted) {
    return {
      sourceMonitorGain: 0,
      voiceFeedbackGain: 0,
      uiCueGain: 0,
      sourcePlaybackAllowed: params.navigationState !== 'space',
    };
  }

  const inDeepSpace =
    params.navigationState === 'space' || params.navigationState === 'transitioning';
  if (inDeepSpace) {
    return {
      sourceMonitorGain: 0,
      voiceFeedbackGain: 0.9,
      uiCueGain: 0.18,
      sourcePlaybackAllowed: false,
    };
  }

  const voiceActive =
    params.voiceState === 'listening' ||
    params.voiceState === 'hearing' ||
    params.voiceState === 'speaking';

  const clampedUserGain = Math.max(0, Math.min(1, params.userSourceGain));
  return {
    sourceMonitorGain: voiceActive ? clampedUserGain * 0.35 : clampedUserGain,
    voiceFeedbackGain: 0.9,
    uiCueGain: 0.15,
    sourcePlaybackAllowed: true,
  };
}

export type CoordinateSpaceBadgeLabel =
  | 'SPACE: ASTRONOMICAL (SKY / ICRS)'
  | 'SPACE: EXPLANATORY SPECIMEN'
  | 'SPACE: GEODESIC MODEL';

/**
 * Resolves the active Coordinate Space Badge per Track 1 & Track 14 of the Master Brief.
 */
export function resolveCoordinateSpaceBadge(params: {
  lensMode: 'clean_aperture' | 'isometric_strata' | 'geodesic_exploded';
  isSpace3D: boolean;
}): CoordinateSpaceBadgeLabel {
  if (params.isSpace3D) {
    return 'SPACE: ASTRONOMICAL (SKY / ICRS)';
  }
  if (params.lensMode === 'isometric_strata') {
    return 'SPACE: EXPLANATORY SPECIMEN';
  }
  if (params.lensMode === 'geodesic_exploded') {
    return 'SPACE: GEODESIC MODEL';
  }
  return 'SPACE: ASTRONOMICAL (SKY / ICRS)';
}

