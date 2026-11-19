import React, { useEffect, useRef } from 'react';
import type { ObservationSolution, VoiceQuestionReply } from '@albedian/contracts';
import { EvidenceTag } from '../look/index.js';
import { statementLine } from './answerWords.js';

/** Whether a key went to a field the person is typing in. */
const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  const tag = el?.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable === true;
};

/**
 * The answer in the star card's place (ask, approved 2026-11-12): the words as heard, the answer's sentence, then
 * under a hairline each statement as its evidence tag with its value line where one is worded (answerWords.ts). A
 * refusal is the words and its sentence only. Every word is the reply's own; Esc closes it.
 */
export const AnswerPanel: React.FC<{ reply: VoiceQuestionReply; solution: ObservationSolution | undefined; onClose: () => void }> = ({
  reply,
  solution,
  onClose,
}) => {
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

  const { heard, answer } = reply;
  return (
    <section className="reveal-answer" data-testid="reveal-answer" aria-live="polite">
      <p className="reveal-answer__q">{heard}</p>
      <p className="reveal-answer__s">{answer.sentence}</p>
      {answer.status === 'answered' && (
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
