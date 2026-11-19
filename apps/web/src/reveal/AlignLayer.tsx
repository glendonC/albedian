import React, { useEffect, useRef } from 'react';
import { dragPose, projectAltAz, scalePose, turnPose, type AlignPose, type AlignTarget } from './alignPose.js';

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

/** Each target inside the frame, and its name where it fits: right of the ring, else left, inside the frame, clear of names placed before it. */
export function alignMarks(targets: readonly AlignTarget[], pose: AlignPose, rect: FrameRect, fontPx: number): AlignMark[] {
  const r = ALIGN_RING_PX;
  const boxes: Array<{ x0: number; x1: number; y0: number; y1: number }> = [];
  const marks: AlignMark[] = [];
  for (const target of targets) {
    const at = projectAltAz(target.altDeg, target.azDeg, pose);
    if (!at || at.u < 0 || at.u > 1 || at.v < 0 || at.v > 1) continue;
    const x = rect.leftPx + at.u * rect.widthPx;
    const y = rect.topPx + at.v * rect.heightPx;
    const width = target.name.length * fontPx * 0.56;
    let mark: AlignMark = { id: target.finding.id, xPx: x, yPx: y, name: null, nameOnLeft: false };
    for (const left of [false, true]) {
      const b = { x0: left ? x - r - 6 - width : x + r + 4, x1: left ? x - r - 4 : x + r + 6 + width, y0: y - fontPx, y1: y + fontPx * 0.5 };
      if (b.x0 < rect.leftPx + 2 || b.x1 > rect.leftPx + rect.widthPx - 2) continue;
      if (boxes.some((q) => b.x0 < q.x1 && b.x1 > q.x0 && b.y0 < q.y1 && b.y1 > q.y0)) continue;
      boxes.push(b);
      mark = { ...mark, name: target.name, nameOnLeft: left };
      break;
    }
    marks.push(mark);
  }
  return marks;
}

/** A wheel step of this many pixels scales the field by e to this power: up enlarges, down shrinks. */
const WHEEL_SCALE_PER_PX = 0.0015;

export const AlignLayer: React.FC<{
  rect: FrameRect;
  pose: AlignPose;
  targets: readonly AlignTarget[];
  onPose: (pose: AlignPose) => void;
  phone?: boolean;
}> = ({ rect, pose, targets, onPose, phone = false }) => {
  const layer = useRef<HTMLDivElement>(null);
  const live = useRef(pose);
  live.current = pose;
  const report = useRef(onPose);
  report.current = onPose;
  const held = useRef<{ x: number; y: number; id: number } | null>(null);

  // The wheel is taken without the page scrolling (a passive listener cannot stop it)
  useEffect(() => {
    const el = layer.current;
    if (!el) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const next = scalePose(live.current, Math.exp(event.deltaY * WHEEL_SCALE_PER_PX));
      live.current = next;
      report.current(next);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const centre = { x: rect.leftPx + rect.widthPx / 2, y: rect.topPx + rect.heightPx / 2 };
  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    held.current = { x: event.clientX, y: event.clientY, id: event.pointerId };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const from = held.current;
    if (!from || from.id !== event.pointerId) return;
    let next: AlignPose;
    if (event.ctrlKey) {
      // The angle swept about the photo's centre
      const a0 = Math.atan2(from.y - centre.y, from.x - centre.x);
      const a1 = Math.atan2(event.clientY - centre.y, event.clientX - centre.x);
      next = turnPose(live.current, ((a1 - a0) * 180) / Math.PI);
    } else {
      next = dragPose(live.current, event.clientX - from.x, event.clientY - from.y, rect.widthPx);
    }
    held.current = { ...from, x: event.clientX, y: event.clientY };
    live.current = next;
    report.current(next);
  };
  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (held.current?.id === event.pointerId) held.current = null;
  };

  const marks = alignMarks(targets, pose, rect, phone ? 11 : 13);
  return (
    <div
      ref={layer}
      className="align-layer"
      data-testid="align-layer"
      style={{ left: `${rect.leftPx}px`, top: `${rect.topPx}px`, width: `${rect.widthPx}px`, height: `${rect.heightPx}px` }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <svg className="align-layer__rings" aria-hidden="true" focusable="false">
        {marks.map((mark) => (
          <circle key={mark.id} cx={mark.xPx - rect.leftPx} cy={mark.yPx - rect.topPx} r={ALIGN_RING_PX} />
        ))}
      </svg>
      {marks.map((mark) =>
        mark.name === null ? null : (
          <span
            key={`name:${mark.id}`}
            className={mark.nameOnLeft ? 'align-layer__name is-left' : 'align-layer__name'}
            style={
              mark.nameOnLeft
                ? { right: `${rect.leftPx + rect.widthPx - (mark.xPx - ALIGN_RING_PX - 6)}px`, top: `${mark.yPx - rect.topPx}px` }
                : { left: `${mark.xPx - rect.leftPx + ALIGN_RING_PX + 6}px`, top: `${mark.yPx - rect.topPx}px` }
            }
          >
            {mark.name}
          </span>
        )
      )}
    </div>
  );
};
