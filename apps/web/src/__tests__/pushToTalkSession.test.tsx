// @vitest-environment jsdom
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ObservationSession, VoiceMachineState } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import {
  SPEECH_ASKING,
  SPEECH_LISTENING,
  SPEECH_PREVIOUS_PHOTO,
  SPEECH_UNAVAILABLE,
  type SpeechRecognitionLike,
} from '../voice/speechInput';
import { VOICE_REPLY_PREVIOUS_PHOTO } from '../voice/voiceBackend';

/**
 * Push-to-talk in the mounted app, with the viewport stubbed to hand over its props and a fake recognizer in place
 * of the browser's. The voice machine says listening exactly while the microphone capture is open, and returns to
 * off when the capture ends, fails or never starts. The spoken command applies to the session as it stands when
 * the words arrive, with the pointer as it stands then, so a click made while listening is kept.
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onSelectFinding: (findingId: string, pointerCoords?: { xPx: number; yPx: number }) => void;
  onPushToTalk: () => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
}

const seen = vi.hoisted(() => ({ props: undefined as unknown as ViewportProps, voices: [] as VoiceMachineState[] }));

vi.mock('../components/SpatialObservatoryViewport.js', () => ({
  SpatialObservatoryViewport: (props: ViewportProps) => {
    seen.props = props;
    seen.voices.push(props.session.machines.voice);
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
  /** The browser opened the microphone, after any permission prompt. */
  open() {
    this.onaudiostart?.();
  }
}

class RefusingRecognition extends FakeRecognition {
  override start(): void {
    throw new DOMException('recognition has already started', 'InvalidStateError');
  }
}

type SpeechWindow = { SpeechRecognition?: new () => SpeechRecognitionLike; webkitSpeechRecognition?: unknown };
const speechWindow = window as unknown as SpeechWindow;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  seen.voices = [];
  FakeRecognition.last = undefined;
  speechWindow.SpeechRecognition = FakeRecognition;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  const calibrated = createSyntheticCalibratedSession();
  act(() =>
    seen.props.setSession({ ...calibrated, selectedFindingId: null, machines: { ...calibrated.machines, voice: 'off' } })
  );
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete speechWindow.SpeechRecognition;
  vi.unstubAllGlobals();
});

/** The session of a newly loaded photo: another session and asset, at the same view revision by coincidence. */
function anotherPhoto(session: ObservationSession): ObservationSession {
  return {
    ...session,
    id: 'session-another-photo-made-up',
    sourceAsset: { ...session.sourceAsset!, id: 'asset-another-photo-made-up', sha256: 'f'.repeat(64) },
    selectedFindingId: null,
    voiceActions: [],
  };
}

const voice = () => seen.props.session.machines.voice;

