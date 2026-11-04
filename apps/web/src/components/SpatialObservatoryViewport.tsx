import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  DiscoveredMediaItem,
} from '../services/apiClient.js';
import {
  SparkPortalRenderer,
  SpatialScaleRegime,
} from '../spatial/SparkPortalRenderer.js';
import {
  observatoryAudio,
} from '../audio/ObservatoryAudioEngine.js';
import {
  CoalescingBar,
} from './CoalescingBar.js';
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
    subtitle: 'Relativistic Black Hole · 26,670 ly',
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

export const SpatialObservatoryViewport: React.FC<
  SpatialObservatoryViewportProps
> = ({
  session,
  discoveredMedia,
  activeDiscoveredId,
  activeDiscoveredItem: propActiveDiscoveredItem,
  onTriggerUpload,
}) => {
  const canvasHostRef = useRef<HTMLDivElement | null>(null);
  const rendererRef = useRef<SparkPortalRenderer | null>(null);

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
      if (!Number.isNaN(idx) && idx >= 0 && idx < LANDING_SHOWCASE_SCENES.length) {
        return idx;
      }
    }
    return 0;
  });
  const showcaseMountedRef = useRef<boolean>(false);

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
    session.sourceAsset.observerLocation?.latDeg ??
    activeDiscoveredItem?.latDeg ??
    null;
  const observerLon =
    session.sourceAsset.observerLocation?.lonDeg ??
    activeDiscoveredItem?.lonDeg ??
    null;

  // Initialize WebGL2 + @sparkjsdev/spark 3D Gaussian Splatting Observatory Renderer
  useEffect(() => {
    const host = canvasHostRef.current;
    if (!host) return;
    const renderer = new SparkPortalRenderer(host, (nextRegime) => {
      setActiveRegime(nextRegime);
      observatoryAudio.setScaleRegime(nextRegime);
    });
    rendererRef.current = renderer;
    renderer.setSpectralBand('optical');
    renderer.setSpectralMonocleScreenPos(0.5, 0.5, false);

    const handleResize = () => {
      if (!host) return;
      renderer.resize(host.clientWidth || 1440, host.clientHeight || 900);
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, []);

  // Sync observer geographic coordinates into 3D renderer
  useEffect(() => {
    if (observerLat === null || observerLon === null) return;
    rendererRef.current?.setObserverCoordinates(observerLat, observerLon);
  }, [observerLat, observerLon]);

  // The header slider is the engine's master volume; the engine keeps it until audio starts
  useEffect(() => {
    observatoryAudio.setMasterVolume(volumeLevel);
  }, [volumeLevel]);

  // Landing Page Non-Interactive 3D Background Scene Carousel
  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    if (!entryStageActive) {
      renderer.setShowcaseMode(false);
      return;
    }

    renderer.setShowcaseMode(true);
    const sceneDef =
      LANDING_SHOWCASE_SCENES[showcaseSceneIdx] ?? LANDING_SHOWCASE_SCENES[0];
    const isInitial = !showcaseMountedRef.current;
    showcaseMountedRef.current = true;
    renderer.transitionToShowcaseScene(sceneDef.key, isInitial);

    if (typeof window === 'undefined') return;
    const freeze = new URLSearchParams(window.location.search).get('freeze') === '1';
    if (freeze) return;

    const DURATION_MS = 7200;
    const timerId = window.setTimeout(() => {
      setShowcaseSceneIdx((prev) => (prev + 1) % LANDING_SHOWCASE_SCENES.length);
    }, DURATION_MS);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [entryStageActive, showcaseSceneIdx]);

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
                  Light pollution hides the stars for 83% of humanity, severing
                  our perspective in the cosmos. Upload a night photo to find
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
            >
              Audio
            </button>
            <CoalescingBar
              value={soundEnabled ? volumeLevel : 0.64}
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
          </div>
        </div>
      </header>
    </div>
  );
};
