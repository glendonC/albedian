import React, { useEffect, useState } from 'react';
import type { ObservationSession } from '@albedian/contracts';
import { EvidenceTag, Logo } from '../look/index.js';
import { useViewportSize } from '../components/sourcePhotoRect.js';
import type { CameraSkyPose } from '../spatial/cosmosRenderer.js';
import { predictedSkyWords } from './predictedSky.js';
import { predictedSkyMarks } from './predictedSkyMarks.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';

/**
 * D33 on the laptop (UIUX-predsky-READY.md items 1, 5 and 6): the approved There screen's frame over the app's sky (the
 * logo top left; the Sound circle, Back to Moment and Ask out loud are the viewport's), and the status column: the
 * Predicted tag, the heading, the disclosure with "Not lined up with your photo.", a hairline, then the time, the
 * place and the catalogues. None of the old panel's lines appear. Item 4: D20's amber rings and names on the predicted
 * stars brighter than magnitude 3.2, placed where the renderer's camera draws them, followed frame by frame. The
 * horizon and the cardinal letters are the renderer's (SPATIAL's file; asked in WEB-UX-predsky-2-FILES.md).
 */
export const PredictedSkyScreen: React.FC<{ session: ObservationSession; onHome: () => void; skyPose?: () => CameraSkyPose | null }> = ({ session, onHome, skyPose }) => {
  const words = predictedSkyWords(session);
  // The renderer's camera, followed frame by frame; a new pose only when it changed
  const [pose, setPose] = useState<CameraSkyPose | null>(null);
  useEffect(() => {
    if (!skyPose) return undefined;
    let frame = 0;
    let last = '';
    const follow = () => {
      const now = skyPose();
      const key = now ? JSON.stringify(now) : '';
      if (key !== last) {
        last = key;
        setPose(now);
      }
      frame = window.requestAnimationFrame(follow);
    };
    follow();
    return () => window.cancelAnimationFrame(frame);
  }, [skyPose]);
  const size = useViewportSize();
  const marks = predictedSkyMarks(session.observationSolution, pose, size.widthPx, size.heightPx);
  return (
    <div className="predsky" data-testid="predicted-sky" data-evidence-class="predicted">
      <svg className="align-layer__rings" data-testid="predicted-sky-rings" aria-hidden="true" focusable="false">
        {marks.map((mark) => (
          <circle key={mark.id} cx={mark.xPx} cy={mark.yPx} r={mark.r} />
        ))}
      </svg>
      {marks.map((mark) =>
        mark.name === null ? null : (
          <span
            key={`name:${mark.id}`}
            className={mark.nameOnLeft ? 'align-layer__name is-left' : 'align-layer__name'}
            style={mark.nameOnLeft ? { right: `${size.widthPx - (mark.xPx - mark.r - 4)}px`, top: `${mark.yPx}px` } : { left: `${mark.xPx + mark.r + 4}px`, top: `${mark.yPx}px` }}
          >
            {mark.name}
          </span>
        )
      )}
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
