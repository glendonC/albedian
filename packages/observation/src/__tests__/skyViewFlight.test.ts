import { describe, expect, it } from 'vitest';
import type { ObservationSession, SkyView } from '@albedian/contracts';
import { dispatchAlbedianAction } from '../reducer.js';
import { buildActionContext, parseUtteranceToAction } from '../voiceParser.js';
import { returnCheckpointOf } from '../checkpoints.js';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions.js';

// go-there-predicted-2 (DECIDED-BY-LEAD 25 Nov 02:45, A): the predicted sky's view rides on the request's context, on
// to space.enter, into the checkpoint the flight leaves from, and back with Back to Moment.

const VIEW: SkyView = { cameraQuaternionIcrs: [0.5, -0.5, 0.5, 0.5], fovVerticalDeg: 45 };

/** A sky with no verified WCS and no pose: its stars predicted, each with its catalog row and distance kept. */
function predictedSky(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  const solution = base.observationSolution!;
  const { wcs: _wcs, ...rest } = solution;
  return {
    ...base,
    machines: { ...base.machines, calibration: 'predicted_only' },
    observationSolution: { ...rest, status: 'predicted_only', findings: solution.findings.map((f) => ({ ...f, evidenceClass: f.evidenceClass === 'catalog_matched' ? 'predicted' : f.evidenceClass })) },
  } as ObservationSession;
}
const starOf = (s: ObservationSession) => s.observationSolution!.findings.find((f) => f.evidenceClass === 'predicted' && f.inferredDistancePc && f.catalogRef)!;
const enter = (s: ObservationSession, skyView?: SkyView) =>
  dispatchAlbedianAction(s, { type: 'space.enter', requestId: 'go', expectedViewRevision: s.viewRevision, findingId: starOf(s).id, distancePolicy: 'measured_or_estimated', ...(skyView ? { skyView } : {}) });
const departure = (s: ObservationSession) => s.navigationHistory.checkpoints[s.navigationHistory.cursorIndex]!;

describe('the request\'s context and the parser', () => {
  it('puts the sky view on the context only when one is given', () => {
    const s = predictedSky();
    expect(buildActionContext(s, undefined, VIEW).skyView).toEqual(VIEW);
    expect(buildActionContext(s).skyView).toBeUndefined();
  });

  it('carries the context\'s sky view onto space.enter, and none when the context has none', () => {
    const s = predictedSky();
    const name = starOf(s).title;
    const withView = parseUtteranceToAction(`take me to ${name}`, buildActionContext(s, undefined, VIEW), s);
    expect(withView.action).toMatchObject({ type: 'space.enter', skyView: VIEW });
    const without = parseUtteranceToAction(`take me to ${name}`, buildActionContext(s), s);
    expect(without.action).toMatchObject({ type: 'space.enter' });
    expect((without.action as { skyView?: SkyView }).skyView).toBeUndefined();
  });
});

describe('the reducer keeps the sky view in the checkpoint the flight leaves from', () => {
  it('on a predicted sky: the departing checkpoint holds it, and Back to Moment brings it back', () => {
    const s = predictedSky();
    const { session: flown, outcome } = enter(s, VIEW);
    expect(outcome.status).toBe('completed');
    expect(departure(flown).spatialView.skyView).toEqual(VIEW);
    // The flight's own state does not carry it: only the place it left from does
    expect(flown.spatialView.skyView).toBeUndefined();
    const { session: back } = dispatchAlbedianAction(flown, { type: 'history.back', requestId: 'back', expectedViewRevision: flown.viewRevision, mode: 'full' } as never);
    expect(back.spatialView.skyView).toEqual(VIEW);
  });

  it('keeps none from a verified solve or without a sky view on the request', () => {
    const solved = createSyntheticCalibratedSession();
    const star = solved.observationSolution!.findings.find((f) => f.evidenceClass === 'catalog_matched' && f.inferredDistancePc)!;
    const { session: fromSolve } = dispatchAlbedianAction(solved, { type: 'space.enter', requestId: 'go', expectedViewRevision: solved.viewRevision, findingId: star.id, distancePolicy: 'measured_or_estimated', skyView: VIEW });
    expect(departure(fromSolve).spatialView.skyView).toBeUndefined();
    expect(departure(enter(predictedSky()).session).spatialView.skyView).toBeUndefined();
  });

  it('keeps none from a sky with a pose estimate, whose own WCS the flight leaves from', () => {
    const s = predictedSky();
    const posed = { ...s, observationSolution: { ...s.observationSolution!, poseEstimate: {} } } as unknown as ObservationSession;
    expect(departure(enter(posed, VIEW).session).spatialView.skyView).toBeUndefined();
  });
});

