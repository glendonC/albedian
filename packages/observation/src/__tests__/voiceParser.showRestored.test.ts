import { describe, expect, it } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { buildActionContext, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * voice-show-restored-one (FIELD's walk; the lead's message of 25 Nov): asked to "show the restored version" on a
 * restored photo, voice answered "Nothing in this photo's data answers that.", since the restore rule's word
 * "restore" is read whole and "restored" is another word, so the sentence went on as a question. Asking to be shown
 * the restored version is a restore (`media.restore`), as "make the sky clearer" is; a question about whether a photo
 * was restored stays a question. Both parsers read the one grammar (packages/contracts/src/data/voice-command-grammar.json),
 * as tests/integration/test_voice_show_restored.py holds for the backend.
 */
const parse = (text: string, session: ObservationSession = createSyntheticCalibratedSession()) =>
  parseUtteranceToAction(text, buildActionContext(session), session);

describe('@albedian/observation voice parser: asking to be shown the restored version', () => {
  it.each([
    'show the restored version',
    'Show me the restored version',
    'show the restored photo',
    'show the restored clip',
    'show the restoration',
    'show me the restoration',
    'the restored version',
    'restored version please',
    'let me see the restored version',
    'see the restored one',
  ])('%s is a restore', (text) => {
    const result = parse(text);
    expect(result.action?.type, text).toBe('media.restore');
  });

  it.each(['is it restored', 'has it been restored', 'was that restored', 'have you restored it', 'I already restored it'])(
    '%s is not a request to restore',
    (text) => {
      expect(parse(text).action?.type, text).not.toBe('media.restore');
    }
  );

  it('the original beside the restored version is a compare, as before', () => {
    expect(parse('compare the original with the restored version').action?.type).toBe('media.compare');
    expect(parse('show original').action?.type).toBe('media.compare');
  });
});
