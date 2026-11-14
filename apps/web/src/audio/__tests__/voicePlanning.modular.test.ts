import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import {
  DISTANCE_CUES,
  DUST,
  LOUDNESS,
  REGIME_DISTANCE_WINDOWS,
  STAR_DISTANCE_WINDOW,
  directReverbGains,
  directToReverberantDb,
  distanceLowpassHz,
  dustLowpassHz,
  perceptualDistance,
  planckFrequencyScaleHz,
  receivedLevelDb,
  uncertaintyBeatHz,
  type ScaleAudioRegime,
} from '../mappings.js';
import * as voicePlanningExtensionless from '../voicePlanning';
import * as voicePlanningModule from '../voicePlanning.js';
import {
  DEFAULT_SELECTION_HYSTERESIS_DB,
  DEFAULT_VOICE_CAPACITY,
  IDENTITY_PITCHES_HZ,
  IDENTITY_QUAT,
  assignIdentityPitches,
  directionFromAzimuthElevation,
  distanceWindowFor,
  fnv1a32,
  normalizeQuat,
  planVoice,
  rotateByQuat,
  selectVoices,
  worldToListenerDirection,
  yawQuat,
  type AudioSourceInput,
  type AudioSourceKind,
  type PlanContext,
  type PlanOutcome,
  type Quat,
  type Vec3,
  type VoicePlan,
} from '../voicePlanning.js';

const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const ALL_REGIMES: readonly ScaleAudioRegime[] = [
  'earth_capture',
  'cislunar',
  'solar_neighborhood',
  'deep_sky',
  'planetary_surface',
];

const DEFAULT_CONTEXT: PlanContext = {
  distanceWindow: REGIME_DISTANCE_WINDOWS.solar_neighborhood,
  listenerOrientation: IDENTITY_QUAT,
};

const BASE_STAR: AudioSourceInput = {
  id: 'sirius-a',
  kind: 'star',
  directionWorld: [0, 0, -1],
  distancePc: 2.64,
  luminositySolar: 25.4,
  teffK: 9940,
};

const BASE_PULSAR: AudioSourceInput = {
  id: 'psr-b0833-45',
  kind: 'pulsar',
  directionWorld: [0, 0.6, -0.8],
  distancePc: 287,
  luminositySolar: 6.9e36 / 3.828e33,
  spinFrequencyHz: 11.1946499395,
};

function makeVoicePlan(
  id: string,
  levelDb: number,
  options: { isTarget?: boolean; evidenceClass?: string | null; kind?: AudioSourceKind } = {}
): VoicePlan {
  const kind = options.kind ?? 'star';
  return {
    id,
    kind,
    isTarget: options.isTarget ?? false,
    evidenceClass: options.evidenceClass ?? null,
    levelDb,
    perceptualDistance: 0.25,
    drrDb: 7.5,
    directGain: Math.sqrt(0.85),
    reverbGain: Math.sqrt(0.15),
    distanceLowpassHz: 11500,
    dustLowpassHz: null,
    directionListener: [0, 0, -1],
    star: kind === 'star' ? { teffK: 5772, planckScaleHz: 260.9 } : null,
    pulsar: kind === 'pulsar' ? { pulseRateHz: 11.19 } : null,
    uncertainty: null,
  };
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function multiplyQuat(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

const nonZeroVecArb = fc
  .tuple(
    fc.double({ min: -50, max: 50, noNaN: true }),
    fc.double({ min: -50, max: 50, noNaN: true }),
    fc.double({ min: -50, max: 50, noNaN: true })
  )
  .filter(([x, y, z]) => Math.hypot(x, y, z) > 1e-3)
  .map(([x, y, z]): Vec3 => [x, y, z]);

const unitQuatArb = fc
  .tuple(
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true }),
    fc.double({ min: -1, max: 1, noNaN: true })
  )
  .filter((q) => Math.hypot(...q) > 1e-3)
  .map((q): Quat => normalizeQuat(q)!);

