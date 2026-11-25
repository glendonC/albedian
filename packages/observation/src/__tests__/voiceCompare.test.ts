import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { MediaVariantSchema, type AlbedianAction, type MediaVariant, type ObservationSession } from '@albedian/contracts';
import { EMPTY_MEDIA_VARIANT_ID } from '../emptySession';
import { dispatchAlbedianAction } from '../reducer';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * voice-compare-one (DECIDED-BY-LEAD 24 Nov 19:40, 19:42 and 21:17; UIUX-voice-compare-restore-words-READY.md):
 * - "compare with the original" does what the compare on screen does: the restored version against the recording
 *   itself, through the reducer, where no display copy of the recording is held (the original is then no variant);
 * - a compare that cannot be made says why in plain words, naming the recording by its noun, never a variant id;
 * - Restore asked off the recording (the flight, the predicted sky) while a restored version exists is refused with
 *   not_connected and leaves the session as it is, saying where the restored version shows.
 */
const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const media = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
  valid: { MediaVariant: Array<Record<string, unknown>> };
};
const templateOf = (kind: 'restoration' | 'display_copy') => media.valid.MediaVariant.find((v) => v.kind === kind)!;
const NOUNS = ['photo', 'clip'] as const;
type Noun = (typeof NOUNS)[number];

/** A recording on screen as the web holds it: the restored version shown is the only variant (voice-restore-one). */
function recording(noun: Noun, { restored = true, pressed = false, displayCopy = false } = {}) {
  const base = createSyntheticCalibratedSession();
  const sha = base.sourceAsset!.sha256;
  const variantOf = (kind: 'restoration' | 'display_copy', id: string): MediaVariant =>
    MediaVariantSchema.parse({ ...templateOf(kind), id, sourceSha256: sha, sourceAssetId: base.sourceAsset!.id, uri: `/api/media/${sha}/variants/${id}/file` });
  const shown = variantOf('restoration', 'var_0000000000000001');
  const copy = variantOf('display_copy', 'var_00000000000000c0');
  const { fpsNumerator: _fn, fpsDenominator: _fd, ...still } = base.sourceAsset!;
  const sourceAsset = noun === 'photo' ? { ...still, mediaKind: 'image', mimeType: 'image/jpeg', durationMs: 0, audioStreams: [] } : base.sourceAsset!;
  const session = {
    ...base,
    sourceAsset,
    mediaVariants: [...(restored ? [shown] : []), ...(displayCopy ? [copy] : [])],
    activeVariantId: pressed ? shown.id : EMPTY_MEDIA_VARIANT_ID,
  } as ObservationSession;
  return { session, shown, copy };
}

const said = (session: ObservationSession, words: string) => {
  const parsed = parseUtteranceToAction(words, buildActionContext(session), session, { now: NOW });
  expect(parsed.action, words).toBeDefined();
  return { action: parsed.action!, result: dispatchAlbedianAction(session, parsed.action!) };
};
const compareOf = (session: ObservationSession, variantAId: string, variantBId: string) =>
  dispatchAlbedianAction(session, {
    type: 'media.compare', requestId: 'req_cmp', expectedViewRevision: session.viewRevision, variantAId, variantBId, preserveTime: true,
  } as AlbedianAction);

