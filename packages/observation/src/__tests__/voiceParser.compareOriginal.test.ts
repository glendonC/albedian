import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { MediaVariantSchema, type MediaVariant, type ObservationSession } from '@albedian/contracts';
import { EMPTY_MEDIA_VARIANT_ID } from '../emptySession';
import { dispatchAlbedianAction } from '../reducer';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

// checkpoint-version-one (DECIDED-BY-LEAD 19:40 on 24 Nov): a version is never compared with itself. The parser names
// the original as no variant when the session holds no display copy of the recording; with a display copy held, that
// copy is the original. voice-compare-one: the reducer takes no variant as the recording itself, so asking to compare
// with the original does what the compare on screen does, the restored version against the recording.

const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));
const media = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
  valid: { MediaVariant: Array<Record<string, unknown>> };
};
const templateOf = (kind: 'restoration' | 'display_copy') => media.valid.MediaVariant.find((v) => v.kind === kind)!;

/** A photo whose screen shows the restored version `X`: the only variant the session holds, as voice-restore-one leaves it. */
function photoShowing(activeIsShown: boolean, withDisplayCopy = false) {
  const base = createSyntheticCalibratedSession();
  const sha = base.sourceAsset!.sha256;
  const variantOf = (kind: 'restoration' | 'display_copy', id: string): MediaVariant =>
    MediaVariantSchema.parse({ ...templateOf(kind), id, sourceSha256: sha, sourceAssetId: base.sourceAsset!.id, uri: `/api/media/${sha}/variants/${id}/file` });
  const shown = variantOf('restoration', 'var_0000000000000001');
  const copy = variantOf('display_copy', 'var_00000000000000c0');
  const session = {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg', width: 3024, height: 4032, durationMs: 0 },
    mediaVariants: withDisplayCopy ? [shown, copy] : [shown],
    activeVariantId: activeIsShown ? shown.id : EMPTY_MEDIA_VARIANT_ID,
  } as ObservationSession;
  return { session, shown, copy };
}
const compare = (session: ObservationSession) => {
  const parsed = parseUtteranceToAction('compare with the original', buildActionContext(session), session, { now: NOW });
  expect(parsed.action?.type).toBe('media.compare');
  return { action: parsed.action as Extract<NonNullable<typeof parsed.action>, { type: 'media.compare' }>, result: dispatchAlbedianAction(session, parsed.action!) };
};

describe('"compare with the original": the original is a display copy of the recording, or no variant', () => {
  it('with a pressed row active and no display copy held: the version pressed against the recording itself', () => {
    const { session, shown } = photoShowing(true);
    const { action, result } = compare(session);
    expect(action.variantAId).toBe(EMPTY_MEDIA_VARIANT_ID);
    expect(action.variantBId).toBe(shown.id);
    expect(result.outcome.status).toBe('completed');
    expect(result.session.machines.navigation).toBe('comparison');
    expect(result.session.compareVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
  });

  it('with nothing pressed: the version shown against the recording itself', () => {
    const { session, shown } = photoShowing(false);
    const { action, result } = compare(session);
    expect([action.variantAId, action.variantBId]).toEqual([EMPTY_MEDIA_VARIANT_ID, shown.id]);
    expect(result.outcome.status).toBe('completed');
  });

  it('with a display copy of the recording held: that copy is the original, compared with the version shown', () => {
    const { session, shown, copy } = photoShowing(true, true);
    const { action, result } = compare(session);
    expect([action.variantAId, action.variantBId]).toEqual([copy.id, shown.id]);
    expect(result.outcome.status).toBe('completed');
  });

  it('a display copy of another recording is not this one\'s original', () => {
    const { session, shown, copy } = photoShowing(true, true);
    const other = { ...session, mediaVariants: [shown, { ...copy, sourceSha256: 'f'.repeat(64) }] } as ObservationSession;
    expect(compare(other).action.variantAId).toBe(EMPTY_MEDIA_VARIANT_ID);
  });
});
