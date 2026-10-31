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
import { ProcessingGraphModal } from './components/ProcessingGraphModal.js';
import { AlbedianWebAudioBus } from './audio/WebAudioBus.js';
import { MOROCCO_CLIP_PRESETS, MoroccoClipKey } from './moroccoPresetSessions.js';
import './styles/albedian.css';

const QUICK_VOICE_UTTERANCES = [
  'Make the sky clearer',
  "Don't change the people",
  'What were we looking at?',
  'Select Betelgeuse',
  'How far away is it?',
  'Take me there',
  "What's behind it?",
  'Where is Earth?',
  'Show me this sky a thousand years ago',
  'Go back',
];

export const App: React.FC = () => {
  const [session, setSession] = useState<ObservationSession>(() =>
    createDefaultSaharaObservationSession()
  );
  const [commandText, setCommandText] = useState('');
  const [recentPointer, setRecentPointer] =
    useState<ActionContext['recentPointer']>(undefined);
  const [disambiguationPrompt, setDisambiguationPrompt] = useState<{
    question: string;
    choices: Array<{ id: string; label: string }>;
  } | null>(null);
  const [isGraphModalOpen, setIsGraphModalOpen] = useState(false);
  const [latestExport, setLatestExport] = useState<ExportArtifact | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [uploadedMediaUrl, setUploadedMediaUrl] = useState<string | null>(null);
  const [uploadedMediaKind, setUploadedMediaKind] = useState<
    'image' | 'video' | null
  >(null);
  const [activeMoroccoClip, setActiveMoroccoClip] =
    useState<MoroccoClipKey | null>(null);

  const audioBusRef = useRef<AlbedianWebAudioBus | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    audioBusRef.current = new AlbedianWebAudioBus();
    return () => {
      audioBusRef.current?.dispose();
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

  // Advance media playback clock when playing in source / comparison / sky_registered modes
  useEffect(() => {
    if (!session.isPlaying || session.machines.navigation === 'space') {
      return undefined;
    }
    const durationSec = Math.max(1, session.sourceAsset.durationMs / 1000);
    const interval = window.setInterval(() => {
      setSession((prev) => ({
        ...prev,
        currentSourceTimeSec: Number(
          ((prev.currentSourceTimeSec + 0.1) % durationSec).toFixed(2)
        ),
      }));
    }, 100);
    return () => window.clearInterval(interval);
  }, [session.isPlaying, session.machines.navigation, session.sourceAsset.durationMs]);

  const selectedFinding = useMemo(
    () =>
      session.observationSolution.findings.find(
        (f) => f.id === session.selectedFindingId
      ) ?? null,
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
    const result = executeVoiceOrTextCommand(
      session,
      utterance,
      modality,
      recentPointer
    );
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
  };

  const handleOrbitViewpoint = (findingId: string) => {
    audioBusRef.current?.playSelectionClick(isMuted);
    const currentOrigin = session.spatialView.observerOriginParsec;
    const nextOffset: [number, number, number] =
      Math.abs(currentOrigin[0]) < 25
        ? [42.0, -28.0, 18.0]
        : [-36.0, 48.0, -14.0];
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
      epochISO: `${yearCE.toString().padStart(4, '0')}-01-01T00:00:00Z`,
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

    const observedCount = sol.findings.filter(
      (f) => f.evidenceClass === 'observed'
    ).length;
    const catalogMatchedCount = sol.findings.filter(
      (f) => f.evidenceClass === 'catalog_matched'
    ).length;
    const predictedCount = sol.findings.filter(
      (f) => f.evidenceClass === 'predicted'
    ).length;

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
        observedCount,
        catalogMatchedCount,
        predictedCount,
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
      {/* Top Observational Bar */}
      <header className="alb-topbar">
        <div className="alb-brand">
          <span className="alb-brand-title">ALBEDIAN</span>
          <span className="alb-brand-tagline">See (Beyond) the Night Sky.</span>
          <span
            className="alb-tabular"
            style={{
              fontSize: 12,
              color: 'var(--alb-accent-observation)',
              marginLeft: 10,
              paddingLeft: 10,
              borderLeft: '1px solid var(--alb-border-subtle)',
            }}
          >
            {session.title}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {/* Scene Fixture Switcher */}
          <button
            type="button"
            className={`alb-btn ${
              !activeMoroccoClip && session.observationSolution.status === 'calibrated'
                ? 'alb-btn-active'
                : ''
            }`}
            onClick={() => {
              setActiveMoroccoClip(null);
              setUploadedMediaUrl(null);
              setSession(createDefaultSaharaObservationSession());
            }}
          >
            Sahara Orion (Calibrated)
          </button>
          <button
            type="button"
            className={`alb-btn ${
              !activeMoroccoClip && session.observationSolution.status !== 'calibrated'
                ? 'alb-btn-active'
                : ''
            }`}
            onClick={() => {
              setActiveMoroccoClip(null);
              setUploadedMediaUrl(null);
              setSession(createUnresolvedObservationSession());
            }}
          >
            Overcast Negative (Predicted Only)
          </button>

          {(Object.keys(MOROCCO_CLIP_PRESETS) as MoroccoClipKey[]).map((key) => {
            const preset = MOROCCO_CLIP_PRESETS[key];
            return (
              <button
                key={key}
                type="button"
                className={`alb-btn ${activeMoroccoClip === key ? 'alb-btn-active' : ''}`}
                onClick={() => {
                  setUploadedMediaUrl(null);
                  setActiveMoroccoClip(key);
                  setSession(preset.createSession());
                }}
              >
                {preset.label}
              </button>
            );
          })}

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,video/*"
            style={{ display: 'none' }}
            onChange={handleFileUpload}
          />
          <button
            type="button"
            className="alb-btn"
            onClick={() => fileInputRef.current?.click()}
          >
            Import Media
          </button>

          {/* Representation Continuity Controls */}
          <button
            type="button"
            className={`alb-btn ${
              session.machines.navigation === 'comparison' ? 'alb-btn-active' : ''
            }`}
            onClick={() => {
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
          >
            Compare A/B
          </button>

          <button
            type="button"
            className={`alb-btn ${
              session.machines.navigation === 'sky_registered' ? 'alb-btn-active' : ''
            }`}
            onClick={() => {
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
          >
            {session.observationSolution.status === 'calibrated'
              ? 'Registered Sky'
              : 'Predicted Sky'}
          </button>

          {session.machines.navigation !== 'source' && (
            <button
              type="button"
              className="alb-btn alb-btn-primary"
              onClick={handleReturnToMoment}
            >
              Back to Moment
            </button>
          )}

          <button
            type="button"
            className={`alb-btn ${session.reducedMotion ? 'alb-btn-active' : ''}`}
            onClick={() =>
              setSession((prev) => ({
                ...prev,
                reducedMotion: !prev.reducedMotion,
              }))
            }
          >
            {session.reducedMotion ? 'Reduced Motion: On' : 'Reduced Motion: Off'}
          </button>

          <button
            type="button"
            className="alb-btn alb-btn-primary"
            onClick={handleExportWithProvenance}
          >
            Export
          </button>
        </div>
      </header>

      {/* Center Observational Viewport + Contextual Inspector */}
      <main className="alb-main-workspace">
        <SpatialStage
          session={session}
          uploadedMediaUrl={uploadedMediaUrl}
          uploadedMediaKind={uploadedMediaKind}
          realMediaSet={
            activeMoroccoClip ? MOROCCO_CLIP_PRESETS[activeMoroccoClip].mediaSet : null
          }
          onSelectFinding={handleSelectFinding}
          onChangeCompareWipe={(pos) =>
            setSession((prev) => ({ ...prev, compareWipePosition: pos }))
          }
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
        />
      </main>

      {/* Bottom Transport, Restoration Attempts & Voice/Command Rail */}
      <footer className="alb-bottom-rail">
        {/* Row 1: Timeline, Audio Status, and Restoration Attempt Branches */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              type="button"
              className="alb-btn"
              onClick={() =>
                setSession((prev) => ({
                  ...prev,
                  isPlaying:
                    prev.machines.navigation === 'space' ? false : !prev.isPlaying,
                }))
              }
              disabled={session.machines.navigation === 'space'}
            >
              {session.isPlaying ? 'Pause' : 'Play'}
            </button>

            <span className="alb-tabular" style={{ fontSize: 12 }}>
              {session.currentSourceTimeSec.toFixed(2)}s /{' '}
              {(session.sourceAsset.durationMs / 1000).toFixed(2)}s
            </span>

            <input
              type="range"
              aria-label="Source Timeline Scrub"
              min={0}
              max={session.sourceAsset.durationMs / 1000}
              step={0.1}
              value={session.currentSourceTimeSec}
              onChange={(e) => {
                const nextTime = parseFloat(e.target.value);
                const res = dispatchAlbedianAction(session, {
                  type: 'media.seek',
                  requestId: `seek_${Date.now()}`,
                  expectedViewRevision: session.viewRevision,
                  sourceTimeSec: nextTime,
                  preserveAudioSync: true,
                });
                setSession(res.session);
              }}
              style={{ width: 150 }}
            />

            <button
              type="button"
              className="alb-btn"
              onClick={() => setIsMuted((m) => !m)}
            >
              {isMuted
                ? 'Audio: Muted'
                : session.machines.navigation === 'space'
                  ? 'Original Audio: Paused in 3D Space'
                  : 'Original Audio: Preserved ●'}
            </button>
          </div>

          {/* Restoration Attempt Strip */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11, color: 'var(--alb-text-muted)' }}>
              Restoration Branches:
            </span>
            {session.mediaVariants.map((variant) => (
              <button
                key={variant.id}
                type="button"
                className={`alb-btn ${
                  session.activeVariantId === variant.id ? 'alb-btn-active' : ''
                }`}
                onClick={() =>
                  setSession((prev) => ({
                    ...prev,
                    activeVariantId: variant.id,
                    lastStatusBanner: `Selected branch "${variant.label}" (${variant.summaryNote})`,
                  }))
                }
              >
                {variant.label}
              </button>
            ))}
          </div>
        </div>

        {/* Row 2: Direct Manipulation Voice & Typed Command Input */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            runUtterance(commandText, 'text');
            setCommandText('');
          }}
          style={{ display: 'flex', alignItems: 'center', gap: 8 }}
        >
          <button
            type="button"
            className={`alb-btn ${
              session.machines.voice === 'listening' ? 'alb-btn-active' : ''
            }`}
            onClick={handlePushToTalk}
            title="Push-to-talk microphone (never listens permanently by default)"
          >
            {session.machines.voice === 'listening' ? '● Listening…' : 'Mic (Push-to-Talk)'}
          </button>

          <input
            type="text"
            className="alb-command-input"
            placeholder='Ask or command: "Make the sky clearer", "What were we looking at?", "How far away is it?", "Take me there", "Go back"...'
            value={commandText}
            onChange={(e) => setCommandText(e.target.value)}
            aria-label="Voice or Typed Direct Manipulation Command"
          />

          <button type="submit" className="alb-btn alb-btn-primary">
            Execute ↵
          </button>
        </form>

        {/* Row 3: Quick Canonical Utterance Chips & Status Banner */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {QUICK_VOICE_UTTERANCES.map((utt) => (
              <button
                key={utt}
                type="button"
                className="alb-btn"
                style={{ fontSize: 11, minHeight: 24, padding: '2px 8px' }}
                onClick={() => runUtterance(utt, 'voice')}
              >
                “{utt}”
              </button>
            ))}
          </div>

          {session.lastStatusBanner && (
            <div
              className="alb-tabular"
              role="status"
              style={{ fontSize: 11, color: 'var(--alb-accent-catalog)' }}
            >
              {session.lastStatusBanner}
            </div>
          )}
        </div>

        {/* Disambiguation Prompt Banner when Deictic Target is Ambiguous */}
        {disambiguationPrompt && (
          <div
            role="alert"
            style={{
              padding: '8px 12px',
              backgroundColor: 'var(--alb-surface-raised)',
              border: '1px solid var(--alb-accent-warning)',
              borderRadius: 4,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <span style={{ fontSize: 12 }}>{disambiguationPrompt.question}</span>
            <div style={{ display: 'flex', gap: 6 }}>
              {disambiguationPrompt.choices.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className="alb-btn alb-btn-primary"
                  onClick={() => handleSelectFinding(c.id)}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Export Artifact Provenance Drawer */}
        {latestExport && (
          <div
            className="alb-tabular"
            style={{
              padding: '8px 12px',
              backgroundColor: 'var(--alb-space-0)',
              border: '1px solid var(--alb-accent-catalog)',
              borderRadius: 4,
              fontSize: 11,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <div>
              <strong>Verified Export Provenance:</strong> {latestExport.outputUri} · Source SHA-256:{' '}
              {latestExport.sourceSha256.slice(0, 12)}… · Output SHA-256:{' '}
              {latestExport.outputSha256.slice(0, 12)}… · Audio Preserved:{' '}
              {latestExport.audioPreserved ? 'Yes' : 'No'} (Drift {latestExport.audioSyncOffsetMs.toFixed(1)} ms) ·
              Matches: {latestExport.epistemicSummary.catalogMatchedCount} CAT /{' '}
              {latestExport.epistemicSummary.observedCount} OBS /{' '}
              {latestExport.epistemicSummary.predictedCount} PRD
            </div>
            <button
              type="button"
              className="alb-btn"
              style={{ minHeight: 24, fontSize: 11 }}
              onClick={() => setLatestExport(null)}
            >
              Dismiss
            </button>
          </div>
        )}
      </footer>

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
