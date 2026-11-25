import { describe, expect, it } from 'vitest';
import { flightRefusal } from '@albedian/contracts';
import { POSE, poseSession, sample } from '../../components/__tests__/poseSessions.js';
import { projectThroughPose } from '../../components/poseProjection.js';
import { starCardFacts } from '../starCardFacts.js';
import { realSkyGrid } from '../realSkyGrid.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parsePanoramaRegistration } from '../../spatial/sky/skyRegistration.js';

// go-there-predicted-1 (LEAD ANSWER 12:55; ruling A with the 19:44 batch line): the card's Go there for a predicted star
// on a photo with a pose estimate, and the flight's glow drawn through that pose's own WCS, ESO's photograph never.

const here = path.dirname(fileURLToPath(import.meta.url));
const registration = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../public/textures/eso0932a_registration.json'), 'utf8')));
const star = POSE.findings.find((f) => f.evidenceClass === 'predicted' && flightRefusal(f) === null && projectThroughPose(f, POSE) !== null)!;

describe('the star card on a photo with a pose estimate', () => {
  it('offers Go there for a predicted star whose ring the pose draws, given the solution, and not without it', () => {
    expect(starCardFacts(star, null, POSE).canGoThere).toBe(true);
    expect(starCardFacts(star, null).canGoThere).toBe(false);
    expect(starCardFacts(star, null, POSE).evidenceClass).toBe('predicted');
  });
});

describe('realSkyGrid through a pose estimate', () => {
  it("maps the window through the pose's WCS only when asked (the flight's glow), never by default (ESO's photograph)", () => {
    const session = poseSession(POSE);
    expect(realSkyGrid(session, 1440, 900, registration)).toBeNull();
    const grid = realSkyGrid(session, 1440, 900, registration, 8, undefined, true);
    expect(grid).not.toBeNull();
    expect(grid!.cols * grid!.rows).toBeGreaterThan(0);
  });
  it('takes no pose where there is none (a predicted sky alone)', () => {
    expect(realSkyGrid(poseSession(sample('horizon_predicted_only')), 1440, 900, registration, 8, undefined, true)).toBeNull();
  });
});
