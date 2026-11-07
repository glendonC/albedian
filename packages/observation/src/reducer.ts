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
 * @albedian/observation: Revision-Gated Action Dispatcher & State Reducer
 * ============================================================================
 */

type RejectionReason = Extract<ActionOutcome, { status: 'rejected' }>['reason'];

function rejectAction(
  session: ObservationSession,
  actionId: string,
  reason: RejectionReason,
  explanation: string,
  nextActions: string[]
): { session: ObservationSession; outcome: ActionOutcome } {
  return {
    session,
    outcome: ActionOutcomeSchema.parse({ status: 'rejected', actionId, reason, explanation, nextActions }),
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
    outcome: ActionOutcomeSchema.parse({ status: 'completed', actionId, newStateVersion, spokenFeedback, visualStatus }),
  };
}

/**
 * Authoritative revision-checked `AlbedianAction` dispatcher and reducer.
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

  const base = syncAnchorCheckpointIfInitial(session);
  const nextRev = base.viewRevision + 1;

  switch (action.type) {
    case 'media.restore': {
      if (!base.sourceAsset || action.sourceAssetId !== base.sourceAsset.id) {
        return rejectAction(session, action.requestId, 'missing_source', `SourceAsset "${action.sourceAssetId}" does not match active session asset "${base.sourceAsset?.id ?? 'none'}".`, ['Dispatch media.restore with the active SourceAsset ID']);
      }
      if ((action.constraints as { allowSyntheticSky?: boolean }).allowSyntheticSky !== false) {
        return rejectAction(session, action.requestId, 'unsafe_synthetic_edit', 'Synthetic sky generation is forbidden by Albedian fidelity policy.', ['Use star_protected or temporal restoration profiles']);
      }
      const graph = base.processingGraphs.find((g) => g.profileName === action.profile || (action.profile === 'natural' && g.profileName === 'star_protected'));
      const variant = base.mediaVariants.find((v) => v.graphId === graph?.id) ?? base.mediaVariants[base.mediaVariants.length - 1];
      if (!variant) {
        return rejectAction(session, action.requestId, 'missing_source', `No MediaVariant available for profile "${action.profile}".`, ['Run restoration pipeline first']);
      }
      const status = `Applied ${variant.label} (${variant.summaryNote})`;
      return completeAction(
        { ...base, viewRevision: nextRev, activeVariantId: variant.id, machines: { ...base.machines, restoration: 'ready', voice: 'listening' }, lastStatusBanner: status },
        action.requestId,
        nextRev,
        `Restored sky clarity using ${variant.label} while preserving people and original audio.`,
        status
      );
    }

    case 'media.compare': {
      const variantA = base.mediaVariants.find((v) => v.id === action.variantAId);
      const variantB = base.mediaVariants.find((v) => v.id === action.variantBId);
      if (!variantA || !variantB) {
        return rejectAction(session, action.requestId, 'missing_source', `Cannot compare missing media variants (${action.variantAId} vs ${action.variantBId}).`, ['Select valid media variants from session.mediaVariants']);
      }
      const status = `Comparing ${variantA.label} with ${variantB.label} at ${base.currentSourceTimeSec.toFixed(1)}s.`;
      return completeAction(
        { ...base, viewRevision: nextRev, activeVariantId: variantB.id, compareVariantId: variantA.id, compareWipePosition: 0.5, machines: { ...base.machines, navigation: 'comparison', voice: 'listening' }, lastStatusBanner: status },
        action.requestId,
        nextRev,
        `Comparing ${variantA.label} and ${variantB.label}.`,
        status
      );
    }

    case 'media.seek': {
      if (!base.sourceAsset) {
        return rejectAction(session, action.requestId, 'missing_source', 'Cannot seek when no SourceAsset is loaded.', ['Import a recording first']);
      }
      const clampedSec = Math.min(Math.max(0, action.sourceTimeSec), base.sourceAsset.durationMs / 1000);
      const status = `Seeked to ${clampedSec.toFixed(2)}s (audio stream synchronized).`;
      return completeAction(
        { ...base, viewRevision: nextRev, currentSourceTimeSec: clampedSec, lastStatusBanner: status },
        action.requestId,
        nextRev,
        `Seeked to ${clampedSec.toFixed(1)} seconds.`,
        status
      );
    }

    case 'sky.show': {
      if (!base.observationSolution || base.machines.calibration === 'unresolved' || base.observationSolution.status === 'unresolved') {
        return rejectAction(session, action.requestId, 'unresolved_sky', 'Sky registration is unresolved: insufficient star detections and no timestamp/location fallback.', ['Provide capture timestamp and location for predicted sky model']);
      }
      if (action.mode === 'measured' && (base.machines.calibration !== 'calibrated' || base.observationSolution.status !== 'calibrated')) {
        return rejectAction(session, action.requestId, 'unresolved_sky', `Direct measured pixel registration requires a calibrated WCS solution (current status: ${base.observationSolution.status}).`, ['Show predicted sky model instead (`mode: "predicted"`)']);
      }
      const fromSource = base.machines.navigation === 'source' || base.machines.navigation === 'comparison';
      const nextHistory = fromSource ? pushCheckpointIfDepartingSource(base, 'Departure from Source to Registered Sky') : base.navigationHistory;
      const isCal = base.observationSolution.status === 'calibrated';
      const status = isCal
        ? `Calibrated sky registered (${base.observationSolution.quality.heldOutInliers} held-out stars${base.observationSolution.quality.medianResidualPx !== undefined ? `, ${base.observationSolution.quality.medianResidualPx.toFixed(2)} px median residual` : ''}).`
        : 'Showing predicted sky model from recorded timestamp and location (separate from source pixels).';

      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          machines: { ...base.machines, navigation: 'sky_registered', voice: 'listening' },
          spatialView: {
            ...base.spatialView,
            mode: 'registered_sky',
            transitionPhase: 'at_registered_sky',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            activeOverlays: { ...base.spatialView.activeOverlays, observedMarkers: isCal, catalogMatchedLabels: isCal, predictedEphemerides: true, constellationLines: true },
          },
          navigationHistory: nextHistory,
          lastStatusBanner: status,
        },
        action.requestId,
        nextRev,
        status,
        status
      );
    }

    case 'finding.select': {
      const finding = base.observationSolution?.findings.find((f) => f.id === action.findingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', `CelestialFinding "${action.findingId}" does not exist in active ObservationSolution.`, ['Select a visible star or planet marker']);
      }
      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const status = `${finding.title} (${finding.subtitle}) · ${badge.label} · ${distInfo.lightYearText}`;
      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          spatialView: { ...base.spatialView, lockedAnchorNdc: computeStarAnchorNdc(finding, base.sourceAsset) },
          lastStatusBanner: status,
        },
        action.requestId,
        nextRev,
        `${finding.title}, ${finding.subtitle}. ${badge.label}. ${distInfo.lightYearText}.`,
        status
      );
    }

    case 'finding.describe': {
      const finding = base.observationSolution?.findings.find((f) => f.id === action.findingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', `Cannot describe unknown finding "${action.findingId}".`, ['Select a finding first']);
      }
      const badge = getEpistemicBadgeDescriptor(finding.evidenceClass);
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      const segments: string[] = [`${finding.title} (${finding.subtitle})`];
      if (action.fields.includes('distance')) segments.push(`Distance: ${distInfo.lightYearText} [${distInfo.parsecText}]`);
      if (action.fields.includes('type') && finding.catalogRef?.spectralType) segments.push(`Spectral type: ${finding.catalogRef.spectralType}`);
      if (action.fields.includes('motion') && finding.catalogRef?.properMotionRaMasYr !== undefined) {
        segments.push(`Proper motion: (${finding.catalogRef.properMotionRaMasYr}, ${finding.catalogRef.properMotionDecMasYr ?? 0}) mas/yr`);
      }
      if (action.fields.includes('evidence')) segments.push(`Evidence: ${badge.label}. ${badge.disclosure}`);
      // The view does not draw a sightline cone yet, so behind_sightline reports the sky direction without
      // turning on activeOverlays.sightlineCone; a pixel-only detection has no sky direction to report
      const sky = finding.skyPosition;
      if (action.fields.includes('behind_sightline')) {
        segments.push(
          sky
            ? `Sightline along RA ${sky.raDeg.toFixed(2)}°, Dec ${sky.decDeg.toFixed(2)}° (the view does not draw a sightline cone yet).`
            : `No sightline: ${finding.title} has a pixel position only, with no verified WCS to place it on the sky.`
        );
      }
      const text = segments.join(' · ');
      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          lastStatusBanner: text,
        },
        action.requestId,
        nextRev,
        text,
        text
      );
    }

    case 'space.enter': {
      if (!base.observationSolution || base.machines.calibration === 'unresolved' || base.observationSolution.status === 'unresolved') {
        return rejectAction(session, action.requestId, 'unresolved_sky', 'Cannot enter 3D catalog space from an unresolved sky.', ['Calibrate sky first']);
      }
      const finding = base.observationSolution.findings.find((f) => f.id === action.findingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', `Target finding "${action.findingId}" not found in active ObservationSolution.`, ['Select a catalog-matched star first']);
      }
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (finding.evidenceClass !== 'catalog_matched' || !finding.inferredDistancePc || !distInfo.canEnter3DSpace) {
        return rejectAction(session, action.requestId, 'no_distance_estimate', `${finding.title} (${finding.evidenceClass}) lacks a verified catalog parallax or Bailer-Jones distance prior required for 3D space navigation.`, ['Select a catalog-matched star with a verified distance interval']);
      }
      const nextHistory = pushCheckpointIfDepartingSource(base, `Checkpoint before entering 3D space toward ${finding.title}`);
      const targetCoords = finding.cartesianParsecICRS ?? computeCartesianParsecICRS(finding.skyPosition.raDeg, finding.skyPosition.decDeg, finding.inferredDistancePc.median);
      const status = `Entered 3D Space · ${finding.title} (${distInfo.lightYearText}) · Catalog Geometry · Distance Estimates`;
      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          isPlaying: false,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          machines: { ...base.machines, navigation: 'space', voice: 'listening' },
          spatialView: {
            ...base.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            observerOriginParsec: targetCoords,
            fovVerticalDeg: 45,
            lockedAnchorNdc: computeStarAnchorNdc(finding, base.sourceAsset),
          },
          navigationHistory: nextHistory,
          lastStatusBanner: status,
        },
        action.requestId,
        nextRev,
        `Arrived near ${finding.title}, approximately ${distInfo.lightYearText} from Earth. Catalog geometry and distance estimates active.`,
        status
      );
    }

    case 'space.viewpoint': {
      const finding = base.observationSolution?.findings.find((f) => f.id === action.targetFindingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', `Cannot shift viewpoint around unknown finding "${action.targetFindingId}".`, ['Select a catalog-matched star first']);
      }
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      if (finding.evidenceClass !== 'catalog_matched' || !finding.inferredDistancePc || !distInfo.canEnter3DSpace) {
        return rejectAction(session, action.requestId, 'no_distance_estimate', `${finding.title} lacks a catalog distance estimate for 3D viewpoint displacement.`, ['Select a catalog-matched star with a verified distance estimate']);
      }
      const nextHistory = pushCheckpointIfDepartingSource(base, `Checkpoint before ${action.trajectory} viewpoint around ${finding.title}`);
      const baseCoords = finding.cartesianParsecICRS ?? computeCartesianParsecICRS(finding.skyPosition.raDeg, finding.skyPosition.decDeg, finding.inferredDistancePc.median);
      const displacedOrigin: [number, number, number] = [
        Number((baseCoords[0] + action.offsetParsec[0]).toFixed(6)),
        Number((baseCoords[1] + action.offsetParsec[1]).toFixed(6)),
        Number((baseCoords[2] + action.offsetParsec[2]).toFixed(6)),
      ];
      const status = `Simulated 3D Viewpoint (${action.trajectory}) near ${finding.title} · Offset [${action.offsetParsec.join(', ')}] pc · Catalog Geometry · Distance Estimates`;
      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          isPlaying: false,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          machines: { ...base.machines, navigation: 'space', voice: 'listening' },
          spatialView: {
            ...base.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            observerOriginParsec: displacedOrigin,
            cameraQuaternion: [0, 0.258819, 0, 0.965926],
            activeOverlays: { ...base.spatialView.activeOverlays, earthDirectionVector: true },
          },
          navigationHistory: nextHistory,
          lastStatusBanner: status,
        },
        action.requestId,
        nextRev,
        `Shifted to alternative 3D viewpoint around ${finding.title}. Earth direction vector enabled.`,
        status
      );
    }

    case 'history.back': {
      if (action.mode === 'partial') {
        const fraction = Math.min(Math.max(action.fraction ?? 0.35, 0.05), 1);
        const cur = base.spatialView.observerOriginParsec;
        const anc = base.navigationHistory.anchorMomentCheckpoint.spatialView.observerOriginParsec;
        const interpolatedOrigin: [number, number, number] = [
          Number((cur[0] * (1 - fraction) + anc[0] * fraction).toFixed(6)),
          Number((cur[1] * (1 - fraction) + anc[1] * fraction).toFixed(6)),
          Number((cur[2] * (1 - fraction) + anc[2] * fraction).toFixed(6)),
        ];
        const curFov = base.spatialView.fovVerticalDeg;
        const ancFov = base.navigationHistory.anchorMomentCheckpoint.spatialView.fovVerticalDeg;
        const status = `Stepped back ${Math.round(fraction * 100)}% toward observation checkpoint.`;
        return completeAction(
          {
            ...base,
            viewRevision: nextRev,
            spatialView: {
              ...base.spatialView,
              observerOriginParsec: interpolatedOrigin,
              fovVerticalDeg: Number((curFov * (1 - fraction) + ancFov * fraction).toFixed(4)),
              transitionProgress: Number((1 - fraction).toFixed(4)),
            },
            lastStatusBanner: status,
          },
          action.requestId,
          nextRev,
          status,
          status
        );
      }

      const checkpoints = base.navigationHistory.checkpoints;
      const last = checkpoints[checkpoints.length - 1];
      const target = checkpoints.length > 1 && last ? last : base.navigationHistory.anchorMomentCheckpoint;
      const remaining = checkpoints.length > 1 ? checkpoints.slice(0, checkpoints.length - 1) : [base.navigationHistory.anchorMomentCheckpoint];
      const status = `Restored checkpoint: ${target.label}`;
      return completeAction(
        restoreSessionToCheckpoint(base, nextRev, target, false, remaining, status),
        action.requestId,
        nextRev,
        status,
        status
      );
    }

    case 'navigation.cancel': {
      const settledNavState =
        base.machines.navigation === 'transitioning' || base.machines.navigation === 'returning'
          ? base.spatialView.mode === 'source_2d'
            ? 'source'
            : 'sky_registered'
          : base.machines.navigation;
      const status = 'Navigation transition canceled; camera settled at current pose.';
      return {
        session: {
          ...base,
          viewRevision: nextRev,
          machines: { ...base.machines, navigation: settledNavState, voice: 'interrupted' },
          spatialView: { ...base.spatialView, activeTransitionId: undefined },
          lastStatusBanner: status,
        },
        outcome: ActionOutcomeSchema.parse({ status: 'canceled', actionId: action.requestId, settledStateVersion: nextRev, visualStatus: status }),
      };
    }

    case 'source.return': {
      const all = [base.navigationHistory.anchorMomentCheckpoint, ...base.navigationHistory.checkpoints];
      const target = action.checkpointId
        ? (all.find((c) => c.id === action.checkpointId) ?? base.navigationHistory.anchorMomentCheckpoint)
        : base.navigationHistory.anchorMomentCheckpoint;
      const status = `Returned to Moment (${target.sourceTimeSec.toFixed(1)}s) · Audio & frame restored.`;
      return completeAction(
        restoreSessionToCheckpoint(base, nextRev, target, true, [target], status),
        action.requestId,
        nextRev,
        'Returned to your original recorded moment.',
        status
      );
    }

    // Each turns into a real action in the unit that wires it to the viewport or the audio engine
    case 'ui.overlay':
    case 'space.regime':
    case 'scene.show':
    case 'audio.set':
      return rejectAction(session, action.requestId, 'not_connected', 'Voice cannot do this in the app yet.', []);
    // The view cannot show another year yet: it does not move the drawn stars to a chosen year (camera-rig's
    // propagation has no caller), so a new epoch would change only the words on screen
    case 'space.epoch':
      return rejectAction(
        session,
        action.requestId,
        'not_connected',
        'The app cannot show the sky at another year yet: the view does not move the stars to a chosen year.',
        []
      );

    // Answers wait for the M3 tools, which compute every number from the user's own data
    case 'question.ask':
      return rejectAction(
        session,
        action.requestId,
        'no_grounded_answer',
        'No tool can answer questions from your data yet.',
        []
      );
  }
}

export const validateAndDispatchAction = dispatchAlbedianAction;
