import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CelestialFinding, ObservationSession } from '@albedian/contracts';
import { getMarkerVisualSpec, resolveMotionTokens } from '@albedian/design-system';
import { EvidenceTag, IconButton, Logo } from '../look/index.js';
import { useViewportSize } from '../components/sourcePhotoRect.js';
import { blockLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import type { CameraSkyPose } from '../spatial/cosmosRenderer.js';
import { deepSkyRowOf, isDeepSky, useDeepSkyTable } from './deepSky.js';
import { phoneFacts, predictedSkyWords } from './predictedSky.js';
import { StarCard } from './StarCard.js';
import { fadeOf } from './keepClear.js';
import { predictedSkyMarks, skyLeaderPath, type Box } from './predictedSkyMarks.js';

/** The star card's seven staggered lines (top, name, sub, numbers, source, actions, notes; motion.css). */
const STAR_CARD_LINES = 7;

/** The places on screen D33's marks keep clear of (ruled 13:10): the status column, the Sound circle, the foot's buttons. */
function clearOf(status: HTMLElement | null): Box[] {
  // On the phone: the readout, its foot at y 756 (F2), and the close circle
  // The Credits link joins them (keepclear-answer-views-one, 21:07)
  return [status, document.querySelector('.reveal-nav .sound-control'), document.querySelector('.ask-bar'), document.querySelector('.predsky-top'), document.querySelector('.reveal-credits')]
    .filter((el): el is Element => el !== null)
    .flatMap((el) => {
      const r = el.getBoundingClientRect();
      if (!(r.width > 0 && r.height > 0)) return [];
      // The status column and the Sound circle carry the ruled fade on the laptop: a name clears its reach (20:12)
      const fade = fadeOf(el);
      return [{ x0: r.left, x1: r.right, y0: r.top, y1: r.bottom, ...(fade ? { fade } : {}) }];
    });
}
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';
import '../styles/motion.css';

/**
 * D33 on the laptop (UIUX-predsky-READY.md items 1, 5 and 6): the approved There screen's frame over the app's sky (the
 * logo top left; the Sound circle, Back to Moment and Ask out loud are the viewport's), and the status column: the
 * Predicted tag, the heading, the disclosure with "Not lined up with your photo.", a hairline, then the time, the
 * place and the catalogues. None of the old panel's lines appear. Item 4: D20's amber rings and names on the predicted
 * stars brighter than magnitude 3.2, placed where the renderer's camera draws them, followed frame by frame. The
 * horizon and the cardinal letters are the renderer's (SPATIAL's file; asked in WEB-UX-predsky-2-FILES.md).
 */
export const PredictedSkyScreen: React.FC<{
  session: ObservationSession;
  onHome: () => void;
  skyPose?: () => CameraSkyPose | null;
  phone?: boolean;
  /** A name pressed: picks its star, whose D14 card then opens (ruled 13:25). */
  onSelect?: (findingId: string) => void;
  onGoThere?: (findingId: string) => void;
  onCloseCard?: () => void;
  /** predsky-starless-one: whether the dome shows the starless glow (its line says so only then). */
  starless?: boolean;
}> = ({ session, onHome, skyPose, phone = false, onSelect, onGoThere, onCloseCard, starless = false }) => {
  const words = predictedSkyWords(session, starless);
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
  const status = useRef<HTMLDivElement>(null);
  const picked = session.observationSolution?.findings.find((f) => f.id === session.selectedFindingId && f.evidenceClass === 'predicted') ?? null;
  const leaveMotion = resolveMotionTokens(reducedMotion());
  const cardLeaveMs = phone ? Math.max(blockLeaveMs(STAR_CARD_LINES, leaveMotion), leaveMotion.sheetMs) : blockLeaveMs(STAR_CARD_LINES, leaveMotion);
  const [heldStar, setHeldStar] = useState<CelestialFinding | null>(picked);
  useEffect(() => {
    if (picked) setHeldStar(picked);
  }, [picked]);
  const cardStar = picked ?? heldStar;
  const card = useLeaving(picked !== null, cardLeaveMs);
  const phoneAboveCard = phone && card.mounted;
  const deepSky = useDeepSkyTable(session.observationSolution?.findings.some(isDeepSky) === true);
  // The open card's box (ruled 17:05): rings and names under it are left out, the picked star's own mark stays.
  // Read untransformed offset geometry first so phone sheet-in translateY at frame 0 still clears the settled 469..814 px card area.
  const [cardBox, setCardBox] = useState<Box | null>(null);
  useLayoutEffect(() => {
    const cardEl = card.mounted ? document.querySelector<HTMLElement>('.predsky .reveal-card') : null;
    const r = cardEl?.getBoundingClientRect();
    const box =
      cardEl && cardEl.offsetWidth > 0 && cardEl.offsetHeight > 0
        ? { x0: cardEl.offsetLeft, x1: cardEl.offsetLeft + cardEl.offsetWidth, y0: cardEl.offsetTop, y1: cardEl.offsetTop + cardEl.offsetHeight }
        : r && r.width > 0 && r.height > 0
          ? { x0: r.left, x1: r.right, y0: r.top, y1: r.bottom }
          : null;
    setCardBox(box);
  }, [card.mounted, cardStar?.id, phone, size.widthPx, size.heightPx]);
  // Names at 11 px on the phone, 13 on the laptop
  const fontPx = phone ? 11 : 13;
  const marks = predictedSkyMarks(session.observationSolution, pose, size.widthPx, size.heightPx, fontPx, [...clearOf(status.current), ...(cardBox ? [cardBox] : [])], picked?.id ?? null);
  const facts = phone ? phoneFacts(words) : words.facts;
  const pickedMark = picked && !phone ? (marks.find((m) => m.id === picked.id) ?? null) : null;
  const leaderNow = pickedMark ? skyLeaderPath(pickedMark, size.widthPx, fontPx) : null;
  const [heldLeader, setHeldLeader] = useState<string | null>(leaderNow);
  useEffect(() => {
    if (picked !== null) setHeldLeader(leaderNow);
  }, [picked, leaderNow]);
  const leaderD = leaderNow ?? heldLeader;
  const leader = useLeaving(leaderNow !== null, cardLeaveMs);
  return (
    <div className={phone ? 'predsky is-phone' : 'predsky'} data-testid="predicted-sky" data-evidence-class="predicted">
      {!phone && leader.mounted && leaderD !== null && (
        <svg className={leader.leaving ? 'reveal-leader is-predicted is-leaving' : 'reveal-leader is-predicted'} data-testid="predicted-sky-leader" aria-hidden="true" focusable="false">
          <path pathLength={1} d={leaderD} />
        </svg>
      )}
      <svg className="align-layer__rings" data-testid="predicted-sky-rings" aria-hidden="true" focusable="false">
        {marks.map((mark) => (
          <circle key={mark.id} cx={mark.xPx} cy={mark.yPx} r={mark.r} />
        ))}
      </svg>
      {marks.map((mark) =>
        mark.name === null ? null : (
          <button
            type="button"
            key={`name:${mark.id}`}
            className={mark.nameOnLeft ? 'align-layer__name predsky-name is-left' : 'align-layer__name predsky-name'}
            // Heard as the reveal's marks are: the name, its evidence class and whether it is picked
            aria-label={getMarkerVisualSpec({ evidenceClass: 'predicted', title: mark.name, isSelected: mark.id === picked?.id, isFocused: false, isHovered: false }).ariaDescription}
            aria-pressed={mark.id === picked?.id}
            onClick={() => onSelect?.(mark.id)}
            style={mark.nameOnLeft ? { right: `${size.widthPx - (mark.xPx - mark.r - 4)}px`, top: `${mark.yPx}px` } : { left: `${mark.xPx + mark.r + 4}px`, top: `${mark.yPx}px` }}
          >
            {mark.name}
          </button>
        )
      )}
      {phone ? (
        // The approved phone There frame: the close circle top left
        <div className="predsky-top">
          <IconButton label="Close" icon="x" size="phone" onClick={onHome} />
        </div>
      ) : (
        <button type="button" className="reveal-word" data-testid="predicted-sky-home" onClick={onHome}>
          <Logo sizePx={21} />
        </button>
      )}
      <div
        ref={status}
        className={phoneAboveCard ? 'reveal-status predsky-status is-above-card' : 'reveal-status predsky-status'}
        data-testid="reveal-status"
        data-moment="predicted-sky"
        role="status"
      >
        {/* The 14:55 ruling: the stars' Predicted tag, then the Milky Way's Simulated tag, both at full from the first frame;
            when the phone StarCard rises or leaves below, both tags and ESO's line step 16 px above the sheet */}
        <div className="predsky-status__tags">
          <EvidenceTag evidenceClass="predicted" />
          <EvidenceTag evidenceClass="simulated" />
        </div>
        {!phoneAboveCard && <p className="predsky-status__lead">{words.heading}</p>}
        {!phoneAboveCard && <p className="predsky-status__note">{words.note}</p>}
        <p className="predsky-status__sky">{words.milkyWay}</p>
        {!phoneAboveCard && (
          <div className="predsky-status__facts">
            {facts.map((fact) => (
              <p key={fact}>{fact}</p>
            ))}
          </div>
        )}
      </div>
      {card.mounted && cardStar && (
        <StarCard
          key={cardStar.id}
          finding={cardStar}
          deepSky={isDeepSky(cardStar) ? deepSkyRowOf(cardStar, deepSky) : null}
          originalPixels={false}
          onToggleOriginalPixels={() => {}}
          onGoThere={() => onGoThere?.(cardStar.id)}
          onClose={() => onCloseCard?.()}
          leaving={card.leaving}
          phone={phone}
          inSky
        />
      )}
    </div>
  );
};
