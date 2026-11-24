// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D34 (UIUX-cliprestore-READY.md, decided by the lead 17:08): a restored clip compared with its original on D16's clip
// screen with D10's compare in the clip's place: the restored clip, muted, over the stage's original, cut at the line,
// on the original's clock; the labels 18 px down at the window's middle band; Restore offered on a clip.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const jobs = (JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);
const clip = poseSession(sample('plate_calibrated'), 'video');
const sha = clip.sourceAsset!.sha256;

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});
const q = (s: string) => container.querySelector(s);
const show = (props: Record<string, unknown>) =>
  act(() => root.render(createElement(RevealPhoto, { session: clip, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: 1440, viewportHeightPx: 900, ...props })));

describe('the restored clip compared (D34)', () => {
  it('draws the restored clip muted over the stage original, on its clock, with no second original', () => {
    const parent = document.createElement('video');
    parent.currentTime = 0;
    show({ clipVideo: parent, restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } } });
    const compare = q('[data-testid="restore-compare"]')!;
    expect(compare.className).toBe('restore-compare is-clip');
    const restored = compare.querySelector('.restore-compare__pic video') as HTMLVideoElement;
    expect(restored.getAttribute('src')).toBe(`/api/media/${sha}/variants/${succeeded.variantId}/file`);
    expect(restored.muted).toBe(true);
    expect(compare.querySelector('.restore-compare__original')).toBeNull();
    // On the parent's clock: a seek of the original brings the restored clip to its time
    const play = vi.spyOn(restored, 'play').mockResolvedValue(undefined);
    Object.defineProperty(parent, 'paused', { value: false, configurable: true });
    act(() => {
      parent.dispatchEvent(new Event('play'));
    });
    expect(play).toHaveBeenCalled();
  });

  it('offers Restore on a clip', () => {
    show({ restoration: { phase: 'idle' }, onRestore: () => {} });
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Restore')).toBe(true);
  });

  it("sets the clip's labels 18 px down at the window's middle band", () => {
    const rules: Record<string, Record<string, string>> = {};
    postcss.parse(css).walkRules((rule) => {
      if (!rule.selector.startsWith('.restore-compare.is-clip ')) return;
      rules[rule.selector.replace(/\s+/g, ' ')] = Object.fromEntries((rule.nodes ?? []).map((d) => [(d as postcss.Declaration).prop, (d as postcss.Declaration).value]));
    });
    expect(rules).toEqual({
      '.restore-compare.is-clip .restore-compare__label': { top: '18px' },
      '.restore-compare.is-clip .restore-compare__label.is-left': { left: '400px' },
      '.restore-compare.is-clip .restore-compare__label.is-right': { right: '400px' },
    });
  });

  it('keeps the restored clip playing and aligned to the parent clock across D36 version switches and async loadedmetadata', async () => {
    const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../../tests/fixtures/media_contract.json'), 'utf8')) as {
      valid: { MediaVariant: Array<Record<string, unknown>> };
    };
    const base = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
    const vOld = { ...base, id: 'var_0000000000000016', createdAtUTC: '2026-11-18T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_0000000000000016/file` };
    const vNew = { ...base, id: succeeded.variantId!, createdAtUTC: '2026-11-19T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${succeeded.variantId}/file` };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ variants: [vOld, vNew], attempts: [] }), { status: 200 }));

    const parent = document.createElement('video');
    parent.currentTime = 4.25;
    parent.playbackRate = 1.5;
    parent.loop = true;
    Object.defineProperty(parent, 'paused', { value: false, configurable: true });
    const playSpy = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);

    show({
      clipVideo: parent,
      restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${sha}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
    });
    for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));

    const restored = q('[data-testid="restore-compare"] .restore-compare__pic video') as HTMLVideoElement;
    expect(restored.currentTime).toBeCloseTo(4.25, 6);
    expect(restored.playbackRate).toBe(1.5);
    expect(restored.loop).toBe(true);
    expect(playSpy).toHaveBeenCalledTimes(1);

    // Switch to older clip version while parent is playing: HTML5 load resets child.currentTime = 0 and paused = true
    playSpy.mockClear();
    const versionRows = [...container.querySelectorAll<HTMLButtonElement>('[data-testid="restore-versions"] button')];
    expect(versionRows).toHaveLength(2);
    act(() => {
      restored.currentTime = 0;
      versionRows[1]!.click();
    });
    expect(restored.getAttribute('src')).toBe(`/api/media/${sha}/variants/${vOld.id}/file`);
    expect(restored.currentTime).toBeCloseTo(4.25, 6);
    expect(playSpy).toHaveBeenCalledTimes(1);

    // Pause parent at solved frame 5.0 s, simulate browser resource-load clobbering child.currentTime and playbackRate before loadedmetadata fires
    Object.defineProperty(parent, 'paused', { value: true, configurable: true });
    parent.currentTime = 5.0;
    playSpy.mockClear();
    restored.currentTime = 0;
    restored.playbackRate = 1;
    act(() => {
      restored.dispatchEvent(new Event('loadedmetadata'));
    });
    expect(restored.currentTime).toBeCloseTo(5.0, 6);
    expect(restored.playbackRate).toBe(1.5);
    expect(playSpy).not.toHaveBeenCalled();
  });
});
