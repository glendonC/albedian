import { describe, expect, it } from 'vitest';
import { AlbedianActionSchema, ActionContextSchema, SkyViewSchema, SpatialViewStateSchema, UNIT_QUATERNION_TOLERANCE } from '../index.js';

// go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): the sky view a request carries while the predicted sky is
// on screen, and space.enter carrying it on to the reducer.

const VIEW = { cameraQuaternionIcrs: [0.5, -0.5, 0.5, 0.5] as [number, number, number, number], fovVerticalDeg: 45 };
const CONTEXT = {
  sessionId: 'session-sample',
  sourceAssetId: 'src_sample',
  sourceTimeSec: 0,
  navigationState: 'source',
  calibrationState: 'predicted_only',
  selectedFindingId: 'finding-star-vega',
  focusedFindingId: null,
  simulationEpochISO: '2021-05-23T21:42:00Z',
  viewRevision: 3,
  reducedMotion: false,
};
const ENTER = { type: 'space.enter', requestId: 'req-1', expectedViewRevision: 3, findingId: 'finding-star-vega', distancePolicy: 'measured_or_estimated' };

describe('SkyViewSchema', () => {
  it('takes a unit quaternion and a vertical field from 1 to 140 degrees', () => {
    expect(SkyViewSchema.parse(VIEW)).toEqual(VIEW);
    for (const fovVerticalDeg of [1, 140]) expect(SkyViewSchema.safeParse({ ...VIEW, fovVerticalDeg }).success).toBe(true);
    for (const fovVerticalDeg of [0.99, 140.01, Number.NaN]) expect(SkyViewSchema.safeParse({ ...VIEW, fovVerticalDeg }).success).toBe(false);
  });

  it('refuses a turn that is not a unit quaternion, past the shared tolerance', () => {
    const n = 1 + UNIT_QUATERNION_TOLERANCE * 2;
    expect(SkyViewSchema.safeParse({ ...VIEW, cameraQuaternionIcrs: [0, 0, 0, n] }).success).toBe(false);
    expect(SkyViewSchema.safeParse({ ...VIEW, cameraQuaternionIcrs: [0, 0, 0, 1 + UNIT_QUATERNION_TOLERANCE / 2] }).success).toBe(true);
    expect(SkyViewSchema.safeParse({ ...VIEW, cameraQuaternionIcrs: [0, 0, 1] }).success).toBe(false);
    expect(SkyViewSchema.safeParse({ ...VIEW, cameraQuaternionIcrs: [0, 0, Number.POSITIVE_INFINITY, 1] }).success).toBe(false);
    expect(SkyViewSchema.safeParse({ fovVerticalDeg: 45 }).success).toBe(false);
  });
});

describe('the sky view on a request and on space.enter', () => {
  it('lets a request carry it, and leaves it optional', () => {
    expect(ActionContextSchema.parse({ ...CONTEXT, skyView: VIEW }).skyView).toEqual(VIEW);
    expect(ActionContextSchema.parse(CONTEXT).skyView).toBeUndefined();
    expect(ActionContextSchema.safeParse({ ...CONTEXT, skyView: { ...VIEW, fovVerticalDeg: 0 } }).success).toBe(false);
  });

  it('lets space.enter carry it on, and leaves it optional', () => {
    expect(AlbedianActionSchema.parse({ ...ENTER, skyView: VIEW })).toEqual({ ...ENTER, skyView: VIEW });
    expect(AlbedianActionSchema.parse(ENTER)).toEqual(ENTER);
    expect(AlbedianActionSchema.safeParse({ ...ENTER, skyView: null }).success).toBe(false);
  });
});

describe('the sky view kept in a spatial view state', () => {
  it('is optional, and refused when it is not a sky view', () => {
    const view = {
      mode: 'source_2d',
      transitionPhase: 'at_source',
      transitionProgress: 0,
      observerOriginParsec: [0, 0, 0],
      cameraQuaternion: [0, 0, 0, 1],
      fovVerticalDeg: 60,
      frustumShiftNdc: [0, 0],
      simulationEpochISO: '2021-05-23T21:42:00Z',
      sceneScalePolicy: 'physical_euclidean',
      activeOverlays: { observedMarkers: true, catalogMatchedLabels: true, predictedEphemerides: true, constellationLines: false, earthDirectionVector: false, residualVectors: false, sightlineCone: false },
    };
    expect(SpatialViewStateSchema.parse(view).skyView).toBeUndefined();
    expect(SpatialViewStateSchema.parse({ ...view, skyView: VIEW }).skyView).toEqual(VIEW);
    expect(SpatialViewStateSchema.safeParse({ ...view, skyView: { ...VIEW, cameraQuaternionIcrs: [0, 0, 0, 2] } }).success).toBe(false);
    expect(SpatialViewStateSchema.safeParse({ ...view, skyView: null }).success).toBe(false);
  });
});
