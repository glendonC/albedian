// @vitest-environment jsdom
import { Fragment, act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fc from 'fast-check';
import type { ObservationSession, ObservationSolution } from '@albedian/contracts';
import { dispatchAlbedianAction, executeVoiceOrTextCommand } from '@albedian/observation';
import { createSyntheticCalibratedSession } from '@albedian/observation/testing';
import { ObservationSolveAndCompareHud } from '../../spatial/ObservationSolveAndCompareHud';
import { buildSessionFromDiscoveredMedia, type DiscoveredMediaItem } from '../../services/apiClient';
import { goToMomentSentence } from '../../services/skyLayerWords';
import { CelestialMarkerOverlay, projectFindingToViewport } from '../CelestialMarkerOverlay';
import { SourcePhotoStage } from '../SourcePhotoStage';
import { linedUpSky, shownAtFrame } from '../poseProjection';
import { CLIP_FPS, POSE, clipPoseSolution, poseSession, predictedOf, sample, type ClipFacts } from './poseSessions';

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function renderOverlay(session: ObservationSession) {
  act(() => {
    root.render(
      createElement(CelestialMarkerOverlay, {
        session,
        selectedFinding: null,
        onSelectFinding: () => {},
        viewportWidthPx: 1440,
        viewportHeightPx: 900,
      })
    );
  });
}

function renderStage(session: ObservationSession) {
  act(() => {
    root.render(createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 }));
  });
}

const markerIds = (evidenceClass: string) =>
  [...container.querySelectorAll(`.alb-marker-hit[data-evidence-class="${evidenceClass}"]`)].map((el) =>
    (el as HTMLElement).dataset.testid!.replace('celestial-marker-', '')
  );
const rings = () => [...container.querySelectorAll('[data-testid^="celestial-marker-ring-"]')] as HTMLElement[];
const IN_FRAME = 7; // the six stars and the Moon the fixture places inside its frame

/** The stage and the markers over it, as the source view draws them together. */
function renderScene(session: ObservationSession) {
  act(() => {
    root.render(
      createElement(
        Fragment,
        null,
        createElement(SourcePhotoStage, { session, viewportWidthPx: 1440, viewportHeightPx: 900 }),
        createElement(CelestialMarkerOverlay, {
          session,
          selectedFinding: null,
          onSelectFinding: () => {},
          viewportWidthPx: 1440,
          viewportHeightPx: 900,
        })
      )
    );
  });
}
const stageVideo = () => container.querySelector('[data-testid="source-photo-stage"] video') as HTMLVideoElement;
/** The browser reads the clip's metadata: the stage seeks on it, and later seeks go straight to the loaded element. */
function loadClip(): HTMLVideoElement {
  const video = stageVideo();
  act(() => {
    video.dispatchEvent(new Event('loadedmetadata'));
  });
  Object.defineProperty(video, 'readyState', { value: 1, configurable: true });
  return video;
}
const goToLine = () => container.querySelector('[data-testid="lined-up-sky-go-to"]');
const disclosure = () => container.querySelector('[data-testid="lined-up-sky-disclosure"]');
/** Words said or typed to the app, read by its own parser into an action and run, as the voice and command box do. */
function say(session: ObservationSession, words: string): ObservationSession {
  const result = executeVoiceOrTextCommand(session, words, 'voice');
  expect(result.outcome.status, words).toBe('completed');
  return result.session;
}
/** The seconds the go-to line names, and the command it quotes, exactly as written between its quotes. */
function quotedCommand(line: string): { command: string; namedSec: number } {
  const m = /^Lined up (\d+\.\d) seconds into the clip\. Say or type "([^"]+)" to see it\.$/.exec(line);
  expect(m, line).not.toBeNull();
  return { command: m![2]!, namedSec: Number(m![1]) };
}
/** Words typed into the command box, read by the app's own parser into an action and run. */
function typeIn(session: ObservationSession, words: string): ObservationSession {
  const result = executeVoiceOrTextCommand(session, words, 'text');
  expect(result.outcome.status, words).toBe('completed');
  return result.session;
}
/** The pose sample as a clip lined up on the frame at `frameTimeSec`, both observed points measured on that frame. */
const clipOn = (frameTimeSec: number, facts?: ClipFacts): ObservationSession =>
  poseSession(clipPoseSolution(frameTimeSec, frameTimeSec), 'video', facts);

