import React, { useEffect, useRef } from 'react';
import type { ObservationSolution, VoiceAnswer } from '@albedian/contracts';
import { EvidenceTag } from '../look/index.js';
import { statementLine } from './answerWords.js';
import '../styles/motion.css';

/** Whether a key went to a field the person is typing in. */
const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable === true;
};

/**
 * The answer in the star card's place (ask, approved 2026-11-12): the words as heard, the answer's sentence, then
 * under a hairline each statement as its evidence tag with its value line where one is worded (answerWords.ts). A
 * refusal is the words and its sentence only, and so is a command the app could not carry out (command, decided
 * 2026-11-12: its refusal shows where answers show). Every word is the reply's or the app's own; Esc closes it.
 */
export interface ShownAnswer {
  /** The words as heard (or as typed); empty when a capture heard nothing. */
  heard: string;
  /** The answer's sentence, or the refusal's. */
  sentence: string;
  /** Under the sentence, smaller: the rest of a listening ending, or "Ask again." (C11) */
  sub?: string;
  /** The answer route's answer, for its statements and the star it is about; none for a command's refusal. */
  answer: VoiceAnswer | null;
}

export const AnswerPanel: React.FC<{
  shown: ShownAnswer;
  solution: ObservationSolution | undefined;
  onClose: () => void;
  /** At phone width, under the photo in the counts' place (ask, approved 2026-11-12, iPhone; #p-answer). */
  phone?: boolean;
  /** Its foot's distance from the screen's foot, where something it must not cover stands (UIUX-tagphone-READY.md item B). */
  footPx?: number | null;
  /** On the predicted sky: on its card surface in the card's place, or in the card sheet's place at phone width (refusal-on-predsky-one). */
  sky?: boolean;
}> = ({ shown, solution, onClose, phone = false, footPx = null, sky = false }) => {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || typing(event.target)) return;
      event.preventDefault();
      close.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const { heard, sentence, sub, answer } = shown;
  return (
    <section className={`${phone ? 'reveal-answer is-phone' : 'reveal-answer'}${sky ? ' is-sky' : ''}`} data-testid="reveal-answer" aria-live="polite" style={footPx === null ? undefined : { bottom: `${footPx}px` }}>
      {heard !== '' && <p className="reveal-answer__q">{heard}</p>}
      <p className="reveal-answer__s">{sentence}</p>
      {sub !== undefined && <p className="reveal-answer__sub">{sub}</p>}
      {answer?.status === 'answered' && (
        <div className="reveal-answer__st">
          {answer.statements.flatMap((statement, index) => {
            const line = statementLine(statement, solution);
            return [
              <EvidenceTag key={`tag-${index}`} evidenceClass={statement.evidenceClass} />,
              ...(line === null ? [] : [<span key={`line-${index}`} className="reveal-answer__v">{line}</span>]),
            ];
          })}
        </div>
      )}
    </section>
  );
};
