import React, { useEffect, useRef, useState } from 'react';
import { resolveMotionTokens } from '@albedian/design-system';
import { useLeaving, reducedMotion } from '../hooks/useLeaving.js';
import { IconButton, LookIcon, MainAction, SecondAction } from '../look/index.js';
import '../styles/motion.css';

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
  /** At phone width (ask, approved 2026-11-12, iPhone; #p-answer): Ask out loud alone at the phone's size, under the counts. */
  phone?: boolean;
  /** There after arrival: "Fly on your own", outlined, beside Back to Moment (D26 item 6, pilot-1f; laptop only). */
  onFlyOnYourOwn?: () => void;
  /** On the phone while the person steers: "Autopilot", outlined, beside Back to Moment, giving the camera back (D30 item 6). */
  onAutopilot?: () => void;
}

/** Whether a key belongs to a field or editable text, which types it, rather than to the bar's keys. */
function ownedByField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable === true;
}

/** Whether a key belongs to a field or to a button: a focused button takes Space as its own press, never "/". */
function ownedByControl(target: EventTarget | null): boolean {
  return ownedByField(target) || (target as HTMLElement | null)?.tagName?.toLowerCase() === 'button';
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
export const AskBar: React.FC<AskBarProps> = ({ canListen, listening, words, onListen, onSend, onBack, phone = false, onFlyOnYourOwn, onAutopilot }) => {
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const listen = useRef(onListen);
  listen.current = onListen;
  // There, after Go there (the motion record, "Go there" steps 1 and 5): the ask controls leave in words out with the
  // reveal's other words; on the laptop they come back with Back to Moment once the camera is still, after both halves;
  // on the phone Back to Moment is alone there. Reduced motion: the camera does not travel, so on the laptop they stay.
  const there = onBack !== undefined;
  const motion = resolveMotionTokens(reducedMotion());
  const restMs = motion.cameraHalfMs * 2;
  const [rested, setRested] = useState(false);
  useEffect(() => {
    if (!there || restMs === 0) {
      setRested(false);
      return undefined;
    }
    const timer = setTimeout(() => setRested(true), restMs);
    return () => clearTimeout(timer);
  }, [there, restMs]);
  const asks = useLeaving(!there || (!phone && (restMs === 0 || rested)), motion.wordsOutMs);
  const asksMotion = asks.leaving ? 'is-leaving' : there && rested ? 'is-back' : '';
  const withAsks = (part: string) => (asksMotion ? `${part} ${asksMotion}` : part);

  useEffect(() => {
    if (typing) input.current?.focus();
  }, [typing]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || ownedByField(event.target)) return;
      if (canListen && (event.key === ' ' || event.code === 'Space') && !ownedByControl(event.target)) {
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
    <div className={phone ? 'ask-bar is-phone' : 'ask-bar'} data-hud-interactive="true" data-testid="ask-bar">
      {onBack && <MainAction label="Back to Moment" icon="arrow-u-up-left" size="home" onClick={() => onBack()} />}
      {onBack && !phone && onFlyOnYourOwn && <SecondAction part="ask-bar__fly" label="Fly on your own" size="home" onClick={() => onFlyOnYourOwn()} />}
      {onBack && phone && onAutopilot && <SecondAction part="ask-bar__autopilot" label="Autopilot" size="phone" onClick={() => onAutopilot()} />}
      {/* There on the phone, Back to Moment alone (fly, approved, iPhone; #p-there .bar) */}
      {!asks.mounted ? null : typing ? (
        <form className={withAsks('ask-bar__field')} onSubmit={send} inert={asks.leaving}>
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
            part={withAsks(listening ? 'ask-bar__ask is-listening' : 'ask-bar__ask')}
            inert={asks.leaving}
            label={listening && words && words.text ? heardLabel(words) : 'Ask out loud'}
            icon="microphone"
            size="home"
            onClick={() => onListen()}
          />
          {!phone && <IconButton part={withAsks('ask-bar__type')} inert={asks.leaving} label="Type a question" icon="keyboard" size="home" onClick={() => setTyping(true)} />}
        </>
      ) : (
        <SecondAction
          part={withAsks('ask-bar__ask')}
          inert={asks.leaving}
          label="Type to ask"
          icon="keyboard"
          size="home"
          onClick={() => setTyping(true)}
        />
      )}
    </div>
  );
};
