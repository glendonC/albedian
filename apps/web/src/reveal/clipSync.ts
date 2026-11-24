/**
 * The one clock of D34's clip compare (UIUX-cliprestore-READY.md item 2, decided by the lead 17:08), Topaz Video's
 * rule: the original is the parent; the restored clip copies its playback rate and loop, jumps to the parent's time
 * when they are more than 0.1 s apart, comes within 0.02 s after a seek, and plays and pauses with it; at the end both
 * restart together, since a restart is the parent's time jumping back. The restored clip is always muted (item 3): the
 * sound is the original's alone.
 *
 * clipswap-1 (DECIDED-BY-LEAD 12:40 on 24 Nov, MOTION-clipswap-QUESTION.md): no new seek starts while the restored
 * clip is still seeking; once its own seek lands it comes to the parent's time by the same rule (0.1 s while the parent
 * plays, 0.02 s while it is paused). Before, a seek started on every timeupdate more than 0.1 s apart, so under load
 * each seek was replaced before it decoded a frame and a copy just switched to was never painted [verified: MOTION-clipswap-QUESTION.md, measured live].
 */

export const CLIP_SYNC_DRIFT_SEC = 0.1;
export const CLIP_SYNC_SEEK_SEC = 0.02;
/** HTMLMediaElement.HAVE_CURRENT_DATA: the element has a decoded frame at its current time. */
const HAVE_CURRENT_DATA = 2;

/** The part of a media element the rule reads and sets. */
export interface MediaLike extends EventTarget {
  currentTime: number;
  playbackRate: number;
  paused: boolean;
  muted: boolean;
  loop: boolean;
  readonly seeking: boolean;
  readonly readyState: number;
  play(): Promise<void> | void;
  pause(): void;
}

/** Keeps `child` on `parent`'s clock until the returned function is called. */
export function followParent(parent: MediaLike, child: MediaLike): () => void {
  child.muted = true;
  const align = (limitSec: number) => {
    if (child.playbackRate !== parent.playbackRate) child.playbackRate = parent.playbackRate;
    if (child.loop !== parent.loop) child.loop = parent.loop;
    if (child.seeking) return;
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
  const onLanded = () => align(parent.paused ? CLIP_SYNC_SEEK_SEC : CLIP_SYNC_DRIFT_SEC);
  align(0);
  parent.addEventListener('play', onPlay);
  parent.addEventListener('pause', onPause);
  parent.addEventListener('timeupdate', onTick);
  parent.addEventListener('ratechange', onTick);
  parent.addEventListener('seeked', onSeek);
  child.addEventListener('seeked', onLanded);
  return () => {
    parent.removeEventListener('play', onPlay);
    parent.removeEventListener('pause', onPause);
    parent.removeEventListener('timeupdate', onTick);
    parent.removeEventListener('ratechange', onTick);
    parent.removeEventListener('seeked', onSeek);
    child.removeEventListener('seeked', onLanded);
  };
}

const CHILD_FRAME_EVENTS = ['loadeddata', 'canplay', 'seeked', 'timeupdate', 'playing'] as const;
const PARENT_FRAME_EVENTS = ['timeupdate', 'seeked'] as const;

/**
 * Calls `then` once, as soon as `child` has a decoded frame within 0.1 s of `parent`'s time and no seek under way: the
 * moment a restored clip switched to may take the outgoing one's place (clipswap-1). Never calls once let go.
 */
export function onFrameAtParent(parent: MediaLike, child: MediaLike, then: () => void): () => void {
  let done = false;
  const stop = () => {
    for (const type of CHILD_FRAME_EVENTS) child.removeEventListener(type, check);
    for (const type of PARENT_FRAME_EVENTS) parent.removeEventListener(type, check);
  };
  function check() {
    if (done || child.seeking || child.readyState < HAVE_CURRENT_DATA) return;
    if (Math.abs(child.currentTime - parent.currentTime) > CLIP_SYNC_DRIFT_SEC) return;
    done = true;
    stop();
    then();
  }
  for (const type of CHILD_FRAME_EVENTS) child.addEventListener(type, check);
  for (const type of PARENT_FRAME_EVENTS) parent.addEventListener(type, check);
  check();
  return () => {
    done = true;
    stop();
  };
}
