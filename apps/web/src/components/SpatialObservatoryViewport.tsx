import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { resolveMotionTokens } from '@albedian/design-system';
import {
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  DiscoveredMediaItem,
} from '../services/apiClient.js';
import { hasVerifiedWcs, type ObservationSolution, type PersonAlignment, type RenderLayerSet, type SkyView } from '@albedian/contracts';
import { skyViewOfPose } from '../spatial/flight/skyViewWcs.js';
import type { AlignedFrom, AlignPose, AlignStart } from '../reveal/alignPose.js';
import { dispatchAlbedianAction, returnCheckpointOf } from '@albedian/observation';
import { useCameraFlightAnimation } from '../hooks/useCameraFlightAnimation.js';
import { IconButton, ViewPills } from '../look/index.js';
import { findingCanEnterSpace } from './askSuggestions.js';
import {
  SparkPortalRenderer,
  SpatialScaleRegime,
} from '../spatial/SparkPortalRenderer.js';
import {
  type CosmosRenderer,
  type CosmosRendererKind,
} from '../spatial/cosmosRenderer.js';
import {
  observatoryAudio,
} from '../audio/ObservatoryAudioEngine.js';
import { skyAudio } from '../audio/SkyAudioFeed.js';
import { AUDIO_NOTICES_URL, appHeardMusic } from '../audio/audioCredits.js';
import { blockLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import { CreditsPage } from './CreditsPage.js';
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
import { OpenWait } from '../reveal/OpenWait.js';
import { earlyPoseOf, type PlacedTarget } from '../reveal/earlyPose.js';
import { PredictedSkyScreen } from '../reveal/PredictedSkyScreen.js';
import { isPredictedSkyShown, predictedSkyStart } from '../reveal/predictedSky.js';
import { skyViewPicture } from '../spatial/sky/predictedSkyPicture.js';
import { RevealPhoto } from '../reveal/RevealPhoto.js';
import { SoundControl } from '../reveal/SoundControl.js';
import { AskBar, type HeardWords } from '../reveal/AskBar.js';
import { canDrawRealSky, isRealSkyShown, prepareRealSky } from '../reveal/RealSkyLayer.js';
import { prepareFlight } from '../spatial/flight/prepareFlight.js';
import { flightReadout, isFlightShown } from '../reveal/flightView.js';
import { KeyboardHelp } from '../reveal/KeyboardHelp.js';
import { ClipPlayer } from '../reveal/ClipPlayer.js';
import { MeasuredMarks } from '../reveal/MeasuredMarks.js';
import { realSkyShade } from '../reveal/RealSkyLayer.js';
import { orientedSize } from '@albedian/contracts';
import { isPhoneNoMatchShown, isPhoneRevealShown, isSideways, phoneEndsMask, phoneSlotCrop } from '../reveal/phoneSlot.js';
import { readLeadingMarginPx } from './phoneSheet.js';
import { useRestoration } from '../reveal/restoration.js';
import { sourcePhotoDisplayRect } from './sourcePhotoRect.js';
import type { ShownAnswer } from '../reveal/AnswerPanel.js';
import { usePhoneLayout } from '../reveal/usePhoneLayout.js';
import { liveClipSec, useClipPlayback, withClipClock } from '../reveal/clipPlayback.js';
import { withPickedVersion, withShownRestoration } from '../reveal/shownRestoration.js';
import type { Arrival } from '../reveal/arrival.js';
import '../styles/albedian.css';

export interface SpatialObservatoryViewportProps {
  session: ObservationSession;
  setSession: React.Dispatch<React.SetStateAction<ObservationSession>>;
  /** Where the playing clip's live time is handed to App, so a command that leaves the recording saves it. */
  clipClockRef?: React.MutableRefObject<(() => number | null) | null>;
  /** Where the Restore button's own action is handed to App, for asking to restore by voice (voice-restore-two). */
  restoreNowRef?: React.MutableRefObject<(() => boolean) | null>;
  /** go-there-predicted-2: filled while the predicted sky is on screen with where it is looking. */
  skyViewRef?: React.MutableRefObject<(() => SkyView | null) | null>;
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
  /** The page was opened at an upload's address and its records are still being read: the app's black, never the home. */
  opening?: boolean;
  /** The upload on its way to a solved sky: while there is one, the working screen shows in place of what follows the solve. */
  arrival?: Arrival | null;
  /** The sky the last solve predicted from an early pose, kept after its job ended, for the reveal's lock-on (D25). */
  lockOnSky?: ObservationSolution | null;
  /** The rings the last Solve from here was sent from, for the reveal's lock-on when it matches (D25 item 2). */
  lockOnAligned?: { sha256: string; marks: readonly PlacedTarget[] } | null;
  /** Starts the failed upload's work again (the working screen's Try again). */
  onTryAgain?: () => void;
  /** Asks the server to cancel the upload's job (the working screen's Cancel, D2). */
  onCancelJob?: () => Promise<unknown> | void;
  /** Solve from here (D24 step 2): the person's alignment, the pose it came from and where that started. */
  onSolveFromHere?: (alignment: PersonAlignment, pose: AlignPose, from: AlignStart) => void;
  /** The alignment the last Solve from here was sent from (D24 steps 4 and 5). */
  alignedFrom?: AlignedFrom | null;
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
  clipClockRef,
  restoreNowRef,
  skyViewRef,
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
  opening = false,
  arrival = null,
  lockOnSky = null,
  lockOnAligned = null,
  onSolveFromHere,
  alignedFrom = null,
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
  // D30 item 6: on the phone a drag on the sky takes the steering and Autopilot gives it back; leaving there ends it
  const [phoneSteering, setPhoneSteering] = useState(false);
  useEffect(() => {
    if (!flightShown) setPhoneSteering(false);
  }, [flightShown]);
  const skyOver = realSkyShown || flightShown;
  // D33: the sky Show predicted sky opens, in place of the old panel (UIUX-predsky-READY.md), at phone width too
  const predictedSkyShown = !flightShown && isPredictedSkyShown(session);
  const [entryStageActive, setEntryStageActive] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    return new URLSearchParams(window.location.search).get('view') !== 'observatory';
  });
  const [clipVideo, setClipVideo] = useState<HTMLVideoElement | null>(null);
  // voice-restore-one: the restored version the screen put in the session last, so it is swapped or taken out alone
  const shownRestorationId = useRef<string | null>(null);
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
  // The clip's own play and pause, its end included, written to the session (clip-playing-checkpoint-1), and its live
  // time handed to App for the commands that leave the recording
  useClipPlayback(clipVideo, setSession);
  const clipVideoNow = useRef(clipVideo);
  clipVideoNow.current = clipVideo;
  useEffect(() => {
    if (!clipClockRef) return undefined;
    const clock = () => liveClipSec(clipVideoNow.current);
    clipClockRef.current = clock;
    return () => {
      if (clipClockRef.current === clock) clipClockRef.current = null;
    };
  }, [clipClockRef]);
  const clipActive = !entryStageActive && !arrival && !skyOver && session.sourceAsset?.mediaKind === 'video' && isSourcePhotoShown(session);
  const liveSession = clipActive && clipPlaying ? ({ ...session, isPlaying: true } as ObservationSession) : session;
  // The reveal at phone width (reveal, approved 2026-11-12, iPhone 1), over a verified or pose-estimated photo or clip;
  // while a clip plays, liveSession marks it playing so frame-gated marks step aside cleanly (D16, P-SKY-9) [verified]
  // The phone's no-match reveal (no-match, approved, iPhone), with no ask button: its one main button takes that place
  const phoneNoMatch = phone && isPhoneNoMatchShown(liveSession);
  const phoneReveal = phone && (isPhoneRevealShown(liveSession) || realSkyShown || flightShown || phoneNoMatch);
  const revealShown = (!phone && (isSourcePhotoShown(session) || skyOver)) || phoneReveal;
  // Original pixels (original, decided 2026-11-12): pressed on the star's card, it shows the photo whole; it lasts
  // only while that card is open
  const [originalPixels, setOriginalPixels] = useState(false);
  // credits-reach-one (UIUX-credits-reach-READY.md, ruled 17:39 on 24 Nov): Credits over the moment, from the link at the
  // laptop's bottom right; the moment stands unchanged under it, and back returns to it with the link focused
  const [creditsOpen, setCreditsOpen] = useState(false);
  const creditsShown = useLeaving(creditsOpen, blockLeaveMs(1, resolveMotionTokens(reducedMotion())));
  const creditsAtSound = useRef(false);
  const creditsLink = useRef<HTMLButtonElement>(null);
  // Back from Credits puts the keyboard where it was, on the link, once the moment takes input again
  const creditsWasOpen = useRef(false);
  useEffect(() => {
    if (creditsOpen) {
      creditsWasOpen.current = true;
      return;
    }
    if (!creditsWasOpen.current) return;
    creditsWasOpen.current = false;
    creditsLink.current?.focus();
  }, [creditsOpen]);
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

  const [activeRegime, setActiveRegime] =
    useState<SpatialScaleRegime>('cislunar');
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [volumeLevel, setVolumeLevel] = useState<number>(0.42);

  // Line it up yourself (D24) on the phone: while it shows, the close circle cancels it instead of going home
  const [phoneAligning, setPhoneAligning] = useState(false);
  // While the phone's restoration holds the foot (its compare's Download, D10 item 5, or its wait's Cancel, D38), Ask out loud gives way
  const [phoneFoot, setPhoneFoot] = useState(false);
  const [cancelAligningToken, setCancelAligningToken] = useState(0);

  // Markers are hidden when the browser turned the photo differently from its orientation tag
  const [photoFrameMismatch, setPhotoFrameMismatch] = useState(false);
  // A new photo has not been decoded yet, so an earlier photo's mismatch says nothing about it
  useEffect(() => {
    setPhotoFrameMismatch(false);
  }, [session.sourceAsset?.uri]);

  // A page opened at an upload's address (open-direct-one, UIUX-opendirect-READY.md): while its records are read the
  // app's black with the logo, never the home; once read the sequence enters at its step 3, as if the drop had just
  // happened: the photo frames in where the reveal places it and, the job being done, the reveal follows once the frame
  // is in (marks brightest first, then the words), the working rows never drawn. An upload that cannot be opened lands
  // on the home. Settled in the render the wait ends in, so no frame of the home is drawn between
  const [openingSeen, setOpeningSeen] = useState(opening);
  const [photoEntering, setPhotoEntering] = useState(false);
  if (openingSeen !== opening) {
    setOpeningSeen(opening);
    if (!opening && uploadedMediaUrl) {
      setEntryStageActive(false);
      setPhotoEntering(true);
    }
  }
  useEffect(() => {
    if (!photoEntering) return undefined;
    const framedIn = window.setTimeout(() => setPhotoEntering(false), resolveMotionTokens(reducedMotion()).frameInMs);
    return () => window.clearTimeout(framedIn);
  }, [photoEntering]);
  // What follows the photo on screen: not while it frames in after the wait
  const pastPhoto = !entryStageActive && !arrival && !photoEntering;

  // Choosing a file to upload leaves the landing, so the upload, its solve and the photo show at once
  useEffect(() => {
    if (uploadedMediaUrl) setEntryStageActive(false);
  }, [uploadedMediaUrl]);

  // A refused upload stored nothing, so the home comes back with the refusal beside Upload
  useEffect(() => {
    if (uploadRefusal) setEntryStageActive(true);
  }, [uploadRefusal]);

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

  // D33's start (decided 13:23): the predicted sky opens facing D24's start, 30 degrees up (26 on the phone), once
  // each time it opens (the renderer's lookAtSky, added to SPATIAL's files with the lead's yes of 13:30)
  const skyStart = predictedSkyShown ? predictedSkyStart(session, phone) : null;
  // go-there-predicted-2: Back to Moment from a flight that left the predicted sky brings back the view it left from
  const keptSkyView = predictedSkyShown ? session.spatialView.skyView : undefined;
  const skyStartKey = skyStart ? `${session.observationSolution?.id ?? ''}:${skyStart.azDeg}:${skyStart.altDeg}:${skyStart.acrossDeg}:${keptSkyView ? keptSkyView.cameraQuaternionIcrs.join(',') : ''}` : null;
  useEffect(() => {
    if (!skyStart) return;
    if (keptSkyView && rendererRef.current?.lookAtSkyView) rendererRef.current.lookAtSkyView(keptSkyView);
    else rendererRef.current?.lookAtSky?.(skyStart.azDeg, skyStart.altDeg, skyStart.acrossDeg);
  }, [skyStartKey, rendererGeneration]);

  // predsky-starless-one (18:04): the predicted sky's dome takes the starless glow and its catalog reaches 10.5; every
  // other view the photograph and the naked-eye limit; the line is worded from what the dome shows (21:51)
  const [skyPictureShown, setSkyPictureShown] = useState<'photograph' | 'starless'>('photograph');
  useEffect(() => {
    const { picture, limitMag } = skyViewPicture(predictedSkyShown);
    rendererRef.current?.setSkyPicture?.(picture, limitMag, setSkyPictureShown);
  }, [predictedSkyShown, rendererGeneration]);

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
  // The upload's restoration (D10, D34): asked for from Restore on the reveal, followed, compared once done
  const { restoration, start: startRestore, cancel: cancelRestore } = useRestoration(session.sourceAsset?.sha256 ?? null, {
    orientation: session.sourceAsset?.displayOrientation,
    mediaKind: session.sourceAsset?.mediaKind,
  });
  // voice-restore-two (DECIDED-BY-LEAD 18:21 on 24 Nov): the Restore button's own action, handed to App for asking to
  // restore by voice. It acts where the button does (the recording on screen, no restoration running or shown, or a
  // stop it can try again: start asks, joins the one running, or returns the stored one) and says whether it did
  const restoreNow = useRef<() => boolean>(() => false);
  restoreNow.current = () => {
    if (!session.sourceAsset || !isSourcePhotoShown(session)) return false;
    // voice-restore-known-one (DECIDED-BY-LEAD 25 Nov 02:37): Original pixels shows the photo whole, the compare hidden;
    // asking for the restored version turns them off, as pressing Original pixels again does
    const pixelsOff = originalPixels;
    if (pixelsOff) setOriginalPixels(false);
    if (!(restoration.phase === 'idle' || (restoration.phase === 'stopped' && restoration.canTryAgain))) return pixelsOff;
    startRestore();
    return true;
  };
  useEffect(() => {
    if (!restoreNowRef) return undefined;
    const press = () => restoreNow.current();
    restoreNowRef.current = press;
    return () => {
      if (restoreNowRef.current === press) restoreNowRef.current = null;
    };
  }, [restoreNowRef]);

  // go-there-predicted-glow-one: on the predicted sky its flight's glow and programs are prepared at rest, as a photo's are
  useEffect(() => {
    if (!predictedSkyShown) return;
    prepareRealSky(false);
    prepareFlight();
  }, [predictedSkyShown]);

  // go-there-predicted-2: while the predicted sky is on screen a request carries where it looks (the portal's pose)
  useEffect(() => {
    if (!skyViewRef || !predictedSkyShown) return undefined;
    const read = () => {
      const pose = rendererRef.current?.getCameraSkyPose?.();
      return pose ? skyViewOfPose(pose) : null;
    };
    skyViewRef.current = read;
    return () => {
      if (skyViewRef.current === read) skyViewRef.current = null;
    };
  }, [skyViewRef, predictedSkyShown]);

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

  // While the home covers the canvas no frame of the scene can be seen, so nothing draws under it; leaving it, the sky
  // view is set up (home-cycle-1: the home's showcase scene cycle under it, drawn by nothing and in no record, is gone)
  useEffect(() => {
    if (!entryStageActive) rendererRef.current?.showSkyView?.();
  }, [entryStageActive, rendererGeneration]);
  // portal-pause-one (DECIDED-BY-LEAD 24 Nov 17:34): nor can any frame of it be seen under the photo's reveal, Real sky or
  // the flight, steering included (measured at both sizes: hiding the canvas changes no pixel of those screens), so the
  // scene draws only while it is what is on screen. Told in the commit that uncovers it, before that screen is painted,
  // so its next frame is drawn before the screen shows it.
  const portalCovered = entryStageActive || revealShown;
  useLayoutEffect(() => {
    rendererRef.current?.setDrawing?.(!portalCovered);
  }, [portalCovered, rendererGeneration]);

  // Keyboard navigation (WASD + Arrows)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (entryStageActive || e.defaultPrevented) return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable) return;
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
          if (document.querySelector('[role="listbox"]')) break;
          renderer.nudgeLook(0, 0.06);
          break;
        case 'ArrowDown':
          if (document.querySelector('[role="listbox"]')) break;
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
      setSession((now) => {
        const solution = now.observationSolution;
        if (!solution) return now;
        // A playing clip's live time first, so the moment saved on leaving is the frame on screen
        const cur = withClipClock(now, liveClipSec(clipVideoNow.current));
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
  const threeDOpens = pickedFinding !== null && findingCanEnterSpace(pickedFinding, session.observationSolution, predictedSkyShown);
  const handleView = (view: string) => {
    if (view === 'sky' && realSkyOpens && !realSkyShown) showSky('measured');
    // Back to the photo from the sky: the saved moment, restored exactly
    if (view === 'photo' && skyOver) backToMoment();
    if (view === '3d' && pickedFinding && threeDOpens && !flightShown) onRunUtterance(`Take me to ${pickedFinding.title}`, 'pointer');
  };

  // A clip's player over the reveal (D16, UIUX-clipphone-READY.md section 2): the clip's own element, and where it stops
  // sets the session's clock through the reducer, so what the reveal draws follows the frame on screen; at phone width
  // hidden while aligning or while a picked star's card sheet covers the lower screen [verified]
  const clipOnReveal = !entryStageActive && !arrival && revealShown && !skyOver && session.sourceAsset?.mediaKind === 'video';
  const clipShown = clipOnReveal && !photoEntering && (!phone || (!phoneAligning && pickedFinding === null));
  // The first view pill names the recording's kind, as every result sentence does (C16)
  const firstViewLabel = session.sourceAsset?.mediaKind === 'video' ? 'Your clip' : 'Your photo';
  const seekClip = (sec: number) =>
    setSession((cur) =>
      dispatchAlbedianAction(cur, { type: 'media.seek', requestId: `ui-clip-seek-${cur.viewRevision}`, expectedViewRevision: cur.viewRevision, sourceTimeSec: sec, preserveAudioSync: true })
        .session
    );
  // A clip wider than the reveal's photo column on the laptop is shaded at the sides as Real sky is, so the words beside it stay legible; omitted on a phone clip (D16)
  const clipShaded = !phone && clipOnReveal && session.sourceAsset !== undefined && (() => {
    const shown = orientedSize(session.sourceAsset!.width, session.sourceAsset!.height, session.sourceAsset!.displayOrientation);
    return shown.widthPx / Math.max(1, shown.heightPx) > CLIP_COLUMN_PX / Math.max(1, windowSize.heightPx);
  })();

  // At phone width a photo covers its slot with its ends faded, while a clip fits inside it crisp with no endsMask (D16) [verified]
  // The working screen at phone width keeps the upload in the reveal's slot (D2, jp-run)
  const phoneWorking = phone && !entryStageActive && arrival !== null;
  const slotted: ObservationSession = (() => {
    const asset = session.sourceAsset;
    if (!(phoneReveal || phoneWorking) || !asset) return session;
    const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
    // nomatch-sideways-one (item 8a): held on its side the no-match photo stands whole at the leading margin, a column beside it;
    // never while the solve still runs, when the session reads as unmatched too and the waiting photo stands centred (item 2;
    // nomatch-sideways-wait-one)
    const leading = !phoneWorking && phoneNoMatch && asset.mediaKind === 'image' && isSideways(windowSize.widthPx, windowSize.heightPx) ? readLeadingMarginPx() : undefined;
    return { ...session, cropTransform: { ...session.cropTransform, ...phoneSlotCrop(shown.widthPx, shown.heightPx, windowSize.widthPx, windowSize.heightPx, asset.mediaKind, leading) } } as ObservationSession;
  })();
  const slotMask = (() => {
    const asset = session.sourceAsset;
    if (!(phoneReveal || phoneWorking) || !asset || asset.mediaKind === 'video') return undefined;
    const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
    return phoneEndsMask(sourcePhotoDisplayRect(shown.widthPx, shown.heightPx, windowSize.widthPx, windowSize.heightPx, slotted.cropTransform), windowSize.heightPx, windowSize.widthPx);
  })();

  // Back to Moment (fly, approved 2026-11-12): the saved CameraCheckpoint, restored exactly by the reducer; from there,
  // after the flight flown backwards (the motion record; at once under reduced motion); from Real sky, at once
  const { animateReturnToMoment } = useCameraFlightAnimation(session, setSession);
  const restoreMoment = () =>
    setSession((cur) =>
      dispatchAlbedianAction(cur, {
        type: 'source.return',
        requestId: `ui-source-return-${cur.viewRevision}`,
        expectedViewRevision: cur.viewRevision,
        checkpointId: returnCheckpointOf(cur)?.id,
        restoreAudio: true,
      }).session
    );
  // Pressed there, there's words leave while the flight flies back (the motion record); gone with the flight
  const [returning, setReturning] = useState(false);
  useEffect(() => {
    if (!flightShown) setReturning(false);
  }, [flightShown]);
  const backToMoment = () => {
    if (!flightShown) return restoreMoment();
    setReturning(true);
    return animateReturnToMoment(restoreMoment);
  };
  const back = useRef(backToMoment);
  back.current = backToMoment;

  // There, and on D33's predicted sky (which takes There's frame and replaced the old panel that held this key), Esc
  // returns too, with nothing on screen saying so; an open answer takes Esc first
  useEffect(() => {
    // An open card on the predicted sky takes Esc first, as an answer does
    if (!(flightShown || (predictedSkyShown && !session.selectedFindingId)) || answer) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (event.key !== 'Escape' || event.defaultPrevented || tag === 'input' || tag === 'textarea') return;
      event.preventDefault();
      back.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [flightShown, predictedSkyShown, session.selectedFindingId, answer]);

  const handleToggleSound = useCallback(() => {
    const next = observatoryAudio.toggle();
    setSoundEnabled(next);
  }, []);

  return (
    <div
      className="alb-root"
      inert={creditsOpen}
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
      {renderLayers && pastPhoto && !isSourcePhotoShown(session) && !skyOver && !predictedSkyShown && (
        <RenderLayerDisclosure layers={renderLayers} />
      )}

      {/* The home (arrive, 2026-11-12) shows until a photo is chosen; it covers the canvas, as a photo does */}
      {/* The page opened at an upload's address: the app's black with the logo until its records are read, then the logo
          alone over the photo while it frames in; at phone width the black alone */}
      {(opening || (photoEntering && !arrival)) && <OpenWait black={opening} phone={phone} />}

      {entryStageActive && !opening && (
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
          <SourcePhotoStage
            session={phoneWorking ? slotted : session}
            onFrameMismatch={setPhotoFrameMismatch}
            bare
            dimmed={arrival.failure !== null}
            entering={photoEntering}
            endsMask={phoneWorking ? slotMask : undefined}
          />
          {/* wait-measured-one (UIUX-first-use-wow-READY.md item 3): once Measured ends, the points of light it measured on
              the photo as Observed marks, from the job's own route; a stopped job's dimmed photo carries none */}
          {arrival.failure === null && (
            <MeasuredMarks
              session={phoneWorking ? slotted : session}
              jobId={arrival.job?.id}
              measuredAtUTC={arrival.job?.measuredAtUTC}
              viewportWidthPx={windowSize.widthPx}
              viewportHeightPx={windowSize.heightPx}
              phone={phone}
            />
          )}
          <WorkingScreen
            phone={phone}
            arrival={arrival}
            photoRect={photoFrameMismatch ? null : sessionPhotoRect(session, windowSize.widthPx, windowSize.heightPx)}
            onTryAgain={() => onTryAgain?.()}
            onCancel={onCancelJob}
            onUploadAnother={(file) => onFileChosen?.(file)}
            onHome={() => setEntryStageActive(true)}
            early={earlyPoseOf(arrival.early, session.sourceAsset)}
            aligned={arrival.aligned ? { marks: arrival.aligned } : null}
          />
        </>
      )}

      {!entryStageActive && !arrival && (
        <>
          <SourcePhotoStage
            session={slotted}
            onFrameMismatch={setPhotoFrameMismatch}
            bare={revealShown}
            fadeSides={revealShown && !phoneReveal}
            fadeOpen={originalPixels}
            endsMask={slotMask}
            underSky={skyOver}
            onVideo={setClipVideo}
            entering={photoEntering}
          />
          {/* Everything over the photo follows once it has framed in after the wait */}
          {!photoEntering && (
          <>
          {revealShown && (
            <RevealPhoto
              lockOnSky={lockOnSky}
              lockOnAligned={lockOnAligned}
              clipVideo={clipVideo}
              onSolveFromHere={onSolveFromHere}
              alignedFrom={alignedFrom}
              session={clipOnReveal && clipPlaying ? { ...slotted, isPlaying: true } : slotted}
              phone={phoneReveal}
              onAligningChange={setPhoneAligning}
              // voice-restore-one: the session holds the restored version on screen, so voice finds it
              onShownRestoration={(variant) => {
                // Only a change reaches the session: the one put in before is swapped or taken out, the session's own
                // variants stay
                const before = shownRestorationId.current;
                const now = variant?.id ?? null;
                if (now === before) return;
                shownRestorationId.current = now;
                setSession((cur) => withShownRestoration(cur, variant, before));
              }}
              // checkpoint-version-one: the row pressed is the session's active variant, so the moment left keeps it
              onPickVersion={(id) => setSession((cur) => withPickedVersion(cur, id))}
              onPhoneFootChange={setPhoneFoot}
              cancelAligningToken={cancelAligningToken}
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
              returning={returning}
              freeFlight={freeFlight}
              onEndFreeFlight={() => setFreeFlight(false)}
              phoneSteering={phoneSteering}
              onPhoneSteer={() => setPhoneSteering(true)}
              onEndPhoneSteer={() => setPhoneSteering(false)}
              corners={!clipShaded}
            />
          )}
          {predictedSkyShown && <PredictedSkyScreen
              session={session}
              starless={skyPictureShown === 'starless'}
              onHome={() => setEntryStageActive(true)}
              skyPose={readSkyPose}
              phone={phone}
              onSelect={(findingId) => onSelectFinding(findingId)}
              onGoThere={(findingId) => {
                const title = session.observationSolution?.findings.find((finding) => finding.id === findingId)?.title;
                if (title) onRunUtterance(`Take me to ${title}`, 'pointer');
              }}
              onCloseCard={() => setSession((prev) => ({ ...prev, selectedFindingId: null }))}
              answer={answer}
              onCloseAnswer={onCloseAnswer}
            />}
          {!revealShown && !predictedSkyShown && !(photoFrameMismatch && isSourcePhotoShown(session)) && (
            <CelestialMarkerOverlay
              session={session}
              selectedFinding={selectedFinding}
              onSelectFinding={onSelectFinding}
              skyPose={readSkyPose}
            />
          )}
          {!revealShown && !predictedSkyShown && (
          <CelestialTargetCard
            session={session}
            selectedFinding={selectedFinding && selectedFinding.id === session.selectedFindingId ? selectedFinding : null}
            onRunUtterance={onRunUtterance}
            onPushToTalk={onPushToTalk}
            sourceImageUrl={uploadedMediaUrl}
            onClose={() => setSession((prev) => ({ ...prev, selectedFindingId: null }))}
          />
          )}
          {!skyOver && !predictedSkyShown && (
          <FlightAndRestoreHud
            session={session}
            setSession={setSession}
          />
          )}
          {!revealShown && !predictedSkyShown && (
            <ObservationSolveAndCompareHud
              session={session}
              setSession={setSession}
            />
          )}
          {/* Kept on the predicted sky (D33): it draws nothing unless the epoch was shifted, and then it is the simulated
              epoch's own disclosure, which the screen's status column does not give */}
          {!skyOver && (
          <EpochAndDisclosureHud
            session={session}
            setSession={setSession}
          />
          )}
          {/* Over the reveal, the approved Ask out loud button (with the keyboard button on the laptop) takes the old
              bar's place (ask, approved 2026-11-12); elsewhere the old bar stays until its own unit */}
          {/* On the phone the picked star's card covers the foot, Ask out loud's place, as #p-pick draws it */}
          {/* Nor over Real sky on the phone, as #p-real draws it; nor while the phone's restoration holds the foot, whose
              Download (D10 item 5) or Cancel (D38) stands there */}
          {(revealShown && phoneReveal && !flightShown && (pickedFinding || realSkyShown || phoneNoMatch || phoneFoot)) || (predictedSkyShown && phone && pickedFinding?.evidenceClass === 'predicted') ? null : revealShown || predictedSkyShown ? (
            <AskBar
              canListen={canListen}
              listening={session.machines?.voice === 'listening' || session.machines?.voice === 'permission'}
              words={heardWords}
              onListen={onPushToTalk}
              onSend={(text) => onRunUtterance(text, 'text')}
              onBack={flightShown || predictedSkyShown ? backToMoment : undefined}
              leaving={returning}
              phone={phoneReveal || (phone && predictedSkyShown)}
              onFlyOnYourOwn={arrived && !freeFlight ? () => setFreeFlight(true) : undefined}
              onAutopilot={flightShown && phoneReveal && phoneSteering ? () => setPhoneSteering(false) : undefined}
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
        </>
      )}

      {/* =======================================================================
          TOP BAR: Brand Wordmark + 03-B Mark on Left, Audio Control on Right (past the home only)
          ======================================================================= */}
      {/* No header strip over the phone's reveal: the iPhone's web view has no sound control (sound, approved) */}
      {pastPhoto && !phoneReveal && (
        <header
          className="alb-lusion-header alb-visor-topright"
          data-hud-interactive="true"
        >
          <div className="alb-lusion-header-left">
            {!revealShown && !predictedSkyShown && (
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
          {/* Not on D33's predicted sky, whose phone frame has only the close circle at the top */}
          {phone && !predictedSkyShown && (
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
        <ClipPlayer video={clipVideo} durationSec={session.sourceAsset.durationMs / 1000} onSeek={seekClip} phone={phone} />
      )}

      {/* The phone reveal's top row (reveal, approved, iPhone 1): a 44 pt close circle, home as the laptop's logo, and the views */}
      {phoneReveal && pastPhoto && (
        <div className="reveal-phone-top" data-hud-interactive="true">
          <IconButton label="Close" icon="x" size="phone" onClick={() => (phoneAligning ? setCancelAligningToken((n) => n + 1) : setEntryStageActive(true))} />
          <ViewPills
            label="View"
            size="phone"
            views={[
              { id: 'photo', label: firstViewLabel },
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
      {!phone && pastPhoto && <KeyboardHelp threeD={!revealShown} />}

      {!phone && pastPhoto && (
        <div className="reveal-nav" data-hud-interactive="true">
          {revealShown && (
            <ViewPills
              label="View"
              views={[
                { id: 'photo', label: firstViewLabel },
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

      {/* Credits on every laptop screen after the home, as map products keep their attribution on the map: always in
          view at the bottom right, in the disclosure lines' style; the home keeps its pill, the phone its audio strip */}
      {!phone && pastPhoto && (
        <button
          ref={creditsLink}
          type="button"
          className="reveal-credits"
          aria-label="Credits"
          data-hud-interactive="true"
          onClick={() => {
            // Once music has played this visit the page starts at its Sound row, where the playing piece's credit is
            creditsAtSound.current = appHeardMusic.get().length > 0;
            setCreditsOpen(true);
          }}
        >
          Credits
        </button>
      )}
      {creditsShown.mounted &&
        createPortal(
          <CreditsPage over startAtSound={creditsAtSound.current} leaving={creditsShown.leaving} onBack={() => setCreditsOpen(false)} />,
          document.body
        )}
    </div>
  );
};
