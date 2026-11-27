import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  CARD_MARK_CLEAR_PX,
  PICKED_MARK_RADIUS_PX,
  SIDEWAYS_CARD_ROOM_PX,
  SIDEWAYS_CARD_WIDTH_PX,
  cardMarkShift,
  coveredBySidewaysCard,
  sidewaysCardBox,
} from '../cardMark.js';

const seed = Number(process.env.ALBEDIAN_FC_SEED ?? 1);

// phone-sideways-three: UIUX-phone-sideways-READY.md item 5's card-mark rule (RULED at the lead's line stamped 06:27) and
// UIUX-card-mark-clear-READY.md: the photo slides on the card's entrance so the picked mark stands 16 px clear of the
// card, by its top or its side, whichever move is shorter
describe('phone-sideways-three: the card-mark rule on a phone held on its side', () => {
  it('keeps the record numbers: 16 px clear, the picked halo of 22, the sheet 390 wide under a room of 64', () => {
    expect(CARD_MARK_CLEAR_PX).toBe(16);
    expect(PICKED_MARK_RADIUS_PX).toBe(22);
    expect(SIDEWAYS_CARD_WIDTH_PX).toBe(390);
    expect(SIDEWAYS_CARD_ROOM_PX).toBe(64);
  });

  it('stands the sheet centred on the foot, as tall as its content up to the window less 64', () => {
    expect(sidewaysCardBox(874, 292, 228)).toEqual({ leftPx: 242, rightPx: 632, topPx: 64 });
    expect(sidewaysCardBox(874, 402, 500)).toEqual({ leftPx: 242, rightPx: 632, topPx: 64 });
    expect(sidewaysCardBox(667, 320, 200)).toEqual({ leftPx: 138.5, rightPx: 528.5, topPx: 120 });
  });

  it('moves nothing when the mark already stands 16 px clear above the card or beside it', () => {
    const card = sidewaysCardBox(874, 292, 228);
    // Above: the halo's foot at 48 = 64 - 16
    expect(cardMarkShift({ xPx: 437, yPx: 26 }, card)).toEqual({ xPx: 0, yPx: 0 });
    // Beside: the halo's edge 16 px from the card's leading or trailing side
    expect(cardMarkShift({ xPx: 204, yPx: 200 }, card)).toEqual({ xPx: 0, yPx: 0 });
    expect(cardMarkShift({ xPx: 670, yPx: 200 }, card)).toEqual({ xPx: 0, yPx: 0 });
  });

  it('slides up by its top when that move is the shortest', () => {
    const card = sidewaysCardBox(874, 292, 228);
    // up 60 + 22 - 48 = 34; left 437 + 22 - 226 = 233; right 648 - 415 = 233
    expect(cardMarkShift({ xPx: 437, yPx: 60 }, card)).toEqual({ xPx: 0, yPx: -34 });
    // A card standing lower leaves more room above: up 200 + 22 - (180 - 16) = 58
    expect(cardMarkShift({ xPx: 437, yPx: 200 }, sidewaysCardBox(874, 292, 112))).toEqual({ xPx: 0, yPx: -58 });
  });

  it('slides to the leading side or the trailing side when that move is the shortest', () => {
    const card = sidewaysCardBox(874, 292, 228);
    // left 291 + 22 - 226 = 87; up 277 + 22 - 48 = 251; right 648 - 269 = 379
    expect(cardMarkShift({ xPx: 291, yPx: 277 }, card)).toEqual({ xPx: -87, yPx: 0 });
    // right 648 - (600 - 22) = 70; left 600 + 22 - 226 = 396; up 251
    expect(cardMarkShift({ xPx: 600, yPx: 277 }, card)).toEqual({ xPx: 70, yPx: 0 });
  });

  it('prefers the top on a tie with a side, and the leading side on a tie between the sides', () => {
    const card = sidewaysCardBox(874, 292, 228);
    // up 113 + 22 - 48 = 87 = left 291 + 22 - 226
    expect(cardMarkShift({ xPx: 291, yPx: 113 }, card)).toEqual({ xPx: 0, yPx: -87 });
    // centred: left 233 = right 233, up 251
    expect(cardMarkShift({ xPx: 437, yPx: 277 }, card)).toEqual({ xPx: -233, yPx: 0 });
  });

  it('covers a point only inside the sheet: under its top and between its sides', () => {
    const card = sidewaysCardBox(874, 292, 228);
    expect(coveredBySidewaysCard({ xPx: 437, yPx: 65 }, card)).toBe(true);
    expect(coveredBySidewaysCard({ xPx: 437, yPx: 64 }, card)).toBe(false);
    expect(coveredBySidewaysCard({ xPx: 241, yPx: 200 }, card)).toBe(false);
    expect(coveredBySidewaysCard({ xPx: 243, yPx: 200 }, card)).toBe(true);
    expect(coveredBySidewaysCard({ xPx: 631, yPx: 200 }, card)).toBe(true);
    expect(coveredBySidewaysCard({ xPx: 633, yPx: 200 }, card)).toBe(false);
  });

  it('Property: after the move the mark stands at least 16 px clear, by the shortest of the three moves, one axis only', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 667, max: 1000 }),
        fc.integer({ min: 240, max: 500 }),
        fc.integer({ min: 40, max: 700 }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.double({ min: 0, max: 1, noNaN: true }),
        (w, h, content, fx, fy) => {
          const card = sidewaysCardBox(w, h, content);
          const mark = { xPx: fx * w, yPx: fy * h };
          const shift = cardMarkShift(mark, card);
          const moved = { xPx: mark.xPx + shift.xPx, yPx: mark.yPx + shift.yPx };
          const r = PICKED_MARK_RADIUS_PX;
          const clearAbove = moved.yPx + r <= card.topPx - CARD_MARK_CLEAR_PX + 1e-9;
          const clearLeading = moved.xPx + r <= card.leftPx - CARD_MARK_CLEAR_PX + 1e-9;
          const clearTrailing = moved.xPx - r >= card.rightPx + CARD_MARK_CLEAR_PX - 1e-9;
          expect(clearAbove || clearLeading || clearTrailing).toBe(true);
          expect(shift.xPx === 0 || shift.yPx === 0).toBe(true);
          expect(shift.yPx).toBeLessThanOrEqual(0);
          const up = Math.max(0, mark.yPx + r - (card.topPx - CARD_MARK_CLEAR_PX));
          const left = Math.max(0, mark.xPx + r - (card.leftPx - CARD_MARK_CLEAR_PX));
          const right = Math.max(0, card.rightPx + CARD_MARK_CLEAR_PX - (mark.xPx - r));
          const shortest = Math.min(up, left, right);
          expect(Math.abs(shift.xPx) + Math.abs(shift.yPx)).toBeCloseTo(shortest, 9);
        }
      ),
      { seed, numRuns: 300 }
    );
  });
});
