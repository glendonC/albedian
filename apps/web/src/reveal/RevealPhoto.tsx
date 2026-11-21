import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { alignReadout, alignStartWords, alignTargets, personAlignmentOf, placedAtPose, startPose, type AlignedFrom, type AlignPose, type AlignStart } from './alignPose.js';
import { ALIGN_RING_PX, AlignLayer, StillAlignMarks } from './AlignLayer.js';
import { EARLY_SOURCE_WORDS, earlyPoseOf, type EarlyPose, type PlacedTarget } from './earlyPose.js';
import { lockOnPlan } from './lockOn.js';
import type { ObservationSolution, PersonAlignment } from '@albedian/contracts';
import { answerFindings, DISCLOSURES, hasVerifiedWcs, orientedSize, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { deepSkyRowOf, isDeepSky, useDeepSkyTable, type DeepSkySymbol } from './deepSky.js';
import { readRestorations, saveFromServer, variantExportUrl, variantFileUrl, type StoredRestoration } from '../services/apiClient.js';
import { getMarkerVisualSpec, MOTION_TOKENS, resolveMotionTokens } from '@albedian/design-system';
import { blockLeaveMs, linesLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import { EvidenceTag, LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import { SIDE_FADE } from '../components/SourcePhotoStage.js';
import { RestoreCompare, RestoreSteps, RestoreVersions, RestoreWhy } from './RestoreParts.js';
import type { Restoration } from './restoration.js';
import { sessionPhotoRect, useViewportSize } from '../components/sourcePhotoRect.js';
import { PhotoCorners } from './PhotoCorners.js';
import { leaderPath, markArrivalRanks, revealMarks, type RevealMark } from './revealMarks.js';
import { StarCard } from './StarCard.js';
import { AnswerPanel, type ShownAnswer } from './AnswerPanel.js';
import { RealSkyLayer } from './RealSkyLayer.js';
import { FlightStarsLayer } from '../spatial/flight/FlightStarsLayer.js';
import { CraftPicker, DetailsSheet, PhoneTagRow, PilotTrip } from '../spatial/flight/PilotPieces.js';
import { phoneCraftFraming } from '../spatial/flight/craftScene.js';
import { CRAFT_PICKER_FOOT, craftSpeedSource } from '../spatial/flight/pilotReadout.js';
import { FlightClock } from '../spatial/flight/pilotReadout.js';
import { craftFromSearch, type Craft } from '../spatial/flight/crafts.js';
import { noDistanceSentence } from '../spatial/flight/flightRings.js';
import { CONE_LINES, coneGeometryOf } from '../spatial/flight/coneView.js';
import { ConeReadout, FlightPills } from '../spatial/flight/ConePieces.js';
import { PHONE_STEER_LINE, STEER_LINES, steeringSpeedLine } from '../spatial/flight/steering.js';
import { placeFinding } from '../spatial/flight/flightFrame.js';
import { phoneSlotRect } from './phoneSlot.js';
import { PhoneAlignRefused, PhoneNoMatch } from './PhoneNoMatch.js';

/** How long Real sky takes to dissolve in or out: the motion record's view change (reveal.css .real-sky). */
export const REAL_SKY_FADE_MS = MOTION_TOKENS.viewMs;
/** The star card's lines, top to foot (motion.css staggers them): top, name, sub, numbers, source, actions, notes. */
const STAR_CARD_LINES = 7;
// How long the reveal is arriving: its marks' span, then the status, In this photo and Ask out loud, 20 ms apart (the
// motion record). While it is, the status waits for the marks; after, a view change brings it in at the words' delay.
export const REVEAL_ARRIVAL_MS = MOTION_TOKENS.marksSpanMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs;
/** The arrival that locks on (D25): the travel, the matched marks' colour at rest, then the words as after the marks. */
export const LOCK_ON_ARRIVAL_MS = MOTION_TOKENS.frameInMs + MOTION_TOKENS.colourMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs;

/**
 * Under the photo at phone width: the catalog matched count large with its tag, then the others with theirs; an answer
 * takes their place (ask, approved, iPhone; #p-answer). In the estimate's block (tag place A) the same row, in its flow.
 */
const PhoneCounts: React.FC<{ counts: ReturnType<typeof evidenceCounts> }> = ({ counts }) => (
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
);

/** When the last lock-on mark has gone: a travelling one after its travel and colour, a leaving one after frame out. */
function lockOnMarksEndMs(plan: { travelling: readonly unknown[]; leaving: readonly unknown[] }, reduce: boolean): number {
  const motion = resolveMotionTokens(reduce);
  if (plan.travelling.length > 0) return reduce ? motion.dissolveMs : motion.frameInMs + motion.colourMs;
  return plan.leaving.length > 0 ? motion.frameOutMs : 0;
}

/** One early mark of the lock-on: its ring at the point, its name beside it as the working screen drew it. */
const LockOnMark: React.FC<{ className: string; xPx: number; yPx: number; name: string | null; nameOnLeft: boolean; dxPx?: number; dyPx?: number }> = ({ className, xPx, yPx, name, nameOnLeft, dxPx, dyPx }) => (
  <div
    className={className}
    style={{ left: `${xPx}px`, top: `${yPx}px`, width: 0, height: 0, ...(dxPx === undefined ? {} : { '--lock-dx': `${dxPx}px`, '--lock-dy': `${dyPx}px` }) } as React.CSSProperties}
  >
    <svg className="align-layer__rings" aria-hidden="true" focusable="false">
      <circle cx={0} cy={0} r={ALIGN_RING_PX} />
    </svg>
    {name !== null && (
      <span className={nameOnLeft ? 'align-layer__name is-left' : 'align-layer__name'} style={nameOnLeft ? { right: `${ALIGN_RING_PX + 6}px`, top: 0 } : { left: `${ALIGN_RING_PX + 6}px`, top: 0 }}>
        {name}
      </span>
    )}
  </div>
);

/** The phone readout's foot, from the window's bottom (D30 item 2: anchored by its foot so it grows upward). */
const PHONE_READOUT_FOOT_PX = 148;

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
import { threeFigures, type FlightReadout } from './flightView.js';
import { evidenceCounts, firstSentenceOf, revealStatus } from './revealStatus.js';
import { useKeepClear } from './keepClear.js';
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
  /** Solve from here (D24 step 2): the person's alignment, sent as the solve's body, the pose and where it started. */
  onSolveFromHere?: (alignment: PersonAlignment, pose: AlignPose, from: AlignStart) => void;
  /** The alignment a solve was last sent from for this upload (D24 steps 4 and 5): kept after a refusal, named on a match. */
  alignedFrom?: AlignedFrom | null;
  /**
   * The sky the solve predicted from an early pose while it ran (the lock-on, D25), kept after the job ended: on the
   * laptop its marks travel to the solve's places on the first arrival once the check passed, and stay still and amber
   * at the early pose when it was refused.
   */
  lockOnSky?: ObservationSolution | null;
  /**
   * The rings a Solve from here was sent from, for that upload: its job's early marks (UIUX-lockon-READY.md item 1,
   * "Placed from your alignment"), which lock on in place of any earlier served sky when that solve matches (item 2).
   */
  lockOnAligned?: { sha256: string; marks: readonly PlacedTarget[] } | null;
  /** A clip's playing original on the stage: the parent of D34's one clock when its restoration is compared. */
  clipVideo?: HTMLVideoElement | null;
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
  /** Back to Moment pressed: on the laptop the readout leaves on words out while the flight flies back (the motion record). */
  returning?: boolean;
  /** Whether the frame's corners are drawn: a clip filling the width has none (INTERIM: UIUX-clip-QUESTION.md). */
  corners?: boolean;
  /** pilot-1f (D26 item 6): flying on one's own after "Fly on your own", with no star ahead. */
  freeFlight?: boolean;
  /** D30 item 6: on the phone the person steers (a drag on the sky took the camera; Autopilot in the ask bar gives it back). */
  phoneSteering?: boolean;
  /** On the phone, told when a drag on the sky takes the steering. */
  onPhoneSteer?: () => void;
  /** On the phone, told when the steering must end without Autopilot (your photo's view opened). */
  onEndPhoneSteer?: () => void;
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
  returning = false,
  corners = true,
  freeFlight = false,
  onEndFreeFlight,
  phoneSteering = false,
  onPhoneSteer,
  onEndPhoneSteer,
  phone = false,
  viewportWidthPx,
  viewportHeightPx,
  restoration,
  lockOnSky = null,
  lockOnAligned = null,
  clipVideo = null,
  onRestore,
  onCancelRestore,
  onAligningChange,
  cancelAligningToken = 0,
  onSolveFromHere,
  alignedFrom = null,
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
  // D30 item 5: on the phone the caret circle opens the whole disclosure as a sheet; leaving there closes it
  const [details, setDetails] = useState(false);
  useEffect(() => {
    if (!there) setDetails(false);
  }, [there]);
  // D30 item 1: the phone's sky band ends at the readout's top, which moves as the readout's lines grow; unmeasured, at
  // its foot
  const readoutRef = useRef<HTMLElement>(null);
  const [readoutTopPx, setReadoutTopPx] = useState<number | null>(null);
  // UIUX-tagphone-READY.md item B: an answer open in the phone's flight keeps the readout's tag row and first sentence,
  // and its own foot stands 16 px above that tag row; meanwhile the sky band and the craft keep the full readout's top
  const [answerFootPx, setAnswerFootPx] = useState<number | null>(null);
  useLayoutEffect(() => {
    const answerOverReadout = phone && Boolean(answer);
    const box = readoutRef.current?.getBoundingClientRect();
    const top = box && box.height > 0 ? box.top : null;
    if (!answerOverReadout && top !== readoutTopPx) setReadoutTopPx(top);
    const row = answerOverReadout ? readoutRef.current?.querySelector('.flight-readout__tagrow')?.getBoundingClientRect() : undefined;
    const foot = row && row.height > 0 ? viewH - row.top + 16 : null;
    if (foot !== answerFootPx) setAnswerFootPx(foot);
  });
  // pilot-1e: a press on the sky hands the flight's camera to the person and takes it back; leaving there, or the cone
  // view, ends it. While they steer, the readout's numbers are their camera's own
  const [steeringAsked, setSteeringAsked] = useState(false);
  useEffect(() => {
    if (!there || cone) setSteeringAsked(false);
  }, [there, cone]);
  const steering = there && !cone && (phone ? phoneSteering : steeringAsked || freeFlight);
  // Your photo's view ends the phone's steering, as it ends the laptop's (no Autopilot left offered where nothing steers)
  useEffect(() => {
    if (phone && cone && phoneSteering) onEndPhoneSteer?.();
  }, [phone, cone, phoneSteering]);
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
  const status = revealStatus(session);
  // Every ring and name drawn on the photo keeps clear of the controls (UIUX-keepclear-READY.md, its last section
  // decided 13:34): the reveal's marks here, the calibrated reveal's and Real sky's too, the picked star excepted; the
  // kept and aligning rings below. A matched mark left out under the column is no lock-on target, so its early mark
  // leaves on frame out (MOTION's first line)
  const keepClear = useKeepClear(!there);
  const marks = revealMarks(marked, viewW, viewH, { phone, deepSky, clear: there ? [] : keepClear }).filter((mark) => !there || mark.picked);
  // The order the marks arrive in: brightest first (the motion record)
  // A deep-sky mark is not a matched mark: it takes no place in their arrival and appears without movement (D23)
  const arrivingMarks = marks.filter((mark) => mark.deepSky === null || mark.picked);
  const arrival = markArrivalRanks(arrivingMarks);
  const counts = evidenceCounts(session.observationSolution);
  // The lock-on (D25, MOTION-READY-web.md section 6): on the laptop, the early sky of this upload, from the reveal's
  // first arrival; it travels only once the check passed, and the arrival lasts until its words are in
  const early = !phone && !there && frame !== null ? earlyPoseOf(lockOnSky, session.sourceAsset) : null;
  // The early marks the lock-on starts from: a Solve from here's alignment rings for this upload, else the served sky's
  const alignedEarly: EarlyPose | null =
    !phone && !there && frame !== null && lockOnAligned !== null && lockOnAligned.sha256 === session.sourceAsset?.sha256 ? { source: 'alignment', marks: [...lockOnAligned.marks] } : null;
  const lockOnEarly = alignedEarly ?? early;
  const [lockingAtStart] = useState(() => lockOnEarly !== null && status.kind === 'calibrated');
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), lockingAtStart ? LOCK_ON_ARRIVAL_MS : REVEAL_ARRIVAL_MS);
    return () => clearTimeout(timer);
  }, []);
  const lockOn = lockingAtStart && arriving && lockOnEarly && frame && !bareFrame && !earthOnly ? lockOnPlan(lockOnEarly, frame, arrivingMarks) : null;
  // The lock-on's early marks keep their source line at full in the status column, with no entrance, from the reveal's
  // first frame until the last of them has gone (UIUX-lockon-READY.md item 1, "no early marks are ever drawn without a
  // source line"; the 17:38 tag rule; AUDIT-C's tag sweep, break 1): the travelling ones in the frame-in and colour
  // times, the leaving ones in frame out; under reduced motion both dissolve where they are (motion.css, the lock-on)
  const lockOnStarted = lockOn !== null;
  const lockOnFromEndMs = lockOn === null ? 0 : lockOnMarksEndMs(lockOn, reducedMotion());
  const [lockOnFromDone, setLockOnFromDone] = useState(false);
  useEffect(() => {
    if (!lockOnStarted || lockOnFromEndMs === 0) return undefined;
    const timer = setTimeout(() => setLockOnFromDone(true), lockOnFromEndMs);
    return () => clearTimeout(timer);
  }, [lockOnStarted, lockOnFromEndMs]);
  // The restoration (D10), on the laptop over the photo itself: offered while idle on a still, its steps in the counts'
  // place while it works or once it stopped, the compare once done
  const overPhoto = !phone && !there && !skyShown;
  // A clip is restored too now (restore-7; D34 compares it), so Restore is offered on a photo or a clip
  const restoreOffered = overPhoto && onRestore !== undefined && restoration?.phase === 'idle' && session.sourceAsset !== undefined;
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
  // The alignment the last Solve from here was sent from, for this upload only
  const kept = alignedFrom && session.sourceAsset && alignedFrom.sha256 === session.sourceAsset.sha256 ? alignedFrom : null;
  const aligning = alignAllowed ? aligningState : null;
  const keptRefused = kept && kept.refusal === undefined && alignAllowed && !aligning ? kept : null;
  // C15 (UIUX-alignwords-READY.md, ruled 17:46): refused outright for an upload with no capture time or place, the
  // route's sentence in the refusal line's place, the marks gone, only Restore (it needs neither). No Try again
  const keptRefusal = kept?.refusal !== undefined && !phone && status.kind === 'unmatched' && !aligning && !compareShown ? kept.refusal : null;
  const alignOffered = alignAllowed && alignStart !== null && restoration?.phase !== 'working';
  // Refused after an early pose (D25 point 5): its marks stay over the no-match reveal
  const earlyKept = early !== null && frame !== null && status.kind === 'unmatched' && !aligning && !skyShown && !compareShown && !bareFrame;
  // Predicted rings drawn on the photo: the status column leads with the Predicted tag, and the column has no entrance,
  // so the tag is there from the rings' first frame (the lead's 17:38 rule; its four places decided 17:56,
  // WEB-UX-refused-tag-QUESTION.md). The laptop's column only: the phone's aligning and no-match screens are their own,
  // and no record places it there
  const ringsTagged = aligning !== null || earlyKept || keptRefused !== null;
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
  // D36: the photo's ready restorations, read once its restoration is done; the compare shows the newest, or the row
  // pressed until the compare is left; Download saves the one shown
  const doneVariant = restoration?.phase === 'done' ? restoration.job.variantId : undefined;
  const [versions, setVersions] = useState<StoredRestoration[]>([]);
  const [pickedVersion, setPickedVersion] = useState<string | null>(null);
  useEffect(() => {
    setVersions([]);
    if (doneVariant === undefined || !asset) return undefined;
    let live = true;
    readRestorations(asset.sha256)
      .then((list) => {
        if (live) setVersions(list);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [doneVariant, asset?.sha256]);
  useEffect(() => {
    if (!compareShown) setPickedVersion(null);
  }, [compareShown]);
  const shownVersionId = pickedVersion ?? versions[0]?.id ?? doneVariant;
  const shownVersion = versions.find((v) => v.id === shownVersionId) ?? null;
  const ready = compareShown && restoration?.phase === 'done' ? shownVersionId : undefined;
  const versionsList =
    ready !== undefined && versions.length >= 2 ? <RestoreVersions versions={versions} shownId={ready} onShow={setPickedVersion} /> : null;
  const downloadAction =
    ready !== undefined && asset ? (
      <>
        <MainAction label="Download" icon="download-simple" onClick={() => saveFromServer(variantExportUrl(asset.sha256, ready))} />
        {versionsList}
      </>
    ) : null;
  const uprightWidth = asset ? orientedSize(asset.width, asset.height, asset.displayOrientation).widthPx : 0;

  // A star let go: its card (seven lines) and its leader line stay while they leave, with the last picked star; on the
  // phone the sheet goes down in its own time (the motion record). Another star picked swaps the card at once.
  const leaveMotion = resolveMotionTokens(reducedMotion());
  const cardLeaveMs = phone ? Math.max(blockLeaveMs(STAR_CARD_LINES, leaveMotion), leaveMotion.sheetMs) : blockLeaveMs(STAR_CARD_LINES, leaveMotion);
  const [heldStar, setHeldStar] = useState<CelestialFinding | null>(picked);
  useEffect(() => {
    if (picked) setHeldStar(picked);
  }, [picked]);
  const cardStar = picked ?? heldStar;
  const card = useLeaving(!answer && !there && picked !== null, cardLeaveMs);
  const leaderMark = !phone && !bareFrame && !earthOnly && picked ? (marks.find((mark) => mark.picked) ?? null) : null;
  const leaderNow = leaderMark ? { d: leaderPath(leaderMark, viewW), predicted: leaderMark.kind === 'predicted' } : null;
  const [heldLeader, setHeldLeader] = useState(leaderNow);
  useEffect(() => {
    if (leaderNow) setHeldLeader(leaderNow);
  }, [leaderNow?.d, leaderNow?.predicted]);
  const leaderShape = leaderNow ?? heldLeader;
  const leader = useLeaving(leaderNow !== null, cardLeaveMs);
  // Original pixels pressed: the marks go on the words-out time (the motion record), then the photo is whole; a frame
  // the browser turned wrongly drops them at once (a correction, not a press)
  const marksLayer = useLeaving(!bareFrame, leaveMotion.wordsOutMs);
  const marksShown = marksLayer.mounted && !frameMismatch;
  // Released, they come back on the words-in time, all together, not one by one as they first arrived
  const [marksBack, setMarksBack] = useState(false);
  useEffect(() => {
    if (originalPixels) setMarksBack(true);
  }, [originalPixels]);
  const marksClass = marksLayer.leaving ? 'reveal-marks is-leaving' : marksBack ? 'reveal-marks is-back' : 'reveal-marks';
  // At Go there, In this photo leaves: its rows last first, then its title (the motion record)
  const legend = useLeaving(!phone && !there && counts.length > 0, linesLeaveMs(counts.length + 1, leaveMotion));
  // While the Real sky layer is still on screen leaving, its line stays with its Simulated tag (AGENTS.md 1.2): the
  // photo's line comes once the layer has gone. Original pixels takes the layer away at once, and the line with it.
  const lastSkyLine = useRef<React.ReactElement | null>(null);
  // Left at once from there (reduced motion), no Real sky line came before the fade: one is shown for it, its tag with it
  const skyLeaving = !skyShown && skyMounted && skyDrawn && !originalPixels && !earthOnly;
  const skyLineShown = skyOnScreen || skyLeaving;
  // The status line for this moment (each moment its own element, keyed: motion-14)
  const statusLine: React.ReactElement | null = phone && !skyOnScreen && status.kind === 'unmatched' && aligning ? (
        // Aligning at phone width (alp-align): the sentence at 654, the one line of hints at 700, "Solve from here"
        // centred at 772 at the approved phone size, 44 px (C30); the close circle cancels
        <div className="align-phone" data-testid="reveal-status" role="status">
          {/* UIUX-tagphone-READY.md item C: the person's rings carry the Predicted tag, at full from their first frame */}
          <span className="align-phone__tag">
            <EvidenceTag evidenceClass="predicted" />
          </span>
          <p className="align-phone__lead">Drag the stars onto the dots you see.</p>
          <p className="align-phone__help">Drag to move. Turn or pinch with two fingers.</p>
          {onSolveFromHere && (
            <div className="align-phone__solve">
              <MainAction label="Solve from here" size="phone" onClick={() => onSolveFromHere(personAlignmentOf(aligning.pose), aligning.pose, aligning.from)} />
            </div>
          )}
        </div>
      ) : phone && !skyOnScreen && status.kind === 'unmatched' && keptRefused ? (
        <PhoneAlignRefused
          onAgain={() => setAligning({ pose: keptRefused.pose, from: keptRefused.from })}
          onShowPredictedSky={status.predictedSky ? () => onShowPredictedSky?.() : undefined}
          onRestore={restoreOffered ? () => onRestore?.() : undefined}
        />
      ) : phone && !skyOnScreen && status.kind === 'unmatched' ? (
        <PhoneNoMatch status={status} onShowPredictedSky={() => onShowPredictedSky?.()} onAlign={alignOffered ? () => setAligning(alignStart) : undefined} />
      ) : phone && (!skyLineShown || there) ? null : there && (skyOnScreen || earthOnly || Boolean(answer)) ? (
        // The flight's column once its panorama has drawn, or in the cone view or while steering; and whenever an answer
        // hides the readout, so its Simulated tag stays in view (AUDIT-C's tag sweep, break 3, laptop half)
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
              {/* The Milky Way line names the panorama, so only once it has drawn */}
              {!steering && skyOnScreen && <p className="reveal-status__line">{FLIGHT_LINES.milkyWay}</p>}
              {/* While steering, D26 item 6's plain lines at 13 px, ink 66 percent */}
              {steering &&
                STEER_LINES.map((line) => (
                  <p key={line} className="reveal-status__line" data-testid="steer-line" style={{ fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>
                    {line}
                  </p>
                ))}
              {/* While a craft is drawn (outside the cone view), D26's own line names its models' source
                  (UIUX-tagphone-READY.md item F1); the phone keeps it in its details sheet */}
              {craft && <p className="reveal-status__line">{CRAFT_PICKER_FOOT}</p>}
            </>
          )}
        </div>
      ) : skyLeaving && lastSkyLine.current ? (
        lastSkyLine.current
      ) : skyOnScreen || skyLeaving ? (
        <div key="real-sky" className={phone ? 'reveal-status is-phone' : 'reveal-status'} data-testid="reveal-status" data-moment="real-sky" role="status">
          <EvidenceTag evidenceClass="simulated" />
          {/* A verified solve always carries its residual (the contract); without one no "within" is claimed and no sentence shows */}
          {residual !== null && (
            <p className="reveal-status__line">{`ESO's photograph of this part of the sky, placed by your photo's solve to within ${residual}′. Not light your camera recorded.`}</p>
          )}
        </div>
      ) : status.kind === 'unmatched' ? (
        <div key="unmatched" className={ringsTagged ? 'reveal-status is-unmatched has-predicted' : 'reveal-status is-unmatched'} data-testid="reveal-status" data-moment="unmatched" role="status">
          {ringsTagged && <EvidenceTag evidenceClass="predicted" />}
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
            // Aligning (D24): what to do, how, Solve from here and Cancel 10 px apart, then the live readout and where the
            // start came from
            <>
              <p className="reveal-status__lead align-lead">Drag the stars onto the dots you see.</p>
              <div className="align-help">
                <span>Drag to move</span>
                <span>Ctrl and drag to turn</span>
                <span>Scroll or pinch to scale</span>
              </div>
              <div className="align-acts">
                {onSolveFromHere && <MainAction label="Solve from here" onClick={() => onSolveFromHere(personAlignmentOf(aligning.pose), aligning.pose, aligning.from)} />}
                <SecondAction label="Cancel" onClick={() => setAligning(null)} />
              </div>
              <p className="align-readout" data-testid="align-readout">
                {alignReadout(aligning.pose)}
                <br />
                {alignStartWords(aligning.from)}
              </p>
            </>
          ) : keptRefusal !== null ? (
            <div className="align-refused">
              <p className="reveal-status__lead">{keptRefusal}</p>
              {restoreOffered && (
                <div className="align-refused__acts">
                  <SecondAction label="Restore" onClick={() => onRestore?.()} />
                </div>
              )}
            </div>
          ) : keptRefused ? (
            // Refused after Solve from here (D24 step 4): the alignment kept, drawn as predicted at its pose
            <div className="align-refused">
              <p className="reveal-status__lead">No stars matched near your alignment.</p>
              <p className="align-refused__kept">Your alignment is kept, drawn as predicted.</p>
              <div className="align-refused__acts">
                <MainAction label="Line it up again" onClick={() => setAligning({ pose: keptRefused.pose, from: keptRefused.from })} />
                {status.predictedSky && <SecondAction label="Show predicted sky" onClick={() => onShowPredictedSky?.()} />}
                {restoreOffered && <SecondAction label="Restore" onClick={() => onRestore?.()} />}
              </div>
            </div>
          ) : (
            <>
              {status.lines.map((line) => (
                <p key={line} className="reveal-status__lead">
                  {line}
                </p>
              ))}
              {/* D25 item 5 under the tag rule (UIUX-tagplaces-DECIDED-BY-LEAD.md place 4): the early marks' source line kept */}
              {earlyKept && <p className="reveal-status__from">{EARLY_SOURCE_WORDS[early!.source]}</p>}
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
      <div key="photo" className={status.kind === 'estimate' ? 'reveal-status has-predicted' : 'reveal-status'} data-testid="reveal-status" data-moment="photo" role="status">
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
            {/* Matched after Solve from here (D24 step 5): one more status line */}
            {kept && status.kind === 'calibrated' && <p className="reveal-status__line">Found near your alignment</p>}
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
      );
  // The line that just gave way to another moment's stays while its words go (the motion record): no longer a
  // status for a screen reader, and taking no input
  const [outgoing, setOutgoing] = useState<React.ReactElement | null>(null);
  const latestLine = useRef<React.ReactElement | null>(statusLine);
  const lineKey = statusLine?.key ?? null;
  useEffect(() => {
    const before = latestLine.current;
    if (!before || before.key === lineKey || !(before.props as { 'data-moment'?: string })['data-moment']) return undefined;
    setOutgoing(before);
    const timer = setTimeout(() => setOutgoing(null), leaveMotion.wordsOutMs);
    return () => clearTimeout(timer);
  }, [lineKey]);
  useEffect(() => {
    latestLine.current = statusLine;
    if (statusLine?.key === 'real-sky') lastSkyLine.current = statusLine;
    else if (!skyLeaving) lastSkyLine.current = null;
  });
  const outgoingLine = outgoing
    ? React.cloneElement(outgoing as React.ReactElement<Record<string, unknown>>, {
        key: `leaving-${outgoing.key}`,
        className: `${(outgoing.props as { className: string }).className} is-leaving`,
        role: undefined,
        'data-testid': 'reveal-status-leaving',
        inert: true,
      })
    : null;


  return (
    <div className={`${phone ? 'reveal-photo is-phone' : 'reveal-photo'}${lockOn ? ' is-locking' : ''}`} data-testid="reveal-photo" data-hud-interactive="true" data-arriving={arriving ? 'true' : undefined}>
      {skyMounted && !originalPixels && !earthOnly && <RealSkyLayer session={framed} rect={frame} viewportWidthPx={viewW} viewportHeightPx={viewH} leaving={!skyShown} onDrawn={setSkyDrawn} phone={phone} there={there} readoutTopPx={phone && there ? readoutTopPx : null} />}
      {there && !originalPixels && <FlightStarsLayer session={session} viewportWidthPx={viewW} viewportHeightPx={viewH} craft={craft} craftFraming={phone ? phoneCraftFraming(viewW, readoutTopPx ?? viewH - PHONE_READOUT_FOOT_PX) : undefined} cone={cone} onNoDistanceCount={setNoDistance} phone={phone} steering={steering} onPressSky={phone ? undefined : () => (freeFlight ? onEndFreeFlight?.() : setSteeringAsked((on) => !on))} onDragSky={phone && !steering ? onPhoneSteer : undefined} onSteer={setSteered} />}
      {rect && <PhotoCorners rect={rect} />}

      {leader.mounted && leaderShape && !earthOnly && (
        <svg className={`reveal-leader${leaderShape.predicted ? ' is-predicted' : ''}${leader.leaving ? ' is-leaving' : ''}`} data-testid="reveal-leader" aria-hidden="true" focusable="false">
          <path pathLength={1} d={leaderShape.d} />
        </svg>
      )}

      {/* The compare (D10) in the photo's own frame, over the original the stage draws, with no marks over it (D15) */}
      {compareShown && frame && restoration?.phase === 'done' && asset && (
        <RestoreCompare
          rect={frame}
          src={shownVersion && shownVersion.id !== restoration.job.variantId ? variantFileUrl(asset.sha256, shownVersion.id) : restoration.variantUrl}
          originalSrc={asset.uri}
          fade={SIDE_FADE}
          orientation={shownVersion && shownVersion.id !== restoration.job.variantId ? shownVersion.orientation : restoration.orientation}
          maxZoom={asset.mediaKind === 'video' ? 1 : uprightWidth / Math.max(1, frame.widthPx)}
          onZoomed={setCompareZoomed}
          clipParent={asset.mediaKind === 'video' ? clipVideo : undefined}
        />
      )}

      {aligning && frame && (
        <AlignLayer rect={frame} pose={aligning.pose} targets={alignTargetList} phone={phone} clear={keepClear} onPose={(pose) => setAligning((now) => (now ? { ...now, pose } : now))} />
      )}

      {/* Refused after Solve from here (D24 step 4): the rings stay amber and dashed at the kept pose */}
      {keptRefused && frame && <StillAlignMarks rect={frame} placed={placedAtPose(alignTargetList, keptRefused.pose)} clear={keepClear} />}
      {/* Refused after an early pose (D25 point 5): the early marks stay still and amber where the pose put them */}
      {earlyKept && <StillAlignMarks rect={frame!} placed={early!.marks} clear={keepClear} />}

      {/* The lock-on (D25 point 2): each early mark travels from its pose place to its solve place, together; those the
          solve does not place leave. Drawn as the early marks were, amber and dashed, taking no pointer */}
      {lockOn && lockOnFromEndMs > 0 && !lockOnFromDone && (
        <div className="lockon-from" data-testid="lockon-from">
          <p className="reveal-status__from">{EARLY_SOURCE_WORDS[lockOnEarly!.source]}</p>
        </div>
      )}
      {lockOn && (
        <div className="lockon" data-testid="lockon" aria-hidden="true">
          {lockOn.travelling.map((t) => (
            <LockOnMark key={t.id} className="align-layer is-still lockon-mark is-travelling" xPx={t.fromXPx} yPx={t.fromYPx} name={t.name} nameOnLeft={t.nameOnLeft} dxPx={t.toXPx - t.fromXPx} dyPx={t.toYPx - t.fromYPx} />
          ))}
          {lockOn.leaving.map((l) => (
            <LockOnMark key={l.id} className="align-layer is-still lockon-mark is-leaving" xPx={l.xPx} yPx={l.yPx} name={l.name} nameOnLeft={l.nameOnLeft} />
          ))}
        </div>
      )}

      {marksShown && !earthOnly && !(compareShown && compareZoomed) && (
      <div className={marksClass} data-testid="reveal-marks" inert={marksLayer.leaving} style={{ '--mark-n': arrivingMarks.length } as React.CSSProperties}>
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
            className={`${MARK_CLASS[mark.kind]}${lockOn?.lockedIds.has(mark.finding.id) ? ' is-locked-on' : ''}${mark.deepSky !== null && !mark.picked ? ' is-deepsky' : ''}${mark.picked ? ' is-picked' : ''}${mark.nameOnLeft ? ' is-left' : ''}`}
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
      {statusLine}
      {outgoingLine}

      {answer && <AnswerPanel key={`${answer.heard}|${answer.sentence}`} shown={answer} solution={session.observationSolution} onClose={() => onCloseAnswer?.()} phone={phone} footPx={answerFootPx} />}

      {(!answer || phone) && shownFlight && !cone && (
        <section
          ref={readoutRef}
          className={phone ? 'flight-readout is-phone' : returning ? 'flight-readout is-leaving' : 'flight-readout'}
          inert={returning && !phone}
          data-testid="flight-readout"
          aria-label={shownFlight.name}
          style={phone && details ? { visibility: 'hidden' } : undefined}
        >
          {phone ? <PhoneTagRow open={false} onToggle={() => setDetails(true)} /> : <EvidenceTag evidenceClass="simulated" />}
          {/* D26 item 7: while the person steers, the flight's own speed under the Simulated tag */}
          {steering && steered && !phone && (
            <p data-testid="steer-speed" style={{ margin: '10px 0 0', fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>
              {steeringSpeedLine(steered.speedPcPerS)}
            </p>
          )}
          {/* On the phone, where no status line shows, the readout keeps D27's first sentence in view and the rest one
              press away behind the circle (D30 items 2b and 5; AGENTS.md 1.2) */}
          {phone && (
            <p className="flight-readout__note">
              {FLIGHT_LINES.spec}
              {/* D30 item 6: while steering, its line under the first sentence, at ink 66 percent */}
              {steering && (
                <>
                  <br />
                  <span data-testid="steer-line" style={{ color: 'var(--look-ink-2)' }}>
                    {PHONE_STEER_LINE}
                  </span>
                </>
              )}
            </p>
          )}
          {/* Under an open answer the phone keeps only the tag row and the first sentence (tag place B) */}
          {!answer && (
            <>
              <h1>{shownFlight.name}</h1>
              <div className="flight-readout__stats">
                {shownFlight.toStarLy !== null && (
                  <div className="flight-readout__stat">
                    <b>
                      {threeFigures(shownFlight.toStarLy)}
                      <small>ly</small>
                    </b>
                    <span>{`to ${shownFlight.name}`}</span>
                  </div>
                )}
                <div className="flight-readout__stat">
                  <b>
                    {threeFigures(shownFlight.fromEarthLy)}
                    <small>ly</small>
                  </b>
                  <span>from Earth</span>
                </div>
              </div>
              {!freeFlight && craft && picked && placeFinding(picked).kind === 'at_distance' && (
                <PilotTrip craft={craft} starName={shownFlight.name} starDistancePc={(placeFinding(picked) as { distancePc: number }).distancePc} flightSeconds={flightSeconds} phone={phone} />
              )}
            </>
          )}
        </section>
      )}

      {there && (
        <FlightPills craftPill={<CraftPicker craft={craft} onPick={setCraft} phone={phone} />} cone={cone} onCone={() => setConeAsked((on) => !on)} phone={phone} />
      )}
      {phone && there && details && (
        <DetailsSheet
          lines={
            cone
              ? [CONE_LINES.spec, CONE_LINES.view, CONE_LINES.stars, CONE_LINES.rings, ...(noDistanceSentence(noDistance) ? [noDistanceSentence(noDistance)!] : [])]
              : [
                  FLIGHT_LINES.spec,
                  FLIGHT_LINES.stars,
                  FLIGHT_LINES.rings,
                  ...(noDistanceSentence(noDistance) ? [noDistanceSentence(noDistance)!] : []),
                  ...(steering ? [] : [FLIGHT_LINES.milkyWay]),
                  ...(craft && craftSpeedSource(craft) ? [craftSpeedSource(craft)!] : []),
                  ...(craft ? [CRAFT_PICKER_FOOT] : []),
                ]
          }
          onClose={() => setDetails(false)}
        />
      )}
      {!answer && cone && coneGeometry && <ConeReadout geometry={coneGeometry} phone={phone} hidden={phone && details} onDetails={() => setDetails(true)} />}

      {!answer && card.mounted && cardStar && (
        <StarCard
          key={cardStar.id}
          finding={cardStar}
          originalPixels={originalPixels}
          onToggleOriginalPixels={() => onToggleOriginalPixels?.()}
          onGoThere={() => onGoThere?.(cardStar.id)}
          onClose={() => onCloseCard?.()}
          phone={phone}
          deepSky={isDeepSky(cardStar) ? deepSkyRowOf(cardStar, deepSky) : null}
          leaving={card.leaving}
        />
      )}

      {phone && !answer && !picked && !skyShown && status.kind === 'estimate' && (
        // The estimate at phone width (UIUX-tagphone-READY.md item A): one block anchored by its foot 16 px above Ask out
        // loud, growing upward: the Predicted tag at full from the rings' first frame, the pose's first sentence (what the
        // sky rests on), then the counts row as built
        <div className="reveal-phone-estimate" data-testid="reveal-phone-estimate">
          <EvidenceTag evidenceClass="predicted" />
          <p className="reveal-phone-estimate__source">{firstSentenceOf(status.lines[0] ?? '')}</p>
          {counts.length > 0 && <PhoneCounts counts={counts} />}
        </div>
      )}
      {phone && !answer && !picked && !skyShown && status.kind === 'calibrated' && counts.length > 0 && <PhoneCounts counts={counts} />}

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

      {!phone && !restoring && legend.mounted && counts.length > 0 && (
        <div className={legend.leaving ? 'reveal-legend is-leaving' : 'reveal-legend'} data-testid="reveal-legend" style={{ '--legend-n': counts.length } as React.CSSProperties}>
          {/* A clip names its kind, as "your clip" does (INTERIM: UIUX-clip-QUESTION.md, a new word for the owner) */}
          <p className="reveal-legend__title">{session.sourceAsset?.mediaKind === 'video' ? 'In this clip' : 'In this photo'}</p>
          {counts.map((row, i) => (
            <p key={row.evidenceClass} className="reveal-legend__row" style={{ '--legend-i': i } as React.CSSProperties}>
              <span className="reveal-legend__count">{row.text}</span>
              <EvidenceTag evidenceClass={row.evidenceClass} />
            </p>
          ))}
        </div>
      )}
    </div>
  );
};
