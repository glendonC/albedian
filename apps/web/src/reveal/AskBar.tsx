import React, { useEffect, useRef, useState } from 'react';
import { IconButton, LookIcon, MainAction, SecondAction } from '../look/index.js';

/** The words heard so far while listening, and whether the recogniser has settled the last of them. */
export interface HeardWords {
  text: string;
  settled: boolean;
}

export interface AskBarProps {
  /** Whether this browser can listen: when it cannot, the button reads "Type to ask" and opens the field. */
  canListen: boolean;
  /** The microphone is opening or open. */
  listening: boolean;
  words: HeardWords | null;
  onListen: () => void;
  onSend: (text: string) => void;
  /** There, after Go there: "Back to Moment", the screen's one main button, before Ask out loud (fly, approved 2026-11-12). */
  onBack?: () => void;
}

/** Whether a key belongs to a field, a button or editable text rather than to the bar's keys. */
function ownedByControl(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'button' || el?.isContentEditable === true;
}

/**
 * The words as heard, the last one at its own weight until the recogniser settles it, in one element (the approved
 * page's .lbl), so the pill's gap falls only between the words and the circle.
 */
function heardLabel(words: HeardWords): React.ReactNode {
  const at = words.text.lastIndexOf(' ');
  return (
    <span className="ask-bar__words">
      {words.settled ? words.text : at < 0 ? <i>{words.text}</i> : (
        <>
          {words.text.slice(0, at + 1)}
          <i>{words.text.slice(at + 1)}</i>
        </>
      )}
    </span>
  );
}

/**
 * Asking on the laptop reveal, bottom centre (ask, approved 2026-11-12; typed, decided the same night): "Ask out loud"
 * alone with the keyboard button beside it. A press or Space starts listening: the button fills and shows the words
 * as they are heard. The keyboard button or "/" turns the ask button into the field in place; Enter or the arrow
 * sends, Esc closes. Nothing on screen names Space or "/".
 */
export const AskBar: React.FC<AskBarProps> = ({ canListen, listening, words, onListen, onSend, onBack }) => {
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const listen = useRef(onListen);
  listen.current = onListen;

  useEffect(() => {
    if (typing) input.current?.focus();
  }, [typing]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || ownedByControl(event.target)) return;
      if (canListen && (event.key === ' ' || event.code === 'Space')) {
        event.preventDefault();
        if (!event.repeat) listen.current();
      } else if (event.key === '/') {
        event.preventDefault();
        setTyping(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [canListen]);

  const close = () => {
    setTyping(false);
    setText('');
  };
  const send = (event: React.FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    onSend(text);
    close();
  };

  return (
    <div className="ask-bar" data-hud-interactive="true" data-testid="ask-bar">
      {onBack && <MainAction label="Back to Moment" icon="arrow-u-up-left" size="home" onClick={() => onBack()} />}
      {typing ? (
        <form className="ask-bar__field" onSubmit={send}>
          <input
            ref={input}
            type="text"
            aria-label="Type a question"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Escape') return;
              event.preventDefault();
              close();
            }}
          />
          <button type="submit" className="ask-bar__send" aria-label="Send" disabled={!text.trim()}>
            <LookIcon name="arrow-up" />
          </button>
        </form>
      ) : canListen ? (
        <>
          <SecondAction
            part={listening ? 'ask-bar__ask is-listening' : 'ask-bar__ask'}
            label={listening && words && words.text ? heardLabel(words) : 'Ask out loud'}
            icon="microphone"
            size="home"
            onClick={() => onListen()}
          />
          <IconButton part="ask-bar__type" label="Type a question" icon="keyboard" size="home" onClick={() => setTyping(true)} />
        </>
      ) : (
        <SecondAction
          part="ask-bar__ask"
          label="Type to ask"
          icon="keyboard"
          size="home"
          onClick={() => setTyping(true)}
        />
      )}
    </div>
  );
};
