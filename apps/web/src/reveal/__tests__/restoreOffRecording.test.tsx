// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AlbedianAction, MediaVariant, ObservationSession, UploadJob } from '@albedian/contracts';
import { dispatchAlbedianAction, executeVoiceOrTextCommand } from '@albedian/observation';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { withShownRestoration } from '../shownRestoration.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
vi.mock('../../spatial/flight/FlightStarsLayer.js', () => ({ FlightStarsLayer: () => null }));

// voice-compare-one version two (AUDIT-C's FAIL at cda2b439, the lead's answer on it): the live flow, nothing put in the
// session by hand. Restore has run and its compare shows; the person leaves the recording for the flight, Real sky or
// the predicted sky and asks to make the sky clearer. The real RevealPhoto reports the restored version it shows, and
// the harness plays the viewport's part exactly as SpatialObservatoryViewport.tsx does (onShownRestoration: only a
// change reaches the session, through withShownRestoration), the leaving through the reducer, the question through the
// app's own parser and reducer (executeVoiceOrTextCommand, as App.tsx's runCommand calls it).

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const LAPTOP = { w: 1440, h: 900 };
const FLIGHT = { name: 'Betelgeuse', toStarLy: 1, fromEarthLy: 548 };
const OFF = "The restored version shows on this photo itself; Back to Moment returns to it.";

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

/** RevealPhoto over a photo whose Restore is done, the viewport's part played as the viewport plays it. */
function restored(name: 'plate_calibrated' | 'horizon_predicted_only') {
  let session: ObservationSession = { ...poseSession(sample(name)), mediaVariants: [] };
  const sha = session.sourceAsset!.sha256;
  const variant = { ...template, id: succeeded.variantId!, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${succeeded.variantId}/file` };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [variant], attempts: [] }), { status: 200 }));
  // SpatialObservatoryViewport.tsx's onShownRestoration, as it stands
  let shownRestorationId: string | null = null;
  const onShownRestoration = (shown: MediaVariant | null) => {
    const before = shownRestorationId;
    const now = shown?.id ?? null;
    if (now === before) return;
    shownRestorationId = now;
    session = withShownRestoration(session, shown, before);
  };
  const show = async (extra: Record<string, unknown> = {}) => {
    act(() =>
      root.render(
        createElement(RevealPhoto, {
          session,
          onSelectFinding: () => {},
          onHome: () => {},
          viewportWidthPx: LAPTOP.w,
          viewportHeightPx: LAPTOP.h,
          phone: false,
          onShowPredictedSky: () => {},
          onRestore: () => {},
          restoration: { phase: 'done', job: succeeded, variantUrl: variant.uri, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
          onShownRestoration,
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
  const ask = (words: string) => {
    const result = executeVoiceOrTextCommand(session, words, 'text');
    session = result.session;
    return result.outcome;
  };
  const compareShown = () => container.querySelector('[data-testid="restore-compare"]') !== null;
  return { show, run, ask, compareShown, variant, session: () => session };
}

describe('asked to make the sky clearer off the recording, after Restore', () => {
  it('in the flight: the restored version is said to show on the photo, nothing changed', async () => {
    const r = restored('plate_calibrated');
    await r.show();
    expect(r.compareShown()).toBe(true);
    expect(r.session().mediaVariants.map((v) => v.id)).toEqual([r.variant.id]);
    r.run({ type: 'space.enter', findingId: 'finding-star-betelgeuse', preserveTime: true });
    await r.show({ flight: FLIGHT });
    expect(r.compareShown()).toBe(false);
    const before = r.session();
    expect(r.ask('make the sky clearer')).toMatchObject({ status: 'rejected', reason: 'not_connected', explanation: OFF });
    expect(r.session().activeVariantId).toBe(before.activeVariantId);
    expect(r.session().spatialView.mode).toBe('catalog_3d');
  });

  it('in Real sky: the restored version is said to show on the photo itself', async () => {
    const r = restored('plate_calibrated');
    await r.show();
    r.run({ type: 'sky.show', mode: 'measured', observationSolutionId: r.session().observationSolution!.id });
    await r.show({ realSky: true });
    expect(r.compareShown()).toBe(false);
    const outcome = r.ask('make the sky clearer');
    expect(outcome).toMatchObject({ status: 'rejected', reason: 'not_connected', explanation: 'The restored version shows on this photo itself; Your photo returns to it.' });
    expect(r.session().spatialView.mode).toBe('registered_sky');
  });

  it('on the predicted sky, which takes the reveal down: the same as in the flight', async () => {
    const r = restored('horizon_predicted_only');
    await r.show();
    r.run({ type: 'sky.show', mode: 'predicted', observationSolutionId: r.session().observationSolution!.id });
    act(() => root.render(null));
    expect(r.ask('make the sky clearer')).toMatchObject({ status: 'rejected', reason: 'not_connected', explanation: OFF });
  });

  it('back on the recording, the compare shows again and the request shows the restored version', async () => {
    const r = restored('plate_calibrated');
    await r.show();
    r.run({ type: 'space.enter', findingId: 'finding-star-betelgeuse', preserveTime: true });
    await r.show({ flight: FLIGHT });
    r.run({ type: 'source.return', restoreAudio: true });
    await r.show({ flight: null });
    expect(r.compareShown()).toBe(true);
    expect(r.ask('make the sky clearer')).toMatchObject({ status: 'completed' });
    expect(r.session().activeVariantId).toBe(r.variant.id);
  });

  it('with no restoration ever shown, off the recording it still says none, so the button may run', async () => {
    const r = restored('plate_calibrated');
    // The reveal never drawn: no restoration reported, so the session holds none
    r.run({ type: 'space.enter', findingId: 'finding-star-betelgeuse', preserveTime: true });
    expect(r.ask('make the sky clearer')).toMatchObject({ status: 'rejected', reason: 'missing_source', explanation: 'This photo has not been restored.' });
  });
});
