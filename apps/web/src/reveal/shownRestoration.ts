import type { MediaVariant, ObservationSession } from '@albedian/contracts';
import { EMPTY_MEDIA_VARIANT_ID } from '@albedian/observation';

/**
 * voice-restore-one: the session holds the restored version the screen shows of this recording (RevealPhoto reports
 * it: the compare after Restore, a row pressed, the phone's stored ones), so a spoken or typed "make the sky clearer"
 * finds it through the reducer's media.restore. voice-restore-known-one (DECIDED-BY-LEAD 25 Nov 02:37): the session
 * holds what is held, so a restoration once held stays when the screen stops showing it (`putBefore`, the id the
 * screen put in last time, is no longer taken out); on the recording, what voice then says is made true by the
 * screen's own control (App.tsx). A variant already held is not added twice, and a variant of another recording, or
 * one that is not a restoration, is never taken. The same session comes back when nothing changes.
 */
export function withShownRestoration(session: ObservationSession, variant: MediaVariant | null, _putBefore: string | null): ObservationSession {
  return withStoredRestorations(session, variant === null ? [] : [variant]);
}

/**
 * voice-restore-known-one: the upload's stored restorations, as its records name them (`readRestorations`), held by the
 * session from the moment the records are read, not only once one is shown, so voice speaks of them truly on a first
 * visit too. Only restorations of this recording are taken, each once; the session's other variants stay as they are.
 */
export function withStoredRestorations(session: ObservationSession, variants: readonly MediaVariant[]): ObservationSession {
  const sha256 = session.sourceAsset?.sha256;
  const added = variants.filter((v) => v.kind === 'restoration' && v.sourceSha256 === sha256 && !session.mediaVariants.some((held) => held.id === v.id));
  return added.length === 0 ? session : { ...session, mediaVariants: [...session.mediaVariants, ...added] };
}

/**
 * checkpoint-version-one (DECIDED-BY-LEAD 18:46 on 24 Nov): the row pressed under Download is the session's active
 * variant, so the moment saved on leaving carries it (CameraCheckpoint.mediaVariantId) and Back to Moment shows that same
 * version; let go on the recording (null), none is active, as in a session that never pressed one. The same session
 * comes back when nothing changes.
 */
export function withPickedVersion(session: ObservationSession, id: string | null): ObservationSession {
  const active = id ?? EMPTY_MEDIA_VARIANT_ID;
  return session.activeVariantId === active ? session : { ...session, activeVariantId: active };
}
