import {
  ActionContext,
  ActionContextSchema,
  ActionOutcome,
  ActionOutcomeSchema,
  AlbedianAction,
  AlbedianActionSchema,
  CelestialFinding,
  ObservationSession,
  VoiceAction,
  VoiceActionSchema,
  deterministicHexDigest,
} from '@albedian/contracts';
import { dispatchAlbedianAction } from './reducer.js';

/**
 * ============================================================================
 * @albedian/observation — Context-Bound Voice Utterance Parser & Deictic Resolver
 * ============================================================================
 */

export const MAX_POINTER_RECENCY_MS = 10_000;

/**
 * Builds a schema-validated `ActionContext` from the current `ObservationSession`
 * and optional contemporaneous pointer telemetry.
 */
export function buildActionContext(
  session: ObservationSession,
  recentPointer?: ActionContext['recentPointer']
): ActionContext {
  return ActionContextSchema.parse({
    sessionId: session.id,
    sourceAssetId: session.sourceAsset.id,
    sourceTimeSec: session.currentSourceTimeSec,
    navigationState: session.machines.navigation,
    calibrationState: session.machines.calibration,
    selectedFindingId: session.selectedFindingId,
    focusedFindingId: session.focusedFindingId,
    recentPointer,
    simulationEpochISO: session.spatialView.simulationEpochISO,
    viewRevision: session.viewRevision,
    reducedMotion: session.reducedMotion,
  });
}

function isPointerFreshAndMatchingRevision(
  pointer: NonNullable<ActionContext['recentPointer']> | undefined,
  context: ActionContext
): pointer is NonNullable<ActionContext['recentPointer']> {
  if (!pointer || pointer.viewRevision !== context.viewRevision) return false;
  if (!Number.isFinite(pointer.atTimestampMs) || pointer.atTimestampMs < 0) return false;
  if (pointer.atTimestampMs >= 1_000_000_000_000) {
    const ageMs = Date.now() - pointer.atTimestampMs;
    return ageMs <= MAX_POINTER_RECENCY_MS && ageMs >= -60_000;
  }
  return context.sourceTimeSec * 1000 - pointer.atTimestampMs <= MAX_POINTER_RECENCY_MS;
}

function findNamedFindingInUtterance(
  normalizedUtterance: string,
  session: ObservationSession
): CelestialFinding | undefined {
  for (const finding of session.observationSolution.findings) {
    if (normalizedUtterance.includes(finding.title.toLowerCase())) return finding;
    const idShort = finding.id.replace(/^finding_/, '').toLowerCase();
    if (idShort.length >= 3 && normalizedUtterance.includes(idShort)) return finding;
    if (finding.catalogRef?.bayerDesignation && normalizedUtterance.includes(finding.catalogRef.bayerDesignation.toLowerCase())) {
      return finding;
    }
    if (finding.catalogRef?.catalogSourceId && normalizedUtterance.includes(finding.catalogRef.catalogSourceId.toLowerCase())) {
      return finding;
    }
  }
  return undefined;
}

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
}

function resolveTargetFinding(
  normalizedUtterance: string,
  context: ActionContext,
  session: ObservationSession
): TargetResolutionResult {
  const named = findNamedFindingInUtterance(normalizedUtterance, session);
  if (named) return { findingId: named.id };

  const pointerFresh = isPointerFreshAndMatchingRevision(context.recentPointer, context);

  if (pointerFresh && context.recentPointer?.overlappingCandidateIds && context.recentPointer.overlappingCandidateIds.length > 1) {
    const findingMap = new Map(session.observationSolution.findings.map((f) => [f.id, f]));
    const choices = context.recentPointer.overlappingCandidateIds.map((id) => ({
      id,
      label: findingMap.get(id)?.title ?? id,
    }));
    return {
      disambiguation: {
        question: `Multiple celestial objects overlap near your pointer. Did you mean ${choices.map((c) => c.label).join(' or ')}?`,
        choices,
      },
    };
  }

  if (context.selectedFindingId) return { findingId: context.selectedFindingId };
  if (context.focusedFindingId) return { findingId: context.focusedFindingId };

  if (pointerFresh && context.recentPointer) {
    if (context.recentPointer.nearestFindingId) return { findingId: context.recentPointer.nearestFindingId };
    if (context.recentPointer.overlappingCandidateIds?.length === 1) return { findingId: context.recentPointer.overlappingCandidateIds[0] };
  }

  const soleFinding = session.observationSolution.findings[0];
  if (session.observationSolution.findings.length === 1 && soleFinding) return { findingId: soleFinding.id };

  return {
    error: 'Ambiguous target reference: no star is currently selected and no recent pointer location matches the active view revision.',
  };
}

/**
 * Translates natural-language voice or text utterances into schema-validated `AlbedianAction`
 * payloads bound to `context.viewRevision`, or returns a structured `disambiguation` / `error`.
 */
