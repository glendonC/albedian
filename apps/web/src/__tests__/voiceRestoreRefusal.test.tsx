// @vitest-environment jsdom
import { act, createElement, type MutableRefObject } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AlbedianAction, ObservationSession } from '@albedian/contracts';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';

/**
 * voice-restore-three (AUDIT-C's T7 on voice-restore-two, LEAD ANSWER to WEB-UX 19:42 on 24 Nov): the Restore button's
 * own action runs only for a restore the reducer refused for want of a restored version on screen. A restore refused
 * for any other reason (here the reducer's own fidelity refusal of a synthetic sky) presses nothing and shows that
 * refusal. The parser never forms such a restore today (it refuses the words first), so the request the reducer sees
 * here is the parser's own with the synthetic sky allowed, refused by the real reducer; the refusal's words are the
 * reducer's, read from it, not pinned here.
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  answer: { sentence: string } | null;
  restoreNowRef?: MutableRefObject<(() => boolean) | null>;
}

const seen = vi.hoisted(() => ({ props: undefined as unknown as ViewportProps, synthetic: false }));
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
vi.mock('@albedian/observation', async (importOriginal) => {
  const original = await importOriginal<typeof import('@albedian/observation')>();
  return {
    ...original,
    executeVoiceOrTextCommand: (...args: Parameters<typeof original.executeVoiceOrTextCommand>) => {
      const result = original.executeVoiceOrTextCommand(...args);
      const proposed = result.voiceActionRecord.proposedAction;
      if (!seen.synthetic || proposed?.type !== 'media.restore') return result;
      // The same request with the synthetic sky allowed, through the real reducer
      const asked = { ...proposed, constraints: { ...proposed.constraints, allowSyntheticSky: true } } as unknown as AlbedianAction;
      const refused = original.dispatchAlbedianAction(args[0], asked);
      const record = { ...result.voiceActionRecord, proposedAction: asked };
      return { session: { ...refused.session, voiceActions: [...args[0].voiceActions, record] }, outcome: refused.outcome, voiceActionRecord: record };
    },
  };
});

const { App } = await import('../App');
const { dispatchAlbedianAction } = await import('@albedian/observation');

let container: HTMLDivElement;
let root: Root;
let pressed: ReturnType<typeof vi.fn>;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  pressed = vi.fn(() => true);
  seen.props.restoreNowRef!.current = pressed as unknown as () => boolean;
  seen.synthetic = false;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function photo(): ObservationSession {
  const base = createSyntheticCalibratedSession();
  return {
    ...base,
    selectedFindingId: null,
    mediaVariants: [],
    sourceAsset: { ...base.sourceAsset!, mediaKind: 'image', mimeType: 'image/jpeg' },
    machines: { ...base.machines, voice: 'off' },
  } as unknown as ObservationSession;
}

describe("a restore refused for another reason presses nothing and shows the reducer's refusal (T7)", () => {
  it('a restore asking for a synthetic sky: refused as unsafe, the button not pressed, the refusal shown', () => {
    const session = photo();
    act(() => seen.props.setSession(session));
    seen.synthetic = true;
    act(() => seen.props.onRunUtterance('make the sky clearer', 'text'));
    expect(seen.props.session.voiceActions.at(-1)?.proposedAction?.type).toBe('media.restore');
    expect(pressed).not.toHaveBeenCalled();
    // The reducer's own sentence for that request, whatever its words
    const asked = seen.props.session.voiceActions.at(-1)!.proposedAction!;
    const reducer = dispatchAlbedianAction(session, asked);
    expect(reducer.outcome.status).toBe('rejected');
    expect(reducer.outcome.status === 'rejected' && reducer.outcome.reason).toBe('unsafe_synthetic_edit');
    expect(seen.props.answer?.sentence).toBe(reducer.outcome.status === 'rejected' ? reducer.outcome.explanation : undefined);
  });

  it('the same words with no synthetic sky asked: refused for want of a restored version, so the button runs', () => {
    act(() => seen.props.setSession(photo()));
    act(() => seen.props.onRunUtterance('make the sky clearer', 'text'));
    expect(pressed).toHaveBeenCalledTimes(1);
    expect(seen.props.answer).toBeNull();
  });
});
