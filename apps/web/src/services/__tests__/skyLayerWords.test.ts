import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import fc from 'fast-check';
import { ObservationSolutionSchema, type ObservationSolution, type PoseEstimate } from '@albedian/contracts';
import {
  formatClipTime,
  goToMomentSentence,
  poseBasisSentence,
  poseUncertaintySentence,
  skyLayerSentence,
  withinAboutDeg,
} from '../skyLayerWords';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

// The solutions the Python side writes (tests/integration/test_unregistered_detections.py), synthetic throughout
const FIXTURE = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, unknown>;
const sample = (name: string): ObservationSolution => ObservationSolutionSchema.parse(FIXTURE[name]);

// The synthetic moment and open-ocean place the fixtures use
const SYNTHETIC_TIME = '2030-01-01T02:00:00Z';
const SYNTHETIC_PLACE = { latDeg: 30.0, lonDeg: -40.0 };
type Facts = { mediaKind: 'image' | 'video'; capturedAtUTC?: string; observerLocation?: { latDeg: number; lonDeg: number } };
const photo = (facts: Partial<Facts> = {}): Facts => ({
  mediaKind: 'image',
  capturedAtUTC: SYNTHETIC_TIME,
  observerLocation: SYNTHETIC_PLACE,
  ...facts,
});
const RAW_STATUS = /calibrated|approximate|predicted_only|unresolved|ambiguous|pending/i;

function withPose(pose: Partial<PoseEstimate>): ObservationSolution {
  const base = sample('pose_moon_level_head');
  return { ...base, poseEstimate: { ...base.poseEstimate!, ...pose } };
}