export function parseUtteranceToAction(
  utterance: string,
  context: ActionContext,
  session: ObservationSession
): {
  action?: AlbedianAction;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
} {
  const trimmed = utterance.trim();
  if (!trimmed) return { error: 'Empty voice or text command.' };

  const norm = trimmed.toLowerCase();
  const requestId = `req_${context.viewRevision}_${deterministicHexDigest(`${trimmed}:${context.viewRevision}`).slice(0, 8)}`;

  if (/\b(fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b/i.test(norm)) {
    return { error: 'Unsafe synthetic edit rejected: Albedian preserves authentic observations and never fabricates sky content.' };
  }

  if (/^(stop|cancel|wait|halt|abort|freeze)\b/i.test(norm) || /\b(stop|cancel)\s+(navigation|transition|flight)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({ type: 'navigation.cancel', requestId, expectedViewRevision: context.viewRevision, transitionId: session.spatialView.activeTransitionId }),
    };
  }

  if (/\b(back a little|back a bit|step back slightly|slightly back|partial back)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({ type: 'history.back', requestId, expectedViewRevision: context.viewRevision, mode: 'partial', fraction: 0.35 }),
    };
  }

  if (/\b(take me back|back to moment|back to the moment|return to source|back to source|back to recording|back to photo|back to video|go back|return home)\b/i.test(norm) || norm === 'back') {
    if (/\b(previous checkpoint|last checkpoint|checkpoint back)\b/i.test(norm)) {
      return { action: AlbedianActionSchema.parse({ type: 'history.back', requestId, expectedViewRevision: context.viewRevision, mode: 'checkpoint' }) };
    }
    return {
      action: AlbedianActionSchema.parse({ type: 'source.return', requestId, expectedViewRevision: context.viewRevision, checkpointId: session.navigationHistory.anchorMomentCheckpoint.id, restoreAudio: true }),
    };
  }

  if (/\b(thousand years ago|1000 years ago|1026\s*ce|year\s+-?\d+|\d+\s*(?:ce|bce|bc|ad)|years from now|years ago|epoch)\b/i.test(norm)) {
    let yearCE = 1026;
    if (!/\b(thousand years ago|1000 years ago|1026)\b/i.test(norm)) {
      const bceMatch = norm.match(/(\d+)\s*(?:bce|bc)\b/i);
      const ceMatch = norm.match(/(-?\d+)\s*(?:ce|ad)\b/i);
      const agoMatch = norm.match(/(\d+)\s*years\s*ago\b/i);
      const futureMatch = norm.match(/(\d+)\s*years\s*from\s*now\b/i);
      const yearMatch = norm.match(/year\s+(-?\d+)/i);
      if (bceMatch?.[1]) yearCE = -Number.parseInt(bceMatch[1], 10);
      else if (ceMatch?.[1]) yearCE = Number.parseInt(ceMatch[1], 10);
      else if (agoMatch?.[1]) yearCE = 2026 - Number.parseInt(agoMatch[1], 10);
      else if (futureMatch?.[1]) yearCE = 2026 + Number.parseInt(futureMatch[1], 10);
      else if (yearMatch?.[1]) yearCE = Number.parseInt(yearMatch[1], 10);
    }

    if (yearCE < -10000 || yearCE > 12000) {
      return { error: `Requested epoch year ${yearCE} CE is outside the validated proper-motion propagation window (-10000 to 12000 CE).` };
    }

    const paddedYear = yearCE < 0 ? `-${Math.abs(yearCE).toString().padStart(4, '0')}` : yearCE.toString().padStart(4, '0');
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.epoch',
        requestId,
        expectedViewRevision: context.viewRevision,
        epochISO: `${paddedYear}-01-01T00:00:00Z`,
        yearCE,
      }),
    };
  }

  if (/\b(where is earth|earth direction|point to earth|direction to earth|home vector)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'earthDirectionVector', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(constellation lines|constellations overlay|star lines)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'constellationLines', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(residual vectors|residuals)\b/i.test(norm)) {
    return { action: AlbedianActionSchema.parse({ type: 'ui.overlay', requestId, expectedViewRevision: context.viewRevision, overlay: 'residualVectors', enabled: !/\b(hide|off|disable)\b/i.test(norm) }) };
  }

  if (/\b(compare|show original|original recording|before and after|a\/b wipe|split view)\b/i.test(norm)) {
    const originalVariant = session.mediaVariants[0]?.id ?? 'variant_original';
    const restoredVariant = session.activeVariantId !== originalVariant ? session.activeVariantId : (session.mediaVariants[1]?.id ?? originalVariant);
    return { action: AlbedianActionSchema.parse({ type: 'media.compare', requestId, expectedViewRevision: context.viewRevision, variantAId: originalVariant, variantBId: restoredVariant, preserveTime: true }) };
  }

  if (/\b(make the sky clearer|sky clearer|don't change the people|do not change the people|preserve the people|temporal stack|motion-compensated|denoise|clean up noise|restore|bring out faint stars)\b/i.test(norm)) {
    const profile: 'natural' | 'temporal' | 'star_protected' = /\b(temporal|stack|faint)\b/i.test(norm) ? 'temporal' : 'star_protected';
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.restore',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceAssetId: session.sourceAsset.id,
        profile,
        constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
      }),
    };
  }

  const seekMatch = norm.match(/\b(?:seek|jump|scrub)\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b/i);
  if (seekMatch?.[1]) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'media.seek',
        requestId,
        expectedViewRevision: context.viewRevision,
        sourceTimeSec: Number.parseFloat(seekMatch[1]),
        preserveAudioSync: true,
      }),
    };
  }

  if (/\b(what were we looking at|what was overhead|show me what was overhead|identify the stars|calibrate|register the sky|show sky|what is in the sky|match the sky)\b/i.test(norm)) {
    return {
      action: AlbedianActionSchema.parse({
        type: 'sky.show',
        requestId,
        expectedViewRevision: context.viewRevision,
        mode: context.calibrationState === 'calibrated' ? 'measured' : 'predicted',
        observationSolutionId: session.observationSolution.id,
      }),
    };
  }

  if (/\b(another viewpoint|different viewpoint|orbit|another angle|from the side|translate viewpoint|shift viewpoint)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.viewpoint',
        requestId,
        expectedViewRevision: context.viewRevision,
        targetFindingId: resolved.findingId ?? session.selectedFindingId ?? 'finding_betelgeuse',
        units: 'pc',
        offsetParsec: [12, 8, -5],
        trajectory: /\b(translate|shift)\b/i.test(norm) ? 'translate' : 'orbit',
      }),
    };
  }

  if (/\b(how far|distance|light years|parsecs|how distant)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['distance', 'evidence'],
      }),
    };
  }

  if (/\b(what's behind|what is behind|what lies beyond|behind it|beyond it|behind|beyond|behind sightline|along this sightline)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.describe',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        fields: ['behind_sightline'],
      }),
    };
  }

  if (/\b(take me there|fly there|go there|explore|fly to|travel to|enter space|enter 3d|zoom into space|take me to)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.enter',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
        distancePolicy: 'measured_or_estimated',
      }),
    };
  }

  if (/\b(what's that|what is that|what star is that|which star|that one|this one|select|focus on|inspect|point at|look at|tell me about)\b/i.test(norm) || Boolean(findNamedFindingInUtterance(norm, session))) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.select',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
      }),
    };
  }

  return { error: `Unrecognized or ambiguous observation command: "${trimmed}".` };
}

