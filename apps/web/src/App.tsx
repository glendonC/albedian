import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
  CelestialFinding,
  ObservationSession,
} from '@albedian/contracts';
import {
  createEmptyObservationSession,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { SpatialObservatoryViewport } from './components/SpatialObservatoryViewport.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { useCameraFlightAnimation } from './hooks/useCameraFlightAnimation.js';
import {
  DiscoveredMediaItem,
  UploadRefusedError,
  failureReason,
  fetchDiscoveredMedia,
  uploadAndSolveMedia,
} from './services/apiClient.js';
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
 * A 5xx answer or no answer proves no refusal, so it is not one.
 */
export async function ingestAndSolveUploadedFile(
  file: File,
  localObjectUrl: string,
  onSessionUpdate: (session: ObservationSession) => void,
  onRefused?: (detail: string | null) => void
): Promise<ObservationSession> {
  onSessionUpdate({
    ...createEmptyObservationSession(),
    lastStatusBanner: `Uploading "${file.name}" to the Albedian server…`,
  });

  try {
    return await uploadAndSolveMedia(file, file.name, {
      localObjectUrl,
      onSessionUpdate,
    });
  } catch (err) {
    const notStored: ObservationSession = {
      ...createEmptyObservationSession(),
      lastStatusBanner:
        err instanceof UploadRefusedError
          ? err.status >= 500
            ? // A 5xx proves neither outcome: the dev proxy answers 500 with no backend at all, and a
              // crash in the router answers 500 too, so say only that the upload did not finish
              `The upload of "${file.name}" did not finish: ${err.reason}.`
            : `Not stored: ${err.reason} ("${file.name}").`
          : `The upload of "${file.name}" did not finish: ${failureReason(err)}.`,
    };
    onSessionUpdate(notStored);
    if (err instanceof UploadRefusedError && err.status < 500) onRefused?.(err.detail);
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
  const [discoveredMedia, setDiscoveredMedia] = useState<DiscoveredMediaItem[]>([]);
  const [activeDiscoveredId, setActiveDiscoveredId] = useState<string | null>(null);
  const [commandText, setCommandText] = useState('');
  const [recentPointer, setRecentPointer] = useState<ActionContext['recentPointer']>(undefined);
  const [isMuted] = useState(false);
  const [uploadedMediaUrl, setUploadedMediaUrl] = useState<string | null>(null);
  const [uploadRefusal, setUploadRefusal] = useState<HomeRefusal | null>(null);
  // The file chosen last: an older upload's refusal arriving after another choice is not shown
  const latestChoiceRef = useRef<File | null>(null);

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
    const result = executeVoiceOrTextCommand(asked, utterance, modality, pointer);
    setSession(result.session);

    if (result.outcome.status === 'completed') {
      audioBusRef.current?.playSelectionClick(isMuted);
      if (result.voiceActionRecord.proposedAction?.type === 'space.enter') {
        animatePhaseAToPhaseBSpaceFlight(result.voiceActionRecord.proposedAction.findingId);
      }
    } else if (result.outcome.status === 'rejected' && backendMayResolve(result.outcome.reason)) {
      // The backend may read what the parser could not. Its reply applies to the session as it stands when
      // the reply arrives, through the reducer, which refuses an action that went stale meanwhile.
      void askVoiceBackend(utterance, result.session, pointer).then((reply) => {
        if (!reply) return;
        // A reply about the previous photo is dropped: a new session can match its view revision by coincidence
        if (photoKeyOf(sessionRef.current) !== photoKeyOf(asked)) {
          setSession((prev) => ({ ...prev, lastStatusBanner: VOICE_REPLY_PREVIOUS_PHOTO }));
          return;
        }
        const applied = applyVoiceBackendReply(sessionRef.current, reply);
        setSession(applied.session);
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
      onAsking: () =>
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'permission' },
          lastStatusBanner: SPEECH_ASKING,
        })),
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
      onStopped: (banner) =>
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'off' },
          ...(banner === null ? {} : { lastStatusBanner: banner }),
        })),
    });
  };

  // A photo or clip chosen on the home starts its upload and solve, and clears the last refusal; a refusal brings
  // the home back with the file's name and the server's reason
  const handleFileChosen = (file: File) => {
    const url = URL.createObjectURL(file);
    latestChoiceRef.current = file;
    setUploadRefusal(null);
    setActiveDiscoveredId(null);
    setUploadedMediaUrl(url);
    void ingestAndSolveUploadedFile(file, url, setSession, (detail) => {
      if (latestChoiceRef.current !== file) return;
      setUploadRefusal({ fileName: file.name, detail });
    });
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
      />
    </>
  );
};
