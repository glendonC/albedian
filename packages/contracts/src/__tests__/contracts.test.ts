import { describe, expect, it } from 'vitest';
import { ObservationSessionSchema, SourceAssetSchema } from '../index.js';

// The restoration and export records are tested against the Python mirror in media.test.ts
describe('@albedian/contracts: Domain Schemas', () => {
  it('validates immutable SourceAsset schema and rejects mutable flags', () => {
    const validSource = {
      id: 'src_synthetic_001',
      title: 'Synthetic night clip',
      mediaKind: 'video' as const,
      sha256: 'a8f5c2d9104e8b771234567890abcdefa8f5c2d9104e8b771234567890abcdef',
      uri: 'file://fixtures/synthetic_night.mp4',
      width: 1920,
      height: 1080,
      durationMs: 12000,
      fpsNumerator: 30,
      fpsDenominator: 1,
      videoCodec: 'h264',
      audioStreams: [
        {
          index: 1,
          codec: 'aac',
          sampleRateHz: 48000,
          channels: 2,
          startPtsMs: 0,
          durationMs: 12000,
          sha256: 'audio77889900aabb',
        },
      ],
      // A synthetic moment and place that point at nobody: the open North Atlantic
      capturedAtUTC: '2030-01-01T02:00:00Z',
      observerLocation: {
        latDeg: 30.0,
        lonDeg: -40.0,
        elevationM: 0,
        placeName: 'Synthetic place, open North Atlantic',
      },
      metadataSources: {
        capturedAtUTC: { container: 'synthetic' as const, tags: ['test-fixture'] },
        observerLocation: { container: 'synthetic' as const, tags: ['test-fixture'] },
      },
      privacyClass: 'consented_demo' as const,
      immutable: true as const,
    };

    expect(SourceAssetSchema.safeParse(validSource).success).toBe(true);
    expect(
      SourceAssetSchema.safeParse({ ...validSource, immutable: false }).success
    ).toBe(false);
  });

  it('requires ObservationSession to omit sourceAsset and observationSolution when media is empty and include both otherwise', () => {
    const spatialView = {
      mode: 'source_2d' as const,
      transitionPhase: 'at_source' as const,
      transitionProgress: 0,
      observerOriginParsec: [0, 0, 0] as [number, number, number],
      cameraQuaternion: [0, 0, 0, 1] as [number, number, number, number],
      fovVerticalDeg: 60,
      frustumShiftNdc: [0, 0] as [number, number],
      simulationEpochISO: '2030-01-01T02:00:00Z',
      sceneScalePolicy: 'physical_euclidean' as const,
      activeOverlays: {
        observedMarkers: false,
        catalogMatchedLabels: false,
        predictedEphemerides: false,
        constellationLines: false,
        earthDirectionVector: false,
        residualVectors: false,
        sightlineCone: false,
      },
    };
    const anchorMomentCheckpoint = {
      id: 'chk_none',
      label: 'No observation yet',
      createdAtMs: 0,
      sourceAssetId: 'src_none',
      sourceTimeSec: 0,
      playbackWasPlaying: false,
      audioGain: 1,
      mediaVariantId: 'variant_none',
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      selectedFindingId: null,
      navigationState: 'source' as const,
      spatialView,
      viewRevision: 0,
    };
    const emptySession = {
      id: 'session_empty',
      title: 'No observation yet',
      viewRevision: 0,
      machines: {
        media: 'empty' as const,
        restoration: 'idle' as const,
        calibration: 'not_requested' as const,
        navigation: 'source' as const,
        voice: 'off' as const,
      },
      activeVariantId: 'variant_none',
      compareWipePosition: 0.5,
      currentSourceTimeSec: 0,
      isPlaying: false,
      audioGain: 1,
      cropTransform: { zoom: 1, panX: 0, panY: 0 },
      processingGraphs: [],
      processingAttempts: [],
      mediaVariants: [],
      selectedFindingId: null,
      focusedFindingId: null,
      spatialView,
      navigationHistory: {
        anchorMomentCheckpoint,
        checkpoints: [anchorMomentCheckpoint],
        cursorIndex: 0,
      },
      voiceActions: [],
      exportArtifacts: [],
      reducedMotion: false,
    };
    const sourceAsset = {
      id: 'src_upload_01',
      title: 'night.jpg',
      mediaKind: 'image' as const,
      sha256: 'a8f5c2d9104e8b771234567890abcdefa8f5c2d9104e8b771234567890abcdef',
      uri: 'blob:night',
      width: 1920,
      height: 1080,
      durationMs: 0,
      videoCodec: 'none',
      audioStreams: [],
      privacyClass: 'private' as const,
      immutable: true as const,
    };
    const observationSolution = {
      id: 'sol_upload_01',
      sourceAssetId: sourceAsset.id,
      sourceSha256: sourceAsset.sha256,
      frameTimeSec: 0,
      revision: 1,
      status: 'pending' as const,
      // Nothing has run yet, so a pending solution names no solver and counts nothing
      findings: [],
      warnings: [],
    };

    const parsedEmpty = ObservationSessionSchema.safeParse(emptySession);
    expect(parsedEmpty.error?.issues ?? []).toEqual([]);
    expect(parsedEmpty.success).toBe(true);

    expect(ObservationSessionSchema.safeParse({ ...emptySession, sourceAsset }).success).toBe(false);
    expect(ObservationSessionSchema.safeParse({ ...emptySession, observationSolution }).success).toBe(false);
    expect(
      ObservationSessionSchema.safeParse({ ...emptySession, sourceAsset, observationSolution }).success
    ).toBe(false);

    const importedSession = {
      ...emptySession,
      machines: { ...emptySession.machines, media: 'importing' as const },
      sourceAsset,
      observationSolution,
    };
    expect(ObservationSessionSchema.safeParse(importedSession).success).toBe(true);
    expect(
      ObservationSessionSchema.safeParse({
        ...emptySession,
        machines: { ...emptySession.machines, media: 'importing' as const },
        sourceAsset,
      }).success
    ).toBe(false);
    expect(
      ObservationSessionSchema.safeParse({
        ...emptySession,
        machines: { ...emptySession.machines, media: 'importing' as const },
        observationSolution,
      }).success
    ).toBe(false);
  });
});