describe('skyLayerSentence: one plain line for each layer (REQ-SKY-6.1)', () => {
  it('says how many stars a calibrated photo or clip was matched to', () => {
    const calibrated = sample('plate_calibrated');
    expect(skyLayerSentence(calibrated, photo())).toBe('Matched to 11 stars in your photo.');
    expect(skyLayerSentence(calibrated, photo({ mediaKind: 'video' }))).toBe('Matched to 11 stars in your clip.');
    const one = { ...calibrated, quality: { ...calibrated.quality!, matchedInlierCount: 1 } };
    expect(skyLayerSentence(one, photo())).toBe('Matched to 1 star in your photo.');
  });

  it('says a pose was lined up from its beacon, within about the whole degrees above its two bounds', () => {
    // The fixture: 2.619 deg at the frame centre plus 0.0073 deg for the Moon's centroid, so about 3
    expect(skyLayerSentence(sample('pose_moon_level_head'), photo())).toBe(
      'Lined up from the Moon, within about 3 degrees. Not checked against stars.'
    );
    // Rounded up, never to the nearest: 2.2 + 0.1 is about 3, not 2
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 2.2, beaconCentroidUncertaintyDeg: 0.1 }), photo())).toContain(
      'within about 3 degrees'
    );
    // Both bounds count: 2.95 alone would round up to 3, with the centroid's 0.1 it is 4
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 2.95, beaconCentroidUncertaintyDeg: 0.1 }), photo())).toContain(
      'within about 4 degrees'
    );
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 0.5, beaconCentroidUncertaintyDeg: 0.2 }), photo())).toContain(
      'within about 1 degree.'
    );
    expect(skyLayerSentence(withPose({ method: 'sun_horizon', beacon: 'sun' }), photo())).toBe(
      'Lined up from the Sun, within about 3 degrees. Not checked against stars.'
    );
  });

  it('says a pointing without a pose estimate is rough and is not drawn over the photo', () => {
    expect(skyLayerSentence(sample('horizon_approximate'), photo())).toBe(
      'Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your photo.'
    );
  });

  it('says a predicted_only result had too few points of light below the solver minimum, and shows the sky', () => {
    // The fixture detected none
    expect(sample('horizon_predicted_only').quality!.detectedCandidateCount).toBe(0);
    expect(skyLayerSentence(sample('horizon_predicted_only'), photo())).toBe(
      "Too few points of light to match to stars. Showing the sky predicted for your photo's time and place."
    );
    expect(skyLayerSentence(sample('horizon_predicted_only'), photo({ mediaKind: 'video' }))).toBe(
      "Too few points of light to match to stars. Showing the sky predicted for your clip's time and place."
    );
  });

  it('says how many points of light were found when there were enough to try, never too few (verdict finding 2)', () => {
    const base = sample('horizon_predicted_only');
    const detected = (n: number): ObservationSolution => ({ ...base, quality: { ...base.quality!, detectedCandidateCount: n } });
    // services/science/solver.py stops before matching below 6 detections, its own minimum
    expect(skyLayerSentence(detected(5), photo())).toBe(
      "Too few points of light to match to stars. Showing the sky predicted for your photo's time and place."
    );
    expect(skyLayerSentence(detected(6), photo())).toBe(
      "Found 6 points of light, but could not match them to stars for sure. Showing the sky predicted for your photo's time and place."
    );
    // As the auditor's 40 synthetic detections reach it through the solver and complete_sky
    expect(skyLayerSentence(detected(40), photo({ mediaKind: 'video' }))).toBe(
      "Found 40 points of light, but could not match them to stars for sure. Showing the sky predicted for your clip's time and place."
    );
    for (const n of [6, 7, 40, 1000]) expect(skyLayerSentence(detected(n), photo())).not.toMatch(/too few/i);
  });

  it('says the points of light were not matched when the solution carries no count', () => {
    const { quality: _quality, ...bare } = sample('horizon_predicted_only');
    expect(skyLayerSentence(bare as ObservationSolution, photo())).toBe(
      "The points of light could not be matched to stars. Showing the sky predicted for your photo's time and place."
    );
  });

  it('says which of the time and the place the file lacks when there is no sky (REQ-SKY-2.1)', () => {
    for (const name of ['plate_too_few_sources', 'plate_wcs_failed_verification']) {
      const unresolved = sample(name);
      expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: undefined }))).toBe(
        'Your photo does not say when it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ observerLocation: undefined }))).toBe(
        'Your photo does not say where it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: undefined, observerLocation: undefined }))).toBe(
        'Your photo does not say when or where it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ mediaKind: 'video', capturedAtUTC: undefined }))).toBe(
        'Your clip does not say when it was recorded, so its sky cannot be shown.'
      );
      const ambiguous = { ...unresolved, status: 'ambiguous' as const };
      expect(skyLayerSentence(ambiguous, photo({ observerLocation: undefined }))).toBe(
        'Your photo does not say where it was taken, so its sky cannot be shown.'
      );
    }
  });

  it('says the moment is outside 1800 to 2050 at the ephemeris bounds, with the frame time added (REQ-SKY-2.3)', () => {
    const unresolved = sample('plate_too_few_sources');
    const outside = 'Your photo was taken outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown.';
    const inside = 'No sky could be worked out for your photo.';
    // services/science/ephemeris.py require_ephemeris_range: from 1800-01-01T00:00:00Z, until 2051-01-01T00:00:00Z
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '1799-12-31T23:59:59Z' }))).toBe(outside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '1800-01-01T00:00:00Z' }))).toBe(inside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '2050-12-31T23:59:59Z' }))).toBe(inside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '2051-01-01T00:00:00Z' }))).toBe(outside);
    // The moment is the solution's frame: a clip that starts inside and reaches its frame two seconds later is outside
    const late = { ...unresolved, frameTimeSec: 2 };
    expect(skyLayerSentence(late, photo({ capturedAtUTC: '2050-12-31T23:59:59Z' }))).toBe(outside);
    expect(skyLayerSentence(unresolved, photo())).toBe(inside);
  });

  it('says a failed solve stopped, and a pending one is not worked out yet', () => {
    const unresolved = sample('plate_too_few_sources');
    expect(skyLayerSentence(unresolved, photo(), { state: 'failed' })).toBe(
      'The sky solve for your photo stopped before it finished.'
    );
    expect(skyLayerSentence(undefined, photo({ mediaKind: 'video' }), { state: 'failed' })).toBe(
      'The sky solve for your clip stopped before it finished.'
    );
    expect(skyLayerSentence(sample('pending_unsolved'), photo())).toBe('The sky for your photo has not been worked out yet.');
  });

  it('PBT: never says a raw status, the capture time, the place or the file name', () => {
    const names = Object.keys(FIXTURE);
    const kinds = fc.constantFrom<'image' | 'video'>('image', 'video');
    // Synthetic moments across and beyond the ephemeris' years, and places with fractional degrees
    const moment = fc
      .integer({ min: Date.UTC(1700, 0, 1), max: Date.UTC(2100, 0, 1) })
      .map((ms) => new Date(Math.floor(ms / 1000) * 1000).toISOString().replace('.000Z', 'Z'));
    const degrees = (limit: number) => fc.double({ min: -limit, max: limit, noNaN: true }).filter((v) => Math.abs(v % 1) > 0.01);
    fc.assert(
      fc.property(
        fc.constantFrom(...names),
        kinds,
        fc.option(moment, { nil: undefined }),
        fc.option(fc.record({ latDeg: degrees(89), lonDeg: degrees(179) }), { nil: undefined }),
        fc.constantFrom<'failed' | 'succeeded' | undefined>(undefined, 'failed', 'succeeded'),
        (name, mediaKind, capturedAtUTC, observerLocation, jobState) => {
          const solution = sample(name);
          const sentence = skyLayerSentence(solution, { mediaKind, capturedAtUTC, observerLocation }, jobState ? { state: jobState } : undefined);
          expect(sentence).not.toMatch(RAW_STATUS);
          expect(sentence).not.toMatch(/\.(png|jpe?g|mov|mp4|heic)/i);
          if (capturedAtUTC) {
            const [date, time] = capturedAtUTC.split('T');
            expect(sentence).not.toContain(date!);
            expect(sentence).not.toContain(time!.slice(0, 5));
            const year = date!.slice(0, 4);
            if (year !== '1800' && year !== '2050') expect(sentence).not.toContain(year);
          }
          if (observerLocation) {
            for (const value of [observerLocation.latDeg, observerLocation.lonDeg]) {
              expect(sentence).not.toContain(value.toFixed(2));
              expect(sentence).not.toContain(Math.abs(value).toFixed(1));
            }
          }
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          ['plate_too_few_sources', 'image', '1750-06-01T00:00:00Z', SYNTHETIC_PLACE, undefined],
          ['plate_calibrated', 'video', SYNTHETIC_TIME, { latDeg: 12.34, lonDeg: -56.78 }, 'succeeded'],
          ['pose_moon_level_head', 'image', SYNTHETIC_TIME, SYNTHETIC_PLACE, undefined],
        ],
      }
    );
  });
});

