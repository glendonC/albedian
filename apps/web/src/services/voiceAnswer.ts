import {
  VoiceQuestionReplySchema,
  VoiceQuestionRequestSchema,
  type ActionContext,
  type ObservationSession,
  type VoiceQuestionReply,
} from '@albedian/contracts';
import { buildActionContext, isPointerFreshAndMatchingRevision, parseUtteranceToAction } from '@albedian/observation';

/**
 * ============================================================================
 * @albedian/web: asking a question about the photo (ask, approved 2026-11-12)
 * ============================================================================
 *
 * The answer route (POST /api/voice/answer/{sha256}, services/voice) answers a question from the upload's own solve,
 * catalog rows and ephemeris, in the contract's typed answer: one sentence and its statements, or a refusal. Commands
 * stay with the app's own parser and reducer.
 */

/** How long the app waits for the answer route; past it the route counts as not answering. */
export const VOICE_ANSWER_TIMEOUT_MS = 20_000;

const SHA256 = /^[0-9a-f]{64}$/;

/** The actions that ask about a star rather than change the view. */
const QUESTION_ACTIONS: ReadonlySet<string> = new Set(['finding.describe', 'question.ask']);

/**
 * Whether these words are a question for the answer route: the app's own parser reads them as asking about a star,
 * or reads no command in them at all (a question no command answers, such as "Is that a plane?"). Words it reads as a
 * command, or whose target it asks about, stay with the app.
 */
export function isQuestion(utterance: string, session: ObservationSession, recentPointer?: ActionContext['recentPointer']): boolean {
  const unpointed = buildActionContext(session);
  const pointer = isPointerFreshAndMatchingRevision(recentPointer, unpointed) ? recentPointer : undefined;
  const parsed = parseUtteranceToAction(utterance, buildActionContext(session, pointer), session);
  if (parsed.disambiguation) return false;
  if (parsed.action) return QUESTION_ACTIONS.has(parsed.action.type);
  return parsed.reason === 'no_grounded_answer';
}

/**
 * Asks the answer route about the upload named by `sha256`, with the words as heard and the picked star. Returns null
 * when the address is not 64 lowercase hex or the words are refused by the contract (nothing is sent then), and when
 * the route does not answer, answers with an error status or answers anything the contract refuses.
 */
export async function askVoiceAnswer(
  sha256: string,
  question: string,
  selectedFindingId: string | null,
  { timeoutMs = VOICE_ANSWER_TIMEOUT_MS }: { timeoutMs?: number } = {}
): Promise<VoiceQuestionReply | null> {
  if (!SHA256.test(sha256)) return null;
  const request = VoiceQuestionRequestSchema.safeParse(selectedFindingId ? { question, selectedFindingId } : { question });
  if (!request.success) return null;
  try {
    const res = await fetch(`/api/voice/answer/${sha256}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request.data),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return null;
    const reply = VoiceQuestionReplySchema.safeParse(await res.json());
    return reply.success ? reply.data : null;
  } catch {
    return null;
  }
}
