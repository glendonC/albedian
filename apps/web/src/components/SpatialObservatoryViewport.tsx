import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  DiscoveredMediaItem,
} from '../services/apiClient.js';
import type { RenderLayerSet } from '@albedian/contracts';
import {
  SparkPortalRenderer,
  SpatialScaleRegime,
} from '../spatial/SparkPortalRenderer.js';
import {
  WEBGPU_PORTED_SHOWCASE_SCENES,
  type CosmosRenderer,
  type CosmosRendererKind,
} from '../spatial/cosmosRenderer.js';
import {
  observatoryAudio,
} from '../audio/ObservatoryAudioEngine.js';
import { skyAudio } from '../audio/SkyAudioFeed.js';
import { AUDIO_NOTICES_URL } from '../audio/audioCredits.js';
import { useSoundtrackMoment } from '../audio/music/soundtrackMoment.js';
import { MusicSettingControl } from '../audio/MusicSettingControl.js';
import { useAudioCreditLines, useAudioDisclosure } from '../audio/useAudioCreditLines.js';
import {
  CoalescingBar,
} from './CoalescingBar.js';
import { RenderLayerDisclosure } from './RenderLayerDisclosure.js';
import { CelestialMarkerOverlay } from './CelestialMarkerOverlay.js';
import { CelestialTargetCard } from './CelestialTargetCard.js';
import { FlightAndRestoreHud } from './FlightAndRestoreHud.js';
import { EpochAndDisclosureHud } from '../spatial/EpochAndDisclosureHud.js';
import { ObservationSolveAndCompareHud } from '../spatial/ObservationSolveAndCompareHud.js';
import { VoiceCommandHud } from './VoiceCommandHud.js';
import { SourcePhotoStage } from './SourcePhotoStage.js';
import { isSourcePhotoShown } from './sourcePhotoRect.js';
import '../styles/albedian.css';

export interface SpatialObservatoryViewportProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
  selectedFinding: CelestialFinding | null;
  discoveredMedia: DiscoveredMediaItem[];
  activeDiscoveredId: string | null;
  setActiveDiscoveredId: React.Dispatch<React.SetStateAction<string | null>>;
  activeDiscoveredItem: DiscoveredMediaItem | null;
  uploadedMediaUrl: string | null;
  setUploadedMediaUrl: React.Dispatch<React.SetStateAction<string | null>>;
  commandText: string;
  setCommandText: React.Dispatch<React.SetStateAction<string>>;
  onRunUtterance: (
    utterance: string,
    modality?: 'voice' | 'text' | 'pointer'
  ) => void;
  onSelectFinding: (
    findingId: string,
    pointerCoords?: { xPx: number; yPx: number }
  ) => void;
  onPushToTalk: () => void;
  onTriggerUpload: () => void;
}

const LANDING_SHOWCASE_SCENES = [
  {
    key: 'cislunar' as const,
    num: '01',
    title: 'Cislunar Orbit',
    subtitle: 'Earth & Moon · 384,400 km',
  },
  {
    key: 'black_hole' as const,
    num: '02',
    title: 'Sagittarius A*',
    subtitle: 'Modelled black hole · 26,670 ly',
  },
  {
    key: 'supergiant' as const,
    num: '03',
    title: 'Betelgeuse 3D',
    subtitle: 'Red Supergiant & Local Bubble · 548 ly',
  },
  {
    key: 'saturn_horizon' as const,
    num: '04',
    title: 'Ringed Saturn',
    subtitle: 'Titan Horizon · 1.2B km',
  },
] as const;

interface RendererChoice {
  kind: CosmosRendererKind;
  /** Run the WebGPU host's TSL on three's WebGL2 backend (`?backend=webgl`). */
  forceWebGL: boolean;
  /** Scene clock held for matched snapshots (`?t=<seconds>`), or null. */
  heldClockSec: number | null;
}

/**
 * `?renderer=webgpu` selects the WebGPU host, which draws only the scenes ported to it so far;
 * the WebGL2 and Spark renderer stays the default.
 */
function readRendererChoice(): RendererChoice {
  if (typeof window === 'undefined') return { kind: 'webgl-spark', forceWebGL: false, heldClockSec: null };
  const params = new URLSearchParams(window.location.search);
  const heldRaw = params.get('t');
  const held = heldRaw === null ? Number.NaN : Number(heldRaw);
  return {
    kind: params.get('renderer') === 'webgpu' ? 'webgpu' : 'webgl-spark',
    forceWebGL: params.get('backend') === 'webgl',
    heldClockSec: Number.isFinite(held) ? held : null,
  };
}

