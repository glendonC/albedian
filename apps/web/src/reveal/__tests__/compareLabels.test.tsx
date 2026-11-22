// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { orientedSize, type ObservationSession, type UploadJob } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { phoneSlotCrop } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';
import { winningPointer } from './winningPointer.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});

// AUDIT-B's read of phonepress-1 (verdicts/phonepress-1.md, a finding outside it), assigned by the lead 04:27 on 22 Nov:
// on the restore compare the star marks stood over the Original and Restored labels, so a press at Restored opened a
// star's card. The labels stand above the marks and take the press, as the compare's other buttons do, with no new look:
// they are drawn after the marks, in a layer of the compare's own box, and before the answer and the star card, so
// those still stand above them; nothing is given a z-index.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');
const fixtures = path.resolve(here, '../../../../../tests/fixtures');
const media = JSON.parse(fs.readFileSync(path.join(fixtures, 'media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
const jobs = (JSON.parse(fs.readFileSync(path.join(fixtures, 'job_contract.json'), 'utf8')) as { valid: UploadJob[] }).valid;
const succeeded = structuredClone(jobs.find((job) => job.kind === 'restoration' && job.state === 'succeeded')!);

function slotted(session: ObservationSession): ObservationSession {
  const asset = session.sourceAsset!;
  const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
  return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, 390, 844) } } as ObservationSession;
}
const matched = poseSession(sample('plate_calibrated'));
const phoneMatched = slotted(matched);
const sha = phoneMatched.sourceAsset!.sha256;
const ready = { ...template, id: 'var_0000000000000002', createdAtUTC: '2026-11-19T18:20:00Z', sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/var_0000000000000002/file` };

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
async function show(props: Record<string, unknown>, variants: unknown[] = []) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants, attempts: [] }), { status: 200 }));
  act(() => root.render(createElement(RevealPhoto, { onSelectFinding: () => {}, onHome: () => {}, ...props } as unknown as Parameters<typeof RevealPhoto>[0])));
  for (let i = 0; i < 5; i++) await act(async () => await new Promise((r) => setTimeout(r, 0)));
}
const laptop = (extra: Record<string, unknown> = {}) => ({
  session: matched,
  viewportWidthPx: 1440,
  viewportHeightPx: 900,
  restoration: { phase: 'done', job: succeeded, variantUrl: `/api/media/${matched.sourceAsset!.sha256}/variants/${succeeded.variantId}/file`, orientation: { quarterTurnsClockwise: 0, mirrored: false } },
  ...extra,
});
const phone = { session: phoneMatched, phone: true, viewportWidthPx: 390, viewportHeightPx: 844 };
const labels = () => [...container.querySelectorAll('.restore-compare__label')] as HTMLButtonElement[];
const after = (a: Element, b: Element) => Boolean(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);
const box = (el: Element) => {
  const s = (el as HTMLElement).style;
  return [s.left, s.top, s.width, s.height];
};

function expectAboveTheMarks() {
  const marks = [...container.querySelectorAll('.reveal-mark')];
  expect(marks.length).toBeGreaterThan(0);
  expect(labels().map((b) => b.textContent)).toEqual(['Original', 'Restored']);
  for (const label of labels()) {
    expect(marks.every((mark) => after(label, mark))).toBe(true);
    expect(winningPointer(label)).toBe('auto');
  }
  // In the compare's own box, so each label sits where it sat
  const layer = labels()[0]!.parentElement!;
  expect(box(layer)).toEqual(box(q('[data-testid="restore-compare"]')!));
  return layer;
}

describe("the compare's labels above the star marks (AUDIT-B's finding, the lead 04:27)", () => {
  it('on the laptop: drawn after the marks in the compare box, taking the press, the line still moving to them', async () => {
    await show(laptop());
    const layer = expectAboveTheMarks();
    expect(layer.className).toBe('restore-compare is-labels');
    expect((layer as HTMLElement).style.getPropertyValue('--compare-label-top')).toBe(
      (q('[data-testid="restore-compare"]') as HTMLElement).style.getPropertyValue('--compare-label-top')
    );
    expect((layer as HTMLElement).style.maskImage).toBe((q('[data-testid="restore-compare"]') as HTMLElement).style.maskImage);
    const compare = q('[data-testid="restore-compare"]') as HTMLElement;
    act(() => labels()[1]!.click());
    expect(compare.style.getPropertyValue('--split')).toBe('0%');
    act(() => labels()[0]!.click());
    expect(compare.style.getPropertyValue('--split')).toBe('100%');
  });

  it('at phone width, with the phone compare\'s 16 px places', async () => {
    await show(phone, [ready]);
    const layer = expectAboveTheMarks();
    expect(layer.className).toBe('restore-compare is-labels is-phone');
  });

  it('under an open answer and a picked star\'s card, which still stand above the labels', async () => {
    const star = matched.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched')!;
    await show(laptop({ session: { ...matched, selectedFindingId: star.id }, answer: { heard: 'what is that', sentence: 'A star.', answer: null } }));
    const card = q('.reveal-card') ?? q('[data-testid="star-card"]');
    const panel = q('.reveal-answer');
    for (const label of labels()) {
      if (card) expect(after(card, label)).toBe(true);
      if (panel) expect(after(panel, label)).toBe(true);
    }
    expect(card !== null || panel !== null).toBe(true);
  });

  it('lets the press through the layer itself, and gives nothing a z-index', () => {
    const rules: string[] = [];
    postcss.parse(css).walkDecls('z-index', (decl) => {
      const sel = (decl.parent as postcss.Rule).selector ?? '';
      if (/restore-compare|reveal-mark/.test(sel)) rules.push(sel);
    });
    expect(rules).toEqual([]);
    const found: Record<string, string> = {};
    postcss.parse(css).walkRules((rule) => {
      if (rule.selector === '.restore-compare.is-labels' || rule.selector === '.restore-compare.is-labels .restore-compare__label') {
        rule.walkDecls((decl) => {
          found[`${rule.selector} ${decl.prop}`] = decl.value;
        });
      }
    });
    expect(found).toEqual({
      '.restore-compare.is-labels pointer-events': 'none',
      '.restore-compare.is-labels .restore-compare__label pointer-events': 'auto',
    });
  });
});
