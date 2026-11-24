import { useEffect, useRef } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction, savedOnRecording } from '@albedian/observation';

/**
 * A clip's playback in the session (clip-playing-checkpoint-1, AUDIT-C's C1; AGENTS.md non-negotiable 5): the clip
 * element plays and pauses by itself (ClipPlayer), and the session hears of it here, through the reducer, so the
 * CameraCheckpoint saved on leaving the recording carries whether it played and at which frame.
 */

/** The session with the clip playing or paused, through media.playback; the same session when it already says so or
 * the reducer refuses (the clip not on screen: a refusal hands the session back as it was, so none reaches a screen). */
export function withPlayback(session: ObservationSession, playing: boolean): ObservationSession {
  if (session.isPlaying === playing) return session;
  return dispatchAlbedianAction(session, {
    type: 'media.playback',
    requestId: `ui-clip-playback-${session.viewRevision}`,
    expectedViewRevision: session.viewRevision,
    playing,
  }).session;
}

/**
 * While a clip plays, the session's clock still holds the frame of its last pause; before a command that may leave the
 * recording, it is set to the clip's live time (one media.seek), so the checkpoint carries the frame on screen. Nothing
 * changes with no live time (the clip stands still), off the recording, or on a photo.
 */
export function withClipClock(session: ObservationSession, liveSec: number | null): ObservationSession {
  if (liveSec === null || !Number.isFinite(liveSec) || session.sourceAsset?.mediaKind !== 'video') return session;
  if (!savedOnRecording({ navigationState: session.machines.navigation, spatialView: session.spatialView })) return session;
  if (Math.abs(liveSec - session.currentSourceTimeSec) < 1e-3) return session;
  return dispatchAlbedianAction(session, {
    type: 'media.seek',
    requestId: `ui-clip-clock-${session.viewRevision}`,
    expectedViewRevision: session.viewRevision,
    sourceTimeSec: liveSec,
    preserveAudioSync: true,
  }).session;
}

/** The clip element's own time while it plays, else null. */
export function liveClipSec(video: HTMLVideoElement | null): number | null {
  return video && !video.paused ? video.currentTime : null;
}

/**
 * The clip element's own play and pause events, however they came (the Play circle by pointer, Enter or Space on it,
 * the clip ending by itself), written to the session.
 */
export function useClipPlayback(video: HTMLVideoElement | null, setSession: (update: (cur: ObservationSession) => ObservationSession) => void): void {
  const set = useRef(setSession);
  set.current = setSession;
  useEffect(() => {
    if (!video) return undefined;
    const onPlay = () => set.current((cur) => withPlayback(cur, true));
    const onPause = () => set.current((cur) => withPlayback(cur, false));
    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    return () => {
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
    };
  }, [video]);
}
