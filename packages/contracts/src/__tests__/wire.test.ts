import { describe, expect, it } from 'vitest';
import { z, type ZodTypeAny } from 'zod';
import { wireInt, wireNumber } from '../wire.js';

/**
 * The wire module's numbers read minus zero as zero, as services/contracts/wire.py's WireNumber and WireInt do
 * (WIRE_RULES.md rule 2), through every check a contract chains on them: zod builds each chained number schema anew,
 * so a check that dropped the reading would keep minus zero.
 */
const CHAINS: [string, () => ZodTypeAny][] = [
  ['wireNumber()', () => wireNumber()],
  ['wireNumber().min(0)', () => wireNumber().min(0)],
  ['wireNumber().gte(-1).lte(1)', () => wireNumber().gte(-1).lte(1)],
  ['wireNumber().max(0)', () => wireNumber().max(0)],
  ['wireNumber().gt(-1).lt(1)', () => wireNumber().gt(-1).lt(1)],
  ['wireNumber().nonnegative()', () => wireNumber().nonnegative()],
  ['wireNumber().nonpositive()', () => wireNumber().nonpositive()],
  ['wireNumber().multipleOf(0.5)', () => wireNumber().multipleOf(0.5)],
  ['wireNumber().step(0.25)', () => wireNumber().step(0.25)],
  ['wireNumber().finite()', () => wireNumber().finite()],
  ['wireNumber().int()', () => wireNumber().int()],
  ['wireNumber().safe()', () => wireNumber().safe()],
  ['wireNumber().describe("x")', () => wireNumber().describe('x')],
  ['wireNumber().optional()', () => wireNumber().optional()],
  ['wireInt()', () => wireInt()],
  ['wireInt().nonnegative()', () => wireInt().nonnegative()],
  ['wireInt().min(0).max(3)', () => wireInt().min(0).max(3)],
];

describe('the wire module reads minus zero as zero', () => {
  it.each(CHAINS)('%s', (_, schema) => {
    const read = schema().safeParse(JSON.parse('-0.0'));
    expect(read.success).toBe(true);
    expect(Object.is(read.success ? read.data : undefined, 0)).toBe(true);
    for (const value of [0, 1, 0.5]) {
      const kept = schema().safeParse(value);
      if (kept.success) expect(Object.is(kept.data, value)).toBe(true);
    }
  });

  it('keeps every other reading of a check, at its edges', () => {
    expect(wireNumber().positive().safeParse(-0).success).toBe(false);
    expect(wireNumber().negative().safeParse(-0).success).toBe(false);
    expect(wireNumber().min(0).safeParse(-0.5).success).toBe(false);
    expect(wireNumber().lt(0).safeParse(-0).success).toBe(false);
    expect(wireNumber().safeParse(Infinity).success).toBe(false);
    expect(wireNumber().safeParse(true).success).toBe(false);
    expect(wireInt().safeParse(2 ** 53).success).toBe(false);
    expect(wireInt().safeParse(1.5).success).toBe(false);
    expect(wireNumber().optional().safeParse(undefined)).toEqual({ success: true, data: undefined });
  });

  it('keeps the path of a refusal it reads minus zero for', () => {
    const read = z.object({ a: z.array(wireNumber().positive()) }).strict().safeParse({ a: [1, -0] });
    expect(read.success ? [] : read.error.issues.map((issue) => issue.path)).toEqual([['a', 1]]);
  });

  it('reads minus zero as zero inside a record and a list', () => {
    const read = z.object({ a: wireNumber(), b: z.array(wireInt()) }).strict().safeParse(JSON.parse('{"a": -0.0, "b": [-0.0, 2]}'));
    expect(read.success && Object.is(read.data.a, 0) && Object.is(read.data.b[0], 0)).toBe(true);
  });
});
