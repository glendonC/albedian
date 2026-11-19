import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
  CelestialFinding,
  IngestReceipt,
  ObservationSession,
  ObservationSolution,
  PersonAlignment,
  orientedSize,
} from '@albedian/contracts';
import { alignTargets, placedAtPose, type AlignPose, type AlignStart } from './reveal/alignPose.js';
import {
  createEmptyObservationSession,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { SpatialObservatoryViewport } from './components/SpatialObservatoryViewport.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { useCameraFlightAnimation } from './hooks/useCameraFlightAnimation.js';
import { usePrefersReducedMotion, withReducedMotion } from './hooks/usePrefersReducedMotion.js';
import {
  DiscoveredMediaItem,
  UploadRefusedError,
  cancelJob,
  failureReason,
  fetchDiscoveredMedia,
  solveStoredUpload,
  uploadAndSolveMedia,
  type UploadEvent,
} from './services/apiClient.js';
import { openStoredUpload, uploadAddressed } from './services/openUpload.js';
import { arrivalAfter, arrivalOf, type Arrival } from './reveal/arrival.js';
import { usePhoneLayout } from './reveal/usePhoneLayout.js';
import { isSourcePhotoShown } from './components/sourcePhotoRect.js';
import { isPhoneRevealShown } from './reveal/phoneSlot.js';
import { isRealSkyShown } from './reveal/RealSkyLayer.js';
import { isFlightShown } from './reveal/flightView.js';
import { askVoiceAnswer, isQuestion } from './services/voiceAnswer.js';
import type { ShownAnswer } from './reveal/AnswerPanel.js';
import type { HeardWords } from './reveal/AskBar.js';
import {
  applyVoiceBackendReply,
  askVoiceBackend,
  backendMayResolve,
  photoKeyOf,
  VOICE_REPLY_PREVIOUS_PHOTO,
} from './voice/voiceBackend.js';
import {
  SPEECH_ASKING,
  SPEECH_LISTENING,
  SPEECH_PREVIOUS_PHOTO,
  speechStopParts,
  startSpeechCapture,
  type SpeechRecognitionLike,
} from './voice/speechInput.js';
import type { HomeRefusal } from './components/homeRefusal.js';
import './styles/fonts.css';
import './styles/albedian.css';

/**
 * Upload a chosen file and solve it. Nothing about the file is known until the server's receipt
 * arrives, so until then the session stays empty and says it is uploading; a refused upload says
 * why and that nothing was stored, and `onRefused` hears the server's own words (or null) for the home.
 * A 5xx answer or no answer proves no refusal, so it is not one: `onEvent`, which hears every step of the upload
 * and its solve for the working screen, hears it as a failure with the sentence the status line already says.
 */
export async function ingestAndSolveUploadedFile(
  file: File,
  localObjectUrl: string,
  onSessionUpdate: (session: ObservationSession) => void,
  onRefused?: (detail: string | null) => void,
  onEvent?: (event: UploadEvent) => void
): Promise<ObservationSession> {
  onSessionUpdate({
    ...createEmptyObservationSession(),
    lastStatusBanner: `Uploading "${file.name}" to the Albedian server…`,
  });

  try {
    return await uploadAndSolveMedia(file, file.name, {
      localObjectUrl,
      onSessionUpdate,
      onEvent,
    });
  } catch (err) {
    const refused = err instanceof UploadRefusedError && err.status < 500;
    const sentence =
      err instanceof UploadRefusedError
        ? err.status >= 500
          ? // A 5xx proves neither outcome: the dev proxy answers 500 with no backend at all, and a
            // crash in the router answers 500 too, so say only that the upload did not finish
            `The upload of "${file.name}" did not finish: ${err.reason}.`
          : `Not stored: ${err.reason} ("${file.name}").`
        : `The upload of "${file.name}" did not finish: ${failureReason(err)}.`;
    const notStored: ObservationSession = { ...createEmptyObservationSession(), lastStatusBanner: sentence };
    onSessionUpdate(notStored);
    if (refused) onRefused?.((err as UploadRefusedError).detail);
    else onEvent?.({ kind: 'failed', sentence, job: null });
    return notStored;
  }
}

/** The finding the session selected, or none: a target card shows only for a real selection. */
export function selectedFindingOf(session: ObservationSession): CelestialFinding | null {
  const id = session.selectedFindingId;
  if (!id) return null;
  return session.observationSolution?.findings.find((f) => f.id === id) ?? null;
}

export const App: React.FC = () => {
  const [session, setSession] = useState<ObservationSession>(() =>
    createEmptyObservationSession()
  );
  // Less motion when the person asks for it: every session the app builds or opens follows the setting, so Go there
  // dissolves instead of flying (REQ-DSA-2.1)
  const reducedMotion = usePrefersReducedMotion();
  useEffect(() => {
    setSession((prev) => withReducedMotion(prev, reducedMotion));
  }, [reducedMotion, session]);
  const [discoveredMedia, setDiscoveredMedia] = useState<DiscoveredMediaItem[]>([]);
  const [activeDiscoveredId, setActiveDiscoveredId] = useState<string | null>(null);
  const [commandText, setCommandText] = useState('');
  const [recentPointer, setRecentPointer] = useState<ActionContext['recentPointer']>(undefined);
  const [isMuted] = useState(false);
  const [uploadedMediaUrl, setUploadedMediaUrl] = useState<string | null>(null);
  const [uploadRefusal, setUploadRefusal] = useState<HomeRefusal | null>(null);
  // The file chosen last: an older upload's refusal arriving after another choice is not shown
  const latestChoiceRef = useRef<File | null>(null);
  // The upload on its way to a solved sky, for the working screen; none once its sky is solved or it was refused
  const [arrival, setArrival] = useState<Arrival | null>(null);
  // The sky the solve predicted from an early pose (the lock-on, D25), kept past the job's end for the reveal
  const [lockOnSky, setLockOnSky] = useState<ObservationSolution | null>(null);
  // The answer to the last question asked over the laptop reveal, or a command's refusal, with the photo it is about
  // (ask, approved 2026-11-12; command, decided the same night)
  const [answer, setAnswer] = useState<({ photoKey: string } & ShownAnswer) | null>(null);
  const phone = usePhoneLayout();
  // The words heard so far while the microphone is open, for the listening button
  const [heardWords, setHeardWords] = useState<HeardWords | null>(null);
  // Whether this browser can listen: a recogniser, on a secure page (a phone's plain http address is not one)
  const canListen = useMemo(() => {
    const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
    return Boolean(w.SpeechRecognition ?? w.webkitSpeechRecognition) && window.isSecureContext !== false;
  }, []);
  // Where answers show: the laptop reveal, over the photo or over Real sky in the photo's frame or there; the phone's
  // reveal under its photo
  const revealOn = (s: ObservationSession) => (phone ? isPhoneRevealShown(s) : isSourcePhotoShown(s) || isRealSkyShown(s) || isFlightShown(s));
  // What Try again needs: the file and its browser URL, and the receipt once the file is stored
  const inFlightRef = useRef<{ file: File; url: string; receipt: IngestReceipt | null } | null>(null);

  const audioBusRef = useRef<AlbedianWebAudioBus | null>(null);
  // The session and pointer as they stand now, for spoken words or a backend reply that arrive after later changes
  const sessionRef = useRef(session);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);
  const recentPointerRef = useRef(recentPointer);
  useEffect(() => {
    recentPointerRef.current = recentPointer;
  }, [recentPointer]);

  const { animatePhaseAToPhaseBSpaceFlight } = useCameraFlightAnimation(session, setSession);

  useEffect(() => {
    audioBusRef.current = new AlbedianWebAudioBus();
    return () => {
      audioBusRef.current?.dispose();
    };
  }, []);

  // List media found in data/uploads; a capture becomes the session only when the user picks it
  useEffect(() => {
    void fetchDiscoveredMedia().then(setDiscoveredMedia);
  }, []);

  // The address may name one stored upload (?upload=<sha256>, as the iPhone's Open writes it): its stored reveal
  // opens with no upload and no solve. A value that is no sha256 asks the server nothing, and an upload that cannot
  // be opened leaves the home as it is. A photo chosen meanwhile wins: the stored one arriving later changes nothing
  useEffect(() => {
    const sha256 = uploadAddressed(window.location.search);
    if (sha256 === null) return undefined;
    let left = false;
    void openStoredUpload(sha256).then((opened) => {
      if (left || opened === null || latestChoiceRef.current !== null) return;
      setSession(opened);
      setUploadedMediaUrl(opened.sourceAsset?.uri ?? null);
    });
    return () => {
      left = true;
    };
  }, []);

  // Sync Web Audio Bus whenever navigation, voice, or playback state changes
  useEffect(() => {
    audioBusRef.current?.syncBusState({
      navigationState: session.machines.navigation,
      voiceState: session.machines.voice,
      userSourceGain: session.audioGain,
      isMuted,
      isPlaying: session.isPlaying,
    });
  }, [
    session.machines.navigation,
    session.machines.voice,
    session.audioGain,
    session.isPlaying,
    isMuted,
  ]);

  const activeDiscoveredItem = useMemo(
    () => discoveredMedia.find((m) => m.id === activeDiscoveredId) ?? null,
    [discoveredMedia, activeDiscoveredId]
  );

  const selectedFinding = useMemo(
    () => selectedFindingOf(session),
    [session.observationSolution, session.selectedFindingId]
  );

  const runUtterance = (
    utterance: string,
    modality: 'voice' | 'text' | 'pointer' = 'text'
  ) => {
    if (!utterance.trim()) return;
    // Push-to-talk calls this from the render that opened the microphone; the words act on what stands now
    const pointer = recentPointerRef.current;
    const asked = sessionRef.current;
    // A question over the laptop reveal goes to the answer route of the photo's upload, whose answer takes the card's
    // place; when the route does not answer, the words run as they did before. A command stays with the app.
    const sha256 = asked.sourceAsset?.sha256;
    if (modality !== 'pointer' && sha256 && revealOn(asked) && isQuestion(utterance, asked, pointer)) {
      const photoKey = photoKeyOf(asked);
      void askVoiceAnswer(sha256, utterance, asked.selectedFindingId).then((reply) => {
        // An answer about the previous photo is dropped
        if (photoKeyOf(sessionRef.current) !== photoKey) return;
        if (reply) setAnswer({ photoKey, heard: reply.heard, sentence: reply.answer.sentence, answer: reply.answer });
        // The route gave no answer: say so, and to ask again (C11, decided by the lead)
        else setAnswer({ photoKey, heard: utterance, sentence: 'No answer from the Albedian server.', sub: 'Ask again.', answer: null });
      });
      return;
    }
    runCommand(utterance, modality, pointer);
  };

  const runCommand = (utterance: string, modality: 'voice' | 'text' | 'pointer', pointer: ActionContext['recentPointer']) => {
    setAnswer(null);
    const asked = sessionRef.current;
    const result = executeVoiceOrTextCommand(asked, utterance, modality, pointer);
    setSession(result.session);
    // A command it cannot carry out shows its refusal where answers show, over the laptop reveal (command, decided
    // 2026-11-12): the words as heard, then the app's own sentence; nothing lights up
    const refuse = (sentence: string) => {
      if (revealOn(asked) && photoKeyOf(sessionRef.current) === photoKeyOf(asked)) {
        setAnswer({ photoKey: photoKeyOf(asked), heard: utterance, sentence, answer: null });
      }
    };
    if (result.outcome.status === 'needs_input') refuse(result.outcome.question);
    if (result.outcome.status === 'rejected' && !backendMayResolve(result.outcome.reason)) refuse(result.outcome.explanation);

    if (result.outcome.status === 'completed') {
      audioBusRef.current?.playSelectionClick(isMuted);
      if (result.voiceActionRecord.proposedAction?.type === 'space.enter') {
        animatePhaseAToPhaseBSpaceFlight(result.voiceActionRecord.proposedAction.findingId);
      }
    } else if (result.outcome.status === 'rejected' && backendMayResolve(result.outcome.reason)) {
      // The backend may read what the parser could not. Its reply applies to the session as it stands when
      // the reply arrives, through the reducer, which refuses an action that went stale meanwhile.
      const local = result.outcome.explanation;
      void askVoiceBackend(utterance, result.session, pointer).then((reply) => {
        if (!reply) {
          refuse(local);
          return;
        }
        // A reply about the previous photo is dropped: a new session can match its view revision by coincidence
        if (photoKeyOf(sessionRef.current) !== photoKeyOf(asked)) {
          setSession((prev) => ({ ...prev, lastStatusBanner: VOICE_REPLY_PREVIOUS_PHOTO }));
          return;
        }
        const applied = applyVoiceBackendReply(sessionRef.current, reply);
        setSession(applied.session);
        const outcome = applied.outcome;
        if (outcome?.status === 'rejected') refuse(outcome.explanation);
        else if (outcome?.status === 'needs_input') refuse(outcome.question);
        else if (!outcome && reply.status === 'rejected') refuse(reply.explanation);
        else if (!outcome && reply.status === 'needs_input') refuse(reply.question);
        if (applied.outcome?.status === 'completed') {
          audioBusRef.current?.playSelectionClick(isMuted);
          if (applied.flyToFindingId) animatePhaseAToPhaseBSpaceFlight(applied.flyToFindingId);
        }
      });
    }
  };

  const handleSelectFinding = (
    findingId: string,
    pointerCoords?: { xPx: number; yPx: number }
  ) => {
    audioBusRef.current?.playSelectionClick(isMuted);
    // Picking a star closes the answer; its card takes the place
    setAnswer(null);
    if (pointerCoords) {
      setRecentPointer({
        xPx: pointerCoords.xPx,
        yPx: pointerCoords.yPx,
        atTimestampMs: Date.now(),
        viewRevision: session.viewRevision + 1,
        nearestFindingId: findingId,
      });
    }

    const res = dispatchAlbedianAction(session, {
      type: 'finding.select',
      requestId: `sel_${Date.now()}`,
      expectedViewRevision: session.viewRevision,
      findingId,
    });
    setSession(res.session);
  };

  const handlePushToTalk = () => {
    type SpeechRecWindow = {
      SpeechRecognition?: new () => SpeechRecognitionLike;
      webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    };
    const w = window as unknown as SpeechRecWindow;
    // The photo the words are spoken over; words that arrive after another photo is loaded are dropped
    const spokenOver = photoKeyOf(sessionRef.current);
    startSpeechCapture(w.SpeechRecognition ?? w.webkitSpeechRecognition, {
      // The browser may be asking for the microphone; nothing is heard yet
      onAsking: () => {
        setHeardWords(null);
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'permission' },
          lastStatusBanner: SPEECH_ASKING,
        }));
      },
      onWords: (text, settled) => {
        if (photoKeyOf(sessionRef.current) === spokenOver) setHeardWords({ text, settled });
      },
      onListening: () =>
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'listening' },
          lastStatusBanner: SPEECH_LISTENING,
        })),
      onTranscript: (transcript) => {
        if (photoKeyOf(sessionRef.current) !== spokenOver) {
          setSession((prev) => ({ ...prev, lastStatusBanner: SPEECH_PREVIOUS_PHOTO }));
          return;
        }
        setCommandText(transcript);
        runUtterance(transcript, 'voice');
      },
      // Every ending returns voice to off, which also lifts the listening duck on the source audio
      onStopped: (banner) => {
        setHeardWords(null);
        // A capture that heard nothing says why where answers show, over the laptop reveal, in today's sentence until
        // the design lane rewords it (the lead's answer of 2026-11-18 21:33, option A)
        // In the decided words (C11): the first sentence large, the rest under it; nothing for a capture the person stopped
        const now = sessionRef.current;
        const parts = banner === null ? null : speechStopParts(banner);
        if (parts !== null && revealOn(now) && photoKeyOf(now) === spokenOver) {
          setAnswer({ photoKey: spokenOver, heard: '', sentence: parts.sentence, sub: parts.sub, answer: null });
        }
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'off' },
          ...(banner === null ? {} : { lastStatusBanner: banner }),
        }));
      },
    });
  };

  // A photo or clip chosen on the home starts its upload and solve, and clears the last refusal; a refusal brings
  // the home back with the file's name and the server's reason
  const handleFileChosen = (file: File) => {
    const url = URL.createObjectURL(file);
    latestChoiceRef.current = file;
    inFlightRef.current = { file, url, receipt: null };
    solveHereAgainRef.current = null;
    ++solveHereRef.current;
    setUploadRefusal(null);
    setActiveDiscoveredId(null);
    setUploadedMediaUrl(url);
    setArrival(arrivalOf(file));
    setLockOnSky(null);
    void ingestAndSolveUploadedFile(
      file,
      url,
      (next) => {
        if (latestChoiceRef.current === file) setSession(next);
      },
      (detail) => {
        if (latestChoiceRef.current !== file) return;
        // Nothing was stored: the home comes back with the refusal, and there is no arrival to show
        setArrival(null);
        setUploadRefusal({ fileName: file.name, detail });
      },
      (event) => hearUploadEvent(file, event)
    );
  };

  // One step of the upload on screen: an older upload's steps arriving after another choice change nothing
  const hearUploadEvent = (file: File, event: UploadEvent) => {
    if (latestChoiceRef.current !== file) return;
    if (event.kind === 'stored' && inFlightRef.current?.file === file) inFlightRef.current.receipt = event.receipt;
    if (event.kind === 'early') setLockOnSky(event.solution);
    setArrival((prev) => arrivalAfter(prev, event));
  };

  // D24's Solve from here: the stored upload solved again from the person's alignment, its own job on the server. The
  // working screen shows the sent state with the rings at their pose (placed now, from the sky on screen), then the
  // reveal of what the solve found. A Solve from here that stops is the solve Try again repeats, alignment and all,
  // until another file is chosen
  const solveHereRef = useRef(0);
  const solveHereAgainRef = useRef<(() => void) | null>(null);
  const [alignedFrom, setAlignedFrom] = useState<{ sha256: string; pose: AlignPose; from: AlignStart } | null>(null);
  const handleSolveFromHere = (alignment: PersonAlignment, pose: AlignPose, from: AlignStart) => {
    const asset = session.sourceAsset;
    if (!asset) return;
    // Kept for what follows: a refusal draws it again, a match names it (D24 steps 4 and 5)
    setAlignedFrom({ sha256: asset.sha256, pose, from });
    const kept = inFlightRef.current?.receipt;
    const receipt: IngestReceipt = kept && kept.sourceAsset.sha256 === asset.sha256 ? kept : ({ sourceAsset: asset, hints: {}, alreadyStored: true } as IngestReceipt);
    const shown = orientedSize(asset.width, asset.height, asset.displayOrientation);
    const aligned = placedAtPose(alignTargets(session.observationSolution), pose);
    const localObjectUrl = uploadedMediaUrl ?? undefined;
    const send = () => {
      const token = ++solveHereRef.current;
      latestChoiceRef.current = null;
      setArrival({ fileName: asset.title, byteSize: asset.byteSize ?? 0, stored: true, widthPx: shown.widthPx, heightPx: shown.heightPx, job: null, failure: null, aligned });
      void solveStoredUpload(receipt, {
        alignment,
        localObjectUrl,
        onSessionUpdate: (next) => {
          if (solveHereRef.current === token) setSession(next);
        },
        onEvent: (event) => {
          if (solveHereRef.current === token) setArrival((prev) => arrivalAfter(prev, event));
        },
      });
    };
    solveHereAgainRef.current = send;
    send();
  };

  // Try again: the same solve of the stored file, or the same file sent again when it never reached the server
  const handleTryAgain = () => {
    if (solveHereAgainRef.current) {
      solveHereAgainRef.current();
      return;
    }
    const inFlight = inFlightRef.current;
    if (!inFlight) return;
    const { file, url, receipt } = inFlight;
    if (receipt === null) {
      handleFileChosen(file);
      return;
    }
    setArrival((prev) => (prev ? { ...prev, job: null, failure: null } : prev));
    void solveStoredUpload(
      receipt,
      {
        localObjectUrl: url,
        onSessionUpdate: (next) => {
          if (latestChoiceRef.current === file) setSession(next);
        },
        onEvent: (event) => hearUploadEvent(file, event),
      },
      file.name
    );
  };

  return (
    <>
      <SpatialObservatoryViewport
        session={session}
        setSession={setSession}
        selectedFinding={selectedFinding}
        discoveredMedia={discoveredMedia}
        activeDiscoveredId={activeDiscoveredId}
        setActiveDiscoveredId={setActiveDiscoveredId}
        activeDiscoveredItem={activeDiscoveredItem}
        uploadedMediaUrl={uploadedMediaUrl}
        setUploadedMediaUrl={setUploadedMediaUrl}
        commandText={commandText}
        setCommandText={setCommandText}
        onRunUtterance={(utt, mod) => runUtterance(utt, mod)}
        onSelectFinding={(id, coords) => handleSelectFinding(id, coords)}
        onPushToTalk={handlePushToTalk}
        onFileChosen={handleFileChosen}
        uploadRefusal={uploadRefusal}
        arrival={arrival}
        lockOnSky={lockOnSky}
        onTryAgain={handleTryAgain}
        onCancelJob={() => (arrival?.job?.id ? cancelJob(arrival.job.id) : undefined)}
        onSolveFromHere={handleSolveFromHere}
        alignedFrom={alignedFrom}
        answer={answer && answer.photoKey === photoKeyOf(session) ? answer : null}
        heardWords={heardWords}
        canListen={canListen}
        onCloseAnswer={() => setAnswer(null)}
      />
    </>
  );
};
