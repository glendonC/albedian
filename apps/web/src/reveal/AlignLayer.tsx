import React, { useEffect, useRef, useState } from 'react';
import { grabPose, pinchPose, projectAltAz, scalePose, skyAt, turnPose, type AlignPose, type AlignTarget } from './alignPose.js';
import type { PlacedTarget } from './earlyPose.js';
import { nameClearOf } from './keepClear.js';
import type { Box } from './predictedSkyMarks.js';

/**
 * The aligning layer (D24, UIUX-align-READY.md): over the photo's own frame, the predicted targets through the person's
 * pose as amber dashed rings r 6.5 with amber names where they fit (the design's rings(), .cache/uiux/align/build.mjs),
 * and the hands on it: drag moves, Ctrl and drag turns, the wheel or a trackpad pinch scales. Nothing it draws is
 * measured; no movement is added (MOTION has not stated one for these handles).
 */

export interface FrameRect {
  leftPx: number;
  topPx: number;
  widthPx: number;
  heightPx: number;
}

export interface AlignMark {
  id: string;
  /** The ring's centre on screen. */
  xPx: number;
  yPx: number;
  name: string | null;
  nameOnLeft: boolean;
}

export const ALIGN_RING_PX = 6.5;

/** Each target inside the frame through the pose, and its name where it fits (placeMarks). */
export function alignMarks(targets: readonly AlignTarget[], pose: AlignPose, rect: FrameRect, fontPx: number, clear: readonly Box[] = []): AlignMark[] {
  const placed = targets.flatMap((target) => {
    const at = projectAltAz(target.altDeg, target.azDeg, pose);
    return at ? [{ id: target.finding.id, name: target.name, u: at.u, v: at.v }] : [];
  });
  // While aligning, every ring stays: the person lines them up with the stars (WEB-UX-keptnames-QUESTION.md place 4)
  return placeMarks(placed, rect, fontPx, clear, true);
}

/**
 * Each place inside the frame (u and v shares of its width and height), in the order given, and its name where it fits:
 * right of the ring, else left, inside the frame, clear of names placed before it. By D33's rule (UIUX-predsky-READY.md,
 * ruled 13:10 on 19 Nov) a ring that lies under or touches a place kept clear (the status column, the Sound circle, the
 * foot's buttons) is not drawn while it is there, unless ringsUnderClear keeps it; a name is placed only where its box
 * clears them.
 */
export function placeMarks(placed: readonly PlacedTarget[], rect: FrameRect, fontPx: number, clear: readonly Box[] = [], ringsUnderClear = false): AlignMark[] {
  const r = ALIGN_RING_PX;
  const boxes: Box[] = [];
  const marks: AlignMark[] = [];
  const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;
  for (const target of placed) {
    if (target.u < 0 || target.u > 1 || target.v < 0 || target.v > 1) continue;
    const x = rect.leftPx + target.u * rect.widthPx;
    const y = rect.topPx + target.v * rect.heightPx;
    if (!ringsUnderClear && clear.some((b) => overlaps({ x0: x - r, x1: x + r, y0: y - r, y1: y + r }, b))) continue;
    // A target with no proper name keeps its ring, unnamed
    if (!target.name) {
      marks.push({ id: target.id, xPx: x, yPx: y, name: null, nameOnLeft: false });
      continue;
    }
    const width = target.name.length * fontPx * 0.56;
    let mark: AlignMark = { id: target.id, xPx: x, yPx: y, name: null, nameOnLeft: false };
    for (const left of [false, true]) {
      const b = { x0: left ? x - r - 6 - width : x + r + 4, x1: left ? x - r - 4 : x + r + 6 + width, y0: y - fontPx, y1: y + fontPx * 0.5 };
      if (b.x0 < rect.leftPx + 2 || b.x1 > rect.leftPx + rect.widthPx - 2) continue;
      if (boxes.some((q) => overlaps(b, q))) continue;
      if (clear.some((q) => overlaps(b, nameClearOf(q)))) continue;
      boxes.push(b);
      mark = { ...mark, name: target.name, nameOnLeft: left };
      break;
    }
    marks.push(mark);
  }
  return marks;
}

// The rings and names the aligning layer or the still marks last drew, so the next screen's still marks know which of
// them it hides at a Solve from here hand-over
let lastDrawn: { marks: readonly AlignMark[]; rect: FrameRect } | null = null;

/**
 * Of the marks drawn before, those standing where this screen would put them that this screen does not draw: at a
 * hand-over they leave (MOTION-READY-web.md section six, DECIDED-BY-LEAD 22 Nov 05:52). A mark drawn at another place
 * (another pose) is no hand-over, and one hidden on both screens is never drawn again.
 */
