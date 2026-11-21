import { z } from 'zod';
import {
  ActionInterpretationSchema,
  AlbedianActionSchema,
  NeedsInputOutcomeSchema,
  RejectedOutcomeSchema,
} from './spatial-and-voice.js';
import { isLetterOrDigit } from './voice-answer.js';
import VOICE_COMMAND_GRAMMAR_JSON from './data/voice-command-grammar.json';

/**
 * The voice command grammar as written (data/voice-command-grammar.json): the words that ask for each command and
 * the order both parsers try them in. Plain data, not a contract: each parser loads it with its own strict loader
 * (packages/observation/src/voiceGrammar.ts, services/voice/grammar.py), so it is typed unknown here.
 */
export const VOICE_COMMAND_GRAMMAR_TABLE: unknown = VOICE_COMMAND_GRAMMAR_JSON;

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

/**
 * Why a request has no target, as a kind a screen or a test reads without reading English, with what the person
 * said (`said`, their own words as the parser read them, in lower case), the name of the app's tables it stands for
 * (`name`, the one the sentence shows) and, for a deep-sky object voice cannot reach, the catalogue row that holds
 * it. The sentence shown is made from the refusal by `refusalSentence`, here and in services/contracts/voice.py.
 * Each kind names the rejection reasons it goes out under, whether it carries `said` and `catalogRow`, and whether
 * the backend can know it: the backend holds no photo, so the kinds that need the photo's sky are the app
 * parser's alone (packages/observation/src/voiceParser.ts) and a voice reply never carries one.
 */
export const REFUSAL_KINDS = {
  // A word no list holds: it may be a name the parser does not know
  not_understood: { reasons: ['ambiguous_target'], said: true, name: false, catalogRow: false, backend: true },
  // A star or body the name tables know, not in this photo's sky
  not_in_this_sky: { reasons: ['ambiguous_target'], said: true, name: true, catalogRow: false, backend: false },
  // A deep-sky object a catalogue holds, which voice cannot reach yet
  deep_sky_out_of_reach: { reasons: ['no_grounded_answer'], said: true, name: true, catalogRow: true, backend: true },
  // A sky name no catalogue of the app holds by name
  no_named_row: { reasons: ['no_grounded_answer'], said: true, name: true, catalogRow: false, backend: true },
  // The Southern Cross, none of whose stars this sky holds
  southern_cross_not_in_this_sky: { reasons: ['ambiguous_target'], said: false, name: false, catalogRow: false, backend: false },
  // A thing described but not named, so it could be any of several
  which_one: { reasons: ['ambiguous_target'], said: false, name: false, catalogRow: false, backend: true },
  // A pointing word, or a command said alone, with nothing selected, focused or pointed at
  nothing_in_view: { reasons: ['ambiguous_target'], said: false, name: false, catalogRow: false, backend: true },
  // A name asked for in a sky not worked out, so nothing in it has a name yet
  sky_not_worked_out: { reasons: ['ambiguous_target', 'unresolved_sky'], said: false, name: false, catalogRow: false, backend: false },
  // Nothing in view, in a sky the view marks unresolved
  nothing_in_view_sky_not_worked_out: { reasons: ['unresolved_sky'], said: false, name: false, catalogRow: false, backend: false },
  // A sentence that changes the scene with a word its rule does not account for: no action (answers-3b v4)
  not_a_request: { reasons: ['not_one_request'], said: true, name: false, catalogRow: false, backend: true },
  // A sentence that holds two requests that change the scene: neither is done
  two_requests: { reasons: ['not_one_request'], said: false, name: false, catalogRow: false, backend: true },
} as const satisfies Record<
  string,
  { reasons: readonly RejectionReason[]; said: boolean; name: boolean; catalogRow: boolean; backend: boolean }
>;
type RejectionReason = z.infer<typeof RejectedOutcomeSchema>['reason'];
export type RefusalKind = keyof typeof REFUSAL_KINDS;
export const RefusalKindSchema = z.enum(Object.keys(REFUSAL_KINDS) as [RefusalKind, ...RefusalKind[]]);
/** [proposed] What a refusal quotes back: at most six words and 80 characters with " ..." (services/voice/names.py
 * MAX_QUOTED_*), so 80 astral characters and the cut mark fit. Counted in UTF-16 code units. */
