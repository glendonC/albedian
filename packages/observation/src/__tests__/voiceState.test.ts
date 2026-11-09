import { describe, expect, it } from 'vitest';
import type { ObservationSession, VoiceMachineState } from '@albedian/contracts';
import { dispatchAlbedianAction } from '../reducer.js';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

/**
 * The voice machine says listening only while a microphone capture is open: apps/web's push-to-talk sets it when
 * the capture opens and off when it ends (apps/web/src/__tests__/pushToTalkSession.test.tsx). No action decides
 * the microphone, so every action leaves the voice state as it found it, whether a button or a spoken command
 * dispatched it. Before language-7 the reducer set listening after restoring media, comparing, showing the sky,
 * entering space and moving the viewpoint, and when restoring a checkpoint, so the voice bar said "Listening" and
 * the recording ducked to 35 percent with no microphone on.
 */

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));

function withVoice(session: ObservationSession, voice: VoiceMachineState): ObservationSession {
  return { ...session, machines: { ...session.machines, voice } };
}

/** The journey, each step the action the parser forms, dispatched straight to the reducer as a button does. */
const JOURNEY: Array<[string, string]> = [
  ['media.restore', 'make the sky clearer'],
  ['media.compare', 'compare before and after'],
  ['sky.show', 'what were we looking at'],
  ['finding.select', 'select betelgeuse'],
  ['space.enter', 'take me there'],
  ['space.viewpoint', 'show it from another angle'],
  ['history.back', 'go back to the previous checkpoint'],
  ['source.return', 'back to moment'],
];

describe('@albedian/observation voice state: no action decides the microphone', () => {
  it.each(['off', 'listening'] as const)('every step of the journey leaves voice %s as it found it', (voice) => {
    let session = withVoice(createSyntheticCalibratedSession(), voice);
    for (const [type, utterance] of JOURNEY) {
      const parsed = parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
      expect(parsed.action?.type, utterance).toBe(type);
      const result = dispatchAlbedianAction(session, parsed.action!);
      expect(result.outcome.status, utterance).toBe('completed');
      session = result.session;
      expect(session.machines.voice, type).toBe(voice);
    }
  });

  it('still says interrupted when a request stops a flight (barge-in)', () => {
    const session = withVoice(createSyntheticCalibratedSession(), 'off');
    const parsed = parseUtteranceToAction('stop', buildActionContext(session), session, { now: NOW });
    expect(parsed.action?.type).toBe('navigation.cancel');
    expect(dispatchAlbedianAction(session, parsed.action!).session.machines.voice).toBe('interrupted');
  });
});
