import React, { useEffect, useMemo, useRef, useState } from 'react';
import { alignReadout, alignStartWords, alignTargets, startPose, type AlignPose, type AlignStart } from './alignPose.js';
import { AlignLayer } from './AlignLayer.js';
import { answerFindings, DISCLOSURES, hasVerifiedWcs, orientedSize, type ObservationSession } from '@albedian/contracts';
import { deepSkyRowOf, isDeepSky, useDeepSkyTable, type DeepSkySymbol } from './deepSky.js';
import { saveFromServer, variantExportUrl } from '../services/apiClient.js';
import { getMarkerVisualSpec, MOTION_TOKENS } from '@albedian/design-system';
import { EvidenceTag, LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import { SIDE_FADE } from '../components/SourcePhotoStage.js';
import { RestoreCompare, RestoreSteps, RestoreWhy } from './RestoreParts.js';
import type { Restoration } from './restoration.js';
import { sessionPhotoRect, useViewportSize } from '../components/sourcePhotoRect.js';
import { PhotoCorners } from './PhotoCorners.js';
import { leaderPath, markArrivalRanks, revealMarks, type RevealMark } from './revealMarks.js';
import { StarCard } from './StarCard.js';
import { AnswerPanel, type ShownAnswer } from './AnswerPanel.js';
import { RealSkyLayer } from './RealSkyLayer.js';
import { FlightStarsLayer } from '../spatial/flight/FlightStarsLayer.js';
import { CraftPicker, PilotTrip } from '../spatial/flight/PilotPieces.js';
import { FlightClock } from '../spatial/flight/pilotReadout.js';
import { craftFromSearch, type Craft } from '../spatial/flight/crafts.js';
import { noDistanceSentence } from '../spatial/flight/flightRings.js';
import { CONE_LINES, coneGeometryOf } from '../spatial/flight/coneView.js';
import { ConeReadout, FlightPills } from '../spatial/flight/ConePieces.js';
import { STEER_LINES, steeringSpeedLine } from '../spatial/flight/steering.js';
import { placeFinding } from '../spatial/flight/flightFrame.js';
import { phoneSlotRect } from './phoneSlot.js';
import { PhoneNoMatch } from './PhoneNoMatch.js';

/** How long Real sky takes to dissolve in or out: the motion record's view change (reveal.css .real-sky). */
export const REAL_SKY_FADE_MS = MOTION_TOKENS.viewMs;
// How long the reveal is arriving: its marks' span, then the status, In this photo and Ask out loud, 20 ms apart (the
// motion record). While it is, the status waits for the marks; after, a view change brings it in at the words' delay.
export const REVEAL_ARRIVAL_MS = MOTION_TOKENS.marksSpanMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs;

/** What the view there is, word for word from the approved fly record: the spec's sentence, then the layer's sources. */
const THERE_DISCLOSURE =
  'Drawn or computed by the app; not measured in the source recording. Stars at their Hipparcos 2007 and Gaia DR3 distances. Milky Way: ESO/S. Brunier.';

/**
 * There on the laptop, in the flight through the stars (D27, UIUX-flight-READY.md item 4), word for word and in order:
 * the spec's sentence, the stars' line, the rings' line, the no-distance count when any, the dimmed panorama's credit.
 * The cone view (cone-1) keeps the first two: no rings and no panorama are drawn there.
 */
const FLIGHT_LINES = {
  spec: 'Drawn or computed by the app; not measured in the source recording.',
  stars: 'Stars at their Bailer-Jones (2021) or Hipparcos 2007 distances, as bright as they would look from here.',
  rings: 'Rings: the stars your photo matched.',
  milkyWay: 'Milky Way: ESO/S. Brunier, dimmed in flight, since its stars are as seen from Earth.',
} as const;
import { realSkyResidualArcmin } from './realSkyGrid.js';
import { oneDecimal, type FlightReadout } from './flightView.js';
import { evidenceCounts, revealStatus } from './revealStatus.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';

export interface RevealPhotoProps {
  session: ObservationSession;
  /** The photo's restoration (D10): Restore while idle, its steps while it works, the compare once done. */
  restoration?: Restoration;
  /** Restore, or Try again after a stopped restoration. */
  onRestore?: () => void;
  /** Cancel, while the restoration works. */
  onCancelRestore?: () => Promise<unknown> | void;
  /** Told whenever Line it up yourself (D24) starts or ends, so the phone's close circle (the viewport's) can cancel it. */
  onAligningChange?: (active: boolean) => void;
  /** Changed to cancel an alignment from outside: the phone's close circle while aligning. */
  cancelAligningToken?: number;
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
  /** pilot-1f (D26 item 6): flying on one's own after "Fly on your own", with no star ahead. */
  freeFlight?: boolean;
  /** A press on the sky during free flight hands the camera back: the flight ends. */
  onEndFreeFlight?: () => void;
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
 * A deep-sky object's symbol by its type (D23, copied from build.mjs's symbol()), 12 px across: every outline dashed
 * 2.6 2.4, the cross and ticks solid.
 */
const DEEP_SKY_DASH = '2.6 2.4';
const DeepSkyGlyph: React.FC<{ symbol: DeepSkySymbol }> = ({ symbol }) =>
  symbol === 'globular' ? (
    <>
      <circle r="6" strokeDasharray={DEEP_SKY_DASH} />
      <path d="M-6 0H6M0 -6V6" />
    </>
  ) : symbol === 'cluster_nebula' ? (
    <>
      <rect x="-6" y="-6" width="12" height="12" strokeDasharray={DEEP_SKY_DASH} />
      <circle r="4.8" strokeDasharray={DEEP_SKY_DASH} />
    </>
  ) : symbol === 'planetary' ? (
    <>
      <circle r="6" strokeDasharray={DEEP_SKY_DASH} />
      <path d="M-10.5 0H-6M6 0H10.5M0 -10.5V-6M0 6V10.5" />
    </>
  ) : symbol === 'galaxy' ? (
    <ellipse rx="3" ry="6" strokeDasharray={DEEP_SKY_DASH} />
  ) : (
    <circle r="6" strokeDasharray={DEEP_SKY_DASH} />
  );

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
    ) : mark.deepSky !== null && !mark.picked ? (
      <DeepSkyGlyph symbol={mark.deepSky} />
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
  freeFlight = false,
  onEndFreeFlight,
  phone = false,
  viewportWidthPx,
  viewportHeightPx,
  restoration,
  onRestore,
  onCancelRestore,
  onAligningChange,
  cancelAligningToken = 0,
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
  // The flight through the stars: the craft flown (D26; the page address may ask for one), the drawn stars kept on the
  // sky sphere (D27 item 2), the flight's own measured travel time, and the cone view (cone-1)
  const [craft, setCraft] = useState<Craft | null>(() => (typeof window === 'undefined' ? null : craftFromSearch(window.location.search)));
  const [noDistance, setNoDistance] = useState(0);
  const clock = useRef(new FlightClock());
  const phase = session.spatialView?.transitionPhase;
  const [flightSeconds, setFlightSeconds] = useState<number | null>(null);
  useEffect(() => {
    clock.current.observe(phase, performance.now());
    setFlightSeconds(clock.current.seconds);
  }, [phase]);
  // D28: "Your photo's view" turns the cone view on and off there; leaving there turns it off
  const [coneAsked, setConeAsked] = useState(false);
  useEffect(() => {
    if (!there) setConeAsked(false);
  }, [there]);
  const cone = there && coneAsked;
  // pilot-1e: a press on the sky hands the flight's camera to the person and takes it back; leaving there, or the cone
  // view, ends it. While they steer, the readout's numbers are their camera's own
  const [steeringAsked, setSteeringAsked] = useState(false);
  useEffect(() => {
    if (!there || cone) setSteeringAsked(false);
  }, [there, cone]);
  const steering = there && !cone && !phone && (steeringAsked || freeFlight);
  const [steered, setSteered] = useState<{ fromEarthPc: number; toStarPc: number; speedPcPerS: number } | null>(null);
  // What is only true from Earth (the panorama, the photo-frame marks) leaves in the cone view and while steering
  const earthOnly = cone || steering;
  // Free flight has no star ahead, so the readout keeps "from Earth" only (D26 item 6)
  const shownFlight =
    flight && steering && steered
      ? { ...flight, toStarLy: freeFlight || flight.toStarLy === null ? null : steered.toStarPc * 3.261563777, fromEarthLy: steered.fromEarthPc * 3.261563777 }
      : flight;
  const coneGeometry = cone ? coneGeometryOf(session) : null;
  const skyOnScreen = skyShown && skyDrawn && !originalPixels;
  // An answer lights the first star it is about, as a picked star, in the picked star's place; none for a refusal
  const lit = answer ? (answer.answer ? (answerFindings(answer.answer)[0] ?? null) : null) : undefined;
  const marked = lit === undefined ? framed : { ...framed, selectedFindingId: lit };
  const picked = session.observationSolution?.findings.find((finding) => finding.id === marked.selectedFindingId) ?? null;
  // There, only the picked star's mark stays (fly, approved)
  // Deep-sky objects on a verified solve (D23): their OpenNGC rows read once from the shipped table
  const solution = session.observationSolution;
  const deepSkyWanted = solution?.status === 'calibrated' && hasVerifiedWcs(solution) && solution.findings.some(isDeepSky);
  const deepSky = useDeepSkyTable(deepSkyWanted);
  const marks = revealMarks(marked, viewW, viewH, { phone, deepSky }).filter((mark) => !there || mark.picked);
  // The order the marks arrive in: brightest first (the motion record)
  // A deep-sky mark is not a matched mark: it takes no place in their arrival and appears without movement (D23)
  const arrivingMarks = marks.filter((mark) => mark.deepSky === null || mark.picked);
  const arrival = markArrivalRanks(arrivingMarks);
  const status = revealStatus(session);
  const counts = evidenceCounts(session.observationSolution);
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), REVEAL_ARRIVAL_MS);
    return () => clearTimeout(timer);
  }, []);
  // The restoration (D10), on the laptop over the photo itself: offered while idle on a still, its steps in the counts'
  // place while it works or once it stopped, the compare once done
  const overPhoto = !phone && !there && !skyShown;
  const restoreOffered = overPhoto && onRestore !== undefined && restoration?.phase === 'idle' && session.sourceAsset?.mediaKind === 'image';
  const restoring = overPhoto && (restoration?.phase === 'working' || restoration?.phase === 'stopped');
  // Original pixels shows the photo bare, the compare too (D15)
  const compareShown = overPhoto && restoration?.phase === 'done' && !originalPixels;
  // While the compare is zoomed in, the marks step aside (D15)
  const [compareZoomed, setCompareZoomed] = useState(false);
  useEffect(() => {
    if (!compareShown) setCompareZoomed(false);
  }, [compareShown]);
  // Line it up yourself (D24), on the laptop's no-match reveal over the photo: from the start the file gives, until
  // Cancel or Esc, which keep nothing
  const alignStart = useMemo(() => startPose(session), [session]);
  const alignTargetList = useMemo(() => alignTargets(session.observationSolution), [session.observationSolution]);
  const [aligningState, setAligning] = useState<{ pose: AlignPose; from: AlignStart } | null>(null);
  // On the laptop and at phone width alike (alp-align), over the photo itself
  const alignAllowed = !there && !skyShown && !compareShown && status.kind === 'unmatched' && frame !== null && !bareFrame;
  const aligning = alignAllowed ? aligningState : null;
  const alignOffered = alignAllowed && alignStart !== null && restoration?.phase !== 'working';
  useEffect(() => {
    setAligning(null);
  }, [session.sourceAsset?.sha256, cancelAligningToken]);
  // The phone's close circle is the viewport's: it is told while aligning shows, so the circle cancels it
  const aligningShown = aligning !== null;
  useEffect(() => {
    onAligningChange?.(aligningShown);
  }, [aligningShown]);
  useEffect(() => {
    if (!aligning) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAligning(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [aligning !== null]);
  const asset = session.sourceAsset;
  // Download (D10, D22), 18 px under the fixed sentence on a ready restoration: the export route's one zip, handed to the
  // browser's own download
  const ready = compareShown && restoration?.phase === 'done' ? restoration.job.variantId : undefined;
  const downloadAction =
    ready !== undefined && asset ? (
      <MainAction label="Download" icon="download-simple" onClick={() => saveFromServer(variantExportUrl(asset.sha256, ready))} />
    ) : null;
  const uprightWidth = asset ? orientedSize(asset.width, asset.height, asset.displayOrientation).widthPx : 0;

  return (
    <div className={phone ? 'reveal-photo is-phone' : 'reveal-photo'} data-testid="reveal-photo" data-hud-interactive="true" data-arriving={arriving ? 'true' : undefined}>
      {skyMounted && !originalPixels && !earthOnly && <RealSkyLayer session={framed} rect={frame} viewportWidthPx={viewW} viewportHeightPx={viewH} leaving={!skyShown} onDrawn={setSkyDrawn} phone={phone} there={there} />}
      {there && !originalPixels && <FlightStarsLayer session={session} viewportWidthPx={viewW} viewportHeightPx={viewH} craft={phone ? null : craft} cone={cone} onNoDistanceCount={setNoDistance} phone={phone} steering={steering} onPressSky={phone ? undefined : () => (freeFlight ? onEndFreeFlight?.() : setSteeringAsked((on) => !on))} onSteer={setSteered} />}
      {rect && <PhotoCorners rect={rect} />}

      {!phone && !bareFrame && !earthOnly && picked && marks.some((mark) => mark.picked) && (
        <svg className={marks.find((mark) => mark.picked)!.kind === 'predicted' ? 'reveal-leader is-predicted' : 'reveal-leader'} data-testid="reveal-leader" aria-hidden="true" focusable="false">
          <path pathLength={1} d={leaderPath(marks.find((mark) => mark.picked)!, viewW)} />
        </svg>
      )}

      {/* The compare (D10) in the photo's own frame, over the original the stage draws, with no marks over it (D15) */}
      {compareShown && frame && restoration?.phase === 'done' && asset && (
        <RestoreCompare
          rect={frame}
          src={restoration.variantUrl}
          originalSrc={asset.uri}
          fade={SIDE_FADE}
          orientation={restoration.orientation}
          maxZoom={uprightWidth / Math.max(1, frame.widthPx)}
          onZoomed={setCompareZoomed}
        />
      )}

      {aligning && frame && (
        <AlignLayer rect={frame} pose={aligning.pose} targets={alignTargetList} phone={phone} onPose={(pose) => setAligning((now) => (now ? { ...now, pose } : now))} />
      )}

      {!bareFrame && !earthOnly && !(compareShown && compareZoomed) && (
      <div className="reveal-marks" data-testid="reveal-marks" style={{ '--mark-n': arrivingMarks.length } as React.CSSProperties}>
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
            className={`${MARK_CLASS[mark.kind]}${mark.deepSky !== null && !mark.picked ? ' is-deepsky' : ''}${mark.picked ? ' is-picked' : ''}${mark.nameOnLeft ? ' is-left' : ''}`}
            data-testid={`reveal-mark-${mark.finding.id}`}
            data-evidence-class={mark.finding.evidenceClass}
            aria-label={
              getMarkerVisualSpec({ evidenceClass: mark.finding.evidenceClass, title: mark.finding.title, isSelected: mark.picked, isFocused: false, isHovered: false })
                .ariaDescription
            }
            aria-pressed={mark.picked}
            style={{ left: `${mark.xPx}px`, top: `${mark.yPx}px`, '--mark-i': arrival.get(mark.finding.id) } as React.CSSProperties}
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
      {phone && !skyOnScreen && status.kind === 'unmatched' && aligning ? (
        // Aligning at phone width (alp-align): the sentence at 654, the one line of hints at 700; the close circle
        // cancels. "Solve from here" at 772 waits on SOLVER's contract (lead 07:51)
        <div className="align-phone" data-testid="reveal-status" role="status">
          <p className="align-phone__lead">Drag the stars onto the dots you see.</p>
          <p className="align-phone__help">Drag to move. Turn or pinch with two fingers.</p>
        </div>
      ) : phone && !skyOnScreen && status.kind === 'unmatched' ? (
        <PhoneNoMatch status={status} onShowPredictedSky={() => onShowPredictedSky?.()} onAlign={alignOffered ? () => setAligning(alignStart) : undefined} />
      ) : phone && (!skyOnScreen || there) ? null : there && (skyOnScreen || earthOnly) ? (
        <div key="there" className="reveal-status" data-testid="reveal-status" data-moment="there" role="status">
          <EvidenceTag evidenceClass="simulated" />
          {cone ? (
            <>
              <p className="reveal-status__line">{CONE_LINES.spec}</p>
              <p className="reveal-status__line">{CONE_LINES.view}</p>
              <p className="reveal-status__line">{CONE_LINES.stars}</p>
              <p className="reveal-status__line">{CONE_LINES.rings}</p>
              {noDistanceSentence(noDistance) && <p className="reveal-status__line">{noDistanceSentence(noDistance)}</p>}
            </>
          ) : (
            <>
              <p className="reveal-status__line">{FLIGHT_LINES.spec}</p>
              <p className="reveal-status__line">{FLIGHT_LINES.stars}</p>
              <p className="reveal-status__line">{FLIGHT_LINES.rings}</p>
              {noDistanceSentence(noDistance) && <p className="reveal-status__line">{noDistanceSentence(noDistance)}</p>}
              {!steering && <p className="reveal-status__line">{FLIGHT_LINES.milkyWay}</p>}
              {/* While steering, D26 item 6's plain lines at 13 px, ink 66 percent */}
              {steering &&
                STEER_LINES.map((line) => (
                  <p key={line} className="reveal-status__line" data-testid="steer-line" style={{ fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>
                    {line}
                  </p>
                ))}
            </>
          )}
        </div>
      ) : skyOnScreen ? (
        <div key="real-sky" className={phone ? 'reveal-status is-phone' : 'reveal-status'} data-testid="reveal-status" data-moment="real-sky" role="status">
          <EvidenceTag evidenceClass="simulated" />
          {/* A verified solve always carries its residual (the contract); without one no "within" is claimed and no sentence shows */}
          {residual !== null && (
            <p className="reveal-status__line">{`ESO's photograph of this part of the sky, placed by your photo's solve to within ${residual}′. Not light your camera recorded.`}</p>
          )}
        </div>
      ) : status.kind === 'unmatched' ? (
        <div key="unmatched" className="reveal-status is-unmatched" data-testid="reveal-status" data-moment="unmatched" role="status">
          {compareShown ? (
            // Restored here too: the fixed sentence leads; the warnings stay under it
            <>
              <p className="reveal-status__note">
                <LookIcon name="info" />
                <span>{DISCLOSURES.restoration}</span>
              </p>
              {downloadAction}
            </>
          ) : aligning ? (
            // Aligning (D24): what to do, how, Cancel, then the live readout and where the start came from. "Solve from
            // here" waits on SOLVER's alignment contract (the lead, 07:51)
            <>
              <p className="reveal-status__lead align-lead">Drag the stars onto the dots you see.</p>
              <div className="align-help">
                <span>Drag to move</span>
                <span>Ctrl and drag to turn</span>
                <span>Scroll or pinch to scale</span>
              </div>
              <div className="align-acts">
                <SecondAction label="Cancel" onClick={() => setAligning(null)} />
              </div>
              <p className="align-readout" data-testid="align-readout">
                {alignReadout(aligning.pose)}
                <br />
                {alignStartWords(aligning.from)}
              </p>
            </>
          ) : (
            <>
              {status.lines.map((line) => (
                <p key={line} className="reveal-status__lead">
                  {line}
                </p>
              ))}
              {status.predictedSky && <MainAction label="Show predicted sky" onClick={() => onShowPredictedSky?.()} />}
              {/* Line it up yourself 16 px under Show predicted sky (D24), Restore 10 px under it (D10; the lead, 06:39) */}
              {(alignOffered || restoreOffered) && (
                <div className="reveal-status__act">
                  {alignOffered && <SecondAction label="Line it up yourself" onClick={() => setAligning(alignStart)} />}
                  {restoreOffered && <SecondAction label="Restore" onClick={() => onRestore?.()} />}
                </div>
              )}
            </>
          )}
          {!aligning && status.warnings.length > 0 && (
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
      <div key="photo" className="reveal-status" data-testid="reveal-status" data-moment="photo" role="status">
        {compareShown ? (
          // Once restored, the status place holds the fixed sentence (REQ-RST-9.3), verbatim
          <>
            <p className="reveal-status__note">
              <LookIcon name="info" />
              <span>{DISCLOSURES.restoration}</span>
            </p>
            {downloadAction}
          </>
        ) : (
          <>
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
          </>
        )}
        {restoreOffered && (
          <div className="reveal-status__act">
            <SecondAction label="Restore" onClick={() => onRestore?.()} />
          </div>
        )}
      </div>
      )}

      {answer && <AnswerPanel key={`${answer.heard}|${answer.sentence}`} shown={answer} solution={session.observationSolution} onClose={() => onCloseAnswer?.()} phone={phone} />}

      {!answer && shownFlight && !cone && (
        <section className={phone ? 'flight-readout is-phone' : 'flight-readout'} data-testid="flight-readout" aria-label={shownFlight.name}>
          <EvidenceTag evidenceClass="simulated" />
          {/* D26 item 7: while the person steers, the flight's own speed under the Simulated tag */}
          {steering && steered && (
            <p data-testid="steer-speed" style={{ margin: '10px 0 0', fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>
              {steeringSpeedLine(steered.speedPcPerS)}
            </p>
          )}
          {/* On the phone, where no status line shows, the readout says what the view is (AGENTS.md 1.2) */}
          {phone && <p className="flight-readout__note">{THERE_DISCLOSURE}</p>}
          <h1>{shownFlight.name}</h1>
          <div className="flight-readout__stats">
            {shownFlight.toStarLy !== null && (
              <div className="flight-readout__stat">
                <b>
                  {oneDecimal(shownFlight.toStarLy)}
                  <small>ly</small>
                </b>
                <span>{`to ${shownFlight.name}`}</span>
              </div>
            )}
            <div className="flight-readout__stat">
              <b>
                {oneDecimal(shownFlight.fromEarthLy)}
                <small>ly</small>
              </b>
              <span>from Earth</span>
            </div>
          </div>
          {!phone && !freeFlight && craft && picked && placeFinding(picked).kind === 'at_distance' && (
            <PilotTrip craft={craft} starName={shownFlight.name} starDistancePc={(placeFinding(picked) as { distancePc: number }).distancePc} flightSeconds={flightSeconds} />
          )}
        </section>
      )}

      {there && !phone && (
        <FlightPills craftPill={<CraftPicker craft={craft} onPick={setCraft} />} cone={cone} onCone={() => setConeAsked((on) => !on)} />
      )}
      {!answer && cone && coneGeometry && <ConeReadout geometry={coneGeometry} />}

      {!answer && !there && picked && (
        <StarCard
          key={picked.id}
          finding={picked}
          originalPixels={originalPixels}
          onToggleOriginalPixels={() => onToggleOriginalPixels?.()}
          onGoThere={() => onGoThere?.(picked.id)}
          onClose={() => onCloseCard?.()}
          phone={phone}
          deepSky={isDeepSky(picked) ? deepSkyRowOf(picked, deepSky) : null}
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

      {restoring && restoration !== undefined && (restoration.phase === 'working' || restoration.phase === 'stopped') && (
        <>
          <RestoreWhy
            job={restoration.job}
            sentence={restoration.phase === 'stopped' ? restoration.sentence : null}
            subline={restoration.phase === 'stopped' ? restoration.subline : null}
            canTryAgain={restoration.phase === 'stopped' ? restoration.canTryAgain : true}
            working={restoration.phase === 'working'}
            onCancel={() => onCancelRestore?.()}
            onTryAgain={() => onRestore?.()}
          />
          {restoration.job && <RestoreSteps job={restoration.job} />}
        </>
      )}

      {!phone && !there && !restoring && counts.length > 0 && (
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
