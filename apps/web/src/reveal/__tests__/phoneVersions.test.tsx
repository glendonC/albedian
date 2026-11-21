// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { madeAtWords } from '../../services/apiClient.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// D36 item 5 (UIUX-restorations-READY.md, decided by the lead 13:45 on 19 Nov): at phone width, a photo with two or
// more ready restorations lists them under Download, newest first, as the laptop does, reached by scrolling, its rows
// 44 px; a row pressed shows it in the compare and Download saves the one shown.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;

const W = 390;
const H = 844;
function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, W, H) } } as ObservationSession;
}
const photo = slotted(poseSession(sample('plate_calibrated')));
const sha = photo.sourceAsset!.sha256;
const variant = (id: string, createdAtUTC: string, byteSize: number) => ({
  ...template,
  id,
  createdAtUTC,
  byteSize,
  sourceSha256: sha,
  sourceAssetId: `src_${sha.slice(0, 16)}`,
  uri: `/api/media/${sha}/variants/${id}/file`,
});
const OLDER = variant('var_0000000000000001', '2026-11-18T18:20:00Z', 14_000_000);
const NEWER = variant('var_0000000000000002', '2026-11-19T18:20:00Z', 14_600_000);

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
const rows = () => [...container.querySelectorAll<HTMLButtonElement>('.restore-phone-versions .restore-versions__row')];
const download = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Download') as HTMLButtonElement;

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

describe('the list under Download at phone width (D36 item 5)', () => {
  it('lists two ready restorations under Download, newest first, the shown one checked, the screen scrolling to them', async () => {
    answer([OLDER, NEWER]);
    await showPhone();
    expect(rows().map((row) => row.querySelector('.restore-versions__time')!.textContent)).toEqual([madeAtWords(NEWER.createdAtUTC), madeAtWords(OLDER.createdAtUTC)]);
    expect(rows().map((row) => row.getAttribute('aria-pressed'))).toEqual(['true', 'false']);
    expect(rows()[1]!.querySelector('.restore-versions__older')!.textContent).toBe('Made by an earlier version of Albedian.');
    expect(download().compareDocumentPosition(q('.restore-phone-versions')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(q('[data-testid="reveal-photo"]')!.className).toContain('is-scroll');
    expect(document.documentElement.dataset.phoneScroll).toBe('');
  });

  it('shows a pressed row in the compare and Download saves it', async () => {
    answer([OLDER, NEWER]);
    await showPhone();
    act(() => rows()[1]!.click());
    expect(q('.restore-compare__pic img')!.getAttribute('src')).toBe(`/api/media/${sha}/variants/${OLDER.id}/file`);
    expect(rows().map((row) => row.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    let href: string | null = null;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      href = this.getAttribute('href');
    });
    act(() => download().click());
    expect(href).toBe(`/api/media/${sha}/variants/${OLDER.id}/export`);
  });

  it('draws no list and does not scroll with one ready restoration, nor under a picked star', async () => {
    answer([NEWER]);
    await showPhone();
    expect(q('.restore-phone-versions')).toBeNull();
    expect(q('[data-testid="reveal-photo"]')!.className).not.toContain('is-scroll');
    act(() => root.unmount());
    root = createRoot(container);
    answer([OLDER, NEWER]);
    const star = photo.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    await showPhone({ session: { ...photo, selectedFindingId: star.id } });
    expect(q('.restore-phone-versions')).toBeNull();
  });

  it('sets the list under Download with the phone gutters and 44 px rows', () => {
    expect(declarationsOf('.restore-phone-versions')).toEqual({ position: 'absolute', left: '24px', right: '24px', top: '828px', 'padding-bottom': '40px' });
    expect(declarationsOf('.restore-phone-versions .restore-versions')).toEqual({ margin: '0' });
    expect(declarationsOf('.restore-phone-versions .restore-versions__row')).toEqual({ 'min-height': '44px' });
  });
});
