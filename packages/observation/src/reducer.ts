import {
  ActionOutcome,
  ActionOutcomeSchema,
  AlbedianAction,
  ObservationSession,
} from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';
import {
  computeCartesianParsecICRS,
  computeStarAnchorNdc,
  pushCheckpointIfDepartingSource,
  restoreSessionToCheckpoint,
  syncAnchorCheckpointIfInitial,
} from './checkpoints.js';

/**
 * ============================================================================
 * @albedian/observation — Revision-Gated Action Dispatcher & State Reducer
 * ============================================================================
 */

function rejectAction(
  session: ObservationSession,
  actionId: string,
  reason:
    | 'stale_revision'
    | 'unresolved_sky'
    | 'ambiguous_target'
    | 'no_distance_estimate'
    | 'invalid_epoch'
    | 'missing_source'
    | 'unsafe_synthetic_edit',
  explanation: string,
  nextActions: string[]
): { session: ObservationSession; outcome: ActionOutcome } {
  return {
    session,
    outcome: ActionOutcomeSchema.parse({
      status: 'rejected',
      actionId,
      reason,
      explanation,
      nextActions,
    }),
  };
}

function completeAction(
  session: ObservationSession,
  actionId: string,
  newStateVersion: number,
  spokenFeedback: string,
  visualStatus: string
): { session: ObservationSession; outcome: ActionOutcome } {
  return {
    session,
    outcome: ActionOutcomeSchema.parse({
      status: 'completed',
      actionId,
      newStateVersion,
      spokenFeedback,
      visualStatus,
    }),
  };
}

/**
 * Authoritative revision-checked `AlbedianAction` dispatcher and reducer.
 * - Rejects any action with `expectedViewRevision !== session.viewRevision` (`reason: 'stale_revision'`)
 *   and leaves `ObservationSession` untouched (`PBT-VOICE-1`).
 * - Enforces scientific calibration and distance prerequisites (`space.enter` requires a
 *   `catalog_matched` finding with a valid `inferredDistancePc`).
 * - Pushes immutable `CameraCheckpoint`s when departing `source`/`comparison` for `sky_registered` or `space`.
 * - Restores `anchorMomentCheckpoint` identically on `source.return` (`PBT-NAV-1`).
 */
