import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { FLIGHT_STANDOFF_PC, distancePc, flightEndOrigin, threeFigures } from '../flightTarget.js';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const DEG = Math.PI / 180;
const at = (raDeg: number, decDeg: number, pc: number): [number, number, number] => [
  pc * Math.cos(decDeg * DEG) * Math.cos(raDeg * DEG),
  pc * Math.cos(decDeg * DEG) * Math.sin(raDeg * DEG),
  pc * Math.sin(decDeg * DEG),
];

/**
 * Tabit (pi3 Orionis) as the app's catalog carries it (services/science/catalog_orion.py): Gaia DR3
 * 3288921720025503360 at RA 72.460047, Dec 6.961275, Bailer-Jones median distance 8.02372 pc, or
 * 26.17 light years. The old flight stopped at 0.45 of that (3.61 pc, 11.78 ly from Earth), 4.41 pc
 * short of the star, and said it had arrived.
 */
const TABIT = at(72.460047, 6.961275, 8.02372);
const EARTH: [number, number, number] = [0, 0, 0];

const vec = fc.tuple(
  fc.double({ min: -500, max: 500, noNaN: true }),
  fc.double({ min: -500, max: 500, noNaN: true }),
  fc.double({ min: -500, max: 500, noNaN: true })
);

describe('a flight to a star ends one light year short of it', () => {
  it('is one light year', () => {
    expect(FLIGHT_STANDOFF_PC * PARSEC_TO_LIGHT_YEAR).toBeCloseTo(1, 12);
  });

  it('flies to Tabit and stops one light year short, 25.17 ly from Earth', () => {
    const end = flightEndOrigin(EARTH, TABIT);
    expect(distancePc(end, TABIT) * PARSEC_TO_LIGHT_YEAR).toBeCloseTo(1, 9);
    expect(distancePc(EARTH, end) * PARSEC_TO_LIGHT_YEAR).toBeCloseTo(26.17 - 1, 2);
  });

  it('ends on the line from where it starts, one light year short, whatever the start and target', () => {
    fc.assert(
      fc.property(vec, vec, (from, target) => {
        const d = distancePc(from, target);
        fc.pre(d > FLIGHT_STANDOFF_PC * 1.001);
        const end = flightEndOrigin(from, target);
        expect(Math.abs(distancePc(end, target) - FLIGHT_STANDOFF_PC)).toBeLessThan(1e-9 * Math.max(1, d));
        // On the segment: the two legs add up to the whole
        expect(Math.abs(distancePc(from, end) + distancePc(end, target) - d)).toBeLessThan(1e-9 * Math.max(1, d));
      }),
      { numRuns: 300, seed: SEED, examples: [[EARTH, TABIT]] }
    );
  });

  it('stays put when it starts within a light year of the target, rather than backing away', () => {
    fc.assert(
      fc.property(vec, fc.double({ min: 0, max: 0.99, noNaN: true }), (from, fraction) => {
        const target: [number, number, number] = [from[0] + FLIGHT_STANDOFF_PC * fraction, from[1], from[2]];
        expect(flightEndOrigin(from, target)).toEqual([from[0], from[1], from[2]]);
      }),
      { numRuns: 100, seed: SEED }
    );
  });

  it('writes distances to three significant figures, never in exponent form', () => {
    const cases: Array<[number, string]> = [
      [547.16, '547'], [25.17, '25.2'], [1, '1.00'], [0.5, '0.500'], [8.598, '8.60'], [1234, '1230'], [0, '0.00'],
      // Where toPrecision switches to exponents
      [999.5, '1000'], [999.96, '1000'], [9.99e-7, '0.000000999'], [4.06e-8, '0.0000000406'],
    ];
    for (const [x, text] of cases) expect(threeFigures(x), String(x)).toBe(text);
    expect(threeFigures(Number.NaN)).toBe('unknown');
  });

  it('never writes an exponent, at any size', () => {
    fc.assert(
      fc.property(fc.double({ min: -12, max: 12, noNaN: true }), fc.boolean(), (log10, negative) => {
        const x = (negative ? -1 : 1) * 10 ** log10;
        expect(threeFigures(x)).toMatch(/^-?\d+(\.\d+)?$/);
      }),
      { numRuns: 300, seed: SEED, examples: [[Math.log10(999.5), false], [Math.log10(9.99e-7), false], [-7.39, false]] }
    );
  });
});
