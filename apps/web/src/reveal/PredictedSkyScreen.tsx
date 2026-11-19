import React from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { EvidenceTag, Logo } from '../look/index.js';
import { predictedSkyWords } from './predictedSky.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';

/**
 * D33 on the laptop (UIUX-predsky-READY.md items 1, 5 and 6): the approved There screen's frame over the app's sky (the
 * logo top left; the Sound circle, Back to Moment and Ask out loud are the viewport's), and the status column: the
 * Predicted tag, the heading, the disclosure with "Not lined up with your photo.", a hairline, then the time, the
 * place and the catalogues. None of the old panel's lines appear. The horizon, the cardinal letters and D20's marks
 * are their own units.
 */
export const PredictedSkyScreen: React.FC<{ session: ObservationSession; onHome: () => void }> = ({ session, onHome }) => {
  const words = predictedSkyWords(session);
  return (
    <div className="predsky" data-testid="predicted-sky" data-evidence-class="predicted">
      <button type="button" className="reveal-word" data-testid="predicted-sky-home" onClick={onHome}>
        <Logo sizePx={21} />
      </button>
      <div className="reveal-status predsky-status" data-testid="reveal-status" data-moment="predicted-sky" role="status">
        <EvidenceTag evidenceClass="predicted" />
        <p className="predsky-status__lead">{words.heading}</p>
        <p className="predsky-status__note">{words.note}</p>
        <div className="predsky-status__facts">
          {words.facts.map((fact) => (
            <p key={fact}>{fact}</p>
          ))}
        </div>
      </div>
    </div>
  );
};
