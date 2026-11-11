import { describe, expect, it } from 'vitest';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { BRIGHTEST_PATTERN, buildActionContext, executeVoiceOrTextCommand, parseUtteranceToAction } from '../voiceParser.js';
import { createEmptyObservationSession } from '../emptySession.js';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions.js';

/**
 * "What is the brightest star in my photo" is answered from the brightness measured in the photo's own pixels
 * (each finding's sourceDetection flux on the source asset), never from catalog magnitudes, never from what
 * is selected. When the top fluxes agree within their measurement uncertainty (flux / SNR), the parser asks
 * which one rather than choosing. A brightest in anything other than the photo is refused.
 *
 * Synthetic calibrated session, measured on its synthetic clip at 4.2 s: Sirius 42800 (SNR 98.7),
 * Rigel 22950, Procyon 19650, Betelgeuse 18420, ... Jupiter and Uranus are predicted, with no detection.
 */

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const base = createSyntheticCalibratedSession();
const SELECTED = 'finding_betelgeuse';

function withFindings(edit: (f: CelestialFinding) => CelestialFinding, session: ObservationSession = base): ObservationSession {
  return {
    ...session,
    selectedFindingId: SELECTED,
    observationSolution: { ...session.observationSolution!, findings: session.observationSolution!.findings.map((f) => edit(structuredClone(f))) },
  };
}

const selected = withFindings((f) => f);

function parse(utterance: string, session: ObservationSession = selected) {
  return parseUtteranceToAction(utterance, buildActionContext(session), session, { now: NOW });
}

function targetOf(utterance: string, session: ObservationSession = selected): string | undefined {
  const action = parse(utterance, session).action;
  return action && 'findingId' in action ? action.findingId : undefined;
}

