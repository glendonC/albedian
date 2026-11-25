import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { alignReadout, alignStartWords, alignTargets, personAlignmentOf, placedAtPose, startPose, type AlignedFrom, type AlignPose, type AlignStart } from './alignPose.js';
import { ALIGN_RING_PX, AlignLayer, StillAlignMarks } from './AlignLayer.js';
import { PhoneAligningBlock } from './PhoneAligning.js';
import { EARLY_SOURCE_WORDS, earlyPoseOf, type EarlyPose, type PlacedTarget } from './earlyPose.js';
import { lockOnPlan } from './lockOn.js';
import type { MediaVariant, ObservationSolution, PersonAlignment } from '@albedian/contracts';
import { answerFindings, DISCLOSURES, hasVerifiedWcs, orientedSize, type CelestialFinding, type ObservationSession } from '@albedian/contracts';
import { deepSkyRowOf, isDeepSky, useDeepSkyTable, type DeepSkySymbol } from './deepSky.js';
import { readRestorations, restoredSource, saveFromServer, variantExportUrl, type StoredRestoration } from '../services/apiClient.js';
import { drawableVersion, standsAlone, useFirstNeedCopies } from './firstNeedCopies.js';
import { getMarkerVisualSpec, MOTION_TOKENS, resolveMotionTokens } from '@albedian/design-system';
import { blockLeaveMs, linesLeaveMs, reducedMotion, useLeaving } from '../hooks/useLeaving.js';
import { EvidenceTag, LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import { SIDE_FADE } from '../components/SourcePhotoStage.js';
import { RestoreCompare, RestorePhoneFoot, RestorePhoneStopped, RestorePhoneWait, RestoreSteps, RestoreVersions, RestoreWhy } from './RestoreParts.js';
import type { Restoration } from './restoration.js';
import { sessionPhotoRect, useViewportSize } from '../components/sourcePhotoRect.js';
import { PhotoCorners } from './PhotoCorners.js';
import { leaderPath, markArrivalRanks, revealMarks, type RevealMark } from './revealMarks.js';
import { StarCard } from './StarCard.js';
import { AnswerPanel, type ShownAnswer } from './AnswerPanel.js';
import { RealSkyLayer, prepareRealSky } from './RealSkyLayer.js';
import { FlightStarsLayer } from '../spatial/flight/FlightStarsLayer.js';
import { arrivalShiftOf } from '../spatial/flight/arrival.js';
import { prepareFlight } from '../spatial/flight/prepareFlight.js';
import { FLIGHT_MILKY_WAY_LINE, flightSkyLineOf } from '../spatial/sky/flightMilkyWay.js';
import { CraftPicker, DetailsSheet, PhoneTagRow, PilotTrip } from '../spatial/flight/PilotPieces.js';
import { phoneCraftFraming } from '../spatial/flight/craftScene.js';
import { CRAFT_PICKER_FOOT, craftSpeedSource } from '../spatial/flight/pilotReadout.js';
import { FlightClock } from '../spatial/flight/pilotReadout.js';
import { craftFromSearch, type Craft } from '../spatial/flight/crafts.js';
import { noDistanceSentence } from '../spatial/flight/flightRings.js';
import { CONE_LINES, coneGeometryOf } from '../spatial/flight/coneView.js';
import { ConeReadout, FlightPills } from '../spatial/flight/ConePieces.js';
import { PHONE_STEER_LINE, STEER_LINES, steeringSpeedLine } from '../spatial/flight/steering.js';
import { readoutDistance } from '../spatial/flight/nearStar.js';
import { placeFinding } from '../spatial/flight/flightFrame.js';
import { phoneSlotIn, phoneSlotRect } from './phoneSlot.js';
import { PhoneAlignRefused, PhoneNoMatch } from './PhoneNoMatch.js';

/** How long Real sky takes to dissolve in or out: the motion record's view change (reveal.css .real-sky). */
export const REAL_SKY_FADE_MS = MOTION_TOKENS.viewMs;
/** The star card's lines, top to foot (motion.css staggers them): top, name, sub, numbers, source, actions, notes. */
const STAR_CARD_LINES = 7;
// How long the reveal is arriving: its marks' span, then the status, In this photo and Ask out loud, 20 ms apart (the
// motion record). While it is, the status waits for the marks; after, a view change brings it in at the words' delay.
/**
 * The mark the first hint stands beside (UIUX-first-use-wow-READY.md item two): the brightest named matched star; never a
 * predicted ring, an unnamed star or a deep-sky symbol.
 */
export function pressHintMarkOf(marks: readonly RevealMark[]): RevealMark | null {
  return (
    [...marks]
      .filter((mark) => mark.kind === 'catalog' && mark.name !== null && mark.deepSky === null)
      .sort((a, b) => (a.finding.catalogRef?.apparentMagnitude ?? 99) - (b.finding.catalogRef?.apparentMagnitude ?? 99))[0] ?? null
  );
}

/** The first-use hints, each kept on the device once used (UIUX-first-use-wow-READY.md: never back once used). */
const PRESS_HINT_KEY = 'albedian.hint.pressStar';
const DRAG_HINT_KEY = 'albedian.hint.dragLook';
function readHint(key: string): boolean {
  try {
    return typeof window !== 'undefined' && window.localStorage.getItem(key) === 'used';
  } catch {
    return false;
  }
}
function keepHint(key: string): void {
  try {
    window.localStorage.setItem(key, 'used');
  } catch {
    // A browser that keeps nothing shows the hint again next time; nothing else depends on it
  }
}

export const REVEAL_ARRIVAL_MS = MOTION_TOKENS.marksSpanMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs;
/** The arrival that locks on (D25): the travel, the matched marks' colour at rest, then the words as after the marks. */
export const LOCK_ON_ARRIVAL_MS = MOTION_TOKENS.frameInMs + MOTION_TOKENS.colourMs + MOTION_TOKENS.wordsInDelayMs + MOTION_TOKENS.wordsInMs + 2 * MOTION_TOKENS.staggerMs;

/** How near the status column may come to In this photo before the legend gives way (refusedwrap-1, decided 20:44 on 22 Nov). */
const LEGEND_CLEAR_PX = 24;
/**
 * Top of .reveal-card.is-phone in CSS px in a window this tall (reveal.css; phone-card-foot-one): the card stands 30 px
 * above the window's foot, 345 high, up to the room under the top bar at 118; 469 in 844. Marks below it sit behind the
 * open phone card.
 */
export function phoneCardTopPx(viewportHeightPx: number): number {
  return Math.max(118, viewportHeightPx - 30 - 345);
}

/**
 * Under the photo at phone width: the catalog matched count large with its tag, then the others with theirs; an answer
 * takes their place (ask, approved, iPhone; #p-answer). In the estimate's block (tag place A) the same row, in its flow;
 * there, with a star picked, it goes from view but keeps its room (item D), so the tag above it holds its place.
 */
const PhoneCounts: React.FC<{ counts: ReturnType<typeof evidenceCounts>; hidden?: boolean }> = ({ counts, hidden = false }) => (
  <div className="reveal-phone-counts" data-testid="reveal-legend" style={hidden ? { visibility: 'hidden' } : undefined}>
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

/** The part of a frame on screen inside the viewport (align-zoom-1's cut on the laptop). */
function clippedToView(frame: { leftPx: number; topPx: number; widthPx: number; heightPx: number }, viewW: number, viewH: number) {
  const leftPx = Math.max(0, frame.leftPx);
  const topPx = Math.max(0, frame.topPx);
  return {
    leftPx,
    topPx,
    widthPx: Math.max(0, Math.min(viewW, frame.leftPx + frame.widthPx) - leftPx),
    heightPx: Math.max(0, Math.min(viewH, frame.topPx + frame.heightPx) - topPx),
  };
}

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
  milkyWay: FLIGHT_MILKY_WAY_LINE,
} as const;
/** The caret's path, as the phone readout's caret circle draws it (Phosphor's caret-up, as PilotPieces.tsx's CARET_UP). */
const COLUMN_CARET_UP = 'M213.66,165.66a8,8,0,0,1-11.32,0L128,91.31,53.66,165.66a8,8,0,0,1-11.32-11.32l80-80a8,8,0,0,1,11.32,0l80,80A8,8,0,0,1,213.66,165.66Z';

/**
 * flight-column-one (UIUX-flight-READY.md's addition for the lead's ask stamped 01:02; DECIDED-BY-LEAD 01:05): the tag's
 * caret circle on the laptop's flight column, the approved icon-only circle as the phone readout's (D30 item 2). It
 * opens the column's other lines in place under the first sentence, so it says expanded, not a dialog. The column opens
 * downward, so the caret points down while closed and up while open (the phone's points up, toward its sheet).
 */
const ColumnCaret: React.FC<{ open: boolean; onToggle: () => void }> = ({ open, onToggle }) => (
  <button
    type="button"
    className={`look-icon${open ? ' is-on' : ''} reveal-status__caret`}
    data-testid="column-caret"
    aria-label={open ? 'Fewer details' : 'More details'}
    aria-expanded={open}
    onClick={onToggle}
  >
    <svg className="look-i" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false" style={open ? undefined : { transform: 'rotate(180deg)' }}>
      <path d={COLUMN_CARET_UP} />
    </svg>
  </button>
);

/** Real sky's sentence (reveal, approved): ESO's photograph placed through the solve, with the solve's residual. */
const realSkySentence = (residualArcmin: number) => `ESO's photograph of this part of the sky, placed by your photo's solve to within ${residualArcmin}′. Not light your camera recorded.`;
/** realsky-points-one (DECIDED-BY-LEAD 20:37): Real sky's sentence while it draws the glow with the catalog's stars. */
const realSkyPointsSentence = (residualArcmin: number) =>
  `ESO/S. Brunier's photograph with its own stars removed, and the Gaia and Hipparcos stars at their catalog places, placed by your photo's solve to within ${residualArcmin}′. Not light your camera recorded.`;

/** The card's caution for a target whose catalog row gives no distance (13:03), in the readout's numbers' place (20:05). */
const NO_DISTANCE_LINE = 'Its catalog row gives no distance, so none is shown.';
import { realSkyResidualArcmin } from './realSkyGrid.js';
import { threeFigures, type FlightReadout } from './flightView.js';
import { evidenceCounts, firstSentenceOf, revealStatus } from './revealStatus.js';
import { blockBoxOf, useKeepClear } from './keepClear.js';
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
  /** Told the restored version the compare shows, or null, so the session holds what is on screen (voice-restore-one). */
  onShownRestoration?: (variant: MediaVariant | null) => void;
  /**
   * Told the row pressed under Download, or null once it is let go on the recording, so the session's active variant
   * is the version pressed and the moment saved on leaving carries it (checkpoint-version-one).
   */
  onPickVersion?: (id: string | null) => void;
  /**
   * Told whenever the phone's restoration takes or frees the foot (its compare's Download, or its wait's Cancel), so
   * Ask out loud gives way to it.
   */
  onPhoneFootChange?: (taken: boolean) => void;
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

/** No restorations listed yet: one array, so the versions read from it keep their identity between renders. */
const NO_VERSIONS: readonly StoredRestoration[] = [];

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
  onShownRestoration,
  onPickVersion,
  onPhoneFootChange,
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
  const rect = bareFrame || there || !corners || (phone && !skyShown) || !frame ? null : phone ? phoneSlotRect(frame, viewW, viewH) : frame;
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
    if (!cone && !answerOverReadout && top !== readoutTopPx) setReadoutTopPx(top);
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
  // flight-column-one: the laptop's flight column opens closed on every new flight and stays as the person left it until
  // closed or until the flight ends; nothing in it leaves on a timer
  const [columnOpen, setColumnOpen] = useState(false);
  useEffect(() => {
    if (!there) setColumnOpen(false);
  }, [there]);
  // Your photo's view ends the phone's steering, as it ends the laptop's (no Autopilot left offered where nothing steers)
  useEffect(() => {
    if (phone && cone && phoneSteering) onEndPhoneSteer?.();
  }, [phone, cone, phoneSteering]);
  const [steered, setSteered] = useState<{ fromEarthPc: number; toStarPc: number; speedPcPerS: number } | null>(null);
  // What is only true from Earth (the panorama, the photo-frame marks) leaves in the cone view and while steering
  const earthOnly = cone || steering;
  // milkyway-flight-one: while steering the sky is the starless glow's dome, told by the star layer once it has drawn
  const [domeDrawn, setDomeDrawn] = useState(false);
  // ...and at a star the starless glow, told by the Real sky layer once it has drawn (AUDIT-D 18:18)
  const [glowDrawn, setGlowDrawn] = useState(false);
  const [glowFailed, setGlowFailed] = useState(false);
  // realsky-points-one: whether Real sky draws the glow with the catalog's stars, and whether the flight's own stars have drawn
  const [pointsShown, setPointsShown] = useState(false);
  const [flightStarsDrawn, setFlightStarsDrawn] = useState(false);
  // flight-sky-line-state-one (DECIDED-BY-LEAD 21:56): in flight, the line of whichever sky is drawn
  const flightSkyKind = flightSkyLineOf({ steering, glowDrawn, domeDrawn, photographDrawn: skyDrawn, glowFailed });
  const flightResidual = flightSkyKind === 'photograph' && session.observationSolution ? realSkyResidualArcmin(session.observationSolution) : null;
  const flightSkyLine = flightSkyKind === 'glow' ? FLIGHT_LINES.milkyWay : flightResidual !== null ? realSkySentence(flightResidual) : null;
  // Free flight has no star ahead, so the readout keeps "from Earth" only (D26 item 6)
  const shownFlight =
    flight && steering && steered
      ? { ...flight, toStarLy: freeFlight || flight.toStarLy === null ? null : steered.toStarPc * 3.261563777, fromEarthLy: steered.fromEarthPc * 3.261563777 }
      : flight;
  const coneGeometry = cone ? coneGeometryOf(session) : null;
  const skyOnScreen = skyShown && skyDrawn && !originalPixels;
  // Left at once from there (reduced motion), no Real sky line came before the fade: one is shown for it, its tag with it
  const skyLeaving = !skyShown && skyMounted && skyDrawn && !originalPixels && !earthOnly;
  const skyLineShown = skyOnScreen || skyLeaving;
  // Whether Real sky's layer is mounted, as its mount below reads it: fading in, drawn or fading out (its line's tag
  // lives exactly as long)
  const skyLayerOn = skyMounted && !originalPixels && !earthOnly;
  // An answer lights the first star it is about, as a picked star, in the picked star's place; none for a refusal
  const lit = answer ? (answer.answer ? (answerFindings(answer.answer)[0] ?? null) : null) : undefined;
  const marked = lit === undefined ? framed : { ...framed, selectedFindingId: lit };
  const picked = session.observationSolution?.findings.find((finding) => finding.id === marked.selectedFindingId) ?? null;
  // There, only the picked star's mark stays (fly, approved)
  // Deep-sky objects on a verified solve (D23): their OpenNGC rows read once from the shipped table
  const solution = session.observationSolution;
  const deepSkyWanted = solution?.status === 'calibrated' && hasVerifiedWcs(solution) && solution.findings.some(isDeepSky);
  const deepSky = useDeepSkyTable(deepSkyWanted);
  // realsky-upload-one: on a verified solve the sky pictures go up, and the flight's programs link, while the photo is at
  // rest, before Real sky or Go there asks for them
  // go-there-predicted-1: a pose estimate's flight to a predicted star takes the glow too, so it is prepared at rest
  const skyPrepared = (solution?.status === 'calibrated' && hasVerifiedWcs(solution)) || (solution?.status === 'approximate' && solution.poseEstimate !== undefined);
  // UIUX-predflight-READY.md item 4: the rings' line only where the photo matched stars (a predicted star's flight has none)
  const ringsMatched = solution?.findings.some((f) => f.evidenceClass === 'catalog_matched') ?? false;
  useEffect(() => {
    if (!skyPrepared) return;
    prepareRealSky();
    prepareFlight();
  }, [skyPrepared]);
  const status = revealStatus(session);
  // Every ring and name drawn on the photo keeps clear of the controls (UIUX-keepclear-READY.md, its last section
  // decided 13:34): the reveal's marks here, the calibrated reveal's and Real sky's too, the picked star excepted; the
  // kept and aligning rings below. A matched mark left out under the column is no lock-on target, so its early mark
  // leaves on frame out (MOTION's first line)
  const keepClear = useKeepClear(!there);
  // arrival-star-1 (look gap two): There, the view's move toward the star's resting place, which the picked star's mark,
  // its name and leader, and the panorama follow as the flight's stars do
  const arrivalMove = there && !originalPixels ? arrivalShiftOf(session, viewW, viewH, phone) : null;
  const marks = revealMarks(marked, viewW, viewH, { phone, deepSky, clear: there ? [] : keepClear, pickedShiftPx: arrivalMove?.nowPx ?? null }).filter((mark) => !there || mark.picked);
  // The order the marks arrive in: brightest first (the motion record)
  // A deep-sky mark is not a matched mark: it takes no place in their arrival and appears without movement (D23)
  const arrivingMarks = marks.filter((mark) => mark.deepSky === null || mark.picked);
  const arrival = markArrivalRanks(arrivingMarks);
  const counts = evidenceCounts(session.observationSolution);
  // The lock-on (D25, MOTION-READY-web.md section 6): on the laptop, the early sky of this upload, from the reveal's
  // first arrival; it travels only once the check passed, and the arrival lasts until its words are in
  const early = !phone && !there && frame !== null ? earlyPoseOf(lockOnSky, session.sourceAsset) : null;
  // The early marks the lock-on starts from: a Solve from here's alignment rings for this upload, else the served sky's;
  // the phone's too since its wait keeps them (MOTION-READY-web.md section six, DECIDED-BY-LEAD 24 Nov 12:58)
  const alignedEarly: EarlyPose | null =
    !there && frame !== null && lockOnAligned !== null && lockOnAligned.sha256 === session.sourceAsset?.sha256 ? { source: 'alignment', marks: [...lockOnAligned.marks] } : null;
  const lockOnEarly = alignedEarly ?? early;
  const [lockingAtStart] = useState(() => lockOnEarly !== null && status.kind === 'calibrated');
  const [arriving, setArriving] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setArriving(false), lockingAtStart ? LOCK_ON_ARRIVAL_MS : REVEAL_ARRIVAL_MS);
    return () => clearTimeout(timer);
  }, []);
  const lockOn = lockingAtStart && arriving && lockOnEarly && frame && !bareFrame && !earthOnly ? lockOnPlan(lockOnEarly, frame, arrivingMarks, phone ? 11 : 13) : null;
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
  // D10 item 5 and D34 at phone width (UIUX-clipphone-READY.md section 4): a photo or clip that already has a ready
  // restoration (or has just finished one) shows the compare in the slot, the fixed sentence, and Download at 772
  const asset = session.sourceAsset;
  const phoneClip = phone && asset?.mediaKind === 'video';
  const phoneMedia = phone && asset !== undefined;
  const estimateRef = useRef<HTMLDivElement>(null);
  const [clipAnswerFootPx, setClipAnswerFootPx] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!(phone && Boolean(answer) && !there && !skyShown)) {
      if (clipAnswerFootPx !== null) setClipAnswerFootPx(null);
      return;
    }
    const estBox = estimateRef.current?.getBoundingClientRect();
    const nextFoot =
      estBox && estBox.height > 0
        ? viewH - estBox.top + 16
        : phoneClip
          ? viewH - (phoneSlotIn(viewH).topPx + phoneSlotIn(viewH).heightPx)
          : null;
    if (nextFoot !== clipAnswerFootPx) setClipAnswerFootPx(nextFoot);
  });
  const [storedVersions, setStoredVersions] = useState<{ sha256: string; list: StoredRestoration[] } | null>(null);
  const listedVersions = asset && storedVersions !== null && storedVersions.sha256 === asset.sha256 ? storedVersions.list : NO_VERSIONS;
  // playback-firstneed: a listed restored clip with no copy asks for one at once, and is drawn once its copy is served
  const { versions, retry: retryCopy } = useFirstNeedCopies(asset?.sha256 ?? null, listedVersions);
  const phoneRestoring = phoneMedia && !there && !skyShown && !skyLeaving && restoration?.phase === 'working';
  const phoneRerunStopped = phoneMedia && !there && !skyShown && !skyLeaving && restoration?.phase === 'stopped';
  const phoneCompare =
    phoneMedia &&
    !there &&
    !skyShown &&
    !skyLeaving &&
    !originalPixels &&
    !bareFrame &&
    frame !== null &&
    !phoneRestoring &&
    !phoneRerunStopped &&
    (versions.length > 0 || restoration?.phase === 'done');
  const compareOn = compareShown || phoneCompare;
  // UIUX-restorephone-READY.md, ruled 15:38 and decided by the lead 15:43 on 21 Nov (D15's pictures): over the
  // compare, at every width, only the matched marks are drawn; predicted marks show, with their tag, wherever the
  // compare is not on screen, so no predicted layer stands without its tag
  const drawnMarks = compareOn ? marks.filter((mark) => mark.kind !== 'predicted') : marks;
  // first-use-hints-one, item two (UIUX-first-use-wow-READY.md, RULED 03:22): once the reveal has arrived, one hint
  // beside the brightest named matched star's ring; it leaves the moment a star is picked (pressed, or named by voice or
  // typing), never on a timer, and once used it is kept on the device so it never comes back
  const [pressHintUsed, setPressHintUsed] = useState(() => readHint(PRESS_HINT_KEY));
  useEffect(() => {
    if (picked === null || pressHintUsed) return;
    keepHint(PRESS_HINT_KEY);
    setPressHintUsed(true);
  }, [picked, pressHintUsed]);
  // first-use-hints-one, item four: when the flight's camera rests, "Drag to look around." at the readout's foot; it
  // leaves at the first drag (a pointer moved with a button held) or key that moves the view, kept used on the device
  const [dragHintUsed, setDragHintUsed] = useState(() => readHint(DRAG_HINT_KEY));
  const flightAtRest = there && phase === 'at_space' && !returning;
  useEffect(() => {
    if (!flightAtRest || dragHintUsed) return undefined;
    const used = () => {
      keepHint(DRAG_HINT_KEY);
      setDragHintUsed(true);
    };
    const onMove = (event: Event) => {
      if ((event as MouseEvent).buttons !== 0) used();
    };
    const onKey = (event: KeyboardEvent) => {
      // A key typed into a field moves no view (AUDIT-C's D2), as the flight's own key handlers skip typing targets
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (/^(Arrow(Up|Down|Left|Right)|[wasdWASD])$/.test(event.key)) used();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('keydown', onKey);
    };
  }, [flightAtRest, dragHintUsed]);
  const dragHintShown = flightAtRest && !dragHintUsed;
  const pressHintMark =
    pressHintUsed || arriving || there || skyShown || bareFrame || answer || picked !== null
      ? null
      : pressHintMarkOf(drawnMarks);
  // Line it up yourself (D24), on the laptop's no-match reveal and at phone width alike, over the photo or clip itself
  const alignStart = useMemo(() => startPose(session), [session]);
  const alignTargetList = useMemo(() => alignTargets(session.observationSolution), [session.observationSolution]);
  const [aligningState, setAligning] = useState<{ pose: AlignPose; from: AlignStart } | null>(null);
  const alignAllowed = !there && !skyShown && !compareOn && status.kind === 'unmatched' && frame !== null && !bareFrame;
  const kept = alignedFrom && session.sourceAsset && alignedFrom.sha256 === session.sourceAsset.sha256 ? alignedFrom : null;
  const aligning = alignAllowed ? aligningState : null;
  // align-zoom-1 (UIUX-alignzoom-READY.md): while aligning, the photo at its own pixels around this point of it (shares
  // of the whole frame), or the whole photo; drawn larger only, never kept in the session, gone when aligning ends
  const [alignZoomState, setAlignZoom] = useState<{ u: number; v: number } | null>(null);
  const alignZoom = aligning ? alignZoomState : null;
  const alignZoomShown = useRef(false);
  alignZoomShown.current = alignZoom !== null;
  useEffect(() => {
    if (!aligning) setAlignZoom(null);
  }, [aligning === null]);
  const keptRefused = kept && kept.refusal === undefined && alignAllowed && !aligning ? kept : null;
  // D38 (UIUX-restorephone-READY.md, UIUX-clipphone-READY.md section 4): on a matched phone photo or clip, Restore 16 px
  // under Ask out loud, reached by scrolling the whole screen under the fixed top bar; while it works, D2's phone row
  // and Cancel where Ask out loud sat. Not while a restoration is ready (the compare shows) or running
  const phoneRestoreOffered =
    phoneMedia && !phoneCompare && onRestore !== undefined && restoration?.phase === 'idle' && !there && !skyShown && !bareFrame && frame !== null && status.kind !== 'unmatched' && picked === null && !answer;
  // D38 item 5 (ruled 15:43): stopped, in D37's layout with D21's sentences; only Try again holds the foot (a refusal frees it)
  const phoneStopped = phoneRerunStopped && !aligning;
  // D38's no-match rule: on the phone's no-match screen and its refused alignment, Restore under the screen's own
  // actions, reached by their scroll (UIUX-nomatch-restore-READY.md, alp-refused, UIUX-clipphone-READY.md section 3)
  const phoneNoMatchRestore =
    phoneMedia && !phoneCompare && onRestore !== undefined && restoration?.phase === 'idle' && !there && !skyShown && !bareFrame && status.kind === 'unmatched';
  // The phone's foot taken by the restoration (its compare's Download, or its wait's Cancel): Ask out loud gives way
  const phoneFoot = phoneCompare || phoneRestoring || (phoneStopped && restoration?.phase === 'stopped' && restoration.canTryAgain);
  useEffect(() => {
    onPhoneFootChange?.(phoneFoot);
  }, [phoneFoot]);
  useEffect(() => () => onPhoneFootChange?.(false), []);
  // D36 item 5: two or more ready restorations are listed under the phone's Download, reached by scrolling too
  // D36 item 5, and with one ready restoration that has no copy, its row (UIUX-versionwait-READY.md, addition stamped 02:22)
  const phoneVersionsShown = phoneCompare && (versions.length >= 2 || (versions[0] !== undefined && standsAlone(versions[0]))) && picked === null && !answer;
  // The whole screen scrolls to Restore or to the list: the photo and Ask out loud move with it (as the no-match
  // screen's photo does)
  const phoneScroll = phoneRestoreOffered || phoneVersionsShown;
  const revealRoot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!phoneScroll) return undefined;
    if (revealRoot.current) revealRoot.current.scrollTop = 0;
    const doc = document.documentElement;
    doc.dataset.phoneScroll = '';
    doc.style.removeProperty('--phone-scroll');
    return () => {
      delete doc.dataset.phoneScroll;
      doc.style.removeProperty('--phone-scroll');
    };
  }, [phoneScroll, asset?.sha256]);
  const onRevealScroll = phoneScroll
    ? () => document.documentElement.style.setProperty('--phone-scroll', `${revealRoot.current?.scrollTop ?? 0}px`)
    : undefined;
  // While the compare is zoomed in, the marks step aside (D15)
  const [compareZoomed, setCompareZoomed] = useState(false);
  // The compare's labels are drawn here, after the marks (AUDIT-B's finding, the lead 04:27 on 22 Nov)
  const [compareLabelHost, setCompareLabelHost] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!compareShown) setCompareZoomed(false);
  }, [compareShown]);
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
      if (event.key !== 'Escape') return;
      // At the photo's own pixels, Esc returns to the whole photo first (align-zoom-1)
      if (alignZoomShown.current) setAlignZoom(null);
      else setAligning(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [aligning !== null]);
  // Download (D10, D22), 18 px under the fixed sentence on a ready restoration: the export route's one zip, handed to the
  // browser's own download
  // D36: the photo's or clip's ready restorations, read once its restoration is done; the compare shows the newest, or
  // the row pressed until the compare is left; Download saves the one shown
  const doneVariant = restoration?.phase === 'done' ? restoration.job.variantId : undefined;
  const [pickedVersionState, setPickedVersionState] = useState<{ sha256: string; id: string } | null>(null);
  // checkpoint-version-one (DECIDED-BY-LEAD 18:46 on 24 Nov): the row pressed is the session's active variant too, which
  // the moment saved on leaving carries and Back to Moment restores, so an active variant that is one of these versions
  // is the row pressed; a row pressed here shows at once
  const sessionPick = versions.some((v) => v.id === session.activeVariantId) ? session.activeVariantId : null;
  const pickedVersion = (asset && pickedVersionState !== null && pickedVersionState.sha256 === asset.sha256 ? pickedVersionState.id : null) ?? sessionPick;
  const tellPick = useRef(onPickVersion);
  tellPick.current = onPickVersion;
  const setPickedVersion = (id: string) => {
    setPickedVersionState(asset ? { sha256: asset.sha256, id } : null);
    tellPick.current?.(id);
  };
  useEffect(() => {
    setStoredVersions(null);
    // On the phone a photo's or clip's stored restorations are read at once (D10 item 5, D34)
    if ((doneVariant === undefined && !phoneMedia) || !asset) return undefined;
    const sha = asset.sha256;
    let live = true;
    readRestorations(sha)
      .then((list) => {
        if (live) setStoredVersions({ sha256: sha, list });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [doneVariant, phoneMedia, asset?.sha256]);
  // The compare left on the recording itself lets the row go, in the session too; left for the sky or 3D (skyShown holds
  // the flight too) it is kept there
  useEffect(() => {
    if (compareOn) return;
    setPickedVersionState(null);
    if (!skyShown && pickedVersion !== null) tellPick.current?.(null);
  }, [compareOn]);
  // A moment returned to names its own version (or none): the session's wins over a row pressed here before
  useEffect(() => {
    setPickedVersionState(null);
  }, [session.activeVariantId]);
  useEffect(() => {
    setPickedVersionState(null);
  }, [asset?.sha256]);
  // The version drawn, which takes the check: the row pressed once it can be drawn; while a pressed row's copy is made,
  // the one drawn before it; else the newest that can be drawn here. One with no copy served is never drawn (the 22:19
  // ruling; UIUX-versionwait-READY.md, its addition of 22:20, item 2)
  const lastDrawn = useRef<string | null>(null);
  const pickedListed = pickedVersion === null ? undefined : versions.find((v) => v.id === pickedVersion);
  const drawnVersion =
    pickedListed !== undefined && drawableVersion(pickedListed)
      ? pickedListed
      : ((pickedListed !== undefined ? versions.find((v) => v.id === lastDrawn.current && drawableVersion(v)) : undefined) ??
        versions.find(drawableVersion));
  if (drawnVersion !== undefined) lastDrawn.current = drawnVersion.id;
  const shownVersionId = drawnVersion?.id ?? pickedVersion ?? versions[0]?.id ?? doneVariant;
  const shownVersion = versions.find((v) => v.id === shownVersionId) ?? null;
  const drawable = shownVersion
    ? drawableVersion(shownVersion)
    : !(restoration?.phase === 'done' && (restoration.noCopy === true || restoration.firstNeed === true));
  // aria-pressed follows the press, and the drawn row when none was (item 6)
  const pressedVersionId = pickedVersion ?? shownVersionId ?? null;
  // voice-restore-one: the restored version on screen, for the session, so "make the sky clearer" finds what is shown.
  // voice-compare-one version two (AUDIT-C's FAIL): away from the recording (the flight, Real sky, the sky leaving) the
  // version last shown stays the session's, so voice there answers what the session holds; on the recording, only the
  // compare's version, or none
  const lastShownRestoration = useRef<MediaVariant | null>(null);
  if (compareOn) lastShownRestoration.current = shownVersion?.variant ?? null;
  if (lastShownRestoration.current !== null && lastShownRestoration.current.sourceSha256 !== asset?.sha256) lastShownRestoration.current = null;
  const awayFromRecording = there || realSky || skyLeaving;
  const shownRestoration = compareOn ? (shownVersion?.variant ?? null) : awayFromRecording ? lastShownRestoration.current : null;
  const tellShown = useRef(onShownRestoration);
  tellShown.current = onShownRestoration;
  useEffect(() => {
    tellShown.current?.(shownRestoration);
  }, [shownRestoration?.id ?? null]);
  const phoneCompareSrc =
    !drawable ? null : shownVersion && asset ? restoredSource(asset.sha256, shownVersion) : restoration?.phase === 'done' ? restoration.variantUrl : null;
  const phoneCompareOrientation =
    shownVersion ? shownVersion.orientation : restoration?.phase === 'done' ? restoration.orientation : undefined;
  const ready = compareShown && restoration?.phase === 'done' ? shownVersionId : undefined;
  const versionsList =
    ready !== undefined ? (
      <RestoreVersions versions={versions} shownId={ready} pressedId={pressedVersionId} onShow={setPickedVersion} onRetry={retryCopy} />
    ) : null;
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
  // Go there Phase A starts the card leaving at once, and at phone width the opaque sheet stays out over Real sky and its fade
  const card = useLeaving(
    !answer && !there && phase !== 'phase_a_photo_to_sky' && !(phone && (realSky || skyLeaving)) && picked !== null,
    cardLeaveMs,
  );
  const leaderMark = !phone && !bareFrame && !earthOnly && picked ? (drawnMarks.find((mark) => mark.picked) ?? null) : null;
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
  // The tag rule through the phone restoration (AUDIT-B's FAIL of d38-3; EXPORT-d38tag-QUESTION.md, recommended): while
  // D38's row and its button hold the foot and predicted marks stay drawn, item A's lead stands above the row: their
  // tag at full and, on an estimate, the pose's first sentence (UIUX-tagphone-READY.md item A). A picked star's card
  // sheet rises over it: the lead stays drawn until the sheet covers it, and the card's own tag stands in for it at full
  // (item D; the card's rules in motion.css)
  // tagalign-1 (FIELD's walk, DECIDED-BY-LEAD 21:08 on 22 Nov): over a refused line-up the kept alignment's rings stay
  // drawn through the restoration as their own layer, so the lead stands for them too, the alignment's own source line
  // in the pose sentence's place
  const keptRingsDrawn = keptRefused !== null && frame !== null;
  const phonePredictedRingsDrawn =
    (marksShown && !earthOnly && drawnMarks.some((mark) => mark.kind === 'predicted')) || keptRingsDrawn;
  const phoneCardOpen = phone && !answer && card.mounted && !card.leaving && cardStar !== null;
  const nonPredictedCardOpen =
    phone && !answer && card.mounted && cardStar !== null && cardStar.evidenceClass !== 'predicted';
  const cardAbovePredicted = nonPredictedCardOpen && phonePredictedRingsDrawn;
  const cardHadAbovePredicted = useRef(false);
  if (!card.mounted) {
    cardHadAbovePredicted.current = false;
  } else if (cardAbovePredicted) {
    cardHadAbovePredicted.current = true;
  }
  const phonePredictedLead = (
    <>
      <EvidenceTag evidenceClass="predicted" />
      {status.kind === 'estimate' ? (
        <p className="reveal-phone-estimate__source">{firstSentenceOf(status.lines[0] ?? '')}</p>
      ) : keptRingsDrawn ? (
        <p className="reveal-phone-estimate__source">{EARLY_SOURCE_WORDS.alignment}</p>
      ) : null}
    </>
  );
  const phoneAlignRefusedUnderAnswer =
    phone && !phoneCompare && !phoneRestoring && !phoneStopped && !skyOnScreen && Boolean(answer) && status.kind === 'unmatched' && keptRingsDrawn;
  const phoneStoppedUnderAnswer = phoneStopped && Boolean(answer) && restoration?.phase === 'stopped' && !restoration.canTryAgain;
  const phoneRestoreLead =
    (phoneRestoring || phoneStopped) && !nonPredictedCardOpen && (!answer || phoneStoppedUnderAnswer) && phonePredictedRingsDrawn
      ? phonePredictedLead
      : null;
  // Released, they come back on the words-in time, all together, not one by one as they first arrived
  const [marksBack, setMarksBack] = useState(false);
  useEffect(() => {
    if (originalPixels) setMarksBack(true);
  }, [originalPixels]);
  const marksClass = marksLayer.leaving ? 'reveal-marks is-leaving' : marksBack ? 'reveal-marks is-back' : 'reveal-marks';
  // At Go there, In this photo leaves: its rows last first, then its title (the motion record)
  const legend = useLeaving(!phone && !there && counts.length > 0, linesLeaveMs(counts.length + 1, leaveMotion));
  // refusedwrap-1 (DECIDED-BY-LEAD 20:44 on 22 Nov; UIUX records it beside UIUX-keepclear-READY.md): in a short laptop
  // window, while the status column's measured box comes within 24 px of In this photo, the legend is not drawn, and it
  // is back as soon as there is room, by the keep-clear rule's own precedence. Only while the column itself carries the
  // drawn rings' tag and source line (the refused alignment, or a refusal after an early pose) and every drawn mark is
  // predicted, so every drawn mark keeps its tag in view.
  const legendRef = useRef<HTMLDivElement>(null);
  const [legendGivesWay, setLegendGivesWay] = useState(false);
  const legendMayGiveWay = !phone && status.kind === 'unmatched' && (keptRefused !== null || earlyKept) && drawnMarks.every((mark) => mark.kind === 'predicted');
  useLayoutEffect(() => {
    const legendBox = legendRef.current?.getBoundingClientRect();
    const column = legendRef.current?.closest('[data-testid="reveal-photo"]')?.querySelector('[data-testid="reveal-status"]');
    const columnBox = column ? blockBoxOf(column)[0] : undefined;
    const near =
      legendMayGiveWay &&
      legendBox !== undefined &&
      legendBox.height > 0 &&
      columnBox !== undefined &&
      columnBox.y1 + LEGEND_CLEAR_PX > legendBox.top &&
      columnBox.x0 < legendBox.right &&
      columnBox.x1 > legendBox.left;
    if (near !== legendGivesWay) setLegendGivesWay(near);
  });
  // restorewhy-warns-1 (UIUX/built-main-76ae.md Drift 2; refusedwrap-1's precedence of 22 Nov 20:44): while a laptop
  // restoration's .restore-why comes within 24 px of the status column's warnings list, .reveal-status__warns (which
  // carries no EvidenceTag) gives way so the two never paint over each other, and returns once there is room.
  const warnsRef = useRef<HTMLUListElement>(null);
  const [warnsGivesWay, setWarnsGivesWay] = useState(false);
  useLayoutEffect(() => {
    const warnsBox = warnsRef.current?.getBoundingClientRect();
    const whyBox = restoring ? revealRoot.current?.querySelector('.restore-why')?.getBoundingClientRect() : undefined;
    const near =
      !phone &&
      restoring &&
      warnsBox !== undefined &&
      warnsBox.height > 0 &&
      whyBox !== undefined &&
      whyBox.height > 0 &&
      warnsBox.bottom + LEGEND_CLEAR_PX > whyBox.top &&
      warnsBox.left < whyBox.right &&
      warnsBox.right > whyBox.left;
    if (near !== warnsGivesWay) setWarnsGivesWay(near);
  });
  // While the Real sky layer is still on screen leaving, its line stays with its Simulated tag (AGENTS.md 1.2): the
  // photo's line comes once the layer has gone. Original pixels takes the layer away at once, and the line with it.
  const lastSkyLine = useRef<React.ReactElement | null>(null);
  // The status line for this moment (each moment its own element, keyed: motion-14)
  // The phone's restoration holds the foot: its compare's sentence and Download (D10 item 5), or its wait's row and Cancel (D38)
  const statusLine: React.ReactElement | null = phoneCompare || phoneRestoring || phoneStopped ? null : phone && !skyOnScreen && status.kind === 'unmatched' && aligning ? (
        // Aligning at phone width (alp-align): the sentence at 654, the one line of hints at 700, "Solve from here"
        // centred at 772 at the approved phone size, 44 px (C30); the close circle cancels
        // UIUX-tagphone-READY.md item C: the person's rings carry the Predicted tag, at full from their first frame
        <PhoneAligningBlock onSolve={onSolveFromHere ? () => onSolveFromHere(personAlignmentOf(aligning.pose), aligning.pose, aligning.from) : undefined} />
      ) : phone && !skyOnScreen && !answer && status.kind === 'unmatched' && keptRefused ? (
        <PhoneAlignRefused
          assetKey={asset?.sha256}
          clip={phoneClip}
          hideTag={cardAbovePredicted}
          inert={phoneCardOpen}
          onAgain={() => setAligning({ pose: keptRefused.pose, from: keptRefused.from })}
          onShowPredictedSky={status.predictedSky ? () => onShowPredictedSky?.() : undefined}
          onRestore={restoreOffered || phoneNoMatchRestore ? () => onRestore?.() : undefined}
        />
      ) : phone && !skyOnScreen && !answer && status.kind === 'unmatched' ? (
        <PhoneNoMatch
          assetKey={asset?.sha256}
          clip={phoneClip}
          inert={phoneCardOpen}
          status={status}
          onShowPredictedSky={() => onShowPredictedSky?.()}
          onAlign={alignOffered ? () => setAligning(alignStart) : undefined}
          onRestore={phoneNoMatchRestore ? () => onRestore?.() : undefined}
        />
      ) : phone && (!skyLineShown || there) ? null : there ? (
        // The flight's column from the flight's first frame (3D stars, rings and any craft draw before the panorama
        // finishes loading, and through a Back to Moment return); the Milky Way line waits for the glow, or the dome while steering, below
        <div key="there" className="reveal-status" data-testid="reveal-status" data-moment="there" role="status">
          <EvidenceTag evidenceClass="simulated" />
          {/* flight-column-one: the tag, its caret and D27's first sentence stand through the whole flight; every other line
              one press away, in place under the first sentence */}
          <ColumnCaret open={columnOpen} onToggle={() => setColumnOpen((open) => !open)} />
          {cone ? (
            <>
              <p className="reveal-status__line">{CONE_LINES.spec}</p>
              {columnOpen && (
                <>
                  <p className="reveal-status__line">{CONE_LINES.view}</p>
                  <p className="reveal-status__line">{CONE_LINES.stars}</p>
                  {ringsMatched && <p className="reveal-status__line">{CONE_LINES.rings}</p>}
                  {noDistanceSentence(noDistance) && <p className="reveal-status__line">{noDistanceSentence(noDistance)}</p>}
                </>
              )}
            </>
          ) : (
            <>
              <p className="reveal-status__line">{FLIGHT_LINES.spec}</p>
              {columnOpen && (
                <>
                  <p className="reveal-status__line">{FLIGHT_LINES.stars}</p>
                  {ringsMatched && <p className="reveal-status__line">{FLIGHT_LINES.rings}</p>}
                  {noDistanceSentence(noDistance) && <p className="reveal-status__line">{noDistanceSentence(noDistance)}</p>}
                  {/* The sky's line names what is drawn: the glow (the dome while steering), or the photograph until the glow comes */}
                  {flightSkyLine && <p className="reveal-status__line">{flightSkyLine}</p>}
                </>
              )}
              {/* While steering, D26 item 6's plain lines at 13 px, ink 66 percent */}
              {steering &&
                STEER_LINES.map((line) => (
                  <p key={line} className="reveal-status__line" data-testid="steer-line" style={{ fontSize: 13, color: 'rgba(244, 243, 239, 0.66)' }}>
                    {line}
                  </p>
                ))}
              {/* While a craft is drawn (outside the cone view), D26's own line names its models' source
                  (UIUX-tagphone-READY.md item F1); the phone keeps it in its details sheet */}
              {columnOpen && craft && <p className="reveal-status__line">{CRAFT_PICKER_FOOT}</p>}
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
            <p className="reveal-status__line">{pointsShown ? realSkyPointsSentence(residual) : realSkySentence(residual)}</p>
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
                {/* align-zoom-1 (UIUX-alignzoom-READY.md item 7, the lead's words) */}
                <span>Double-click to see the photo full size</span>
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
            <ul ref={warnsRef} className="reveal-status__warns" style={warnsGivesWay ? { visibility: 'hidden' } : undefined}>
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
    // motion-seams-one (AUDIT-D on flight-anim-one version two): a tag leaves on its layer's last frame, never after, so
    // Real sky's line plays no words out of its own once its layer has gone (faded out, or taken at once)
    if (before.key === 'real-sky' && !skyLayerOn) return undefined;
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
    <div
      ref={revealRoot}
      className={`${phone ? 'reveal-photo is-phone' : 'reveal-photo'}${phoneScroll ? ' is-scroll' : ''}${lockOn ? ' is-locking' : ''}`}
      data-testid="reveal-photo"
      data-hud-interactive="true"
      data-arriving={arriving ? 'true' : undefined}
      data-card-above-predicted={cardAbovePredicted || cardHadAbovePredicted.current ? 'true' : undefined}
      onScroll={onRevealScroll}
    >
      {/* The room to scroll to Restore, 16 px under Ask out loud (D38 item 1) */}
      {phoneRestoreOffered && <div className="reveal-phone-room" aria-hidden="true" />}
      {skyMounted && !originalPixels && !earthOnly && <RealSkyLayer session={framed} rect={frame} viewportWidthPx={viewW} viewportHeightPx={viewH} leaving={!skyShown} onDrawn={setSkyDrawn} onGlowDrawn={setGlowDrawn} onGlowFailed={setGlowFailed} onPointsShown={setPointsShown} flightStarsDrawn={there && flightStarsDrawn} phone={phone} there={there} readoutTopPx={phone && there ? readoutTopPx : null} shift={arrivalMove} />}
      {there && !originalPixels && <FlightStarsLayer session={session} viewportWidthPx={viewW} viewportHeightPx={viewH} craft={craft} craftFraming={phone ? phoneCraftFraming(viewW, readoutTopPx ?? viewH - PHONE_READOUT_FOOT_PX) : undefined} cone={cone} onNoDistanceCount={setNoDistance} phone={phone} steering={steering} onPressSky={phone ? undefined : () => (freeFlight ? onEndFreeFlight?.() : setSteeringAsked((on) => !on))} onDragSky={phone && !steering ? onPhoneSteer : undefined} onSteer={setSteered} onSkyDome={setDomeDrawn} onStarsDrawn={setFlightStarsDrawn} />}
      {rect && <PhotoCorners rect={rect} />}

      {leader.mounted && leaderShape && !earthOnly && (
        <svg className={`reveal-leader${leaderShape.predicted ? ' is-predicted' : ''}${leader.leaving ? ' is-leaving' : ''}`} data-testid="reveal-leader" aria-hidden="true" focusable="false">
          <path pathLength={1} d={leaderShape.d} />
        </svg>
      )}

      {/* The compare (D10) in the photo's own frame, over the original the stage draws, with no marks over it (D15) */}
      {compareShown && drawable && frame && restoration?.phase === 'done' && asset && (
        <RestoreCompare
          key={asset.sha256}
          rect={frame}
          src={shownVersion ? restoredSource(asset.sha256, shownVersion) : restoration.variantUrl}
          originalSrc={asset.uri}
          fade={SIDE_FADE}
          orientation={shownVersion && shownVersion.id !== restoration.job.variantId ? shownVersion.orientation : restoration.orientation}
          maxZoom={asset.mediaKind === 'video' ? 1 : uprightWidth / Math.max(1, frame.widthPx)}
          onZoomed={setCompareZoomed}
          clipParent={asset.mediaKind === 'video' ? clipVideo : undefined}
          labelHost={compareLabelHost}
        />
      )}

      {/* The phone's compare (D10 item 5, D34) in the slot, the pictures where the stage draws the photo or clip; no zoom
          is drawn for the phone, and only matched marks stay over it (D15) */}
      {phoneCompare && frame && asset && phoneCompareSrc !== null && (
        <RestoreCompare
          key={asset.sha256}
          rect={frame}
          slot={phoneSlotRect(frame, viewW, viewH)}
          src={phoneCompareSrc}
          originalSrc={asset.uri}
          orientation={phoneCompareOrientation}
          clipParent={asset.mediaKind === 'video' ? clipVideo : undefined}
          labelHost={compareLabelHost}
        />
      )}

      {aligning && frame && asset && (() => {
        const onPose = (pose: AlignPose) => setAligning((now) => (now ? { ...now, pose } : now));
        // The photo at its own pixels (1:1) when it is drawn smaller than that; a photo drawn at or above them has no zoom
        const fullScale = uprightWidth / Math.max(1, frame.widthPx);
        const onZoomAt = (xPx: number, yPx: number) => {
          if (alignZoom) setAlignZoom(null);
          else if (fullScale > 1) setAlignZoom({ u: (xPx - frame.leftPx) / frame.widthPx, v: (yPx - frame.topPx) / frame.heightPx });
        };
        // Inside the photo's own frame on screen (the phone's slot), the point at its centre, the frame always covered
        const box = phone ? phoneSlotRect(frame, viewW, viewH) : clippedToView(frame, viewW, viewH);
        const widthPx = frame.widthPx * fullScale;
        const heightPx = frame.heightPx * fullScale;
        const clamp = (at: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, at));
        const zoomed = alignZoom && {
          leftPx: clamp(box.leftPx + box.widthPx / 2 - alignZoom.u * widthPx, box.leftPx + box.widthPx - widthPx, box.leftPx),
          topPx: clamp(box.topPx + box.heightPx / 2 - alignZoom.v * heightPx, box.topPx + box.heightPx - heightPx, box.topPx),
          widthPx,
          heightPx,
        };
        const pictureStyle: React.CSSProperties = zoomed === null ? {} : {
          position: 'absolute',
          left: `${zoomed.leftPx}px`,
          top: `${zoomed.topPx}px`,
          width: `${zoomed.widthPx}px`,
          height: `${zoomed.heightPx}px`,
          maxWidth: 'none',
          display: 'block',
          // Turned by its own tag, as the stage draws it; no filter, no stretch: the pixels are only drawn larger
          imageOrientation: 'from-image',
        };
        // One wrapper and one layer for both views (the picture's place held by null), so the layer, and what its
        // pointers saw, lives across the toggle
        return (
          <div
            className="align-zoom"
            {...(zoomed
              ? {
                  'data-testid': 'align-zoom',
                  style: { clipPath: `inset(${box.topPx}px ${viewW - box.leftPx - box.widthPx}px ${viewH - box.topPx - box.heightPx}px ${box.leftPx}px)` },
                }
              : {})}
          >
            {zoomed === null ? null : asset.mediaKind === 'video' ? (
              <video
                src={asset.uri}
                muted
                playsInline
                preload="auto"
                aria-hidden="true"
                style={pictureStyle}
                onLoadedMetadata={(event) => {
                  event.currentTarget.currentTime = session.currentSourceTimeSec;
                }}
              />
            ) : (
              <img src={asset.uri} alt="" aria-hidden="true" draggable={false} style={pictureStyle} />
            )}
            <AlignLayer
              rect={zoomed ?? frame}
              wholeRect={zoomed ? frame : undefined}
              pose={aligning.pose}
              targets={alignTargetList}
              phone={phone}
              clear={keepClear}
              onPose={onPose}
              onZoomAt={onZoomAt}
            />
          </div>
        );
      })()}

      {/* Refused after Solve from here (D24 step 4): the rings stay amber and dashed at the kept pose */}
      {keptRefused && frame && <StillAlignMarks rect={frame} placed={placedAtPose(alignTargetList, keptRefused.pose)} clear={keepClear} handOver phone={phone} />}
      {/* Refused after an early pose (D25 point 5): the early marks stay still and amber where the pose put them */}
      {earlyKept && <StillAlignMarks rect={frame!} placed={early!.marks} clear={keepClear} />}

      {/* The lock-on (D25 point 2): each early mark travels from its pose place to its solve place, together; those the
          solve does not place leave. Drawn as the early marks were, amber and dashed, taking no pointer */}
      {lockOn && lockOnFromEndMs > 0 && !lockOnFromDone &&
        (phone ? (
          // The phone's lock-on (section six, DECIDED-BY-LEAD 24 Nov 12:58): the Predicted tag and the source line stand
          // in their wait places until the last lock-on mark has gone
          <div className="lockon-from is-phone" data-testid="lockon-from">
            <span className="work-phone-early__tag">
              <EvidenceTag evidenceClass="predicted" />
            </span>
            <p className="work-phone-early__from">{EARLY_SOURCE_WORDS[lockOnEarly!.source]}</p>
          </div>
        ) : (
          <div className="lockon-from" data-testid="lockon-from">
            <p className="reveal-status__from">{EARLY_SOURCE_WORDS[lockOnEarly!.source]}</p>
          </div>
        ))}
      {lockOn && (
        <div className="lockon" data-testid="lockon" aria-hidden="true">
          {lockOn.travelling.map((t) => (
            <LockOnMark key={t.id} className={phone ? 'align-layer is-still lockon-mark is-travelling is-phone' : 'align-layer is-still lockon-mark is-travelling'} xPx={t.fromXPx} yPx={t.fromYPx} name={t.name} nameOnLeft={t.nameOnLeft} dxPx={t.toXPx - t.fromXPx} dyPx={t.toYPx - t.fromYPx} />
          ))}
          {lockOn.leaving.map((l) => (
            <LockOnMark key={l.id} className={phone ? 'align-layer is-still lockon-mark is-leaving is-phone' : 'align-layer is-still lockon-mark is-leaving'} xPx={l.xPx} yPx={l.yPx} name={l.name} nameOnLeft={l.nameOnLeft} />
          ))}
        </div>
      )}

      {marksShown && !earthOnly && !(compareShown && compareZoomed) && (
      <div className={marksClass} data-testid="reveal-marks" inert={marksLayer.leaving} style={{ '--mark-n': arrivingMarks.length } as React.CSSProperties}>
        {drawnMarks.map((mark) =>
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
        {drawnMarks.map((mark) => {
          const coveredByPhoneCard = phoneCardOpen && mark.yPx > phoneCardTopPx(viewH);
          return (
            <button
              key={mark.finding.id}
              type="button"
              className={`${MARK_CLASS[mark.kind]}${lockOn?.lockedIds.has(mark.finding.id) ? ' is-locked-on' : ''}${mark.deepSky !== null && !mark.picked ? ' is-deepsky' : ''}${mark.picked ? ' is-picked' : ''}${mark.nameOnLeft ? ' is-left' : ''}`}
              data-testid={`reveal-mark-${mark.finding.id}`}
              data-evidence-class={mark.finding.evidenceClass}
              inert={coveredByPhoneCard}
              tabIndex={coveredByPhoneCard ? -1 : undefined}
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
          );
        })}
      </div>
      )}

      {pressHintMark && (
        <p
          className={pressHintMark.nameOnLeft ? 'reveal-hint is-left' : 'reveal-hint'}
          data-testid="reveal-hint"
          style={{ left: `${pressHintMark.xPx}px`, top: `${pressHintMark.yPx}px` }}
        >
          Press a star to open it.
        </p>
      )}

      {/* The compare's Original and Restored, above the marks and under the answer and the star card */}
      {compareOn && <div ref={setCompareLabelHost} />}

      {!phone && (
      <button type="button" className="reveal-word" data-testid="reveal-home" onClick={onHome}>
        <Logo sizePx={21} />
      </button>
      )}

      {/* There on the phone the Simulated tag rides in the readout (#p-there) */}
      {statusLine}
      {outgoingLine}

      {answer && <AnswerPanel key={`${answer.heard}|${answer.sentence}`} shown={answer} solution={session.observationSolution} onClose={() => onCloseAnswer?.()} phone={phone} footPx={answerFootPx ?? clipAnswerFootPx} />}

      {(!answer || phone) && shownFlight && !cone && (
        <section
          ref={readoutRef}
          className={phone ? (returning ? 'flight-readout is-phone is-returning' : 'flight-readout is-phone') : returning ? 'flight-readout is-leaving' : 'flight-readout'}
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
              {/* A number under a target's name is that target's or none (flight-readout-nodistance-one, ruled 20:09 on 24
                  Nov): with no distance, the recorded line in the numbers' place and the camera's own distance apart, in
                  the view's own word; free flight keeps from Earth only (D26 item 6) */}
              {!freeFlight && shownFlight.toStarLy === null ? (
                <>
                  <p className="flight-readout__nodistance">{NO_DISTANCE_LINE}</p>
                  <p className="flight-readout__view">{`Your view is ${threeFigures(shownFlight.fromEarthLy)} ly from Earth.`}</p>
                </>
              ) : (
              <div className="flight-readout__stats">
                {shownFlight.toStarLy !== null && (
                  <div className="flight-readout__stat">
                    {/* near-star-flight-one: astronomical units below a tenth of a light year (nearStar.ts readoutDistance) */}
                    <b>
                      {threeFigures(readoutDistance(shownFlight.toStarLy).value)}
                      <small>{readoutDistance(shownFlight.toStarLy).unit}</small>
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
              )}
              {!freeFlight && craft && picked && placeFinding(picked).kind === 'at_distance' && (
                <PilotTrip craft={craft} starName={shownFlight.name} starDistancePc={(placeFinding(picked) as { distancePc: number }).distancePc} flightSeconds={flightSeconds} phone={phone} />
              )}
            </>
          )}
          {dragHintShown && !answer && (
            <p className="flight-readout__hint" data-testid="drag-hint">
              Drag to look around.
            </p>
          )}
        </section>
      )}

      {/* motion-seams-one: at Back to Moment the views row leaves on words out at the press, taking no input as it goes (a
          host with no box of its own, so the row stands where it stood) */}
      {there && (
        <div className={returning ? 'flight-pills-host is-leaving' : 'flight-pills-host'} inert={returning} style={{ display: 'contents' }}>
          <FlightPills craftPill={<CraftPicker craft={craft} onPick={setCraft} phone={phone} />} cone={cone} onCone={() => setConeAsked((on) => !on)} phone={phone} />
        </div>
      )}
      {phone && there && details && (
        <DetailsSheet
          lines={
            cone
              ? [CONE_LINES.spec, CONE_LINES.view, CONE_LINES.stars, ...(ringsMatched ? [CONE_LINES.rings] : []), ...(noDistanceSentence(noDistance) ? [noDistanceSentence(noDistance)!] : [])]
              : [
                  FLIGHT_LINES.spec,
                  FLIGHT_LINES.stars,
                  ...(ringsMatched ? [FLIGHT_LINES.rings] : []),
                  ...(noDistanceSentence(noDistance) ? [noDistanceSentence(noDistance)!] : []),
                  ...(flightSkyLine ? [flightSkyLine] : []),
                  ...(craft && craftSpeedSource(craft) ? [craftSpeedSource(craft)!] : []),
                  ...(craft ? [CRAFT_PICKER_FOOT] : []),
                ]
          }
          onClose={() => setDetails(false)}
        />
      )}
      {(!answer || phone) && cone && coneGeometry && <ConeReadout ref={readoutRef} geometry={coneGeometry} phone={phone} answerOpen={Boolean(answer)} hidden={phone && details} onDetails={() => setDetails(true)} />}

      {phone && !nonPredictedCardOpen && !phoneCompare && !phoneRestoring && !phoneStopped && !skyShown && status.kind === 'estimate' && (
        // The estimate at phone width (UIUX-tagphone-READY.md items A and B; UIUX-clipphone-READY.md section 3): one
        // block anchored by its foot 16 px above Ask out loud (or at y 638 above D16's player on a clip), staying in
        // view under an open answer with its counts hidden: the Predicted tag at full from the rings' first frame, the
        // pose's first sentence, and on a photo without an answer the counts row. With a star picked the card rises (item D)
        <div
          ref={estimateRef}
          className={phoneClip ? 'reveal-phone-estimate is-clip' : 'reveal-phone-estimate'}
          data-testid="reveal-phone-estimate"
        >
          <EvidenceTag evidenceClass="predicted" />
          <p className="reveal-phone-estimate__source">{firstSentenceOf(status.lines[0] ?? '')}</p>
          {!answer && !phoneClip && counts.length > 0 && <PhoneCounts counts={counts} hidden={picked !== null} />}
        </div>
      )}
      {/* D38 at phone width: while the restoration works, its lead (item A's tag) over the step and Cancel; once it stops,
          the stop's own block. Drawn before the card, so a picked star's sheet rises over them, the lead's tag drawn
          until the sheet covers it (item D), as the sheet covers Ask out loud */}
      {(phoneAlignRefusedUnderAnswer || (phoneStoppedUnderAnswer && phoneRestoreLead !== null)) && (
        <div
          ref={estimateRef}
          className={phoneClip ? 'reveal-phone-estimate is-clip' : 'reveal-phone-estimate'}
          data-testid="reveal-phone-estimate"
        >
          {phoneAlignRefusedUnderAnswer ? phonePredictedLead : phoneRestoreLead}
        </div>
      )}
      {phoneRestoring && phoneRestoreLead !== null && (
        <div className={phoneClip ? 'restore-phone-lead is-clip' : 'restore-phone-lead'}>{phoneRestoreLead}</div>
      )}
      {phoneRestoring && restoration?.phase === 'working' && (
        <RestorePhoneWait job={restoration.job} onCancel={() => onCancelRestore?.()} clip={phoneClip} inert={phoneCardOpen} />
      )}
      {phoneStopped && !phoneStoppedUnderAnswer && restoration?.phase === 'stopped' && (
        <RestorePhoneStopped
          job={restoration.job}
          sentence={restoration.sentence}
          subline={restoration.subline}
          canTryAgain={restoration.canTryAgain}
          onTryAgain={() => onRestore?.()}
          lead={phoneRestoreLead}
          clip={phoneClip}
          inert={phoneCardOpen}
        />
      )}
      {/* After a refusal on a phone no-match photo or clip, the screen's own main action comes back at 772 (Show predicted
          sky, or Line it up again over a kept refused alignment: DECIDED-BY-LEAD 05:02, UIUX-clipphone-READY.md section 4) */}
      {(phoneAlignRefusedUnderAnswer || (phoneStopped && restoration?.phase === 'stopped' && !restoration.canTryAgain && status.kind === 'unmatched' && (keptRefused !== null || status.predictedSky))) && (
        <div className="restore-phone-go" inert={phoneCardOpen}>
          {keptRefused !== null ? (
            <MainAction
              label="Line it up again"
              size="phone"
              onClick={() => {
                if (answer) onCloseAnswer?.();
                setAligning({ pose: keptRefused.pose, from: keptRefused.from });
              }}
            />
          ) : (
            <MainAction label="Show predicted sky" size="phone" onClick={() => onShowPredictedSky?.()} />
          )}
        </div>
      )}

      {cardAbovePredicted && (
        <div className="reveal-phone-estimate is-above-card" data-testid="reveal-phone-estimate">
          {phonePredictedLead}
        </div>
      )}

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
          solution={session.observationSolution}
          leaving={card.leaving}
        />
      )}

      {/* The phone's compare: the fixed sentence in the counts' place and Download in Ask out loud's (D10 item 5, D34); a
          picked star's card covers the foot, as it covers Ask out loud */}
      {phoneCompare && !answer && !picked && asset && shownVersionId !== undefined && (
        <RestorePhoneFoot clip={phoneClip} onDownload={() => saveFromServer(variantExportUrl(asset.sha256, shownVersionId))} />
      )}
      {/* D36 item 5: the list under Download, reached by scrolling, its rows 44 px */}
      {phoneVersionsShown && shownVersion && (
        <div className="restore-phone-versions">
          <RestoreVersions versions={versions} shownId={shownVersion.id} pressedId={pressedVersionId} onShow={setPickedVersion} onRetry={retryCopy} />
        </div>
      )}

      {/* D38: Restore under Ask out loud, reached by scrolling (its wait and stop: before the card) */}
      {phoneRestoreOffered && (
        <div className="restore-phone-rest">
          <SecondAction label="Restore" size="phone" onClick={() => onRestore?.()} />
        </div>
      )}

      {phone && !phoneClip && !phoneCompare && !phoneRestoring && !phoneStopped && !answer && !picked && !cardAbovePredicted && !skyLineShown && status.kind === 'calibrated' && counts.length > 0 && <PhoneCounts counts={counts} />}

      {restoring && restoration !== undefined && (restoration.phase === 'working' || restoration.phase === 'stopped') && (() => {
        const why = (
          <RestoreWhy
            job={restoration.job}
            sentence={restoration.phase === 'stopped' ? restoration.sentence : null}
            subline={restoration.phase === 'stopped' ? restoration.subline : null}
            canTryAgain={restoration.phase === 'stopped' ? restoration.canTryAgain : true}
            working={restoration.phase === 'working'}
            onCancel={() => onCancelRestore?.()}
            onTryAgain={() => onRestore?.()}
          />
        );
        const steps = restoration.job && <RestoreSteps job={restoration.job} />;
        // Gap eight (DECIDED-BY-LEAD 13:09 on 24 Nov): while a clip's steps count together, Cancel stands 16 px above the
        // rows' top and rises with them as their bars are added, so no row runs under it; a still, and a stopped job,
        // keep Cancel's block at its own place (D10)
        return restoration.phase === 'working' && asset?.mediaKind === 'video' ? (
          <div className="restore-wait">
            {why}
            {steps}
          </div>
        ) : (
          <>
            {why}
            {steps}
          </>
        );
      })()}

      {!phone && !restoring && legend.mounted && counts.length > 0 && (
        <div
          ref={legendRef}
          className={legend.leaving ? 'reveal-legend is-leaving' : 'reveal-legend'}
          data-testid="reveal-legend"
          style={{ '--legend-n': counts.length, ...(legendGivesWay ? { visibility: 'hidden' } : {}) } as React.CSSProperties}
        >
          {/* A clip names its kind, as "your clip" does (INTERIM: UIUX-clip-QUESTION.md, a new word for the owner) */}
          {/* The word in its own box, which stands on its own line box over the photograph at rest (words-on-photo-one) */}
          <p className="reveal-legend__title">
            <span className="reveal-legend__word">{session.sourceAsset?.mediaKind === 'video' ? 'In this clip' : 'In this photo'}</span>
          </p>
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
