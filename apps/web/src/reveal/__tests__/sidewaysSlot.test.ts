import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { PHONE_SLOT, phoneSlotCrop, phoneSlotIn } from '../phoneSlot.js';

// phone-sideways-two (UIUX-phone-sideways-READY.md item 2, RULED at the lead's line stamped 06:27): a phone on its side
// (a touch window under 500 CSS px tall, which the phone's layout already takes) shows the photo whole, fit to the
// window's height, centred, the wider window the photo's gain; MOBILE's Safari window is 874 by 292 with its bars and
// 874 by 402 once they collapse. Upright windows keep their slot: nothing moves at the upright sizes.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};

describe('the photo on a phone held on its side', () => {
  it('takes the whole window as its slot under 500 px tall, and keeps the upright slot above', () => {
    for (const h of [292, 320, 370, 402, 500]) expect(phoneSlotIn(h), String(h)).toEqual({ topPx: 0, heightPx: h });
    expect(phoneSlotIn(844)).toEqual(PHONE_SLOT);
    expect(phoneSlotIn(650)).toEqual({ topPx: 118, heightPx: 326 });
    expect(phoneSlotIn(501)).toEqual({ topPx: 118, heightPx: 177 });
  });

  it('shows a 3:2 photo whole at Safari\'s 874 by 292, fit to the height and centred', () => {
    const rect = sourcePhotoDisplayRect(3000, 2000, 874, 292, phoneSlotCrop(3000, 2000, 874, 292));
    expect(rect.heightPx).toBeCloseTo(292, 6);
    expect(rect.widthPx).toBeCloseTo(438, 6);
    expect(rect.topPx).toBeCloseTo(0, 6);
    expect(rect.leftPx).toBeCloseTo((874 - 438) / 2, 6);
  });

  it('shows any photo whole in any short window: inside it, one side fitting it exactly, centred', () => {
    fc.assert(
      fc.property(fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 560, max: 960 }), fc.integer({ min: 280, max: 500 }), (w, h, viewW, viewH) => {
        const rect = sourcePhotoDisplayRect(w, h, viewW, viewH, phoneSlotCrop(w, h, viewW, viewH));
        const tol = 1e-6;
        expect(rect.leftPx).toBeGreaterThanOrEqual(-tol);
        expect(rect.topPx).toBeGreaterThanOrEqual(-tol);
        expect(rect.leftPx + rect.widthPx).toBeLessThanOrEqual(viewW + tol);
        expect(rect.topPx + rect.heightPx).toBeLessThanOrEqual(viewH + tol);
        expect(Math.abs(rect.widthPx - viewW) < 1e-6 || Math.abs(rect.heightPx - viewH) < 1e-6).toBe(true);
        expect(rect.leftPx + rect.widthPx / 2).toBeCloseTo(viewW / 2, 6);
        expect(rect.topPx + rect.heightPx / 2).toBeCloseTo(viewH / 2, 6);
      }),
      SEED
    );
  });
});
