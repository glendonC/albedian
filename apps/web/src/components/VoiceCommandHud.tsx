import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CelestialFinding, VoiceMachineState } from '@albedian/contracts';
import { TAKE_ME_THERE, askSuggestionsFor } from './askSuggestions.js';
import { CAPTIONS_CHANGE_EVENT, runAfterSheetSettles } from './phoneSheet.js';

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
  /** The selected star, or null when the session has no selection; the palette offers its questions only then. */
  selectedFinding?: CelestialFinding | null;
}

/** True when a key press belongs to a field, a button or editable text rather than to the HUD's shortcuts. */
function isOwnedByControl(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    tag === 'button' ||
    Boolean(el?.isContentEditable)
  );
}

/**
 * Moment 07 voice element in the HUD black glass (equipped-test house style):
 * - Listen key: a pill that starts push to talk. Press Space or click; push to talk is one capture per press,
 *   so the key says Press, not Hold. Listening shows on the key's ring; nothing draws a level it does not measure.
 * - Ask palette: opens on "/" or the type key, offers the selected star's questions ([1] to [3], arrows and Enter),
 *   opens with an empty field, closes on Esc (which stops there, so it never reaches Back to Moment) or once
 *   something is asked, and hands focus back to whatever had it before.
 * - Captions: "You" with the last utterance and "Albedian" with the status banner, in a polite live region.
 */
