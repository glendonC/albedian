import { describe, expect, it } from 'vitest';
import {
  createDefaultSaharaObservationSession,
  createUnresolvedObservationSession,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import {
  benchmarkRendererAdapters,
  computeAnchorLockedPhaseAState,
  lookAtDirectionQuaternion,
  querySightlineCone,
  raDecToUnitVector,
} from '@albedian/camera-rig';
import { computeAudioBusGains } from '@albedian/design-system';

describe('@albedian/web — End-to-End Observation-to-Space Journey & Audio/Voice Integration', () => {
  it('executes the complete defining experience: Import -> Reveal -> Calibrated Sky -> Select Star -> 3D Space -> Sightline -> 1026 CE Epoch -> Back to Moment', () => {
    let session = createDefaultSaharaObservationSession();
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
    const phaseA = computeAnchorLockedPhaseAState({
      anchorRaDeg: betelgeuse.skyPosition.raDeg,
      anchorDecDeg: betelgeuse.skyPosition.decDeg,
      anchorTargetNdc: [0.22, -0.18],
      qStart: session.spatialView.cameraQuaternion,
      qEnd: lookAtDirectionQuaternion(
        raDecToUnitVector(betelgeuse.skyPosition.raDeg, betelgeuse.skyPosition.decDeg)
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
    );
    expect(cone.length).toBeGreaterThan(0);

    // 6. User says: "Show me this sky a thousand years ago"
    const r5 = executeVoiceOrTextCommand(
      session,
      'Show me this sky a thousand years ago',
      'voice'
    );
    expect(r5.outcome.status).toBe('completed');
    session = r5.session;
    expect(session.spatialView.simulationEpochISO.startsWith('1026')).toBe(true);

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

  it('prevents 3D space transition on unresolved / predicted-only observations and reports renderer benchmark', () => {
    const unresolvedSession = createUnresolvedObservationSession();
    expect(unresolvedSession.observationSolution.status).not.toBe('calibrated');

    const bench = benchmarkRendererAdapters(10000);
    expect(bench.recommendedPrimaryEngine).toBe('custom_wcs_three_rig');
    expect(bench.customRigAnchorDriftPx).toBeLessThan(0.01);
  });
});
