import { PARSEC_TO_LIGHT_YEAR } from '@albedian/evidence';

/**
 * Where a flight toward a star ends, in ICRS parsecs from the Sun: on the straight line from the
 * camera's start to the star, one light year short of it, so the star is near and still ahead.
 */

type Vec3 = readonly [number, number, number];

/** One light year, in parsecs. */
export const FLIGHT_STANDOFF_PC = 1 / PARSEC_TO_LIGHT_YEAR;

export function distancePc(a: Vec3, b: Vec3): number {
  return Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
}

const THREE_FIGURES = new Intl.NumberFormat('en-US', {
  minimumSignificantDigits: 3,
  maximumSignificantDigits: 3,
  useGrouping: false,
  notation: 'standard',
});

/**
 * A distance to three significant figures, as a catalog distance deserves: 548, 25.2, 1.00, 0.500.
 * Never in exponent form: 1234 reads 1230, 999.5 reads 1000, 9.99e-7 reads 0.000000999.
 */
export function threeFigures(x: number): string {
  if (!Number.isFinite(x)) return 'unknown';
  if (x === 0) return '0.00';
  return THREE_FIGURES.format(x);
}

/** The flight's end: `standoffPc` short of `target` toward `from`, or `from` itself when already that close. */
export function flightEndOrigin(from: Vec3, target: Vec3, standoffPc = FLIGHT_STANDOFF_PC): [number, number, number] {
  const d = distancePc(from, target);
  if (d <= standoffPc) return [from[0], from[1], from[2]];
  const f = (d - standoffPc) / d;
  return [from[0] + (target[0] - from[0]) * f, from[1] + (target[1] - from[1]) * f, from[2] + (target[2] - from[2]) * f];
}
