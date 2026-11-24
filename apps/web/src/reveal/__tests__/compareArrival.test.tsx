// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { appSoundtrackMoment } from '../../audio/music/soundtrackMoment.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// compare-music-1 (DECIDED-BY-LEAD 12:41 on 24 Nov, UIUX-sound-READY.md): the restored compare's arrival counts as a
// reveal for the music. The compare tells the page's store once when it is drawn; a version switch inside it is no
// arrival, and the compare of another upload is a new one.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
  valid: { MediaVariant: Array<Record<string, unknown>> };
};

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

const photoOf = (seed: string) => {
  const session = poseSession(sample('plate_calibrated'));
  const sha = seed.repeat(64).slice(0, 64);
  return { ...session, sourceAsset: { ...session.sourceAsset!, sha256: sha } } as typeof session;
};
const versionsOf = (sha: string) => {
  const base = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
  const made = (id: string, createdAtUTC: string) => ({ ...base, id, createdAtUTC, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${id}/file` });
  return [made('var_0000000000000016', '2026-11-18T18:20:00Z'), made(succeeded.variantId!, '2026-11-19T18:20:00Z')];
};
async function show(session: ReturnType<typeof photoOf>) {
  const sha = session.sourceAsset!.sha256;
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ variants: versionsOf(sha), attempts: [] }), { status: 200 }));
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session,
        onSelectFinding: () => {},
        onHome: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
        restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
      })
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}

describe("the restored compare's arrival, for the music", () => {
  it('is told once when the compare is drawn, not on a version switch, and again for another upload', async () => {
    const before = appSoundtrackMoment.arrivals();
    await show(photoOf('a'));
    expect(container.querySelector('[data-testid="restore-compare"]')).not.toBeNull();
    expect(appSoundtrackMoment.arrivals()).toBe(before + 1);
    const rows = [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(rows).toHaveLength(2);
    act(() => rows[1]!.click());
    act(() => rows[0]!.click());
    expect(appSoundtrackMoment.arrivals()).toBe(before + 1);
    await show(photoOf('b'));
    expect(appSoundtrackMoment.arrivals()).toBe(before + 2);
    // The moment itself is the viewport's to set; an arrival leaves it as it is
    expect(appSoundtrackMoment.get()).toBe('arrive');
  });
});