describe('@albedian/web push-to-talk: the voice state and the session a spoken command acts on', () => {
  it('says listening only once the microphone is open, and off once the capture ends', () => {
    expect(voice()).toBe('off');
    act(() => seen.props.onPushToTalk());
    // The browser may still be asking for the microphone
    expect(voice()).toBe('permission');
    expect(seen.props.session.lastStatusBanner).toBe(SPEECH_ASKING);
    act(() => FakeRecognition.last!.open());
    expect(voice()).toBe('listening');
    expect(seen.props.session.lastStatusBanner).toBe(SPEECH_LISTENING);
    act(() => FakeRecognition.last!.onend?.());
    expect(voice()).toBe('off');
  });

  it('returns to off when the microphone is refused, never having said listening', () => {
    seen.voices = [];
    act(() => seen.props.onPushToTalk());
    act(() => {
      FakeRecognition.last!.onerror?.({ error: 'not-allowed' });
      FakeRecognition.last!.onend?.();
    });
    expect(voice()).toBe('off');
    expect(seen.voices).not.toContain('listening');
    expect(seen.props.session.lastStatusBanner).toMatch(/microphone is not allowed/i);
  });

  it('never says listening when recognition refuses to start', () => {
    speechWindow.SpeechRecognition = RefusingRecognition;
    seen.voices = [];
    act(() => seen.props.onPushToTalk());
    expect(seen.voices).not.toContain('listening');
    expect(voice()).toBe('off');
  });

  it('never says listening when the browser has no speech recognition', () => {
    delete speechWindow.SpeechRecognition;
    seen.voices = [];
    act(() => seen.props.onPushToTalk());
    expect(seen.voices).not.toContain('listening');
    expect(seen.props.session.lastStatusBanner).toBe(SPEECH_UNAVAILABLE);
  });

  it('acts on the session as it stands when the words arrive, keeping a click made while listening', () => {
    act(() => seen.props.onPushToTalk());
    act(() => FakeRecognition.last!.open());
    act(() => seen.props.onSelectFinding('finding_rigel', { xPx: 12, yPx: 34 }));
    expect(seen.props.session.selectedFindingId).toBe('finding_rigel');
    act(() => FakeRecognition.last!.onresult?.({ results: [[{ transcript: 'what is that' }]] }));
    const session = seen.props.session;
    expect(session.selectedFindingId).toBe('finding_rigel');
    const record = session.voiceActions[session.voiceActions.length - 1]!;
    expect(record.utterance).toBe('what is that');
    expect(record.proposedAction).toMatchObject({ type: 'finding.select', findingId: 'finding_rigel' });
    // The pointer from the click, not the one from before the capture opened
    expect(record.context.recentPointer).toMatchObject({ xPx: 12, yPx: 34, nearestFindingId: 'finding_rigel' });
    act(() => FakeRecognition.last!.onend?.());
    expect(voice()).toBe('off');
  });

  it('drops words spoken over the previous photo instead of applying them to a new one', () => {
    act(() => seen.props.onPushToTalk());
    act(() => FakeRecognition.last!.open());
    const newPhoto = anotherPhoto(seen.props.session);
    act(() => seen.props.setSession(newPhoto));
    act(() => FakeRecognition.last!.onresult?.({ results: [[{ transcript: 'select rigel' }]] }));
    const session = seen.props.session;
    expect(session.id).toBe(newPhoto.id);
    expect(session.selectedFindingId).toBeNull();
    expect(session.voiceActions).toEqual([]);
    expect(session.lastStatusBanner).toBe(SPEECH_PREVIOUS_PHOTO);
    act(() => FakeRecognition.last!.onend?.());
    expect(voice()).toBe('off');
    expect(seen.props.session.lastStatusBanner).toBe(SPEECH_PREVIOUS_PHOTO);
  });

  it('drops a backend reply asked about the previous photo, even when the view revision matches by coincidence', async () => {
    let answer: (value: unknown) => void = () => {};
    const fetchMock = vi.fn(() => new Promise((resolve) => (answer = resolve)));
    vi.stubGlobal('fetch', fetchMock);
    const before = seen.props.session;
    // Nothing is selected, so the app's parser refuses and asks the backend
    act(() => seen.props.onRunUtterance('what is that', 'text'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const newPhoto = anotherPhoto(seen.props.session);
    act(() => seen.props.setSession(newPhoto));
    expect(newPhoto.viewRevision).toBe(seen.props.session.viewRevision);
    const reply = {
      status: 'proposed',
      actionId: 'req-made-up',
      proposedAction: {
        type: 'finding.select',
        requestId: 'req-made-up',
        expectedViewRevision: newPhoto.viewRevision,
        findingId: 'finding_rigel',
      },
      visualStatus: 'Checked finding.select; not applied',
    };
    await act(async () => {
      answer({ ok: true, json: async () => reply });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    const session = seen.props.session;
    expect(session.id).toBe(newPhoto.id);
    expect(session.selectedFindingId).toBeNull();
    expect(session.lastStatusBanner).toBe(VOICE_REPLY_PREVIOUS_PHOTO);
    expect(before.id).not.toBe(newPhoto.id);
  });
});
