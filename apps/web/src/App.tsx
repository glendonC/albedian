import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
  ObservationSession,
} from '@albedian/contracts';
import {
  createEmptyObservationSession,
  createSessionForSourceAsset,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { SpatialObservatoryViewport } from './components/SpatialObservatoryViewport.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { useCameraFlightAnimation } from './hooks/useCameraFlightAnimation.js';
import {
  DiscoveredMediaItem,
  fetchDiscoveredMedia,
} from './services/apiClient.js';
import { applyVoiceBackendReply, askVoiceBackend, backendMayResolve } from './voice/voiceBackend.js';
import './styles/albedian.css';

async function sha256HexOfFile(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
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

  const selectedFinding = useMemo(
    () =>
      session.observationSolution.findings.find((f) => f.id === session.selectedFindingId) ??
      session.observationSolution.findings[0] ??
      null,
    [session.observationSolution.findings, session.selectedFindingId]
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
      SpeechRecognition?: new () => {
        lang: string;
        interimResults: boolean;
        onresult: (ev: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void;
        onerror: () => void;
        start: () => void;
      };
      webkitSpeechRecognition?: SpeechRecWindow['SpeechRecognition'];
    };
    const w = window as unknown as SpeechRecWindow;
    const SpeechRec = w.SpeechRecognition ?? w.webkitSpeechRecognition;

    if (!SpeechRec) {
      setSession((prev) => ({
        ...prev,
        lastStatusBanner:
          'Browser speech recognition unavailable in this environment; use the typed command bar below.',
      }));
      return;
    }

    setSession((prev) => ({
      ...prev,
      machines: { ...prev.machines, voice: 'listening' },
      lastStatusBanner: 'Listening (Push-to-Talk active)… Speak a command.',
    }));

    const rec = new SpeechRec();
    rec.lang = 'en-US';
    rec.interimResults = false;
    rec.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript ?? '';
      if (transcript) {
        setCommandText(transcript);
        runUtterance(transcript, 'voice');
      }
    };
    rec.onerror = () => {
      setSession((prev) => ({
        ...prev,
        machines: { ...prev.machines, voice: 'off' },
        lastStatusBanner: 'Microphone capture ended.',
      }));
    };
    rec.start();
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const isVideo = file.type.startsWith('video/');
    setActiveDiscoveredId(null);
    setUploadedMediaUrl(url);

    void sha256HexOfFile(file).then((sha256) => {
      // Dimensions, duration and audio streams stay placeholders until the file is decoded
      const imported = createSessionForSourceAsset(
        {
          ...createEmptyObservationSession().sourceAsset,
          id: `src_upload_${Date.now()}`,
          title: file.name,
          mediaKind: isVideo ? 'video' : 'image',
          sha256,
          uri: url,
        },
        'importing'
      );
      setSession({
        ...imported,
        lastStatusBanner: `Imported "${file.name}" (SHA-256 ${sha256.slice(0, 16)}…). Not yet decoded or calibrated.`,
      });
    });
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
