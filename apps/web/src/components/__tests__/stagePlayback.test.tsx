// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { SourcePhotoStage } from '../SourcePhotoStage.js';

// clip-playing-checkpoint-1 (AUDIT-C's C1; AGENTS.md non-negotiable 5): when the recording comes back on screen (Back
// to Moment from the sky, where the clip stayed drawn under it, or from 3D, where it is drawn again) and the restored
// session says the clip was playing, the clip plays from the restored frame. A clip on screen that the person paused
// is never started by the stage.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const clip = (over: Partial<ObservationSession> = {}): ObservationSession =>
  ({ ...createSyntheticCalibratedSession(), currentSourceTimeSec: 7.25, ...over }) as ObservationSession;
const inSky = (session: ObservationSession): ObservationSession =>
  ({ ...session, machines: { ...session.machines, navigation: 'sky_registered' }, spatialView: { ...session.spatialView, mode: 'registered_sky' } }) as ObservationSession;
const show = (session: ObservationSession, underSky = false) =>
  act(() => root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900, underSky })));

/** The stage's clip element, loaded, standing still, with its play() watched. */
function loadedVideo() {
  const video = container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
  expect(video).not.toBeNull();
  let paused = true;
  let time = 0;
  Object.defineProperty(video, 'readyState', { configurable: true, get: () => 4 });
  Object.defineProperty(video, 'paused', { configurable: true, get: () => paused });
  Object.defineProperty(video, 'currentTime', { configurable: true, get: () => time, set: (t: number) => void (time = t) });
  const play = vi.fn(() => {
    paused = false;
    return Promise.resolve();
  });
  video.play = play;
  return { video, play, at: () => time, stop: () => void (paused = true) };
}

describe('the stage plays a clip that comes back playing', () => {
  it('from the sky, where it stayed drawn: at the restored frame', () => {
    show(inSky(clip({ isPlaying: true })), true);
    const { play, at } = loadedVideo();
    expect(play).not.toHaveBeenCalled();
    show(clip({ isPlaying: true }));
    expect(at()).toBe(7.25);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('from the sky, paused when it left: it stays paused', () => {
    show(inSky(clip({ isPlaying: false })), true);
    const { play } = loadedVideo();
    show(clip({ isPlaying: false }));
    expect(play).not.toHaveBeenCalled();
  });

  it('from 3D, drawn again: once its metadata loads, at the restored frame', () => {
    show(clip({ isPlaying: true }));
    const { video, play, at } = loadedVideo();
    act(() => {
      video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(at()).toBe(7.25);
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('leaving for the sky never starts a clip that stands still under it, whatever the session says', () => {
    show(clip({ isPlaying: true }));
    const { play } = loadedVideo();
    show(inSky(clip({ isPlaying: true })), true);
    expect(play).not.toHaveBeenCalled();
  });

  it('a new recording standing still, and a clip the person paused on screen, are never started by the stage', () => {
    show(clip({ isPlaying: false }));
    const first = loadedVideo();
    act(() => {
      first.video.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(first.play).not.toHaveBeenCalled();
    // On screen all along: a session that still says playing while the element stands still is no return
    show(clip({ isPlaying: true, viewRevision: 9 }));
    show(clip({ isPlaying: true, viewRevision: 10 }));
    expect(first.play).not.toHaveBeenCalled();
  });
});