describe('the photo view draws the predicted sky lined up by a pose (REQ-SKY-6.2, 6.6)', () => {
  it('draws each in-frame predicted finding as a dashed circle with a faint dashed ring of its radius', () => {
    const session = poseSession();
    renderOverlay(session);
    const ids = markerIds('predicted');
    expect(ids.length).toBe(IN_FRAME);
    expect(ids).not.toContain('finding-predicted-uranus');
    for (const id of ids) {
      const marker = container.querySelector(`[data-testid="celestial-marker-${id}"]`) as HTMLElement;
      expect(marker.dataset.markerShape).toBe('dashed_circle');
    }
    expect(rings().length).toBe(IN_FRAME);
    for (const ring of rings()) {
      const id = ring.dataset.testid!.replace('celestial-marker-ring-', '');
      const pos = projectFindingToViewport(POSE.findings.find((f) => f.id === id)!, session, 1440, 900);
      expect(Number(ring.dataset.ringRadiusPx)).toBeCloseTo(pos.ringPx!, 2);
      const circle = ring.querySelector('circle')!;
      expect(circle.getAttribute('stroke-dasharray')).toBeTruthy();
      expect(Number(circle.getAttribute('stroke-opacity'))).toBeLessThan(1);
    }
    // The observed Moon stays where it was measured
    expect(markerIds('observed')).toEqual(['finding-observed-moon']);
  });

  it('hides them when the predicted layer is turned off', () => {
    const base = poseSession();
    const off: ObservationSession = {
      ...base,
      spatialView: { ...base.spatialView, activeOverlays: { ...base.spatialView.activeOverlays, predictedEphemerides: false } },
    };
    renderOverlay(off);
    expect(markerIds('predicted')).toEqual([]);
    expect(rings()).toEqual([]);
    expect(markerIds('observed')).toEqual(['finding-observed-moon']);
    expect(linedUpSky(off).kind).toBe('none');
    // A session whose layers do not say the predicted one is on (the contract requires the toggle) draws none either
    const { predictedEphemerides: _on, ...unsaid } = base.spatialView.activeOverlays;
    const silent = { ...base, spatialView: { ...base.spatialView, activeOverlays: unsaid } } as unknown as ObservationSession;
    renderOverlay(silent);
    expect(markerIds('predicted')).toEqual([]);
  });

  it('draws no predicted marker over the photo without a pose estimate, or for a predicted_only result', () => {
    for (const session of [
      poseSession({ ...POSE, poseEstimate: undefined }),
      poseSession(sample('horizon_predicted_only')),
      poseSession(sample('horizon_approximate')),
    ]) {
      expect(session.spatialView.activeOverlays.predictedEphemerides).toBe(true);
      renderOverlay(session);
      expect(markerIds('predicted')).toEqual([]);
      expect(rings()).toEqual([]);
      expect(linedUpSky(session).kind).toBe('none');
    }
  });
});