describe('@albedian/observation voice parser: the brightest star in the photo', () => {
  it.each([
    'what is the brightest star in my photo',
    'What is the brightest star in my photo?',
    'brightest star',
    'which is the brightest star here',
    'show me the brightest one',
    'select the brightest star in the picture',
    "what's the brightest object in this image",
    'brightest star on screen',
    'the brightest star in view',
    'tell me about the brightest star in the photo',
  ])('"%s" selects the brightest measured finding, not the selection', (utterance) => {
    const parsed = parse(utterance);
    expect(parsed.action?.type).toBe('finding.select');
    expect(targetOf(utterance)).toBe('finding_sirius');
  });

  it('flies to the brightest measured finding when asked to go there', () => {
    const parsed = parse('take me to the brightest star');
    expect(parsed.action?.type).toBe('space.enter');
    expect(targetOf('take me to the brightest star')).toBe('finding_sirius');
  });

  it('says the answer comes from measured flux in this photo, then the card line', () => {
    const photo: ObservationSession = { ...selected, sourceAsset: { ...selected.sourceAsset!, mediaKind: 'image' } };
    const result = executeVoiceOrTextCommand(photo, 'what is the brightest star in my photo', 'voice', undefined, { now: NOW });
    expect(result.outcome.status).toBe('completed');
    expect(result.session.selectedFindingId).toBe('finding_sirius');
    expect(result.session.lastStatusBanner).toMatch(/^Brightest by measured flux in this photo: Sirius/);
  });

  it('says video, not photo, for a video (the synthetic session is a video)', () => {
    expect(selected.sourceAsset?.mediaKind).toBe('video');
    const result = executeVoiceOrTextCommand(selected, 'brightest star', 'voice', undefined, { now: NOW });
    expect(result.session.lastStatusBanner).toMatch(/^Brightest by measured flux in this video: Sirius/);
  });

  it('asks which one when the top two agree within their measurement uncertainty', () => {
    // Rigel at 42600 with SNR 92.5 (sigma 460) against Sirius 42800 with SNR 98.7 (sigma 434): 200 apart, under 632
    const close = withFindings((f) => (f.id === 'finding_rigel' ? { ...f, sourceDetection: { ...f.sourceDetection!, flux: 42600 } } : f));
    const parsed = parse('what is the brightest star in my photo', close);
    expect(parsed.action).toBeUndefined();
    expect(parsed.disambiguation?.choices.map((c) => c.id)).toEqual(['finding_sirius', 'finding_rigel']);
    expect(parsed.disambiguation?.question).toMatch(/measurement uncertainty/);
    const result = executeVoiceOrTextCommand(close, 'brightest star', 'voice', undefined, { now: NOW });
    expect(result.outcome.status).toBe('needs_input');
  });

  it('chooses when the runner-up is just outside the combined uncertainty', () => {
    // sqrt(434^2 + 446^2) = 622: Rigel at 42170 with SNR 94.5 sits 630 below Sirius
    const apart = withFindings((f) =>
      f.id === 'finding_rigel' ? { ...f, sourceDetection: { ...f.sourceDetection!, flux: 42170, snr: 94.5 } } : f
    );
    expect(targetOf('brightest star', apart)).toBe('finding_sirius');
  });

  it('asks between the two faint sources of the overcast session, which agree within their noise', () => {
    const overcast = createSyntheticUnresolvedSession();
    const parsed = parse('what is the brightest star in my photo', overcast);
    expect(parsed.disambiguation?.choices.map((c) => c.id)).toEqual(['finding_obs_overcast_1', 'finding_obs_overcast_2']);
  });

  it('never ranks a predicted finding, or a detection measured in another asset', () => {
    const odd = withFindings((f) => {
      // The contract now refuses a predicted finding with a detection (REQ-SKY-5.2); this case checks the parser's own guard
      if (f.id === 'finding_jupiter') return { ...f, sourceDetection: { ...base.observationSolution!.findings[0]!.sourceDetection!, flux: 1e9, snr: 1e4 } };
      if (f.id === 'finding_sirius') return { ...f, sourceDetection: { ...f.sourceDetection!, assetId: 'asset_some_variant' } };
      return f;
    });
    expect(targetOf('brightest star', odd)).toBe('finding_rigel');
  });

  it('compares only detections from the frame nearest the current time', () => {
    const frames = withFindings((f) => (f.id === 'finding_sirius' ? { ...f, sourceDetection: { ...f.sourceDetection!, frameTimeSec: 10 } } : f));
    expect(targetOf('brightest star', { ...frames, currentSourceTimeSec: 4.2 })).toBe('finding_rigel');
    expect(targetOf('brightest star', { ...frames, currentSourceTimeSec: 9 })).toBe('finding_sirius');
  });

  it('refuses plainly when no finding carries a measured brightness, and never takes the selection', () => {
    const unmeasured = withFindings((f) => {
      const { sourceDetection: _dropped, ...rest } = f;
      return rest.evidenceClass === 'observed' ? f : (rest as CelestialFinding);
    });
    const noObserved: ObservationSession = {
      ...unmeasured,
      observationSolution: {
        ...unmeasured.observationSolution!,
        findings: unmeasured.observationSolution!.findings.filter((f) => f.evidenceClass !== 'observed'),
      },
    };
    const parsed = parse('what is the brightest star in my photo', noObserved);
    expect(parsed.action).toBeUndefined();
    expect(parsed.reason).toBe('no_grounded_answer');
    expect(parsed.error).toMatch(/measured/);
    const empty = createEmptyObservationSession();
    expect(parse('brightest star', empty).reason).toBe('no_grounded_answer');
  });

  it.each([
    'what is the brightest star in Orion',
    'the brightest star in the sky',
    'brightest star of the southern cross',
    'what is the second brightest star in my photo',
    'third-brightest star',
  ])('refuses "%s": only the single brightest in this photo is named', (utterance) => {
    const parsed = parse(utterance);
    expect(parsed.action).toBeUndefined();
    expect(parsed.disambiguation).toBeUndefined();
    expect(parsed.reason).toBe('no_grounded_answer');
    expect(parsed.error).toMatch(/brightest/);
  });

  it.each([
    'what is the brightest planet in my photo',
    'brightest galaxy',
    'the brightest nebula here',
    'take me to the brightest cluster',
    'which is the brightest moon',
    'brightest deep-sky object',
    'the brightest deep sky object',
  ])('refuses "%s": measured brightness ranks stars and points of light only', (utterance) => {
    const parsed = parse(utterance);
    expect(parsed.action).toBeUndefined();
    expect(parsed.reason).toBe('no_grounded_answer');
    expect(parsed.error).toMatch(/ranks stars and points of light only, so a brightest planet, moon, galaxy, nebula or cluster is not named/);
  });

  it.each(['the brightest red star', 'brightest visible star'])('refuses "%s": only measured brightness ranks', (utterance) => {
    const parsed = parse(utterance);
    expect(parsed.action).toBeUndefined();
    expect(parsed.reason).toBe('no_grounded_answer');
    expect(parsed.error).toMatch(/by measured brightness alone/);
  });

  it.each(['brightest light in the photo', 'the brightest point', 'brightest thing here', 'which is the brightest of them all', 'brightest?'])(
    '"%s" is a star or an unqualified word, so it is answered',
    (utterance) => {
      expect(targetOf(utterance)).toBe('finding_sirius');
    }
  );

  it('reads brightest only as a whole word', () => {
    expect(BRIGHTEST_PATTERN.test('what is the brightest star')).toBe(true);
    expect(BRIGHTEST_PATTERN.test('brighten the photo')).toBe(false);
    expect(parse('select betelgeuse').action).toMatchObject({ type: 'finding.select', findingId: 'finding_betelgeuse' });
  });
});