describe('Back to Moment from the predicted sky\'s flight', () => {
  const back = (s: ObservationSession) =>
    dispatchAlbedianAction(s, { type: 'source.return', requestId: `back-${s.viewRevision}`, expectedViewRevision: s.viewRevision, checkpointId: returnCheckpointOf(s)?.id, restoreAudio: true }).session;
  function onPredictedSky(): ObservationSession {
    const s = predictedSky();
    return dispatchAlbedianAction(s, { type: 'sky.show', requestId: 'sky', expectedViewRevision: s.viewRevision, mode: 'predicted' }).session;
  }

  it('returns to the predicted sky at the view the flight left from, and from there on to the photo', () => {
    const sky = onPredictedSky();
    expect(sky.spatialView.mode).toBe('registered_sky');
    const flown = enter(sky, VIEW).session;
    const returned = back(flown);
    expect(returned.spatialView.mode).toBe('registered_sky');
    expect(returned.spatialView.skyView).toEqual(VIEW);
    // Pressed again on the predicted sky: the photo, as before
    const photo = back(returned);
    expect(photo.spatialView.mode).toBe('source_2d');
    // Shown afresh later, the sky opens as its design record says: the kept view is let go (lead, 04:47)
    const again = dispatchAlbedianAction(returned, { type: 'sky.show', requestId: 'again', expectedViewRevision: returned.viewRevision, mode: 'predicted' }).session;
    expect(again.spatialView.skyView).toBeUndefined();
    // The return takes the sky's checkpoint out of the history it leaves
    expect(returned.navigationHistory.checkpoints.some((c) => c.spatialView.skyView !== undefined)).toBe(false);
  });

  it('counts a kept sky view as the moment only from a flight', () => {
    const flown = enter(onPredictedSky(), VIEW).session;
    expect(returnCheckpointOf(flown)?.spatialView.skyView).toEqual(VIEW);
    // The same history read while not flying: the photo's own moment
    const notFlying = { ...flown, machines: { ...flown.machines, navigation: 'source' } } as ObservationSession;
    expect(returnCheckpointOf(notFlying)?.spatialView.skyView).toBeUndefined();
  });

  // go-there-predicted-words-one (UIUX-predsky-READY.md, the lead's line stamped 04:47): the return to the predicted sky
  // speaks of the sky and the view, not of the recording it does not reach; no class words; the photo's return unchanged
  it('says it returned to the predicted sky at the view left, and the photo\'s words when it goes on to the photo', () => {
    const say = (s: ObservationSession) => {
      const r = dispatchAlbedianAction(s, { type: 'source.return', requestId: `back-${s.viewRevision}`, expectedViewRevision: s.viewRevision, checkpointId: returnCheckpointOf(s)?.id, restoreAudio: true });
      if (r.outcome.status !== 'completed') throw new Error(`not completed: ${r.outcome.status}`);
      return { session: r.session, outcome: r.outcome };
    };
    const flown = enter(onPredictedSky(), VIEW).session;
    const toSky = say(flown);
    expect(toSky.outcome.spokenFeedback).toBe('Returned to the predicted sky, at the view you left.');
    expect(toSky.outcome.visualStatus).toBe('Returned to the predicted sky · The view you left restored.');
    expect(toSky.session.lastStatusBanner).toBe('Returned to the predicted sky · The view you left restored.');
    const toPhoto = say(toSky.session);
    expect(toPhoto.outcome.spokenFeedback).toBe('Returned to your original recorded moment.');
    expect(toPhoto.outcome.visualStatus).toMatch(/^Returned to Moment \(\d+\.\ds\) · Audio & frame restored\.$/);
    expect(toPhoto.session.lastStatusBanner).toBe(toPhoto.outcome.visualStatus);
    // A flight with no sky view returns to the photo with the photo's words
    const plain = say(enter(onPredictedSky()).session);
    expect(plain.outcome.spokenFeedback).toBe('Returned to your original recorded moment.');
    expect(plain.outcome.visualStatus).toMatch(/^Returned to Moment /);
  });

  it('returns to the photo from a flight that carried no sky view, as before', () => {
    const returned = back(enter(onPredictedSky()).session);
    expect(returned.spatialView.skyView).toBeUndefined();
    expect(returned.spatialView.mode).toBe('source_2d');
  });
});