describe('the disclosure beside the lined-up markers (REQ-SKY-6.3)', () => {
  it('names the basis and the uncertainty with the predicted label, and says the dashed markers are drawn', () => {
    renderStage(poseSession());
    const note = container.querySelector('[data-testid="lined-up-sky-disclosure"]') as HTMLElement;
    expect(note).not.toBeNull();
    expect(note.dataset.evidenceClass).toBe('predicted');
    // The bright spot is said to be taken for the Moon, never to be it: one frame cannot prove it (decision 5)
    expect(note.textContent).toBe(
      "PRD · Lined up by taking a bright spot to be the Moon, with the camera's field of view and a level head. Drawn by the app, not measured in your photo, and not checked against stars. Within about 3 degrees at the middle of the frame, less near the bright spot taken to be the Moon and more away from it. Each dashed ring shows how far its marker may be off, if those assumptions hold."
    );
    expect(note.textContent).not.toMatch(/from the Moon|near the Moon/);
    const credits = container.querySelector('.alb-layer-credits')!.textContent!;
    expect(credits).not.toContain('Each marker sits on the pixel it was measured at.');
    expect(credits).toContain('Each solid marker sits on the pixel it was measured at; the dashed ones are drawn by the app.');
  });

  it('keeps the solid and dashed markers apart when the browser cannot show the file, and calls no marker a star (verdict finding 1)', () => {
    const session = poseSession();
    renderStage(session);
    act(() => {
      container.querySelector('img')!.dispatchEvent(new Event('error'));
    });
    expect(container.querySelector('[data-testid="source-photo-unavailable"]')).not.toBeNull();
    // The one solid marker of this lined-up sky is the measured bright spot, which no star was matched to
    expect(session.observationSolution!.findings.filter((f) => f.sourceDetection).map((f) => f.evidenceClass)).toEqual(['observed']);
    expect(container.querySelector('.alb-layer-credits')!.textContent).toBe(
      'Each solid marker sits where its light was measured in the stored file; the dashed ones are drawn by the app.'
    );
    expect(container.querySelector('[data-testid="lined-up-sky-disclosure"]')).not.toBeNull();
  });

  it('says each marker sits where its light was measured when the browser cannot show a file with no lined-up sky (verdict finding 1)', () => {
    // Unmatched points of light only, a calibrated photo whose markers include one unmatched point, and a predicted
    // sky with nothing measured in the frame
    for (const name of ['plate_too_few_sources', 'plate_calibrated', 'horizon_predicted_only']) {
      for (const mediaKind of ['image', 'video'] as const) {
        const session = poseSession(sample(name), mediaKind);
        renderStage(session);
        act(() => {
          container.querySelector(mediaKind === 'video' ? 'video' : 'img')!.dispatchEvent(new Event('error'));
        });
        expect(container.querySelector('[data-testid="source-photo-unavailable"]'), name).not.toBeNull();
        expect(container.querySelector('.alb-layer-credits')!.textContent, name).toBe(
          'Each marker sits where its light was measured in the stored file.'
        );
        act(() => root.unmount());
        root = createRoot(container);
      }
    }
    expect(sample('plate_too_few_sources').findings.every((f) => f.evidenceClass === 'observed')).toBe(true);
    expect(sample('plate_calibrated').findings.some((f) => f.evidenceClass === 'observed')).toBe(true);
  });

  it('never calls what a marker sits on a star, shown, turned the other way or not shown, for any sample', () => {
    const names = ['pose_moon_level_head', 'horizon_approximate', 'horizon_predicted_only', 'plate_calibrated', 'plate_too_few_sources', 'plate_wcs_failed_verification'];
    const seen = new Set<string>();
    for (const name of names) {
      for (const state of ['shown', 'failed', 'mismatched'] as const) {
        renderStage(poseSession(sample(name)));
        const img = container.querySelector('img') as HTMLImageElement;
        if (state === 'failed') act(() => void img.dispatchEvent(new Event('error')));
        if (state === 'mismatched') {
          Object.defineProperty(img, 'naturalWidth', { value: 540, configurable: true });
          Object.defineProperty(img, 'naturalHeight', { value: 960, configurable: true });
          act(() => void img.dispatchEvent(new Event('load')));
        }
        // The first credits line is the one about the markers; the disclosure after it speaks of the sky's line-up
        const aboutMarkers = container.querySelector('.alb-layer-credits')!.textContent!;
        expect(aboutMarkers, `${name} ${state}`).not.toMatch(/star/i);
        seen.add(aboutMarkers);
        act(() => root.unmount());
        root = createRoot(container);
      }
    }
    // Every sentence the stage has about its markers was reached: two shown, two not shown, one turned
    expect(seen.size).toBe(5);
  });

  it('says nothing about the lined-up sky when the markers are hidden over a frame turned the other way', () => {
    renderStage(poseSession());
    const img = container.querySelector('img') as HTMLImageElement;
    // The browser decodes a portrait frame for a landscape asset
    Object.defineProperty(img, 'naturalWidth', { value: 540, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: 960, configurable: true });
    act(() => {
      img.dispatchEvent(new Event('load'));
    });
    expect(container.querySelector('[data-testid="source-photo-orientation-mismatch"]')).not.toBeNull();
    expect(disclosure()).toBeNull();
    expect(goToLine()).toBeNull();
  });

  it('says nothing about a lined-up sky when none is drawn', () => {
    renderStage(poseSession({ ...POSE, poseEstimate: undefined }));
    expect(disclosure()).toBeNull();
    expect(goToLine()).toBeNull();
    expect(container.querySelector('.alb-layer-credits')!.textContent).toContain(
      'Each marker sits on the pixel it was measured at.'
    );
  });
});

