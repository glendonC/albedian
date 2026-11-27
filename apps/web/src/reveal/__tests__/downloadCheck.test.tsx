// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// download-check-one (UIUX-export-READY.md, "After Download is pressed", DECIDED-BY-LEAD 12:28 on 27 Nov, from GitHub's
// copy control read live): from the press the arrow in Download's circle becomes a check, the press taken and the file
// handed to the browser, nothing more; it stays while that restored version stands and another version shown restores
// the arrow; the words stay "Download" and a press again saves another copy; the same on the laptop and the phone.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const ARROW =
  'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0Zm-101.66,5.66a8,8,0,0,0,11.32,0l40-40a8,8,0,0,0-11.32-11.32L136,124.69V32a8,8,0,0,0-16,0v92.69L93.66,98.34a8,8,0,0,0-11.32,11.32Z';
const CHECK = 'M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z';

let container: HTMLDivElement;
let root: Root;
let saved: string[];
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  saved = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.getAttribute('href')!);
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete document.documentElement.dataset.phoneScroll;
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const download = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement;
const circle = () => download().querySelector('.look-main__circle path')!.getAttribute('d');
const press = (b: HTMLButtonElement) => act(() => b.click());

for (const phone of [false, true]) {
  const W = phone ? 390 : 1440;
  const H = phone ? 844 : 900;
  const base = poseSession(sample('plate_calibrated'));
  const asset = base.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  const session = (phone ? { ...base, cropTransform: { ...base.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, W, H) } } : base) as ObservationSession;
  const sha = asset.sha256;
  const variant = (id: string, createdAtUTC: string, byteSize: number) => ({ ...template, id, createdAtUTC, byteSize, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${id}/file` });
  const OLDER = variant('var_0000000000000001', '2026-11-18T18:20:00Z', 14_000_000);
  const NEWER = variant(succeeded.variantId!, '2026-11-19T18:20:00Z', 14_600_000);
  const rows = () => [...container.querySelectorAll<HTMLButtonElement>(phone ? '.restore-phone-versions .restore-versions__row' : '[data-testid="restore-versions"] button')];
  async function showDone(variants: unknown[]) {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
    const extra = phone
      ? { phone: true }
      : { restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } } };
    act(() => root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: W, viewportHeightPx: H, ...extra } as unknown as Parameters<typeof RevealPhoto>[0])));
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
  }
  const where = phone ? 'on the phone' : 'on the laptop';

  describe(`download-check-one ${where}`, () => {
    it('turns the arrow into a check from the press, the words and everything else unchanged', async () => {
      await showDone([OLDER, NEWER]);
      expect(circle()).toBe(ARROW);
      const before = container.innerHTML;
      press(download());
      expect(saved).toEqual([`/api/media/${sha}/variants/${NEWER.id}/export`]);
      expect(circle()).toBe(CHECK);
      expect(download().textContent).toBe('Download');
      expect(container.innerHTML).toBe(before.replace(ARROW, CHECK));
    });

    it('saves another copy on a press again, the check kept', async () => {
      await showDone([OLDER, NEWER]);
      press(download());
      press(download());
      expect(saved).toEqual([`/api/media/${sha}/variants/${NEWER.id}/export`, `/api/media/${sha}/variants/${NEWER.id}/export`]);
      expect(circle()).toBe(CHECK);
    });

    it('gives the arrow back when another version is shown, and on coming back to the one saved', async () => {
      await showDone([OLDER, NEWER]);
      press(download());
      expect(circle()).toBe(CHECK);
      press(rows()[1]!);
      expect(q('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
      expect(circle()).toBe(ARROW);
      press(rows()[0]!);
      expect(circle()).toBe(ARROW);
      // The version shown is the one saved: the other is pressed, saved, and checked alone
      press(rows()[1]!);
      press(download());
      expect(saved.at(-1)).toBe(`/api/media/${sha}/variants/${OLDER.id}/export`);
      expect(circle()).toBe(CHECK);
    });
  });
}
