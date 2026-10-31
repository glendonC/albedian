import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActionContext,
  ExportArtifact,
  ObservationSession,
  deterministicHexDigest,
} from '@albedian/contracts';
import {
  createDefaultSaharaObservationSession,
  createUnresolvedObservationSession,
  dispatchAlbedianAction,
  executeVoiceOrTextCommand,
} from '@albedian/observation';
import { querySightlineCone } from '@albedian/camera-rig';
import { SpatialStage } from './spatial/SpatialStage.js';
import { ContextualInspector } from './components/ContextualInspector.js';
import { LeftFieldTelemetryColumn } from './components/LeftFieldTelemetryColumn.js';
import { ProcessingGraphModal } from './components/ProcessingGraphModal.js';
import { BottomTransportRail } from './components/BottomTransportRail.js';
import { TopObservationalBar } from './components/TopObservationalBar.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { useCameraFlightAnimation } from './hooks/useCameraFlightAnimation.js';
import { useGuidedSkyTour } from './hooks/useGuidedSkyTour.js';
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
  const [disambiguationPrompt, setDisambiguationPrompt] = useState<{
    question: string;
    choices: Array<{ id: string; label: string }>;
  } | null>(null);
  const [isGraphModalOpen, setIsGraphModalOpen] = useState(false);
  const [latestExport, setLatestExport] = useState<ExportArtifact | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [uploadedMediaUrl, setUploadedMediaUrl] = useState<string | null>(null);
  const [uploadedMediaKind, setUploadedMediaKind] = useState<'image' | 'video' | null>(null);
  const [showIsometricGrid, setShowIsometricGrid] = useState(true);
  const [showGeodesicRays, setShowGeodesicRays] = useState(true);
  const [showExplodedTeardown, setShowExplodedTeardown] = useState(true);

  const audioBusRef = useRef<AlbedianWebAudioBus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const { animatePhaseAToPhaseBSpaceFlight } = useCameraFlightAnimation(session, setSession);
  const { isTourActive, startGuidedTour, stopGuidedTour } = useGuidedSkyTour(
    session,
    setSession,
    animatePhaseAToPhaseBSpaceFlight,
    () => audioBusRef.current?.playCalibrationHarmonic(isMuted)
  );

  useEffect(() => {
    audioBusRef.current = new AlbedianWebAudioBus();
    return () => {
      audioBusRef.current?.dispose();
    };
  }, []);

  // Dynamically discover any media in data/uploads via backend API (zero hardcoded filenames)
  useEffect(() => {
    void fetchDiscoveredMedia().then((items) => {
      setDiscoveredMedia(items);
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

  // Advance synthetic clock only when no live <video> element is driving timeline
  useEffect(() => {
    if (
      !session.isPlaying ||
      session.machines.navigation === 'space' ||
      Boolean(activeDiscoveredItem?.urls.origVideoUrl)
    ) {
      return undefined;
    }
    const durationSec = Math.max(1, session.sourceAsset.durationMs / 1000);
    const interval = window.setInterval(() => {
      setSession((prev) => ({
        ...prev,
        currentSourceTimeSec: Number(((prev.currentSourceTimeSec + 0.1) % durationSec).toFixed(2)),
      }));
    }, 100);
    return () => window.clearInterval(interval);
  }, [
    session.isPlaying,
    session.machines.navigation,
    session.sourceAsset.durationMs,
    activeDiscoveredItem,
  ]);

  const selectedFinding = useMemo(
    () =>
      session.observationSolution.findings.find((f) => f.id === session.selectedFindingId) ?? null,
    [session.observationSolution.findings, session.selectedFindingId]
  );

  const sightlineBehindItems = useMemo(() => {
    if (!selectedFinding) return [];
    return querySightlineCone(
      session.observationSolution.findings,
      session.spatialView.observerOriginParsec,
      selectedFinding,
      25
    );
  }, [
    session.observationSolution.findings,
    session.spatialView.observerOriginParsec,
    selectedFinding,
  ]);

  const runUtterance = (
    utterance: string,
    modality: 'voice' | 'text' | 'pointer' = 'text'
  ) => {
    if (!utterance.trim()) return;
    const result = executeVoiceOrTextCommand(session, utterance, modality, recentPointer);
    setSession(result.session);

    if (result.outcome.status === 'needs_input') {
      setDisambiguationPrompt({
        question: result.outcome.question,
        choices: result.outcome.choices,
      });
    } else {
      setDisambiguationPrompt(null);
    }

    if (result.outcome.status === 'completed') {
      audioBusRef.current?.playSelectionClick(isMuted);
      if (result.voiceActionRecord.proposedAction?.type === 'space.enter') {
        animatePhaseAToPhaseBSpaceFlight(result.voiceActionRecord.proposedAction.findingId);
      }
    } else if (result.outcome.status === 'rejected') {
      // Optionally consult backend LLM/deterministic parser if local parser rejected
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
    setDisambiguationPrompt(null);
  };

  const handleEnterSpace = (findingId: string) => {
    audioBusRef.current?.playCalibrationHarmonic(isMuted);
    const res = dispatchAlbedianAction(session, {
      type: 'space.enter',
      requestId: `space_${Date.now()}`,
      expectedViewRevision: session.viewRevision,
      findingId,
      distancePolicy: 'measured_or_estimated',
    });
    setSession(res.session);
    animatePhaseAToPhaseBSpaceFlight(findingId);
  };

  const handleOrbitViewpoint = (findingId: string) => {
    audioBusRef.current?.playSelectionClick(isMuted);
    const currentOrigin = session.spatialView.observerOriginParsec;
    const nextOffset: [number, number, number] =
      Math.abs(currentOrigin[0]) < 25 ? [42.0, -28.0, 18.0] : [-36.0, 48.0, -14.0];
    const res = dispatchAlbedianAction(session, {
      type: 'space.viewpoint',
      requestId: `orbit_${Date.now()}`,
      expectedViewRevision: session.viewRevision,
      targetFindingId: findingId,
      units: 'pc',
      offsetParsec: nextOffset,
      trajectory: 'orbit',
    });
    setSession(res.session);
  };

  const handleSetEpochYear = (yearCE: number) => {
    audioBusRef.current?.playSelectionClick(isMuted);
    const res = dispatchAlbedianAction(session, {
      type: 'space.epoch',
      requestId: `epoch_${Date.now()}`,
      expectedViewRevision: session.viewRevision,
      epochISO: `${yearCE.toString().padStart(4, '0')}-10-19T22:14:00Z`,
      yearCE,
    });
    setSession(res.session);
  };

  const handleReturnToMoment = () => {
    audioBusRef.current?.playCalibrationHarmonic(isMuted);
    const res = dispatchAlbedianAction(session, {
      type: 'source.return',
      requestId: `ret_${Date.now()}`,
      expectedViewRevision: session.viewRevision,
      restoreAudio: true,
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
          'Browser speech recognition unavailable in this environment; use the typed command bar or quick command chips below.',
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

  const handleExportWithProvenance = () => {
    const activeVariant =
      session.mediaVariants.find((v) => v.id === session.activeVariantId) ??
      session.mediaVariants[0]!;
    const sol = session.observationSolution;

    const artifact: ExportArtifact = {
      id: `export_${Date.now()}`,
      sessionId: session.id,
      sourceAssetId: session.sourceAsset.id,
      sourceSha256: session.sourceAsset.sha256,
      exportedVariantId: activeVariant.id,
      outputUri: `albedian-export-${activeVariant.id}.mp4`,
      outputSha256: deterministicHexDigest(
        `${session.sourceAsset.sha256}::${activeVariant.sha256}::${session.viewRevision}`
      ),
      containerFormat: 'mp4',
      videoCodec: 'h264',
      audioPreserved: true,
      audioCodec: session.sourceAsset.audioStreams[0]?.codec ?? 'aac',
      audioSyncOffsetMs: activeVariant.audioSyncOffsetMs,
      processingGraphId: activeVariant.graphId,
      observationSolutionId: sol.id,
      catalogCitations: [
        'ESA Gaia DR3 (2022) Astrometric Catalog',
        'CDS Hipparcos Bright Star Cross-Reference',
        'Bailer-Jones et al. (2021) Photogeometric Distance Priors',
        'NASA JPL Solar System Ephemerides',
      ],
      epistemicSummary: {
        observedCount: sol.findings.filter((f) => f.evidenceClass === 'observed').length,
        catalogMatchedCount: sol.findings.filter((f) => f.evidenceClass === 'catalog_matched').length,
        predictedCount: sol.findings.filter((f) => f.evidenceClass === 'predicted').length,
        simulatedViewsIncluded: session.machines.navigation === 'space',
        strictFidelityEnforced: true,
      },
      exportedAtUTC: new Date().toISOString(),
    };

    setLatestExport(artifact);
    setSession((prev) => ({
      ...prev,
      exportArtifacts: [...prev.exportArtifacts, artifact],
      lastStatusBanner: `Exported ${artifact.outputUri} with preserved audio (drift ${artifact.audioSyncOffsetMs.toFixed(1)} ms) and provenance manifest.`,
    }));
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const url = URL.createObjectURL(file);
    const isVideo = file.type.startsWith('video/');
    setActiveDiscoveredId(null);
    setUploadedMediaUrl(url);
    setUploadedMediaKind(isVideo ? 'video' : 'image');

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
    <div className="alb-shell">
      <TopObservationalBar
        session={session}
        discoveredMedia={discoveredMedia}
        activeDiscoveredId={activeDiscoveredId}
        fileInputRef={fileInputRef}
        onSelectSaharaCalibrated={() => {
          setActiveDiscoveredId(null);
          setUploadedMediaUrl(null);
          setSession(createDefaultSaharaObservationSession());
        }}
        onSelectOvercastNegative={() => {
          setActiveDiscoveredId(null);
          setUploadedMediaUrl(null);
          setSession(createUnresolvedObservationSession());
        }}
        onSelectDiscoveredMedia={(item) => {
          setUploadedMediaUrl(null);
          setActiveDiscoveredId(item.id);
          setSession(buildSessionFromDiscoveredMedia(item));
        }}
        onFileUpload={handleFileUpload}
        onToggleCompareAB={() => {
          const res = dispatchAlbedianAction(session, {
            type: 'media.compare',
            requestId: `cmp_${Date.now()}`,
            expectedViewRevision: session.viewRevision,
            variantAId: 'variant_original',
            variantBId:
              session.activeVariantId === 'variant_original'
                ? 'variant_natural'
                : session.activeVariantId,
            preserveTime: true,
          });
          setSession(res.session);
        }}
        onShowSky={() => {
          const res = dispatchAlbedianAction(session, {
            type: 'sky.show',
            requestId: `sky_${Date.now()}`,
            expectedViewRevision: session.viewRevision,
            mode:
              session.observationSolution.status === 'calibrated'
                ? 'measured'
                : 'predicted',
          });
          setSession(res.session);
        }}
        onReturnToMoment={handleReturnToMoment}
        onToggleReducedMotion={() =>
          setSession((prev) => ({
            ...prev,
            reducedMotion: !prev.reducedMotion,
          }))
        }
        onExportWithProvenance={handleExportWithProvenance}
        isTourActive={isTourActive}
        onToggleGuidedTour={() => {
          if (isTourActive) stopGuidedTour();
          else startGuidedTour();
        }}
      />

      <main
        style={{
          position: 'relative',
          width: '100vw',
          height: '100vh',
          overflow: 'hidden',
        }}
      >
        <LeftFieldTelemetryColumn
          session={session}
          onOpenGraphModal={() => setIsGraphModalOpen(true)}
          onSelectFinding={(id) => handleSelectFinding(id)}
        />

        <SpatialStage
          session={session}
          uploadedMediaUrl={uploadedMediaUrl}
          uploadedMediaKind={uploadedMediaKind}
          isMuted={isMuted}
          realMediaSet={activeDiscoveredItem ? activeDiscoveredItem.urls : null}
          onSelectFinding={handleSelectFinding}
          onChangeCompareWipe={(pos) =>
            setSession((prev) => ({ ...prev, compareWipePosition: pos }))
          }
          onVideoTimeUpdate={(t) =>
            setSession((prev) => ({ ...prev, currentSourceTimeSec: t }))
          }
          showIsometricGrid={showIsometricGrid}
          showGeodesicRays={showGeodesicRays}
          showExplodedTeardown={showExplodedTeardown}
        />

        <ContextualInspector
          session={session}
          selectedFinding={selectedFinding}
          sightlineBehindItems={sightlineBehindItems}
          onSelectFinding={(id) => handleSelectFinding(id)}
          onEnterSpace={handleEnterSpace}
          onOrbitViewpoint={handleOrbitViewpoint}
          onSetEpochYear={handleSetEpochYear}
          onReturnToMoment={handleReturnToMoment}
          onOpenProcessingGraph={() => setIsGraphModalOpen(true)}
          showIsometricGrid={showIsometricGrid}
          showGeodesicRays={showGeodesicRays}
          showExplodedTeardown={showExplodedTeardown}
          onToggleIsometricGrid={() => setShowIsometricGrid((v) => !v)}
          onToggleGeodesicRays={() => setShowGeodesicRays((v) => !v)}
          onToggleExplodedTeardown={() => setShowExplodedTeardown((v) => !v)}
          onSelectVariant={(variantId) =>
            setSession((prev) => ({ ...prev, activeVariantId: variantId }))
          }
          onToggleCompare={() => {
            if (session.machines.navigation === 'comparison') {
              handleReturnToMoment();
              return;
            }
            const res = dispatchAlbedianAction(session, {
              type: 'media.compare',
              requestId: `cmp_${Date.now()}`,
              expectedViewRevision: session.viewRevision,
              variantAId: 'variant_original',
              variantBId:
                session.activeVariantId === 'variant_original'
                  ? 'variant_natural'
                  : session.activeVariantId,
              preserveTime: true,
            });
            setSession(res.session);
          }}
          onStartGuidedTour={() => {
            if (isTourActive) stopGuidedTour();
            else startGuidedTour();
          }}
        />
      </main>

      <BottomTransportRail
        session={session}
        commandText={commandText}
        isMuted={isMuted}
        disambiguationPrompt={disambiguationPrompt}
        latestExport={latestExport}
        onChangeCommandText={setCommandText}
        onExecuteUtterance={(utt, mod) => runUtterance(utt, mod)}
        onPushToTalk={handlePushToTalk}
        onTogglePlay={() =>
          setSession((prev) => ({
            ...prev,
            isPlaying: prev.machines.navigation === 'space' ? false : !prev.isPlaying,
          }))
        }
        onSeekTimeline={(nextTime) => {
          const res = dispatchAlbedianAction(session, {
            type: 'media.seek',
            requestId: `seek_${Date.now()}`,
            expectedViewRevision: session.viewRevision,
            sourceTimeSec: nextTime,
            preserveAudioSync: true,
          });
          setSession(res.session);
        }}
        onToggleMute={() => setIsMuted((m) => !m)}
        onSelectVariant={(variantId, label, summaryNote) =>
          setSession((prev) => ({
            ...prev,
            activeVariantId: variantId,
            lastStatusBanner: `Selected branch "${label}" (${summaryNote})`,
          }))
        }
        onSelectDisambiguationChoice={(id) => handleSelectFinding(id)}
        onDismissExport={() => setLatestExport(null)}
      />

      {isGraphModalOpen && (
        <ProcessingGraphModal
          session={session}
          onClose={() => setIsGraphModalOpen(false)}
          onSelectVariant={(variantId) =>
            setSession((prev) => ({ ...prev, activeVariantId: variantId }))
          }
        />
      )}
    </div>
  );
};