describe('P-SKY-9: on a clip, markers show only while not playing and the clock is within the frame window of their own frame', () => {
  // The window is the longer of one frame and 0.05 s: at 30 fps, 0.05 s
  const WINDOW_SEC = Math.max(1 / CLIP_FPS, 0.05);
  const clipAt = (currentSourceTimeSec: number, isPlaying: boolean): ObservationSession => ({
    ...poseSession(clipPoseSolution(3, 5), 'video'),
    currentSourceTimeSec,
    isPlaying,
  });

  it('opens the clip paused at the solved frame, lined-up markers shown', () => {
    const opened = poseSession(clipPoseSolution(3, 5), 'video');
    expect(opened.isPlaying).toBe(false);
    expect(opened.currentSourceTimeSec).toBe(3);
    // Back to Moment returns to the frame the clip opened on
    expect(opened.navigationHistory.anchorMomentCheckpoint.sourceTimeSec).toBe(3);
    expect(opened.navigationHistory.checkpoints).toEqual([opened.navigationHistory.anchorMomentCheckpoint]);
    renderOverlay(opened);
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    expect(linedUpSky(opened).kind).toBe('shown');
  });

  it('hides the lined-up and the observed markers while the clip plays, and says which moment to go to', () => {
    const playing = clipAt(3, true);
    renderOverlay(playing);
    expect(markerIds('predicted')).toEqual([]);
    expect(markerIds('observed')).toEqual([]);
    expect(rings()).toEqual([]);
    renderStage(playing);
    expect(disclosure()).toBeNull();
    expect(goToLine()!.textContent).toBe('Lined up 3.0 seconds into the clip. Say or type "jump to 3.0 seconds" to see it.');
  });

  it('shows them within 0.05 s of their frame on a 30 fps clip, wider than its one frame, and hides them beyond', () => {
    expect(WINDOW_SEC).toBe(0.05);
    renderOverlay(clipAt(3 + WINDOW_SEC, false));
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    renderOverlay(clipAt(3 - WINDOW_SEC, false));
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    const beyond = clipAt(3 + 1.01 * WINDOW_SEC, false);
    renderOverlay(beyond);
    expect(markerIds('predicted')).toEqual([]);
    expect(linedUpSky(beyond)).toMatchObject({ kind: 'go_to', frameTimeSec: 3 });
  });

  it('gates each observed marker by the frame it was measured on, not the pose frame', () => {
    renderOverlay(clipAt(3, false));
    expect(markerIds('observed')).toEqual(['finding-observed-moon']);
    renderOverlay(clipAt(5, false));
    expect(markerIds('observed')).toEqual(['finding-observed-other-frame']);
    expect(markerIds('predicted')).toEqual([]);
    renderOverlay(clipAt(4, false));
    expect(markerIds('observed')).toEqual([]);
  });

  it('PBT: a clip marker is visible exactly when not playing within max(one frame, 0.05 s) of its time; a still is always', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 240_000 }),
        fc.integer({ min: 1, max: 1001 }),
        fc.double({ min: 0, max: 60, noNaN: true }),
        fc.double({ min: -3, max: 3, noNaN: true }),
        fc.boolean(),
        fc.constantFrom<'image' | 'video'>('image', 'video'),
        (fpsNumerator, fpsDenominator, frameTimeSec, framesAway, isPlaying, mediaKind) => {
          // framesAway counts windows: the window is one frame, or 0.05 s when a frame is shorter
          const window = Math.max(fpsDenominator / fpsNumerator, 0.05);
          const t = frameTimeSec + framesAway * window;
          fc.pre(t >= 0);
          const session = {
            sourceAsset: mediaKind === 'video' ? { mediaKind, fpsNumerator, fpsDenominator } : { mediaKind },
            isPlaying,
            currentSourceTimeSec: t,
          } as unknown as ObservationSession;
          const shown = shownAtFrame(session, frameTimeSec);
          if (mediaKind === 'image') expect(shown).toBe(true);
          else if (isPlaying) expect(shown).toBe(false);
          else if (Math.abs(framesAway) <= 0.999) expect(shown).toBe(true);
          else if (Math.abs(framesAway) >= 1.001) expect(shown).toBe(false);
        }
      ),
      {
        seed: SEED,
        numRuns: 500,
        examples: [
          [30, 1, 3, 0, false, 'video'],
          [30, 1, 3, 0, true, 'video'],
          [30, 1, 3, 1.5, false, 'video'],
          [30, 1, 3, -0.5, false, 'video'],
          [30, 1, 3, 0, true, 'image'],
          // A frame longer than 0.05 s (10 fps) and one shorter (30 fps), each at 0.9 and 1.1 windows
          [10, 1, 3, 0.9, false, 'video'],
          [10, 1, 3, 1.1, false, 'video'],
          [30, 1, 3, 0.9, false, 'video'],
          [30, 1, 3, -1.1, false, 'video'],
        ],
      }
    );
  });

  it('takes one frame as the window when a frame is longer than 0.05 s, counting exactly one frame as within it', () => {
    const session = (t: number) =>
      ({ sourceAsset: { mediaKind: 'video', fpsNumerator: 10, fpsDenominator: 1 }, isPlaying: false, currentSourceTimeSec: t }) as unknown as ObservationSession;
    expect(shownAtFrame(session(1 / 10), 0)).toBe(true);
    expect(shownAtFrame(session(2 / 10), 1 / 10)).toBe(true);
    expect(shownAtFrame(session(1.001 / 10), 0)).toBe(false);
  });

  it('takes 0.05 s as the window when a frame is shorter, or the clip has no frame rate', () => {
    for (const rate of [{ fpsNumerator: 30, fpsDenominator: 1 }, { fpsNumerator: 240, fpsDenominator: 1 }, {}]) {
      const session = (t: number) =>
        ({ sourceAsset: { mediaKind: 'video', ...rate }, isPlaying: false, currentSourceTimeSec: t }) as unknown as ObservationSession;
      expect(shownAtFrame(session(3), 3)).toBe(true);
      expect(shownAtFrame(session(3.05), 3)).toBe(true);
      expect(shownAtFrame(session(2.95), 3)).toBe(true);
      expect(shownAtFrame(session(3.0501), 3)).toBe(false);
      expect(shownAtFrame(session(2.9499), 3)).toBe(false);
    }
  });
});

