import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WARM_GIVE_UP_MS, forgetKeptSkyPictures, warmPrograms } from '../skyPicture.js';
import { FakeOffscreenCanvas, installSkyFakes, named } from './skyPictureFakes.js';

// realsky-upload-one (LEAD ANSWER 19:21): programs are linked once at rest in a throwaway context with nothing read back,
// so no frame waits, and the context is let go once the links are done.

beforeEach(() => forgetKeptSkyPictures());
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

const PAIRS = [
  ['v1', 'f1'],
  ['v2', 'f2'],
] as const;

describe('warmPrograms', () => {
  it('compiles and links each program in a context of its own with nothing read back, then lets it go once every link is done', async () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 3 });
    expect(warmPrograms(PAIRS)).toBe(true);
    expect(fakes.contexts).toHaveLength(1);
    expect(named(fakes.calls, 'shaderSource').map((c) => c.args[1])).toEqual(['v1', 'f1', 'v2', 'f2']);
    expect(named(fakes.calls, 'linkProgram')).toHaveLength(2);
    // Issued with the calls, before any frame; nothing that waits for a link is read
    expect(named(fakes.calls, 'linkProgram').every((c) => c.frame === 0)).toBe(true);
    expect(named(fakes.calls, 'getShaderParameter')).toHaveLength(0);
    expect(fakes.calls.filter((c) => c.name === 'getProgramParameter' && c.args[1] !== 'COMPLETION_STATUS_KHR')).toHaveLength(0);
    expect(named(fakes.calls, 'getUniformLocation')).toHaveLength(0);
    await fakes.frame(2);
    expect(named(fakes.calls, 'loseContext')).toHaveLength(0);
    await fakes.frame(2);
    const lost = named(fakes.calls, 'loseContext');
    expect(lost).toHaveLength(1);
    expect(lost[0]!.frame).toBe(3);
    await fakes.frame(2);
    expect(fakes.pendingFrames()).toBe(0);
  });

  it('waits for the last link, not the first, before letting the context go', async () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 2, parallelStagger: true });
    warmPrograms(PAIRS);
    await fakes.frame(2);
    expect(named(fakes.calls, 'loseContext')).toHaveLength(0);
    await fakes.frame(2);
    expect(named(fakes.calls, 'loseContext').map((c) => c.frame)).toEqual([3]);
  });

  it('lets the context go after the give-up time where the browser cannot say when its links are done', async () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: null });
    let now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    warmPrograms(PAIRS);
    await fakes.frame(3);
    expect(named(fakes.calls, 'loseContext')).toHaveLength(0);
    now += WARM_GIVE_UP_MS + 1;
    await fakes.frame();
    expect(named(fakes.calls, 'loseContext')).toHaveLength(1);
  });

  it('asks the warm context for the attributes the real one will have, and issues what `more` adds on it', () => {
    const fakes = installSkyFakes({ pictureUrl: '/x', parallelDoneAt: 1 });
    const more = vi.fn(() => []);
    warmPrograms(PAIRS, more, { alpha: false, antialias: false, premultipliedAlpha: true });
    expect((fakes.contexts[0]!.canvas as unknown as FakeOffscreenCanvas).attributes).toEqual({ alpha: false, antialias: false, premultipliedAlpha: true });
    expect(more).toHaveBeenCalledWith(fakes.contexts[0]);
  });

  it('does nothing where there is no OffscreenCanvas', () => {
    installSkyFakes({ pictureUrl: '/x' });
    vi.stubGlobal('OffscreenCanvas', undefined);
    expect(warmPrograms(PAIRS)).toBe(false);
  });
});
