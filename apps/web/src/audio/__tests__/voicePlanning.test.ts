import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { LOUDNESS, REGIME_DISTANCE_WINDOWS, STAR_DISTANCE_WINDOW, type ScaleAudioRegime } from '../mappings';
import {
  IDENTITY_PITCHES_HZ,
  IDENTITY_QUAT,
  assignIdentityPitches,
  directionFromAzimuthElevation,
  distanceWindowFor,
  planVoice,
  rotateByQuat,
  selectVoices,
  worldToListenerDirection,
  yawQuat,
  type AudioSourceInput,
  type PlanContext,
  type Quat,
  type Vec3,
  type VoicePlan,
} from '../voicePlanning';

const context: PlanContext = {
  distanceWindow: REGIME_DISTANCE_WINDOWS.solar_neighborhood,
  listenerOrientation: IDENTITY_QUAT,
};

const front: Vec3 = [0, 0, -1];

/** A star bright and near enough to be audible: Sirius-like numbers for convenience. */
const audibleStar: AudioSourceInput = {
  id: 'star-a',
  kind: 'star',
  directionWorld: front,
  distancePc: 2.64,
  luminositySolar: 25.4,
  teffK: 9940,
};

const crab: AudioSourceInput = {
  id: 'psr-j0534+2200',
  kind: 'pulsar',
  directionWorld: [0.3, 0.1, -0.9],
  distancePc: 2000,
  luminositySolar: 4.5e38 / 3.828e33,
  spinFrequencyHz: 29.946923,
};

const missing = fc.constantFrom<number | null | undefined>(undefined, null, Number.NaN, Infinity, -1, 0);

const unitVec = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter(([x, y, z]) => Math.hypot(x, y, z) > 1e-3)
  .map(([x, y, z]): Vec3 => {
    const n = Math.hypot(x, y, z);
    return [x / n, y / n, z / n];
  });

const unitQuat = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter((q) => Math.hypot(...q) > 1e-3)
  .map((q): Quat => {
    const n = Math.hypot(...q);
    return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
  });

function plan(id: string, levelDb: number, isTarget = false): VoicePlan {
  return {
    id,
    kind: 'star',
    isTarget,
    evidenceClass: null,
    levelDb,
    perceptualDistance: 0.5,
    drrDb: 0,
    directGain: Math.SQRT1_2,
    reverbGain: Math.SQRT1_2,
    distanceLowpassHz: 8000,
    dustLowpassHz: null,
    directionListener: front,
    star: { teffK: 5772, planckScaleHz: 261 },
    pulsar: null,
    uncertainty: null,
  };
}

