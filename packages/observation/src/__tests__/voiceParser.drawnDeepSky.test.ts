import fs from 'node:fs';
import path from 'node:path';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { describe, expect, it } from 'vitest';
import { buildActionContext, parseUtteranceToAction } from '../index';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

/**
 * deepsky-answer-1 (FIELD's break of 24 Nov): a southern showpiece the photo draws is a finding voice reaches by the
 * names people say, as a star's name is, instead of the "voice does not reach deep-sky objects" refusal. The findings
 * are the real ones the solve of the public corpus photo rp-b-01 served (tests/fixtures/deepsky_drawn_findings.json).
 */
const FIXTURE = path.resolve(__dirname, '../../../../tests/fixtures/deepsky_drawn_findings.json');
const drawn = (JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as { findings: CelestialFinding[] }).findings;
const OMEGA = 'finding-openngc-NGC5139';
const JEWEL = 'finding-openngc-NGC4755';
const LMC = 'finding-openngc-ESO056-115';

function sessionWith(extra: CelestialFinding[]): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const solution = base.observationSolution!;
  return { ...base, observationSolution: { ...solution, findings: [...solution.findings, ...extra] } };
}

const parse = (text: string, session: ObservationSession) => parseUtteranceToAction(text, buildActionContext(session), session);
const target = (result: ReturnType<typeof parse>) =>
  result.action && 'findingId' in result.action ? [result.action.type, result.action.findingId] : [result.reason, result.refusal?.kind];

describe('@albedian/observation voice parser: a drawn deep-sky object is reached by its spoken names', () => {
  const session = sessionWith(drawn);

  it.each([
    ['select Omega Centauri', 'finding.select', OMEGA],
    ['select omega cen', 'finding.select', OMEGA],
    ['tell me about the Jewel Box', 'finding.select', JEWEL],
    ['select the kappa crucis cluster', 'finding.select', JEWEL],
    ["select Herschel's Jewel Box", 'finding.select', JEWEL],
    ['select the Large Magellanic Cloud', 'finding.select', LMC],
    ['select the lmc', 'finding.select', LMC],
    ['fly to Omega Centauri', 'space.enter', OMEGA],
    ['what is Omega Centauri', 'finding.describe', OMEGA],
  ])('%s', (text, type, id) => {
    expect(target(parse(text, session))).toEqual([type, id]);
  });

  it('refuses one the photo does not draw as not in this sky, the recorded C33 words', () => {
    const bare = sessionWith(drawn.filter((f) => f.id !== OMEGA));
    const omega = parse('select Omega Centauri', bare);
    expect(omega.refusal).toEqual({ kind: 'not_in_this_sky', said: 'omega centauri', name: 'Omega Centauri' });
    expect(omega.reason).toBe('ambiguous_target');
    // The synthetic session is a clip, the decided noun (C35)
    expect(omega.error).toBe("Omega Centauri was not found in this clip's sky.");
    // In a sky not worked out, all that is known is that nothing is placed yet, as for a star
    const unsolved = { ...bare, machines: { ...bare.machines, calibration: 'unresolved' as const },
      observationSolution: { ...bare.observationSolution!, status: 'unresolved' as const, wcs: undefined, findings: [] } };
    expect(parse('select Omega Centauri', unsolved).refusal).toEqual({ kind: 'sky_not_worked_out' });
    // An object with no row of its own is never a drawn finding, and keeps its refusal
    expect(parse('select Eta Carinae', session).refusal).toEqual({ kind: 'no_named_row', said: 'eta carinae', name: 'Eta Carinae' });
    // A drawn object beside one that is not drawn: the one not drawn still stops the request
    expect(parse('how far is Omega Centauri from the Coalsack', session).refusal).toEqual({ kind: 'not_in_this_sky', said: 'coalsack', name: 'the Coalsack' });
  });

  it('keeps reading stars as before beside the drawn objects', () => {
    expect(target(parse('select Acrux', session))).toEqual(['finding.select', 'finding-hip-60718']);
  });
});
