import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
  ObservationSession,
  deterministicHexDigest,
} from '@albedian/contracts';
import {
  createDefaultSaharaObservationSession,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { SpatialObservatoryViewport } from './components/SpatialObservatoryViewport.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { useCameraFlightAnimation } from './hooks/useCameraFlightAnimation.js';
import {
  DiscoveredMediaItem,
  buildSessionFromDiscoveredMedia,
  fetchDiscoveredMedia,
  postVoiceIntentToBackend,
} from './services/apiClient.js';
import './styles/albedian.css';

export const App: React.FC = () => {
  const [session, setSession] = useState<ObservationSession>(() =>
    createDefaultSaharaObservationSession()
  );
  const [discoveredMedia, setDiscoveredMedia] = useState<DiscoveredMediaItem[]>([]);
  const [activeDiscoveredId, setActiveDiscoveredId] = useState<string | null>(null);
  const [commandText, setCommandText] = useState('');
  const [recentPointer, setRecentPointer] = useState<ActionContext['recentPointer']>(undefined);
  const [isMuted] = useState(false);
  const [uploadedMediaUrl, setUploadedMediaUrl] = useState<string | null>(null);

  const audioBusRef = useRef<AlbedianWebAudioBus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { animatePhaseAToPhaseBSpaceFlight } = useCameraFlightAnimation(session, setSession);

  useEffect(() => {
    audioBusRef.current = new AlbedianWebAudioBus();
    return () => {
      audioBusRef.current?.dispose();
    };
  }, []);

  // Dynamically discover any media in data/uploads via backend API and auto-boot into the first real capture
  useEffect(() => {
    void fetchDiscoveredMedia().then((items) => {
      setDiscoveredMedia(items);
      if (items.length > 0) {
        const firstReal = items[0]!;
        setActiveDiscoveredId(firstReal.id);
        setSession(buildSessionFromDiscoveredMedia(firstReal));
      }
    });
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
    } else if (result.outcome.status === 'rejected') {
      void postVoiceIntentToBackend(utterance, session).then((remote) => {
        if (remote?.scientificExplanation) {
          setSession((prev) => ({
            ...prev,
            lastStatusBanner: remote.scientificExplanation ?? prev.lastStatusBanner,
          }));
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

    const baseSession = createDefaultSaharaObservationSession();
    const fileHash = deterministicHexDigest(`${file.name}:${file.size}:${file.lastModified}`);
    setSession({
      ...baseSession,
      title: `Imported Observation · ${file.name}`,
      sourceAsset: {
        ...baseSession.sourceAsset,
        id: `src_upload_${Date.now()}`,
        title: file.name,
        mediaKind: isVideo ? 'video' : 'image',
        sha256: fileHash,
        uri: url,
      },
      lastStatusBanner: `Imported immutable source "${file.name}" (SHA-256: ${fileHash.slice(0, 16)}…). Ready for restoration and calibration.`,
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
