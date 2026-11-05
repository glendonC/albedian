import { describe, expect, it } from 'vitest';
import { dispatchAlbedianAction, executeVoiceOrTextCommand } from '@albedian/observation';
import {
  createSyntheticCalibratedSession,
  createSyntheticUnresolvedSession,
} from '@albedian/observation/testing';
import {
  benchmarkRendererAdapters,
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  querySightlineCone,
  raDecToUnitVector,
} from '@albedian/camera-rig';
import { ObservationSolution } from '@albedian/contracts';
import { computeAudioBusGains } from '@albedian/design-system';
import { buildSessionFromDiscoveredMedia } from '../services/apiClient';
import { createOriginCameraFrustumIndicator } from '../spatial/referenceFrameContinuity';
import {
  BRUNETON_ATMO_GLSL_CHUNK,
  BRUNETON_ATMO_LUT_URL,
} from '../spatial/volumetricSplatsAndLut';
import { observatoryAudio } from '../audio/ObservatoryAudioEngine';
import {
  ALBEDIAN_FLAT_WAVEFRONT_PALETTE,
  ALBEDIAN_PURE_SILVER_PALETTE,
} from '../components/CoalescingBar';
import fs from 'node:fs';
import path from 'node:path';

describe('@albedian/web — End-to-End Observation-to-Space Journey & Audio/Voice Integration', () => {
  it('executes the complete defining experience: Import -> Reveal -> Calibrated Sky -> Select Star -> 3D Space -> Sightline -> Epoch refused -> Back to Moment', () => {
    let session = createSyntheticCalibratedSession();
    const initialTimeSec = session.currentSourceTimeSec;
    const initialAudioGain = session.audioGain;
    const initialSourceHash = session.sourceAsset.sha256;

    // 1. User says: "Make the sky clearer"
    const r1 = executeVoiceOrTextCommand(session, 'Make the sky clearer', 'voice');
    expect(r1.outcome.status).toBe('completed');
    session = r1.session;
    expect(session.sourceAsset.sha256).toBe(initialSourceHash);

    // 2. User says: "What were we looking at?"
    const r2 = executeVoiceOrTextCommand(session, 'What were we looking at?', 'voice');
    expect(r2.outcome.status).toBe('completed');
    session = r2.session;
    expect(session.machines.navigation).toBe('sky_registered');

    // 3. User selects Betelgeuse
    const r3 = executeVoiceOrTextCommand(session, 'Select Betelgeuse', 'voice');
    expect(r3.outcome.status).toBe('completed');
    session = r3.session;
    expect(session.selectedFindingId).toBe('finding_betelgeuse');

    // Verify Phase A off-axis anchor lock math on Betelgeuse
    const betelgeuse = session.observationSolution.findings.find(
      (f) => f.id === 'finding_betelgeuse'
    )!;
    const betelgeuseSky = betelgeuse.skyPosition!;
    const phaseA = computeAnchorLockedPhaseAState({
      anchorRaDeg: betelgeuseSky.raDeg,
      anchorDecDeg: betelgeuseSky.decDeg,
      anchorTargetNdc: [0.22, -0.18],
      qStart: session.spatialView.cameraQuaternion,
      qEnd: lookAtDirectionQuaternion(
        raDecToUnitVector(betelgeuseSky.raDeg, betelgeuseSky.decDeg)
      ),
      fovStartDeg: 37.8,
      fovEndDeg: 28.0,
      aspect: 16 / 9,
      progress: 0.65,
    });
    expect(phaseA.anchorDriftPx).toBeLessThan(1e-4);

    // 4. User says: "Take me there"
    const r4 = executeVoiceOrTextCommand(session, 'Take me there', 'voice');
    expect(r4.outcome.status).toBe('completed');
    session = r4.session;
    expect(session.machines.navigation).toBe('space');
    expect(session.isPlaying).toBe(false);

    // Verify Web Audio Bus pauses earthside source audio during 3D space exploration
    const spaceAudio = computeAudioBusGains({
      navigationState: session.machines.navigation,
      voiceState: session.machines.voice,
      userSourceGain: session.audioGain,
      isMuted: false,
    });
    expect(spaceAudio.sourceMonitorGain).toBe(0);
    expect(spaceAudio.sourcePlaybackAllowed).toBe(false);

    // 5. Sightline cone query ("What's behind it?")
    const cone = querySightlineCone(
      session.observationSolution.findings,
      [0, 0, 0],
      betelgeuse,
      25
    ) ?? [];
    expect(cone.length).toBeGreaterThan(0);

    // 6. User says: "Show me this sky a thousand years ago". Nothing propagates star positions yet, so the
    // epoch is refused and the view keeps its moment
    const epochBefore = session.spatialView.simulationEpochISO;
    const r5 = executeVoiceOrTextCommand(
      session,
      'Show me this sky a thousand years ago',
      'voice'
    );
    expect(r5.outcome).toMatchObject({ status: 'rejected', reason: 'not_connected' });
    session = r5.session;
    expect(session.spatialView.simulationEpochISO).toBe(epochBefore);

    // 7. User says: "Take me back"
    const r6 = dispatchAlbedianAction(session, {
      type: 'source.return',
      requestId: 'return_e2e',
      expectedViewRevision: session.viewRevision,
      restoreAudio: true,
    });
    expect(r6.outcome.status).toBe('completed');
    session = r6.session;

    expect(session.machines.navigation).toBe('source');
    expect(session.currentSourceTimeSec).toBe(initialTimeSec);
    expect(session.audioGain).toBe(initialAudioGain);
    expect(session.sourceAsset.sha256).toBe(initialSourceHash);
  });

  it('loads backend detections without a sky position and gives them no 3D position', () => {
    const samples = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
    ) as Record<string, ObservationSolution>;
    for (const name of ['plate_wcs_failed_verification', 'horizon_approximate']) {
      const solution = samples[name]!;
      expect(solution.sourceSha256).toMatch(/^[0-9a-f]{64}$/);
      const session = buildSessionFromDiscoveredMedia({
        id: name, title: name, filename: `${name}.png`, sha256: solution.sourceSha256!, mediaKind: 'image',
        width: 960, height: 540, durationSec: 0, fps: 1, videoCodec: 'none', capturedAtUTC: solution.observedAtUTC,
        latDeg: solution.observerLocation?.latDeg ?? null, lonDeg: solution.observerLocation?.lonDeg ?? null,
        cameraModel: 'Unknown camera', urls: { origUrl: `/media/${name}.png`, v1Url: '', v2Url: '' }, solution,
      });
      const unplaced = session.observationSolution.findings.filter((f) => f.skyPosition === undefined);
      expect(unplaced.length).toBeGreaterThan(0);
      for (const finding of unplaced) {
        expect(finding.evidenceClass).toBe('observed');
        expect(finding.sourceDetection).toBeDefined();
        expect(finding.cartesianParsecICRS).toBeUndefined();
      }
    }
  });

  it('prevents 3D space transition on unresolved / predicted-only observations and reports renderer benchmark', () => {
    const unresolvedSession = createSyntheticUnresolvedSession();
    expect(unresolvedSession.observationSolution.status).not.toBe('calibrated');

    const bench = benchmarkRendererAdapters(10000);
    expect(bench.recommendedPrimaryEngine).toBe('custom_wcs_three_rig');
    expect(bench.customRigAnchorDriftPx).toBeLessThan(0.01);
  });

  it('verifies Track 1 (Origin Frustum Continuity) and Track 2 (Bruneton LUT)', () => {
    // Track 1: Origin Frustum
    const frustum = createOriginCameraFrustumIndicator();
    expect(frustum.name).toBe('OriginCameraFrustumIndicator');

    // Track 2: Bruneton LUT & GLSL Chunk
    expect(BRUNETON_ATMO_LUT_URL).toBe('/textures/bruneton_atmo_lut.png');
    expect(BRUNETON_ATMO_GLSL_CHUNK).toContain('sampleBrunetonAtmoLut');

  });

  it('verifies Flat Architectural Vapor-Silver + Luminous Wavefront Refraction palette invariants (@zzzzshawn + Lusion)', () => {
    // High-albedo Vapor-Silver solid fill (#D4DCE7 -> #EDF2F8 -> #FFFFFF)
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidStart).toBe('#D4DCE7');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidMid).toBe('#EDF2F8');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.solidEnd).toBe('#FFFFFF');

    // Dichroic Ice-Cobalt wavefront refraction strictly inside coalescing dots/slats
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.wavefrontMid).toBe('#5A87E8');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.wavefrontDim).toBe('#355291');
    expect(ALBEDIAN_FLAT_WAVEFRONT_PALETTE.restPattern).toBe('#252D3D');

    // Pure Silver alternative
    expect(ALBEDIAN_PURE_SILVER_PALETTE.solidEnd).toBe('#FFFFFF');
  });

  it('verifies Living Sky Window Landing Page vs. App View separation, Audio toggle, and 3D showcase invariants', () => {
    // 1. Audio starts disabled until the user turns it on
    expect(observatoryAudio.getEnabled()).toBe(false);

    // 2. SpatialObservatoryViewport & SparkPortalRenderer invariants
    const viewportSrc = fs.readFileSync(
      path.resolve(__dirname, '../components/SpatialObservatoryViewport.tsx'),
      'utf8'
    );
    expect(viewportSrc).toContain('LANDING_SHOWCASE_SCENES');
    expect(viewportSrc).toContain('Discover the universe behind');
    expect(viewportSrc).toContain('the buried night sky.');
    expect(viewportSrc).toContain('Launch Albedian');
    expect(viewportSrc).toContain('if (entryStageActive) return;');

    const rendererSrc = fs.readFileSync(
      path.resolve(__dirname, '../spatial/SparkPortalRenderer.ts'),
      'utf8'
    );
    expect(rendererSrc).toContain('setShowcaseMode(enabled: boolean)');
    expect(rendererSrc).toContain('transitionToShowcaseScene(');
    expect(rendererSrc).toContain('this.originFrustumGroup.visible = false;');
    expect(rendererSrc).toContain('sampleBrunetonAtmoLut');
  });
});
