import { describe, expect, it } from 'vitest';
import type { Vec3 } from '@albedian/camera-rig';
import { CRAFTS, INSIGHT_ELEVATION_DEG, modelToIcrs } from '../crafts.js';

// is-1 (UIUX-hangarpick-READY.md, ruled 24 Nov 20:31; AUDIT-B's note, LEAD ANSWER 02:14): InSight rests standing on its
// three legs, the model's +Y up the screen, seen from the front quarter at the elevation chosen live (0 degrees), so a
// change to its up axis or its rest elevation fails here.

const apply = (m: number[], v: Vec3): Vec3 => [0, 1, 2].map((i) => m[i * 3]! * v[0] + m[i * 3 + 1]! * v[1] + m[i * 3 + 2]! * v[2]) as Vec3;
const near = (a: readonly number[], b: readonly number[]) => a.forEach((x, i) => expect(x).toBeCloseTo(b[i]!, 12));

describe('InSight at rest', () => {
  const insight = CRAFTS.insight!;

  it('rests at the elevation chosen live: 0 degrees above the front quarter', () => {
    expect(INSIGHT_ELEVATION_DEG).toBe(0);
    const h = Math.hypot(0.55, 1);
    near(insight.modelForward, [0.55 / h, 0, -1 / h]);
  });

  it('stands on its legs: the model\'s +Y is the screen\'s up, and the look has no part along it', () => {
    near(insight.modelUp, [0, 1, 0]);
    expect(insight.modelForward[1]).toBeCloseTo(0, 12);
  });

  it('draws the model\'s +Y along the screen\'s up and its instrument side (+Z) toward the camera', () => {
    const right: Vec3 = [1, 0, 0];
    const up: Vec3 = [0, 0, 1];
    const forward: Vec3 = [0, 1, 0];
    const m = modelToIcrs(insight, right, up, forward);
    near(apply(m, [0, 1, 0]), up);
    // The instrument side faces back along the look, toward the camera
    const instruments = apply(m, [0, 0, 1]);
    expect(instruments[0] * forward[0] + instruments[1] * forward[1] + instruments[2] * forward[2]).toBeLessThan(0);
  });
});
