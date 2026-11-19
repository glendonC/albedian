import React from 'react';
import { answerFindings, type ObservationSession, type VoiceQuestionReply } from '@albedian/contracts';
import { getMarkerVisualSpec } from '@albedian/design-system';
import { EvidenceTag, LookIcon, Logo, MainAction } from '../look/index.js';
import { sessionPhotoRect, useViewportSize } from '../components/sourcePhotoRect.js';
import { PhotoCorners } from './PhotoCorners.js';
import { leaderPath, revealMarks, type RevealMark } from './revealMarks.js';
import { StarCard } from './StarCard.js';
import { AnswerPanel } from './AnswerPanel.js';
import { evidenceCounts, revealStatus } from './revealStatus.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';

export interface RevealPhotoProps {
  session: ObservationSession;
  onSelectFinding: (findingId: string, pointerCoords?: { xPx: number; yPx: number }) => void;
  /** The logo, pressed: back to the home. */
  onHome: () => void;
  /** No corners and no marks: the browser turned the photo differently from its tag, so their places would be wrong. */
  frameMismatch?: boolean;
  /** Original pixels is pressed (original, decided 2026-11-12): no corners and no marks; the words and the card stay. */
  originalPixels?: boolean;
  onToggleOriginalPixels?: () => void;
  /** Go there on the picked star's card. */
  onGoThere?: (findingId: string) => void;
  /** The card's close button, Esc, or a tap on empty sky. */
  onCloseCard?: () => void;
  /** "Show predicted sky" on a reveal no star was matched on (no-match reveal, approved 2026-11-18). */
  onShowPredictedSky?: () => void;
  /** The answer to the last question, in the card's place, with the star it is about lit (ask, approved 2026-11-12). */
  answer?: VoiceQuestionReply | null;
  onCloseAnswer?: () => void;
  viewportWidthPx?: number;
  viewportHeightPx?: number;
}

const MARK_CLASS: Readonly<Record<RevealMark['kind'], string>> = {
  catalog: 'reveal-mark is-catalog',
  predicted: 'reveal-mark is-predicted',
};

/**
 * A mark's drawing, in a box centred on the star: the approved page's rings and ticks, sized as it sizes them. The
 * picked look is a star's (the reveal record); a predicted object keeps its dashed ring when picked, so the mark never
 * stops saying it is predicted (the auditor's verdict on unit-1d; its picked look is the design lane's to give).
 */
const MarkGlyph: React.FC<{ mark: RevealMark }> = ({ mark }) => (
  <svg className="reveal-mark__glyph" viewBox="-24 -24 48 48" aria-hidden="true" focusable="false">
    {mark.picked && mark.kind === 'catalog' ? (
      <>
        <circle className="reveal-mark__halo" r="22" />
        <circle r="10" />
        <path d="M0 -18V-13M0 13V18M-18 0H-13M13 0H18" />
      </>
    ) : mark.kind === 'catalog' ? (
      <>
        <circle r="6" />
        <path d="M0 -12V-9M0 9V12M-12 0H-9M9 0H12" />
      </>
    ) : (
      <circle r="7" strokeDasharray="2.8 2.6" />
    )}
  </svg>
);

/**
 * The reveal's "your photo" on the laptop (reveal, approved 2026-11-12): over the photo, which SourcePhotoStage draws
 * under it, the viewfinder corners at its exact frame and a mark on each catalog matched star; top left the logo and
 * what the solve found, in its own counts; bottom left how many findings the photo has of each evidence class. The
 * estimate's disclosure and the solve's warnings sit in the status block (estimate and cautions, decided the same
 * night). Every word and number comes from the solution (revealStatus.ts, revealMarks.ts).
 */
