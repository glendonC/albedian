// @vitest-environment jsdom
import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction, returnCheckpointOf } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { useClipPlayback, withClipClock, withPlayback } from '../clipPlayback.js';

// clip-playing-checkpoint-1 (AUDIT-C's C1; LEAD ANSWER to WEB-UX 12:08, 12:27 and 15:35 on 24 Nov): a clip played or
// paused on screen, however it was started, is written to the session through media.playback, and while it plays a
// command that leaves the recording first sets the session's clock to the clip's live time, so the checkpoint carries
// the frame on screen and Back to Moment returns the clip playing there, at its sound level.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** A clip element as the app sees it: its time, whether it plays, and its own events. */
class FakeVideo extends EventTarget {
  currentTime = 0;
  paused = true;
  play = vi.fn(async () => {
    this.paused = false;
    this.dispatchEvent(new Event('play'));
  });
  pause = vi.fn(() => {
    this.paused = true;
    this.dispatchEvent(new Event('pause'));
  });
  /** The clip reaches its end by itself: the browser pauses it and says so. */
  end() {
    this.paused = true;
    this.dispatchEvent(new Event('pause'));
    this.dispatchEvent(new Event('ended'));
  }
}

const pausedClip = (): ObservationSession => ({ ...createSyntheticCalibratedSession(), isPlaying: false, currentSourceTimeSec: 1.5 });
const leave = (s: ObservationSession) =>
  dispatchAlbedianAction(s, { type: 'space.enter', requestId: 'req_leave', expectedViewRevision: s.viewRevision, findingId: 'finding_betelgeuse', distancePolicy: 'measured_or_estimated' }).session;
const back = (s: ObservationSession) =>
  dispatchAlbedianAction(s, { type: 'source.return', requestId: 'req_back', expectedViewRevision: s.viewRevision, restoreAudio: true }).session;

describe('withPlayback and withClipClock', () => {
  it('writes playing once, through the reducer, and leaves a session that already says so untouched', () => {
    const start = pausedClip();
    const played = withPlayback(start, true);
    expect(played.isPlaying).toBe(true);
    expect(played.viewRevision).toBe(start.viewRevision + 1);
    expect(withPlayback(played, true)).toBe(played);
    expect(withPlayback(played, false).isPlaying).toBe(false);
  });

  it('keeps the session as it was when the reducer refuses (the clip not on screen): no refusal reaches the screen', () => {
    const away = leave(pausedClip());
    expect(withPlayback(away, true)).toBe(away);
  });

  it('sets the clock to the live time only while the clip plays on the recording', () => {
    const playing = withPlayback(pausedClip(), true);
    const synced = withClipClock(playing, 7.25);
    expect(synced.currentSourceTimeSec).toBe(7.25);
    expect(synced.viewRevision).toBe(playing.viewRevision + 1);
    // Not playing (no live time), the same time, or away from the recording: nothing changes
    expect(withClipClock(playing, null)).toBe(playing);
    // A clip element before its metadata has no time (NaN): the clock is kept
    expect(withClipClock(playing, Number.NaN)).toBe(playing);
    expect(withClipClock(synced, 7.25)).toBe(synced);
    const away = leave(playing);
    expect(withClipClock(away, 9)).toBe(away);
    // A photo has no clip clock
    const photo = { ...playing, sourceAsset: { ...playing.sourceAsset!, mediaKind: 'image' } } as ObservationSession;
    expect(withClipClock(photo, 7.25)).toBe(photo);
  });

  // clip-checkpoint-time-test-one (AUDIT-C's T2 on clip-playing-checkpoint-1: the same-time tolerance widened from a
  // millisecond to two seconds survived): a clip half a second, or a tenth of one, past the clock is a different frame
  it('sets the clock for any frame the clip has moved to, however near: only the same instant is left alone', () => {
    const playing = { ...withPlayback(pausedClip(), true), currentSourceTimeSec: 7 } as ObservationSession;
    for (const live of [7.5, 7.1, 6.95, 7.002]) {
      const synced = withClipClock(playing, live);
      expect(synced.currentSourceTimeSec, String(live)).toBe(live);
      expect(synced.viewRevision, String(live)).toBe(playing.viewRevision + 1);
    }
    // The same instant, to within half a millisecond, is no new frame
    expect(withClipClock(playing, 7.0004)).toBe(playing);
  });

  it('so a clip left while playing comes back playing at the frame it was on, at its sound level', () => {
    const playing = { ...withPlayback(pausedClip(), true), audioGain: 0.4 };
    const left = leave(withClipClock(playing, 7.25));
    const checkpoint = returnCheckpointOf(left)!;
    expect(checkpoint.sourceTimeSec).toBe(7.25);
    expect(checkpoint.playbackWasPlaying).toBe(true);
    const returned = back(left);
    expect(returned.currentSourceTimeSec).toBe(7.25);
    expect(returned.isPlaying).toBe(true);
    expect(returned.audioGain).toBe(0.4);
  });
});

describe('useClipPlayback: the clip element says when it plays and stops', () => {
  let container: HTMLDivElement;
  let root: Root;
  let session: ObservationSession;
  const Harness = ({ video }: { video: FakeVideo }) => {
    const [state, setState] = useState<ObservationSession>(pausedClip);
    session = state;
    useClipPlayback(video as unknown as HTMLVideoElement, setState);
    return null;
  };
  beforeEach(() => {
    container = document.createElement('div');
    root = createRoot(container);
  });
  afterEach(() => act(() => root.unmount()));

  it('a pointer or a key on Play (both play the element), a pause by hand, and the clip ending by itself', async () => {
    const video = new FakeVideo();
    act(() => root.render(createElement(Harness, { video })));
    expect(session.isPlaying).toBe(false);
    // The Play circle pressed, by pointer or by Enter or Space on the focused button: the element plays
    await act(async () => {
      await video.play();
    });
    expect(session.isPlaying).toBe(true);
    act(() => video.pause());
    expect(session.isPlaying).toBe(false);
    await act(async () => {
      await video.play();
    });
    expect(session.isPlaying).toBe(true);
    act(() => video.end());
    expect(session.isPlaying).toBe(false);
  });

  it('stops listening to an element it no longer has', () => {
    const video = new FakeVideo();
    act(() => root.render(createElement(Harness, { video })));
    const other = new FakeVideo();
    act(() => root.render(createElement(Harness, { video: other })));
    act(() => {
      video.paused = false;
      video.dispatchEvent(new Event('play'));
    });
    expect(session.isPlaying).toBe(false);
  });
});
