// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { LANDSCAPE_PHONE_QUERY, SIDEWAYS_QUERY } from '../../components/phoneSheet.js';
import { isSideways, phoneSlotCrop, phoneSlotIn } from '../phoneSlot.js';
import { RevealPhoto } from '../RevealPhoto.js';

vi.mock('../RealSkyLayer.js', async (importOriginal) => {
  const original = await importOriginal<typeof import('../RealSkyLayer.js')>();
  return { ...original, RealSkyLayer: () => null };
});
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// sideways-landscape-guard-one (AUDIT-C's note upright-short-phone on main 96566d28; DECIDED-BY-LEAD 03:19 on 27 Nov): the
// sideways rules apply only to a touch window both under 500 tall and wider than it is tall; an upright window of any height
// (a small iPhone upright in Safari, an Android phone with its keyboard open: 390 by 480, 360 by 400) keeps the upright
// phone's layout, where the predicted sky's card sheet stood 12 and 42 px past the left edge

const here = path.dirname(fileURLToPath(import.meta.url));
const css = fs.readFileSync(path.resolve(here, '../../styles/reveal.css'), 'utf8');

describe('sideways-landscape-guard-one: the sideways layout only on a short window wider than tall', () => {
  it('guards the sideways query with the landscape orientation, the phone query itself unchanged', () => {
    expect(SIDEWAYS_QUERY).toBe('(max-height: 500px) and (pointer: coarse) and (orientation: landscape)');
    expect(LANDSCAPE_PHONE_QUERY).toBe('(max-height: 500px) and (pointer: coarse)');
  });

  it('stands every sideways block of reveal.css under the guarded query', () => {
    const params: string[] = [];
    postcss.parse(css).walkAtRules('media', (at) => {
      if (at.params.includes('max-height: 500px')) params.push(at.params);
    });
    expect(params.length).toBeGreaterThan(0);
    for (const p of params) expect(p === SIDEWAYS_QUERY || p.startsWith(`${SIDEWAYS_QUERY} and `), p).toBe(true);
  });

  it('reads a phone as on its side only when its window is 500 tall or less and wider than tall', () => {
    for (const [w, h] of [[874, 292], [874, 402], [667, 320], [401, 400], [874, 500]] as const) expect(isSideways(w, h), `${w}x${h}`).toBe(true);
    for (const [w, h] of [[390, 480], [360, 400], [400, 400], [320, 460], [874, 501], [390, 844], [402, 650]] as const) expect(isSideways(w, h), `${w}x${h}`).toBe(false);
  });

  it('covers the upright slot with the photo at the upright short sizes, where it was drawn whole as if sideways', () => {
    for (const [w, h] of [[390, 480], [360, 400]] as const) {
      const slot = phoneSlotIn(h, w);
      expect(slot.topPx, `${w}x${h}`).toBe(118);
      const rect = sourcePhotoDisplayRect(3024, 4032, w, h, phoneSlotCrop(3024, 4032, w, h));
      // Covering: the photo spans the window's width (an upright photo, taller than the slot)
      expect(rect.widthPx, `${w}x${h}`).toBeCloseTo(w, 6);
      expect(rect.topPx, `${w}x${h}`).toBeLessThanOrEqual(slot.topPx + 1e-6);
    }
    // Sideways, whole and fit to the height
    const whole = sourcePhotoDisplayRect(3024, 4032, 874, 292, phoneSlotCrop(3024, 4032, 874, 292));
    expect(whole.heightPx).toBeCloseTo(292, 6);
  });

  it('keeps the card-mark slide off on the upright short sizes and on at the sideways ones', () => {
    const base = poseSession(sample('plate_calibrated') as ObservationSolution);
    const rigel = base.observationSolution!.findings.find((f) => f.title === 'Rigel')!;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ variants: [], attempts: [] }), { status: 200 }));
    const host = document.createElement('div');
    document.body.appendChild(host);
    try {
      for (const [w, h, sideways] of [[390, 480, false], [360, 400, false], [874, 292, true], [874, 402, true], [667, 320, true]] as const) {
        const root = createRoot(host);
        const session = { ...base, selectedFindingId: rigel.id, cropTransform: { ...base.cropTransform, ...phoneSlotCrop(1920, 1080, w, h) } } as ObservationSession;
        act(() => root.render(createElement(RevealPhoto, { session, onSelectFinding: () => {}, onHome: () => {}, viewportWidthPx: w, viewportHeightPx: h, phone: true, restoration: { phase: 'idle' } })));
        expect(document.documentElement.dataset.cardMark !== undefined, `${w}x${h}`).toBe(sideways);
        act(() => root.unmount());
      }
    } finally {
      host.remove();
      vi.restoreAllMocks();
    }
  });
});
