import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { ARRIVAL_REST_SHARE } from '../arrival.js';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { frameShiftAt, handoverShiftError, restPointPx, screenThrough } from '../heldStar.js';
import { flightCameraOf } from '../flightFrame.js';
import { steerStartOf } from '../starView.js';

// phone-star-open-one (LEAD ANSWER 26 Nov 23:14, on LOOK's finding of 25 Nov 08:00): the star flown to stands in the
// open part of the frame, never under the readout's words. It rests where the arrival record rules (ARRIVAL_REST_SHARE,
// UIUX-arrivalstar-READY.md, DECIDED-BY-LEAD 15:34) and the steering's hand-over keeps it there: while the heading turns
// onto the star, the view's frame on the window moves with the turn, so the star holds its place and the sky turns
// about it; after the turn the heading stands at the resting place, not wherever the solve's reference pixel falls.

const seed = Number(process.env.ALBEDIAN_FC_SEED ?? 1);

describe('where the star flown to rests', () => {
  it('is the arrival record\'s share of the window at each size', () => {
    expect(restPointPx(390, 844, true)).toEqual([ARRIVAL_REST_SHARE.phone[0] * 390, ARRIVAL_REST_SHARE.phone[1] * 844]);
    expect(restPointPx(1440, 900, false)).toEqual([ARRIVAL_REST_SHARE.laptop[0] * 1440, ARRIVAL_REST_SHARE.laptop[1] * 900]);
  });
});

describe('the frame\'s shift through the hand-over', () => {
  const rest: [number, number] = [225, 364.6];

  it('is the arrival\'s own shift on the hand-over\'s first frame, so nothing jumps when steering starts', () => {
    // The star drawn at rest through the photo's axes lands a hair off the resting place (Go there's rounding)
    const starAtStart: [number, number] = [97.3, 502.1];
    const full: [number, number] = [127.9, -137.2];
    const error = handoverShiftError(full, rest, starAtStart);
    const s = frameShiftAt({ restPx: rest, starPx: starAtStart, errorPx: error, share: 0, fallbackPx: [0, 0] });
    expect(s[0]).toBeCloseTo(full[0], 9);
    expect(s[1]).toBeCloseTo(full[1], 9);
  });

  it('holds the star at its resting place once the turn is made, whatever the start\'s hair', () => {
    const error = handoverShiftError([127.9, -137.2], rest, [97.3, 502.1]);
    // At the turn's end the heading is the star: drawn without a shift it lands at the reference pixel's place
    const onAxis: [number, number] = [143.4, 388.0];
    const s = frameShiftAt({ restPx: rest, starPx: onAxis, errorPx: error, share: 1, fallbackPx: [0, 0] });
    expect(onAxis[0] + s[0]).toBeCloseTo(rest[0], 12);
    expect(onAxis[1] + s[1]).toBeCloseTo(rest[1], 12);
  });

  it('keeps the star where it rested plus the start\'s hair fading with the turn, at every share', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1, noNaN: true }),
        fc.tuple(fc.double({ min: -2000, max: 2000, noNaN: true }), fc.double({ min: -2000, max: 2000, noNaN: true })),
        fc.tuple(fc.double({ min: -3, max: 3, noNaN: true }), fc.double({ min: -3, max: 3, noNaN: true })),
        (share, star, hair) => {
          const s = frameShiftAt({ restPx: rest, starPx: star, errorPx: hair, share, fallbackPx: [0, 0] });
          expect(star[0] + s[0]).toBeCloseTo(rest[0] + hair[0] * (1 - share), 9);
          expect(star[1] + s[1]).toBeCloseTo(rest[1] + hair[1] * (1 - share), 9);
        }
      ),
      { seed, numRuns: 200 }
    );
  });

  it('takes a share past either end as the end itself', () => {
    const at = (share: number) => frameShiftAt({ restPx: rest, starPx: [100, 200], errorPx: [4, -6], share, fallbackPx: [0, 0] });
    expect(at(1.5)).toEqual(at(1));
    expect(at(-0.5)).toEqual(at(0));
  });

  it('stays as it was when there is no star to hold (none flown to, or behind the camera)', () => {
    expect(frameShiftAt({ restPx: rest, starPx: null, errorPx: [1, 1], share: 0.5, fallbackPx: [12, -7] })).toEqual([12, -7]);
  });

  it('sees a point in front of the camera where the flight\'s own projection does, and none behind it', () => {
    const camera = flightCameraOf(createSyntheticCalibratedSession(), 390, 844)!;
    const start = steerStartOf(camera);
    const ahead: [number, number, number] = [start.positionPc[0] + start.rows.z[0] * 10, start.positionPc[1] + start.rows.z[1] * 10, start.positionPc[2] + start.rows.z[2] * 10];
    const behind: [number, number, number] = [start.positionPc[0] - start.rows.z[0] * 10, start.positionPc[1] - start.rows.z[1] * 10, start.positionPc[2] - start.rows.z[2] * 10];
    const seen = screenThrough(camera, start.positionPc, start.rows, ahead)!;
    // On the axis: the reference pixel, through the photo's frame on the window
    const m = camera.storedToScreen;
    expect(seen[0]).toBeCloseTo(m[0] * camera.wcs.crpixX + m[1] * camera.wcs.crpixY + m[2], 6);
    expect(seen[1]).toBeCloseTo(m[3] * camera.wcs.crpixX + m[4] * camera.wcs.crpixY + m[5], 6);
    expect(screenThrough(camera, start.positionPc, start.rows, behind)).toBeNull();
  });

  it('measures the start\'s hair as the arrival\'s shift less the shift that would put the star exactly at rest', () => {
    expect(handoverShiftError([10, 20], [100, 200], [91, 181])).toEqual([1, 1]);
  });
});
