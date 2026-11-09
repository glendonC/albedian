import {
  CameraCheckpoint,
  CameraCheckpointSchema,
  CelestialFinding,
  MediaVariant,
  ObservationSession,
  orientedSize,
  orientStoredPoint,
  SourceAsset,
  SpatialViewState,
} from '@albedian/contracts';
import { EMPTY_SOURCE_ASSET_ID } from './emptySession.js';

/**
 * ============================================================================
 * @albedian/observation: Checkpoints & Media Timeline Synchronization
 * Reversible CameraCheckpoint stack, Back-to-Moment restoration & sync checks
 * ============================================================================
 */

export const MAX_ALLOWED_MEDIA_DRIFT_MS = 42.0;

/**
 * Computes ICRS heliocentric/barycentric Cartesian coordinates in parsecs
 * from J2000 Right Ascension (deg), Declination (deg), and distance (pc).
 */
export function computeCartesianParsecICRS(
  raDeg: number,
  decDeg: number,
  distancePc: number
): [number, number, number] {
  const raRad = (raDeg * Math.PI) / 180;
  const decRad = (decDeg * Math.PI) / 180;
  const x = Number((distancePc * Math.cos(decRad) * Math.cos(raRad)).toFixed(6));
  const y = Number((distancePc * Math.cos(decRad) * Math.sin(raRad)).toFixed(6));
  const z = Number((distancePc * Math.sin(decRad)).toFixed(6));
  return [x, y, z];
}

/**
 * Creates a snapshot `CameraCheckpoint` from the current `ObservationSession`.
 */
export function createCameraCheckpoint(
  session: ObservationSession,
  label = 'Saved Observation Moment'
): CameraCheckpoint {
  return CameraCheckpointSchema.parse({
    id: `chk_${session.id}_rev_${session.viewRevision}_${session.navigationHistory.checkpoints.length}`,
    label,
    createdAtMs: Math.round(session.currentSourceTimeSec * 1000),
    sourceAssetId: session.sourceAsset?.id ?? EMPTY_SOURCE_ASSET_ID,
    sourceTimeSec: session.currentSourceTimeSec,
    playbackWasPlaying: session.isPlaying,
    audioGain: session.audioGain,
    mediaVariantId: session.activeVariantId,
    cropTransform: { ...session.cropTransform },
    selectedFindingId: session.selectedFindingId,
    navigationState: session.machines.navigation,
    spatialView: structuredClone(session.spatialView),
    viewRevision: session.viewRevision,
  });
}

/**
 * Transitions one of the 4+1 orthogonal state machines without mutating the other four.
 */
export function transitionOrthogonalMachine<K extends keyof ObservationSession['machines']>(
  session: ObservationSession,
  machine: K,
  nextState: ObservationSession['machines'][K]
): ObservationSession {
  return {
    ...session,
    machines: {
      ...session.machines,
      [machine]: nextState,
    },
  };
}

/**
 * Synchronizes `anchorMomentCheckpoint` with the session's initial source state
 * if the session is still at its initial untouched revision before departing `source`.
 */
export function syncAnchorCheckpointIfInitial(session: ObservationSession): ObservationSession {
  const anchor = session.navigationHistory.anchorMomentCheckpoint;
  if (
    session.navigationHistory.checkpoints.length === 1 &&
    session.viewRevision === anchor.viewRevision &&
    session.machines.navigation === 'source'
  ) {
    const syncedAnchor: CameraCheckpoint = {
      ...anchor,
      sourceAssetId: session.sourceAsset?.id ?? EMPTY_SOURCE_ASSET_ID,
      sourceTimeSec: session.currentSourceTimeSec,
      playbackWasPlaying: session.isPlaying,
      audioGain: session.audioGain,
      mediaVariantId: session.activeVariantId,
      cropTransform: { ...session.cropTransform },
      selectedFindingId: session.selectedFindingId,
      navigationState: session.machines.navigation,
      spatialView: structuredClone(session.spatialView),
      viewRevision: session.viewRevision,
    };
    return {
      ...session,
      navigationHistory: {
        anchorMomentCheckpoint: syncedAnchor,
        checkpoints: [syncedAnchor],
        cursorIndex: 0,
      },
    };
  }
  return session;
}