export function leavingMarks(before: readonly AlignMark[], placed: readonly PlacedTarget[], rect: FrameRect, now: readonly AlignMark[]): AlignMark[] {
  const shown = new Set(now.map((mark) => mark.id));
  return before.filter((mark) => {
    if (shown.has(mark.id)) return false;
    const at = placed.find((target) => target.id === mark.id);
    return at !== undefined && Math.abs(rect.leftPx + at.u * rect.widthPx - mark.xPx) < 0.5 && Math.abs(rect.topPx + at.v * rect.heightPx - mark.yPx) < 0.5;
  });
}

/**
 * The same rings and names, still: the lock-on's early marks (D25), drawn where an early pose puts the predicted sky,
 * with no hands on them. At a Solve from here hand-over (`handOver`: the working screen's alignment marks, the refused
 * screen's kept ones), a ring and its name the screen before drew that this one's keep-clear hides leave on frame out
 * where they stood, then are drawn no more; everything else stays put (section six, DECIDED-BY-LEAD 22 Nov 05:52; on the
 * phone too since 24 Nov 12:58, whose wait draws the rings at the 11 px phone size). `wordsLeaving`: drawn beside the
 * marks from the hand-over's first frame when a screen drew marks before this one, until it says its words have gone
 * (the phone's aligning block, leaving).
 */
export const StillAlignMarks: React.FC<{
  rect: FrameRect;
  placed: readonly PlacedTarget[];
  clear?: readonly Box[];
  handOver?: boolean;
  phone?: boolean;
  wordsLeaving?: (gone: () => void) => React.ReactNode;
}> = ({ rect, placed, clear = [], handOver = false, phone = false, wordsLeaving }) => {
  // What the screen before drew, read once as this one comes in
  const [before] = useState(() => (handOver ? lastDrawn : null));
  // The phone's working screen is handed the whole screen's fit, not the slot its photo is drawn in (y 118 to 638): at
  // a phone hand-over the marks stand in the frame the screen before drew them in
  const frame = phone && before !== null ? before.rect : rect;
  const marks = placeMarks(placed, frame, phone ? 11 : 13, clear);
  const [left, setLeft] = useState<ReadonlySet<string>>(() => new Set());
  const [wordsGone, setWordsGone] = useState(false);
  const gone = before === null ? [] : leavingMarks(before.marks, placed, frame, marks);
  const leaving = gone.filter((mark) => !left.has(mark.id));
  useEffect(() => {
    lastDrawn = { marks, rect: frame };
  });
  const style: React.CSSProperties = { left: `${frame.leftPx}px`, top: `${frame.topPx}px`, width: `${frame.widthPx}px`, height: `${frame.heightPx}px` };
  return (
    <>
      {before !== null && !wordsGone && wordsLeaving?.(() => setWordsGone(true))}
      <div className={phone ? 'align-layer is-still is-phone' : 'align-layer is-still'} data-testid="early-marks" style={style}>
        <MarksDrawing marks={marks} rect={frame} leaving={leaving} onLeft={(id) => setLeft((done) => new Set(done).add(id))} />
      </div>
    </>
  );
};

const MarksDrawing: React.FC<{ marks: readonly AlignMark[]; rect: FrameRect; leaving?: readonly AlignMark[]; onLeft?: (id: string) => void }> = ({
  marks,
  rect,
  leaving = [],
  onLeft,
}) => (
  <>
    <svg className="align-layer__rings" aria-hidden="true" focusable="false">
      {marks.map((mark) => (
        <circle key={mark.id} cx={mark.xPx - rect.leftPx} cy={mark.yPx - rect.topPx} r={ALIGN_RING_PX} />
      ))}
      {leaving.map((mark) => (
        <circle key={`leaving:${mark.id}`} className="is-leaving" cx={mark.xPx - rect.leftPx} cy={mark.yPx - rect.topPx} r={ALIGN_RING_PX} onAnimationEnd={() => onLeft?.(mark.id)} />
      ))}
    </svg>
    {[...marks.map((mark) => ({ mark, out: false })), ...leaving.map((mark) => ({ mark, out: true }))].map(({ mark, out }) => {
      if (mark.name === null) return null;
      const place: React.CSSProperties = mark.nameOnLeft
        ? { right: `${rect.leftPx + rect.widthPx - (mark.xPx - ALIGN_RING_PX - 6)}px`, top: `${mark.yPx - rect.topPx}px` }
        : { left: `${mark.xPx - rect.leftPx + ALIGN_RING_PX + 6}px`, top: `${mark.yPx - rect.topPx}px` };
      return (
        <span key={`${out ? 'leaving-name' : 'name'}:${mark.id}`} className={`${mark.nameOnLeft ? 'align-layer__name is-left' : 'align-layer__name'}${out ? ' is-leaving' : ''}`} style={place}>
          {mark.name}
        </span>
      );
    })}
  </>
);

