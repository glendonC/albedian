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
  solveStoppedSentence,
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

// Whether a star search ran is said by whoever built the session (apiClient.ts), never read from the solution: an
// upload's solver searched the frame for points of light, the solver of a clip discovered on the laptop did not
const SEARCHED = { starSearchRan: true };
const NOT_SEARCHED = { starSearchRan: false };
const BOTH = [SEARCHED, NOT_SEARCHED];
const openSky = (kind: 'photo' | 'clip'): string => `You can open the sky predicted for your ${kind}'s time and place.`;

function withPose(pose: Partial<PoseEstimate>): ObservationSolution {
  const base = sample('pose_moon_level_head');
  return { ...base, poseEstimate: { ...base.poseEstimate!, ...pose } };
}

/**
 * What is left of a sentence once the two forms that take a bright spot to be the Moon or the Sun are removed. The
 * Moon or the Sun named anywhere else would state as fact what one frame cannot prove.
 */
const outsideTakenToBe = (sentence: string): string =>
  sentence.replace(/taking a bright spot to be the (Moon|Sun)/g, '').replace(/the bright spot taken to be the (Moon|Sun)/g, '');

describe('skyLayerSentence: one plain line for each layer (REQ-SKY-6.1)', () => {
  it('says how many stars a calibrated photo or clip was matched to', () => {
    const calibrated = sample('plate_calibrated');
    expect(skyLayerSentence(calibrated, photo(), SEARCHED)).toBe('Matched to 11 stars in your photo.');
    expect(skyLayerSentence(calibrated, photo({ mediaKind: 'video' }), SEARCHED)).toBe('Matched to 11 stars in your clip.');
    const one = { ...calibrated, quality: { ...calibrated.quality!, matchedInlierCount: 1 } };
    expect(skyLayerSentence(one, photo(), SEARCHED)).toBe('Matched to 1 star in your photo.');
  });

  it('says a pose was lined up by taking a bright spot to be the Moon or the Sun, never that the spot is one (decision 5)', () => {
    // The fixture: 2.619 deg at the frame centre plus 0.0073 deg for the Moon's centroid, so about 3
    expect(skyLayerSentence(sample('pose_moon_level_head'), photo(), SEARCHED)).toBe(
      "Lined up by taking a bright spot to be the Moon, with the camera's field of view and a level head, within about 3 degrees; not checked against stars."
    );
    expect(skyLayerSentence(withPose({ method: 'moon_horizon' }), photo(), SEARCHED)).toBe(
      'Lined up by taking a bright spot to be the Moon, with the skyline taken to be level, within about 3 degrees; not checked against stars.'
    );
    expect(skyLayerSentence(withPose({ method: 'sun_horizon', beacon: 'sun' }), photo({ mediaKind: 'video' }), SEARCHED)).toBe(
      'Lined up by taking a bright spot to be the Sun, with the skyline taken to be level, within about 3 degrees; not checked against stars.'
    );
    for (const pose of [{}, { method: 'moon_horizon' }, { method: 'sun_horizon', beacon: 'sun' }] as Partial<PoseEstimate>[]) {
      for (const search of BOTH) {
        const line = skyLayerSentence(withPose(pose), photo(), search);
        // One plain sentence, and the Moon or the Sun only as what the bright spot was taken to be
        expect(line.match(/\./g)).toHaveLength(1);
        expect(line).toMatch(/^Lined up by taking a bright spot to be the (Moon|Sun), with /);
        expect(outsideTakenToBe(line)).not.toMatch(/Moon|Sun/);
        expect(line).not.toMatch(/from the (Moon|Sun)/);
      }
    }
  });

  it('rounds how far off a pose may be up to whole degrees, from both of its bounds', () => {
    // Rounded up, never to the nearest: 2.2 + 0.1 is about 3, not 2
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 2.2, beaconCentroidUncertaintyDeg: 0.1 }), photo(), SEARCHED)).toContain(
      ', within about 3 degrees; not checked against stars.'
    );
    // Both bounds count: 2.95 alone would round up to 3, with the centroid's 0.1 it is 4
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 2.95, beaconCentroidUncertaintyDeg: 0.1 }), photo(), SEARCHED)).toContain(
      ', within about 4 degrees; not checked against stars.'
    );
    expect(skyLayerSentence(withPose({ maxCenterOffsetDeg: 0.5, beaconCentroidUncertaintyDeg: 0.2 }), photo(), SEARCHED)).toContain(
      ', within about 1 degree; not checked against stars.'
    );
  });

  it('says a pointing without a pose estimate is rough and is not drawn over the photo', () => {
    expect(skyLayerSentence(sample('horizon_approximate'), photo(), SEARCHED)).toBe(
      'Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your photo.'
    );
    expect(skyLayerSentence(sample('horizon_approximate'), photo({ mediaKind: 'video' }), NOT_SEARCHED)).toBe(
      'Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your clip.'
    );
  });

  it('makes no lined-up claim for a pose estimate without the WCS it describes (decision 4, the surviving mutant)', () => {
    const { wcs: _wcs, ...poseWithoutWcs } = sample('pose_moon_level_head');
    // The contract refuses the shape, so it is built by hand and reaches the sentence unparsed, as a discovered
    // clip's solution does (apiClient.ts)
    expect(poseWithoutWcs.poseEstimate).toBeDefined();
    expect(ObservationSolutionSchema.safeParse(poseWithoutWcs).success).toBe(false);
    for (const search of BOTH) {
      // Nothing places the sky over the photo without the WCS, so the line says only that the predicted sky can be opened
      expect(skyLayerSentence(poseWithoutWcs as ObservationSolution, photo(), search)).toBe(openSky('photo'));
      const clipLine = skyLayerSentence(poseWithoutWcs as ObservationSolution, photo({ mediaKind: 'video' }), search);
      expect(clipLine).toBe(openSky('clip'));
      expect(clipLine).not.toMatch(/lined up|within about|degree|bright spot|Moon|Sun/i);
    }
  });

  it('says a predicted_only upload had too few points of light below the solver minimum, and that its predicted sky can be opened', () => {
    // The fixture detected none
    expect(sample('horizon_predicted_only').quality!.detectedCandidateCount).toBe(0);
    expect(skyLayerSentence(sample('horizon_predicted_only'), photo(), SEARCHED)).toBe(
      "Too few points of light to match to stars. You can open the sky predicted for your photo's time and place."
    );
    expect(skyLayerSentence(sample('horizon_predicted_only'), photo({ mediaKind: 'video' }), SEARCHED)).toBe(
      "Too few points of light to match to stars. You can open the sky predicted for your clip's time and place."
    );
  });

  it('says how many points of light were found when there were enough to try, never too few (verdict finding 2)', () => {
    const base = sample('horizon_predicted_only');
    const detected = (n: number): ObservationSolution => ({ ...base, quality: { ...base.quality!, detectedCandidateCount: n } });
    // services/science/solver.py stops before matching below 6 detections, its own minimum
    expect(skyLayerSentence(detected(5), photo(), SEARCHED)).toBe(
      "Too few points of light to match to stars. You can open the sky predicted for your photo's time and place."
    );
    expect(skyLayerSentence(detected(6), photo(), SEARCHED)).toBe(
      "Found 6 points of light, but could not match them to stars for sure. You can open the sky predicted for your photo's time and place."
    );
    // As the auditor's 40 synthetic detections reach it through the solver and complete_sky
    expect(skyLayerSentence(detected(40), photo({ mediaKind: 'video' }), SEARCHED)).toBe(
      "Found 40 points of light, but could not match them to stars for sure. You can open the sky predicted for your clip's time and place."
    );
    for (const n of [6, 7, 40, 1000]) expect(skyLayerSentence(detected(n), photo(), SEARCHED)).not.toMatch(/too few/i);
  });

  it('says the points of light were not matched when a searched solution carries no count', () => {
    const { quality: _quality, ...bare } = sample('horizon_predicted_only');
    expect(skyLayerSentence(bare as ObservationSolution, photo(), SEARCHED)).toBe(
      "The points of light could not be matched to stars. You can open the sky predicted for your photo's time and place."
    );
  });

  it('never says the predicted sky is being shown: nothing is drawn over the photo until the person opens it (verdict note b)', () => {
    for (const name of Object.keys(FIXTURE)) {
      for (const mediaKind of ['image', 'video'] as const) {
        for (const search of BOTH) {
          expect(skyLayerSentence(sample(name), photo({ mediaKind }), search), name).not.toMatch(/showing/i);
        }
      }
    }
  });

  it('makes no statement about points of light when no star search ran, whatever count the solution carries (decision 2)', () => {
    const base = sample('horizon_predicted_only');
    // The Moon and horizon solver of the laptop's clips writes 1 or 0 there, whether it found the Moon
    // (services/science/horizon_solver.py); no number in that place is a count of points of light
    for (const n of [0, 1, 5, 6, 40, 1000]) {
      const counted: ObservationSolution = { ...base, quality: { ...base.quality!, detectedCandidateCount: n } };
      expect(skyLayerSentence(counted, photo(), NOT_SEARCHED), `count ${n}`).toBe(openSky('photo'));
      expect(skyLayerSentence(counted, photo({ mediaKind: 'video' }), NOT_SEARCHED), `count ${n}`).toBe(openSky('clip'));
    }
    const { quality: _quality, ...bare } = base;
    expect(skyLayerSentence(bare as ObservationSolution, photo(), NOT_SEARCHED)).toBe(openSky('photo'));
  });

  it('PBT: only the star-search input decides whether points of light are spoken of, never the solver name or the count', () => {
    const base = sample('horizon_predicted_only');
    // The fixture's own solver names (a plate solver, the Moon and horizon solver, a hand-built sample) and any other text
    const solverNames = fc.oneof(
      fc.constantFrom(...Object.keys(FIXTURE).flatMap((name) => sample(name).solver?.name ?? [])),
      fc.string({ minLength: 1, maxLength: 40 })
    );
    fc.assert(
      fc.property(
        fc.nat({ max: 100_000 }),
        solverNames,
        fc.boolean(),
        fc.constantFrom<'image' | 'video'>('image', 'video'),
        (count, solverName, starSearchRan, mediaKind) => {
          const solution: ObservationSolution = {
            ...base,
            solver: { ...base.solver!, name: solverName },
            quality: { ...base.quality!, detectedCandidateCount: count },
          };
          const kind = mediaKind === 'video' ? 'clip' : 'photo';
          const line = skyLayerSentence(solution, photo({ mediaKind }), { starSearchRan });
          const found =
            count < 6 ? 'Too few points of light to match to stars.' : `Found ${count} points of light, but could not match them to stars for sure.`;
          expect(line).toBe(starSearchRan ? `${found} ${openSky(kind)}` : openSky(kind));
          if (!starSearchRan) expect(line).not.toMatch(/points? of light|found|too few|\d/i);
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          // The laptop's clips as they come: the Moon and horizon solver's name with its 1 or 0, no star search
          [1, 'AlbedianHorizonMoonPose', false, 'video'],
          [0, 'AlbedianHorizonMoonPose', false, 'video'],
          // The same name and counts said to be searched, and a plate solver's name said not to be: the input decides
          [0, 'AlbedianHorizonMoonPose', true, 'image'],
          [40, 'AlbedianTriangleSolver', false, 'image'],
          [40, 'AlbedianTriangleSolver', true, 'video'],
          // Either side of the solver's minimum of 6
          [5, 'AlbedianTriangleSolver', true, 'image'],
          [6, 'AlbedianTriangleSolver', true, 'image'],
        ],
      }
    );
  });

  it('reads the star-search input only for a predicted_only line: every other line is the same either way', () => {
    for (const name of Object.keys(FIXTURE)) {
      const solution = sample(name);
      for (const mediaKind of ['image', 'video'] as const) {
        const searched = skyLayerSentence(solution, photo({ mediaKind }), SEARCHED);
        const notSearched = skyLayerSentence(solution, photo({ mediaKind }), NOT_SEARCHED);
        if (solution.status === 'predicted_only') expect(searched, name).not.toBe(notSearched);
        else expect(searched, name).toBe(notSearched);
      }
    }
  });

  it('says which of the time and the place the file lacks when there is no sky (REQ-SKY-2.1)', () => {
    for (const name of ['plate_too_few_sources', 'plate_wcs_failed_verification']) {
      const unresolved = sample(name);
      expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: undefined }), SEARCHED)).toBe(
        'Your photo does not say when it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ observerLocation: undefined }), SEARCHED)).toBe(
        'Your photo does not say where it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: undefined, observerLocation: undefined }), SEARCHED)).toBe(
        'Your photo does not say when or where it was taken, so its sky cannot be shown.'
      );
      expect(skyLayerSentence(unresolved, photo({ mediaKind: 'video', capturedAtUTC: undefined }), SEARCHED)).toBe(
        'Your clip does not say when it was recorded, so its sky cannot be shown.'
      );
      const ambiguous = { ...unresolved, status: 'ambiguous' as const };
      expect(skyLayerSentence(ambiguous, photo({ observerLocation: undefined }), SEARCHED)).toBe(
        'Your photo does not say where it was taken, so its sky cannot be shown.'
      );
    }
  });

  it('says the moment is outside 1800 to 2050 at the ephemeris bounds, with the frame time added (REQ-SKY-2.3)', () => {
    const unresolved = sample('plate_too_few_sources');
    const outside = 'Your photo was taken outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown.';
    const inside = 'No sky could be worked out for your photo.';
    // services/science/ephemeris.py require_ephemeris_range: from 1800-01-01T00:00:00Z, until 2051-01-01T00:00:00Z
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '1799-12-31T23:59:59Z' }), SEARCHED)).toBe(outside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '1800-01-01T00:00:00Z' }), SEARCHED)).toBe(inside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '2050-12-31T23:59:59Z' }), SEARCHED)).toBe(inside);
    expect(skyLayerSentence(unresolved, photo({ capturedAtUTC: '2051-01-01T00:00:00Z' }), SEARCHED)).toBe(outside);
    // The moment is the solution's frame: a clip that starts inside and reaches its frame two seconds later is outside
    const late = { ...unresolved, frameTimeSec: 2 };
    expect(skyLayerSentence(late, photo({ capturedAtUTC: '2050-12-31T23:59:59Z' }), SEARCHED)).toBe(outside);
    expect(skyLayerSentence(unresolved, photo(), SEARCHED)).toBe(inside);
  });

  it('says a failed solve stopped, and a pending or missing solution is not worked out yet', () => {
    expect(solveStoppedSentence(photo())).toBe('The sky solve for your photo stopped before it finished.');
    expect(solveStoppedSentence(photo({ mediaKind: 'video' }))).toBe('The sky solve for your clip stopped before it finished.');
    expect(skyLayerSentence(sample('pending_unsolved'), photo(), SEARCHED)).toBe('The sky for your photo has not been worked out yet.');
    expect(skyLayerSentence(undefined, photo({ mediaKind: 'video' }), NOT_SEARCHED)).toBe(
      'The sky for your clip has not been worked out yet.'
    );
  });

  it('PBT: never says a raw status, the capture time, the place or the file name, nor that the sky is shown or a spot is the Moon', () => {
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
        fc.boolean(),
        (name, mediaKind, capturedAtUTC, observerLocation, jobState, starSearchRan) => {
          const solution = sample(name);
          const facts = { mediaKind, capturedAtUTC, observerLocation };
          // A failed job's line is the stopped one, whatever was found before it stopped (apiClient.ts)
          const sentence = jobState === 'failed' ? solveStoppedSentence(facts) : skyLayerSentence(solution, facts, { starSearchRan });
          expect(sentence).not.toMatch(RAW_STATUS);
          expect(sentence).not.toMatch(/\.(png|jpe?g|mov|mp4|heic)/i);
          expect(sentence).not.toMatch(/showing/i);
          expect(outsideTakenToBe(sentence)).not.toMatch(/Moon|Sun/);
          if (!starSearchRan && solution.status === 'predicted_only') expect(sentence).not.toMatch(/points? of light/i);
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
          ['plate_too_few_sources', 'image', '1750-06-01T00:00:00Z', SYNTHETIC_PLACE, undefined, true],
          ['plate_calibrated', 'video', SYNTHETIC_TIME, { latDeg: 12.34, lonDeg: -56.78 }, 'succeeded', true],
          ['pose_moon_level_head', 'image', SYNTHETIC_TIME, SYNTHETIC_PLACE, undefined, true],
          // A clip discovered on the laptop: no star search ran
          ['horizon_predicted_only', 'video', SYNTHETIC_TIME, SYNTHETIC_PLACE, undefined, false],
          ['horizon_predicted_only', 'image', SYNTHETIC_TIME, SYNTHETIC_PLACE, 'succeeded', true],
          ['plate_too_few_sources', 'video', SYNTHETIC_TIME, SYNTHETIC_PLACE, 'failed', false],
        ],
      }
    );
  });
});