describe.each(NOUNS)('voice-compare-one on a %s', (noun) => {
  it('compare with the original, nothing pressed: the restored version against the recording itself', () => {
    const { session, shown } = recording(noun);
    const { action, result } = said(session, 'compare with the original');
    expect(action).toMatchObject({ type: 'media.compare', variantAId: EMPTY_MEDIA_VARIANT_ID, variantBId: shown.id });
    expect(result.outcome.status).toBe('completed');
    expect(result.session.activeVariantId).toBe(shown.id);
    expect(result.session.compareVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    expect(result.session.machines.navigation).toBe('comparison');
  });

  it('compare with the original, the restored version pressed: the same', () => {
    const { session, shown } = recording(noun, { pressed: true });
    const { action, result } = said(session, 'compare with the original');
    expect(action).toMatchObject({ variantAId: EMPTY_MEDIA_VARIANT_ID, variantBId: shown.id });
    expect(result.outcome.status).toBe('completed');
  });

  it('with two restored versions held: the one pressed, else the one put in last', () => {
    const { session, shown } = recording(noun, { pressed: true });
    const later = MediaVariantSchema.parse({ ...shown, id: 'var_0000000000000002', uri: shown.uri.replace(shown.id, 'var_0000000000000002') });
    const two = { ...session, mediaVariants: [shown, later] } as ObservationSession;
    expect(said(two, 'compare with the original').action).toMatchObject({ variantAId: EMPTY_MEDIA_VARIANT_ID, variantBId: shown.id });
    const none = { ...two, activeVariantId: EMPTY_MEDIA_VARIANT_ID } as ObservationSession;
    expect(said(none, 'compare with the original').action).toMatchObject({ variantAId: EMPTY_MEDIA_VARIANT_ID, variantBId: later.id });
  });

  it('with a display copy of the recording held, that copy is the original', () => {
    const { session, shown, copy } = recording(noun, { displayCopy: true });
    const { action, result } = said(session, 'compare with the original');
    expect(action).toMatchObject({ variantAId: copy.id, variantBId: shown.id });
    expect(result.outcome.status).toBe('completed');
    expect(result.session.compareVariantId).toBe(copy.id);
  });

  it.each([[{}], [{ displayCopy: true }]])('with no restored version: nothing to compare it with (%o)', (extra) => {
    const { session } = recording(noun, { restored: false, ...extra });
    const { result } = said(session, 'compare with the original');
    expect(result.outcome).toMatchObject({
      status: 'rejected', reason: 'missing_source', explanation: `This ${noun} has not been restored, so there is nothing to compare it with.`,
    });
    expect(result.session).toBe(session);
  });

  it('a version compared with itself: nothing was compared', () => {
    const { session, shown } = recording(noun, { pressed: true });
    for (const id of [shown.id, EMPTY_MEDIA_VARIANT_ID]) {
      const result = compareOf(session, id, id);
      expect(result.outcome).toMatchObject({
        status: 'rejected', reason: 'missing_source', explanation: `That would compare a version of this ${noun} with itself, so nothing was compared.`,
      });
      expect(result.session).toBe(session);
    }
  });

  it('a version no longer held (the screen swapped it): the screen changed, never an id', () => {
    const { session, shown } = recording(noun);
    for (const [a, b] of [[EMPTY_MEDIA_VARIANT_ID, 'var_000000000000dead'], ['var_000000000000dead', shown.id]] as const) {
      const result = compareOf(session, a, b);
      expect(result.outcome).toMatchObject({ status: 'rejected', reason: 'missing_source', explanation: 'The screen changed while you asked.' });
      expect(JSON.stringify(result.outcome)).not.toContain('var_');
    }
  });

  it('no compare refusal names a variant id', () => {
    const { session } = recording(noun, { restored: false });
    expect(JSON.stringify(compareOf(session, 'var_000000000000000a', 'var_000000000000000b').outcome)).not.toContain('var_');
  });
});

describe.each(NOUNS)('Restore asked off the %s', (noun) => {
  const flight = (s: ObservationSession) =>
    ({ ...s, spatialView: { ...s.spatialView, mode: 'catalog_3d' }, machines: { ...s.machines, navigation: 'space' } }) as ObservationSession;
  const predictedSky = (s: ObservationSession) =>
    ({ ...s, spatialView: { ...s.spatialView, mode: 'registered_sky' }, observationSolution: { ...s.observationSolution!, status: 'approximate' } }) as ObservationSession;
  const realSky = (s: ObservationSession) => ({ ...s, spatialView: { ...s.spatialView, mode: 'registered_sky' } }) as ObservationSession;

  it.each([['the flight', flight], ['the predicted sky', predictedSky]] as const)('%s, a restored version held: not_connected, nothing changed', (_where, away) => {
    const off = away(recording(noun).session);
    const { action, result } = said(off, 'make the sky clearer');
    expect(action.type).toBe('media.restore');
    expect(result.outcome).toMatchObject({
      status: 'rejected', reason: 'not_connected', explanation: `The restored version shows on this ${noun} itself; Back to Moment returns to it.`,
    });
    expect(result.session).toBe(off);
  });

  it.each([['the flight', flight], ['the predicted sky', predictedSky]] as const)('%s, none held: missing_source as on the recording, so the button may run', (_where, away) => {
    const off = away(recording(noun, { restored: false }).session);
    expect(said(off, 'make the sky clearer').result.outcome).toMatchObject({
      status: 'rejected', reason: 'missing_source', explanation: `This ${noun} has not been restored.`,
    });
  });

  it('Real sky, laid over the recording, is on it: the restored version is shown', () => {
    const { session, shown } = recording(noun);
    const { result } = said(realSky(session), 'make the sky clearer');
    expect(result.outcome.status).toBe('completed');
    expect(result.session.activeVariantId).toBe(shown.id);
  });
});
