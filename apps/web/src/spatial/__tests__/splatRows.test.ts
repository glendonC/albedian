import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { parseSplatRows } from '../splatRows';

const repoFile = (rel: string) => fileURLToPath(new URL(`../../../../../${rel}`, import.meta.url));
const readRows = (rel: string): number[][] => JSON.parse(fs.readFileSync(repoFile(rel), 'utf8'));

describe('splat rows', () => {
  it('draws every row of apps/web/public/models/lb_splats.json at its own position', () => {
    const [scale, rowCount] = [1.24, 27648];
    const raw = readRows('apps/web/public/models/lb_splats.json');
    const rows = parseSplatRows(raw, scale, 0.005);
    expect(raw).toHaveLength(rowCount);
    expect(rows.count).toBe(rowCount);
    let maxOffset = 0;
    raw.forEach((row, i) => {
      for (let k = 0; k < 3; k++) {
        maxOffset = Math.max(maxOffset, Math.abs(rows.centers[i * 3 + k]! - row[k]! * scale));
      }
    });
    // Float32 rounding only: no jitter, thinning or cut moves or removes a splat
    expect(maxOffset).toBeLessThan(1e-5);
  });

  it('skips incomplete rows instead of filling in their attributes', () => {
    const full = [1, 2, 3, 0.5, 0.5, 0.5, 0.2, 0.1, 0.05, 0, 0, 0, 2, 0.01];
    const rows = parseSplatRows(
      [full, full.slice(0, 6), [...full.slice(0, 13), Number.NaN], null, 'row'],
      2,
      0.01
    );
    expect(rows.count).toBe(1);
    expect(Array.from(rows.centers)).toEqual([2, 4, 6]);
    expect(Array.from(rows.quaternions)).toEqual([0, 0, 0, 1]);
    expect(rows.opacities[0]).toBeCloseTo(0.01, 6);
    expect(parseSplatRows(undefined, 1, 0.01).count).toBe(0);
  });
});
