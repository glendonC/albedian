import { describe, expect, it } from 'vitest';
import { flightRefusal, type ObservationSolution } from '@albedian/contracts';
import { POSE, sample } from './poseSessions.js';
import { askSuggestionsFor, findingCanEnterSpace, TAKE_ME_THERE } from '../askSuggestions.js';
import { projectThroughPose } from '../poseProjection.js';

// go-there-predicted-1 (LEAD ANSWER 12:55, the 19:44 batch line; UIUX-predflight-READY.md): the web gate reads the
// contract's flight rule, and a predicted star also needs a start view: its ring shown through the photo's pose estimate.

const flyablePredicted = (solution: ObservationSolution) => solution.findings.filter((f) => f.evidenceClass === 'predicted' && flightRefusal(f) === null);

describe('findingCanEnterSpace', () => {
  it('opens a predicted star the rule allows whose ring the pose estimate draws, given the solution', () => {
    const shown = flyablePredicted(POSE).filter((f) => projectThroughPose(f, POSE) !== null);
    expect(shown.length).toBeGreaterThan(0);
    for (const f of shown) {
      expect(findingCanEnterSpace(f, POSE)).toBe(true);
      expect(askSuggestionsFor(f, POSE)).toContain(TAKE_ME_THERE);
    }
  });

  it('keeps a predicted star closed without the solution, as before, and where its ring is not drawn', () => {
    for (const f of flyablePredicted(POSE)) {
      expect(findingCanEnterSpace(f)).toBe(false);
      expect(askSuggestionsFor(f)).not.toContain(TAKE_ME_THERE);
      if (projectThroughPose(f, POSE) === null) expect(findingCanEnterSpace(f, POSE)).toBe(false);
    }
  });

  it('keeps a predicted sky with no pose closed (its card waits for go-there-predicted-2)', () => {
    const predictedOnly = sample('horizon_predicted_only');
    for (const f of flyablePredicted(predictedOnly)) expect(findingCanEnterSpace(f, predictedOnly)).toBe(false);
  });

  it('refuses a star the rule refuses though its ring shows: an ephemeris distance, or no catalog reference', () => {
    const star = flyablePredicted(POSE).find((f) => projectThroughPose(f, POSE) !== null)!;
    const ephemeris = { ...star, inferredDistancePc: { ...star.inferredDistancePc!, method: 'ephemeris_au' } } as typeof star;
    const unreferenced = { ...star, catalogRef: undefined } as typeof star;
    for (const f of [ephemeris, unreferenced]) {
      expect(projectThroughPose(f, POSE)).not.toBeNull();
      expect(flightRefusal(f)).not.toBeNull();
      expect(findingCanEnterSpace(f, POSE)).toBe(false);
    }
  });

  it('keeps a flyable predicted star closed when its ring falls outside the photo', () => {
    const star = flyablePredicted(POSE).find((f) => projectThroughPose(f, POSE) !== null)!;
    // The same star placed on the far side of the sky from where the pose points
    const away = { ...star, skyPosition: { ...star.skyPosition!, raDeg: (star.skyPosition!.raDeg + 180) % 360, decDeg: -star.skyPosition!.decDeg } } as typeof star;
    expect(flightRefusal(away)).toBeNull();
    expect(projectThroughPose(away, POSE)).toBeNull();
    expect(findingCanEnterSpace(away, POSE)).toBe(false);
  });

  it('refuses what the rule refuses: the Sun, the Moon, a planet, a star with no catalog distance', () => {
    for (const f of POSE.findings.filter((x) => flightRefusal(x) !== null)) expect(findingCanEnterSpace(f, POSE)).toBe(false);
  });

  it('opens a matched star with a catalog distance as before, with or without the solution', () => {
    const solved = sample('plate_calibrated');
    const matched = solved.findings.filter((f) => f.evidenceClass === 'catalog_matched' && flightRefusal(f) === null);
    expect(matched.length).toBeGreaterThan(0);
    for (const f of matched) {
      expect(findingCanEnterSpace(f)).toBe(true);
      expect(findingCanEnterSpace(f, solved)).toBe(true);
    }
    expect(findingCanEnterSpace(null)).toBe(false);
  });
});
