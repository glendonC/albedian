import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  DiscoveredMediaItem,
} from '../services/apiClient.js';
import { hasVerifiedWcs, type RenderLayerSet } from '@albedian/contracts';
import { dispatchAlbedianAction, returnCheckpointOf } from '@albedian/observation';
import { IconButton, ViewPills } from '../look/index.js';
import { findingCanEnterSpace } from './askSuggestions.js';
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
import { useSelectionTone } from '../audio/useSelectionTone.js';
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
import { HomeScreen } from './HomeScreen.js';
import type { HomeRefusal } from './homeRefusal.js';
import { isSourcePhotoShown, sessionPhotoRect, useViewportSize } from './sourcePhotoRect.js';
import { WorkingScreen } from '../reveal/WorkingScreen.js';
import { RevealPhoto } from '../reveal/RevealPhoto.js';
import { SoundControl } from '../reveal/SoundControl.js';
import { AskBar, type HeardWords } from '../reveal/AskBar.js';
import { canDrawRealSky, isRealSkyShown } from '../reveal/RealSkyLayer.js';
import { flightReadout, isFlightShown } from '../reveal/flightView.js';
import { KeyboardHelp } from '../reveal/KeyboardHelp.js';
import { ClipPlayer } from '../reveal/ClipPlayer.js';
import { realSkyShade } from '../reveal/RealSkyLayer.js';
import { orientedSize } from '@albedian/contracts';
import { isPhoneNoMatchShown, isPhoneRevealShown, phoneEndsMask, phoneSlotCrop } from '../reveal/phoneSlot.js';
import { useRestoration } from '../reveal/restoration.js';
import { sourcePhotoDisplayRect } from './sourcePhotoRect.js';
import type { ShownAnswer } from '../reveal/AnswerPanel.js';
import { usePhoneLayout } from '../reveal/usePhoneLayout.js';
import type { Arrival } from '../reveal/arrival.js';
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
  /** A photo or clip chosen on the home; choosing one leaves the home at once. */
  onFileChosen?: (file: File) => void;
  /** Told when the home's Upload is pressed, as its picker opens. */
  onTriggerUpload?: () => void;
  /** The upload the server last refused; a new one brings the home back to show it beside Upload. */
  uploadRefusal?: HomeRefusal | null;
  /** The upload on its way to a solved sky: while there is one, the working screen shows in place of what follows the solve. */
  arrival?: Arrival | null;
  /** Starts the failed upload's work again (the working screen's Try again). */
  onTryAgain?: () => void;
  /** Asks the server to cancel the upload's job (the working screen's Cancel, D2). */
  onCancelJob?: () => Promise<unknown> | void;
  /** The answer to the last question, or a command's refusal, shown in the card's place over the laptop reveal. */
  answer?: ShownAnswer | null;
  onCloseAnswer?: () => void;
  /** The words heard so far while listening, for the Ask out loud button. */
  heardWords?: HeardWords | null;
  /** Whether this browser can listen; when it cannot, the button reads "Type to ask". */
  canListen?: boolean;
}