describe('voicePlanning module surface and constants', () => {
  it('exports the exact set of runtime keys through both .js and extensionless specifiers', () => {
    const expectedKeys = [
      'DEFAULT_SELECTION_HYSTERESIS_DB',
      'DEFAULT_VOICE_CAPACITY',
      'IDENTITY_PITCHES_HZ',
      'IDENTITY_QUAT',
      'assignIdentityPitches',
      'directionFromAzimuthElevation',
      'distanceWindowFor',
      'fnv1a32',
      'normalizeQuat',
      'planVoice',
      'rotateByQuat',
      'selectVoices',
      'worldToListenerDirection',
      'yawQuat',
    ];
    expect(Object.keys(voicePlanningModule).sort()).toEqual(expectedKeys);
    expect(Object.keys(voicePlanningExtensionless).sort()).toEqual(expectedKeys);

    for (const key of expectedKeys) {
      expect(voicePlanningExtensionless[key as keyof typeof voicePlanningModule]).toBe(
        voicePlanningModule[key as keyof typeof voicePlanningModule]
      );
    }
  });

  it('pins all exported constants to their exact values', () => {
    expect(DEFAULT_VOICE_CAPACITY).toBe(8);
    expect(DEFAULT_SELECTION_HYSTERESIS_DB).toBe(3);
    expect(IDENTITY_QUAT).toEqual([0, 0, 0, 1]);

    const expectedRatios = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3, 2, 9 / 4, 5 / 2, 3, 10 / 3, 4];
    expect(IDENTITY_PITCHES_HZ).toEqual(expectedRatios.map((r) => 110 * r));
    expect(IDENTITY_PITCHES_HZ).toHaveLength(11);
    expect(IDENTITY_PITCHES_HZ[0]).toBe(110);
    expect(IDENTITY_PITCHES_HZ[IDENTITY_PITCHES_HZ.length - 1]).toBe(440);
    for (let i = 1; i < IDENTITY_PITCHES_HZ.length; i++) {
      expect(IDENTITY_PITCHES_HZ[i]!).toBeGreaterThan(IDENTITY_PITCHES_HZ[i - 1]!);
    }
  });
});