export const SpatialObservatoryViewport: React.FC<
  SpatialObservatoryViewportProps
> = ({
  session,
  setSession,
  selectedFinding,
  discoveredMedia,
  activeDiscoveredId,
  activeDiscoveredItem: propActiveDiscoveredItem,
  uploadedMediaUrl,
  commandText,
  setCommandText,
  onRunUtterance,
  onSelectFinding,
  onPushToTalk,
  onTriggerUpload,
}) => {
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<CosmosRenderer | null>(null);
  const [rendererChoice] = useState<RendererChoice>(readRendererChoice);
  // Bumped when a renderer is attached, so effects that drive it run once it exists (the WebGPU
  // host is created after an async import)
  const [rendererGeneration, setRendererGeneration] = useState(0);
  const [renderLayers, setRenderLayers] = useState<RenderLayerSet | null>(null);

  // The WebGPU host draws only its ported scenes; the carousel cycles through those
  const showcaseScenes = useMemo(
    () =>
      rendererChoice.kind === 'webgpu'
        ? LANDING_SHOWCASE_SCENES.filter((s) => WEBGPU_PORTED_SHOWCASE_SCENES.includes(s.key))
        : [...LANDING_SHOWCASE_SCENES],
    [rendererChoice.kind]
  );

  const [activeRegime, setActiveRegime] =
    useState<SpatialScaleRegime>('cislunar');
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [volumeLevel, setVolumeLevel] = useState<number>(0.42);

  const [entryStageActive, setEntryStageActive] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    return new URLSearchParams(window.location.search).get('view') !== 'observatory';
  });

  const [showcaseSceneIdx, setShowcaseSceneIdx] = useState<number>(() => {
    if (typeof window === 'undefined') return 0;
    const raw = new URLSearchParams(window.location.search).get('scene');
    if (raw !== null) {
      const idx = Number.parseInt(raw, 10);
      const requested = LANDING_SHOWCASE_SCENES[idx];
      if (!Number.isNaN(idx) && requested) {
        // `scene` indexes the full carousel; a scene the active renderer lacks falls back to its first
        const available = showcaseScenes.findIndex((s) => s.key === requested.key);
        if (available < 0) {
          console.info(`[viewport] scene ${requested.key} is not ported to the WebGPU host; showing ${showcaseScenes[0]?.key}`);
        }
        return Math.max(0, available);
      }
    }
    return 0;
  });
  // Markers are hidden when the browser turned the photo differently from its orientation tag
  const [photoFrameMismatch, setPhotoFrameMismatch] = useState(false);

  // Choosing a file to upload leaves the landing, so the upload, its solve and the photo show at once
  useEffect(() => {
    if (uploadedMediaUrl) setEntryStageActive(false);
  }, [uploadedMediaUrl]);

  // The showcase scene last started on which renderer; null scene after leaving the landing
  const appliedShowcaseRef = useRef<{ renderer: CosmosRenderer | null; sceneKey: string | null }>({
    renderer: null,
    sceneKey: null,
  });

  const dragStateRef = useRef<{
    dragging: boolean;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
  }>({
    dragging: false,
    startX: 0,
    startY: 0,
    lastX: 0,
    lastY: 0,
  });

  const activeDiscoveredItem = useMemo(() => {
    if (propActiveDiscoveredItem) return propActiveDiscoveredItem;
    return discoveredMedia.find((item) => item.id === activeDiscoveredId) ?? null;
  }, [propActiveDiscoveredItem, discoveredMedia, activeDiscoveredId]);

  // Location is unknown until the capture's metadata or the user supplies it
  const observerLat =
    session.sourceAsset?.observerLocation?.latDeg ??
    activeDiscoveredItem?.latDeg ??
    null;
  const observerLon =
    session.sourceAsset?.observerLocation?.lonDeg ??
    activeDiscoveredItem?.lonDeg ??
    null;
  const observerCapturedAtUtc = session.sourceAsset?.capturedAtUTC ?? activeDiscoveredItem?.capturedAtUTC ?? null;

  // Initialize the cosmos renderer: WebGL2 + @sparkjsdev/spark by default, the WebGPU host behind ?renderer=webgpu
  useEffect(() => {
    const host = canvasHostRef.current;
    if (!host) return;
    let disposed = false;
    let attached: CosmosRenderer | null = null;

    const handleResize = () => {
      attached?.resize(host.clientWidth || 1440, host.clientHeight || 900);
    };
    const attach = (renderer: CosmosRenderer) => {
      if (disposed) {
        renderer.dispose();
        return;
      }
      attached = renderer;
      rendererRef.current = renderer;
      if (rendererChoice.heldClockSec !== null) renderer.holdClock(rendererChoice.heldClockSec);
      handleResize();
      setRenderLayers(renderer.getRenderLayers?.() ?? null);
      setRendererGeneration((g) => g + 1);
    };

    const onRegimeChange = (nextRegime: SpatialScaleRegime) => {
      setActiveRegime(nextRegime);
      observatoryAudio.setScaleRegime(nextRegime);
      // What is drawn changes with the regime; before attach() the renderer is still being built
      if (attached) setRenderLayers(attached.getRenderLayers?.() ?? null);
    };
    if (rendererChoice.kind === 'webgpu') {
      // Loaded on demand so the default WebGL path does not carry three's WebGPU build
      void import('../spatial/webgpu/CislunarWebGpuRenderer.js').then(
        ({ CislunarWebGpuRenderer }) =>
          attach(
            new CislunarWebGpuRenderer(host, {
              forceWebGL: rendererChoice.forceWebGL,
              onRegimeChange,
              exposeForAutomation: true,
            })
          ),
        (error: unknown) => console.error('[viewport] WebGPU host failed to load', error)
      );
    } else {
      attach(new SparkPortalRenderer(host, onRegimeChange));
    }

    window.addEventListener('resize', handleResize);
    return () => {
      disposed = true;
      window.removeEventListener('resize', handleResize);
      attached?.dispose();
      rendererRef.current = null;
    };
  }, [rendererChoice]);

  // Sync observer geographic coordinates into 3D renderer
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    if (observerLat === null || observerLon === null) renderer.clearObserverCoordinates?.();
    else renderer.setObserverCoordinates(observerLat, observerLon, observerCapturedAtUtc);
    // The sky view's horizon and its disclosure follow the place and time, or their absence
    setRenderLayers(renderer.getRenderLayers?.() ?? null);
  }, [observerLat, observerLon, observerCapturedAtUtc, rendererGeneration]);

  // The header slider is the engine's master volume; the engine keeps it until audio starts
  useEffect(() => {
    observatoryAudio.setMasterVolume(volumeLevel);
  }, [volumeLevel]);

  // The soundtrack follows the moment of the visit; the credits name each piece once heard
  useSoundtrackMoment(entryStageActive, session);
  const audioCreditLines = useAudioCreditLines();
  const audioDisclosure = useAudioDisclosure();

  // While audio is on, the catalog stars in view sound and the camera turns the listener
  useEffect(() => {
    if (!soundEnabled) return;
    let frame = 0;
    const follow = () => {
      skyAudio.follow(rendererRef.current?.getCameraSkyPose?.() ?? null);
      frame = window.requestAnimationFrame(follow);
    };
    frame = window.requestAnimationFrame(follow);
    return () => window.cancelAnimationFrame(frame);
  }, [soundEnabled]);

  // The voices carry the evidence class of the star layer they sound; none where it is undeclared
  useEffect(() => {
    skyAudio.setEvidenceClass(renderLayers?.find((layer) => layer.kind === 'catalog_stars')?.evidenceClass ?? null);
  }, [renderLayers]);

  // Landing Page Non-Interactive 3D Background Scene Carousel
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    if (!entryStageActive) {
      renderer.setShowcaseMode(false);
      appliedShowcaseRef.current.sceneKey = null;
      return;
    }

    renderer.setShowcaseMode(true);
    const sceneDef = showcaseScenes[showcaseSceneIdx] ?? showcaseScenes[0];
    if (!sceneDef) return;
    // Start the scene when it changes or a renderer attaches; a renderer's first scene starts
    // without the warp-in. Re-runs for other reasons leave the running scene alone.
    const applied = appliedShowcaseRef.current;
    if (applied.renderer !== renderer || applied.sceneKey !== sceneDef.key) {
      renderer.transitionToShowcaseScene(sceneDef.key, applied.renderer !== renderer);
      appliedShowcaseRef.current = { renderer, sceneKey: sceneDef.key };
    }

    if (typeof window === 'undefined') return;
    const freeze = new URLSearchParams(window.location.search).get('freeze') === '1';
    // A single scene has nothing to cycle to; re-entering it would replay the warp-in
    if (freeze || showcaseScenes.length < 2) return;

    const DURATION_MS = 7200;
    const timerId = window.setTimeout(() => {
      setShowcaseSceneIdx((prev) => (prev + 1) % showcaseScenes.length);
    }, DURATION_MS);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [entryStageActive, showcaseSceneIdx, showcaseScenes, rendererGeneration]);

  // Keyboard navigation (WASD + Arrows)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (entryStageActive) return;
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      const renderer = rendererRef.current;
      if (!renderer) return;

      switch (e.key) {
        case 'w':
        case 'W':
          renderer.nudgeMovement(0, 0, -1.6);
          break;
        case 's':
        case 'S':
          renderer.nudgeMovement(0, 0, 1.6);
          break;
        case 'a':
        case 'A':
          renderer.nudgeMovement(-1.6, 0, 0);
          break;
        case 'd':
        case 'D':
          renderer.nudgeMovement(1.6, 0, 0);
          break;
        case 'ArrowLeft':
          renderer.nudgeLook(0.08, 0);
          break;
        case 'ArrowRight':
          renderer.nudgeLook(-0.08, 0);
          break;
        case 'ArrowUp':
          renderer.nudgeLook(0, 0.06);
          break;
        case 'ArrowDown':
          renderer.nudgeLook(0, -0.06);
          break;
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [entryStageActive]);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (entryStageActive) return;
      if ((e.target as HTMLElement).closest('[data-hud-interactive="true"]')) {
        return;
      }
      dragStateRef.current = {
        dragging: true,
        startX: e.clientX,
        startY: e.clientY,
        lastX: e.clientX,
        lastY: e.clientY,
      };
    },
    [entryStageActive]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (entryStageActive) return;
      const st = dragStateRef.current;
      if (!st.dragging) return;
      const dx = e.clientX - st.lastX;
      const dy = e.clientY - st.lastY;
      st.lastX = e.clientX;
      st.lastY = e.clientY;
      rendererRef.current?.dragLook(dx, dy);
    },
    [entryStageActive]
  );

  const handlePointerUp = useCallback(() => {
    dragStateRef.current.dragging = false;
  }, []);

  const handleWheel = useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      if (entryStageActive) return;
      rendererRef.current?.zoomByWheel(e.deltaY);
    },
    [entryStageActive]
  );

  const handleToggleSound = useCallback(() => {
    const next = observatoryAudio.toggle();
    setSoundEnabled(next);
  }, []);

  return (
    <div
      className="alb-root"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onWheel={handleWheel}
    >
      {/* Full-Screen WebGL2 + @sparkjsdev/spark 3D Gaussian Splatting Canvas */}
      <div
        ref={canvasHostRef}
        className="alb-canvas-host"
        data-testid="spark-portal-canvas"
      />

      {/* While the photo covers the canvas, the stage's own label says what is on screen */}
      {renderLayers && !(!entryStageActive && isSourcePhotoShown(session)) && (
        <RenderLayerDisclosure layers={renderLayers} />
      )}

      {/* =======================================================================
          SCREEN 1: CLEAN LANDING PAGE
          ======================================================================= */}
      {entryStageActive && (
        <>
          <div className="alb-nazyurtal-entry-mask" />

          <footer
            className="alb-nazyurtal-footer"
            data-hud-interactive="true"
            data-testid="nazyurtal-entry-footer"
          >
            <div className="alb-nazyurtal-split">
              <div className="alb-nazyurtal-left">
                <h1 className="alb-nazyurtal-headline">
                  Discover the universe behind
                  <br />
                  <span className="alb-nazyurtal-headline-muted">
                    the buried night sky.
                  </span>
                </h1>
              </div>
              <div className="alb-nazyurtal-right">
                <p className="alb-nazyurtal-copy">
                  Light pollution hides the Milky Way from more than a third of
                  humanity, severing our perspective in the cosmos. Upload a night photo to find
                  where and when it was taken, see the real sky behind the glow,
                  and fly through the 3D universe behind your frame.
                </p>
                <div className="alb-nazyurtal-actions">
                  <button
                    type="button"
                    data-testid="enter-observatory-btn"
                    className="alb-nazyurtal-btn-primary"
                    onClick={() => {
                      setEntryStageActive(false);
                      rendererRef.current?.setShowcaseMode(false);
                      observatoryAudio.playDetentClick(1020);
                    }}
                  >
                    <span className="alb-btn-primary-label">Launch Albedian</span>
                    <svg
                      width="13"
                      height="12"
                      viewBox="0 0 13 12"
                      fill="none"
                      aria-hidden="true"
                      className="alb-btn-arrow-svg"
                    >
                      <path
                        d="M11.9893 5.58371L12.2471 5.89914L11.9893 6.21555L8.10059 10.9782L7.3252 10.3454L10.5479 6.39914L1.39941 6.39914L1.39941 5.39914L10.5479 5.39914L7.3252 1.45383L8.10059 0.821014L11.9893 5.58371Z"
                        fill="currentColor"
                        fillOpacity="0.85"
                      />
                    </svg>
                  </button>
                  <button
                    type="button"
                    className="alb-nazyurtal-btn-secondary"
                    onClick={onTriggerUpload}
                  >
                    Upload a photo
                  </button>
                </div>
              </div>
            </div>
          </footer>
        </>
      )}

      {!entryStageActive && (
        <>
          <SourcePhotoStage session={session} onFrameMismatch={setPhotoFrameMismatch} />
          {!(photoFrameMismatch && isSourcePhotoShown(session)) && (
            <CelestialMarkerOverlay
              session={session}
              selectedFinding={selectedFinding}
              onSelectFinding={onSelectFinding}
            />
          )}
          <CelestialTargetCard
            session={session}
            selectedFinding={selectedFinding}
            onRunUtterance={onRunUtterance}
            onPushToTalk={onPushToTalk}
          />
          <FlightAndRestoreHud
            session={session}
            setSession={setSession}
          />
          <ObservationSolveAndCompareHud
            session={session}
            setSession={setSession}
          />
          <EpochAndDisclosureHud
            session={session}
            setSession={setSession}
          />
          <VoiceCommandHud
            voiceState={session.machines?.voice ?? 'off'}
            lastStatusBanner={session.lastStatusBanner ?? null}
            lastUtterance={session.voiceActions?.at(-1)?.utterance ?? null}
            commandText={commandText}
            setCommandText={setCommandText}
            onRunUtterance={onRunUtterance}
            onPushToTalk={onPushToTalk}
            selectedFinding={selectedFinding && selectedFinding.id === session.selectedFindingId ? selectedFinding : null}
          />
        </>
      )}

      {/* =======================================================================
          TOP BAR: Brand Wordmark + 03-B Mark on Left, Audio Control on Right
          ======================================================================= */}
      <header
        className="alb-lusion-header alb-visor-topright"
        data-hud-interactive="true"
      >
        <div className="alb-lusion-header-left">
          <button
            type="button"
            className="alb-lusion-brand-btn"
            onClick={() => {
              setEntryStageActive(true);
            }}
          >
            <span className="alb-brand-wordmark">albedian</span>
            <svg
              width="18"
              height="18"
              viewBox="0 0 32 32"
              fill="none"
              aria-hidden="true"
              className="alb-brand-mark-svg alb-brand-shoulder-mark"
            >
              <defs>
                <mask id="albMoonStarCutoutMask">
                  <rect width="32" height="32" fill="white" />
                  <path
                    d="M18.2 2.2Q18.2 13.8 29.8 13.8Q18.2 13.8 18.2 25.4Q18.2 13.8 6.6 13.8Q18.2 13.8 18.2 2.2Z"
                    fill="black"
                  />
                </mask>
              </defs>
              <circle
                cx="15.2"
                cy="16.0"
                r="11.5"
                fill="#FFFFFF"
                mask="url(#albMoonStarCutoutMask)"
              />
            </svg>
          </button>
        </div>

        <div className="alb-lusion-header-right">
          <div
            className="alb-zzzz-inline-control"
            data-testid="inline-audio-coalescing"
          >
            <button
              type="button"
              className={`alb-audio-label-btn ${soundEnabled ? 'is-active' : ''}`}
              onClick={handleToggleSound}
              aria-describedby={soundEnabled ? 'alb-audio-disclosure' : undefined}
            >
              Audio
            </button>
            <CoalescingBar
              value={volumeLevel}
              pattern="slats"
              width={128}
              height={14}
              ariaLabel="Audio volume"
              onChange={(nextVal) => {
                setVolumeLevel(nextVal);
                if (!soundEnabled && nextVal > 0.05) {
                  handleToggleSound();
                }
              }}
            />
            {soundEnabled && <MusicSettingControl />}
          </div>
          {/* What the sound is, and the credits and notices of the works the audio engine ships */}
          {soundEnabled && (
            <div className="alb-audio-notes">
              <p id="alb-audio-disclosure" className="alb-audio-disclosure" role="note">{audioDisclosure}</p>
              <ul className="alb-audio-credits" aria-label="Audio credits">
                {audioCreditLines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <a className="alb-audio-notices" href={AUDIO_NOTICES_URL} target="_blank" rel="noopener noreferrer">
                Notices
              </a>
            </div>
          )}
        </div>
      </header>
    </div>
  );
};
