import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { skyLayerSentence, unmatchedSentence } from '../../services/skyLayerWords.js';
import { poseSession } from '../../components/__tests__/poseSessions.js';
import { ESTIMATE_MARKS_LINE, evidenceCounts, revealStatus } from '../revealStatus.js';

// The reveal's status block and legend (reveal, approved 2026-11-12; estimate and cautions, decided the same night;
// the lead's answer 1 of 2026-11-18 for a sky that is neither matched nor lined up). Every word and number is the
// solution's own.

const samples = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, ObservationSolution>;

function sessionWith(solution: ObservationSolution, over: Partial<ObservationSession> = {}): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', width: 3024, height: 4032, durationMs: 0 },
    observationSolution: solution,
    spatialView: { ...base.spatialView, mode: 'source_2d' },
    ...over,
  } as ObservationSession;
}
const calibrated = samples['plate_calibrated']!;
const withQuality = (quality: Partial<NonNullable<ObservationSolution['quality']>>): ObservationSolution => ({
  ...calibrated,
  quality: { ...calibrated.quality!, ...quality },
});

describe('revealStatus: a calibrated photo, as the approved picture words it', () => {
  it("writes the solver's own counts: matched stars, and held-out stars that landed", () => {
    const status = revealStatus(sessionWith(withQuality({ matchedInlierCount: 17, heldOutInliers: 11, heldOutPairCount: 19 })));
    expect(status.tag).toBeNull();
    expect(status.lines).toEqual(['Calibrated · 17 matched', '11 of 19 held-out stars landed']);
  });

  it('groups thousands, and leaves the held-out line out when no star was held out', () => {
    const status = revealStatus(sessionWith(withQuality({ matchedInlierCount: 1043, heldOutInliers: 0, heldOutPairCount: 0 })));
    expect(status.lines).toEqual(['Calibrated · 1,043 matched']);
  });

  it("puts the solve's warnings under it, word for word and in order, and none when there is none", () => {
    const warned = { ...calibrated, warnings: ['The Sun was about 36 degrees below the horizon: night.', 'A second warning.'] };
    expect(revealStatus(sessionWith(warned)).warnings).toEqual(['The Sun was about 36 degrees below the horizon: night.', 'A second warning.']);
    expect(revealStatus(sessionWith({ ...calibrated, warnings: [] })).warnings).toEqual([]);
  });
});

describe('revealStatus: not matched to stars', () => {
  it.each(Object.keys(samples).filter((key) => samples[key]!.status !== 'calibrated'))(
    "says the app's own sentence for %s, with no tag, when no pose lines the sky up over the photo",
    (key) => {
      const solution = { ...samples[key]!, poseEstimate: undefined } as ObservationSolution;
      const session = sessionWith(solution);
      const status = revealStatus(session);
      expect(status.tag).toBeNull();
      // A predicted sky that can be opened has its own button, so the sentence for a star search leaves out "You can open"
      expect(status.lines).toEqual([
        solution.status === 'predicted_only' ? unmatchedSentence(solution) : skyLayerSentence(solution, session.sourceAsset, { starSearchRan: true }),
      ]);
      expect(status.warnings).toEqual(solution.warnings ?? []);
    }
  );

  it('never calls a photo calibrated on its status word alone: without a verified solve it gets the plain sentence', () => {
    const unverified = withQuality({ passedIndependentVerification: false });
    const status = revealStatus(sessionWith(unverified));
    expect(status.lines.join(' ')).not.toContain('Calibrated');
  });

  it('never calls a photo calibrated on a passed check alone either: the status must say calibrated too', () => {
    // The contract refuses such a solution, so it is given after the session is built: the screen checks for itself
    const session = sessionWith(calibrated);
    const odd = { ...session, observationSolution: { ...calibrated, status: 'ambiguous' } } as ObservationSession;
    expect(revealStatus(odd).lines.join(' ')).not.toContain('Calibrated');
  });

  it('speaks of points of light only when a star search ran', () => {
    const predictedOnly = Object.values(samples).find((s) => s.status === 'predicted_only');
    if (!predictedOnly) return;
    const session = sessionWith({ ...predictedOnly, poseEstimate: undefined } as ObservationSolution);
    expect(revealStatus(session, false).lines).toEqual([skyLayerSentence(session.observationSolution, session.sourceAsset, { starSearchRan: false })]);
  });

  it('has nothing to say before there is a solution', () => {
    const session = sessionWith(calibrated, { observationSolution: undefined });
    expect(revealStatus(session).lines).toEqual([skyLayerSentence(undefined, session.sourceAsset, { starSearchRan: true })]);
  });
});

