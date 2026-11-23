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
});