export const REFUSAL_SAID_MAX_UTF16 = 200;
export const CATALOG_ROW_MAX_UTF16 = 32;
/** [proposed] A name of the app's tables, as long as any text of the answer contract may be. */
export const REFUSAL_NAME_MAX_UTF16 = 128;
/** An OpenNGC row name as the baked table writes it (NGC0292, ESO056-115, C099). */
export const CATALOG_ROW_PATTERN = /^[A-Za-z0-9]+(?:[- ][A-Za-z0-9]+)*$/;
/** The Southern Cross asterism's stars, as the refusal names them (services/voice/names.py SOUTHERN_CROSS_STARS). */
export const SOUTHERN_CROSS_STAR_NAMES = ['Acrux', 'Mimosa', 'Gacrux', 'Imai', 'Ginan'] as const;
/** The media word of the app parser's sentences. */
export type RefusalMedia = 'photo' | 'video';

// Fixed code point ranges, never a runtime's Unicode property tables: a surrogate standing alone (in u mode a pair is
// one code point) and the C0 and C1 controls
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;
/** What may stand between two letters of a name, as the name tables write names ("Eta Carinae", "Al Na'ir"). */
export const REFUSAL_NAME_SEPARATORS = [' ', "'"] as const;

/**
 * A name as the name tables write it: an ASCII letter or digit first ("Vega", "the Moon", "47 Tucanae"), then letters
 * and digits of the tracked letters table, with one space or apostrophe between two of them. The first character is
 * ASCII so the sentence's capital is made by a fixed rule (`capitalized`), the same in every runtime. Held by every
 * name of both parsers' tables (answers-3 v2 report).
 */
export function isTableName(name: string): boolean {
  const chars = [...name];
  if (chars.length === 0 || !/^[A-Za-z0-9]$/.test(chars[0]!)) return false;
  return chars.every((char, index) => {
    if ((REFUSAL_NAME_SEPARATORS as readonly string[]).includes(char)) {
      const next = chars[index + 1];
      return next !== undefined && !(REFUSAL_NAME_SEPARATORS as readonly string[]).includes(next);
    }
    return isLetterOrDigit(char.codePointAt(0)!);
  });
}
/** Text of 1 to `max` UTF-16 units with no lone surrogate and no control character (the lead's wire rules). */
function boundedText(max: number) {
  return z
    .string()
    .min(1)
    .max(max)
    .refine((s) => !LONE_SURROGATE.test(s), 'A text holds no lone surrogate.')
    .refine((s) => !CONTROL.test(s), 'A text holds no control character.');
}

export const VoiceRefusalSchema = z
  .object({
    kind: RefusalKindSchema,
    said: boundedText(REFUSAL_SAID_MAX_UTF16).optional(),
    name: boundedText(REFUSAL_NAME_MAX_UTF16).refine(isTableName, 'A name is written as the name tables write it.').optional(),
    catalogRow: boundedText(CATALOG_ROW_MAX_UTF16)
      .refine((row) => CATALOG_ROW_PATTERN.test(row), 'A catalogue row is named as its table names it.')
      .optional(),
  })
  .strict()
  .superRefine((refusal, ctx) => {
    const spec = REFUSAL_KINDS[refusal.kind];
    if (spec.said !== (refusal.said !== undefined)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['said'], message: `A ${refusal.kind} refusal ${spec.said ? 'quotes' : 'quotes nothing'} said.` });
    }
    if (spec.name !== (refusal.name !== undefined)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['name'], message: `A ${refusal.kind} refusal ${spec.name ? 'names' : 'names no'} the name it stands for.` });
    }
    if (spec.catalogRow !== (refusal.catalogRow !== undefined)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['catalogRow'], message: `A ${refusal.kind} refusal ${spec.catalogRow ? 'names' : 'names no'} catalogue row.` });
    }
  });
export type VoiceRefusal = z.infer<typeof VoiceRefusalSchema>;

