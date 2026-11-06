import React from 'react';
import type { VoiceMachineState } from '@albedian/contracts';

export interface VoiceCommandHudProps {
  voiceState: VoiceMachineState;
  lastStatusBanner?: string | null;
  lastUtterance?: string | null;
  commandText: string;
  setCommandText: (nextText: string) => void;
  onRunUtterance: (
    utterance: string,
    modality?: 'voice' | 'text' | 'pointer'
  ) => void;
  onPushToTalk: () => void;
}

/**
 * Moment 07 voice and text HUD:
 * - Renders a Siri iOS 18 style perimeter screen-edge glow only when `voiceState === 'listening'`.
 * - Renders `lastStatusBanner` in a polite live region above the command bar when non-empty.
 * - Wires Push to talk and the text command form directly to `onPushToTalk`, `setCommandText`,
 *   and `onRunUtterance`.
 */
export const VoiceCommandHud: React.FC<VoiceCommandHudProps> = ({
  voiceState,
  lastStatusBanner,
  lastUtterance,
  commandText,
  setCommandText,
  onRunUtterance,
  onPushToTalk,
}) => {
  const isListening = voiceState === 'listening';
  const trimmedBanner = lastStatusBanner?.trim() ?? '';
  const trimmedUtterance = lastUtterance?.trim() ?? '';
  const trimmedCommand = commandText.trim();

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!trimmedCommand) return;
    onRunUtterance(trimmedCommand, 'text');
  };

  return (
    <>
      {isListening && (
        <div
          className="alb-voice-edge-glow"
          data-testid="voice-edge-glow"
          aria-hidden="true"
        />
      )}
      <div
        className="alb-voice-hud"
        data-hud-interactive="true"
        data-testid="voice-command-hud"
      >
        {trimmedBanner.length > 0 && (
          <div
            className="alb-voice-status-banner"
            role="status"
            aria-live="polite"
            data-testid="voice-status-banner"
          >
            {trimmedUtterance.length > 0 && (
              <div
                className="alb-voice-utterance"
                data-testid="voice-last-utterance"
              >
                {trimmedUtterance}
              </div>
            )}
            <p className="alb-voice-status-text">{trimmedBanner}</p>
          </div>
        )}
        <form className="alb-voice-bar" onSubmit={handleSubmit}>
          <button
            type="button"
            className={`alb-voice-ptt-btn ${isListening ? 'is-listening' : ''}`}
            data-testid="voice-ptt-btn"
            aria-pressed={isListening}
            onClick={onPushToTalk}
          >
            {isListening ? 'Listening' : 'Push to talk'}
          </button>
          <input
            type="text"
            className="alb-voice-input"
            data-testid="voice-command-input"
            aria-label="Voice or text command"
            placeholder="Ask about a star or give a command..."
            value={commandText}
            onChange={(e) => setCommandText(e.target.value)}
          />
          <button
            type="submit"
            className="alb-voice-submit-btn"
            data-testid="voice-command-submit"
            disabled={trimmedCommand.length === 0}
          >
            Ask
          </button>
        </form>
      </div>
    </>
  );
};