describe('revealStatus: which layout, and whether a predicted sky can be opened (no-match reveal, approved 2026-11-18 20:37)', () => {
  const predictedOnly = samples['horizon_predicted_only']!;
  const found = (n: number): ObservationSolution => ({ ...predictedOnly, poseEstimate: undefined, quality: { ...predictedOnly.quality!, detectedCandidateCount: n } }) as ObservationSolution;

  it('says how many points of light were found, with a thousands comma, and offers the predicted sky', () => {
    const status = revealStatus(sessionWith(found(31371)));
    expect(status.kind).toBe('unmatched');
    expect(status.tag).toBeNull();
    expect(status.lines).toEqual(['Found 31,371 points of light, but could not match them to stars for sure.']);
    expect(status.predictedSky).toBe(true);
    expect(revealStatus(sessionWith(found(5))).lines).toEqual(['Too few points of light to match to stars.']);
  });

  it('keeps the whole sentence, "You can open" too, when no star search ran', () => {
    const session = sessionWith(found(31371));
    expect(revealStatus(session, false).lines).toEqual([skyLayerSentence(session.observationSolution, session.sourceAsset, { starSearchRan: false })]);
    expect(revealStatus(session, false).predictedSky).toBe(true);
  });

  it('offers the predicted sky for a rough line-up with no pose, as main did, in its own sentence', () => {
    const rough = { ...samples['horizon_approximate']!, poseEstimate: undefined } as ObservationSolution;
    const session = sessionWith(rough);
    const status = revealStatus(session);
    expect(status.kind).toBe('unmatched');
    expect(status.lines).toEqual([skyLayerSentence(rough, session.sourceAsset, { starSearchRan: true })]);
    expect(status.predictedSky).toBe(true);
  });

  it('offers no predicted sky where none can be opened: unresolved, pending, no solution, or calibrated but not verified', () => {
    for (const key of ['plate_too_few_sources', 'plate_wcs_failed_verification', 'pending_unsolved']) {
      const status = revealStatus(sessionWith(samples[key]!));
      expect(status.kind, key).toBe('unmatched');
      expect(status.predictedSky, key).toBe(false);
    }
    expect(revealStatus(sessionWith(calibrated, { observationSolution: undefined })).predictedSky).toBe(false);
    const unverified = revealStatus(sessionWith(withQuality({ passedIndependentVerification: false })));
    expect(unverified.kind).toBe('unmatched');
    expect(unverified.predictedSky).toBe(false);
  });

  it('keeps the calibrated and the estimate layouts, neither with the predicted sky button', () => {
    expect(revealStatus(sessionWith(calibrated))).toMatchObject({ kind: 'calibrated', predictedSky: false });
    const estimate = revealStatus(poseSession());
    expect(estimate.kind).toBe('estimate');
    expect(estimate.predictedSky).toBe(false);
  });
});

describe('the estimate line and the legend', () => {
  it('keeps the decided line for a sky lined up by an estimate, word for word', () => {
    expect(ESTIMATE_MARKS_LINE).toBe('No star in it is matched yet; every mark is drawn from the catalog.');
  });

  it("counts the photo's findings by class, in the approved order, and lists only the classes it has", () => {
    const finding = (evidenceClass: string, n: number) => Array.from({ length: n }, (_, i) => ({ ...calibrated.findings[0]!, id: `finding-${evidenceClass}-${i}`, evidenceClass }));
    const mixed = { ...calibrated, findings: [...finding('predicted', 45), ...finding('observed', 1204), ...finding('catalog_matched', 17)] } as ObservationSolution;
    expect(evidenceCounts(mixed)).toEqual([
      { evidenceClass: 'observed', count: 1204, text: '1,204' },
      { evidenceClass: 'catalog_matched', count: 17, text: '17' },
      { evidenceClass: 'predicted', count: 45, text: '45' },
    ]);
    const unmatched = { ...calibrated, findings: [...finding('observed', 31371), ...finding('predicted', 471)] } as ObservationSolution;
    expect(evidenceCounts(unmatched).map((row) => [row.evidenceClass, row.text])).toEqual([
      ['observed', '31,371'],
      ['predicted', '471'],
    ]);
    expect(evidenceCounts({ ...calibrated, findings: [] })).toEqual([]);
    expect(evidenceCounts(undefined)).toEqual([]);
  });
});

