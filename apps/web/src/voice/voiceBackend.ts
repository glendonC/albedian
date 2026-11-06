import type { ActionContext, ActionOutcome, ObservationSession, VoiceCommandResponse } from '@albedian/contracts';
import { VoiceCommandRequestSchema, VoiceCommandResponseSchema } from '@albedian/contracts';
import { buildActionContext, dispatchAlbedianAction, isPointerFreshAndMatchingRevision } from '@albedian/observation';

/**
 * ============================================================================
 * @albedian/web: the voice backend, asked after a local refusal
 * ============================================================================
 *
 * The backend (`POST /api/voice/command`, services/voice/router.py) holds no session and applies nothing.
 * It may read words the app's own parser cannot, through its rules or, when the owner has turned it on, a
 * model. Its reply is parsed with the contract's VoiceCommandResponseSchema, and a proposed action goes
 * through the app's own reducer, which decides: an action that went stale while the request was out is
 * refused there.
 */

/**
 * The local refusals the backend may resolve: no target, or no rule matched the words. The backend refuses
 * every other one (stale, unsafe, a year, not connected, an unresolved sky, no distance) the same way.
 */
export const BACKEND_MAY_RESOLVE = ['ambiguous_target', 'no_grounded_answer'] as const;

type RejectionReason = Extract<ActionOutcome, { status: 'rejected' }>['reason'];

export function backendMayResolve(reason: RejectionReason): boolean {
  return (BACKEND_MAY_RESOLVE as readonly string[]).includes(reason);
}

/**
 * Asks the backend to read `utterance` against the parser's own context, plus the recording's place and
 * capture time, which the backend needs to place a planet. The pointer goes along only when the parser
 * itself would use it. Returns null when the backend is unreachable, answers with an error status, or
 * answers anything the contract refuses: nothing stands in for a reply.
 */
export async function askVoiceBackend(
  utterance: string,
  session: ObservationSession,
  recentPointer?: ActionContext['recentPointer']
): Promise<VoiceCommandResponse | null> {
  try {
    const unpointed = buildActionContext(session);
    const pointer = isPointerFreshAndMatchingRevision(recentPointer, unpointed) ? recentPointer : undefined;
    const payload = {
      utterance,
      context: {
        ...buildActionContext(session, pointer),
        observerLat: session.sourceAsset.observerLocation?.latDeg ?? null,
        observerLon: session.sourceAsset.observerLocation?.lonDeg ?? null,
        observedAtUTC: session.sourceAsset.capturedAtUTC ?? null,
      },
    };
    if (!VoiceCommandRequestSchema.safeParse(payload).success) return null;
    const res = await fetch('/api/voice/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) return null;
    const reply = VoiceCommandResponseSchema.safeParse(await res.json());
    return reply.success ? reply.data : null;
  } catch {
    return null;
  }
}

export interface AppliedVoiceReply {
  session: ObservationSession;
  /** The reducer's outcome for a proposed action; absent when the backend refused or asked. */
  outcome?: ActionOutcome;
  /** The finding to fly to, when a dispatched space.enter completed. */
  flyToFindingId?: string;
}

function sentences(...parts: Array<string | undefined>): string {
  return parts.filter((part): part is string => Boolean(part)).join(' ');
}

/**
 * Applies one backend reply to the session as it stands when the reply arrives.
 *
 * A proposed action goes through the reducer. When it takes effect (completed, or a transition canceled),
 * the banner shows the reducer's own text, from the user's session, then the backend's description of the
 * target where the reply carries one (the catalog row or the ephemeris model, in their own words). When the
 * reducer refuses it or asks, the banner shows only that. A refusal or a question from the backend is shown as the backend words it. Whenever a
 * model read the words, the banner says which model answered.
 */
export function applyVoiceBackendReply(session: ObservationSession, reply: VoiceCommandResponse): AppliedVoiceReply {
  const interpreted =
    reply.interpretation?.parser === 'model' ? `Interpreted by ${reply.interpretation.servedBy}.` : undefined;

  if (reply.status === 'proposed') {
    const action = reply.proposedAction;
    const dispatched = dispatchAlbedianAction(session, action);
    const outcome = dispatched.outcome;
    if (outcome.status === 'completed' || outcome.status === 'canceled') {
      return {
        session: {
          ...dispatched.session,
          lastStatusBanner: sentences(dispatched.session.lastStatusBanner, reply.scientificExplanation, interpreted),
        },
        outcome,
        ...(outcome.status === 'completed' && action.type === 'space.enter' ? { flyToFindingId: action.findingId } : {}),
      };
    }
    const refusal = outcome.status === 'rejected' ? outcome.explanation : outcome.question;
    return { session: { ...dispatched.session, lastStatusBanner: refusal }, outcome };
  }

  const said = reply.status === 'rejected' ? reply.explanation : reply.question;
  return { session: { ...session, lastStatusBanner: sentences(said, interpreted) } };
}