/**
 * Pushes an immutable `CameraCheckpoint` onto `navigationHistory.checkpoints`.
 */
export function pushCheckpointIfDepartingSource(
  session: ObservationSession,
  label: string
): ObservationSession['navigationHistory'] {
  const checkpoint = createCameraCheckpoint(session, label);
  const nextCheckpoints = [...session.navigationHistory.checkpoints, checkpoint];
  return {
    anchorMomentCheckpoint: session.navigationHistory.anchorMomentCheckpoint,
    checkpoints: nextCheckpoints,
    cursorIndex: nextCheckpoints.length - 1,
  };
}

/**
 * Whether a checkpoint was saved while the photo itself was on screen: on the source, or in the compare wipe
 * opened over the photo (media.compare from the sky or 3D space keeps that view, so the photo is not shown).
 */
export function savedOnRecording(checkpoint: Pick<CameraCheckpoint, 'navigationState' | 'spatialView'>): boolean {
  return (
    checkpoint.navigationState === 'source' ||
    (checkpoint.navigationState === 'comparison' && checkpoint.spatialView.mode === 'source_2d')
  );
}

/**
 * The moment Back to Moment returns to, by button, key or voice: the latest checkpoint at or before the
 * history cursor that was saved while this session's photo was on screen (savedOnRecording; AGENTS.md:
 * leaving the source saves the moment, selection included, and Back to Moment restores it; sky.show and
 * space.enter save it when leaving the wipe too), else the session's anchor. A checkpoint saved on another
 * recording is never the moment to return to.
 */
export function returnCheckpointOf(session: ObservationSession): CameraCheckpoint | undefined {
  const history = session.navigationHistory;
  if (!history) return undefined;
  const assetId = session.sourceAsset?.id;
  for (let i = Math.min(history.cursorIndex, history.checkpoints.length - 1); i >= 0; i--) {
    const checkpoint = history.checkpoints[i];
    if (checkpoint && savedOnRecording(checkpoint) && checkpoint.sourceAssetId === assetId) return checkpoint;
  }
  return history.anchorMomentCheckpoint;
}

/**
 * Restores an `ObservationSession` to a saved `CameraCheckpoint` (`Back to Moment`).
 */
export function restoreSessionToCheckpoint(
  baseSession: ObservationSession,
  nextRevision: number,
  targetCheckpoint: CameraCheckpoint,
  resetToSourceAnchor: boolean,
  remainingCheckpoints: CameraCheckpoint[],
  statusText: string
): ObservationSession {
  const restoredSpatialView: SpatialViewState = resetToSourceAnchor
    ? {
        ...structuredClone(targetCheckpoint.spatialView),
        mode: 'source_2d',
        transitionPhase: 'at_source',
        transitionProgress: 0,
        activeTransitionId: undefined,
      }
    : structuredClone(targetCheckpoint.spatialView);

  return {
    ...baseSession,
    viewRevision: nextRevision,
    currentSourceTimeSec: targetCheckpoint.sourceTimeSec,
    isPlaying: targetCheckpoint.playbackWasPlaying,
    audioGain: targetCheckpoint.audioGain,
    activeVariantId: targetCheckpoint.mediaVariantId,
    compareVariantId: resetToSourceAnchor ? undefined : baseSession.compareVariantId,
    cropTransform: { ...targetCheckpoint.cropTransform },
    selectedFindingId: targetCheckpoint.selectedFindingId,
    focusedFindingId: targetCheckpoint.selectedFindingId,
    machines: {
      ...baseSession.machines,
      navigation: resetToSourceAnchor ? 'source' : targetCheckpoint.navigationState,
    },
    spatialView: restoredSpatialView,
    navigationHistory: {
      anchorMomentCheckpoint: resetToSourceAnchor
        ? targetCheckpoint
        : baseSession.navigationHistory.anchorMomentCheckpoint,
      checkpoints: remainingCheckpoints,
      cursorIndex: Math.max(0, remainingCheckpoints.length - 1),
    },
    lastStatusBanner: statusText,
  };
}

