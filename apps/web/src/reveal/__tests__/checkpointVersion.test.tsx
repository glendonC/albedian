// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type AlbedianAction, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { dispatchAlbedianAction, EMPTY_MEDIA_VARIANT_ID, returnCheckpointOf } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
vi.mock('../../spatial/flight/FlightStarsLayer.js', () => ({ FlightStarsLayer: () => null }));

// checkpoint-version-one (DECIDED-BY-LEAD 18:46 on 24 Nov, AUDIT-C's O5): the checkpoint's promise is the exact state,
// the version included. A row pressed under Download is the session's active variant, which the moment saved on leaving
// carries (CameraCheckpoint.mediaVariantId), so Back to Moment shows that same version again, by every way of leaving.
// D36 stands on the recording itself: Original pixels lets the row go, and the compare shows the newest again.
// RevealPhoto is the real one; the harness plays the viewport's part (the row pressed written to the session, the
// leaving and Back to Moment through the reducer, the props the viewport derives from the session).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);

const LAPTOP = { w: 1440, h: 900 };
const PHONE = { w: 390, h: 844 };

function sessionOf(name: 'plate_calibrated' | 'horizon_predicted_only', phone: boolean): ObservationSession {
  const base = poseSession(sample(name));
  if (!phone) return base;
  const asset = base.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...base, cropTransform: { ...base.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, PHONE.w, PHONE.h) } } as ObservationSession;
}
function versionsOf(sha: string) {
  const variant = (id: string, createdAtUTC: string, byteSize: number) => ({
    ...template,
    id,
    createdAtUTC,
    byteSize,
    sourceSha256: sha,
    sourceAssetId: `src_${sha.slice(0, 16)}`,
    uri: `/api/media/${sha}/variants/${id}/file`,
  });
  return { older: variant('var_0000000000000001', '2026-11-18T18:20:00Z', 14_000_000), newer: variant(succeeded.variantId!, '2026-11-19T18:20:00Z', 14_600_000) };
}

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
  vi.restoreAllMocks();
});
const flush = async () => {
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};

/** RevealPhoto over a photo with two ready restorations, the viewport's part played here. */
function reveal(name: 'plate_calibrated' | 'horizon_predicted_only', phone: boolean) {
  let session = sessionOf(name, phone);
  const sha = session.sourceAsset!.sha256;
  const { older, newer } = versionsOf(sha);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [older, newer], attempts: [] }), { status: 200 }));
  const picks: Array<string | null> = [];
  const size = phone ? PHONE : LAPTOP;
  const show = async (extra: Record<string, unknown> = {}) => {
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session,
          onSelectFinding: () => {},
          onHome: () => {},
          viewportWidthPx: size.w,
          viewportHeightPx: size.h,
          phone,
          onShowPredictedSky: () => {},
          onRestore: () => {},
          restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
          // The viewport's part: the row pressed is the session's active variant, and none once it is let go
          onPickVersion: (id: string | null) => {
            picks.push(id);
            session = { ...session, activeVariantId: id ?? EMPTY_MEDIA_VARIANT_ID };
          },
          ...extra,
        } as unknown as Parameters<typeof RevealPhoto>[0])
      )
    );
    await flush();
  };
  const run = (action: Record<string, unknown>) => {
    const result = dispatchAlbedianAction(session, { ...action, requestId: `req_${session.viewRevision}`, expectedViewRevision: session.viewRevision } as AlbedianAction);
    expect(result.outcome.status, String(action.type)).toBe('completed');
    session = result.session;
  };
  const rows = () => [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
  const pressed = () => rows().map((r) => r.getAttribute('aria-pressed'));
  const comparePic = () => container.querySelector<HTMLImageElement>('[data-testid="restore-compare"] .restore-compare__pic img')?.getAttribute('src') ?? null;
  const fileOf = (id: string) => `/api/media/${sha}/variants/${id}/file`;
  return { show, run, rows, pressed, comparePic, fileOf, picks, older, newer, session: () => session };
}

const FLIGHT = { name: 'Betelgeuse', toStarLy: 1, fromEarthLy: 548 };
const back = { type: 'source.return', restoreAudio: true };

/** Press the older row, then check that it is what the session holds and the compare shows. */
async function pressOlder(r: ReturnType<typeof reveal>) {
  await r.show();
  expect(r.rows()).toHaveLength(2);
  expect(r.pressed()).toEqual(['true', 'false']);
  act(() => r.rows()[1]!.click());
  expect(r.picks).toEqual([r.older.id]);
  expect(r.session().activeVariantId).toBe(r.older.id);
  // The viewport draws again with the session the press wrote, as React does
  await r.show();
  expect(r.pressed()).toEqual(['false', 'true']);
  expect(r.comparePic()).toBe(r.fileOf(r.older.id));
}
/** The moment saved on leaving carries the row pressed, and nothing let it go while away. */
function expectSavedWithOlder(r: ReturnType<typeof reveal>) {
  expect(returnCheckpointOf(r.session())!.mediaVariantId).toBe(r.older.id);
  expect(r.picks).toEqual([r.older.id]);
}
/** Back to Moment shows the older version again, its row pressed, and the session still holds it. */
function expectBackOnOlder(r: ReturnType<typeof reveal>) {
  expect(r.session().activeVariantId).toBe(r.older.id);
  expect(r.comparePic()).toBe(r.fileOf(r.older.id));
  expect(r.pressed()).toEqual(['false', 'true']);
  expect(r.picks).toEqual([r.older.id]);
}

