import { describe, expect, it, vi } from 'vitest';
import { CLIP_SYNC_DRIFT_SEC, CLIP_SYNC_SEEK_SEC, followParent, onFrameAtParent, type MediaLike } from '../clipSync.js';

// D34 item 2 (UIUX-cliprestore-READY.md, decided by the lead 17:08), Topaz Video's rule: the original is the parent; the
// restored clip copies its playback rate, jumps to the parent's time when they are more than 0.1 s apart (within
// 0.02 s after a seek), plays and pauses with it; it is always muted (item 3).

class Media extends EventTarget implements MediaLike {
  currentTime = 0;
  playbackRate = 1;
  paused = true;
  muted = false;
  loop = false;
  seeking = false;
  readyState = 4;
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

// clipswap-1 (DECIDED-BY-LEAD 12:40 on 24 Nov, MOTION-clipswap-QUESTION.md): no new seek starts while the restored
// clip is still seeking, so under load a seek lands on a decoded frame instead of being replaced by the next one; and on
// a version switch the incoming copy takes the outgoing one's place only once it has a frame at the player's time.
describe('a seek still under way', () => {
  it('starts no new seek while the restored clip is still seeking, and still copies the rate and the loop', () => {
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    parent.paused = false;
    child.currentTime = 2;
    child.seeking = true;
    parent.currentTime = 2.3;
    parent.playbackRate = 2;
    parent.loop = true;
    parent.dispatchEvent(new Event('timeupdate'));
    parent.dispatchEvent(new Event('seeked'));
    parent.dispatchEvent(new Event('play'));
    expect([child.currentTime, child.playbackRate, child.loop]).toEqual([2, 2, true]);
    stop();
  });

  it("comes to a paused parent's time within 0.02 s once its own seek lands", () => {
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    child.currentTime = 5;
    child.seeking = true;
    // The player was moved again while the restored clip was still seeking to 5 s
    parent.currentTime = 7.5;
    parent.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(5);
    child.seeking = false;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(7.5);
    child.currentTime = 7.51;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(7.51);
    // Past 0.02 s, though within the 0.1 s a playing parent would leave
    child.currentTime = 7.55;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(7.5);
    stop();
  });

  it("leaves a playing parent's drift of up to 0.1 s once its own seek lands, and jumps past it", () => {
    const parent = new Media();
    const child = new Media();
    const stop = followParent(parent, child);
    parent.paused = false;
    parent.currentTime = 3.09;
    child.currentTime = 3;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(3);
    parent.currentTime = 3.2;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(3.2);
    stop();
  });

  it("stops listening to the restored clip's own seeks once let go", () => {
    const parent = new Media();
    const child = new Media();
    followParent(parent, child)();
    parent.currentTime = 9;
    child.dispatchEvent(new Event('seeked'));
    expect(child.currentTime).toBe(0);
  });
});

describe('onFrameAtParent', () => {
  it("calls once, when the copy has a decoded frame within 0.1 s of the player's time and no seek under way", () => {
    const parent = new Media();
    const child = new Media();
    const then = vi.fn();
    parent.currentTime = 4;
    child.currentTime = 4;
    child.readyState = 1;
    const stop = onFrameAtParent(parent, child, then);
    expect(then).not.toHaveBeenCalled();
    child.readyState = 2;
    child.seeking = true;
    child.dispatchEvent(new Event('loadeddata'));
    expect(then).not.toHaveBeenCalled();
    child.seeking = false;
    child.currentTime = 3.85;
    child.dispatchEvent(new Event('seeked'));
    parent.dispatchEvent(new Event('timeupdate'));
    expect(then).not.toHaveBeenCalled();
    child.currentTime = 3.91;
    child.dispatchEvent(new Event('timeupdate'));
    expect(then).toHaveBeenCalledTimes(1);
    child.dispatchEvent(new Event('timeupdate'));
    parent.dispatchEvent(new Event('seeked'));
    expect(then).toHaveBeenCalledTimes(1);
    stop();
  });

  it("counts exactly 0.1 s from the player's time as at its time", () => {
    const parent = new Media();
    const child = new Media();
    const then = vi.fn();
    parent.currentTime = 0.1;
    child.currentTime = 0;
    onFrameAtParent(parent, child, then)();
    expect(then).toHaveBeenCalledTimes(1);
  });

  it('calls at once for a copy that already has the frame', () => {
    const parent = new Media();
    const child = new Media();
    const then = vi.fn();
    onFrameAtParent(parent, child, then)();
    expect(then).toHaveBeenCalledTimes(1);
  });

  it('hears each event that can bring the frame, from the copy and from the player', () => {
    for (const [who, type] of [
      ['child', 'loadeddata'],
      ['child', 'canplay'],
      ['child', 'seeked'],
      ['child', 'timeupdate'],
      ['child', 'playing'],
      ['parent', 'timeupdate'],
      ['parent', 'seeked'],
    ] as const) {
      const parent = new Media();
      const child = new Media();
      child.readyState = 0;
      const then = vi.fn();
      const stop = onFrameAtParent(parent, child, then);
      child.readyState = 2;
      (who === 'child' ? child : parent).dispatchEvent(new Event(type));
      expect([who, type, then.mock.calls.length]).toEqual([who, type, 1]);
      stop();
    }
  });

  it('never calls once let go', () => {
    const parent = new Media();
    const child = new Media();
    child.readyState = 1;
    const then = vi.fn();
    onFrameAtParent(parent, child, then)();
    child.readyState = 2;
    child.dispatchEvent(new Event('seeked'));
    parent.dispatchEvent(new Event('timeupdate'));
    expect(then).not.toHaveBeenCalled();
  });
});
