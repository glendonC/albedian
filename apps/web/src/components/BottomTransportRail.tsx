import React from 'react';
import { ExportArtifact, ObservationSession } from '@albedian/contracts';

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
 * Panel 08: Field Simulator Observation Log & Voice Direct Manipulation Track
 * inspired by @elvar_official's `| FLIGHT LOG 08` timeline scrubber.
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
  onSelectDisambiguationChoice,
  onDismissExport,
}) => {
  const durationSec = Math.max(1, session.sourceAsset.durationMs / 1000);
  const progressPct = Math.min(100, Math.max(0, (session.currentSourceTimeSec / durationSec) * 100));

  return (
    <footer
      className="albedian-glass-panel"
      style={{
        position: 'absolute',
        bottom: 10,
        left: 14,
        right: 14,
        height: 62,
        zIndex: 25,
        borderRadius: 8,
        padding: '6px 14px',
        display: 'grid',
        gridTemplateColumns: '1fr 390px',
        alignItems: 'center',
        gap: 18,
      }}
    >
      {/* Left: | OBSERVATION LOG 08 + Vermilion Play Circle + Waypoint Scrubber */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 8.5,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ width: 2, height: 9, background: '#FF4522', display: 'inline-block' }} />
            <span style={{ fontWeight: 600, color: '#F8FAFC', letterSpacing: '0.08em' }}>
              OBSERVATION LOG
            </span>
            <span style={{ color: 'rgba(148, 163, 184, 0.65)', marginLeft: 4 }}>
              TAKE-OFF · 00.0S
            </span>
          </div>

          <span
            style={{
              color: '#FF4522',
              fontWeight: 600,
              letterSpacing: '0.06em',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: 420,
            }}
          >
            OPERATOR · {(session.lastStatusBanner || 'WCS CALIBRATED').toUpperCase()}
          </span>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button
              type="button"
              onClick={onToggleMute}
              style={{
                background: 'transparent',
                border: 'none',
                color: isMuted ? '#FF4522' : '#10B981',
                fontFamily: '"JetBrains Mono", monospace',
                fontSize: 8.5,
                fontWeight: 600,
                cursor: 'pointer',
                padding: 0,
              }}
            >
              {isMuted
                ? 'AUDIO: MUTED'
                : session.machines.navigation === 'space'
                  ? 'AUDIO: -5.2dB SPACE DUCK'
                  : 'AUDIO: 48KHZ PRESERVED ●'}
            </button>
            <span style={{ color: 'rgba(148, 163, 184, 0.7)' }}>08</span>
          </div>
        </div>

        {/* Scrubber Track with Vermilion Play Button & Waypoint Ticks */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button
            type="button"
            aria-label={session.isPlaying ? 'Pause' : 'Play'}
            onClick={onTogglePlay}
            disabled={session.machines.navigation === 'space'}
            style={{
              width: 24,
              height: 24,
              borderRadius: '50%',
              background: '#FF4522',
              color: '#FFFFFF',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 10,
              fontWeight: 700,
              cursor: 'pointer',
              flexShrink: 0,
              boxShadow: '0 0 10px rgba(255, 69, 34, 0.5)',
            }}
          >
            {session.isPlaying ? '⏸' : '▶'}
          </button>

          <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center' }}>
            <input
              type="range"
              className="albedian-scrubber"
              aria-label="Source Timeline Scrub"
              min={0}
              max={durationSec}
              step={0.1}
              value={session.currentSourceTimeSec}
              onChange={(e) => onSeekTimeline(parseFloat(e.target.value))}
              style={{
                width: '100%',
                background: `linear-gradient(to right, #FF4522 ${progressPct}%, rgba(148, 163, 184, 0.22) ${progressPct}%)`,
              }}
            />
          </div>

          <span
            style={{
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 9.5,
              color: '#F8FAFC',
              minWidth: 78,
              textAlign: 'right',
            }}
          >
            {session.currentSourceTimeSec.toFixed(1)}s / {durationSec.toFixed(1)}s
          </span>
        </div>
      </div>

      {/* Right: Direct Voice / Natural-Language Command Bar */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 8,
            color: 'rgba(148, 163, 184, 0.72)',
          }}
        >
          <span>DIRECT VOICE & INTENT ENGINE</span>
          <span style={{ color: '#10B981' }}>● SCHEMA VALIDATED</span>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (commandText.trim()) {
              onExecuteUtterance(commandText, 'text');
            }
          }}
          style={{ display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <button
            type="button"
            onClick={onPushToTalk}
            style={{
              background:
                session.machines.voice === 'listening'
                  ? 'rgba(16, 185, 129, 0.18)'
                  : 'rgba(15, 23, 38, 0.75)',
              color: session.machines.voice === 'listening' ? '#10B981' : '#F8FAFC',
              border: '1px solid rgba(16, 185, 129, 0.45)',
              borderRadius: 4,
              padding: '5px 8px',
              fontSize: 8.5,
              fontWeight: 600,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            ● VOICE
          </button>

          <input
            type="text"
            aria-label="Voice or natural language command input"
            placeholder='"Make the sky clearer", "Select Betelgeuse", "Take me there"...'
            value={commandText}
            onChange={(e) => onChangeCommandText(e.target.value)}
            style={{
              flex: 1,
              background: 'rgba(2, 4, 9, 0.65)',
              color: '#F8FAFC',
              border: '1px solid rgba(148, 163, 184, 0.22)',
              borderRadius: 4,
              padding: '5px 8px',
              fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              outline: 'none',
            }}
          />

          <button
            type="submit"
            style={{
              background: '#FF4522',
              color: '#FFFFFF',
              border: 'none',
              borderRadius: 4,
              padding: '5px 9px',
              fontSize: 8.5,
              fontWeight: 700,
              fontFamily: '"JetBrains Mono", monospace',
              cursor: 'pointer',
            }}
          >
            EXEC ↵
          </button>
        </form>
      </div>

      {/* Floating Disambiguation or Export Toast */}
      {(disambiguationPrompt || latestExport) && (
        <div
          className="albedian-glass-panel"
          style={{
            position: 'absolute',
            bottom: 70,
            right: 0,
            padding: '8px 12px',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontFamily: '"JetBrains Mono", monospace',
            fontSize: 9.5,
          }}
        >
          {disambiguationPrompt && (
            <>
              <span style={{ color: '#FBBF24' }}>{disambiguationPrompt.question}</span>
              {disambiguationPrompt.choices.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onSelectDisambiguationChoice(c.id)}
                  style={{
                    background: '#FF4522',
                    color: '#FFF',
                    border: 'none',
                    borderRadius: 3,
                    padding: '2px 6px',
                    fontSize: 8.5,
                    cursor: 'pointer',
                  }}
                >
                  {c.label}
                </button>
              ))}
            </>
          )}
          {latestExport && (
            <>
              <span style={{ color: '#10B981' }}>
                Export Verified ({latestExport.sourceSha256.slice(0, 10)}…)
              </span>
              <button
                type="button"
                onClick={onDismissExport}
                style={{
                  background: 'transparent',
                  color: '#94A3B8',
                  border: '1px solid rgba(148,163,184,0.3)',
                  borderRadius: 3,
                  padding: '1px 5px',
                  fontSize: 8,
                  cursor: 'pointer',
                }}
              >
                ×
              </button>
            </>
          )}
        </div>
      )}
    </footer>
  );
};