export const resolveVoiceUtterance = parseUtteranceToAction;

/**
 * End-to-end voice/text command executor.
 */
export function executeVoiceOrTextCommand(
  session: ObservationSession,
  utterance: string,
  modality: 'voice' | 'text' | 'pointer' = 'voice',
  recentPointer?: ActionContext['recentPointer']
): {
  session: ObservationSession;
  outcome: ActionOutcome;
  voiceActionRecord: VoiceAction;
} {
  const context = buildActionContext(session, recentPointer);
  const parsed = parseUtteranceToAction(utterance, context, session);
  const actionRecordId = `voice_act_${session.viewRevision}_${session.voiceActions.length + 1}`;

  const voiceActionRecord: VoiceAction = VoiceActionSchema.parse({
    id: actionRecordId,
    utterance: utterance.trim() || '(empty)',
    modality,
    context,
    proposedAction: parsed.action,
    createdAtUTC: new Date().toISOString(),
  });

  if (parsed.disambiguation) {
    return {
      session: {
        ...session,
        machines: { ...session.machines, voice: 'awaiting_tool' },
        voiceActions: [...session.voiceActions, voiceActionRecord],
        lastStatusBanner: parsed.disambiguation.question,
      },
      outcome: ActionOutcomeSchema.parse({
        status: 'needs_input',
        actionId: actionRecordId,
        question: parsed.disambiguation.question,
        choices: parsed.disambiguation.choices,
      }),
      voiceActionRecord,
    };
  }

  if (!parsed.action) {
    const errText = parsed.error ?? 'Could not resolve voice or text command.';
    const reason = /synthetic/i.test(errText)
      ? 'unsafe_synthetic_edit'
      : /epoch/i.test(errText)
        ? 'invalid_epoch'
        : session.machines.calibration === 'unresolved'
          ? 'unresolved_sky'
          : 'ambiguous_target';

    return {
      session: {
        ...session,
        voiceActions: [...session.voiceActions, voiceActionRecord],
        lastStatusBanner: errText,
      },
      outcome: ActionOutcomeSchema.parse({
        status: 'rejected',
        actionId: actionRecordId,
        reason,
        explanation: errText,
        nextActions: [
          'Select a star on screen or name it explicitly (e.g. "Select Betelgeuse")',
          'Ask "What were we looking at?"',
          'Say "Back to moment"',
        ],
      }),
      voiceActionRecord,
    };
  }

  const dispatched = dispatchAlbedianAction(session, parsed.action);
  return {
    session: {
      ...dispatched.session,
      voiceActions: [...dispatched.session.voiceActions, voiceActionRecord],
    },
    outcome: dispatched.outcome,
    voiceActionRecord,
  };
}
