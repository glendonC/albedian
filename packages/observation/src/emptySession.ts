import {
  CameraCheckpoint,
  MediaMachineState,
  ObservationSession,
  ObservationSessionSchema,
  SourceAsset,
  SpatialViewState,
} from '@albedian/contracts';

/**
 * ============================================================================
 * @albedian/observation: Empty Session
 * The honest initial state before the user imports media or chooses a sky
 * ============================================================================
 */

export const EMPTY_SOURCE_ASSET_ID = 'src_none';
export const EMPTY_MEDIA_VARIANT_ID = 'variant_none';
const EMPTY_SHA256 = '0'.repeat(64);

/**
 * Returns a schema-valid `ObservationSession` for the `media: 'empty'` state.
 *
 * The contracts require a `SourceAsset`, an `ObservationSolution` and an anchor
 * checkpoint even before anything is imported, so those are explicit placeholders:
 * no observer location, no capture time, no findings, `calibration: 'not_requested'`
 * and solution `status: 'pending'`. The required timestamps carry `createdAtISO`,
 * the moment this session was created, never a capture time.
 */
export function createEmptyObservationSession(
  createdAtISO: string = new Date().toISOString()
): ObservationSession {
  const spatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 60,
    frustumShiftNdc: [0, 0], simulationEpochISO: createdAtISO, observationEpochISO: createdAtISO,
    sceneScalePolicy: 'physical_euclidean',
    activeOverlays: { observedMarkers: false, catalogMatchedLabels: false, predictedEphemerides: false, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
  };

  const anchorCheckpoint: CameraCheckpoint = {
    id: 'chk_none', label: 'No observation yet', createdAtMs: 0,
    sourceAssetId: EMPTY_SOURCE_ASSET_ID, sourceTimeSec: 0, playbackWasPlaying: false, audioGain: 1,
    mediaVariantId: EMPTY_MEDIA_VARIANT_ID, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    selectedFindingId: null, navigationState: 'source', spatialView: structuredClone(spatialView), viewRevision: 0,
  };

  return ObservationSessionSchema.parse({
    id: 'session_empty', title: 'No observation yet', viewRevision: 0,
    machines: { media: 'empty', restoration: 'idle', calibration: 'not_requested', navigation: 'source', voice: 'off' },
    sourceAsset: {
      id: EMPTY_SOURCE_ASSET_ID, title: 'No observation yet', mediaKind: 'image', sha256: EMPTY_SHA256,
      uri: 'albedian://none', width: 1, height: 1, durationMs: 0,
      fpsNumerator: 1, fpsDenominator: 1, videoCodec: 'none', audioStreams: [],
      privacyClass: 'private', immutable: true,
    },
    activeVariantId: EMPTY_MEDIA_VARIANT_ID, compareWipePosition: 0.5,
    currentSourceTimeSec: 0, isPlaying: false, audioGain: 1, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [], processingAttempts: [], mediaVariants: [],
    observationSolution: {
      id: 'sol_none', sourceAssetId: EMPTY_SOURCE_ASSET_ID, sourceSha256: EMPTY_SHA256, frameTimeSec: 0,
      revision: 1, status: 'pending', observedAtUTC: createdAtISO,
      solver: { name: 'none', version: '0', catalogRelease: 'none' },
      quality: { detectedCandidateCount: 0, matchedInlierCount: 0, heldOutInliers: 0, medianResidualPx: 0, p95ResidualPx: 0, medianResidualArcsec: 0, passedIndependentVerification: false },
      findings: [], warnings: [],
    },
    selectedFindingId: null, focusedFindingId: null, spatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
    voiceActions: [], exportArtifacts: [], reducedMotion: false,
  });
}

/**
 * Returns the empty session re-anchored to an imported `sourceAsset`. The solution and
 * anchor checkpoint point at the asset, calibration stays `not_requested`, and the
 * solution takes a location and capture time only when the asset itself carries them.
 */
export function createSessionForSourceAsset(
  sourceAsset: SourceAsset,
  mediaState: MediaMachineState,
  createdAtISO: string = new Date().toISOString()
): ObservationSession {
  const empty = createEmptyObservationSession(createdAtISO);
  const location = sourceAsset.observerLocation;
  const momentISO = sourceAsset.capturedAtUTC ?? createdAtISO;
  const spatialView: SpatialViewState = {
    ...empty.spatialView,
    simulationEpochISO: momentISO,
    observationEpochISO: momentISO,
  };
  const anchorCheckpoint: CameraCheckpoint = {
    ...empty.navigationHistory.anchorMomentCheckpoint,
    id: `chk_${sourceAsset.id}_anchor_0`,
    label: `Original Moment · ${sourceAsset.title}`,
    sourceAssetId: sourceAsset.id,
    spatialView: structuredClone(spatialView),
  };

  return ObservationSessionSchema.parse({
    ...empty,
    id: `session_${sourceAsset.id}`,
    title: sourceAsset.title,
    machines: { ...empty.machines, media: mediaState },
    sourceAsset,
    observationSolution: {
      ...empty.observationSolution,
      id: `sol_${sourceAsset.id}`,
      sourceAssetId: sourceAsset.id,
      sourceSha256: sourceAsset.sha256,
      observedAtUTC: momentISO,
      observerLocation: location
        ? { latDeg: location.latDeg, lonDeg: location.lonDeg, placeName: location.placeName }
        : undefined,
    },
    spatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
  });
}
