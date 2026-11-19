import { describe, expect, it } from 'vitest';
import * as designSystem from '../index.js';
import {
  ALBEDIAN_COLORS,
  ALBEDIAN_SPACING,
  computeAudioBusGains,
  computeContrastRatio,
  getMarkerVisualSpec,
  MOTION_TOKENS,
  resolveTransitionTiming,
  type MotionInteractionKind,
} from '../index.js';

describe('@albedian/design-system: Tokens, Contrast, Markers, Motion & Audio Bus', () => {
  it('verifies WCAG AA/AAA contrast ratios for all text and evidence tokens against space-0 and space-1', () => {
    expect(
      computeContrastRatio(ALBEDIAN_COLORS.textPrimary, ALBEDIAN_COLORS.space0)
    ).toBeGreaterThanOrEqual(15.0); // AAA
    expect(
      computeContrastRatio(ALBEDIAN_COLORS.textSecondary, ALBEDIAN_COLORS.space1)
    ).toBeGreaterThanOrEqual(7.0); // AAA
    expect(
      computeContrastRatio(ALBEDIAN_COLORS.textMuted, ALBEDIAN_COLORS.space0)
    ).toBeGreaterThanOrEqual(4.5); // AA

    // Evidence accent tokens must all exceed 4.5:1 on both dark surfaces
    const accents = [
      ALBEDIAN_COLORS.accentObservation,
      ALBEDIAN_COLORS.accentCatalog,
      ALBEDIAN_COLORS.accentPredicted,
      ALBEDIAN_COLORS.accentWarning,
      ALBEDIAN_COLORS.accentSimulation,
    ];
    for (const accent of accents) {
      expect(computeContrastRatio(accent, ALBEDIAN_COLORS.space0)).toBeGreaterThan(6.0);
      expect(computeContrastRatio(accent, ALBEDIAN_COLORS.space1)).toBeGreaterThan(5.0);
    }
  });

  it('distinguishes hover, keyboard focus, and selection states and enforces >= 44px touch targets', () => {
    const hoverSpec = getMarkerVisualSpec({
      evidenceClass: 'catalog_matched',
      title: 'Betelgeuse',
      isSelected: false,
      isFocused: false,
      isHovered: true,
    });
    const focusSpec = getMarkerVisualSpec({
      evidenceClass: 'catalog_matched',
      title: 'Betelgeuse',
      isSelected: false,
      isFocused: true,
      isHovered: false,
    });
    const selectedSpec = getMarkerVisualSpec({
      evidenceClass: 'catalog_matched',
      title: 'Betelgeuse',
      isSelected: true,
      isFocused: false,
      isHovered: false,
    });

    expect(hoverSpec.hitTargetSizePx).toBeGreaterThanOrEqual(
      ALBEDIAN_SPACING.minTouchTargetPx
    );
    expect(hoverSpec.showHoverHalo).toBe(true);
    expect(hoverSpec.showElbowConnector).toBe(false);

    expect(focusSpec.showKeyboardFocusRing).toBe(true);
    expect(focusSpec.showElbowConnector).toBe(false);

    expect(selectedSpec.showElbowConnector).toBe(true);
    expect(selectedSpec.visibleRadiusPx).toBeGreaterThan(hoverSpec.visibleRadiusPx);
  });

  it('replaces spatial camera flight with the motion record\'s dissolve when reducedMotion is enabled', () => {
    const normalFlight = resolveTransitionTiming('phase_b_sky_to_space', false);
    const reducedFlight = resolveTransitionTiming('phase_b_sky_to_space', true);

    expect(normalFlight.usesSpatialCameraTravel).toBe(true);
    expect(normalFlight.durationMs).toBe(MOTION_TOKENS.cameraHalfMs);
    expect(normalFlight.easing).toBe(MOTION_TOKENS.easeCamera);

    expect(reducedFlight.usesSpatialCameraTravel).toBe(false);
    expect(reducedFlight.durationMs).toBe(MOTION_TOKENS.dissolveMs);
    expect(reducedFlight.easing).toBe('ease-in-out');
  });

  it('takes every interaction timing from the motion tokens (the MOTION record), never a number of its own', () => {
    const expected: Record<MotionInteractionKind, [number, string]> = {
      import_place: [MOTION_TOKENS.frameInMs, MOTION_TOKENS.easeFrameIn],
      analyze_badge: [MOTION_TOKENS.wordsInMs, MOTION_TOKENS.easeStandard],
      restoration_crossfade: [MOTION_TOKENS.viewMs, MOTION_TOKENS.easeStandard],
      compare_wipe: [0, 'linear'],
      detection_reveal: [MOTION_TOKENS.markMs, 'linear'],
      inspector_dock: [MOTION_TOKENS.frameInMs, MOTION_TOKENS.easeFrameIn],
      phase_a_photo_to_sky: [MOTION_TOKENS.cameraHalfMs, MOTION_TOKENS.easeCamera],
      phase_b_sky_to_space: [MOTION_TOKENS.cameraHalfMs, MOTION_TOKENS.easeCamera],
      viewpoint_orbit: [MOTION_TOKENS.cameraHalfMs, MOTION_TOKENS.easeCamera],
      return_to_moment: [MOTION_TOKENS.cameraHalfMs, MOTION_TOKENS.easeCamera],
    };
    for (const [kind, [ms, ease]] of Object.entries(expected) as [MotionInteractionKind, [number, string]][]) {
      const t = resolveTransitionTiming(kind, false);
      expect([kind, t.durationMs, t.easing]).toEqual([kind, ms, ease]);
      const r = resolveTransitionTiming(kind, true);
      // the wipe follows the hand, so it has nothing to dissolve; everything else is the one dissolve
      expect([kind, r.durationMs]).toEqual([kind, kind === 'compare_wipe' ? 0 : MOTION_TOKENS.dissolveMs]);
      expect(r.usesSpatialCameraTravel).toBe(false);
    }
  });

  it('pauses source audio in 3D space and ducks monitor audio during active voice interaction', () => {
    const sourceNormal = computeAudioBusGains({
      navigationState: 'source',
      voiceState: 'off',
      userSourceGain: 0.8,
      isMuted: false,
    });
    expect(sourceNormal.sourceMonitorGain).toBeCloseTo(0.8);
    expect(sourceNormal.sourcePlaybackAllowed).toBe(true);

    const sourceDucking = computeAudioBusGains({
      navigationState: 'source',
      voiceState: 'listening',
      userSourceGain: 0.8,
      isMuted: false,
    });
    expect(sourceDucking.sourceMonitorGain).toBeCloseTo(0.8 * 0.35);

    const spacePaused = computeAudioBusGains({
      navigationState: 'space',
      voiceState: 'off',
      userSourceGain: 0.8,
      isMuted: false,
    });
    expect(spacePaused.sourceMonitorGain).toBe(0);
    expect(spacePaused.sourcePlaybackAllowed).toBe(false);
  });

  it('does not export the unused operational instrument status helper', () => {
    expect('resolveOperationalInstrumentStatus' in designSystem).toBe(false);
  });
});