describe('the stage shows the frame the session clock names (verdict finding 1)', () => {
  it('seeks the clip to the clock: a spoken jump moves the frame on screen, and the markers follow it', () => {
    let session = clipOn(3);
    renderScene(session);
    const video = loadClip();
    expect(video.currentTime).toBe(3);
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    expect(markerIds('observed').length).toBe(2);
    expect(disclosure()).not.toBeNull();

    session = say(session, 'jump to 5 seconds');
    expect(session.currentSourceTimeSec).toBe(5);
    renderScene(session);
    // The frame at 5 s is on screen, so nothing measured or lined up on the frame at 3 s is drawn over it
    expect(video.currentTime).toBe(5);
    expect(markerIds('predicted')).toEqual([]);
    expect(markerIds('observed')).toEqual([]);
    expect(disclosure()).toBeNull();
    expect(goToLine()!.textContent).toBe('Lined up 3.0 seconds into the clip. Say or type "jump to 3.0 seconds" to see it.');

    session = typeIn(session, quotedCommand(goToLine()!.textContent!).command);
    renderScene(session);
    expect(video.currentTime).toBe(3);
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    expect(markerIds('observed').length).toBe(2);
    expect(goToLine()).toBeNull();
  });

  it('lands inside the window when the command the line quotes is typed, at 2.5 s, 99 / 29.4 s and 3601 / 29.97 s', () => {
    for (const [frameTimeSec, facts, named] of [
      [75 / 30, undefined, '2.5'],
      [99 / 29.4, { fpsNumerator: 294, fpsDenominator: 10, durationMs: 10_000 }, '3.4'],
      [3601 / 29.97, { fpsNumerator: 29_970, fpsDenominator: 1000, durationMs: 600_000 }, '120.2'],
    ] as const) {
      let session = say(clipOn(frameTimeSec, facts), 'jump to 0 seconds');
      renderScene(session);
      const video = loadClip();
      expect(video.currentTime).toBe(0);
      expect(markerIds('predicted')).toEqual([]);
      const line = goToLine()!.textContent!;
      expect(line).toBe(`Lined up ${named} seconds into the clip. Say or type "jump to ${named} seconds" to see it.`);
      session = typeIn(session, quotedCommand(line).command);
      renderScene(session);
      expect(video.currentTime).toBe(Number(named));
      expect(markerIds('predicted').length).toBe(IN_FRAME);
      expect(markerIds('observed').length).toBe(2);
      act(() => root.unmount());
      root = createRoot(container);
    }
  });

  it('PBT: typing exactly the command the line quotes lands inside the window, for frame times n / fps over a 10 minute clip', () => {
    const CLIP_SEC = 600;
    fc.assert(
      fc.property(
        fc.oneof(
          fc.record({ num: fc.integer({ min: 10, max: 240 }), den: fc.constant(1) }),
          fc.record({ num: fc.integer({ min: 10_000, max: 240_000 }), den: fc.constantFrom(1000, 1001) })
        ),
        fc.nat({ max: 10_000_000 }),
        ({ num, den }, index) => {
          // As sky-3 takes a frame's time: its index over the probed rate
          const fps = num / den;
          const n = index % (Math.floor(CLIP_SEC * fps) + 1);
          const frameTimeSec = n / fps;
          const facts = { fpsNumerator: num, fpsDenominator: den, durationMs: CLIP_SEC * 1000 };
          const solution: ObservationSolution = { ...POSE, frameTimeSec };
          const away = { ...poseSession(solution, 'video', facts), currentSourceTimeSec: frameTimeSec < 300 ? CLIP_SEC : 0 };
          expect(linedUpSky(away).kind).toBe('go_to');
          const { command, namedSec } = quotedCommand(goToMomentSentence(frameTimeSec));
          // The nearest tenth
          expect(Math.abs(namedSec - frameTimeSec)).toBeLessThanOrEqual(0.05 + 1e-9);
          const there = typeIn(away, command);
          expect(there.currentSourceTimeSec).toBe(namedSec);
          expect(linedUpSky(there).kind).toBe('shown');
          expect(shownAtFrame(there, frameTimeSec)).toBe(true);
        }
      ),
      {
        seed: SEED,
        numRuns: 300,
        examples: [
          [{ num: 30, den: 1 }, 75],
          [{ num: 294, den: 10 }, 99],
          // 7 / 20 is 0.35 s, named 0.4: 0.05 s away in decimals, a hair more in binary floats
          [{ num: 20, den: 1 }, 7],
          // Over a minute: 3601 / 29.97 is about 120.15 s, named 120.2
          [{ num: 29_970, den: 1000 }, 3601],
          [{ num: 30_000, den: 1001 }, 17_981],
          [{ num: 240, den: 1 }, 143_999],
        ],
      }
    );
  });

  it('keeps the frame and the markers through the compare wipe, and follows a seek made while comparing', () => {
    const variants = createSyntheticCalibratedSession().mediaVariants.slice(0, 2);
    let session: ObservationSession = { ...clipOn(3), mediaVariants: variants.map((v) => ({ ...v, sourceAssetId: POSE.sourceAssetId })) };
    renderScene(session);
    const video = loadClip();
    session = dispatchAlbedianAction(session, {
      type: 'media.compare',
      requestId: 'compare',
      expectedViewRevision: session.viewRevision,
      variantAId: variants[0]!.id,
      variantBId: variants[1]!.id,
      preserveTime: true,
    }).session;
    expect(session.machines.navigation).toBe('comparison');
    // The wipe slider moves only the wipe (ObservationSolveAndCompareHud handleWipeSliderChange)
    session = { ...session, compareWipePosition: 0.2 };
    renderScene(session);
    expect(stageVideo()).toBe(video);
    expect(video.currentTime).toBe(3);
    expect(markerIds('predicted').length).toBe(IN_FRAME);
    session = say(session, 'jump to 5 seconds');
    renderScene(session);
    expect(video.currentTime).toBe(5);
    expect(markerIds('predicted')).toEqual([]);
  });

  it('opens a discovered clip lined up at 3.0 s on that frame, with no line, and follows a seek', () => {
    // As the laptop's clips come: predicted_only on the frame at 3.0 s, no pose, no detection
    const item: DiscoveredMediaItem = {
      id: 'synthetic',
      title: 'Synthetic',
      filename: 'synthetic.mov',
      sha256: POSE.sourceSha256!,
      mediaKind: 'video',
      width: 960,
      height: 540,
      durationSec: 15,
      fps: 29.917,
      videoCodec: 'hevc',
      capturedAtUTC: '2030-01-01T02:00:00Z',
      latDeg: 30.0,
      lonDeg: -40.0,
      cameraModel: null,
      urls: { origUrl: '/synthetic.mov', v1Url: '/synthetic.mov', v2Url: '/synthetic.mov' },
      solution: { ...sample('horizon_predicted_only'), frameTimeSec: 3 },
    };
    let session = buildSessionFromDiscoveredMedia(item);
    renderScene(session);
    const video = loadClip();
    expect(video.currentTime).toBe(3);
    expect(linedUpSky(session).kind).toBe('none');
    expect(goToLine()).toBeNull();
    expect(disclosure()).toBeNull();
    session = say(session, 'jump to 5 seconds');
    renderScene(session);
    expect(video.currentTime).toBe(5);
  });
});