export const RevealPhoto: React.FC<RevealPhotoProps> = ({
  session,
  onSelectFinding,
  onHome,
  frameMismatch = false,
  originalPixels = false,
  onToggleOriginalPixels,
  onGoThere,
  onCloseCard,
  onShowPredictedSky,
  answer = null,
  onCloseAnswer,
  viewportWidthPx,
  viewportHeightPx,
}) => {
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  // The photo alone: a frame the browser turned wrongly, or Original pixels pressed
  const bareFrame = frameMismatch || originalPixels;
  const rect = bareFrame ? null : sessionPhotoRect(session, viewW, viewH);
  // An answer lights the first star it is about, as a picked star, in the picked star's place; none for a refusal
  const lit = answer ? (answerFindings(answer.answer)[0] ?? null) : undefined;
  const marked = lit === undefined ? session : { ...session, selectedFindingId: lit };
  const picked = session.observationSolution?.findings.find((finding) => finding.id === marked.selectedFindingId) ?? null;
  const marks = revealMarks(marked, viewW, viewH);
  const status = revealStatus(session);
  const counts = evidenceCounts(session.observationSolution);

  return (
    <div className="reveal-photo" data-testid="reveal-photo" data-hud-interactive="true">
      {rect && <PhotoCorners rect={rect} />}

      {!bareFrame && picked && marks.some((mark) => mark.picked) && (
        <svg className="reveal-leader" data-testid="reveal-leader" aria-hidden="true" focusable="false">
          <path d={leaderPath(marks.find((mark) => mark.picked)!, viewW)} />
        </svg>
      )}

      {!bareFrame && (
      <div className="reveal-marks" data-testid="reveal-marks">
        {marks.map((mark) =>
          mark.uncertaintyPx === null ? null : (
            <svg
              key={`may-be-off-${mark.finding.id}`}
              className="reveal-mark__may-be-off"
              data-testid={`reveal-mark-ring-${mark.finding.id}`}
              aria-hidden="true"
              style={{ left: `${mark.xPx}px`, top: `${mark.yPx}px`, width: `${2 * mark.uncertaintyPx + 2}px`, height: `${2 * mark.uncertaintyPx + 2}px` }}
              viewBox={`${-mark.uncertaintyPx - 1} ${-mark.uncertaintyPx - 1} ${2 * mark.uncertaintyPx + 2} ${2 * mark.uncertaintyPx + 2}`}
            >
              <circle r={mark.uncertaintyPx} />
            </svg>
          )
        )}
        {marks.map((mark) => (
          <button
            key={mark.finding.id}
            type="button"
            className={`${MARK_CLASS[mark.kind]}${mark.picked ? ' is-picked' : ''}${mark.nameOnLeft ? ' is-left' : ''}`}
            data-testid={`reveal-mark-${mark.finding.id}`}
            data-evidence-class={mark.finding.evidenceClass}
            aria-label={
              getMarkerVisualSpec({ evidenceClass: mark.finding.evidenceClass, title: mark.finding.title, isSelected: mark.picked, isFocused: false, isHovered: false })
                .ariaDescription
            }
            aria-pressed={mark.picked}
            style={{ left: `${mark.xPx}px`, top: `${mark.yPx}px` }}
            onClick={() => onSelectFinding(mark.finding.id, { xPx: mark.xPx, yPx: mark.yPx })}
          >
            <MarkGlyph mark={mark} />
            {mark.name !== null && <span className="reveal-mark__name">{mark.name}</span>}
          </button>
        ))}
      </div>
      )}

      <button type="button" className="reveal-word" data-testid="reveal-home" onClick={onHome}>
        <Logo sizePx={21} />
      </button>

      {status.kind === 'unmatched' ? (
        <div className="reveal-status is-unmatched" data-testid="reveal-status" role="status">
          {status.lines.map((line) => (
            <p key={line} className="reveal-status__lead">
              {line}
            </p>
          ))}
          {status.predictedSky && <MainAction label="Show predicted sky" onClick={() => onShowPredictedSky?.()} />}
          {status.warnings.length > 0 && (
            <ul className="reveal-status__warns">
              {status.warnings.map((warning) => (
                <li key={warning}>
                  <LookIcon name="info" />
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
      <div className="reveal-status" data-testid="reveal-status" role="status">
        {status.tag && <EvidenceTag evidenceClass={status.tag} />}
        {status.lines.map((line) => (
          <p key={line} className="reveal-status__line">
            {line}
          </p>
        ))}
        {status.warnings.map((warning) => (
          <p key={warning} className="reveal-status__warning">
            <LookIcon name="info" />
            <span>{warning}</span>
          </p>
        ))}
      </div>
      )}

      {answer && <AnswerPanel key={`${answer.heard}|${answer.answer.sentence}`} reply={answer} solution={session.observationSolution} onClose={() => onCloseAnswer?.()} />}

      {!answer && picked && (
        <StarCard
          key={picked.id}
          finding={picked}
          originalPixels={originalPixels}
          onToggleOriginalPixels={() => onToggleOriginalPixels?.()}
          onGoThere={() => onGoThere?.(picked.id)}
          onClose={() => onCloseCard?.()}
        />
      )}

      {counts.length > 0 && (
        <div className="reveal-legend" data-testid="reveal-legend">
          <p className="reveal-legend__title">In this photo</p>
          {counts.map((row) => (
            <p key={row.evidenceClass} className="reveal-legend__row">
              <span className="reveal-legend__count">{row.text}</span>
              <EvidenceTag evidenceClass={row.evidenceClass} />
            </p>
          ))}
        </div>
      )}
    </div>
  );
};