describe('the lined-up layer in plain words (REQ-SKY-6.3, 6.4)', () => {
  it('names the basis from the method as what was taken to be so, and says it is drawn by the app, not measured, not checked (decision 5)', () => {
    const pose = sample('pose_moon_level_head').poseEstimate!;
    expect(poseBasisSentence(pose, 'video')).toBe(
      "Lined up by taking a bright spot to be the Moon, with the camera's field of view and a level head. Drawn by the app, not measured in your clip, and not checked against stars."
    );
    expect(poseBasisSentence({ ...pose, method: 'moon_horizon' }, 'image')).toBe(
      'Lined up by taking a bright spot to be the Moon, with the skyline taken to be level. Drawn by the app, not measured in your photo, and not checked against stars.'
    );
    expect(poseBasisSentence({ ...pose, method: 'sun_horizon', beacon: 'sun' }, 'image')).toBe(
      'Lined up by taking a bright spot to be the Sun, with the skyline taken to be level. Drawn by the app, not measured in your photo, and not checked against stars.'
    );
  });

  it('gives the uncertainty in whole degrees rounded up, names the bright spot as taken to be the beacon, and says what the rings show', () => {
    const pose = sample('pose_moon_level_head').poseEstimate!;
    expect(withinAboutDeg(pose)).toBe(3);
    expect(withinAboutDeg({ ...pose, maxCenterOffsetDeg: 2.95, beaconCentroidUncertaintyDeg: 0.1 })).toBe(4);
    expect(withinAboutDeg({ ...pose, maxCenterOffsetDeg: 2.2, beaconCentroidUncertaintyDeg: 0.1 })).toBe(3);
    expect(poseUncertaintySentence(pose)).toBe(
      'Within about 3 degrees at the middle of the frame, less near the bright spot taken to be the Moon and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold.'
    );
    expect(poseUncertaintySentence({ ...pose, method: 'sun_horizon', beacon: 'sun', maxCenterOffsetDeg: 0.4, beaconCentroidUncertaintyDeg: 0.1 })).toBe(
      'Within about 1 degree at the middle of the frame, less near the bright spot taken to be the Sun and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold.'
    );
  });

  it('never states in the disclosure that the bright spot is the Moon or the Sun, for any method (decision 5)', () => {
    const pose = sample('pose_moon_level_head').poseEstimate!;
    for (const variant of [{}, { method: 'moon_horizon' }, { method: 'sun_horizon', beacon: 'sun' }] as Partial<PoseEstimate>[]) {
      for (const mediaKind of ['image', 'video'] as const) {
        const lines = `${poseBasisSentence({ ...pose, ...variant }, mediaKind)} ${poseUncertaintySentence({ ...pose, ...variant })}`;
        expect(lines).toMatch(/^Lined up by taking a bright spot to be the (Moon|Sun), with /);
        expect(outsideTakenToBe(lines)).not.toMatch(/Moon|Sun/);
        expect(lines).not.toMatch(/from the (Moon|Sun)|near the (Moon|Sun)/);
      }
    }
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