export function dispatchAlbedianAction(
  session: ObservationSession,
  action: AlbedianAction
): { session: ObservationSession; outcome: ActionOutcome } {
  if (action.expectedViewRevision !== session.viewRevision) {
    return rejectAction(
      session,
      action.requestId,
      'stale_revision',
      `Stale action revision: expected ${action.expectedViewRevision}, but session is at revision ${session.viewRevision}.`,
      ['Rebuild ActionContext from latest ObservationSession and retry']
    );
  }

  const baseSession = syncAnchorCheckpointIfInitial(session);
  const nextRevision = baseSession.viewRevision + 1;

  switch (action.type) {
    case 'media.restore': {
      if (action.sourceAssetId !== baseSession.sourceAsset.id) {
        return rejectAction(
          session,
          action.requestId,
          'missing_source',
          `SourceAsset "${action.sourceAssetId}" does not match active session asset "${baseSession.sourceAsset.id}".`,
          ['Dispatch media.restore with the active SourceAsset ID']
        );
      }

      if ((action.constraints as { allowSyntheticSky?: boolean }).allowSyntheticSky !== false) {
        return rejectAction(
          session,
          action.requestId,
          'unsafe_synthetic_edit',
          'Synthetic sky generation is forbidden by Albedian fidelity policy.',
          ['Use star_protected or temporal restoration profiles']
        );
      }

      const matchingGraph = baseSession.processingGraphs.find(
        (g) =>
          g.profileName === action.profile ||
          (action.profile === 'natural' && g.profileName === 'star_protected')
      );
      const matchingVariant =
        baseSession.mediaVariants.find((v) => v.graphId === matchingGraph?.id) ??
        baseSession.mediaVariants[baseSession.mediaVariants.length - 1];

      if (!matchingVariant) {
        return rejectAction(
          session,
          action.requestId,
          'missing_source',
          `No MediaVariant available for profile "${action.profile}".`,
          ['Run restoration pipeline first']
        );
      }

      const statusText = `Applied ${matchingVariant.label} (${matchingVariant.summaryNote})`;
      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          activeVariantId: matchingVariant.id,
          machines: { ...baseSession.machines, restoration: 'ready', voice: 'listening' },
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Restored sky clarity using ${matchingVariant.label} while preserving people and original audio.`,
        statusText
      );
    }

    case 'media.compare': {
      const variantA = baseSession.mediaVariants.find((v) => v.id === action.variantAId);
      const variantB = baseSession.mediaVariants.find((v) => v.id === action.variantBId);
      if (!variantA || !variantB) {
        return rejectAction(
          session,
          action.requestId,
          'missing_source',
          `Cannot compare missing media variants (${action.variantAId} vs ${action.variantBId}).`,
          ['Select valid media variants from session.mediaVariants']
        );
      }

      const statusText = `Comparing ${variantA.label} with ${variantB.label} at ${baseSession.currentSourceTimeSec.toFixed(1)}s.`;
      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          activeVariantId: variantB.id,
          compareVariantId: variantA.id,
          compareWipePosition: 0.5,
          machines: { ...baseSession.machines, navigation: 'comparison', voice: 'listening' },
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Comparing ${variantA.label} and ${variantB.label}.`,
        statusText
      );
    }

    case 'media.seek': {
      const maxTimeSec = baseSession.sourceAsset.durationMs / 1000;
      const clampedSec = Math.min(Math.max(0, action.sourceTimeSec), maxTimeSec);
      const statusText = `Seeked to ${clampedSec.toFixed(2)}s (audio stream synchronized).`;
      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          currentSourceTimeSec: clampedSec,
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Seeked to ${clampedSec.toFixed(1)} seconds.`,
        statusText
      );
    }

    case 'sky.show': {
      if (
        baseSession.machines.calibration === 'unresolved' ||
        baseSession.observationSolution.status === 'unresolved'
      ) {
        return rejectAction(
          session,
          action.requestId,
          'unresolved_sky',
          'Sky registration is unresolved: insufficient star detections and no timestamp/location fallback.',
          ['Provide capture timestamp and location for predicted sky model']
        );
      }

      if (
        action.mode === 'measured' &&
        (baseSession.machines.calibration !== 'calibrated' ||
          baseSession.observationSolution.status !== 'calibrated')
      ) {
        return rejectAction(
          session,
          action.requestId,
          'unresolved_sky',
          `Direct measured pixel registration requires a calibrated WCS solution (current status: ${baseSession.observationSolution.status}).`,
          ['Show predicted sky model instead (`mode: "predicted"`)']
        );
      }

      const shouldPushCheckpoint =
        baseSession.machines.navigation === 'source' ||
        baseSession.machines.navigation === 'comparison';
      const nextHistory = shouldPushCheckpoint
        ? pushCheckpointIfDepartingSource(baseSession, 'Departure from Source to Registered Sky')
        : baseSession.navigationHistory;

      const isCalibrated = baseSession.observationSolution.status === 'calibrated';
      const statusText = isCalibrated
        ? `Calibrated sky registered (${baseSession.observationSolution.quality.heldOutInliers} held-out stars, ${baseSession.observationSolution.quality.medianResidualPx.toFixed(2)} px median residual).`
        : 'Showing predicted sky model from recorded timestamp and location (separate from source pixels).';

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          machines: { ...baseSession.machines, navigation: 'sky_registered', voice: 'listening' },
          spatialView: {
            ...baseSession.spatialView,
            mode: 'registered_sky',
            transitionPhase: 'at_registered_sky',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            activeOverlays: {
              ...baseSession.spatialView.activeOverlays,
              observedMarkers: isCalibrated,
              catalogMatchedLabels: isCalibrated,
              predictedEphemerides: true,
              constellationLines: true,
            },
          },
          navigationHistory: nextHistory,
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        statusText,
        statusText
      );
    }

    case 'finding.select': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return rejectAction(
          session,
          action.requestId,
          'ambiguous_target',
          `CelestialFinding "${action.findingId}" does not exist in active ObservationSolution.`,
          ['Select a visible star or planet marker']
        );
      }

      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const anchorNdc = computeStarAnchorNdc(finding, baseSession.sourceAsset);
      const statusText = `${finding.title} (${finding.subtitle}) · ${badge.label} · ${distInfo.lightYearText}`;

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          spatialView: { ...baseSession.spatialView, lockedAnchorNdc: anchorNdc },
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `${finding.title}, ${finding.subtitle}. ${badge.label}. ${distInfo.lightYearText}.`,
        statusText
      );
    }

    case 'finding.describe': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return rejectAction(
          session,
          action.requestId,
          'ambiguous_target',
          `Cannot describe unknown finding "${action.findingId}".`,
          ['Select a finding first']
        );
      }

      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const segments: string[] = [`${finding.title} (${finding.subtitle})`];

      if (action.fields.includes('distance')) {
        segments.push(`Distance: ${distInfo.lightYearText} [${distInfo.parsecText}]`);
      }
      if (action.fields.includes('type') && finding.catalogRef?.spectralType) {
        segments.push(`Spectral type: ${finding.catalogRef.spectralType}`);
      }
      if (
        action.fields.includes('motion') &&
        finding.catalogRef?.properMotionRaMasYr !== undefined
      ) {
        segments.push(
          `Proper motion: (${finding.catalogRef.properMotionRaMasYr}, ${finding.catalogRef.properMotionDecMasYr ?? 0}) mas/yr`
        );
      }
      if (action.fields.includes('evidence')) {
        segments.push(`Evidence: ${badge.label} — ${badge.disclosure}`);
      }
      if (action.fields.includes('behind_sightline')) {
        segments.push(
          `Sightline cone active along RA ${finding.skyPosition.raDeg.toFixed(2)}°, Dec ${finding.skyPosition.decDeg.toFixed(2)}° showing deeper catalog background.`
        );
      }

      const descriptionText = segments.join(' · ');
      const enableSightline =
        action.fields.includes('behind_sightline') ||
        baseSession.spatialView.activeOverlays.sightlineCone;

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          spatialView: {
            ...baseSession.spatialView,
            activeOverlays: { ...baseSession.spatialView.activeOverlays, sightlineCone: enableSightline },
          },
          lastStatusBanner: descriptionText,
        },
        action.requestId,
        nextRevision,
        descriptionText,
        descriptionText
      );
    }

    case 'space.enter': {
      if (
        baseSession.machines.calibration === 'unresolved' ||
        baseSession.observationSolution.status === 'unresolved'
      ) {
        return rejectAction(
          session,
          action.requestId,
          'unresolved_sky',
          'Cannot enter 3D catalog space from an unresolved sky.',
          ['Calibrate sky first']
        );
      }

      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.findingId
      );
      if (!finding) {
        return rejectAction(
          session,
          action.requestId,
          'ambiguous_target',
          `Target finding "${action.findingId}" not found in active ObservationSolution.`,
          ['Select a catalog-matched star first']
        );
      }

      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (
        finding.evidenceClass !== 'catalog_matched' ||
        !finding.inferredDistancePc ||
        !distInfo.canEnter3DSpace
      ) {
        return rejectAction(
          session,
          action.requestId,
          'no_distance_estimate',
          `${finding.title} (${finding.evidenceClass}) lacks a verified catalog parallax or Bailer-Jones distance prior required for 3D space navigation.`,
          ['Select a catalog-matched star with a verified distance interval (e.g. Betelgeuse, Rigel, Aldebaran)']
        );
      }

      const nextHistory = pushCheckpointIfDepartingSource(
        baseSession,
        `Checkpoint before entering 3D space toward ${finding.title}`
      );
      const targetCoords =
        finding.cartesianParsecICRS ??
        computeCartesianParsecICRS(
          finding.skyPosition.raDeg,
          finding.skyPosition.decDeg,
          finding.inferredDistancePc.median
        );
      const statusText = `Entered 3D Space · ${finding.title} (${distInfo.lightYearText}) · Catalog Geometry · Distance Estimates`;

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          isPlaying: false,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          machines: { ...baseSession.machines, navigation: 'space', voice: 'listening' },
          spatialView: {
            ...baseSession.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            observerOriginParsec: targetCoords,
            fovVerticalDeg: 45,
            lockedAnchorNdc: computeStarAnchorNdc(finding, baseSession.sourceAsset),
          },
          navigationHistory: nextHistory,
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Arrived near ${finding.title}, approximately ${distInfo.lightYearText} from Earth. Catalog geometry and distance estimates active.`,
        statusText
      );
    }

    case 'space.viewpoint': {
      const finding = baseSession.observationSolution.findings.find(
        (f) => f.id === action.targetFindingId
      );
      if (!finding) {
        return rejectAction(
          session,
          action.requestId,
          'ambiguous_target',
          `Cannot shift viewpoint around unknown finding "${action.targetFindingId}".`,
          ['Select a catalog-matched star first']
        );
      }

      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (
        finding.evidenceClass !== 'catalog_matched' ||
        !finding.inferredDistancePc ||
        !distInfo.canEnter3DSpace
      ) {
        return rejectAction(
          session,
          action.requestId,
          'no_distance_estimate',
          `${finding.title} lacks a catalog distance estimate for 3D viewpoint displacement.`,
          ['Select a catalog-matched star with a verified distance estimate']
        );
      }

      const nextHistory = pushCheckpointIfDepartingSource(
        baseSession,
        `Checkpoint before ${action.trajectory} viewpoint around ${finding.title}`
      );
      const baseCoords =
        finding.cartesianParsecICRS ??
        computeCartesianParsecICRS(
          finding.skyPosition.raDeg,
          finding.skyPosition.decDeg,
          finding.inferredDistancePc.median
        );
      const displacedOrigin: [number, number, number] = [
        Number((baseCoords[0] + action.offsetParsec[0]).toFixed(6)),
        Number((baseCoords[1] + action.offsetParsec[1]).toFixed(6)),
        Number((baseCoords[2] + action.offsetParsec[2]).toFixed(6)),
      ];
      const statusText = `Simulated 3D Viewpoint (${action.trajectory}) near ${finding.title} · Offset [${action.offsetParsec.join(', ')}] pc · Catalog Geometry · Distance Estimates`;

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          isPlaying: false,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          machines: { ...baseSession.machines, navigation: 'space', voice: 'listening' },
          spatialView: {
            ...baseSession.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            observerOriginParsec: displacedOrigin,
            cameraQuaternion: [0, 0.258819, 0, 0.965926],
            activeOverlays: { ...baseSession.spatialView.activeOverlays, earthDirectionVector: true },
          },
          navigationHistory: nextHistory,
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Shifted to alternative 3D viewpoint around ${finding.title}. Earth direction vector enabled.`,
        statusText
      );
    }

    case 'space.epoch': {
      if (
        !Number.isInteger(action.yearCE) ||
        action.yearCE < -10000 ||
        action.yearCE > 12000 ||
        !action.epochISO ||
        action.epochISO.trim().length < 4
      ) {
        return rejectAction(
          session,
          action.requestId,
          'invalid_epoch',
          `Epoch year ${action.yearCE} CE is outside the valid proper-motion integration bounds (-10000 to 12000 CE).`,
          ['Specify an epoch between 10,000 BCE and 12,000 CE']
        );
      }

      const shouldPushCheckpoint =
        baseSession.machines.navigation === 'source' ||
        baseSession.machines.navigation === 'comparison';
      const nextHistory = shouldPushCheckpoint
        ? pushCheckpointIfDepartingSource(baseSession, `Checkpoint before epoch change to ${action.yearCE} CE`)
        : baseSession.navigationHistory;
      const nextNavState = shouldPushCheckpoint ? 'sky_registered' : baseSession.machines.navigation;
      const statusText = `Modeled Sky · Epoch ${action.yearCE} CE (Gaia DR3 proper motion propagation)`;

      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          isPlaying: false,
          machines: { ...baseSession.machines, navigation: nextNavState, voice: 'listening' },
          spatialView: {
            ...baseSession.spatialView,
            mode: 'modeled_epoch',
            transitionPhase: nextNavState === 'space' ? 'at_space' : 'at_registered_sky',
            transitionProgress: 1,
            simulationEpochISO: action.epochISO,
          },
          navigationHistory: nextHistory,
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        `Showing modeled sky at ${action.yearCE} CE using Gaia proper motions.`,
        statusText
      );
    }

    case 'history.back': {
      if (action.mode === 'partial') {
        const fraction = Math.min(Math.max(action.fraction ?? 0.35, 0.05), 1);
        const currentOrigin = baseSession.spatialView.observerOriginParsec;
        const anchorOrigin =
          baseSession.navigationHistory.anchorMomentCheckpoint.spatialView.observerOriginParsec;
        const interpolatedOrigin: [number, number, number] = [
          Number((currentOrigin[0] * (1 - fraction) + anchorOrigin[0] * fraction).toFixed(6)),
          Number((currentOrigin[1] * (1 - fraction) + anchorOrigin[1] * fraction).toFixed(6)),
          Number((currentOrigin[2] * (1 - fraction) + anchorOrigin[2] * fraction).toFixed(6)),
        ];
        const currentFov = baseSession.spatialView.fovVerticalDeg;
        const anchorFov = baseSession.navigationHistory.anchorMomentCheckpoint.spatialView.fovVerticalDeg;
        const statusText = `Stepped back ${Math.round(fraction * 100)}% toward observation checkpoint.`;

        return completeAction(
          {
            ...baseSession,
            viewRevision: nextRevision,
            spatialView: {
              ...baseSession.spatialView,
              observerOriginParsec: interpolatedOrigin,
              fovVerticalDeg: Number((currentFov * (1 - fraction) + anchorFov * fraction).toFixed(4)),
              transitionProgress: Number((1 - fraction).toFixed(4)),
            },
            lastStatusBanner: statusText,
          },
          action.requestId,
          nextRevision,
          statusText,
          statusText
        );
      }

      const checkpoints = baseSession.navigationHistory.checkpoints;
      const lastCheckpoint = checkpoints[checkpoints.length - 1];
      const targetCheckpoint =
        checkpoints.length > 1 && lastCheckpoint
          ? lastCheckpoint
          : baseSession.navigationHistory.anchorMomentCheckpoint;
      const remainingCheckpoints =
        checkpoints.length > 1
          ? checkpoints.slice(0, checkpoints.length - 1)
          : [baseSession.navigationHistory.anchorMomentCheckpoint];
      const statusText = `Restored checkpoint: ${targetCheckpoint.label}`;

      return completeAction(
        restoreSessionToCheckpoint(
          baseSession,
          nextRevision,
          targetCheckpoint,
          false,
          remainingCheckpoints,
          statusText
        ),
        action.requestId,
        nextRevision,
        statusText,
        statusText
      );
    }

    case 'navigation.cancel': {
      const settledNavState =
        baseSession.machines.navigation === 'transitioning' ||
        baseSession.machines.navigation === 'returning'
          ? baseSession.spatialView.mode === 'source_2d'
            ? 'source'
            : 'sky_registered'
          : baseSession.machines.navigation;
      const statusText = 'Navigation transition canceled; camera settled at current pose.';

      return {
        session: {
          ...baseSession,
          viewRevision: nextRevision,
          machines: { ...baseSession.machines, navigation: settledNavState, voice: 'interrupted' },
          spatialView: { ...baseSession.spatialView, activeTransitionId: undefined },
          lastStatusBanner: statusText,
        },
        outcome: ActionOutcomeSchema.parse({
          status: 'canceled',
          actionId: action.requestId,
          settledStateVersion: nextRevision,
          visualStatus: statusText,
        }),
      };
    }

    case 'ui.overlay': {
      const statusText = `${action.enabled ? 'Enabled' : 'Disabled'} overlay: ${action.overlay}.`;
      return completeAction(
        {
          ...baseSession,
          viewRevision: nextRevision,
          spatialView: {
            ...baseSession.spatialView,
            activeOverlays: { ...baseSession.spatialView.activeOverlays, [action.overlay]: action.enabled },
          },
          lastStatusBanner: statusText,
        },
        action.requestId,
        nextRevision,
        statusText,
        statusText
      );
    }

    case 'source.return': {
      const allCheckpoints = [
        baseSession.navigationHistory.anchorMomentCheckpoint,
        ...baseSession.navigationHistory.checkpoints,
      ];
      const targetCheckpoint = action.checkpointId
        ? (allCheckpoints.find((c) => c.id === action.checkpointId) ??
          baseSession.navigationHistory.anchorMomentCheckpoint)
        : baseSession.navigationHistory.anchorMomentCheckpoint;
      const statusText = `Returned to Moment (${targetCheckpoint.sourceTimeSec.toFixed(1)}s) · Audio & frame restored.`;

      return completeAction(
        restoreSessionToCheckpoint(
          baseSession,
          nextRevision,
          targetCheckpoint,
          true,
          [targetCheckpoint],
          statusText
        ),
        action.requestId,
        nextRevision,
        'Returned to your original recorded moment.',
        statusText
      );
    }
  }
}

/**
 * Alias for `dispatchAlbedianAction` per architectural design specification.
 */
export const validateAndDispatchAction = dispatchAlbedianAction;