describe('Show Predicted Sky and Back to Moment for a lined-up upload (REQ-SKY-6.5)', () => {
  it('opens the predicted sky for an approximate or predicted_only upload, and returns to the same lined-up frame', () => {
    for (const solution of [clipPoseSolution(3, 5), sample('horizon_predicted_only')]) {
      let session = poseSession(solution, 'video');
      const setSession = (next: ObservationSession | ((cur: ObservationSession) => ObservationSession)) => {
        session = typeof next === 'function' ? next(session) : next;
      };
      act(() => {
        root.render(createElement(ObservationSolveAndCompareHud, { session, setSession }));
      });
      const button = container.querySelector('[data-testid="solve-register-sky-btn"]') as HTMLButtonElement;
      expect(button.textContent).toBe('Show Predicted Sky');
      expect(button.disabled).toBe(false);
      act(() => button.click());
      expect(session.spatialView.mode).toBe('registered_sky');
      expect(session.spatialView.activeOverlays.predictedEphemerides).toBe(true);
      session = dispatchAlbedianAction(session, {
        type: 'source.return',
        requestId: 'back-to-moment',
        expectedViewRevision: session.viewRevision,
        restoreAudio: true,
      }).session;
      expect(session.spatialView.mode).toBe('source_2d');
      expect(session.currentSourceTimeSec).toBe(solution.frameTimeSec);
      expect(session.isPlaying).toBe(false);
      expect(linedUpSky(session).kind).toBe(solution.poseEstimate ? 'shown' : 'none');
      // Back in the source view the clip is a new element, on the same frame, with the same markers
      renderScene(session);
      expect(loadClip().currentTime).toBe(solution.frameTimeSec);
      expect(markerIds('predicted').length).toBe(solution.poseEstimate ? IN_FRAME : 0);
    }
  });

  it('returns to the moment the person left from, after a seek, and the line still names the lined-up frame', () => {
    let session = say(clipOn(3), 'jump to 5 seconds');
    session = dispatchAlbedianAction(session, {
      type: 'sky.show',
      requestId: 'show-predicted',
      expectedViewRevision: session.viewRevision,
      mode: 'predicted',
    }).session;
    expect(session.spatialView.mode).toBe('registered_sky');
    session = dispatchAlbedianAction(session, {
      type: 'source.return',
      requestId: 'back-to-moment',
      expectedViewRevision: session.viewRevision,
      restoreAudio: true,
    }).session;
    expect(session.currentSourceTimeSec).toBe(5);
    renderScene(session);
    expect(loadClip().currentTime).toBe(5);
    expect(markerIds('predicted')).toEqual([]);
    expect(goToLine()!.textContent).toBe('Lined up 3.0 seconds into the clip. Say or type "jump to 3.0 seconds" to see it.');
  });
});

describe('the predicted layer of a session the builders made', () => {
  it('is on for approximate and predicted_only uploads and off for the others', () => {
    for (const [name, on] of [
      ['pose_moon_level_head', true],
      ['horizon_approximate', true],
      ['horizon_predicted_only', true],
      ['plate_calibrated', false],
      ['plate_too_few_sources', false],
    ] as const) {
      const session = poseSession(sample(name));
      expect(session.spatialView.activeOverlays.predictedEphemerides, name).toBe(on);
      // Back to Moment returns to the same layers
      expect(session.navigationHistory.anchorMomentCheckpoint.spatialView.activeOverlays.predictedEphemerides, name).toBe(on);
    }
    expect(predictedOf(POSE).length).toBeGreaterThan(IN_FRAME);
  });
});