/** The reveal's photo column at 1440 by 900 (675 wide), the frame a wide clip's player and shade follow. */
const CLIP_COLUMN_PX = 675;

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
  onFileChosen,
  onTriggerUpload,
  uploadRefusal = null,
  arrival = null,
  answer = null,
  onCloseAnswer,
  heardWords = null,
  canListen = true,
  onTryAgain,
  onCancelJob,
}) => {
  const windowSize = useViewportSize();
  // The approved laptop reveal shows over the photo, above phone width; the phone keeps its sheets until its own unit
  const phone = usePhoneLayout();
  // Real sky (reveal, approved 2026-11-12): the measured sky of a verified solve, drawn over the photo's frame by the
  // reveal itself, the reducer's sky.show having saved the moment to come back to
  const realSkyShown = isRealSkyShown(session);
  // There, after Go there (fly, approved 2026-11-12): the same panorama in the photo's place, the readout in the card's
  const flightShown = isFlightShown(session);
  // pilot-1f (D26 item 6): "Fly on your own" after arrival hands the camera to the person with no star ahead; leaving
  // there ends it
  const [freeFlight, setFreeFlight] = useState(false);
  useEffect(() => {
    if (!flightShown) setFreeFlight(false);
  }, [flightShown]);
  const arrived = flightShown && session.spatialView?.transitionPhase === 'at_space';
  const skyOver = realSkyShown || flightShown;
  // The reveal at phone width (reveal, approved 2026-11-12, iPhone 1), for now over a photo with a verified solve: the
  // photo covers its slot, with the phone's marks and counts; its card, its asking and the other views keep the old
  // pieces until their own units
  // The phone's no-match reveal (no-match, approved, iPhone), with no ask button: its one main button takes that place
  const phoneNoMatch = phone && isPhoneNoMatchShown(session);
  const phoneReveal = phone && (isPhoneRevealShown(session) || realSkyShown || flightShown || phoneNoMatch);
  const revealShown = (!phone && (isSourcePhotoShown(session) || skyOver)) || phoneReveal;
  // Original pixels (original, decided 2026-11-12): pressed on the star's card, it shows the photo whole; it lasts
  // only while that card is open
  const [originalPixels, setOriginalPixels] = useState(false);
  const cardOpen = revealShown && session.selectedFindingId != null;
  useEffect(() => {
    if (!cardOpen) setOriginalPixels(false);
  }, [cardOpen]);
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
  // A new photo has not been decoded yet, so an earlier photo's mismatch says nothing about it
  useEffect(() => {
    setPhotoFrameMismatch(false);
  }, [session.sourceAsset?.uri]);

  // Choosing a file to upload leaves the landing, so the upload, its solve and the photo show at once
  useEffect(() => {
    if (uploadedMediaUrl) setEntryStageActive(false);
  }, [uploadedMediaUrl]);

  // A refused upload stored nothing, so the home comes back with the refusal beside Upload
  useEffect(() => {
    if (uploadRefusal) setEntryStageActive(true);
  }, [uploadRefusal]);

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
  useSelectionTone(selectedFinding, session);
  const audioCreditLines = useAudioCreditLines();
  const audioDisclosure = useAudioDisclosure();

  // The renderer's camera against its star sphere, for names placed on the sky as it is drawn
  const readSkyPose = useCallback(() => rendererRef.current?.getCameraSkyPose?.() ?? null, []);
  // The photo's restoration (D10): asked for from Restore on the reveal, followed, compared once done
  const { restoration, start: startRestore, cancel: cancelRestore } = useRestoration(session.sourceAsset?.sha256 ?? null, {
    orientation: session.sourceAsset?.displayOrientation,
  });

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
    // While the home covers the canvas no frame of the scene can be seen, so the scene stops drawing; the order here
    // is the point: drawing stops before a landing scene is asked for (a renderer not drawing holds it, so none of its
    // pictures loads), and on leaving the showcase ends, dropping what was held, before drawing starts again
    if (!entryStageActive) {
      renderer.showSkyView?.();
      renderer.setShowcaseMode(false);
      appliedShowcaseRef.current.sceneKey = null;
      renderer.setDrawing?.(true);
      return;
    }

    renderer.setDrawing?.(false);
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

  // The sky the solve allows: measured over a verified solve, predicted otherwise (as main's panel opened it)
  const showSky = useCallback(
    (mode: 'measured' | 'predicted') =>
      setSession((cur) => {
        const solution = cur.observationSolution;
        if (!solution) return cur;
        return dispatchAlbedianAction(cur, {
          type: 'sky.show',
          requestId: `ui-sky-show-${cur.viewRevision}`,
          expectedViewRevision: cur.viewRevision,
          mode,
          observationSolutionId: solution.id,
        }).session;
      }),
    [setSession]
  );

  // The views over the photo (reveal, approved 2026-11-12; no-match reveal, 2026-11-18): Real sky over a verified
  // solve; 3D toward the picked star when the app can fly to it, as its card's Go there does (the lead's question
  // pills-1); a view that cannot open stays in place, not pressable
  const solution = session.observationSolution;
  const realSkyOpens = solution !== undefined && solution.status === 'calibrated' && hasVerifiedWcs(solution) && canDrawRealSky();
  const pickedFinding = solution?.findings.find((finding) => finding.id === session.selectedFindingId) ?? null;
  const threeDOpens = pickedFinding !== null && findingCanEnterSpace(pickedFinding);
  const handleView = (view: string) => {
    if (view === 'sky' && realSkyOpens && !realSkyShown) showSky('measured');
    // Back to the photo from the sky: the saved moment, restored exactly
    if (view === 'photo' && skyOver) backToMoment();
    if (view === '3d' && pickedFinding && threeDOpens && !flightShown) onRunUtterance(`Take me to ${pickedFinding.title}`, 'pointer');
  };

  // A clip's player over the reveal (INTERIM: UIUX-clip-QUESTION.md): the clip's own element, and where it stops sets the
  // session's clock through the reducer, so what the reveal draws follows the frame on screen
  const [clipVideo, setClipVideo] = useState<HTMLVideoElement | null>(null);
  const clipShown = !phone && revealShown && !skyOver && session.sourceAsset?.mediaKind === 'video';
  const seekClip = (sec: number) =>
    setSession((cur) =>
      dispatchAlbedianAction(cur, { type: 'media.seek', requestId: `ui-clip-seek-${cur.viewRevision}`, expectedViewRevision: cur.viewRevision, sourceTimeSec: sec, preserveAudioSync: true })
        .session
    );
  // While the clip element plays, the reveal is drawn as for a playing clip, so marks lined up for one frame (P-SKY-9:
  // on a clip only while it is not playing) never stand over other frames; the session's own playback, which drives
  // the audio bus, is left alone
  const [clipPlaying, setClipPlaying] = useState(false);
  useEffect(() => {
    if (!clipVideo) {
      setClipPlaying(false);
      return undefined;
    }
    const onPlay = () => setClipPlaying(true);
    const onPause = () => setClipPlaying(false);
    clipVideo.addEventListener('play', onPlay);
    clipVideo.addEventListener('pause', onPause);
    setClipPlaying(!clipVideo.paused);
    return () => {
      clipVideo.removeEventListener('play', onPlay);
      clipVideo.removeEventListener('pause', onPause);
    };
  }, [clipVideo]);
  // A clip wider than the reveal's photo column is shaded at the sides as Real sky is, so the words beside it stay legible
  const clipShaded = clipShown && session.sourceAsset !== undefined && (() => {
    const shown = orientedSize(session.sourceAsset!.width, session.sourceAsset!.height, session.sourceAsset!.displayOrientation);
    return shown.widthPx / Math.max(1, shown.heightPx) > CLIP_COLUMN_PX / Math.max(1, windowSize.heightPx);
  })();

  // At phone width the photo covers its slot: written as the crop the stage and every mark read, its ends faded
  const slotted: ObservationSession = (() => {
    const asset = session.sourceAsset;
    if (!phoneReveal || !asset) return session;
    const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
    return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, windowSize.widthPx, windowSize.heightPx) } } as ObservationSession;
  })();
  const slotMask = (() => {
    const asset = session.sourceAsset;
    if (!phoneReveal || !asset) return undefined;
    const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
    return phoneEndsMask(sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, windowSize.widthPx, windowSize.heightPx, slotted.cropTransform));
  })();

  // Back to Moment (fly, approved 2026-11-12): the saved CameraCheckpoint, restored exactly by the reducer
  const backToMoment = () =>
    setSession((cur) =>
      dispatchAlbedianAction(cur, {
        type: 'source.return',
        requestId: `ui-source-return-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        checkpointId: returnCheckpointOf(cur)?.id,
        restoreAudio: true,
      }).session
    );
  const back = useRef(backToMoment);
  back.current = backToMoment;

  // There, Esc returns too, with nothing on screen saying so; an open answer takes Esc first
  useEffect(() => {
    if (!flightShown || answer) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (event.key !== 'Escape' || event.defaultPrevented || tag === 'input' || tag === 'textarea') return;
      event.preventDefault();
      back.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [flightShown, answer]);

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

      {/* While the home or the photo covers the canvas, the modeled scene's layers are not what is on screen */}
      {renderLayers && !entryStageActive && !arrival && !isSourcePhotoShown(session) && !skyOver && (
        <RenderLayerDisclosure layers={renderLayers} />
      )}

      {/* The home (arrive, 2026-11-12) shows until a photo is chosen; it covers the canvas, as a photo does */}
      {entryStageActive && (
        <HomeScreen
          soundOn={soundEnabled}
          onToggleSound={handleToggleSound}
          onUploadPressed={() => {
            onTriggerUpload?.();
          }}
          onFileChosen={(file) => {
            setEntryStageActive(false);
            onFileChosen?.(file);
          }}
          refusal={uploadRefusal}
        />
      )}

      {/* An upload on its way (arrive and failed, 2026-11-12): the photo bare where the reveal will place it, under the
          working rows. Until the receipt there is no photo, so black covers the modeled scene the rows say nothing of */}
      {!entryStageActive && arrival && (
        <>
          {!isSourcePhotoShown(session) && <div className="work-backdrop" />}
          <SourcePhotoStage session={session} onFrameMismatch={setPhotoFrameMismatch} bare dimmed={arrival.failure !== null} />
          <WorkingScreen
            arrival={arrival}
            photoRect={photoFrameMismatch ? null : sessionPhotoRect(session, windowSize.widthPx, windowSize.heightPx)}
            onTryAgain={() => onTryAgain?.()}
            onCancel={onCancelJob}
            onUploadAnother={(file) => onFileChosen?.(file)}
            onHome={() => setEntryStageActive(true)}
          />
        </>
      )}

      {!entryStageActive && !arrival && (
        <>
          <SourcePhotoStage
            session={slotted}
            onFrameMismatch={setPhotoFrameMismatch}
            bare={revealShown}
            fadeSides={revealShown && !phoneReveal && !originalPixels}
            endsMask={slotMask}
            underSky={skyOver}
            onVideo={setClipVideo}
          />
          {revealShown && (
            <RevealPhoto
              session={clipShown && clipPlaying ? { ...slotted, isPlaying: true } : slotted}
              phone={phoneReveal}
              onSelectFinding={onSelectFinding}
              onHome={() => setEntryStageActive(true)}
              frameMismatch={photoFrameMismatch}
              originalPixels={originalPixels}
              onToggleOriginalPixels={() => setOriginalPixels((on) => !on)}
              onGoThere={(findingId) => {
                const title = session.observationSolution?.findings.find((finding) => finding.id === findingId)?.title;
                if (title) onRunUtterance(`Take me to ${title}`, 'pointer');
              }}
              onCloseCard={() => setSession((prev) => ({ ...prev, selectedFindingId: null }))}
              onShowPredictedSky={() => showSky('predicted')}
              answer={answer}
              onCloseAnswer={onCloseAnswer}
              restoration={restoration}
              onRestore={startRestore}
              onCancelRestore={cancelRestore}
              realSky={realSkyShown}
              flight={flightShown ? flightReadout(session) : null}
              freeFlight={freeFlight}
              onEndFreeFlight={() => setFreeFlight(false)}
              corners={!clipShaded}
            />
          )}
          {!revealShown && !(photoFrameMismatch && isSourcePhotoShown(session)) && (
            <CelestialMarkerOverlay
              session={session}
              selectedFinding={selectedFinding}
              onSelectFinding={onSelectFinding}
              skyPose={readSkyPose}
            />
          )}
          {!revealShown && (
          <CelestialTargetCard
            session={session}
            selectedFinding={selectedFinding && selectedFinding.id === session.selectedFindingId ? selectedFinding : null}
            onRunUtterance={onRunUtterance}
            onPushToTalk={onPushToTalk}
            sourceImageUrl={uploadedMediaUrl}
            onClose={() => setSession((prev) => ({ ...prev, selectedFindingId: null }))}
          />
          )}
          {!skyOver && (
          <FlightAndRestoreHud
            session={session}
            setSession={setSession}
          />
          )}
          {!revealShown && (
            <ObservationSolveAndCompareHud
              session={session}
              setSession={setSession}
            />
          )}
          {!skyOver && (
          <EpochAndDisclosureHud
            session={session}
            setSession={setSession}
          />
          )}
          {/* Over the reveal, the approved Ask out loud button (with the keyboard button on the laptop) takes the old
              bar's place (ask, approved 2026-11-12); elsewhere the old bar stays until its own unit */}
          {/* On the phone the picked star's card covers the foot, Ask out loud's place, as #p-pick draws it */}
          {/* Nor over Real sky on the phone, as #p-real draws it */}
          {revealShown && phoneReveal && !flightShown && (pickedFinding || realSkyShown || phoneNoMatch) ? null : revealShown ? (
            <AskBar
              canListen={canListen}
              listening={session.machines?.voice === 'listening' || session.machines?.voice === 'permission'}
              words={heardWords}
              onListen={onPushToTalk}
              onSend={(text) => onRunUtterance(text, 'text')}
              onBack={flightShown ? backToMoment : undefined}
              phone={phoneReveal}
              onFlyOnYourOwn={arrived && !freeFlight ? () => setFreeFlight(true) : undefined}
            />
          ) : (
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
          )}
        </>
      )}

      {/* =======================================================================
          TOP BAR: Brand Wordmark + 03-B Mark on Left, Audio Control on Right (past the home only)
          ======================================================================= */}
      {/* No header strip over the phone's reveal: the iPhone's web view has no sound control (sound, approved) */}
      {!entryStageActive && !arrival && !phoneReveal && (
        <header
          className="alb-lusion-header alb-visor-topright"
          data-hud-interactive="true"
        >
          <div className="alb-lusion-header-left">
            {!revealShown && (
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
            )}
          </div>

          {/* The phone keeps its old audio strip until its own unit; above phone width the approved Sound circle
              is in the row at the top right (below) */}
          {phone && (
          <div className="alb-lusion-header-right">
            <div
              className="alb-zzzz-inline-control"
              data-testid="inline-audio-coalescing"
            >
              <button
                type="button"
                className={`alb-audio-label-btn ${soundEnabled ? 'is-active' : ''}`}
                data-audio-control="toggle"
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
          )}
        </header>
      )}

      {/* The row at the top right of every laptop screen after the home: the view pills over the photo, then the Sound
          circle; none on the home, which has its own, nor while a photo is being worked on */}
      {clipShaded && (
        <div
          className="clip-shade"
          aria-hidden="true"
          style={{ backgroundImage: realSkyShade({ leftPx: (windowSize.widthPx - CLIP_COLUMN_PX) / 2, topPx: 0, widthPx: CLIP_COLUMN_PX, heightPx: windowSize.heightPx }, windowSize.widthPx) }}
        />
      )}
      {clipShown && session.sourceAsset && (
        <ClipPlayer video={clipVideo} durationSec={session.sourceAsset.durationMs / 1000} onSeek={seekClip} />
      )}

      {/* The phone reveal's top row (reveal, approved, iPhone 1): a 44 pt close circle, home as the laptop's logo, and the views */}
      {phoneReveal && !entryStageActive && !arrival && (
        <div className="reveal-phone-top" data-hud-interactive="true">
          <IconButton label="Close" icon="x" size="phone" onClick={() => setEntryStageActive(true)} />
          <ViewPills
            label="View"
            size="phone"
            views={[
              { id: 'photo', label: 'Your photo' },
              { id: 'sky', label: 'Real sky', disabled: !realSkyOpens },
              { id: '3d', label: '3D', disabled: !threeDOpens },
            ]}
            current={flightShown ? '3d' : realSkyShown ? 'sky' : 'photo'}
            onSelect={handleView}
          />
        </div>
      )}

      {/* Keyboard help on "?" (INTERIM: UIUX-keys-QUESTION.md), on the laptop after the home; the 3D view's keys are
          listed only where that view is on screen */}
      {!phone && !entryStageActive && !arrival && <KeyboardHelp threeD={!revealShown} />}

      {!phone && !entryStageActive && !arrival && (
        <div className="reveal-nav" data-hud-interactive="true">
          {revealShown && (
            <ViewPills
              label="View"
              views={[
                { id: 'photo', label: 'Your photo' },
                { id: 'sky', label: 'Real sky', disabled: !realSkyOpens },
                { id: '3d', label: '3D', disabled: !threeDOpens },
              ]}
              current={flightShown ? '3d' : realSkyShown ? 'sky' : 'photo'}
              onSelect={handleView}
            />
          )}
          <SoundControl on={soundEnabled} onToggle={handleToggleSound} level={volumeLevel} onLevel={setVolumeLevel} disclosure={audioDisclosure} />
        </div>
      )}
    </div>
  );
};
