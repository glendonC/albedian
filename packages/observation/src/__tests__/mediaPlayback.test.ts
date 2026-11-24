import { describe, expect, it } from 'vitest';
import { AlbedianActionSchema, type AlbedianAction, type ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction } from '../reducer';
import { returnCheckpointOf } from '../checkpoints';
import { createSyntheticCalibratedSession } from '../testing/syntheticSessions';

// media-playback-0 (LEAD ANSWER to WEB-UX, 12:29 on 24 Nov, route one): a clip played or paused on screen is written to
// the session through the reducer, so the CameraCheckpoint saved on leaving the recording carries playbackWasPlaying as
// it was, and Back to Moment restores it (AGENTS.md non-negotiable 5; AUDIT-C's observation C1, b78).

const playback = (session: ObservationSession, playing: boolean, requestId = `req_playback_${String(playing)}`): AlbedianAction => ({
  type: 'media.playback',
  requestId,
  expectedViewRevision: session.viewRevision,
  playing,
});
const pausedClip = (): ObservationSession => ({ ...createSyntheticCalibratedSession(), isPlaying: false });

describe('media.playback: the contract', () => {
  it('takes a request id, the revision it was made against and whether the clip plays, nothing else', () => {
    const base = { type: 'media.playback', requestId: 'r', expectedViewRevision: 0 };
    expect(AlbedianActionSchema.parse({ ...base, playing: true })).toEqual({ ...base, playing: true });
    expect(AlbedianActionSchema.parse({ ...base, playing: false })).toEqual({ ...base, playing: false });
    for (const playing of [1, 0, 'true', null, undefined]) {
      expect(AlbedianActionSchema.safeParse({ ...base, playing }).success, String(playing)).toBe(false);
    }
    expect(AlbedianActionSchema.safeParse({ ...base, requestId: '', playing: true }).success).toBe(false);
    expect(AlbedianActionSchema.safeParse({ ...base, expectedViewRevision: -1, playing: true }).success).toBe(false);
  });
});

describe('media.playback: the reducer', () => {
  it('writes playing and paused on the recording, one revision each, and says nothing new on screen', () => {
    const start = pausedClip();
    const played = dispatchAlbedianAction(start, playback(start, true));
    expect(played.outcome.status).toBe('completed');
    expect(played.session.isPlaying).toBe(true);
    expect(played.session.viewRevision).toBe(start.viewRevision + 1);
    expect(played.session.lastStatusBanner).toBe(start.lastStatusBanner);
    expect(played.session.currentSourceTimeSec).toBe(start.currentSourceTimeSec);
    const paused = dispatchAlbedianAction(played.session, playback(played.session, false));
    expect(paused.outcome.status).toBe('completed');
    expect(paused.session.isPlaying).toBe(false);
    expect(paused.session.viewRevision).toBe(start.viewRevision + 2);
  });

  it('completes on a session with no status line, leaving it without one', () => {
    const start = { ...pausedClip(), lastStatusBanner: undefined } as ObservationSession;
    const result = dispatchAlbedianAction(start, playback(start, true));
    expect(result.outcome.status).toBe('completed');
    expect(result.session.isPlaying).toBe(true);
    expect(result.session.lastStatusBanner).toBeUndefined();
  });

  it('refuses a stale revision, as media.seek does, and changes nothing', () => {
    const start = pausedClip();
    const stale = { ...playback(start, true), expectedViewRevision: start.viewRevision + 3 };
    const result = dispatchAlbedianAction(start, stale);
    expect(result.outcome).toMatchObject({ status: 'rejected', reason: 'stale_revision' });
    expect(result.session.isPlaying).toBe(false);
  });

  it('refuses with no recording, on a photo, and away from the recording', () => {
    const start = pausedClip();
    const empty = { ...start, sourceAsset: undefined } as unknown as ObservationSession;
    expect(dispatchAlbedianAction(empty, playback(empty, true)).outcome).toMatchObject({ status: 'rejected', reason: 'missing_source' });
    const photo = { ...start, sourceAsset: { ...start.sourceAsset!, mediaKind: 'image' } } as ObservationSession;
    const onPhoto = dispatchAlbedianAction(photo, playback(photo, true));
    expect(onPhoto.outcome).toMatchObject({ status: 'rejected', reason: 'missing_source' });
    expect(onPhoto.session.isPlaying).toBe(false);
    const sky = dispatchAlbedianAction(start, { type: 'sky.show', requestId: 'req_sky', expectedViewRevision: start.viewRevision, mode: 'measured' }).session;
    expect(sky.machines.navigation).not.toBe('source');
    const away = dispatchAlbedianAction(sky, playback(sky, true));
    expect(away.outcome).toMatchObject({ status: 'rejected', reason: 'missing_source' });
    expect(away.session.isPlaying).toBe(false);
  });

  it('plays under a compare wipe opened over the clip, and not under one opened in the sky, which keeps the sky', () => {
    const clip = pausedClip();
    const overClip = { ...clip, machines: { ...clip.machines, navigation: 'comparison' } } as ObservationSession;
    expect(overClip.spatialView.mode).toBe('source_2d');
    const result = dispatchAlbedianAction(overClip, playback(overClip, true));
    expect(result.outcome.status).toBe('completed');
    expect(result.session.isPlaying).toBe(true);
    const inSky = { ...overClip, spatialView: { ...overClip.spatialView, mode: 'registered_sky' } } as ObservationSession;
    expect(dispatchAlbedianAction(inSky, playback(inSky, true)).outcome).toMatchObject({ status: 'rejected', reason: 'missing_source' });
  });

  it('carries a clip that was playing into the checkpoint on leaving, and Back to Moment brings it back playing', () => {
    const start = pausedClip();
    const played = dispatchAlbedianAction(start, playback(start, true)).session;
    const left = dispatchAlbedianAction(played, {
      type: 'space.enter',
      requestId: 'req_space',
      expectedViewRevision: played.viewRevision,
      findingId: 'finding_betelgeuse',
      distancePolicy: 'measured_or_estimated',
    }).session;
    expect(left.machines.navigation).toBe('space');
    expect(left.isPlaying).toBe(false);
    expect(returnCheckpointOf(left)!.playbackWasPlaying).toBe(true);
    const back = dispatchAlbedianAction(left, { type: 'source.return', requestId: 'req_back', expectedViewRevision: left.viewRevision, restoreAudio: true }).session;
    expect(back.isPlaying).toBe(true);
    // And one paused before leaving comes back paused
    const pausedAgain = dispatchAlbedianAction(back, playback(back, false)).session;
    const leftAgain = dispatchAlbedianAction(pausedAgain, { type: 'sky.show', requestId: 'req_sky2', expectedViewRevision: pausedAgain.viewRevision, mode: 'measured' }).session;
    expect(returnCheckpointOf(leftAgain)!.playbackWasPlaying).toBe(false);
    const backAgain = dispatchAlbedianAction(leftAgain, { type: 'source.return', requestId: 'req_back2', expectedViewRevision: leftAgain.viewRevision, restoreAudio: true }).session;
    expect(backAgain.isPlaying).toBe(false);
  });
});
