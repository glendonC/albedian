// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DISCLOSURES, orientedSize, type ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { sessionPhotoRect } from '../../components/sourcePhotoRect.js';
import { LOOK_ICON_PATHS } from '../../look/index.js';
import { PHONE_SLOT, phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D10 item 5 (UIUX-restoration-READY.md, approved by the owner 18 Nov; rsp-cmp in .cache/uiux/restore/screen), built at
// phone width by the lead's 13:17 answer on 21 Nov and D38 item 4 (UIUX-restorephone-READY.md, decided 13:32): a photo
// that already has a ready restoration shows D10's compare in the phone's 390 by 520 slot from y 118, the two labels
// 16 px in, the fixed sentence under it at top 662 and Download compact at 772, where Ask out loud sits. Nothing new.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;

const W = 390;
const H = 844;
/** The session as the viewport hands it to the phone's reveal: the photo covering the slot (phoneSlotCrop). */
function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, W, H) } } as ObservationSession;
}
const photo = slotted(poseSession(sample('plate_calibrated')));
const sha = photo.sourceAsset!.sha256;
const variant = (id: string, createdAtUTC: string) => ({
  ...template,
  id,
  createdAtUTC,
  sourceSha256: sha,
  sourceAssetId: `src_${sha.slice(0, 16)}`,
  uri: `/api/media/${sha}/variants/${id}/file`,
});
const OLDER = variant('var_0000000000000001', '2026-11-18T18:20:00Z');
const NEWER = variant('var_0000000000000002', '2026-11-19T18:20:00Z');

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
const answer = (variants: unknown[]) =>
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
async function showPhone(extra: Record<string, unknown> = {}) {
  act(() =>
    root.render(
      createElement(RevealPhoto, { session: photo, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: W, viewportHeightPx: H, phone: true, ...extra })
    )
  );
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}
const download = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement | undefined;
const px = (n: number) => `${n}px`;

function declarationsOf(selector: string): Record<string, string> {
  const found: Record<string, string> = {};
  let rules = 0;
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector || rule.parent?.type !== 'root') return;
    rules += 1;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  expect(rules, selector).toBe(1);
  return found;
}

