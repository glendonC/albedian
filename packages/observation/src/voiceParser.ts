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
 * @albedian/observation: Context-Bound Voice Utterance Parser & Deictic Resolver
 * ============================================================================
 *
 * The parser says only what the request and the view hold. A target comes from a name, matched as a
 * whole word, or from what is selected, focused or pointed at; nothing else stands in for it. Years count
 * from the clock. A request no rule matches is a question, and every refusal carries its own reason.
 */

export const MAX_POINTER_RECENCY_MS = 10_000;

type RejectionReason = Extract<ActionOutcome, { status: 'rejected' }>['reason'];

/** The contract's bounds on a space.epoch year, which is historical: 500 BCE is -500, and there is no year 0. */
const EPOCH_MIN_YEAR_CE = -10000;
const EPOCH_MAX_YEAR_CE = 12000;
/** The contract's longest question, in UTF-16 code units, as a JavaScript string's length counts them. */
const QUESTION_MAX_UTF16 = 500;

export interface VoiceParseOptions {
  /** Stands in for the clock, whose UTC year "N years ago" counts back from. */
  now?: Date;
}

export interface VoiceParseResult {
  action?: AlbedianAction;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  /** Why no action was formed, as the contract's rejection reason; set whenever `error` is. */
  reason?: RejectionReason;
}

/**
 * epochISO for a historical year, as ISO 8601 writes it: January 1 at 00:00 UTC, in astronomical
 * numbering (1 BCE is year 0, 500 BCE is -499), with the signed six-digit expanded year outside 0000 to
 * 9999, the only form JavaScript's Date reads there. A yearCE of 0, which historical counting lacks, maps
 * to astronomical year 0.
 */
export function epochIsoForYear(yearCE: number): string {
  const astronomical = yearCE < 0 ? yearCE + 1 : yearCE;
  const digits = String(Math.abs(astronomical));
  const year =
    astronomical >= 0 && astronomical <= 9999
      ? digits.padStart(4, '0')
      : `${astronomical < 0 ? '-' : '+'}${digits.padStart(6, '0')}`;
  return `${year}-01-01T00:00:00Z`;
}

/** The historical year of an astronomical one: astronomical 0 is 1 BCE (-1), and -1 is 2 BCE (-2). */
function historicalYear(astronomicalYear: number): number {
  return astronomicalYear <= 0 ? astronomicalYear - 1 : astronomicalYear;
}

const NUMBER = String.raw`(\d{1,3}(?:,\d{3})+|\d+)`;
const YEARS_AGO = new RegExp(String.raw`(?<![\d,])${NUMBER}\s+years?\s+ago\b`);
const THOUSAND_YEARS_AGO = /\b(?:a\s+)?thousand\s+years?\s+ago\b/;
const YEARS_FROM_NOW = new RegExp(String.raw`(?<![\d,])${NUMBER}\s+years?\s+from\s+now\b`);
const YEAR_THEN_ERA = new RegExp(String.raw`(?<![\d,])${NUMBER}\s*(bce|bc|ce|ad)\b`);
const ERA_THEN_YEAR = new RegExp(String.raw`\b(?:ad|ce)\s+${NUMBER}(?![\d,])`);
const YEAR_WORD = new RegExp(String.raw`\b(?:year|epoch)\s+(-?)${NUMBER}(?![\d,])`);

function wholeNumber(digits: string): number {
  return Number.parseInt(digits.replace(/,/g, ''), 10);
}

/**
 * The historical year a lower-cased request names, 'year_zero' for a year 0, or undefined when it names
 * none. "N years ago" counts back from `currentYear`, across 1 BCE to 1 CE. Era words count only as whole
 * words, so the "ce" in "space" or "since" names no year.
 */
function saidYear(text: string, currentYear: number): number | 'year_zero' | undefined {
  let match = text.match(YEARS_AGO);
  if (match?.[1]) return historicalYear(currentYear - wholeNumber(match[1]));
  if (THOUSAND_YEARS_AGO.test(text)) return historicalYear(currentYear - 1000);
  match = text.match(YEARS_FROM_NOW);
  if (match?.[1]) return currentYear + wholeNumber(match[1]);
  match = text.match(YEAR_THEN_ERA);
  if (match?.[1] && match[2]) {
    const year = wholeNumber(match[1]);
    if (year === 0) return 'year_zero';
    return match[2].startsWith('bc') ? -year : year;
  }
  match = text.match(ERA_THEN_YEAR);
  if (match?.[1]) {
    const year = wholeNumber(match[1]);
    return year === 0 ? 'year_zero' : year;
  }
  match = text.match(YEAR_WORD);
  if (match?.[2]) {
    const year = wholeNumber(match[2]);
    if (year === 0) return 'year_zero';
    return match[1] ? -year : year;
  }
  return undefined;
}

function describeYear(yearCE: number): string {
  return yearCE < 0 ? `${-yearCE} BCE` : `${yearCE} CE`;
}

