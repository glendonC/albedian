import { describe, expect, it, vi } from 'vitest';
import { CLIP_SYNC_DRIFT_SEC, CLIP_SYNC_SEEK_SEC, followParent, type MediaLike } from '../clipSync.js';

// D34 item 2 (UIUX-cliprestore-READY.md, decided by the lead 17:08), Topaz Video's rule: the original is the parent; the
// restored clip copies its playback rate, jumps to the parent's time when they are more than 0.1 s apart (within
// 0.02 s after a seek), plays and pauses with it; it is always muted (item 3).

class Media extends EventTarget implements MediaLike {
  currentTime = 0;
  playbackRate = 1;
  paused = true;
  muted = false;
  loop = false;
  play = vi.fn(async () => {
    this.paused = false;
  });
  pause = vi.fn(() => {
    this.paused = true;
  });
}

describe('followParent', () => {
  it('mutes the restored clip and copies the rate, the loop and the time at once', () => {
    const parent = new Media();
    const child = new Media();
    parent.playbackRate = 0.5;
    parent.loop = true;
    parent.currentTime = 3;
    const stop = followParent(parent, child);
    expect([child.muted, child.playbackRate, child.loop, child.currentTime]).toEqual([true, 0.5, true, 3]);
    stop();
  });

  it('plays and pauses with the parent', () => {
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    parent.paused = false;
    parent.dispatchEvent(new Event('play'));
    expect(child.play).toHaveBeenCalledTimes(1);
    parent.paused = true;
    parent.dispatchEvent(new Event('pause'));
    expect(child.pause).toHaveBeenCalledTimes(1);
    stop();
  });

  it('leaves a drift of up to 0.1 s while playing, and jumps past it', () => {
    expect([CLIP_SYNC_DRIFT_SEC, CLIP_SYNC_SEEK_SEC]).toEqual([0.1, 0.02]);
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    parent.currentTime = 10;
    child.currentTime = 10.08;
    parent.dispatchEvent(new Event('timeupdate'));
    expect(child.currentTime).toBe(10.08);
    child.currentTime = 10.15;
    parent.dispatchEvent(new Event('timeupdate'));
    expect(child.currentTime).toBe(10);
    stop();
  });

  it('comes within 0.02 s after a seek, and follows a restart at the end', () => {
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    parent.currentTime = 5;
    child.currentTime = 5.05;
    parent.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(5);
    // The end: the parent starts again from 0, and the restored clip with it
    parent.currentTime = 0.01;
    child.currentTime = 10.9;
    parent.dispatchEvent(new Event('timeupdate'));
    expect(child.currentTime).toBe(0.01);
    stop();
  });

  it('stops following once let go', () => {
    const parent = new Media();
    const child = new Media();
    followParent(parent, child)();
    parent.paused = false;
    parent.dispatchEvent(new Event('play'));
    expect(child.play).not.toHaveBeenCalled();
  });
});
