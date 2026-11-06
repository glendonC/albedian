import { z } from 'zod';
import {
  ActionInterpretationSchema,
  AlbedianActionSchema,
  NeedsInputOutcomeSchema,
  RejectedOutcomeSchema,
} from './spatial-and-voice.js';

/**
 * What `POST /api/voice/command` receives and answers (services/voice/router.py; Python mirror
 * services/contracts/voice.py).
 *
 * The request carries `utterance` (capped at 2,000 UTF-16 code units with a plain message), an optional
 * `context` object, and an optional client-formed `proposedAction` object for the backend to check.
 *
 * The backend holds no session and applies nothing. An action that passes its checks comes back `proposed`,
 * with no state version, for the reducer to apply; the reducer reports the outcome. A request that forms
 * no action, or an action the checks refuse, comes back as the reducer's own `needs_input` or `rejected`
 * outcome, with no action in it: when an action fails AlbedianActionSchema, the refusal (`invalid_action`)
 * lists the issues as field paths and messages, never the payload. `interpretation` names who turned the
 * words into the action, and is absent when the client sent its own action. Every reply is strict, so a
 * state version or any other unknown key is refused, not stripped.
 */

export const UTTERANCE_MAX_UTF16 = 2000;
export const UTTERANCE_MAX_MESSAGE = 'Utterance must be at most 2,000 characters.';

export const VoiceCommandRequestSchema = z.object({
  utterance: z.string().max(UTTERANCE_MAX_UTF16, UTTERANCE_MAX_MESSAGE),
  context: z.record(z.unknown()).optional(),
  proposedAction: z.record(z.unknown()).optional(),
});
export type VoiceCommandRequest = z.infer<typeof VoiceCommandRequestSchema>;

/** The action types whose proposed reply may describe the finding it targets. */
export const FINDING_ACTION_TYPES = ['finding.select', 'finding.describe', 'space.enter'] as const;

export const ProposedVoiceReplySchema = z
  .object({
    status: z.literal('proposed'),
    actionId: z.string().min(1),
    proposedAction: AlbedianActionSchema,
    interpretation: ActionInterpretationSchema.optional(),
    visualStatus: z.string().min(1),
    // The catalog or ephemeris description of the target, for a finding action only
    scientificExplanation: z.string().min(1).optional(),
  })
  .strict();

/** One way an action failed AlbedianActionSchema: where, and what was wrong. It never carries the value. */
export const ActionIssueSchema = z
  .object({
    path: z.array(z.union([z.string(), z.number().int().nonnegative()])),
    message: z.string().min(1),
  })
  .strict();

// A refusal or a question carries no action, so an action that failed the schema never reaches the
// client, which keeps its own request
const refusalExtras = { scientificExplanation: z.string().min(1).optional() };

export const NeedsInputVoiceReplySchema = NeedsInputOutcomeSchema.extend(refusalExtras).strict();
export const RejectedVoiceReplySchema = RejectedOutcomeSchema.extend({
  ...refusalExtras,
  // For an invalid_action refusal only: how the action failed the schema
  issues: z.array(ActionIssueSchema).min(1).optional(),
}).strict();

export const VoiceCommandResponseSchema = z
  .discriminatedUnion('status', [ProposedVoiceReplySchema, NeedsInputVoiceReplySchema, RejectedVoiceReplySchema])
  .superRefine((reply, ctx) => {
    // A discriminated union member cannot carry its own refinement, so the members' rules live here
    if (reply.status === 'rejected' && (reply.reason === 'invalid_action') !== (reply.issues !== undefined)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['issues'],
        message: 'An invalid_action refusal lists its issues, and no other refusal does',
      });
    }
    if (reply.status !== 'proposed') return;
    if (reply.actionId !== reply.proposedAction.requestId) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['actionId'],
        message: "A proposed reply names its action by the action's requestId",
      });
    }
    if (
      reply.scientificExplanation !== undefined &&
      !(FINDING_ACTION_TYPES as readonly string[]).includes(reply.proposedAction.type)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['scientificExplanation'],
        message: 'Only a finding action is described',
      });
    }
  });
export type VoiceCommandResponse = z.infer<typeof VoiceCommandResponseSchema>;