describe('3D vector and quaternion listener orientation math', () => {
  it('normalizes valid quaternions and rejects nullish, malformed, non-finite, or sub-threshold inputs', () => {
    expect(normalizeQuat(null)).toBeNull();
    expect(normalizeQuat(undefined)).toBeNull();
    expect(normalizeQuat([] as unknown as Quat)).toBeNull();
    expect(normalizeQuat([0, 0, 1] as unknown as Quat)).toBeNull();
    expect(normalizeQuat([0, 0, 0, 1, 0] as unknown as Quat)).toBeNull();
    expect(normalizeQuat([Number.NaN, 0, 0, 1])).toBeNull();
    expect(normalizeQuat([0, Infinity, 0, 1])).toBeNull();
    expect(normalizeQuat([0, 0, -Infinity, 1])).toBeNull();
    expect(normalizeQuat([0, 0, 0, 0])).toBeNull();
    expect(normalizeQuat([1e-12, 0, 0, 0])).toBeNull();
    expect(normalizeQuat([5e-13, 5e-13, 5e-13, 5e-13])).toBeNull();

    const aboveThreshold = normalizeQuat([2e-12, 0, 0, 0]);
    expect(aboveThreshold).toEqual([1, 0, 0, 0]);

    const scaled = normalizeQuat([2, -4, 4, 2]);
    expect(scaled).not.toBeNull();
    expect(Math.hypot(...scaled!)).toBeCloseTo(1, 12);
    expect(scaled![0]).toBeCloseTo(2 / Math.sqrt(40), 12);
    expect(scaled![1]).toBeCloseTo(-4 / Math.sqrt(40), 12);
    expect(scaled![2]).toBeCloseTo(4 / Math.sqrt(40), 12);
    expect(scaled![3]).toBeCloseTo(2 / Math.sqrt(40), 12);
  });

  it('rotates right-handed basis vectors accurately around X, Y, and Z axes', () => {
    const s = Math.SQRT1_2;
    const rotX90: Quat = [s, 0, 0, s];
    const rotY90: Quat = [0, s, 0, s];
    const rotZ90: Quat = [0, 0, s, s];

    const yByX = rotateByQuat([0, 1, 0], rotX90);
    expect(yByX[0]).toBeCloseTo(0, 12);
    expect(yByX[1]).toBeCloseTo(0, 12);
    expect(yByX[2]).toBeCloseTo(1, 12);

    const negZByY = rotateByQuat([0, 0, -1], rotY90);
    expect(negZByY[0]).toBeCloseTo(-1, 12);
    expect(negZByY[1]).toBeCloseTo(0, 12);
    expect(negZByY[2]).toBeCloseTo(0, 12);

    const xByZ = rotateByQuat([1, 0, 0], rotZ90);
    expect(xByZ[0]).toBeCloseTo(0, 12);
    expect(xByZ[1]).toBeCloseTo(1, 12);
    expect(xByZ[2]).toBeCloseTo(0, 12);
  });

  it('Property: rotateByQuat is a proper SO(3) isometry respecting group composition and double cover', () => {
    fc.assert(
      fc.property(nonZeroVecArb, nonZeroVecArb, unitQuatArb, unitQuatArb, (u, v, q1, q2) => {
        const ru = rotateByQuat(u, q1);
        const rv = rotateByQuat(v, q1);

        expect(Math.hypot(...ru)).toBeCloseTo(Math.hypot(...u), 9);
        expect(dot(ru, rv)).toBeCloseTo(dot(u, v), 7);

        const crossRotated = rotateByQuat(cross(u, v), q1);
        const rotatedCross = cross(ru, rv);
        for (let i = 0; i < 3; i++) {
          expect(crossRotated[i]!).toBeCloseTo(rotatedCross[i]!, 7);
        }

        const negQ1: Quat = [-q1[0], -q1[1], -q1[2], -q1[3]];
        const ruNeg = rotateByQuat(u, negQ1);
        for (let i = 0; i < 3; i++) {
          expect(ruNeg[i]!).toBeCloseTo(ru[i]!, 9);
        }

        const sequential = rotateByQuat(rotateByQuat(u, q2), q1);
        const combined = rotateByQuat(u, normalizeQuat(multiplyQuat(q1, q2))!);
        for (let i = 0; i < 3; i++) {
          expect(sequential[i]!).toBeCloseTo(combined[i]!, 8);
        }
      }),
      { seed: SEED }
    );
  });

  it('worldToListenerDirection normalizes world vectors, rejects invalid directions, and falls back to identity for invalid orientations', () => {
    expect(worldToListenerDirection(null, IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection(undefined, IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([0, 1] as unknown as Vec3, IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([0, 1, 0, 0] as unknown as Vec3, IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([0, Infinity, -1], IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([0, 0, 0], IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([1e-12, 0, 0], IDENTITY_QUAT)).toBeNull();
    expect(worldToListenerDirection([2e-12, 0, 0], IDENTITY_QUAT)).toEqual([1, 0, 0]);

    const unnormalized = worldToListenerDirection([0, 30, -40], IDENTITY_QUAT)!;
    expect(unnormalized[0]).toBeCloseTo(0, 12);
    expect(unnormalized[1]).toBeCloseTo(0.6, 12);
    expect(unnormalized[2]).toBeCloseTo(-0.8, 12);

    const invalidOrientations: Array<Quat | null | undefined> = [
      null,
      undefined,
      [0, 0, 0, 0],
      [Number.NaN, 0, 0, 1],
      [0, 0, Infinity, 1],
      [1, 0, 0] as unknown as Quat,
    ];
    for (const badQuat of invalidOrientations) {
      const local = worldToListenerDirection([0, 3, -4], badQuat)!;
      expect(local[0]).toBeCloseTo(0, 12);
      expect(local[1]).toBeCloseTo(0.6, 12);
      expect(local[2]).toBeCloseTo(-0.8, 12);
    }

    const scaledYaw90 = worldToListenerDirection([0, 0, -10], [0, 5, 0, 5])!;
    expect(scaledYaw90[0]).toBeCloseTo(1, 12);
    expect(scaledYaw90[1]).toBeCloseTo(0, 12);
    expect(scaledYaw90[2]).toBeCloseTo(0, 12);
  });

  it('computes cardinal directions and yaw quaternions consistently across angles', () => {
    expect(directionFromAzimuthElevation(0, 0)).toEqual([0, 0, -1]);

    const right = directionFromAzimuthElevation(90, 0);
    expect(right[0]).toBeCloseTo(1, 12);
    expect(right[1]).toBeCloseTo(0, 12);
    expect(right[2]).toBeCloseTo(0, 12);

    const left = directionFromAzimuthElevation(-90, 0);
    expect(left[0]).toBeCloseTo(-1, 12);
    expect(left[1]).toBeCloseTo(0, 12);
    expect(left[2]).toBeCloseTo(0, 12);

    const behind = directionFromAzimuthElevation(180, 0);
    expect(behind[0]).toBeCloseTo(0, 12);
    expect(behind[1]).toBeCloseTo(0, 12);
    expect(behind[2]).toBeCloseTo(1, 12);

    const zenith = directionFromAzimuthElevation(37, 90);
    expect(zenith[0]).toBeCloseTo(0, 12);
    expect(zenith[1]).toBeCloseTo(1, 12);
    expect(zenith[2]).toBeCloseTo(0, 12);

    const nadir = directionFromAzimuthElevation(-120, -90);
    expect(nadir[0]).toBeCloseTo(0, 12);
    expect(nadir[1]).toBeCloseTo(-1, 12);
    expect(nadir[2]).toBeCloseTo(0, 12);

    expect(yawQuat(0)).toEqual([0, 0, 0, 1]);
    const q90 = yawQuat(90);
    expect(q90[0]).toBe(0);
    expect(q90[1]).toBeCloseTo(Math.SQRT1_2, 12);
    expect(q90[2]).toBe(0);
    expect(q90[3]).toBeCloseTo(Math.SQRT1_2, 12);
  });

  it('Property: yawing the listener by -azimuth centers any azimuth/elevation source onto the listener meridian', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -360, max: 360, noNaN: true }),
        fc.double({ min: -89, max: 89, noNaN: true }),
        (azDeg, elDeg) => {
          const worldDir = directionFromAzimuthElevation(azDeg, elDeg);
          expect(Math.hypot(...worldDir)).toBeCloseTo(1, 12);

          const q = yawQuat(-azDeg);
          expect(q[0]).toBe(0);
          expect(q[2]).toBe(0);
          expect(Math.hypot(...q)).toBeCloseTo(1, 12);

          const local = worldToListenerDirection(worldDir, q)!;
          const elRad = (elDeg * Math.PI) / 180;
          expect(local[0]).toBeCloseTo(0, 11);
          expect(local[1]).toBeCloseTo(Math.sin(elRad), 11);
          expect(local[2]).toBeCloseTo(-Math.cos(elRad), 11);
        }
      ),
      { seed: SEED }
    );
  });
});

describe('single-source voice planning and silence outcomes', () => {
  it('maps distance windows by source kind across all five scale regimes', () => {
    for (const regime of ALL_REGIMES) {
      expect(distanceWindowFor('star', regime)).toBe(STAR_DISTANCE_WINDOW);
      expect(distanceWindowFor('pulsar', regime)).toBe(REGIME_DISTANCE_WINDOWS[regime]);
    }
  });

  it('reports every missing core field in deterministic order, including empty id and unknown kind', () => {
    const emptyIdOutcome = planVoice({ ...BASE_STAR, id: '' }, DEFAULT_CONTEXT);
    expect(emptyIdOutcome).toEqual({
      status: 'silent',
      id: '',
      reason: 'missing_input',
      missing: ['id'],
    });

    const nonStringIdOutcome = planVoice(
      { ...BASE_STAR, id: null as unknown as string },
      DEFAULT_CONTEXT
    );
    expect(nonStringIdOutcome).toEqual({
      status: 'silent',
      id: '',
      reason: 'missing_input',
      missing: ['id'],
    });

    const allBadStar = planVoice(
      {
        id: '',
        kind: 'star',
        directionWorld: [0, 0, 0],
        distancePc: 0,
        luminositySolar: 0,
        teffK: 0,
      },
      DEFAULT_CONTEXT
    );
    expect(allBadStar).toEqual({
      status: 'silent',
      id: '',
      reason: 'missing_input',
      missing: ['id', 'directionWorld', 'distancePc', 'luminositySolar', 'teffK'],
    });

    const unknownKind = planVoice(
      {
        ...BASE_STAR,
        kind: 'quasar' as unknown as AudioSourceKind,
      },
      DEFAULT_CONTEXT
    );
    expect(unknownKind).toEqual({
      status: 'silent',
      id: BASE_STAR.id,
      reason: 'missing_input',
      missing: ['kind'],
    });
  });

  it('prioritizes missing_input over below_audibility_floor and rejects invalid distance windows or pulsar spin bounds', () => {
    const faintAndMissingTeff = planVoice(
      { ...BASE_STAR, luminositySolar: 1e-6, distancePc: 1e6, teffK: null },
      DEFAULT_CONTEXT
    );
    expect(faintAndMissingTeff).toEqual({
      status: 'silent',
      id: BASE_STAR.id,
      reason: 'missing_input',
      missing: ['teffK'],
    });

    const invalidWindowOutcome = planVoice(BASE_STAR, {
      distanceWindow: { nearPc: 100, farPc: 10 },
      listenerOrientation: IDENTITY_QUAT,
    });
    expect(invalidWindowOutcome).toEqual({
      status: 'silent',
      id: BASE_STAR.id,
      reason: 'missing_input',
      missing: ['distancePc'],
    });

    const overMaxSpin = planVoice({ ...BASE_PULSAR, spinFrequencyHz: 2000.0001 }, DEFAULT_CONTEXT);
    expect(overMaxSpin).toEqual({
      status: 'silent',
      id: BASE_PULSAR.id,
      reason: 'missing_input',
      missing: ['spinFrequencyHz'],
    });

    const exactMaxSpin = planVoice({ ...BASE_PULSAR, spinFrequencyHz: 2000 }, DEFAULT_CONTEXT);
    expect(exactMaxSpin.status).toBe('audible');
    if (exactMaxSpin.status === 'audible') {
      expect(exactMaxSpin.plan.pulsar).toEqual({ pulseRateHz: 2000 });
      expect(exactMaxSpin.plan.star).toBeNull();
    }
  });

  it('produces exact mapping values, optional layer boundaries, metadata flags, and degenerate-orientation fallback', () => {
    const rotatedContext: PlanContext = {
      distanceWindow: STAR_DISTANCE_WINDOW,
      listenerOrientation: yawQuat(90),
    };
    const outcome: PlanOutcome = planVoice(
      {
        ...BASE_STAR,
        isTarget: true,
        evidenceClass: 'catalog_matched',
        extinctionAvMag: 0,
        residualArcsec: 0,
      },
      rotatedContext
    );
    expect(outcome.status).toBe('audible');
    if (outcome.status !== 'audible') return;

    const expectedPerceptual = perceptualDistance(BASE_STAR.distancePc, STAR_DISTANCE_WINDOW)!;
    const expectedDrr = directToReverberantDb(expectedPerceptual)!;
    const expectedGains = directReverbGains(expectedDrr);

    expect(outcome.plan).toEqual({
      id: BASE_STAR.id,
      kind: 'star',
      isTarget: true,
      evidenceClass: 'catalog_matched',
      levelDb: receivedLevelDb(BASE_STAR.luminositySolar, BASE_STAR.distancePc),
      perceptualDistance: expectedPerceptual,
      drrDb: expectedDrr,
      directGain: expectedGains.direct,
      reverbGain: expectedGains.reverb,
      distanceLowpassHz: distanceLowpassHz(expectedPerceptual),
      dustLowpassHz: DUST.openHz,
      directionListener: worldToListenerDirection(BASE_STAR.directionWorld, yawQuat(90)),
      star: {
        teffK: BASE_STAR.teffK!,
        planckScaleHz: planckFrequencyScaleHz(BASE_STAR.teffK)!,
      },
      pulsar: null,
      uncertainty: { beatHz: 0 },
    });

    const negativeOptionals = planVoice(
      {
        ...BASE_PULSAR,
        extinctionAvMag: -0.5,
        residualArcsec: -1,
      },
      {
        distanceWindow: REGIME_DISTANCE_WINDOWS.earth_capture,
        listenerOrientation: [0, 0, 0, 0],
      }
    );
    expect(negativeOptionals.status).toBe('audible');
    if (negativeOptionals.status === 'audible') {
      expect(negativeOptionals.plan.isTarget).toBe(false);
      expect(negativeOptionals.plan.evidenceClass).toBeNull();
      expect(negativeOptionals.plan.dustLowpassHz).toBeNull();
      expect(negativeOptionals.plan.uncertainty).toBeNull();
      expect(negativeOptionals.plan.directionListener).toEqual([0, 0.6, -0.8]);
      expect(negativeOptionals.plan.dustLowpassHz).toBe(dustLowpassHz(-0.5));
      expect(negativeOptionals.plan.uncertainty).toBe(uncertaintyBeatHz(-1));
      expect(negativeOptionals.plan.levelDb).toBeGreaterThanOrEqual(LOUDNESS.floorDb);
      expect(negativeOptionals.plan.distanceLowpassHz).toBeGreaterThanOrEqual(
        DISTANCE_CUES.lowpassFarHz
      );
    }
  });
});

describe('multi-voice capacity selection and incumbent hysteresis', () => {
  it('enforces default capacity (8) and default hysteresis (3 dB) when optional arguments are omitted', () => {
    const twelve = Array.from({ length: 12 }, (_, i) =>
      makeVoicePlan(`v-${String(i).padStart(2, '0')}`, -30 + i)
    );
    const selectedDefault = selectVoices(twelve, new Set());
    expect(selectedDefault).toHaveLength(DEFAULT_VOICE_CAPACITY);
    expect(selectedDefault.map((p) => p.id)).toEqual([
      'v-11',
      'v-10',
      'v-09',
      'v-08',
      'v-07',
      'v-06',
      'v-05',
      'v-04',
    ]);

    const incumbentHolds = selectVoices(
      [makeVoicePlan('inc', -20), makeVoicePlan('new', -17.1)],
      new Set(['inc'])
    );
    expect(incumbentHolds[0]?.id).toBe('inc');

    const newcomerBeatsDefault = selectVoices(
      [makeVoicePlan('inc', -20), makeVoicePlan('new', -16.9)],
      new Set(['inc'])
    );
    expect(newcomerBeatsDefault[0]?.id).toBe('new');
  });

  it('handles zero, negative, and fractional capacities without mutating input', () => {
    const input = [makeVoicePlan('b', -15), makeVoicePlan('a', -10), makeVoicePlan('c', -20)];
    const snapshot = [...input];

    expect(selectVoices(input, new Set(), 0)).toEqual([]);
    expect(selectVoices(input, new Set(), -4)).toEqual([]);
    expect(selectVoices(input, new Set(), 2.9).map((p) => p.id)).toEqual(['a', 'b']);
    expect(input).toEqual(snapshot);
  });

  it('deduplicates duplicate ids by target status first, then loudness, keeping the first on exact ties', () => {
    const targetQuieter = makeVoicePlan('dup', -40, { isTarget: true, evidenceClass: 'observed' });
    const nonTargetLouder = makeVoicePlan('dup', -10, {
      isTarget: false,
      evidenceClass: 'simulated',
    });
    expect(selectVoices([nonTargetLouder, targetQuieter], new Set(), 4)).toEqual([targetQuieter]);
    expect(selectVoices([targetQuieter, nonTargetLouder], new Set(), 4)).toEqual([targetQuieter]);

    const firstTie = makeVoicePlan('same', -22, { evidenceClass: 'first' });
    const secondTie = makeVoicePlan('same', -22, { evidenceClass: 'second' });
    const louder = makeVoicePlan('same', -18, { evidenceClass: 'louder' });
    expect(selectVoices([firstTie, secondTie], new Set(), 4)).toEqual([firstTie]);
    expect(selectVoices([firstTie, louder, secondTie], new Set(), 4)).toEqual([louder]);
  });

  it('ranks targets ahead of louder incumbent non-targets, applies hysteresis within targets, and breaks exact ties by id', () => {
    const quietTarget = makeVoicePlan('z-target', -55, { isTarget: true });
    const loudIncumbent = makeVoicePlan('a-incumbent', -8, { isTarget: false });
    expect(selectVoices([loudIncumbent, quietTarget], new Set(['a-incumbent']), 1, 20)).toEqual([
      quietTarget,
    ]);

    const incumbentTarget = makeVoicePlan('t-old', -30, { isTarget: true });
    const newcomerTarget = makeVoicePlan('t-new', -28, { isTarget: true });
    expect(selectVoices([newcomerTarget, incumbentTarget], new Set(['t-old']), 1, 3)).toEqual([
      incumbentTarget,
    ]);

    const tieIncumbentB = makeVoicePlan('b', -20);
    const tieNewcomerA = makeVoicePlan('a', -17);
    expect(selectVoices([tieIncumbentB, tieNewcomerA], new Set(['b']), 2, 3).map((p) => p.id)).toEqual([
      'a',
      'b',
    ]);
    expect(selectVoices([tieNewcomerA, tieIncumbentB], new Set(['b']), 2, 3).map((p) => p.id)).toEqual([
      'a',
      'b',
    ]);
  });
});

describe('FNV-1a hashing and persistent identity pitch allocation', () => {
  it('matches canonical 32-bit FNV-1a vectors and produces unsigned 32-bit integers', () => {
    expect(fnv1a32('')).toBe(0x811c9dc5);
    expect(fnv1a32('a')).toBe(0xe40c292c);
    expect(fnv1a32('foobar')).toBe(0xbf9cf968);

    fc.assert(
      fc.property(fc.string({ maxLength: 64 }), (text) => {
        const hash = fnv1a32(text);
        expect(Number.isInteger(hash)).toBe(true);
        expect(hash).toBeGreaterThanOrEqual(0);
        expect(hash).toBeLessThanOrEqual(0xffffffff);
      }),
      { seed: SEED }
    );
  });

  it('handles empty pitch sets, duplicate ids, stale previous pitches, and conflicting previous pitches', () => {
    expect(assignIdentityPitches(['a', 'b'], new Map(), []).size).toBe(0);

    const customPitches = [100, 200, 300] as const;
    const withDuplicates = assignIdentityPitches(['dup', 'other', 'dup'], new Map(), customPitches);
    expect(withDuplicates.size).toBe(2);
    expect(withDuplicates.get('dup')).not.toBe(withDuplicates.get('other'));

    const stalePrevious = new Map<string, number>([['star-1', 999]]);
    const reassigned = assignIdentityPitches(['star-1'], stalePrevious, customPitches);
    expect(customPitches).toContain(reassigned.get('star-1'));

    const conflictingPrevious = new Map<string, number>([
      ['first', 200],
      ['second', 200],
    ]);
    const resolved = assignIdentityPitches(['first', 'second'], conflictingPrevious, customPitches);
    expect(resolved.get('first')).toBe(200);
    expect(resolved.get('second')).not.toBe(200);
    expect(customPitches).toContain(resolved.get('second'));
  });

  it('preserves an incumbent pitch even when a newcomer precedes it in ids and hashes to its pitch slot', () => {
    const customPitches = [110, 220, 330] as const;
    const newcomerId = 'newcomer-slot-check';
    const newcomerPreferredSlot = fnv1a32(newcomerId) % customPitches.length;
    const incumbentPitch = customPitches[newcomerPreferredSlot]!;

    const previous = new Map<string, number>([['incumbent', incumbentPitch]]);
    const assigned = assignIdentityPitches([newcomerId, 'incumbent'], previous, customPitches);

    expect(assigned.get('incumbent')).toBe(incumbentPitch);
    expect(assigned.get(newcomerId)).toBe(
      customPitches[(newcomerPreferredSlot + 1) % customPitches.length]
    );
  });

  it('linear-probes deterministically on hash collisions and wraps cleanly when active ids exceed pitch count', () => {
    const twoPitches = [110, 220] as const;
    const bucketZeroIds: string[] = [];
    for (let i = 0; bucketZeroIds.length < 3; i++) {
      const candidate = `probe-${i}`;
      if (fnv1a32(candidate) % 2 === 0) bucketZeroIds.push(candidate);
    }
    const [id0, id1, id2] = bucketZeroIds as [string, string, string];
    const probed = assignIdentityPitches([id0, id1, id2], new Map(), twoPitches);
    expect(probed.get(id0)).toBe(110);
    expect(probed.get(id1)).toBe(220);
    expect(probed.get(id2)).toBe(110);

    fc.assert(
      fc.property(
        fc.uniqueArray(fc.string({ minLength: 1, maxLength: 10 }), { minLength: 12, maxLength: 20 }),
        (manyIds) => {
          const assigned = assignIdentityPitches(manyIds, new Map());
          expect(assigned.size).toBe(manyIds.length);
          for (const pitch of assigned.values()) {
            expect(IDENTITY_PITCHES_HZ).toContain(pitch);
          }
          expect(new Set(assigned.values()).size).toBe(IDENTITY_PITCHES_HZ.length);
        }
      ),
      { seed: SEED }
    );
  });
});
