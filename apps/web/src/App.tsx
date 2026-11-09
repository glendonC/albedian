import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
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
import { applyVoiceBackendReply, askVoiceBackend, backendMayResolve } from './voice/voiceBackend.js';
import { SPEECH_LISTENING, startSpeechCapture, type SpeechRecognitionLike } from './voice/speechInput.js';
import './styles/albedian.css';

/**
 * Upload a chosen file and solve it. Nothing about the file is known until the server's receipt
 * arrives, so until then the session stays empty and says it is uploading; a refused upload says
 * why and that nothing was stored.
 */
export async function ingestAndSolveUploadedFile(
  file: File,
  localObjectUrl: string,
  onSessionUpdate: (session: ObservationSession) => void
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
          ? `Not stored: ${err.reason} ("${file.name}").`
          : `The upload of "${file.name}" did not finish: ${failureReason(err)}.`,
    };
    onSessionUpdate(notStored);
    return notStored;
  }
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

  const audioBusRef = useRef<AlbedianWebAudioBus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // The session as it stands now, for a backend reply that arrives after later changes
  const sessionRef = useRef(session);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

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

  const findings = session.observationSolution?.findings ?? [];
  const selectedFinding = useMemo(
    () =>
      findings.find((f) => f.id === session.selectedFindingId) ??
      findings[0] ??
      null,
    [findings, session.selectedFindingId]
  );

  const runUtterance = (
    utterance: string,
    modality: 'voice' | 'text' | 'pointer' = 'text'
  ) => {
    if (!utterance.trim()) return;
    const result = executeVoiceOrTextCommand(session, utterance, modality, recentPointer);
    setSession(result.session);

    if (result.outcome.status === 'completed') {
      audioBusRef.current?.playSelectionClick(isMuted);
      if (result.voiceActionRecord.proposedAction?.type === 'space.enter') {
        animatePhaseAToPhaseBSpaceFlight(result.voiceActionRecord.proposedAction.findingId);
      }
    } else if (result.outcome.status === 'rejected' && backendMayResolve(result.outcome.reason)) {
      // The backend may read what the parser could not. Its reply applies to the session as it stands when
      // the reply arrives, through the reducer, which refuses an action that went stale meanwhile.
      void askVoiceBackend(utterance, result.session, recentPointer).then((reply) => {
        if (!reply) return;
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
    startSpeechCapture(w.SpeechRecognition ?? w.webkitSpeechRecognition, {
      onListening: () =>
        setSession((prev) => ({
          ...prev,
          machines: { ...prev.machines, voice: 'listening' },
          lastStatusBanner: SPEECH_LISTENING,
        })),
      onTranscript: (transcript) => {
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

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    setActiveDiscoveredId(null);
    setUploadedMediaUrl(url);
    void ingestAndSolveUploadedFile(file, url, setSession);
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="video/*,image/*"
        style={{ display: 'none' }}
        onChange={handleFileUpload}
      />
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
        onTriggerUpload={() => fileInputRef.current?.click()}
      />
    </>
  );
};
