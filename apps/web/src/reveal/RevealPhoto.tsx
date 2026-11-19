import React, { useEffect, useState } from 'react';
import { answerFindings, type ObservationSession } from '@albedian/contracts';
import { getMarkerVisualSpec, MOTION_TOKENS } from '@albedian/design-system';
import { EvidenceTag, LookIcon, Logo, MainAction } from '../look/index.js';
import { sessionPhotoRect, useViewportSize } from '../components/sourcePhotoRect.js';
import { PhotoCorners } from './PhotoCorners.js';
import { leaderPath, revealMarks, type RevealMark } from './revealMarks.js';
import { StarCard } from './StarCard.js';
import { AnswerPanel, type ShownAnswer } from './AnswerPanel.js';
import { RealSkyLayer } from './RealSkyLayer.js';
import { phoneSlotRect } from './phoneSlot.js';
import { PhoneNoMatch } from './PhoneNoMatch.js';

/** How long Real sky takes to dissolve in or out: the motion record's view change (reveal.css .real-sky). */
export const REAL_SKY_FADE_MS = MOTION_TOKENS.viewMs;

/** What the view there is, word for word from the approved fly record: the spec's sentence, then the layer's sources. */
const THERE_DISCLOSURE =
  'Drawn or computed by the app; not measured in the source recording. Stars at their Hipparcos 2007 and Gaia DR3 distances. Milky Way: ESO/S. Brunier.';