describe('a ready restoration at phone width (D10 item 5)', () => {
  it('reads the stored restorations and shows the newest in the slot, the photo kept where the stage draws it', async () => {
    const fetched = answer([OLDER, NEWER]);
    await showPhone();
    expect(fetched.mock.calls.map(([url]) => url)).toContain(`/api/media/${sha}/variants`);
    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    expect(compare.className).toBe('restore-compare is-phone');
    // The compare's box is the slot, with hard edges as rsp-cmp draws it
    expect([compare.style.left, compare.style.top, compare.style.width, compare.style.height]).toEqual([px(0), px(PHONE_SLOT.topPx), px(W), px(PHONE_SLOT.heightPx)]);
    expect(compare.style.maskImage ?? '').toBe('');
    // Both pictures stay where the stage draws the photo, which covers the slot
    const frame = sessionPhotoRect(photo, W, H)!;
    expect(frame.heightPx >= PHONE_SLOT.heightPx - 0.5 && frame.widthPx >= W - 0.5).toBe(true);
    const layers = [...compare.querySelectorAll<HTMLElement>('.restore-compare__layer')];
    expect(layers).toHaveLength(2);
    for (const layer of layers) {
      expect([layer.style.left, layer.style.top, layer.style.width, layer.style.height]).toEqual([
        px(frame.leftPx),
        px(frame.topPx - PHONE_SLOT.topPx),
        px(frame.widthPx),
        px(frame.heightPx),
      ]);
    }
    expect(compare.querySelector('.restore-compare__original img')!.getAttribute('src')).toBe(photo.sourceAsset!.uri);
    expect(compare.querySelector('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${sha}/variants/${NEWER.id}/file`);
    expect([...compare.querySelectorAll('.restore-compare__label')].map((b) => b.textContent)).toEqual(['Original', 'Restored']);
    expect(compare.style.getPropertyValue('--compare-label-top')).toBe('');
  });

  it('says the fixed sentence under it and puts Download where Ask out loud sits, saving the one shown', async () => {
    answer([OLDER, NEWER]);
    await showPhone();
    const note = q('.restore-phone-note')!;
    expect(note.textContent).toBe(DISCLOSURES.restoration);
    expect(note.querySelector('svg.look-i path')!.getAttribute('d')).toBe(LOOK_ICON_PATHS.info);
    const button = download()!;
    expect(button.className).toContain('look-main');
    expect(button.className).toContain('look-size-phone');
    expect(button.closest('.restore-phone-go')).not.toBeNull();
    let href: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      href = this.getAttribute('href');
    });
    act(() => button.click());
    expect(href).toBe(`/api/media/${sha}/variants/${NEWER.id}/export`);
  });

  it("tells the viewport while it shows, so Ask out loud gives way, and gives the counts' place to the sentence", async () => {
    const told = vi.fn();
    answer([NEWER]);
    await showPhone({ onPhoneFootChange: told });
    expect(told).toHaveBeenLastCalledWith(true);
    expect(q('[data-testid="reveal-legend"]')).toBeNull();
    act(() => root.unmount());
    root = createRoot(container);
    const later = vi.fn();
    answer([]);
    await showPhone({ onPhoneFootChange: later });
    expect(later).not.toHaveBeenCalledWith(true);
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(q('.restore-phone-note')).toBeNull();
    expect(download()).toBeUndefined();
    expect(q('[data-testid="reveal-legend"]')).not.toBeNull();
  });

  it('shows nothing of it over Real sky, with the photo bare, or on the laptop', async () => {
    answer([NEWER]);
    await showPhone({ realSky: true });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(download()).toBeUndefined();
    await showPhone({ originalPixels: true });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    await showPhone({ phone: false, viewportWidthPx: 1440, viewportHeightPx: 900 });
    // The laptop shows a restoration only once its own Restore is done (D10), as before
    expect(q('[data-testid="restore-compare"]')).toBeNull();
  });

  it("keeps a clip's phone compare for D16's phone player: no compare and no read on a clip", async () => {
    const fetched = answer([NEWER]);
    const clip = slotted(poseSession(sample('plate_calibrated'), 'video'));
    await showPhone({ session: clip });
    expect(q('[data-testid="restore-compare"]')).toBeNull();
    expect(fetched.mock.calls.map(([url]) => String(url)).filter((url) => url.endsWith('/variants'))).toEqual([]);
    expect(clip.sourceAsset!.mediaKind).toBe('video');
  });

  it('leaves the foot to a picked star, whose card covers it', async () => {
    answer([NEWER]);
    const picked = photo.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    await showPhone({ session: { ...photo, selectedFindingId: picked.id } });
    expect(q('[data-testid="restore-compare"]')).not.toBeNull();
    expect(q('.restore-phone-note')).toBeNull();
    expect(download()).toBeUndefined();
  });
});

describe("the phone compare's rules are rsp-cmp's", () => {
  it('sets the labels 16 px in, the sentence at 662 and Download centred at 772', () => {
    expect(declarationsOf('.restore-compare.is-phone .restore-compare__label')).toEqual({ top: '16px' });
    expect(declarationsOf('.restore-compare.is-phone .restore-compare__label.is-left')).toEqual({ left: '16px' });
    expect(declarationsOf('.restore-compare.is-phone .restore-compare__label.is-right')).toEqual({ right: '16px' });
    expect(declarationsOf('.restore-phone-note')).toEqual({
      '--look-ic': '13px',
      position: 'absolute',
      left: '24px',
      right: '24px',
      top: '662px',
      margin: '0',
      display: 'flex',
      gap: '7px',
      font: '400 13px / 1.45 var(--look-font-sans)',
      color: 'var(--look-ink-2)',
    });
    expect(declarationsOf('.restore-phone-note .look-i')).toEqual({ flex: 'none', 'margin-top': '3px' });
    expect(declarationsOf('.restore-phone-go')).toEqual({ position: 'absolute', left: '0', right: '0', top: '772px', display: 'flex', 'justify-content': 'center' });
  });
});
