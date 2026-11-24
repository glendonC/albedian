import { STAR_CATALOG_DISTANCE_METHODS, refusalSentence, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { dispatchAlbedianAction } from '../index';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions';

/**
 * flight-rule-1: space.enter and space.viewpoint read the contract's flight rule (`flightRefusal`, DECIDED-BY-LEAD 24 Nov
 * 12:55). A predicted star with a catalog reference and a star catalog's distance is flown to as the simulated catalog
 * view, and its finding stays predicted; the Moon, a planet and a target with no catalog distance are refused in the
 * words of UIUX-predflight-READY.md item 5.
 */
const seed = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

const ZOSMA_ID = 'finding-predicted-star-zosma';

function zosma(session: ObservationSession, method = 'geometric_parallax'): CelestialFinding {
  const solution = session.observationSolution!;
  return {
    id: ZOSMA_ID,
    observationSolutionId: solution.id,
    evidenceClass: 'predicted',
    title: 'Zosma',
    subtitle: 'Predicted · Hipparcos 2007 · HIP 54872 · test data',
    catalogRef: {
      id: 'hip-54872', provider: 'Hipparcos', releaseVersion: '2007', catalogSourceId: 'HIP 54872', commonName: 'Zosma',
      constellation: 'Leo', objectType: 'star', coordinateEpoch: 'J1991.25', raDeg: 168.527, decDeg: 20.524, apparentMagnitude: 2.56,
      citation: 'Test row keyed by HIP 54872; the values are typed test data, not read from a catalog release',
    },
    skyPosition: { raDeg: 168.527, decDeg: 20.524, uncertaintyArcsec: 1800 },
    inferredDistancePc: { lower: 17.6, median: 17.9, upper: 18.2, method },
    evidence: {
      id: 'ev-predicted-zosma', evidenceClass: 'predicted', verificationMethod: 'Synthetic test position: no sky model was computed',
      sourceAssetSha256: solution.sourceSha256, catalogReferenceId: 'hip-54872', limitations: ['Synthetic test data'],
      verifiedByIndependentHoldout: false,
    },
  };
}

function withFindings(session: ObservationSession, extra: CelestialFinding[]): ObservationSession {
  const solution = session.observationSolution!;
  return { ...session, observationSolution: { ...solution, findings: [...solution.findings, ...extra] } };
}

function moon(session: ObservationSession): CelestialFinding {
  const solution = session.observationSolution!;
  return {
    id: 'finding-predicted-moon', observationSolutionId: solution.id, evidenceClass: 'predicted', title: 'Moon (Predicted Ephemeris)',
    subtitle: 'Predicted Moon · test data', skyPosition: { raDeg: 40, decDeg: 10, uncertaintyArcsec: 30 },
    evidence: {
      id: 'ev-ephem-moon', evidenceClass: 'predicted', verificationMethod: 'Synthetic test position: no ephemeris was computed',
      sourceAssetSha256: solution.sourceSha256, limitations: ['Synthetic test data'], verifiedByIndependentHoldout: false,
    },
  };
}

function enter(session: ObservationSession, findingId: string) {
  return dispatchAlbedianAction(session, {
    type: 'space.enter', requestId: `req_enter_${findingId}`, expectedViewRevision: session.viewRevision, findingId,
    distancePolicy: 'measured_or_estimated',
  });
}

function viewpoint(session: ObservationSession, targetFindingId: string) {
  return dispatchAlbedianAction(session, {
    type: 'space.viewpoint', requestId: `req_view_${targetFindingId}`, expectedViewRevision: session.viewRevision, targetFindingId,
    units: 'pc', offsetParsec: [1, 0, 0], trajectory: 'orbit',
  });
}

const classes = (session: ObservationSession) => session.observationSolution!.findings.map((f) => [f.id, f.evidenceClass]);

describe('@albedian/observation: the flight rule in space.enter and space.viewpoint', () => {
  it('flies to a predicted star with a catalog distance on a sky that was not matched, and it stays predicted', () => {
    const base = withFindings(createSyntheticUnresolvedSession(), []);
    const session = withFindings(base, [zosma(base)]);
    const before = structuredClone(session.observationSolution!.findings);
    for (const result of [enter(session, ZOSMA_ID), viewpoint(session, ZOSMA_ID)]) {
      expect(result.outcome.status).toBe('completed');
      expect(result.session.machines.navigation).toBe('space');
      expect(result.session.spatialView.mode).toBe('catalog_3d');
      expect(result.session.selectedFindingId).toBe(ZOSMA_ID);
      expect(result.session.observationSolution!.findings).toEqual(before);
      const flown = result.session.observationSolution!.findings.find((f) => f.id === ZOSMA_ID)!;
      expect(flown.evidenceClass).toBe('predicted');
    }
  });

  it('still flies to a matched star with a catalog distance', () => {
    const session = createSyntheticCalibratedSession();
    expect(enter(session, 'finding_betelgeuse').outcome.status).toBe('completed');
  });

  it('refuses a predicted star with no catalog distance in the recorded words', () => {
    const session = createSyntheticUnresolvedSession();
    const result = enter(session, 'finding_pred_vega');
    expect(result.outcome).toMatchObject({
      status: 'rejected', reason: 'no_distance_estimate',
      explanation: refusalSentence({ kind: 'no_catalog_distance', name: 'Vega' }),
    });
    expect(result.outcome.status === 'rejected' && result.outcome.explanation).toBe('Vega has no distance in the app\'s catalogs, so it cannot be flown to.');
    expect(result.session).toBe(session);
  });

  it('refuses the Moon, a planet or the Sun as beyond the flight, by id', () => {
    const base = createSyntheticUnresolvedSession();
    const session = withFindings(base, [moon(base)]);
    for (const result of [enter(session, 'finding-predicted-moon'), viewpoint(session, 'finding-predicted-moon')]) {
      expect(result.outcome).toMatchObject({
        status: 'rejected', reason: 'no_distance_estimate', explanation: 'The flight goes only to stars beyond our solar system.',
      });
      expect(result.session).toBe(session);
    }
  });

  it('refuses a predicted star whose distance is not a star catalog\'s, and one observed in the pixels', () => {
    const base = createSyntheticUnresolvedSession();
    const session = withFindings(base, [zosma(base, 'inverse_parallax')]);
    expect(enter(session, ZOSMA_ID).outcome).toMatchObject({
      status: 'rejected', explanation: 'Zosma has no distance in the app\'s catalogs, so it cannot be flown to.',
    });
    expect(viewpoint(session, ZOSMA_ID).outcome).toMatchObject({ status: 'rejected', reason: 'no_distance_estimate' });
    expect(enter(session, 'finding_obs_overcast_1').outcome).toMatchObject({
      status: 'rejected', explanation: 'Field Source #1 has no distance in the app\'s catalogs, so it cannot be flown to.',
    });
  });

  it('never changes any finding or its class, whatever is asked for', () => {
    const base = createSyntheticUnresolvedSession();
    const unresolved = withFindings(base, [zosma(base), moon(base)]);
    const calibrated = createSyntheticCalibratedSession();
    const sessions = [unresolved, calibrated];
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 1 }),
        fc.nat(),
        fc.boolean(),
        fc.constantFrom(...STAR_CATALOG_DISTANCE_METHODS),
        (which, pick, asViewpoint, _method) => {
          const session = sessions[which]!;
          const findings = session.observationSolution!.findings;
          const id = findings[pick % findings.length]!.id;
          const before = classes(session);
          const result = asViewpoint ? viewpoint(session, id) : enter(session, id);
          expect(classes(result.session)).toEqual(before);
          const flown = findings.find((f) => f.id === id)!;
          if (result.outcome.status === 'completed') {
            expect(['catalog_matched', 'predicted']).toContain(flown.evidenceClass);
          }
        }
      ),
      { seed, numRuns: 200 }
    );
  });
});
