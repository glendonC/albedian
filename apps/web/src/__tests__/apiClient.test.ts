import { afterEach, describe, expect, it, vi } from 'vitest';
import fc from 'fast-check';
import { SourceAssetSchema } from '@albedian/contracts';
import { createEmptyObservationSession } from '@albedian/observation';
import {
  buildSessionFromDiscoveredMedia,
  fetchDiscoveredMedia,
  postVoiceIntentToBackend,
  type DiscoveredMediaItem,
} from '../services/apiClient';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 200;

// Synthetic listings only: invented ids and hashes, with no capture time, place or camera
const hashOf = (n: number) => n.toString(16).padStart(64, '0');

function listed(id: string, n: number, fields: Record<string, unknown>) {
  return {
    id, filename: `${id}.bin`, sha256: hashOf(n), width: 640, height: 480, variants: { orig_png: `/media/${id}.png` }, ...fields,
  };
}

function stubFetch(respond: () => Promise<unknown>) {
  const fetchMock = vi.fn(respond);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function stubListing(items: unknown[]) {
  return stubFetch(async () => ({ ok: true, json: async () => ({ items }) }));
}

function stillItem(fps: number | null): DiscoveredMediaItem {
  return {
    id: 'still', title: 'Still', filename: 'still.png', sha256: hashOf(7), mediaKind: 'image', width: 640,
    height: 480, durationSec: 0, fps, videoCodec: 'none', capturedAtUTC: null, latDeg: null, lonDeg: null,
    cameraModel: null, urls: { origUrl: '/media/still.png', v1Url: '', v2Url: '' },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('discovered media keeps what discovery probed and guesses nothing', () => {
  it('keeps the kind, frame rate and duration as probed, and leaves out items without them', async () => {
    stubListing([
      listed('clip', 1, { mediaType: 'video', fps: 29.97, durationSec: 12.5 }),
      // Discovery today reports ffprobe's image2 default of 25 fps for a still
      listed('still-at-25', 2, { mediaType: 'image', fps: 25, durationSec: 0 }),
      listed('still-bare', 3, { mediaType: 'image', durationSec: 0 }),
      listed('no-kind', 4, { fps: 30, durationSec: 4 }),
      listed('audio', 5, { mediaType: 'audio', fps: 30, durationSec: 4 }),
      listed('clip-no-rate', 6, { mediaType: 'video', durationSec: 4 }),
      listed('clip-zero-rate', 7, { mediaType: 'video', fps: 0, durationSec: 4 }),
      listed('clip-no-duration', 8, { mediaType: 'video', fps: 30 }),
    ]);
    const items = await fetchDiscoveredMedia();
    expect(items.map((item) => item.id)).toEqual(['clip', 'still-at-25', 'still-bare']);
    const [clip, stillAt25, stillBare] = items;
    expect(clip).toMatchObject({ mediaKind: 'video', fps: 29.97, durationSec: 12.5 });
    expect(stillAt25).toMatchObject({ mediaKind: 'image', fps: null, durationSec: 0 });
    expect(stillBare).toMatchObject({ mediaKind: 'image', fps: null, durationSec: 0 });
  });

  it('gives a still no frame rate and a clip its probed rate, each in a contract-valid asset', async () => {
    stubListing([
      listed('clip', 1, { mediaType: 'video', fps: 29.97, durationSec: 12.5 }),
      listed('still-at-25', 2, { mediaType: 'image', fps: 25, durationSec: 0 }),
    ]);
    const [clip, still] = (await fetchDiscoveredMedia()).map(buildSessionFromDiscoveredMedia);
    expect(SourceAssetSchema.parse(clip!.sourceAsset)).toMatchObject({ fpsNumerator: 29970, fpsDenominator: 1000, durationMs: 12500 });
    const stillAsset = SourceAssetSchema.parse(still!.sourceAsset);
    expect(stillAsset.durationMs).toBe(0);
    expect(stillAsset).not.toHaveProperty('fpsNumerator');
    expect(stillAsset).not.toHaveProperty('fpsDenominator');
    // A still built by hand with a rate still has none
    const handBuilt = buildSessionFromDiscoveredMedia(stillItem(25)).sourceAsset;
    expect(handBuilt).not.toHaveProperty('fpsNumerator');
    expect(handBuilt).not.toHaveProperty('fpsDenominator');
  });

  it('refuses to build a clip without a frame rate', () => {
    const clip: DiscoveredMediaItem = { ...stillItem(null), id: 'clip', filename: 'clip.mov', mediaKind: 'video', durationSec: 4 };
    expect(() => buildSessionFromDiscoveredMedia(clip)).toThrow(/frame rate/);
  });

  it('cites only the recorded creationdate for a capture time, never the file-written creation_time', () => {
    // A synthetic capture time; discovery takes a capture time only from the recorded tag
    const timed: DiscoveredMediaItem = { ...stillItem(null), capturedAtUTC: '2030-11-01T22:32:00Z' };
    const asset = SourceAssetSchema.parse(buildSessionFromDiscoveredMedia(timed).sourceAsset);
    expect(asset.metadataSources?.capturedAtUTC).toEqual({ container: 'quicktime', tags: ['com.apple.quicktime.creationdate'] });
    // Without a capture time, no source is cited for one
    expect(buildSessionFromDiscoveredMedia(stillItem(null)).sourceAsset.metadataSources?.capturedAtUTC).toBeUndefined();
  });

  it('PBT: an item is kept exactly when its kind is known and, for a clip, its rate and duration are', async () => {
    const kind = fc.constantFrom<unknown>(undefined, null, 'image', 'video', 'audio', 'IMAGE', '', 3);
    const rate = fc.oneof(
      fc.constantFrom<unknown>(undefined, null, 0, -0, -30, Number.NaN, Number.POSITIVE_INFINITY, '30'),
      fc.double({ min: 0.001, max: 1000, noNaN: true })
    );
    const duration = fc.oneof(
      fc.constantFrom<unknown>(undefined, null, -1, Number.NaN, Number.POSITIVE_INFINITY, '12'),
      fc.double({ min: 0, max: 1e5, noNaN: true })
    );
    const usable = (value: unknown, min: (x: number) => boolean) =>
      typeof value === 'number' && Number.isFinite(value) && min(value);
    await fc.assert(
      fc.asyncProperty(kind, rate, duration, async (mediaType, fps, durationSec) => {
        stubListing([listed('item', 9, { mediaType, fps, durationSec })]);
        const items = await fetchDiscoveredMedia();
        const kept =
          mediaType === 'image' ||
          (mediaType === 'video' && usable(fps, (x) => x > 0) && usable(durationSec, (x) => x >= 0));
        expect(items).toHaveLength(kept ? 1 : 0);
        if (!kept) return;
        const [item] = items;
        if (mediaType === 'image') {
          expect(item).toMatchObject({ mediaKind: 'image', fps: null, durationSec: 0 });
        } else {
          expect(item!.mediaKind).toBe('video');
          expect(item!.fps).toBe(fps);
          expect(item!.durationSec).toBe(durationSec);
        }
        const asset = SourceAssetSchema.parse(buildSessionFromDiscoveredMedia(item!).sourceAsset);
        // A still carries no frame rate; a clip carries the one discovery probed, in thousandths
        if (mediaType === 'image') {
          expect(asset).not.toHaveProperty('fpsNumerator');
          expect(asset).not.toHaveProperty('fpsDenominator');
        } else {
          expect(asset).toMatchObject({ fpsNumerator: Math.max(1, Math.round((fps as number) * 1000)), fpsDenominator: 1000 });
        }
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          ['video', undefined, 4],
          ['video', 30, undefined],
          ['image', 25, 0],
          [undefined, 30, 4],
        ],
      }
    );
  });
});

describe('backend calls report failure as it is', () => {
  it('lists nothing when discovery is unreachable or answers with an error', async () => {
    stubFetch(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(await fetchDiscoveredMedia()).toEqual([]);
    stubFetch(async () => ({ ok: false, json: async () => ({ items: [] }) }));
    expect(await fetchDiscoveredMedia()).toEqual([]);
  });

  it('returns null for a voice request when the backend is unreachable or answers with an error', async () => {
    const session = createEmptyObservationSession('2031-06-01T00:00:00.000Z');
    stubFetch(async () => {
      throw new TypeError('Failed to fetch');
    });
    expect(await postVoiceIntentToBackend('what is that', session)).toBeNull();
    stubFetch(async () => ({ ok: false, json: async () => ({ scientificExplanation: 'not used' }) }));
    expect(await postVoiceIntentToBackend('what is that', session)).toBeNull();
    stubFetch(async () => ({ ok: true, json: async () => ({ scientificExplanation: 'from the backend' }) }));
    expect(await postVoiceIntentToBackend('what is that', session)).toEqual({ scientificExplanation: 'from the backend' });
  });
});
