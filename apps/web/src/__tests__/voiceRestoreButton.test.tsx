// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement, type MutableRefObject } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaVariantSchema, type ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

/**
 * voice-restore-two (DECIDED-BY-LEAD 18:21 on 24 Nov): voice is direct manipulation, so asking to restore does what the
 * Restore button does. The request goes through the reducer as before; where it finds no restored version on screen,
 * the button's own action runs (the viewport hands it over, as it hands over the clip's clock) and the compare shows
 * when it is ready; the sentence that none exists is said only where the button itself could do nothing.
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  answer: { sentence: string } | null;
  restoreNowRef?: MutableRefObject<(() => boolean) | null>;
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

const here = path.dirname(fileURLToPath(import.meta.url));
const media = JSON.parse(fs.readFileSync(path.resolve(here, '../../../../tests/fixtures/media_contract.json'), 'utf8')) as { valid: { MediaVariant: Array<Record<string, unknown>> } };
const template = media.valid.MediaVariant.find((v) => v.kind === 'restoration')!;

let container: HTMLDivElement;
let root: Root;
let pressed: ReturnType<typeof vi.fn>;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  expect(seen.props.restoreNowRef).toBeDefined();
  pressed = vi.fn(() => true);
  seen.props.restoreNowRef!.current = pressed as unknown as () => boolean;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** A recording on the laptop reveal as the app holds it: no restored version in the session until one is shown. */
function recording(kind: 'image' | 'video'): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    selectedFindingId: null,
    mediaVariants: [],
    sourceAsset: { ...base.sourceAsset!, mediaKind: kind, mimeType: kind === 'image' ? 'image/jpeg' : 'video/mp4' },
    machines: { ...base.machines, voice: 'off' },
  } as unknown as ObservationSession;
}
const load = (session: ObservationSession) => act(() => seen.props.setSession(session));
const ask = (words: string) => act(() => seen.props.onRunUtterance(words, 'text'));

describe('asking to restore does what the Restore button does', () => {
  it('a photo whose stored restoration from an earlier visit is not shown on the laptop: the button runs, no refusal', () => {
    load(recording('image'));
    ask('make the sky clearer');
    expect(pressed).toHaveBeenCalledTimes(1);
    expect(seen.props.answer).toBeNull();
  });

  it('a photo with no restoration at all: the button runs (it starts one), no refusal', () => {
    load(recording('image'));
    ask('restore');
    expect(pressed).toHaveBeenCalledTimes(1);
    expect(seen.props.answer).toBeNull();
  });

  it('a clip: the same', () => {
    load(recording('video'));
    ask('make the sky clearer');
    expect(pressed).toHaveBeenCalledTimes(1);
    expect(seen.props.answer).toBeNull();
  });

  it('says that none exists only where the button itself could do nothing', () => {
    load(recording('image'));
    seen.props.restoreNowRef!.current = vi.fn(() => false);
    ask('make the sky clearer');
    expect(seen.props.answer?.sentence).toBe('This photo has not been restored.');
  });

  it('a restored version already on screen: the reducer finds it, and the button is not pressed again', () => {
    const photo = recording('image');
    const sha = photo.sourceAsset!.sha256;
    const shown = MediaVariantSchema.parse({ ...template, sourceSha256: sha, sourceAssetId: `src_${sha.slice(0, 16)}`, uri: `/api/media/${sha}/variants/${String(template.id)}/file` });
    load({ ...photo, mediaVariants: [shown] } as ObservationSession);
    ask('make the sky clearer');
    expect(pressed).not.toHaveBeenCalled();
    expect(seen.props.answer).toBeNull();
    expect(seen.props.session.activeVariantId).toBe(shown.id);
  });

  it('presses nothing for another command, refused or not, even one refused for want of a variant', () => {
    load(recording('image'));
    ask('show me this sky a thousand years ago');
    ask('select rigel');
    // A compare with no variants held is refused with the same reason as a restore: still nothing is pressed
    ask('compare with the original');
    expect(seen.props.session.voiceActions.at(-1)!.proposedAction?.type).toBe('media.compare');
    expect(pressed).not.toHaveBeenCalled();
    expect(seen.props.answer?.sentence).toBe('This photo has not been restored, so there is nothing to compare.');
  });
});
