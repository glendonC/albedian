// @vitest-environment jsdom
import { act, createElement, type MutableRefObject } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession } from '@albedian/contracts';
import { dispatchAlbedianAction, returnCheckpointOf } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { SpeechRecognitionLike } from '../voice/speechInput';

/**
 * clip-playing-checkpoint-1 (LEAD ANSWER to WEB-UX 15:35 on 24 Nov): while a clip plays, a command that leaves the
 * recording first sets the session's clock to the clip's live time (one media.seek at the top of App's runCommand,
 * the live time handed over by the viewport through a ref), so the checkpoint carries the frame on screen and Back to
 * Moment returns the clip playing there. One test for each way of leaving: typed, spoken, the 3D pill, Go there (the
 * last two reach runCommand as the viewport's pointer commands, "Take me to <star>").
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onPushToTalk: () => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  clipClockRef?: MutableRefObject<(() => number | null) | null>;
}

const seen = vi.hoisted(() => ({ props: undefined as unknown as ViewportProps }));
vi.mock('../components/SpatialObservatoryViewport.js', () => ({
  SpatialObservatoryViewport: (props: ViewportProps) => {
    seen.props = props;
    return null;
  },
}));
vi.mock('../services/apiClient.js', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchDiscoveredMedia: async () => [],
}));

import { App } from '../App';

type ResultEvent = { results: ArrayLike<ArrayLike<{ transcript: string }>> };
class FakeRecognition implements SpeechRecognitionLike {
  static last: FakeRecognition | undefined;
  lang = '';
  interimResults = true;
  onresult: ((ev: ResultEvent) => void) | null = null;
  onerror: ((ev: { error?: string }) => void) | null = null;
  onend: (() => void) | null = null;
  onaudiostart: (() => void) | null = null;
  constructor() {
    FakeRecognition.last = this;
  }
  start() {}
}
type SpeechWindow = { SpeechRecognition?: new () => SpeechRecognitionLike };
const speechWindow = window as unknown as SpeechWindow;

let container: HTMLDivElement;
let root: Root;
/** The clip's live time as the viewport would hand it over: a number while it plays, null while it stands still. */
let live: number | null = null;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  speechWindow.SpeechRecognition = FakeRecognition;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  // A clip at 1.5 s whose playback a pointer started (media.playback wrote it), at sound level 0.4
  const clip = { ...createSyntheticCalibratedSession(), selectedFindingId: null, isPlaying: false, currentSourceTimeSec: 1.5, audioGain: 0.4 };
  const playing = dispatchAlbedianAction(clip, { type: 'media.playback', requestId: 'req_play', expectedViewRevision: clip.viewRevision, playing: true }).session;
  act(() => seen.props.setSession({ ...playing, machines: { ...playing.machines, voice: 'off' } }));
  live = 7.25;
  expect(seen.props.clipClockRef).toBeDefined();
  seen.props.clipClockRef!.current = () => live;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete speechWindow.SpeechRecognition;
});

/** Where Back to Moment brings the session back to, and the session it gives. */
function returned() {
  const session = seen.props.session;
  const checkpoint = returnCheckpointOf(session)!;
  const back = dispatchAlbedianAction(session, { type: 'source.return', requestId: 'req_back', expectedViewRevision: session.viewRevision, restoreAudio: true }).session;
  return { checkpoint, back };
}
function expectLeftPlayingAtLiveTime() {
  // Left the recording (the flight's first phase stands in the registered sky)
  expect(seen.props.session.machines.navigation).not.toBe('source');
  const { checkpoint, back } = returned();
  expect(checkpoint.sourceTimeSec).toBe(7.25);
  expect(checkpoint.playbackWasPlaying).toBe(true);
  expect(back.currentSourceTimeSec).toBe(7.25);
  expect(back.isPlaying).toBe(true);
  expect(back.audioGain).toBe(0.4);
}

describe('leaving a playing clip saves the frame on screen', () => {
  it('typed: a command in the Ask bar', () => {
    act(() => seen.props.onRunUtterance('take me to Betelgeuse', 'text'));
    expectLeftPlayingAtLiveTime();
  });

  it('spoken: the same words through push-to-talk', () => {
    act(() => seen.props.onPushToTalk());
    act(() => FakeRecognition.last!.onaudiostart?.());
    act(() => FakeRecognition.last!.onresult?.({ results: [[{ transcript: 'take me to Betelgeuse' }]] }));
    act(() => FakeRecognition.last!.onend?.());
    expectLeftPlayingAtLiveTime();
  });

  it('the 3D pill: the viewport asks to be taken to the picked star', () => {
    act(() => seen.props.onRunUtterance('Take me to Betelgeuse', 'pointer'));
    expectLeftPlayingAtLiveTime();
  });

  it("Go there: the star card's button asks the same", () => {
    act(() => seen.props.onRunUtterance('Take me to Betelgeuse', 'pointer'));
    expectLeftPlayingAtLiveTime();
  });

  it('a clip standing still keeps the clock it has: no seek, and it comes back paused', () => {
    act(() => seen.props.setSession({ ...seen.props.session, isPlaying: false }));
    live = null;
    const before = seen.props.session.viewRevision;
    act(() => seen.props.onRunUtterance('take me to Betelgeuse', 'text'));
    const { checkpoint, back } = returned();
    expect(checkpoint.sourceTimeSec).toBe(1.5);
    expect(checkpoint.playbackWasPlaying).toBe(false);
    expect(back.isPlaying).toBe(false);
    // Only the command's own revision
    expect(seen.props.session.voiceActions.at(-1)!.context.viewRevision).toBe(before);
  });
});
