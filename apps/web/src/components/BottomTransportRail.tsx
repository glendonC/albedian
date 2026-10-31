import React from 'react';
import { ExportArtifact, ObservationSession } from '@albedian/contracts';

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

export interface BottomTransportRailProps {
  session: ObservationSession;
  commandText: string;
  isMuted: boolean;
  disambiguationPrompt: {
    question: string;
    choices: Array<{ id: string; label: string }>;
  } | null;
  latestExport: ExportArtifact | null;
  onChangeCommandText: (text: string) => void;
  onExecuteUtterance: (utterance: string, modality: 'voice' | 'text') => void;
  onPushToTalk: () => void;
  onTogglePlay: () => void;
  onSeekTimeline: (timeSec: number) => void;
  onToggleMute: () => void;
  onSelectVariant: (variantId: string, label: string, summaryNote: string) => void;
  onSelectDisambiguationChoice: (findingId: string) => void;
  onDismissExport: () => void;
}

/**
 * Bottom Transport, Restoration Branches, and Direct-Manipulation Voice/Command Rail.
 */
export const BottomTransportRail: React.FC<BottomTransportRailProps> = ({
  session,
  commandText,
  isMuted,
  disambiguationPrompt,
  latestExport,
  onChangeCommandText,
  onExecuteUtterance,
  onPushToTalk,
  onTogglePlay,
  onSeekTimeline,
  onToggleMute,
  onSelectVariant,
  onSelectDisambiguationChoice,
  onDismissExport,
}) => {
  return (
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
            onClick={onTogglePlay}
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
            onChange={(e) => onSeekTimeline(parseFloat(e.target.value))}
            style={{ width: 150 }}
          />

          <button type="button" className="alb-btn" onClick={onToggleMute}>
            {isMuted
              ? 'Audio: Muted'
              : session.machines.navigation === 'space'
                ? 'Original Audio: Ducking in 3D Space'
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
              onClick={() => onSelectVariant(variant.id, variant.label, variant.summaryNote)}
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
          onExecuteUtterance(commandText, 'text');
          onChangeCommandText('');
        }}
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <button
          type="button"
          className={`alb-btn ${
            session.machines.voice === 'listening' ? 'alb-btn-active' : ''
          }`}
          onClick={onPushToTalk}
          title="Push-to-talk microphone (never listens permanently by default)"
        >
          {session.machines.voice === 'listening' ? '● Listening…' : 'Mic (Push-to-Talk)'}
        </button>

        <input
          type="text"
          className="alb-command-input"
          placeholder='Ask or command: "Make the sky clearer", "What were we looking at?", "How far away is it?", "Take me there", "Go back"...'
          value={commandText}
          onChange={(e) => onChangeCommandText(e.target.value)}
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
              onClick={() => onExecuteUtterance(utt, 'voice')}
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
                onClick={() => onSelectDisambiguationChoice(c.id)}
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
            {latestExport.audioPreserved ? 'Yes' : 'No'} (Drift{' '}
            {latestExport.audioSyncOffsetMs.toFixed(1)} ms) · Matches:{' '}
            {latestExport.epistemicSummary.catalogMatchedCount} CAT /{' '}
            {latestExport.epistemicSummary.observedCount} OBS /{' '}
            {latestExport.epistemicSummary.predictedCount} PRD
          </div>
          <button
            type="button"
            className="alb-btn"
            style={{ minHeight: 24, fontSize: 11 }}
            onClick={onDismissExport}
          >
            Dismiss
          </button>
        </div>
      )}
    </footer>
  );
};
