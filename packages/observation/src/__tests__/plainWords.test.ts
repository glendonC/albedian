import type { AlbedianAction, ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { createEmptyObservationSession, dispatchAlbedianAction } from '../index';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions';

/**
 * reducer-plain-words-1: every refusal the reducer gives that no record held now says the sentence
 * `.lead/uiux/UIUX-predflight-READY.md`'s table rules for it (RULED 12:56 on 24 Nov), the recording named by its own
 * noun, "photo" or "clip" (C35).
 */
const asPhoto = (session: ObservationSession): ObservationSession =>
  ({ ...session, sourceAsset: session.sourceAsset ? { ...session.sourceAsset, mediaKind: 'image' } : undefined }) as ObservationSession;

function explanation(session: ObservationSession, action: Record<string, unknown>): string {
  const result = dispatchAlbedianAction(session, { requestId: 'req_words', expectedViewRevision: session.viewRevision, ...action } as AlbedianAction);
  expect(result.outcome.status, JSON.stringify(action)).toBe('rejected');
  return result.outcome.status === 'rejected' ? result.outcome.explanation : '';
}

describe('@albedian/observation: the reducer refuses in the ruled plain words', () => {
  const clip = createSyntheticCalibratedSession();
  const photo = asPhoto(clip);
  const empty = createEmptyObservationSession();
  const restore = {
    type: 'media.restore', sourceAssetId: clip.sourceAsset!.id, profile: 'natural',
    constraints: { preservePeople: true, allowSyntheticSky: false, protectStars: true },
  };

  it('a request about a screen that has since changed', () => {
    const result = dispatchAlbedianAction(clip, { type: 'ui.overlay', requestId: 'req_old', expectedViewRevision: clip.viewRevision - 1, overlay: 'constellationLines', enabled: true } as AlbedianAction);
    expect(result.outcome).toMatchObject({ status: 'rejected', reason: 'stale_revision', explanation: 'The screen changed while you asked.' });
  });

  it('restoration and its compare', () => {
    expect(explanation(clip, { ...restore, sourceAssetId: 'src_another' })).toBe('Restore works on the clip on screen.');
    expect(explanation(photo, { ...restore, sourceAssetId: 'src_another' })).toBe('Restore works on the photo on screen.');
    const synthetic = { ...restore, constraints: { ...restore.constraints, allowSyntheticSky: true } };
    expect(explanation(photo, synthetic)).toBe("Nothing is generated here: a restoration uses only your photo's own pixels.");
    const unrestored = { ...clip, mediaVariants: clip.mediaVariants.filter((v) => v.kind !== 'restoration') };
    expect(explanation(unrestored, restore)).toBe('This clip has not been restored.');
    expect(explanation(asPhoto(unrestored), restore)).toBe('This photo has not been restored.');
    // voice-compare-one: UIUX's two compare sentences (DECIDED-BY-LEAD 24 Nov 19:42); tests/voiceCompare.test.ts holds the rest
    expect(explanation(unrestored, { type: 'media.compare', variantAId: 'var_missing_a', variantBId: 'var_missing_b' })).toBe(
      'This clip has not been restored, so there is nothing to compare it with.'
    );
    const restored = clip.mediaVariants.find((v) => v.kind === 'restoration')!.id;
    expect(explanation(asPhoto(clip), { type: 'media.compare', variantAId: restored, variantBId: restored })).toBe(
      'That would compare a version of this photo with itself, so nothing was compared.'
    );
  });

  it('moving through a clip that is not there', () => {
    expect(explanation(empty, { type: 'media.seek', sourceTimeSec: 1 })).toBe('There is no clip here to move through.');
  });

  it('showing the sky', () => {
    expect(explanation(empty, { type: 'sky.show', mode: 'predicted' })).toBe('There is no photo or clip here, so there is no sky to show.');
    const unresolved = createSyntheticUnresolvedSession();
    const unsolved = { ...unresolved, machines: { ...unresolved.machines, calibration: 'unresolved' as const },
      observationSolution: { ...unresolved.observationSolution!, status: 'unresolved' as const } };
    const noTime = { ...unsolved, sourceAsset: { ...unsolved.sourceAsset!, capturedAtUTC: undefined } } as ObservationSession;
    // The noun's own verb, as the sky layer's words say it (skyLayerWords.ts): a clip is recorded, a photo taken
    expect(explanation(noTime, { type: 'sky.show', mode: 'predicted' })).toMatch(/ The clip does not say when it was recorded\.$/);
    expect(explanation(asPhoto(noTime), { type: 'sky.show', mode: 'predicted' })).toMatch(/ The photo does not say when it was taken\.$/);
    expect(explanation(unresolved, { type: 'sky.show', mode: 'measured' })).toBe(
      "This clip's stars were not matched, so the real sky cannot be laid over it."
    );
  });

  it('a flight from a sky not worked out', () => {
    const unresolved = createSyntheticUnresolvedSession();
    const unsolved = { ...unresolved, machines: { ...unresolved.machines, calibration: 'unresolved' as const } };
    expect(explanation(unsolved, { type: 'space.enter', findingId: 'finding_pred_vega', distancePolicy: 'measured_or_estimated' })).toBe(
      'The sky in this clip has not been worked out, so nothing in it can be flown to.'
    );
  });

  it('what voice cannot do in this app', () => {
    expect(explanation(clip, { type: 'ui.overlay', overlay: 'constellationLines', enabled: true })).toBe('Voice cannot do that in this app.');
    expect(explanation(photo, { type: 'space.epoch', epochISO: '1900-01-01T00:00:00Z', yearCE: 1900 })).toBe(
      "The sky is shown only at your photo's own time."
    );
    expect(explanation(clip, { type: 'question.ask', question: 'what is an orbit?' })).toBe(
      "That question cannot be answered from your clip's data."
    );
  });
});