import { realSkyResidualArcmin } from './realSkyGrid.js';
import { oneDecimal, type FlightReadout } from './flightView.js';
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
  answer?: ShownAnswer | null;
  onCloseAnswer?: () => void;
  /** Real sky is shown: the panorama through the solve over the photo's frame, its status the Simulated tag and sentence. */
  realSky?: boolean;
  /** There, after Go there: the readout from the flight's own distances, in the card's place (fly, approved 2026-11-12). */
  flight?: FlightReadout | null;
  /** Whether the frame's corners are drawn: a clip filling the width has none (INTERIM: UIUX-clip-QUESTION.md). */
  corners?: boolean;
  /** At phone width (reveal, approved, iPhone 1): smaller marks, the counts under the photo, no logo, status or corners. */
  phone?: boolean;
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
const MarkGlyph: React.FC<{ mark: RevealMark; phone?: boolean }> = ({ mark, phone = false }) => (
  <svg className="reveal-mark__glyph" viewBox="-24 -24 48 48" aria-hidden="true" focusable="false">
    {mark.picked && mark.kind === 'catalog' ? (
      <>
        <circle className="reveal-mark__halo" r="22" />
        <circle r="10" />
        <path d="M0 -18V-13M0 13V18M-18 0H-13M13 0H18" />
      </>
    ) : mark.kind === 'catalog' && phone ? (
      // At phone width the ring is 4.5 with its ticks from 7.5 to 9 (reveal, approved, iPhone 1; #rbp-photo)
      <>
        <circle r="4.5" />
        <path d="M0 -9V-7.5M0 7.5V9M-9 0H-7.5M7.5 0H9" />
      </>
    ) : mark.kind === 'catalog' ? (
      <>
        <circle r="6" />
        <path d="M0 -12V-9M0 9V12M-12 0H-9M9 0H12" />
      </>
    ) : mark.picked ? (
      // A picked predicted object (INTERIM: UIUX-predicted-pick-QUESTION.md): its dashed ring enlarged, with an outer ring
      <>
        <circle className="reveal-mark__halo" r="22" />
        <circle r="10" strokeDasharray="2.8 2.6" />
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
  realSky = false,
  flight = null,
  corners = true,
  phone = false,
  viewportWidthPx,
  viewportHeightPx,
}) => {
  const windowSize = useViewportSize();
  const viewW = viewportWidthPx ?? windowSize.widthPx;
  const viewH = viewportHeightPx ?? windowSize.heightPx;
  // The photo alone: a frame the browser turned wrongly, or Original pixels pressed
  const bareFrame = frameMismatch || originalPixels;
  // Real sky, and there after Go there, keep the photo's frame: marks stand where they stand on the photo
  const there = flight !== null;
  const skyShown = realSky || there;
  const framed: ObservationSession = skyShown ? { ...session, spatialView: { ...session.spatialView, mode: 'source_2d' } } : session;
  const frame = sessionPhotoRect(framed, viewW, viewH);
  // There, the frame's corners leave with the photo (fly, approved)
  // At phone width the corners mark the slot over Real sky only (reveal, approved, iPhone 1 and 2)
  const rect = bareFrame || there || !corners || (phone && !skyShown) || !frame ? null : phone ? phoneSlotRect(frame, viewW) : frame;
  // The layer stays a moment after leaving the sky, so it fades out as it faded in
  const [skyMounted, setSkyMounted] = useState(skyShown);
  useEffect(() => {
    if (skyShown) {
      setSkyMounted(true);
      return undefined;
    }
    const timer = setTimeout(() => setSkyMounted(false), REAL_SKY_FADE_MS);
    return () => clearTimeout(timer);
  }, [skyShown]);
  const residual = realSky && session.observationSolution ? realSkyResidualArcmin(session.observationSolution) : null;
  // Whether the panorama is on screen: the status says Simulated only then, and the photo's own status otherwise (while
  // it loads, when it cannot be drawn, with Original pixels pressed: the verdict on unit-realsky-1)
  const [skyDrawn, setSkyDrawn] = useState(false);
  const skyOnScreen = skyShown && skyDrawn && !originalPixels;
  // An answer lights the first star it is about, as a picked star, in the picked star's place; none for a refusal
  const lit = answer ? (answer.answer ? (answerFindings(answer.answer)[0] ?? null) : null) : undefined;
  const marked = lit === undefined ? framed : { ...framed, selectedFindingId: lit };
  const picked = session.observationSolution?.findings.find((finding) => finding.id === marked.selectedFindingId) ?? null;
  // There, only the picked star's mark stays (fly, approved)
  const marks = revealMarks(marked, viewW, viewH, { phone }).filter((mark) => !there || mark.picked);
  const status = revealStatus(session);
  const counts = evidenceCounts(session.observationSolution);

  return (
    <div className={phone ? 'reveal-photo is-phone' : 'reveal-photo'} data-testid="reveal-photo" data-hud-interactive="true">
      {skyMounted && !originalPixels && <RealSkyLayer session={framed} rect={frame} viewportWidthPx={viewW} viewportHeightPx={viewH} leaving={!skyShown} onDrawn={setSkyDrawn} phone={phone} there={there} />}
      {rect && <PhotoCorners rect={rect} />}

      {!phone && !bareFrame && picked && marks.some((mark) => mark.picked) && (
        <svg className={marks.find((mark) => mark.picked)!.kind === 'predicted' ? 'reveal-leader is-predicted' : 'reveal-leader'} data-testid="reveal-leader" aria-hidden="true" focusable="false">
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
            <MarkGlyph mark={mark} phone={phone} />
            {mark.name !== null && <span className="reveal-mark__name">{mark.name}</span>}
          </button>
        ))}
      </div>
      )}

      {!phone && (
      <button type="button" className="reveal-word" data-testid="reveal-home" onClick={onHome}>
        <Logo sizePx={21} />
      </button>
      )}

      {/* There on the phone the Simulated tag rides in the readout (#p-there) */}
      {phone && !skyOnScreen && status.kind === 'unmatched' ? (
        <PhoneNoMatch status={status} onShowPredictedSky={() => onShowPredictedSky?.()} />
      ) : phone && (!skyOnScreen || there) ? null : there && skyOnScreen ? (
        <div className="reveal-status" data-testid="reveal-status" role="status">
          <EvidenceTag evidenceClass="simulated" />
          <p className="reveal-status__line">{THERE_DISCLOSURE}</p>
        </div>
      ) : skyOnScreen ? (
        <div className={phone ? 'reveal-status is-phone' : 'reveal-status'} data-testid="reveal-status" role="status">
          <EvidenceTag evidenceClass="simulated" />
          {/* A verified solve always carries its residual (the contract); without one no "within" is claimed and no sentence shows */}
          {residual !== null && (
            <p className="reveal-status__line">{`ESO's photograph of this part of the sky, placed by your photo's solve to within ${residual}′. Not light your camera recorded.`}</p>
          )}
        </div>
      ) : status.kind === 'unmatched' ? (
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

      {answer && <AnswerPanel key={`${answer.heard}|${answer.sentence}`} shown={answer} solution={session.observationSolution} onClose={() => onCloseAnswer?.()} phone={phone} />}

      {!answer && flight && (
        <section className={phone ? 'flight-readout is-phone' : 'flight-readout'} data-testid="flight-readout" aria-label={flight.name}>
          <EvidenceTag evidenceClass="simulated" />
          {/* On the phone, where no status line shows, the readout says what the view is (AGENTS.md 1.2) */}
          {phone && <p className="flight-readout__note">{THERE_DISCLOSURE}</p>}
          <h1>{flight.name}</h1>
          <div className="flight-readout__stats">
            {flight.toStarLy !== null && (
              <div className="flight-readout__stat">
                <b>
                  {oneDecimal(flight.toStarLy)}
                  <small>ly</small>
                </b>
                <span>{`to ${flight.name}`}</span>
              </div>
            )}
            <div className="flight-readout__stat">
              <b>
                {oneDecimal(flight.fromEarthLy)}
                <small>ly</small>
              </b>
              <span>from Earth</span>
            </div>
          </div>
        </section>
      )}

      {!answer && !there && picked && (
        <StarCard
          key={picked.id}
          finding={picked}
          originalPixels={originalPixels}
          onToggleOriginalPixels={() => onToggleOriginalPixels?.()}
          onGoThere={() => onGoThere?.(picked.id)}
          onClose={() => onCloseCard?.()}
          phone={phone}
        />
      )}

      {phone && !answer && !picked && !skyShown && status.kind !== 'unmatched' && counts.length > 0 && (
        // Under the photo at phone width: the catalog matched count large with its tag, then the others with theirs;
        // an answer takes their place (ask, approved, iPhone; #p-answer)
        <div className="reveal-phone-counts" data-testid="reveal-legend">
          {counts
            .filter((row) => row.evidenceClass === 'catalog_matched')
            .map((row) => (
              <p key={row.evidenceClass} className="reveal-phone-counts__big">
                <b>{row.text}</b>
                <EvidenceTag evidenceClass={row.evidenceClass} />
              </p>
            ))}
          <p className="reveal-phone-counts__more">
            {counts
              .filter((row) => row.evidenceClass !== 'catalog_matched')
              .map((row) => (
                <span key={row.evidenceClass} className="reveal-phone-counts__item">
                  <b>{row.text}</b>
                  <EvidenceTag evidenceClass={row.evidenceClass} />
                </span>
              ))}
          </p>
        </div>
      )}

      {!phone && !there && counts.length > 0 && (
        <div className="reveal-legend" data-testid="reveal-legend">
          {/* A clip names its kind, as "your clip" does (INTERIM: UIUX-clip-QUESTION.md, a new word for the owner) */}
          <p className="reveal-legend__title">{session.sourceAsset?.mediaKind === 'video' ? 'In this clip' : 'In this photo'}</p>
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