/** A wheel step of this many pixels scales the field by e to this power: up enlarges, down shrinks. */
const WHEEL_SCALE_PER_PX = 0.0015;
/**
 * A wheel notch glides to its field (MOTION-READY-web.md section 6 A; k10, NASA's Eyes' zoom smoothing): what is left
 * of it, in the log of the field, keeps this share of itself each sixtieth of a second, time-scaled, so it settles in
 * about 0.22 s, then ends on the exact value. A trackpad's pinch and reduced motion step at once.
 */
const WHEEL_GLIDE_PER_60TH = 0.8;
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

/** A finger that lifts within this many pixels of where it came down tapped; it did not drag. */
const TAP_SLOP_PX = 10;
/** A second tap this soon after the first and this near it is a double-tap (align-zoom-1). */
const DOUBLE_TAP_MS = 300;
const DOUBLE_TAP_PX = 30;

export const AlignLayer: React.FC<{
  rect: FrameRect;
  pose: AlignPose;
  targets: readonly AlignTarget[];
  onPose: (pose: AlignPose) => void;
  phone?: boolean;
  /** Places on screen no name is set over (the rings all stay while aligning). */
  clear?: readonly Box[];
  /**
   * align-zoom-1 (UIUX-alignzoom-READY.md): a double-click, or a finger's double-tap, at this point on screen. The
   * browser's own double-tap zoom never has it (touch-action none, and the second tap's default is stopped).
   */
  onZoomAt?: (xPx: number, yPx: number) => void;
  /** The whole photo's frame while `rect` is drawn at the photo's own pixels: a hand-over reads the marks there. */
  wholeRect?: FrameRect;
}> = ({ rect, pose, targets, onPose, phone = false, clear = [], onZoomAt, wholeRect }) => {
  const layer = useRef<HTMLDivElement>(null);
  const live = useRef(pose);
  live.current = pose;
  const report = useRef(onPose);
  report.current = onPose;
  // Every pointer held on the layer: one drags; two (a phone's fingers) turn by the angle between them and scale by
  // their spread
  const held = useRef(new Map<number, { x: number; y: number }>());
  // The sky one pointer holds, from its first move until a second pointer, Ctrl or letting go: it comes back under the
  // pointer once a pose can put it there again (near the zenith none may)
  const holding = useRef<{ altDeg: number; azDeg: number } | null>(null);

  // A wheel notch's glide: the field it ends on, when it was last carried and its frame
  const glide = useRef<{ fovXDeg: number; last: number; frame: number } | null>(null);
  const stopGlide = () => {
    if (glide.current) cancelAnimationFrame(glide.current.frame);
    glide.current = null;
  };

  // The wheel is taken without the page scrolling (a passive listener cannot stop it)
  useEffect(() => {
    const el = layer.current;
    if (!el) return undefined;
    const carry = (now: number) => {
      const g = glide.current;
      if (!g) return;
      const keep = Math.pow(WHEEL_GLIDE_PER_60TH, (Math.max(0, now - g.last) / 1000) * 60);
      g.last = now;
      const left = (Math.log(live.current.fovXDeg) - Math.log(g.fovXDeg)) * keep;
      const done = Math.abs(left) < 1e-4;
      const next = { ...live.current, fovXDeg: done ? g.fovXDeg : g.fovXDeg * Math.exp(left) };
      live.current = next;
      report.current(next);
      if (done) glide.current = null;
      else g.frame = requestAnimationFrame(carry);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = Math.exp(event.deltaY * WHEEL_SCALE_PER_PX);
      // A trackpad's pinch comes as a wheel with Ctrl held: it is the hand, so it follows at once, as under reduced motion
      if (event.ctrlKey || (typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION).matches)) {
        stopGlide();
        const next = scalePose(live.current, factor);
        live.current = next;
        report.current(next);
        return;
      }
      const target = scalePose({ ...live.current, fovXDeg: glide.current?.fovXDeg ?? live.current.fovXDeg }, factor).fovXDeg;
      if (glide.current) glide.current.fovXDeg = target;
      else glide.current = { fovXDeg: target, last: performance.now(), frame: requestAnimationFrame(carry) };
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      el.removeEventListener('wheel', onWheel);
      stopGlide();
    };
  }, []);

  // A finger's taps: where and when the last one lifted, and whether the finger now down has moved past a tap
  const lastTap = useRef<{ x: number; y: number; at: number } | null>(null);
  const tapDown = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const byFinger = (event: React.PointerEvent) => event.pointerType === 'touch' || event.pointerType === 'pen';
  // Whether the last pointer down was a finger: Chrome follows a finger's double-tap with a dblclick of its own, which
  // the taps have already answered
  const fingerLast = useRef(false);
  const nearLastTap = (x: number, y: number) => {
    const last = lastTap.current;
    return last !== null && performance.now() - last.at <= DOUBLE_TAP_MS && Math.hypot(x - last.x, y - last.y) <= DOUBLE_TAP_PX;
  };

  const centre = { x: rect.leftPx + rect.widthPx / 2, y: rect.topPx + rect.heightPx / 2 };
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    held.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    holding.current = null;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    fingerLast.current = byFinger(event);
    if (byFinger(event) && held.current.size === 1) {
      tapDown.current = { x: event.clientX, y: event.clientY, moved: false };
      // The second tap of a double-tap is the app's, not the browser's
      if (nearLastTap(event.clientX, event.clientY)) event.preventDefault();
    } else {
      tapDown.current = null;
      lastTap.current = null;
    }
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const pointers = held.current;
    const from = pointers.get(event.pointerId);
    if (!from) return;
    const to = { x: event.clientX, y: event.clientY };
    const down = tapDown.current;
    if (down && Math.hypot(to.x - down.x, to.y - down.y) > TAP_SLOP_PX) down.moved = true;
    // A place on screen in the frame's own pixels, and as shares of it
    const inFrame = (p: { x: number; y: number }) => ({ x: p.x - rect.leftPx, y: p.y - rect.topPx });
    const share = (p: { x: number; y: number }) => ({ u: (p.x - rect.leftPx) / rect.widthPx, v: (p.y - rect.topPx) / rect.heightPx });
    let next: AlignPose;
    if (pointers.size >= 2) {
      // Two fingers: the other one stays where it was for this move; the sky turns and scales about the point between
      // them and follows it (MOTION-READY-web.md section 6 A)
      const other = [...pointers.entries()].find(([id]) => id !== event.pointerId)![1];
      // The fingers take the field from any wheel glide
      stopGlide();
      next = pinchPose(live.current, [inFrame(from), inFrame(other)], [inFrame(to), inFrame(other)], rect.widthPx, rect.heightPx);
      holding.current = null;
    } else if (event.ctrlKey) {
      // The angle swept about the photo's centre
      const a0 = Math.atan2(from.y - centre.y, from.x - centre.x);
      const a1 = Math.atan2(to.y - centre.y, to.x - centre.x);
      next = turnPose(live.current, ((a1 - a0) * 180) / Math.PI);
      holding.current = null;
    } else {
      // The sky under the pointer stays under it (MOTION-READY-web.md section 6 A)
      if (!holding.current) {
        const taken = share(from);
        holding.current = skyAt(taken.u, taken.v, live.current);
      }
      next = grabPose(live.current, holding.current, share(to));
    }
    pointers.set(event.pointerId, to);
    live.current = next;
    report.current(next);
  };
  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    held.current.delete(event.pointerId);
    holding.current = null;
    const down = tapDown.current;
    tapDown.current = null;
    if (!down || !byFinger(event) || event.type !== 'pointerup') return;
    if (down.moved || Math.hypot(event.clientX - down.x, event.clientY - down.y) > TAP_SLOP_PX) {
      lastTap.current = null;
      return;
    }
    if (nearLastTap(event.clientX, event.clientY)) {
      lastTap.current = null;
      onZoomAt?.(event.clientX, event.clientY);
    } else {
      lastTap.current = { x: event.clientX, y: event.clientY, at: performance.now() };
    }
  };

  const fontPx = phone ? 11 : 13;
  const marks = alignMarks(targets, pose, rect, fontPx, clear);
  useEffect(() => {
    // A Solve from here hand-over reads the marks where the whole photo's frame puts them, zoomed or not
    lastDrawn = wholeRect ? { marks: alignMarks(targets, pose, wholeRect, fontPx, clear), rect: wholeRect } : { marks, rect };
  });
  return (
    <div
      ref={layer}
      className={phone ? 'align-layer is-phone' : 'align-layer'}
      data-testid="align-layer"
      style={{ left: `${rect.leftPx}px`, top: `${rect.topPx}px`, width: `${rect.widthPx}px`, height: `${rect.heightPx}px` }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={(event) => {
        if (!fingerLast.current) onZoomAt?.(event.clientX, event.clientY);
      }}
      // On a Mac, Ctrl and click is the secondary click: the browser's menu would take the pointer and end the turn
      onContextMenu={(event) => event.preventDefault()}
    >
      <MarksDrawing marks={marks} rect={rect} />
    </div>
  );
};