// C5, decided by the lead (UIUX-words-DECIDED-BY-LEAD.md): the note for a file with no time zone shows its first
// sentence only; the whole note stays in the solution's record
import { onScreenWarning } from '../revealStatus.js';
describe('the missing time zone note on screen (C5)', () => {
  const NOTE =
    "The file names no time zone for its capture time: it was taken from the time zone at the photo's GPS position, which agrees with its GPS time stamp. The capture time is taken to be within 8 s, a second plus how far the camera's clock and the GPS time stamp differ.";
  it('keeps the first sentence of that note, and every other warning whole', () => {
    expect(onScreenWarning(NOTE)).toBe("The file names no time zone for its capture time: it was taken from the time zone at the photo's GPS position, which agrees with its GPS time stamp.");
    const other = 'The Sun was about 41 degrees below the horizon: night. By the USNO definitions it is dark.';
    expect(onScreenWarning(other)).toBe(other);
    expect(onScreenWarning('The file names no time zone for its capture time.')).toBe('The file names no time zone for its capture time.');
  });
});

describe("the iPhone record's warnings on screen (C13, UIUX-pointingwords-DECIDED-BY-LEAD.md)", () => {
  it('shows the first sentence only of each lent-moment warning; the second stays in the record', () => {
    expect(
      onScreenWarning("The photo's file has no time or place, so the sky is predicted for the time your iPhone recorded, by its own clock, and where it was, to within 8 m. That can move the predicted sky by up to 0.3 arcseconds.")
    ).toBe("The photo's file has no time or place, so the sky is predicted for the time your iPhone recorded, by its own clock, and where it was, to within 8 m.");
    expect(
      onScreenWarning("The photo's file has no place, so the sky is predicted for where your iPhone was, to within 5,000 m, the rough location you allowed. That can move the predicted sky by up to 2.1 arcseconds.")
    ).toBe("The photo's file has no place, so the sky is predicted for where your iPhone was, to within 5,000 m, the rough location you allowed.");
    // One sentence, whole
    const timeOnly = "The photo's file has no time, so the sky is predicted for the time your iPhone recorded, by its own clock.";
    expect(onScreenWarning(timeOnly)).toBe(timeOnly);
  });

  it('leaves the record-not-used note and every other warning whole', () => {
    const notUsed = 'The iPhone record stored with this photo belongs to another photo, so it was not used.';
    expect(onScreenWarning(notUsed)).toBe(notUsed);
    const two = 'The photo has no time. Something else follows.';
    expect(onScreenWarning(two)).toBe(two);
    // Only a note that starts so: the words inside another warning do not cut it
    const inside = "Read with care: the photo's file has no lens data. The field of view was measured instead.";
    expect(onScreenWarning(inside)).toBe(inside);
  });
});

// C14 (UIUX-countwords-DECIDED-BY-LEAD.md): the warning when a floor set a frame's noise (services/science/detector.py
// NOISE_LIMIT_WORDS["noise_floor"]) shows its first sentence on screen; the second stays in the solution's record (C5)
describe("the count warning's words on screen (C14)", () => {
  const SCREEN =
    "This photo's sky is so even that the smallest steps in its brightness can pass for points of light, so some of the points counted are those steps, not stars.";
  const RECORD = "The detector takes one brightness step as the sky's noise, so the count's false rate is not controlled.";

  it('shows the first sentence only of the noise-floor warning', () => {
    expect(onScreenWarning(`${SCREEN} ${RECORD}`)).toBe(SCREEN);
    expect(onScreenWarning(SCREEN)).toBe(SCREEN);
  });

  it("leaves the near-black warning in the engine's old words, one sentence with no decided cut, whole", () => {
    const nearBlack =
      "The photo's sky sits near its darkest value, where the black clip cuts the noise; on such a sky the detector finds more points on pure noise than its stated rate, so the count of detected points is points above the threshold on a frame whose false rate is not controlled.";
    expect(onScreenWarning(nearBlack)).toBe(nearBlack);
  });

  it("cuts the job's own warning as the reveal shows it", () => {
    const base = createSyntheticCalibratedSession();
    const solution = { ...base.observationSolution, warnings: [`${SCREEN} ${RECORD}`] } as ObservationSolution;
    const session: ObservationSession = { ...base, observationSolution: solution };
    expect(revealStatus(session).warnings).toContain(SCREEN);
    expect(revealStatus(session).warnings.join(' ')).not.toContain(RECORD);
  });
});