/**
 * Computes normalized device coordinates (`[-1, 1]`) for a finding's pixel detection, in the photo as it is
 * shown: at the centre of its pixel (the detector's centroids put pixel centres at whole numbers) and turned
 * by the asset's display orientation, the same point the photo stage draws its marker at.
 */
export function computeStarAnchorNdc(
  finding: CelestialFinding,
  sourceAsset: SourceAsset | undefined
): [number, number] {
  if (!finding.sourceDetection || !sourceAsset) {
    return [0, 0];
  }
  const orientation = sourceAsset.displayOrientation;
  const shown = orientedSize(sourceAsset.width, sourceAsset.height, orientation);
  const p = orientStoredPoint(
    finding.sourceDetection.xPx + 0.5,
    finding.sourceDetection.yPx + 0.5,
    sourceAsset.width,
    sourceAsset.height,
    orientation
  );
  const ndcX = Number(((p.xPx / shown.widthPx) * 2 - 1).toFixed(4));
  const ndcY = Number((1 - (p.yPx / shown.heightPx) * 2).toFixed(4));
  return [Math.max(-1, Math.min(1, ndcX)), Math.max(-1, Math.min(1, ndcY))];
}

/**
 * Validates that a derived `MediaVariant` remains synchronized with the immutable
 * `SourceAsset` timeline (`PBT-SYNC-1`):
 * - Frame timestamps are strictly monotonic and finite
 * - Audio stream preservation matches source audio presence
 * - Audio/duration sync drift is bounded (`<= 42.0 ms`)
 */
export function validateMediaTimelineSync(
  source: SourceAsset,
  variant: MediaVariant
): {
  valid: boolean;
  monotonic: boolean;
  maxDriftMs: number;
  audioPreserved: boolean;
} {
  const timestamps = variant.frameTimestampsMs;
  let monotonic = timestamps.length > 0;
  for (let i = 0; i < timestamps.length; i++) {
    const t = timestamps[i];
    if (t === undefined || !Number.isFinite(t) || t < 0) {
      monotonic = false;
      break;
    }
    const prev = i > 0 ? timestamps[i - 1] : undefined;
    if (prev !== undefined && t <= prev) {
      monotonic = false;
      break;
    }
  }

  const hasSourceAudio = source.audioStreams.length > 0;
  const audioPreserved = !hasSourceAudio ? true : variant.audioStreamCopiedFromSource === true;
  // Fail closed when a recording has audio and the variant carries no measured audio sync offset
  const hasMeasuredAudioOffset =
    !hasSourceAudio ||
    (variant.audioSyncOffsetMs !== undefined && Number.isFinite(variant.audioSyncOffsetMs));

  const durationDriftMs = Math.abs(variant.durationMs - source.durationMs);
  const audioOffsetDriftMs =
    variant.audioSyncOffsetMs !== undefined && Number.isFinite(variant.audioSyncOffsetMs)
      ? Math.abs(variant.audioSyncOffsetMs)
      : 0;
  const lastFrameTs = timestamps.length > 0 ? timestamps[timestamps.length - 1] : undefined;
  const lastFrameDriftMs =
    lastFrameTs !== undefined && lastFrameTs > source.durationMs
      ? lastFrameTs - source.durationMs
      : 0;

  const maxDriftMs = Number(
    Math.max(durationDriftMs, audioOffsetDriftMs, lastFrameDriftMs).toFixed(4)
  );

  const sameSourceAsset = variant.sourceAssetId === source.id;
  const valid =
    sameSourceAsset &&
    monotonic &&
    audioPreserved &&
    hasMeasuredAudioOffset &&
    maxDriftMs <= MAX_ALLOWED_MEDIA_DRIFT_MS;

  return {
    valid,
    monotonic,
    maxDriftMs,
    audioPreserved,
  };
}
