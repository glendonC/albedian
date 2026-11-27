import {
  ActionOutcome,
  ActionOutcomeSchema,
  AlbedianAction,
  CelestialFinding,
  FlightRefusalKind,
  ObservationSession,
  flightRefusal,
  hasVerifiedWcs,
  refusalSentence,
} from '@albedian/contracts';
import {
  formatDistanceWithUncertainty,
  getEpistemicBadgeDescriptor,
} from '@albedian/evidence';
import { EMPTY_MEDIA_VARIANT_ID } from './emptySession.js';
import { arrivalDistanceText, flightEndOrigin } from './flight.js';
import { CATALOG_STAR_NAMES } from './generated/catalogStarNames.js';
import {
  computeCartesianParsecICRS,
  computeStarAnchorNdc,
  pushCheckpointIfDepartingSource,
  returnCheckpointOf,
  restoreSessionToCheckpoint,
  savedOnRecording,
  syncAnchorCheckpointIfInitial,
} from './checkpoints.js';

/**
 * ============================================================================
 * @albedian/observation: Revision-Gated Action Dispatcher & State Reducer
 * ============================================================================
 */

type RejectionReason = Extract<ActionOutcome, { status: 'rejected' }>['reason'];

const PREDICTED_BODIES: Readonly<Record<string, string>> = {
  sun: 'the Sun',
  moon: 'the Moon',
  venus: 'Venus',
  mars: 'Mars',
  jupiter: 'Jupiter',
  saturn: 'Saturn',
  uranus: 'Uranus',
};

const KNOWN_FINDING_NAMES: ReadonlyMap<string, { spoken: string; shown: string }> = (() => {
  const byId = new Map<string, { spoken: string; shown: string }>();
  for (const [spoken, id, shown] of CATALOG_STAR_NAMES) {
    if (!byId.has(id)) byId.set(id, { spoken, shown });
    if (id.startsWith('finding-')) {
      const predictedId = `finding-predicted-${id.slice('finding-'.length)}`;
      if (!byId.has(predictedId)) byId.set(predictedId, { spoken, shown });
    }
  }
  for (const [spoken, shown] of Object.entries(PREDICTED_BODIES)) {
    byId.set(`finding-predicted-${spoken}`, { spoken, shown });
  }
  return byId;
})();

/** Plain consumer refusal when a finding action names an absent or unknown finding [verified: UIUX-voiceyet2-READY.md C37]. */
function missingFindingExplanation(base: ObservationSession, findingId: string): string {
  const media = base.sourceAsset?.mediaKind === 'video' ? 'video' : 'photo';
  const status = base.observationSolution?.status;
  const workedOut =
    base.machines.calibration !== 'unresolved' &&
    (status === 'calibrated' || status === 'approximate' || status === 'predicted_only');
  if (!workedOut) {
    return refusalSentence({ kind: 'sky_not_worked_out' }, media);
  }
  const known = KNOWN_FINDING_NAMES.get(findingId);
  if (known) {
    return refusalSentence({ kind: 'not_in_this_sky', said: known.spoken, name: known.shown }, media);
  }
  const noun = media === 'video' ? 'clip' : 'photo';
  return `No finding in this ${noun} has that name.`;
}

/** The recorded refusal of a flight the contract's rule refuses (UIUX-predflight-READY.md item 5): the target by its
 * plain name (its known name, its catalog name, else its title), or, for the Sun, the Moon or a planet, no name. */
function flightRefusalExplanation(finding: CelestialFinding, kind: FlightRefusalKind): string {
  if (kind === 'beyond_solar_system_only') return refusalSentence({ kind });
  const name = KNOWN_FINDING_NAMES.get(finding.id)?.shown ?? finding.catalogRef?.commonName ?? finding.title;
  return refusalSentence({ kind, name });
}

/** The recording's own noun in a sentence a person reads: a video is a "clip", the decided noun (C35). */
function mediaNoun(session: ObservationSession): 'photo' | 'clip' {
  return session.sourceAsset?.mediaKind === 'video' ? 'clip' : 'photo';
}

/**
 * Whether the screen is off the recording: any view but the recording's own (source_2d), as apps/web's
 * isSourcePhotoShown reads it: the flight, Real sky and the predicted sky each take the photo's place (voice-compare-one
 * version two, AUDIT-C's live run: in Real sky the recording is not on screen and Restore cannot be pressed there).
 */
