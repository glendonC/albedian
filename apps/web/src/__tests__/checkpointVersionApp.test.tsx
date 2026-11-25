// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaVariantSchema, type ObservationSession } from '@albedian/contracts';
import { EMPTY_MEDIA_VARIANT_ID, dispatchAlbedianAction, returnCheckpointOf } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import type { SpeechRecognitionLike } from '../voice/speechInput';

/**
 * checkpoint-version-one (DECIDED-BY-LEAD 18:46 on 24 Nov): the ways of leaving that reach App's runCommand (typed,
 * spoken, the 3D pill and Go there, the last two as the viewport's pointer commands "Take me to <star>") save the moment
 * with the row pressed, which the viewport has written to the session's active variant, and Back to Moment brings it
 * back. The viewport stands in here and keeps the props App hands it.
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onPushToTalk: () => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  answer?: { sentence?: string } | null;
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

const OLDER = 'var_0000000000000001';
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  speechWindow.SpeechRecognition = FakeRecognition;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  // A photo whose older restored version is pressed: the viewport wrote it to the session's active variant
  const photo = { ...createSyntheticCalibratedSession(), selectedFindingId: null, activeVariantId: OLDER };
  act(() => seen.props.setSession({ ...photo, machines: { ...photo.machines, voice: 'off' } }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  delete speechWindow.SpeechRecognition;
});

function expectLeftWithOlderAndBack() {
  const session = seen.props.session;
  expect(session.machines.navigation).not.toBe('source');
  expect(returnCheckpointOf(session)!.mediaVariantId).toBe(OLDER);
  const back = dispatchAlbedianAction(session, { type: 'source.return', requestId: 'req_back', expectedViewRevision: session.viewRevision, restoreAudio: true }).session;
  expect(back.activeVariantId).toBe(OLDER);
}

describe('leaving by words or a pointer command saves the row pressed', () => {
  it('typed: a command in the Ask bar', () => {
    act(() => seen.props.onRunUtterance('take me to Betelgeuse', 'text'));
    expectLeftWithOlderAndBack();
  });

  it('typed: asking for the sky overhead', () => {
    act(() => seen.props.onRunUtterance('show me what was overhead', 'text'));
    expectLeftWithOlderAndBack();
  });

  it('typed: asking for the predicted sky', () => {
    act(() => seen.props.onRunUtterance('show me the predicted sky', 'text'));
    expectLeftWithOlderAndBack();
  });

  it('spoken: the same words through push-to-talk', () => {
    act(() => seen.props.onPushToTalk());
    act(() => FakeRecognition.last!.onaudiostart?.());
    act(() => FakeRecognition.last!.onresult?.({ results: [[{ transcript: 'take me to Betelgeuse' }]] }));
    act(() => FakeRecognition.last!.onend?.());
    expectLeftWithOlderAndBack();
  });

  it('the 3D pill and Go there: the viewport asks to be taken to the picked star', () => {
    act(() => seen.props.onRunUtterance('Take me to Betelgeuse', 'pointer'));
    expectLeftWithOlderAndBack();
  });
});

// The lead's 19:40 ruling: a version is never compared with itself. With the row pressed active and the only variant the
// session holds the version on screen (voice-restore-one), asking to compare with the original compares that version
// with the recording itself, as the compare on screen does (voice-compare-one)
describe('with a row pressed, asking to compare with the original', () => {
  it('compares the version pressed with the recording, and keeps it the active version', () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../tests/fixtures/media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
    const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;
    const now = seen.props.session;
    const sha = now.sourceAsset!.sha256;
    const shown = MediaVariantSchema.parse({ ...template, id: OLDER, sourceSha256: sha, sourceAssetId: now.sourceAsset!.id, uri: `/api/media/${sha}/variants/${OLDER}/file` });
    act(() => seen.props.setSession({ ...now, mediaVariants: [shown] }));
    act(() => seen.props.onRunUtterance('compare with the original', 'text'));
    const after = seen.props.session;
    expect(after.voiceActions.at(-1)!.proposedAction?.type).toBe('media.compare');
    expect(seen.props.answer).toBeNull();
    expect(after.machines.navigation).toBe('comparison');
    expect(after.compareVariantId).toBe(EMPTY_MEDIA_VARIANT_ID);
    expect(after.activeVariantId).toBe(OLDER);
  });
});
