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

/**
 * Returns a schema-valid `ObservationSession` for the `media: 'empty'` state.
 *
 * Before anything is imported, the session carries no `SourceAsset` and no
 * `ObservationSolution`, with `calibration: 'not_requested'`. `simulationEpochISO`
 * carries `createdAtISO`, the modeled sky's initial setting, while `observationEpochISO`
 * stays absent until a recording supplies a capture time.
 */
export function createEmptyObservationSession(
  createdAtISO: string = new Date().toISOString()
): ObservationSession {
  const spatialView: SpatialViewState = {
    mode: 'source_2d', transitionPhase: 'at_source', transitionProgress: 0,
    observerOriginParsec: [0, 0, 0], cameraQuaternion: [0, 0, 0, 1], fovVerticalDeg: 60,
    frustumShiftNdc: [0, 0], simulationEpochISO: createdAtISO,
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
    activeVariantId: EMPTY_MEDIA_VARIANT_ID, compareWipePosition: 0.5,
    currentSourceTimeSec: 0, isPlaying: false, audioGain: 1, cropTransform: { zoom: 1, panX: 0, panY: 0 },
    processingGraphs: [], processingAttempts: [], mediaVariants: [],
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
  const capturedAtUTC = sourceAsset.capturedAtUTC;
  const spatialView: SpatialViewState = {
    ...empty.spatialView,
    simulationEpochISO: capturedAtUTC ?? createdAtISO,
    ...(capturedAtUTC !== undefined ? { observationEpochISO: capturedAtUTC } : {}),
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
      id: `sol_${sourceAsset.id}`,
      sourceAssetId: sourceAsset.id,
      sourceSha256: sourceAsset.sha256,
      frameTimeSec: 0,
      revision: 1,
      status: 'pending',
      ...(capturedAtUTC !== undefined ? { observedAtUTC: capturedAtUTC } : {}),
      observerLocation: location
        ? { latDeg: location.latDeg, lonDeg: location.lonDeg, placeName: location.placeName }
        : undefined,
      solver: { name: 'none', version: '0', catalogRelease: 'none' },
      quality: {
        detectedCandidateCount: 0,
        matchedInlierCount: 0,
        heldOutPairCount: 0,
        heldOutInliers: 0,
        passedIndependentVerification: false,
      },
      findings: [],
      warnings: [],
    },
    spatialView,
    navigationHistory: { anchorMomentCheckpoint: anchorCheckpoint, checkpoints: [anchorCheckpoint], cursorIndex: 0 },
  });
}
