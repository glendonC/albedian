import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { sourcePhotoDisplayRect } from '../../components/sourcePhotoRect.js';
import { PHONE_SLOT, phoneEndsMask, phoneSlotCrop, phoneSlotRect } from '../phoneSlot.js';

// The reveal at phone width (reveal, approved 2026-11-12, iPhone 1): the photo spans the width at 390 by 520 from y 118,
// covering that slot, its top and bottom edges fading over 40 pt. The slot is written as the crop the photo stage and
// every mark already read, so the marks stay on their pixels.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};

describe('phoneSlotCrop: the photo covering the slot', () => {
  it('puts a 3:4 photo exactly in the slot on a 390 by 844 phone', () => {
    const crop = phoneSlotCrop(3024, 4032, 390, 844);
    const rect = sourcePhotoDisplayRect(3024, 4032, 390, 844, crop);
    expect(rect.leftPx).toBeCloseTo(0, 6);
    expect(rect.widthPx).toBeCloseTo(390, 6);
    expect(rect.topPx).toBeCloseTo(118, 6);
    expect(rect.heightPx).toBeCloseTo(520, 6);
  });

  it('covers the slot for any shape and window, centred on it: no gap inside the slot, never smaller than it', () => {
    fc.assert(
      fc.property(fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 100, max: 8000 }), fc.integer({ min: 320, max: 480 }), fc.integer({ min: 640, max: 960 }), (w, h, viewW, viewH) => {
        const rect = sourcePhotoDisplayRect(w, h, viewW, viewH, phoneSlotCrop(w, h, viewW, viewH));
        const tol = 1e-6;
        expect(rect.leftPx).toBeLessThanOrEqual(tol);
        expect(rect.leftPx + rect.widthPx).toBeGreaterThanOrEqual(viewW - tol);
        expect(rect.topPx).toBeLessThanOrEqual(PHONE_SLOT.topPx + tol);
        expect(rect.topPx + rect.heightPx).toBeGreaterThanOrEqual(PHONE_SLOT.topPx + PHONE_SLOT.heightPx - tol);
        // One side fits exactly: the photo is as small as covering allows
        const fitsWidth = Math.abs(rect.widthPx - viewW) < 1e-6;
        const fitsHeight = Math.abs(rect.heightPx - PHONE_SLOT.heightPx) < 1e-6;
        expect(fitsWidth || fitsHeight).toBe(true);
        expect(rect.leftPx + rect.widthPx / 2).toBeCloseTo(viewW / 2, 6);
        expect(rect.topPx + rect.heightPx / 2).toBeCloseTo(PHONE_SLOT.topPx + PHONE_SLOT.heightPx / 2, 6);
      }),
      SEED
    );
  });
});

describe('phoneEndsMask: the slot\'s top and bottom fading, nothing outside it', () => {
  it('fades over 40 px inside the slot and hides the photo beyond it, measured in the photo\'s own box', () => {
    // A 9:16 photo covering the slot overflows it above and below
    const crop = phoneSlotCrop(1080, 1920, 390, 844);
    const rect = sourcePhotoDisplayRect(1080, 1920, 390, 844, crop);
    const top = PHONE_SLOT.topPx - rect.topPx;
    const bottom = top + PHONE_SLOT.heightPx;
    const r = (n: number) => Math.round(n * 10) / 10;
    expect(phoneEndsMask(rect)).toBe(
      `linear-gradient(to bottom, transparent ${r(top)}px, #000 ${r(top + 40)}px, #000 ${r(bottom - 40)}px, transparent ${r(bottom)}px)`
    );
    expect(phoneEndsMask(sourcePhotoDisplayRect(3024, 4032, 390, 844, phoneSlotCrop(3024, 4032, 390, 844)))).toBe(
      'linear-gradient(to bottom, transparent 0px, #000 40px, #000 480px, transparent 520px)'
    );
  });
});

describe('phoneSlotRect: the part of the photo the slot shows', () => {
  it('is the slot for a photo covering it, wide or tall, and the photo itself where it is smaller', () => {
    expect(phoneSlotRect({ leftPx: -249, topPx: 118, widthPx: 888, heightPx: 520 }, 390)).toEqual({ leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 });
    expect(phoneSlotRect({ leftPx: 0, topPx: 40, widthPx: 390, heightPx: 693 }, 390)).toEqual({ leftPx: 0, topPx: 118, widthPx: 390, heightPx: 520 });
    expect(phoneSlotRect({ leftPx: 20, topPx: 130, widthPx: 300, heightPx: 400 }, 390)).toEqual({ leftPx: 20, topPx: 130, widthPx: 300, heightPx: 400 });
  });
});
