// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement, type MutableRefObject } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ObservationSolutionSchema, type ObservationSession, type VoiceCommandResponse } from '@albedian/contracts';
import { buildSessionFromIngestReceipt } from '../services/apiClient';
import { poseReceipt } from '../components/__tests__/poseSessions.js';

/**
 * outside-frame-words-one (DECIDED-BY-LEAD 24 Nov 20:09; UIUX-readout-nodistance-READY.md; FIELD's walk): a voice
 * select or describe of a predicted object in the photo's sky but beyond its frame opens its card as before and says
 * so where answers show; an object in the frame says nothing. Over the real solve of the public corpus photo rp-b-01
 * (tests/fixtures/rp_b_01_voice_solve.json), whose frame does not hold the Large Magellanic Cloud or the Moon and holds
 * Omega Centauri.
 */

interface ViewportProps {
  session: ObservationSession;
  setSession: (session: ObservationSession) => void;
  onRunUtterance: (utterance: string, modality?: 'voice' | 'text' | 'pointer') => void;
  answer: { sentence: string } | null;
  restoreNowRef?: MutableRefObject<(() => boolean) | null>;
}

const seen = vi.hoisted(() => ({ props: undefined as unknown as ViewportProps, backend: null as VoiceCommandResponse | null }));
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
vi.mock('../voice/voiceBackend.js', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  askVoiceBackend: async () => seen.backend,
}));

import { App } from '../App';

const here = path.dirname(fileURLToPath(import.meta.url));
const RP_B_01 = ObservationSolutionSchema.parse(
  JSON.parse(fs.readFileSync(path.resolve(here, '../../../../tests/fixtures/rp_b_01_voice_solve.json'), 'utf8'))
);
const LMC = 'finding-openngc-ESO056-115';
const MOON = 'finding-predicted-moon';
const OMEGA = 'finding-openngc-NGC5139';

function realPhoto(mediaKind: 'image' | 'video' = 'image'): ObservationSession {
  const receipt = poseReceipt(RP_B_01, mediaKind);
  const session = buildSessionFromIngestReceipt({ ...receipt, sourceAsset: { ...receipt.sourceAsset, width: 3024, height: 4032 } }, { solution: RP_B_01 });
  return { ...session, selectedFindingId: null, machines: { ...session.machines, voice: 'off' } };
}

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(createElement(App)));
  seen.backend = null;
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});
const load = (session: ObservationSession) => act(() => seen.props.setSession(session));
const ask = async (words: string) => {
  await act(async () => seen.props.onRunUtterance(words, 'text'));
  await act(async () => await new Promise((r) => setTimeout(r, 20)));
};

describe('voice on a predicted object beyond the frame', () => {
  it.each([
    ['select the Large Magellanic Cloud', LMC, "Predicted, not measured: it lies beyond this photo's edges, so it has no mark on it."],
    ['tell me about the Moon', MOON, "Predicted, not measured: the Moon lies beyond this photo's edges, so it has no mark on it."],
  ])('%s: the card opens and the sentence shows where answers show', async (words, id, sentence) => {
    load(realPhoto());
    await ask(words);
    expect(seen.props.session.selectedFindingId).toBe(id);
    expect(seen.props.answer?.sentence).toBe(sentence);
  });

  it('a clip says clip', async () => {
    load(realPhoto('video'));
    await ask('select the Large Magellanic Cloud');
    expect(seen.props.session.selectedFindingId).toBe(LMC);
    expect(seen.props.answer?.sentence).toBe("Predicted, not measured: it lies beyond this clip's edges, so it has no mark on it.");
  });

  it('an object in the frame opens its card and says nothing', async () => {
    load(realPhoto());
    await ask('select Omega Centauri');
    expect(seen.props.session.selectedFindingId).toBe(OMEGA);
    expect(seen.props.answer).toBeNull();
  });

  it('a backend proposal that selects one says it too', async () => {
    load(realPhoto());
    const viewRevision = seen.props.session.viewRevision;
    seen.backend = {
      status: 'proposed',
      actionId: 'req-backend',
      proposedAction: { type: 'finding.select', requestId: 'req-backend', expectedViewRevision: viewRevision, findingId: MOON },
      visualStatus: 'Checked finding.select; not applied',
    } as VoiceCommandResponse;
    await ask('select zorblax');
    expect(seen.props.session.selectedFindingId).toBe(MOON);
    expect(seen.props.answer?.sentence).toBe("Predicted, not measured: the Moon lies beyond this photo's edges, so it has no mark on it.");
  });

  it('a backend describe of one says it, and of one in the frame says nothing', async () => {
    const jupiter = 'finding-predicted-jupiter';
    for (const [id, sentence] of [
      [jupiter, "Predicted, not measured: Jupiter lies beyond this photo's edges, so it has no mark on it."],
      [OMEGA, undefined],
    ] as const) {
      load(realPhoto());
      const viewRevision = seen.props.session.viewRevision;
      seen.backend = {
        status: 'proposed',
        actionId: 'req-backend',
        proposedAction: { type: 'finding.describe', requestId: 'req-backend', expectedViewRevision: viewRevision, findingId: id, fields: ['type'] },
        visualStatus: 'Checked finding.describe; not applied',
      } as VoiceCommandResponse;
      await ask('describe zorblax');
      expect(seen.props.session.selectedFindingId).toBe(id);
      expect(seen.props.answer?.sentence).toBe(sentence);
    }
  });
});