/** True when `name` appears in the lower-cased utterance as a whole word, so the Moon is not in "honeymoon". */
function namesWholeWord(normalizedUtterance: string, name: string): boolean {
  const needle = name.trim().toLowerCase();
  if (!needle) return false;
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(?<![a-z0-9])${escaped}(?![a-z0-9])`).test(normalizedUtterance);
}

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
    if (namesWholeWord(normalizedUtterance, finding.title)) return finding;
    const idShort = finding.id.replace(/^finding_/, '');
    if (idShort.length >= 3 && namesWholeWord(normalizedUtterance, idShort)) return finding;
    if (finding.catalogRef?.bayerDesignation && namesWholeWord(normalizedUtterance, finding.catalogRef.bayerDesignation)) {
      return finding;
    }
    if (finding.catalogRef?.catalogSourceId && namesWholeWord(normalizedUtterance, finding.catalogRef.catalogSourceId)) {
      return finding;
    }
  }
  return undefined;
}

interface TargetResolutionResult {
  findingId?: string;
  disambiguation?: { question: string; choices: Array<{ id: string; label: string }> };
  error?: string;
  reason?: RejectionReason;
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

  // Nothing else stands in: not the solution's only finding, and not any default star
  if (context.calibrationState === 'unresolved') {
    return {
      error: 'The sky is unresolved, so no finding in it can be named, selected or pointed at yet.',
      reason: 'unresolved_sky',
    };
  }
  return {
    error: 'No finding in the sky solution is named in the request, and none is selected, focused or pointed at.',
    reason: 'ambiguous_target',
  };
}

/**
 * Translates natural-language voice or text utterances into schema-validated `AlbedianAction`
 * payloads bound to `context.viewRevision`, or returns a structured `disambiguation`, or an `error`
 * with its contract `reason`. A request no rule matches becomes `question.ask`.
 */
export function parseUtteranceToAction(
  utterance: string,
  context: ActionContext,
  session: ObservationSession,
  options: VoiceParseOptions = {}
): VoiceParseResult {
  const trimmed = utterance.trim();
  if (!trimmed) return { error: 'Empty voice or text command.', reason: 'no_grounded_answer' };

  const norm = trimmed.toLowerCase();
  const requestId = `req_${context.viewRevision}_${deterministicHexDigest(`${trimmed}:${context.viewRevision}`).slice(0, 8)}`;
  const currentYear = (options.now ?? new Date()).getUTCFullYear();

  if (/\b(fake|synthetic|hallucinate|invent|replace the sky|add a moon|add a nebula|generate a galaxy)\b/i.test(norm)) {
    return {
      error: 'Unsafe synthetic edit rejected: Albedian preserves authentic observations and never fabricates sky content.',
      reason: 'unsafe_synthetic_edit',
    };
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

  const yearCE = saidYear(norm, currentYear);
  if (yearCE === 'year_zero') {
    return { error: 'There is no year 0: 1 BCE is followed by 1 CE.', reason: 'invalid_epoch' };
  }
  if (yearCE !== undefined) {
    if (yearCE < EPOCH_MIN_YEAR_CE || yearCE > EPOCH_MAX_YEAR_CE) {
      return {
        error: `Year ${describeYear(yearCE)} is outside the range of a space.epoch year, ${describeYear(EPOCH_MIN_YEAR_CE)} to ${describeYear(EPOCH_MAX_YEAR_CE)}.`,
        reason: 'invalid_epoch',
      };
    }
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.epoch',
        requestId,
        expectedViewRevision: context.viewRevision,
        epochISO: epochIsoForYear(yearCE),
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
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'space.viewpoint',
        requestId,
        expectedViewRevision: context.viewRevision,
        targetFindingId: resolved.findingId,
        units: 'pc',
        offsetParsec: [12, 8, -5],
        trajectory: /\b(translate|shift)\b/i.test(norm) ? 'translate' : 'orbit',
      }),
    };
  }

  if (/\b(how far|distance|light years|parsecs|how distant)\b/i.test(norm)) {
    const resolved = resolveTargetFinding(norm, context, session);
    if (resolved.disambiguation) return { disambiguation: resolved.disambiguation };
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
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
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
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
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
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
    if (!resolved.findingId) return { error: resolved.error, reason: resolved.reason };
    return {
      action: AlbedianActionSchema.parse({
        type: 'finding.select',
        requestId,
        expectedViewRevision: context.viewRevision,
        findingId: resolved.findingId,
      }),
    };
  }

  // No rule matches: a question, which the reducer refuses until the M3 tools can answer it from the user's data
  if (trimmed.length > QUESTION_MAX_UTF16) {
    return {
      error: 'No command matches the request, and it is longer than a question can hold.',
      reason: 'no_grounded_answer',
    };
  }
  return {
    action: AlbedianActionSchema.parse({
      type: 'question.ask',
      requestId,
      expectedViewRevision: context.viewRevision,
      question: trimmed,
    }),
  };
}

export const resolveVoiceUtterance = parseUtteranceToAction;

/**
 * End-to-end voice/text command executor.
 */
export function executeVoiceOrTextCommand(
  session: ObservationSession,
  utterance: string,
  modality: 'voice' | 'text' | 'pointer' = 'voice',
  recentPointer?: ActionContext['recentPointer'],
  options: VoiceParseOptions = {}
): {
  session: ObservationSession;
  outcome: ActionOutcome;
  voiceActionRecord: VoiceAction;
} {
  const context = buildActionContext(session, recentPointer);
  const parsed = parseUtteranceToAction(utterance, context, session, options);
  const actionRecordId = `voice_act_${session.viewRevision}_${session.voiceActions.length + 1}`;

  const voiceActionRecord: VoiceAction = VoiceActionSchema.parse({
    id: actionRecordId,
    utterance: utterance.trim() || '(empty)',
    modality,
    context,
    proposedAction: parsed.action,
    createdAtUTC: (options.now ?? new Date()).toISOString(),
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
    // The parser's own reason, never one guessed from the wording of its message
    const reason = parsed.reason ?? 'ambiguous_target';
    const nextActions =
      reason === 'invalid_epoch'
        ? [`Choose a year from ${describeYear(EPOCH_MIN_YEAR_CE)} to ${describeYear(EPOCH_MAX_YEAR_CE)}`]
        : ['Select a star on screen, or name one the sky solution found', 'Ask "What were we looking at?"', 'Say "Back to moment"'];

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
        nextActions,
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
