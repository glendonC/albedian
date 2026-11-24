import type { MediaVariant, ObservationSession } from '@albedian/contracts';

/**
 * voice-restore-one: the session holds the restored version the screen shows of this recording (RevealPhoto reports
 * it: the compare after Restore, a row pressed, the phone's stored ones), so a spoken or typed "make the sky clearer"
 * finds what is on screen through the reducer's media.restore. `putBefore` is the id the screen put in last time: it
 * is swapped for the one shown now, or taken out when none is shown. The session's own variants are left as they are,
 * a variant already held is not added twice, and a variant of another recording, or one that is not a restoration, is
 * never taken. The same session comes back when nothing changes.
 */
export function withShownRestoration(session: ObservationSession, variant: MediaVariant | null, putBefore: string | null): ObservationSession {
  const shown = variant !== null && variant.kind === 'restoration' && variant.sourceSha256 === session.sourceAsset?.sha256 ? variant : null;
  const kept = putBefore === null || putBefore === shown?.id ? session.mediaVariants : session.mediaVariants.filter((v) => v.id !== putBefore);
  const next = shown === null || kept.some((v) => v.id === shown.id) ? kept : [...kept, shown];
  return next === session.mediaVariants ? session : { ...session, mediaVariants: next };
}