describe('Back to Moment shows the version pressed when the moment was left (laptop)', () => {
  it('left for Real sky', async () => {
    const r = reveal('plate_calibrated', false);
    await pressOlder(r);
    r.run({ type: 'sky.show', mode: 'measured', observationSolutionId: r.session().observationSolution!.id });
    await r.show({ realSky: true });
    expect(r.comparePic()).toBeNull();
    expectSavedWithOlder(r);
    r.run(back);
    await r.show({ realSky: false });
    expectBackOnOlder(r);
  });

  it('left for 3D: the 3D pill, Go there, or words that fly there all open the flight', async () => {
    const r = reveal('plate_calibrated', false);
    await pressOlder(r);
    r.run({ type: 'space.enter', findingId: 'finding-star-betelgeuse', preserveTime: true });
    await r.show({ flight: FLIGHT });
    expect(r.comparePic()).toBeNull();
    expectSavedWithOlder(r);
    r.run(back);
    await r.show({ flight: null });
    expectBackOnOlder(r);
  });

  it('left for the predicted sky, which takes the reveal down: drawn again on return, it shows the same version', async () => {
    const r = reveal('horizon_predicted_only', false);
    await pressOlder(r);
    r.run({ type: 'sky.show', mode: 'predicted', observationSolutionId: r.session().observationSolution!.id });
    act(() => root.render(null));
    expectSavedWithOlder(r);
    r.run(back);
    await r.show();
    expectBackOnOlder(r);
  });
});

describe('Back to Moment shows the version pressed when the moment was left (phone)', () => {
  it('left for Real sky', async () => {
    const r = reveal('plate_calibrated', true);
    await pressOlder(r);
    r.run({ type: 'sky.show', mode: 'measured', observationSolutionId: r.session().observationSolution!.id });
    await r.show({ realSky: true });
    expect(r.comparePic()).toBeNull();
    expectSavedWithOlder(r);
    r.run(back);
    await r.show({ realSky: false });
    expectBackOnOlder(r);
  });

  it('left for 3D', async () => {
    const r = reveal('plate_calibrated', true);
    await pressOlder(r);
    r.run({ type: 'space.enter', findingId: 'finding-star-betelgeuse', preserveTime: true });
    await r.show({ flight: FLIGHT });
    expect(r.comparePic()).toBeNull();
    expectSavedWithOlder(r);
    r.run(back);
    await r.show({ flight: null });
    expectBackOnOlder(r);
  });

  it('left for the predicted sky', async () => {
    const r = reveal('horizon_predicted_only', true);
    await pressOlder(r);
    r.run({ type: 'sky.show', mode: 'predicted', observationSolutionId: r.session().observationSolution!.id });
    act(() => root.render(null));
    expectSavedWithOlder(r);
    r.run(back);
    await r.show();
    expectBackOnOlder(r);
  });
});

describe('the row pressed is let go on the recording itself, as D36 has it', () => {
  it('Original pixels lets it go, in the session too, and the compare shows the newest when it comes back', async () => {
    const r = reveal('plate_calibrated', false);
    await pressOlder(r);
    await r.show({ originalPixels: true });
    expect(r.comparePic()).toBeNull();
    expect(r.picks).toEqual([r.older.id, null]);
    expect(r.session().activeVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    await r.show({ originalPixels: false });
    expect(r.pressed()).toEqual(['true', 'false']);
    expect(r.comparePic()).toBe(r.fileOf(r.newer.id));
    // Leaving now saves the newest shown, with no row pressed, and Back to Moment shows the newest
    r.run({ type: 'sky.show', mode: 'measured', observationSolutionId: r.session().observationSolution!.id });
    expect(returnCheckpointOf(r.session())!.mediaVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    await r.show({ realSky: true });
    r.run(back);
    await r.show({ realSky: false });
    expect(r.pressed()).toEqual(['true', 'false']);
    expect(r.comparePic()).toBe(r.fileOf(r.newer.id));
  });

  it('a return to a moment saved with no row pressed shows the newest, even with a row pressed since', async () => {
    const r = reveal('plate_calibrated', false);
    await r.show();
    // Left with the newest shown, nothing pressed
    r.run({ type: 'sky.show', mode: 'measured', observationSolutionId: r.session().observationSolution!.id });
    await r.show({ realSky: true });
    r.run(back);
    await r.show({ realSky: false });
    act(() => r.rows()[1]!.click());
    await r.show();
    expect(r.pressed()).toEqual(['false', 'true']);
    // Back to the first moment by its id (as voice may name it): that moment had no row pressed
    const first = r.session().navigationHistory.checkpoints.find((c) => c.mediaVariantId === EMPTY_MEDIA_VARIANT_ID && c.navigationState === 'source')!;
    expect(first).toBeDefined();
    r.run({ ...back, checkpointId: first.id });
    await r.show();
    expect(r.pressed()).toEqual(['true', 'false']);
    expect(r.comparePic()).toBe(r.fileOf(r.newer.id));
  });

  it("Original pixels with no row pressed leaves the session's active variant as it is", async () => {
    const r = reveal('plate_calibrated', false);
    await r.show();
    await r.show({ originalPixels: true });
    expect(r.comparePic()).toBeNull();
    expect(r.picks).toEqual([]);
  });

  it('an active variant that names none of the stored versions presses no row', async () => {
    const r = reveal('plate_calibrated', false);
    await r.show();
    expect(r.session().activeVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    expect(r.pressed()).toEqual(['true', 'false']);
    expect(r.picks).toEqual([]);
  });
});