/** `text` with an ASCII small letter first raised to its capital, and any other first character left as it is: a
 * fixed rule, never a runtime's case tables, so both languages make the same sentence. */
function capitalized(text: string): string {
  const first = text.charCodeAt(0);
  return first >= 0x61 && first <= 0x7a ? String.fromCharCode(first - 0x20) + text.slice(1) : text;
}

/** The one sentence a refusal is shown as, from its kind, what was said, and the recording's media word. */
export function refusalSentence(refusal: VoiceRefusal, media: RefusalMedia = 'photo'): string {
  // The token stays the contract's (REFUSAL_MEDIA); the sentence names a video "clip", the decided noun (C35)
  const word = media === 'video' ? 'clip' : media;
  const said = refusal.said ?? '';
  const name = refusal.name ?? '';
  switch (refusal.kind) {
    case 'not_understood':
      // [proposed, for the UIUX work] It fits a name no list holds ("pluto") as well as a word: it asks for nothing
      return `I did not understand "${said}": no list of the app holds that word.`;
    case 'not_in_this_sky':
      return `${capitalized(name)} was not found in this ${word}'s sky.`;
    case 'deep_sky_out_of_reach':
      return `${capitalized(name)} is in the app's deep-sky catalogue (OpenNGC ${refusal.catalogRow ?? ''}), but voice does not reach deep-sky objects yet, so it cannot be selected, described or flown to.`;
    case 'no_named_row':
      return `${capitalized(name)} has no named row in the app's catalogues yet, so it cannot be selected, described or flown to by name.`;
    case 'southern_cross_not_in_this_sky':
      return `The Southern Cross is a constellation, not one star, and none of its stars (${SOUTHERN_CROSS_STAR_NAMES.join(', ')}) is in the sky solution.`;
    case 'which_one':
      return 'That does not say which one. Say its name, or select it and say "that one".';
    case 'nothing_in_view':
      return 'Nothing is selected or pointed at. Select a star or planet first, or say its name.';
    case 'sky_not_worked_out':
      return `The sky in this ${word} has not been worked out, so nothing in it can be found by name.`;
    case 'nothing_in_view_sky_not_worked_out':
      return `The sky in this ${word} has not been worked out, and nothing is selected or pointed at.`;
    case 'not_a_request':
      // [proposed, for the UIUX work]
      return `I did not take that as a request: "${said}" is not a word of one.`;
    case 'two_requests':
      // [proposed, for the UIUX work]
      return 'That asks for two things at once; say one at a time.';
  }
}

// A refusal or a question carries no action, so an action that failed the schema never reaches the
// client, which keeps its own request
const refusalExtras = { scientificExplanation: z.string().min(1).optional() };

export const NeedsInputVoiceReplySchema = NeedsInputOutcomeSchema.extend(refusalExtras).strict();
export const RejectedVoiceReplySchema = RejectedOutcomeSchema.extend({
  ...refusalExtras,
  // For an invalid_action refusal only: how the action failed the schema
  issues: z.array(ActionIssueSchema).min(1).optional(),
  // Why the request has no target: every ambiguous_target refusal says so, and its explanation is the sentence
  refusal: VoiceRefusalSchema.optional(),
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
    if (reply.status === 'rejected') {
      const refusal = reply.refusal;
      if (refusal === undefined) {
        if (reply.reason === 'ambiguous_target' || reply.reason === 'not_one_request') {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['refusal'], message: `A ${reply.reason} refusal says which kind it is` });
        }
      } else {
        const spec = REFUSAL_KINDS[refusal.kind];
        if (!spec.backend) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['refusal', 'kind'], message: `A ${refusal.kind} refusal needs the photo's sky, which the backend does not hold` });
        }
        if (!(spec.reasons as readonly string[]).includes(reply.reason)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['reason'], message: `A ${refusal.kind} refusal goes out as ${spec.reasons.join(' or ')}` });
        }
        if (reply.explanation !== refusalSentence(refusal)) {
          ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['explanation'], message: "A refusal's explanation is its sentence" });
        }
      }
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