// C25 (UIUX-nearblackwords-READY.md): the near-black count warning (services/science/detector.py
// NOISE_LIMIT_WORDS["near_black_clip"]) in C14's shape shows its first sentence on screen; the second stays in the record (C5)
describe("the near-black count warning's words on screen (C25)", () => {
  const SCREEN =
    "This photo's sky is so close to black that its faintest noise is cut off, so some of the points counted may be that noise, not stars.";
  const RECORD =
    "On such a sky the detector finds more points on pure noise than its stated rate, so the count's false rate is not controlled.";

  it('shows the first sentence only of the near-black warning, and keeps it whole when it stands alone', () => {
    expect(onScreenWarning(`${SCREEN} ${RECORD}`)).toBe(SCREEN);
    expect(onScreenWarning(SCREEN)).toBe(SCREEN);
  });

  it('leaves the second sentence whole when it stands alone, since only the first opens the cut', () => {
    expect(onScreenWarning(RECORD)).toBe(RECORD);
  });

  it("cuts the job's own warning as the reveal shows it, the record keeping both sentences", () => {
    const base = createSyntheticCalibratedSession();
    const solution = { ...base.observationSolution, warnings: [`${SCREEN} ${RECORD}`] } as ObservationSolution;
    const session: ObservationSession = { ...base, observationSolution: solution };
    expect(revealStatus(session).warnings).toContain(SCREEN);
    expect(revealStatus(session).warnings.join(' ')).not.toContain(RECORD);
    expect(solution.warnings).toEqual([`${SCREEN} ${RECORD}`]);
  });
});

// C22 (UIUX-blindwords-READY.md, decided by the lead 17:08; SCIENCE-INGEST's blind-words): when the stars alone lined up
// a photo whose moment the iPhone lent, the warning shows its first sentence; the second stays in the record (C5)
describe("the moment-blind warning's words on screen (C22)", () => {
  const RECORD = 'A search at that moment matched nothing, so it may be off; the stars were matched without it.';
  const SCREENS = [
    'The stars alone lined this photo up; what is predicted on it is drawn for the time your iPhone recorded, which the stars do not confirm.',
    'The stars alone lined this photo up; what is predicted on it is drawn for where your iPhone was, which the stars do not confirm.',
    'The stars alone lined this photo up; what is predicted on it is drawn for the time your iPhone recorded and where it was, which the stars do not confirm.',
  ];

  it('shows the first sentence only, in each case of what was lent', () => {
    for (const screen of SCREENS) expect(onScreenWarning(`${screen} ${RECORD}`)).toBe(screen);
  });
});

// C21 (UIUX-skylinewords-READY.md) and C23 (UIUX-sunwords-READY.md): a pose's refusal, under the family's prefix, shows its
// reason; the numbers that follow in a second sentence stay in the solution's record (C5's rule)
describe("a pose's refusal on screen", () => {
  it("shows C21's skyline refusal by its reason only", () => {
    const record =
      "The sky is not lined up over the picture: no skyline was found to line the Moon up with. A skyline here is a row 38 to 72 percent of the way down below which the picture turns brighter by at least 6 times the sky's noise.";
    expect(onScreenWarning(record)).toBe('The sky is not lined up over the picture: no skyline was found to line the Moon up with.');
  });

  it("shows C23's Sun refusals by their reasons only", () => {
    expect(onScreenWarning('The sky is not lined up over the picture: the camera\'s clock or its time zone looks wrong. The Sun stands about 14.1 degrees above the skyline in the picture, but was 9.9 degrees up at the recorded time.')).toBe(
      "The sky is not lined up over the picture: the camera's clock or its time zone looks wrong."
    );
    expect(onScreenWarning("The sky is not lined up over the picture: the brightest glare is too wide to be the Sun's. It is wider than 8 times the Sun's disk through the file's own 26 mm lens.")).toBe(
      "The sky is not lined up over the picture: the brightest glare is too wide to be the Sun's."
    );
  });

  it('keeps a one-sentence refusal whole, numbers in it and all', () => {
    for (const whole of [
      'The sky is not lined up over the picture: the Moon was not more than 5 degrees above the horizon.',
      'The sky is not lined up over the picture: the fit found no camera that puts the Moon on the bright spot at (412, 233) px from the left and top with the skyline at row 610 level within 0.5 px.',
    ])
      expect(onScreenWarning(whole)).toBe(whole);
  });
});
