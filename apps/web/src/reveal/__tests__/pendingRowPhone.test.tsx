// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, UploadJob } from '@albedian/contracts';
import { DISCLOSURES } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { NO_PLAYBACK_NOTE } from '../../services/apiClient.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { NO_COPY_LINE } from '../RestoreParts.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// pending-row-phone-one (UIUX-versionwait-READY.md, its addition of 03:48 on 27 Nov, ruled by the lead): on the phone,
// while the original stands alone because of the chosen version (its copy pending, no copy, no copy made, a copy that
// stopped), that version's row stands in the disclosure's place above the player, as it is in the list, its foot 6 px
// above the player's time chip; once the restored version is drawn the row goes back to the list (or leaves, with one
// restoration) and the disclosure takes the place. The laptop is unchanged.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const doneJob = jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!;
const bareClip = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.mimeType === 'video/mp4' && v.playbackCopy === undefined)!;
const withCopy = media.valid.MediaVariant.find((v) => v.kind === 'restoration' && v.playbackCopy !== undefined)!;
const W = 390;
const H = 844;

function slottedClip(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(asset.width, asset.height, W, H, 'video') } } as ObservationSession;
}
const clip = slottedClip(poseSession(sample('plate_calibrated'), 'video'));
const sha = clip.sourceAsset!.sha256;
const NEWEST = doneJob.variantId!;
const OLDER = 'var_00000000000000e1';
const NOTED_ATTEMPT = 'att_00000000000000e1';
const version = (id: string, createdAtUTC: string, extra: Record<string, unknown> = {}) => ({
  ...bareClip,
  id,
  createdAtUTC,
  sourceSha256: sha,
  sourceAssetId: `src_${sha.slice(0, 16)}`,
  uri: `/api/media/${sha}/variants/${id}/file`,
  ...extra,
});
const copyUri = `/api/media/${sha}/variants/${NEWEST}/playback`;

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
const q = (s: string) => container.querySelector(s);
const flush = async () => {
  for (let i = 0; i < 6; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
};

/** The variants route lists `variants` (with a noted attempt for OLDER); the copy's POST answers `start`. */
function serve(variants: unknown[], start: Promise<Response>) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === 'POST' && url === copyUri) return start;
    if (url === copyUri) return new Response('x', { status: 206, headers: { 'content-type': 'video/mp4' } });
    if (url === `/api/media/${sha}/variants/${NEWEST}`) {
      return new Response(JSON.stringify({ variant: { ...version(NEWEST, '2026-11-26T02:00:00Z'), playbackCopy: { ...(withCopy.playbackCopy as Record<string, unknown>), uri: copyUri } } }), { status: 200 });
    }
    return new Response(JSON.stringify({ variants, attempts: [{ id: NOTED_ATTEMPT, measured: { notes: [NO_PLAYBACK_NOTE] } }] }), { status: 200 });
  });
}
async function showPhoneClip() {
  act(() =>
    root.render(
      createElement(RevealPhoto, {
        session: clip,
        onSelectFinding: () => {},
        onHome: () => {},
        phone: true,
        viewportWidthPx: W,
        viewportHeightPx: H,
        restoration: { phase: 'done', job: doneJob, variantUrl: `/api/media/${sha}/variants/${NEWEST}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
      })
    )
  );
  await flush();
}
const lines = (el: Element | null) => [...(el?.querySelectorAll('.restore-versions__older') ?? [])].map((s) => s.textContent);

describe("the chosen version's row in the disclosure's place on the phone", () => {
  it('stands a lone version waiting for its copy above the player, with no disclosure and no list under Download', async () => {
    serve([version(NEWEST, '2026-11-26T02:00:00Z')], new Promise<Response>(() => {}));
    await showPhoneClip();
    const row = q('.restore-phone-slot .restore-versions__row')!;
    expect(row.getAttribute('aria-busy')).toBe('true');
    expect(row.querySelector('.restore-versions__size')!.textContent).toBe('0 s');
    expect(row.querySelector('.restore-versions__dot')).not.toBeNull();
    expect(q('.restore-phone-note')).toBeNull();
    expect(q('.restore-phone-versions')).toBeNull();
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect([...container.querySelectorAll('button')].some((b) => b.textContent === 'Download')).toBe(true);
  });

  it('keeps the other versions in the list, each with its own lines, while the chosen one stands in the place', async () => {
    serve([version(NEWEST, '2026-11-26T02:00:00Z'), version(OLDER, '2026-11-25T02:00:00Z', { attemptId: NOTED_ATTEMPT })], new Promise<Response>(() => {}));
    await showPhoneClip();
    expect(q('.restore-phone-slot .restore-versions__row')!.getAttribute('aria-busy')).toBe('true');
    expect(lines(q('.restore-phone-slot'))).toEqual([]);
    const listed = [...container.querySelectorAll('.restore-phone-versions .restore-versions__row')];
    expect(listed).toHaveLength(1);
    expect(lines(listed[0]!)).toEqual(['Made by an earlier version of Albedian.', NO_COPY_LINE]);
  });

  it('gives the place to the disclosure once the copy is drawn, and the row leaves with one restoration', async () => {
    let release: (answer: Response) => void = () => {};
    serve([version(NEWEST, '2026-11-26T02:00:00Z')], new Promise<Response>((resolve) => (release = resolve)));
    await showPhoneClip();
    expect(q('.restore-phone-slot')).not.toBeNull();
    release(new Response(JSON.stringify({ detail: 'that variant has a playback copy' }), { status: 409 }));
    await flush();
    expect(q('.restore-phone-slot')).toBeNull();
    expect(q('[data-testid="restore-compare"] video')!.getAttribute('src')).toBe(copyUri);
    act(() => void q('[data-testid="restore-compare"] .restore-compare__pic video')!.dispatchEvent(new Event('loadeddata')));
    expect(q('.restore-phone-note span')!.textContent).toBe(DISCLOSURES.restoration);
    expect(q('.restore-phone-slot')).toBeNull();
    expect(q('.restore-phone-versions')).toBeNull();
  });

  it("stands its foot where the disclosure's stands, 6 px above the time chip, and words in as the disclosure does", () => {
    const decls = (selector: string) => {
      const found: Record<string, string> = {};
      postcss.parse(css).walkRules((rule) => {
        if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
        rule.walkDecls((d) => void (found[d.prop] = d.value.replace(/\s+/g, ' ')));
      });
      return found;
    };
    expect(decls('.restore-phone-slot')).toEqual({ position: 'absolute', left: '24px', right: '24px', bottom: '198px', 'pointer-events': 'auto' });
    expect(decls('.restore-phone-slot .restore-versions__row')).toEqual({ 'min-height': '44px' });
    expect(decls('.restore-phone-note.is-clip').bottom).toBe(decls('.restore-phone-slot').bottom);
    const motion = fs.readFileSync(path.resolve(here, '../../styles/motion.css'), 'utf8');
    let wordsIn = false;
    postcss.parse(motion).walkRules((rule) => {
      if (rule.selectors.includes('.restore-phone-slot') && rule.selectors.includes('.restore-phone-note')) wordsIn = true;
    });
    expect(wordsIn).toBe(true);
  });
});