describe('Property: voice planning stays silent on missing input', () => {
  it('plans an audible voice when every core input is present', () => {
    const outcome = planVoice(audibleStar, context);
    expect(outcome.status).toBe('audible');
    if (outcome.status !== 'audible') return;
    expect(outcome.plan.levelDb).toBeLessThanOrEqual(LOUDNESS.ceilingDb);
    expect(outcome.plan.star?.teffK).toBe(9940);
    expect(outcome.plan.dustLowpassHz).toBeNull();
    expect(outcome.plan.uncertainty).toBeNull();
  });

  it('silences a star when any core input is missing, and names exactly what is missing', () => {
    const coreKeys = ['directionWorld', 'distancePc', 'luminositySolar', 'teffK'] as const;
    fc.assert(
      fc.property(
        fc.subarray([...coreKeys], { minLength: 1 }),
        missing,
        (keys, bad) => {
          const input: Record<string, unknown> = { ...audibleStar };
          for (const key of keys) input[key] = key === 'directionWorld' ? (bad === 0 ? [0, 0, 0] : null) : bad;
          const outcome = planVoice(input as unknown as AudioSourceInput, context);
          expect(outcome.status).toBe('silent');
          if (outcome.status !== 'silent') return;
          expect(outcome.reason).toBe('missing_input');
          expect([...outcome.missing].sort()).toEqual([...keys].sort());
        }
      )
    );
  });

  it('silences a pulsar without a valid spin frequency and never borrows a star timbre', () => {
    fc.assert(
      fc.property(missing, (bad) => {
        const outcome = planVoice({ ...crab, spinFrequencyHz: bad }, context);
        expect(outcome.status).toBe('silent');
        if (outcome.status === 'silent') expect(outcome.missing).toEqual(['spinFrequencyHz']);
      })
    );
  });

  it('plays the Crab at its catalogue spin frequency, unscaled', () => {
    const outcome = planVoice(crab, { ...context, distanceWindow: REGIME_DISTANCE_WINDOWS.deep_sky });
    expect(outcome.status).toBe('audible');
    if (outcome.status === 'audible') {
      expect(outcome.plan.pulsar?.pulseRateHz).toBe(29.946923);
      expect(outcome.plan.star).toBeNull();
    }
  });

  it('adds optional layers only when their own input exists', () => {
    fc.assert(
      fc.property(
        fc.option(fc.double({ min: 0, max: 30, noNaN: true }), { nil: undefined }),
        fc.option(fc.double({ min: 0, max: 600, noNaN: true }), { nil: undefined }),
        (av, residual) => {
          const outcome = planVoice({ ...audibleStar, extinctionAvMag: av, residualArcsec: residual }, context);
          expect(outcome.status).toBe('audible');
          if (outcome.status !== 'audible') return;
          expect(outcome.plan.dustLowpassHz === null).toBe(av === undefined);
          expect(outcome.plan.uncertainty === null).toBe(residual === undefined);
        }
      )
    );
  });

  it('reports a too-faint object as below the audibility floor, not as missing data', () => {
    const outcome = planVoice({ ...audibleStar, luminositySolar: 1e-4, distancePc: 1e5 }, context);
    expect(outcome).toMatchObject({ status: 'silent', reason: 'below_audibility_floor', missing: [] });
  });
});

describe('distance windows by kind', () => {
  it('gives stars the stellar window in every regime, and other kinds their regime window', () => {
    expect(STAR_DISTANCE_WINDOW).toEqual({ nearPc: 1, farPc: 2000 });
    for (const regime of Object.keys(REGIME_DISTANCE_WINDOWS) as ScaleAudioRegime[]) {
      expect(distanceWindowFor('star', regime)).toEqual(STAR_DISTANCE_WINDOW);
      expect(distanceWindowFor('pulsar', regime)).toEqual(REGIME_DISTANCE_WINDOWS[regime]);
    }
  });
});

describe('Property: bounded voice selection', () => {
  const planArb = fc.record({
    id: fc.constantFrom('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l'),
    level: fc.double({ min: -60, max: -6, noNaN: true }),
    target: fc.boolean(),
  });

  it('never exceeds capacity, never invents voices, and keeps ids unique', () => {
    fc.assert(
      fc.property(fc.array(planArb, { maxLength: 30 }), fc.integer({ min: 0, max: 10 }), (specs, capacity) => {
        const plans = specs.map((s) => plan(s.id, s.level, s.target));
        const chosen = selectVoices(plans, new Set(), capacity);
        const ids = chosen.map((p) => p.id);
        expect(chosen.length).toBeLessThanOrEqual(capacity);
        expect(new Set(ids).size).toBe(ids.length);
        for (const p of chosen) expect(plans).toContain(p);
        expect(chosen.length).toBe(Math.min(capacity, new Set(plans.map((p) => p.id)).size));
      })
    );
  });

  it('always includes audible targets first and is independent of input order', () => {
    fc.assert(
      fc.property(fc.array(planArb, { maxLength: 20 }), fc.integer({ min: 1, max: 8 }), (specs, capacity) => {
        const plans = specs.map((s) => plan(s.id, s.level, s.target));
        const chosen = selectVoices(plans, new Set(), capacity);
        const reversed = selectVoices([...plans].reverse(), new Set(), capacity);
        expect(reversed.map((p) => p.id)).toEqual(chosen.map((p) => p.id));
        const targetIds = new Set(plans.filter((p) => p.isTarget).map((p) => p.id));
        const chosenTargets = chosen.filter((p) => targetIds.has(p.id)).length;
        expect(chosenTargets).toBe(Math.min(capacity, targetIds.size));
      })
    );
  });

  it('keeps an incumbent unless a newcomer is louder by more than the hysteresis', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -50, max: -10, noNaN: true }),
        fc.double({ min: 0, max: 6, noNaN: true }),
        (incumbentDb, advantage) => {
          // At an exact tie the id order decides; keep clear of it and of rounding near it.
          fc.pre(Math.abs(advantage - 3) > 1e-9);
          const plans = [plan('old', incumbentDb), plan('new', incumbentDb + advantage)];
          const chosen = selectVoices(plans, new Set(['old']), 1, 3);
          expect(chosen[0]?.id).toBe(advantage > 3 ? 'new' : 'old');
        }
      )
    );
  });
});