function isOffTheRecording(session: ObservationSession): boolean {
  return (session.spatialView?.mode ?? 'source_2d') !== 'source_2d';
}

/**
 * Whether Back to Moment is the screen's way back: the flight and the predicted sky (the viewport's AskBar onBack, as
 * isFlightShown and isPredictedSkyShown read the view); Real sky goes back by its views pill instead.
 */
function backToMomentShown(session: ObservationSession): boolean {
  const mode = session.spatialView?.mode;
  const solution = session.observationSolution;
  return mode === 'catalog_3d' || (mode === 'registered_sky' && solution !== undefined && solution.status !== 'calibrated');
}

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
 *
 * No action opens the microphone, so none sets machines.voice to listening: only an open capture does
 * (apps/web push-to-talk), whether a button or a spoken command dispatched the action.
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
      // UIUX-predflight-READY.md's table, RULED 12:56 on 24 Nov (reducer 115)
      'The screen changed while you asked.',
      ['Rebuild ActionContext from latest ObservationSession and retry']
    );
  }

  const base = syncAnchorCheckpointIfInitial(session);
  const nextRev = base.viewRevision + 1;

  switch (action.type) {
    case 'media.restore': {
      if (!base.sourceAsset || action.sourceAssetId !== base.sourceAsset.id) {
        return rejectAction(session, action.requestId, 'missing_source', `Restore works on the ${mediaNoun(base)} on screen.`, ['Dispatch media.restore with the active SourceAsset ID']);
      }
      if ((action.constraints as { allowSyntheticSky?: boolean }).allowSyntheticSky !== false) {
        return rejectAction(session, action.requestId, 'unsafe_synthetic_edit', `Nothing is generated here: a restoration uses only your ${mediaNoun(base)}'s own pixels.`, ['Use star_protected or temporal restoration profiles']);
      }
      // A stored restoration of this recording, by its kind and its source; nothing here restores anything, so the
      // outcome claims only what the variant's own disclosure says
      const sourceSha256 = base.sourceAsset.sha256;
      const variant = base.mediaVariants.find((v) => v.kind === 'restoration' && v.sourceSha256 === sourceSha256);
      if (!variant) {
        return rejectAction(session, action.requestId, 'missing_source', `This ${mediaNoun(base)} has not been restored.`, ['Run restoration pipeline first']);
      }
      // Off the recording the restored version is not what is on screen: it is said where it shows, and nothing is
      // changed or pressed (not_connected, never missing_source, which the web answers by pressing Restore)
      if (isOffTheRecording(base)) {
        return rejectAction(
          session,
          action.requestId,
          'not_connected',
          // UIUX-voice-compare-restore-words-READY.md item 2, DECIDED-BY-LEAD 24 Nov 19:42; where Back to Moment is not
          // the screen's control (Real sky), the views pill's own word for the first view, as SpatialObservatoryViewport.tsx
          // names it (UIUX-realsky-restore-words-READY.md, voice-realsky-words-one)
          `The restored version shows on this ${mediaNoun(base)} itself; ${backToMomentShown(base) ? 'Back to Moment' : mediaNoun(base) === 'clip' ? 'Your clip' : 'Your photo'} returns to it.`,
          ['Return to the recording with Back to Moment']
        );
      }
      const name = 'the restored version';
      const status = `Applied ${name} (${variant.disclosure})`;
      // Only what the session holds: the variant now shown, and that its audio is the source's when it records so (each
      // of the source's audio streams in it with the packet hash ingest stored). Nothing here restores anything, so no
      // clarity, people or audio is claimed for it
      const sourceAudio = base.sourceAsset.audioStreams;
      const audioCopied =
        sourceAudio.length > 0 &&
        variant.audioStreams.length === sourceAudio.length &&
        sourceAudio.every((stream, i) => variant.audioStreams[i]?.sha256 === stream.sha256);
      const audio = audioCopied ? ' Its audio is copied from the original recording.' : '';
      return completeAction(
        { ...base, viewRevision: nextRev, activeVariantId: variant.id, machines: { ...base.machines, restoration: 'ready' }, lastStatusBanner: status },
        action.requestId,
        nextRev,
        `Now showing ${name}.${audio}`,
        status
      );
    }

    case 'media.compare': {
      // The words of a compare that cannot be made (UIUX-voice-compare-restore-words-READY.md item 1, DECIDED-BY-LEAD 24
      // Nov 19:42) name the recording by its noun, never a variant id
      const sha256 = base.sourceAsset?.sha256;
      if (!base.mediaVariants.some((v) => v.kind === 'restoration' && v.sourceSha256 === sha256)) {
        return rejectAction(session, action.requestId, 'missing_source', `This ${mediaNoun(base)} has not been restored, so there is nothing to compare it with.`, ['Run restoration pipeline first']);
      }
      // A version is never compared with itself (DECIDED-BY-LEAD 24 Nov 19:40)
      if (action.variantAId === action.variantBId) {
        return rejectAction(session, action.requestId, 'missing_source', `That would compare a version of this ${mediaNoun(base)} with itself, so nothing was compared.`, ['Compare the restored version with the original']);
      }
      // The original is the recording itself where no display copy of it is held (the variant id the session names it
      // by, as the active variant is when nothing is pressed): the compare on screen sets the restored version against it
      const held = (id: string) => (id === EMPTY_MEDIA_VARIANT_ID && base.sourceAsset !== undefined) || base.mediaVariants.some((v) => v.id === id);
      if (!held(action.variantAId) || !held(action.variantBId)) {
        // A version the screen has since swapped out (the session's variants change with no new revision): the
        // ruled words for a screen that changed while the person asked (reducer 115)
        return rejectAction(session, action.requestId, 'missing_source', 'The screen changed while you asked.', ['Rebuild ActionContext from latest ObservationSession and retry']);
      }
      const status = `Comparing ${action.variantAId} with ${action.variantBId} at ${base.currentSourceTimeSec.toFixed(1)}s.`;
      return completeAction(
        { ...base, viewRevision: nextRev, activeVariantId: action.variantBId, compareVariantId: action.variantAId, compareWipePosition: 0.5, machines: { ...base.machines, navigation: 'comparison' }, lastStatusBanner: status },
        action.requestId,
        nextRev,
        'Comparing the two versions.',
        status
      );
    }

    case 'media.seek': {
      if (!base.sourceAsset) {
        return rejectAction(session, action.requestId, 'missing_source', 'There is no clip here to move through.', ['Import a recording first']);
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

    // A clip played or paused on screen (media-playback-0): written to isPlaying, so a checkpoint saved on leaving the
    // recording carries playbackWasPlaying as it was (AGENTS.md 1.5). Only a clip on screen plays: on the recording, or
    // under a compare wipe opened over it (the rule Back to Moment reads, savedOnRecording). The status line is left as
    // it is: playing or pausing says nothing new on screen
    case 'media.playback': {
      if (!base.sourceAsset) {
        return rejectAction(session, action.requestId, 'missing_source', 'Cannot play when no SourceAsset is loaded.', ['Import a recording first']);
      }
      if (base.sourceAsset.mediaKind !== 'video') {
        return rejectAction(session, action.requestId, 'missing_source', 'A photo does not play; only a clip does.', []);
      }
      if (!savedOnRecording({ navigationState: base.machines.navigation, spatialView: base.spatialView })) {
        return rejectAction(session, action.requestId, 'missing_source', 'The clip is not on screen, so it cannot play here.', ['Back to Moment first']);
      }
      const said = action.playing ? 'Playing the clip.' : 'Paused the clip.';
      return completeAction({ ...base, viewRevision: nextRev, isPlaying: action.playing }, action.requestId, nextRev, said, said);
    }

    case 'sky.show': {
      if (!base.observationSolution || base.machines.calibration === 'unresolved' || base.observationSolution.status === 'unresolved') {
        // In plain words, only what the session holds: that a solve from the stars did not come out, or that
        // none has been made, and which of its time and place the recording lacks, only when it lacks one. The
        // solver's own warnings are written for the solver's log, so none is passed on to a person
        if (!base.sourceAsset) {
          return rejectAction(session, action.requestId, 'unresolved_sky', 'There is no photo or clip here, so there is no sky to show.', ['Import a recording first']);
        }
        // A clip is named "clip", the decided noun; no "yet", since a solve that ended predicted only says it too (C34)
        const kind = base.sourceAsset.mediaKind === 'video' ? 'clip' : 'photo';
        const sky =
          base.observationSolution?.status === 'unresolved'
            ? `This ${kind}'s sky could not be worked out from its stars.`
            : `This ${kind}'s sky has not been worked out.`;
        const noTime = !base.sourceAsset.capturedAtUTC;
        const noPlace = !base.sourceAsset.observerLocation;
        const lacks = noTime && noPlace ? 'when or where' : noTime ? 'when' : noPlace ? 'where' : undefined;
        // The noun's own verb, as the sky layer's words say it (apps/web/src/services/skyLayerWords.ts): a clip is
        // recorded, a photo taken
        const said = lacks ? `${sky} The ${kind} does not say ${lacks} it was ${kind === 'clip' ? 'recorded' : 'taken'}.` : sky;
        return rejectAction(session, action.requestId, 'unresolved_sky', said, []);
      }
      if (action.mode === 'measured' && (base.machines.calibration !== 'calibrated' || base.observationSolution.status !== 'calibrated')) {
        return rejectAction(session, action.requestId, 'unresolved_sky', `This ${mediaNoun(base)}'s stars were not matched, so the real sky cannot be laid over it.`, ['Show predicted sky model instead (`mode: "predicted"`)']);
      }
      const fromSource = base.machines.navigation === 'source' || base.machines.navigation === 'comparison';
      const nextHistory = fromSource ? pushCheckpointIfDepartingSource(base, 'Departure from Source to Registered Sky') : base.navigationHistory;
      const isCal = base.observationSolution.status === 'calibrated';
      // A calibrated solution always reports its quality (the contract requires it)
      const quality = base.observationSolution.quality;
      const status = isCal && quality
        ? `Calibrated sky registered (${quality.heldOutInliers} held-out stars${quality.medianResidualPx !== undefined ? `, ${quality.medianResidualPx.toFixed(2)} px median residual` : ''}).`
        : 'Showing predicted sky model from recorded timestamp and location (separate from source pixels).';

      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          machines: { ...base.machines, navigation: 'sky_registered' },
          spatialView: {
            ...base.spatialView,
            // go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 04:47): a view kept by a flight's return is let go when the
            // sky is shown afresh, so it opens as its design record says
            skyView: undefined,
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
        return rejectAction(session, action.requestId, 'ambiguous_target', missingFindingExplanation(base, action.findingId), ['Select a visible star or planet marker']);
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
        return rejectAction(session, action.requestId, 'ambiguous_target', missingFindingExplanation(base, action.findingId), ['Select a finding first']);
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
        return rejectAction(session, action.requestId, 'unresolved_sky', `The sky in this ${mediaNoun(base)} has not been worked out, so nothing in it can be flown to.`, ['Calibrate sky first']);
      }
      const finding = base.observationSolution.findings.find((f) => f.id === action.findingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', missingFindingExplanation(base, action.findingId), ['Select a catalog-matched star first']);
      }
      // The flight rule (flight-rule-0): catalog_matched, or predicted with a catalog row, with a star catalog's distance
      // (both classes hold a sky position, which the two last tests only narrow for the type checker).
      // The finding is only read: a predicted star flies as the simulated catalog view and stays predicted
      const refused = flightRefusal(finding);
      if (refused !== null || !finding.inferredDistancePc || !finding.skyPosition) {
        return rejectAction(session, action.requestId, 'no_distance_estimate', flightRefusalExplanation(finding, refused ?? 'no_catalog_distance'), ['Select a catalog-matched star with a verified distance interval']);
      }
      const distInfo = formatDistanceWithUncertainty(finding.inferredDistancePc);
      // go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45): from a sky with no verified WCS and no pose estimate (the
      // predicted sky), the request's sky view is kept in the checkpoint the flight leaves from: the flight starts there
      // and Back to Moment returns there
      const fromSkyView = action.skyView && !hasVerifiedWcs(base.observationSolution) && base.observationSolution.poseEstimate === undefined;
      const departing = fromSkyView ? { ...base, spatialView: { ...base.spatialView, skyView: action.skyView } } : base;
      const nextHistory = pushCheckpointIfDepartingSource(departing, `Checkpoint before entering 3D space toward ${finding.title}`);
      const targetCoords = finding.cartesianParsecICRS ?? computeCartesianParsecICRS(finding.skyPosition.raDeg, finding.skyPosition.decDeg, finding.inferredDistancePc.median);
      const status = `Entered 3D Space · ${finding.title} (${distInfo.lightYearText}) · Catalog Geometry · Distance Estimates`;
      return completeAction(
        {
          ...base,
          viewRevision: nextRev,
          isPlaying: false,
          selectedFindingId: finding.id,
          focusedFindingId: finding.id,
          machines: { ...base.machines, navigation: 'space' },
          spatialView: {
            ...base.spatialView,
            mode: 'catalog_3d',
            transitionPhase: 'at_space',
            transitionProgress: 1,
            activeTransitionId: action.requestId,
            // One light year short of the star, as the flight animation ends (flight.ts)
            observerOriginParsec: flightEndOrigin(base.spatialView.observerOriginParsec, targetCoords),
            fovVerticalDeg: 45,
            lockedAnchorNdc: computeStarAnchorNdc(finding, base.sourceAsset),
          },
          navigationHistory: nextHistory,
          lastStatusBanner: status,
        },
        action.requestId,
        nextRev,
        `Arrived ${arrivalDistanceText(base.spatialView.observerOriginParsec, targetCoords)} from ${finding.title}. The stars stay drawn as seen from Earth.`,
        status
      );
    }

    case 'space.viewpoint': {
      const finding = base.observationSolution?.findings.find((f) => f.id === action.targetFindingId);
      if (!finding) {
        return rejectAction(session, action.requestId, 'ambiguous_target', missingFindingExplanation(base, action.targetFindingId), ['Select a catalog-matched star first']);
      }
      // The same flight rule as space.enter: the finding is only read
      const refused = flightRefusal(finding);
      if (refused !== null || !finding.inferredDistancePc || !finding.skyPosition) {
        return rejectAction(session, action.requestId, 'no_distance_estimate', flightRefusalExplanation(finding, refused ?? 'no_catalog_distance'), ['Select a catalog-matched star with a verified distance estimate']);
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
          machines: { ...base.machines, navigation: 'space' },
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
      // A named checkpoint of this recording wins; otherwise (no id, an unknown id, or one saved on another
      // recording) the moment you left, the same one the button returns to
      const all = [base.navigationHistory.anchorMomentCheckpoint, ...base.navigationHistory.checkpoints];
      const named = action.checkpointId ? all.find((c) => c.id === action.checkpointId) : undefined;
      const target =
        named && named.sourceAssetId === base.sourceAsset?.id
          ? named
          : (returnCheckpointOf(base) ?? base.navigationHistory.anchorMomentCheckpoint);
      // go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45): the predicted sky a flight left from comes back as it was,
      // its view kept; the photo's moment stays the anchor, so Back to Moment on that sky goes on to the photo
      const toSky = target.spatialView.skyView !== undefined && !savedOnRecording(target);
      // go-there-predicted-words-one (UIUX-predsky-READY.md, the lead's line stamped 04:47): that return speaks of the
      // sky and the view it restores, not of the recording it does not reach; no class words, the sky's own Predicted tag
      // stands from its first frame; the photo's return keeps its sentences
      const status = toSky ? 'Returned to the predicted sky · The view you left restored.' : `Returned to Moment (${target.sourceTimeSec.toFixed(1)}s) · Audio & frame restored.`;
      const answer = toSky ? 'Returned to the predicted sky, at the view you left.' : 'Returned to your original recorded moment.';
      const kept = base.navigationHistory.checkpoints;
      return completeAction(
        toSky
          ? restoreSessionToCheckpoint(base, nextRev, target, false, kept.slice(0, Math.max(0, kept.indexOf(target))), status)
          : restoreSessionToCheckpoint(base, nextRev, target, true, [target], status),
        action.requestId,
        nextRev,
        answer,
        status
      );
    }

    // Each turns into a real action in the unit that wires it to the viewport or the audio engine
    case 'ui.overlay':
    case 'space.regime':
    case 'scene.show':
    case 'audio.set':
      return rejectAction(session, action.requestId, 'not_connected', 'Voice cannot do that in this app.', []);
    // The view cannot show another year yet: it does not move the drawn stars to a chosen year (camera-rig's
    // propagation has no caller), so a new epoch would change only the words on screen
    case 'space.epoch':
      return rejectAction(
        session,
        action.requestId,
        'not_connected',
        `The sky is shown only at your ${mediaNoun(base)}'s own time.`,
        []
      );

    // Answers wait for the M3 tools, which compute every number from the user's own data
    case 'question.ask':
      return rejectAction(
        session,
        action.requestId,
        'no_grounded_answer',
        `That question cannot be answered from your ${mediaNoun(base)}'s data.`,
        []
      );
  }
}

export const validateAndDispatchAction = dispatchAlbedianAction;
