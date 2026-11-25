import { beforeEach, describe, expect, it, vi } from 'vitest';

// go-there-predicted-glow-one: what goes up at rest. A photo's prepare keeps ESO's photograph (Real sky) and sends the
// flight's glow up beside it; the predicted sky's keeps the glow alone, since its flight never draws the photograph.

const asked = vi.hoisted(() => ({ kept: [] as string[], prepared: [] as string[] }));
vi.mock('../../sky/skyPicture.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../sky/skyPicture.js')>();
  return {
    ...original,
    keptSkyPicture: async (url: string) => (asked.kept.push(url), null),
    prepareSkyPicture: (url: string) => void asked.prepared.push(url),
    warmPrograms: () => true,
  };
});
const { prepareRealSky, REAL_SKY_PANORAMA_URL } = await import('../../../reveal/RealSkyLayer.js');
const { FLIGHT_MILKY_WAY_URL } = await import('../../sky/flightMilkyWay.js');

beforeEach(() => {
  asked.kept = [];
  asked.prepared = [];
});

describe('the pictures prepared at rest (go-there-predicted-glow-one)', () => {
  it('keeps the photograph and sends the glow up beside it for a photo', () => {
    prepareRealSky();
    expect(asked.kept).toEqual([REAL_SKY_PANORAMA_URL]);
    expect(asked.prepared).toEqual([FLIGHT_MILKY_WAY_URL]);
  });

  it('keeps the glow alone for the predicted sky, and never the photograph', () => {
    prepareRealSky(false);
    expect(asked.kept).toEqual([FLIGHT_MILKY_WAY_URL]);
    expect(asked.prepared).toEqual([]);
  });
});