describe('the lined-up layer in plain words (REQ-SKY-6.3, 6.4)', () => {
  it('names the basis from the method and says it is drawn by the app, not measured, not checked', () => {
    const pose = sample('pose_moon_level_head').poseEstimate!;
    expect(poseBasisSentence(pose, 'video')).toBe(
      "Lined up from the Moon, the camera's field of view and a level head. Drawn by the app, not measured in your clip, and not checked against stars."
    );
    expect(poseBasisSentence({ ...pose, method: 'moon_horizon' }, 'image')).toBe(
      'Lined up from the Moon and the horizon. Drawn by the app, not measured in your photo, and not checked against stars.'
    );
    expect(poseBasisSentence({ ...pose, method: 'sun_horizon', beacon: 'sun' }, 'image')).toBe(
      'Lined up from the Sun and the horizon. Drawn by the app, not measured in your photo, and not checked against stars.'
    );
  });

  it('gives the uncertainty in whole degrees rounded up and says what the rings show', () => {
    const pose = sample('pose_moon_level_head').poseEstimate!;
    expect(withinAboutDeg(pose)).toBe(3);
    expect(withinAboutDeg({ ...pose, maxCenterOffsetDeg: 2.95, beaconCentroidUncertaintyDeg: 0.1 })).toBe(4);
    expect(withinAboutDeg({ ...pose, maxCenterOffsetDeg: 2.2, beaconCentroidUncertaintyDeg: 0.1 })).toBe(3);
    expect(poseUncertaintySentence(pose)).toBe(
      'Within about 3 degrees at the middle of the frame, less near the Moon and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold.'
    );
    expect(poseUncertaintySentence({ ...pose, method: 'sun_horizon', beacon: 'sun', maxCenterOffsetDeg: 0.4, beaconCentroidUncertaintyDeg: 0.1 })).toBe(
      'Within about 1 degree at the middle of the frame, less near the Sun and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold.'
    );
  });

  it('writes a clip time in seconds with one decimal, rounded to the nearest tenth, whole seconds too', () => {
    expect(formatClipTime(0)).toBe('0.0');
    expect(formatClipTime(3)).toBe('3.0');
    // Frame times as sky-3 takes them, n / fps
    expect(formatClipTime(75 / 30)).toBe('2.5');
    expect(formatClipTime(99 / 29.4)).toBe('3.4');
    expect(formatClipTime(7 / 20)).toBe('0.4');
    expect(formatClipTime(3.33)).toBe('3.3');
    expect(formatClipTime(3.97)).toBe('4.0');
    expect(formatClipTime(59.94)).toBe('59.9');
    // Past a minute it stays in seconds, never minutes and seconds
    expect(formatClipTime(59.97)).toBe('60.0');
    expect(formatClipTime(75.5)).toBe('75.5');
    expect(formatClipTime(3601 / 29.97)).toBe('120.2');
    expect(formatClipTime(600)).toBe('600.0');
  });

  it('names the moment in seconds and quotes the command that goes there, in the form the parser reads', () => {
    expect(goToMomentSentence(99 / 29.4)).toBe(
      'Lined up 3.4 seconds into the clip. Say or type "jump to 3.4 seconds" to see it.'
    );
    expect(goToMomentSentence(3)).toBe('Lined up 3.0 seconds into the clip. Say or type "jump to 3.0 seconds" to see it.');
    expect(goToMomentSentence(3601 / 29.97)).toBe(
      'Lined up 120.2 seconds into the clip. Say or type "jump to 120.2 seconds" to see it.'
    );
  });
});