describe('Property: identity pitches', () => {
  const ids = fc.uniqueArray(fc.string({ minLength: 1, maxLength: 12 }), { maxLength: IDENTITY_PITCHES_HZ.length });

  it('gives distinct pitches from the set while there are enough of them', () => {
    fc.assert(
      fc.property(ids, (list) => {
        const assigned = assignIdentityPitches(list, new Map());
        const pitches = [...assigned.values()];
        expect(assigned.size).toBe(list.length);
        expect(new Set(pitches).size).toBe(pitches.length);
        for (const p of pitches) expect(IDENTITY_PITCHES_HZ).toContain(p);
      })
    );
  });

  it('keeps the pitch of every id that stays active, and is deterministic', () => {
    fc.assert(
      fc.property(ids, ids, (before, after) => {
        const first = assignIdentityPitches(before, new Map());
        const second = assignIdentityPitches(after, first);
        const unique = new Set(after);
        for (const id of after) {
          if (first.has(id) && unique.size <= IDENTITY_PITCHES_HZ.length) {
            expect(second.get(id)).toBe(first.get(id));
          }
        }
        expect(assignIdentityPitches(after, first)).toEqual(second);
      })
    );
  });
});

describe('Property: listener geometry', () => {
  it('keeps directions unit length under any orientation and leaves them unchanged at identity', () => {
    fc.assert(
      fc.property(unitVec, unitQuat, (v, q) => {
        const local = worldToListenerDirection(v, q)!;
        expect(Math.hypot(...local)).toBeCloseTo(1, 12);
        const same = worldToListenerDirection(v, IDENTITY_QUAT)!;
        same.forEach((c, i) => expect(c).toBeCloseTo(v[i]!, 12));
      })
    );
  });

  it('round-trips: rotating the listener-local direction back by the orientation restores it', () => {
    fc.assert(
      fc.property(unitVec, unitQuat, (v, q) => {
        const back = rotateByQuat(worldToListenerDirection(v, q)!, q);
        back.forEach((c, i) => expect(c).toBeCloseTo(v[i]!, 9));
      })
    );
  });

  it('turning the listener 90 degrees left puts a source that was ahead on the right', () => {
    const local = worldToListenerDirection(front, yawQuat(90))!;
    expect(local[0]).toBeCloseTo(1, 12);
    expect(local[2]).toBeCloseTo(0, 12);
    const right = directionFromAzimuthElevation(90, 0);
    expect(right[0]).toBeCloseTo(1, 12);
  });

  it('treats a missing or degenerate direction as missing', () => {
    for (const bad of [null, undefined, [0, 0, 0] as Vec3, [Number.NaN, 0, 1] as Vec3]) {
      expect(worldToListenerDirection(bad, IDENTITY_QUAT)).toBeNull();
    }
  });
});