export const VoiceCommandHud: React.FC<VoiceCommandHudProps> = ({
  voiceState,
  lastStatusBanner,
  lastUtterance,
  commandText,
  setCommandText,
  onRunUtterance,
  onPushToTalk,
  selectedFinding,
}) => {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const focusBeforeOpen = useRef<HTMLElement | null>(null);
  const setCommandTextRef = useRef(setCommandText);
  setCommandTextRef.current = setCommandText;
  const pushToTalkRef = useRef(onPushToTalk);
  pushToTalkRef.current = onPushToTalk;
  const captionsRef = useRef<HTMLDivElement>(null);

  const isListening = voiceState === 'listening';
  const trimmedBanner = lastStatusBanner?.trim() ?? '';
  const trimmedUtterance = lastUtterance?.trim() ?? '';
  const trimmedCommand = commandText.trim();
  const title = selectedFinding?.title.trim() ?? '';
  const suggestions = askSuggestionsFor(selectedFinding);

  const openPalette = useCallback(() => {
    const active = document.activeElement;
    focusBeforeOpen.current = active instanceof HTMLElement && active !== document.body ? active : null;
    setCommandTextRef.current('');
    setActiveIndex(-1);
    setPaletteOpen(true);
  }, []);

  const closePalette = useCallback(() => {
    setActiveIndex(-1);
    setPaletteOpen(false);
    const previous = focusBeforeOpen.current;
    focusBeforeOpen.current = null;
    if (previous && previous.isConnected) previous.focus();
    else inputRef.current?.blur();
  }, []);

  const ask = (utterance: string) => {
    // A flight anchors on the star's own pixel, so the phone photo settles back before it starts
    if (utterance === TAKE_ME_THERE) runAfterSheetSettles(() => onRunUtterance(utterance, 'text'));
    else onRunUtterance(utterance, 'text');
    closePalette();
  };

  useEffect(() => {
    if (paletteOpen) inputRef.current?.focus();
  }, [paletteOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (isOwnedByControl(event.target)) return;
      if (event.key === ' ' || event.code === 'Space') {
        event.preventDefault();
        if (!event.repeat) pushToTalkRef.current();
      } else if (event.key === '/') {
        event.preventDefault();
        openPalette();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [openPalette]);

  // Publish the captions' height, so a phone sheet can keep the selected star clear of them
  useLayoutEffect(() => {
    const publish = () => {
      const height = captionsRef.current?.getBoundingClientRect().height ?? 0;
      document.documentElement.style.setProperty('--alb-captions-h', height > 0 ? `${height.toFixed(1)}px` : '0px');
      window.dispatchEvent(new Event(CAPTIONS_CHANGE_EVENT));
    };
    publish();
    const node = captionsRef.current;
    const observer = node && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(publish) : null;
    if (node) observer?.observe(node);
    return () => {
      observer?.disconnect();
      document.documentElement.style.setProperty('--alb-captions-h', '0px');
      window.dispatchEvent(new Event(CAPTIONS_CHANGE_EVENT));
    };
  }, [trimmedBanner, trimmedUtterance]);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!trimmedCommand) return;
    ask(trimmedCommand);
  };

  const handleFieldKeys = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closePalette();
      return;
    }
    if (suggestions.length === 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      setActiveIndex((current) =>
        current < 0 && step < 0 ? suggestions.length - 1 : (current + step + suggestions.length) % suggestions.length
      );
      return;
    }
    if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      const chosen = suggestions[activeIndex];
      if (chosen) ask(chosen);
      return;
    }
    const number = Number(e.key);
    if (commandText.length === 0 && Number.isInteger(number) && number >= 1 && number <= suggestions.length) {
      e.preventDefault();
      const chosen = suggestions[number - 1];
      if (chosen) ask(chosen);
    }
  };

  const optionId = (index: number) => `alb-ask-option-${index}`;
  const answerWords = trimmedBanner.split(/\s+/);

  return (
    <>
      {paletteOpen && (
        <div
          className="alb-ask-palette"
          role="dialog"
          aria-label="Ask Albedian"
          data-hud-interactive="true"
          data-testid="ask-palette"
        >
          {title.length > 0 && (
            <div className="alb-ask-head" data-testid="ask-palette-head">
              Ask about {title}
            </div>
          )}
          <form className="alb-voice-bar" onSubmit={handleSubmit}>
            <input
              ref={inputRef}
              type="text"
              className="alb-ask-input"
              data-testid="voice-command-input"
              aria-label="Voice or text command"
              aria-controls={suggestions.length > 0 ? 'alb-ask-suggestions' : undefined}
              aria-activedescendant={activeIndex >= 0 ? optionId(activeIndex) : undefined}
              placeholder={title.length > 0 ? `Ask about ${title}` : 'Ask about a star or give a command'}
              value={commandText}
              onChange={(e) => setCommandText(e.target.value)}
              onKeyDown={handleFieldKeys}
            />
            <button
              type="submit"
              className="alb-ask-submit"
              data-testid="voice-command-submit"
              disabled={trimmedCommand.length === 0}
            >
              Ask
            </button>
          </form>
          {suggestions.length > 0 && (
            <ul className="alb-ask-list" id="alb-ask-suggestions" role="listbox" aria-label="Questions" data-testid="ask-suggestions">
              {suggestions.map((suggestion, index) => (
                <li
                  key={suggestion}
                  id={optionId(index)}
                  className="alb-ask-option"
                  role="option"
                  aria-selected={index === activeIndex}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => ask(suggestion)}
                >
                  <span className="alb-ask-n">{index + 1}</span>
                  <span className="alb-ask-text">{suggestion}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="alb-ask-legend alb-key-only" aria-hidden="true">
            {suggestions.length > 0 && (
              <span>
                <kbd>↑</kbd> <kbd>↓</kbd> Choose
              </span>
            )}
            <span>
              <kbd>↵</kbd> Ask
            </span>
            <span>
              <kbd>Esc</kbd> Close
            </span>
          </div>
        </div>
      )}
      <div className="alb-voice-hud" data-hud-interactive="true" data-testid="voice-command-hud">
        {trimmedBanner.length > 0 && (
          <div className="alb-voice-captions" ref={captionsRef} role="status" aria-live="polite" data-testid="voice-status-banner">
            {trimmedUtterance.length > 0 && (
              <p className="alb-voice-caption alb-voice-caption--you">
                <span className="alb-voice-speaker">You</span>{' '}
                <span data-testid="voice-last-utterance">{trimmedUtterance}</span>
              </p>
            )}
            <p className="alb-voice-caption alb-voice-caption--answer" key={trimmedBanner}>
              <span className="alb-voice-speaker">Albedian</span>{' '}
              {answerWords.map((word, index) => (
                <React.Fragment key={index}>
                  {index > 0 && ' '}
                  <span className="alb-voice-word" style={{ '--i': index } as React.CSSProperties}>
                    {word}
                  </span>
                </React.Fragment>
              ))}
            </p>
          </div>
        )}
        <div className="alb-listen-pill">
          <button
            type="button"
            className={`alb-listen-key ${isListening ? 'is-listening' : ''}`}
            data-testid="voice-ptt-btn"
            aria-pressed={isListening}
            aria-label={isListening ? 'Listening, speak now' : 'Ask out loud'}
            onClick={onPushToTalk}
          >
            <span className="alb-listen-ring" aria-hidden="true" />
            {isListening ? (
              <span>Listening, speak now</span>
            ) : (
              <>
                <span className="alb-key-only">
                  Press <kbd>Space</kbd> to ask
                </span>
                <span className="alb-touch-only">Tap to ask</span>
              </>
            )}
          </button>
          <span className="alb-listen-sep" aria-hidden="true" />
          <button
            type="button"
            className="alb-type-key"
            data-testid="voice-type-btn"
            aria-expanded={paletteOpen}
            aria-label="Type a question"
            onClick={() => (paletteOpen ? closePalette() : openPalette())}
          >
            <span className="alb-key-only">
              <kbd>/</kbd> to type
            </span>
            <span className="alb-touch-only">Type</span>
          </button>
        </div>
      </div>
    </>
  );
};
