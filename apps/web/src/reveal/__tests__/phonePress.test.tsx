// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import type { Arrival } from '../arrival.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { UNCHANGED } from '../restoration.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { WorkingScreen } from '../WorkingScreen.js';
import { unpressable, winningPointer } from './winningPointer.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// Found in the 390 by 844 captures of 22 Nov: the phone's buttons over the photo did not take a press. The working
// screen and the reveal let the pointer through to the photo (pointer-events none), and only the laptop's buttons were
// given it back (.work-acts, .reveal-status); on the phone, Cancel and Close while a photo is read, Try again and Upload
// another after it stops (D2, D37), and the restoration's Download, Cancel and Try again (D10 item 5, D38) inherited
// none, so a press went to the photo under them.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const done = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const { finishedAtUTC: _f, attemptId: _a, variantId: _v, ...rest } = structuredClone(done);
const still = (stage: string) => ({ stage, unit: 'frames', done: 1, total: 1 });
const RUNNING = { ...rest, state: 'running', stages: [still('decode'), still('measure')] } as unknown as UploadJob;
const FAILED = { ...rest, state: 'failed', stages: [still('decode'), still('measure'), { stage: 'restore', unit: 'frames', done: 0, total: 1 }] } as unknown as UploadJob;

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
const photo = slotted(poseSession(sample('plate_calibrated')));
const sha = photo.sourceAsset!.sha256;
const ready = { ...template, id: 'var_0000000000000002', createdAtUTC: '2026-11-19T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_0000000000000002/file` };

const BYTES = 2_382_523;
const stage = (name: string, unit: string, n: number, total: number | null) => ({ stage: name, unit, done: n, total });
const WORKING_JOB = {
  state: 'running',
  stages: [stage('store', 'bytes', BYTES, BYTES), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)],
};
const arrival = (over: Partial<Arrival> = {}): Arrival => ({ fileName: 'night.jpg', byteSize: BYTES, stored: true, widthPx: 3024, heightPx: 4032, job: WORKING_JOB, failure: null, ...over });
const RECT = { leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 };

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
const noop = () => {};
const labels = () => [...container.querySelectorAll('button')].map((b) => b.textContent?.trim() || b.getAttribute('aria-label'));
function working(props: Record<string, unknown>) {
  act(() => root.render(createElement(WorkingScreen, { arrival: arrival(), photoRect: RECT, onTryAgain: noop, onUploadAnother: noop, onHome: noop, onCancel: noop, ...props })));
}
async function reveal(props: Record<string, unknown>, variants: unknown[] = []) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: photo,
        onSelectFinding: noop,
        onHome: noop,
        viewportWidthPx: 390,
        viewportHeightPx: 844,
        phone: true,
        restoration: { phase: 'idle' },
        onRestore: noop,
        onCancelRestore: noop,
        ...props,
      } as unknown as Parameters<typeof RevealPhoto>[0])
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}

describe("the phone's buttons over the photo take a press", () => {
  it('reads the cascade as a browser does: the screens let the pointer through, the laptop buttons take it back', () => {
    working({ phone: false, arrival: arrival({ job: { ...WORKING_JOB, state: 'failed' }, failure: { sentence: 'The solve stopped.', canTryAgain: true } }) });
    expect(winningPointer(container.querySelector('.work-screen')!)).toBe('none');
    expect(labels()).toEqual(expect.arrayContaining(['Try again', 'Upload another']));
    expect(unpressable(container)).toEqual([]);
  });

  it('while a photo is read at phone width: Close and Cancel', () => {
    working({ phone: true });
    expect(labels()).toEqual(['Close', 'Cancel']);
    expect(unpressable(container)).toEqual([]);
  });

  it('after it stops at phone width (D37): Close, Try again and Upload another', () => {
    working({ phone: true, arrival: arrival({ job: { ...WORKING_JOB, state: 'failed' }, failure: { sentence: 'The solve stopped.', canTryAgain: true } }) });
    expect(labels()).toEqual(expect.arrayContaining(['Close', 'Try again', 'Upload another']));
    expect(unpressable(container)).toEqual([]);
  });

  it("the phone compare's Download (D10 item 5)", async () => {
    await reveal({}, [ready]);
    expect(container.querySelector('[data-testid="restore-compare"]')).not.toBeNull();
    expect(labels()).toContain('Download');
    expect(winningPointer(container.querySelector('[data-testid="reveal-photo"]')!)).toBe('none');
    expect(unpressable(container)).toEqual([]);
  });

  it("the restoration's Restore, its wait's Cancel, and Try again after it stops (D38)", async () => {
    await reveal({});
    expect(labels()).toContain('Restore');
    expect(unpressable(container)).toEqual([]);
    await reveal({ restoration: { phase: 'working', job: RUNNING } });
    expect(labels()).toContain('Cancel');
    expect(unpressable(container)).toEqual([]);
    await reveal({ restoration: { phase: 'stopped', job: FAILED, sentence: 'The restoration stopped.', subline: UNCHANGED, canTryAgain: true } });
    expect(labels()).toContain('Try again');
    expect(unpressable(container)).toEqual([]);
  });
});
