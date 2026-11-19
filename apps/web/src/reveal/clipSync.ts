/**
 * The one clock of D34's clip compare (UIUX-cliprestore-READY.md item 2, decided by the lead 17:08), Topaz Video's
 * rule: the original is the parent; the restored clip copies its playback rate and loop, jumps to the parent's time
 * when they are more than 0.1 s apart, comes within 0.02 s after a seek, and plays and pauses with it; at the end both
 * restart together, since a restart is the parent's time jumping back. The restored clip is always muted (item 3): the
 * sound is the original's alone.
 */

export const CLIP_SYNC_DRIFT_SEC = 0.1;
export const CLIP_SYNC_SEEK_SEC = 0.02;

/** The part of a media element the rule reads and sets. */
export interface MediaLike extends EventTarget {
  currentTime: number;
  playbackRate: number;
  paused: boolean;
  muted: boolean;
  loop: boolean;
  play(): Promise<void> | void;
  pause(): void;
}

/** Keeps `child` on `parent`'s clock until the returned function is called. */
export function followParent(parent: MediaLike, child: MediaLike): () => void {
  child.muted = true;
  const align = (limitSec: number) => {
    if (child.playbackRate !== parent.playbackRate) child.playbackRate = parent.playbackRate;
    if (child.loop !== parent.loop) child.loop = parent.loop;
    if (Math.abs(child.currentTime - parent.currentTime) > limitSec) child.currentTime = parent.currentTime;
  };
  const onPlay = () => {
    align(CLIP_SYNC_SEEK_SEC);
    if (child.paused) void Promise.resolve(child.play()).catch(() => {});
  };
  const onPause = () => {
    if (!child.paused) child.pause();
    align(CLIP_SYNC_SEEK_SEC);
  };
  const onTick = () => align(CLIP_SYNC_DRIFT_SEC);
  const onSeek = () => align(CLIP_SYNC_SEEK_SEC);
  align(0);
  parent.addEventListener('play', onPlay);
  parent.addEventListener('pause', onPause);
  parent.addEventListener('timeupdate', onTick);
  parent.addEventListener('ratechange', onTick);
  parent.addEventListener('seeked', onSeek);
  return () => {
    parent.removeEventListener('play', onPlay);
    parent.removeEventListener('pause', onPause);
    parent.removeEventListener('timeupdate', onTick);
    parent.removeEventListener('ratechange', onTick);
    parent.removeEventListener('seeked', onSeek);
  };
}
