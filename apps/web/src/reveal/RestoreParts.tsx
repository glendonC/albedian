import React, { useEffect, useRef, useState } from 'react';
import { useElapsedRows } from './elapsedRows.js';
import type { DisplayOrientation, UploadJob } from '@albedian/contracts';
import { LookIcon, MainAction, SecondAction } from '../look/index.js';
import type { PhotoRect } from '../components/sourcePhotoRect.js';
import { restorationRows, type RestoreRow } from './restoration.js';

const ROW_CLASS: Readonly<Record<RestoreRow['state'], string>> = {
  done: 'work-step is-done',
  now: 'work-step is-now',
  next: 'work-step is-next',
  failed: 'work-step is-failed',
};
const barWidth = (fraction: number) => `${Number((fraction * 100).toFixed(3))}%`;

/** The restoration job's own steps, in the counts' place, drawn as the working screen draws a solve's (D10). */
export const RestoreSteps: React.FC<{ job: UploadJob }> = ({ job }) => {
  // C19 item 1: a step with no total shows the time passed
  const rows = useElapsedRows(restorationRows(job));
  return (
  <ol className="work-steps restore-steps" data-testid="restore-steps">
    {rows.map((row) => (
      <li key={row.stage} className={ROW_CLASS[row.state]} aria-current={row.state === 'now' ? 'step' : undefined}>
        <span className="work-step__mark" aria-hidden="true">
          {row.state === 'done' ? <LookIcon name="check" /> : row.state === 'failed' ? <LookIcon name="x" /> : <i className="work-step__dot" />}
        </span>
        <span className="work-step__word">{row.word}</span>
        <span className="work-step__value">{row.value}</span>
        {row.fraction !== null && (
          <span className="work-step__bar">
            <b style={{ width: barWidth(row.fraction) }} />
          </span>
        )}
      </li>
    ))}
  </ol>
  );
};

/**
 * Above the steps, where Try again sits on a failed job: "Cancel" while the job works, held pressed and greyed from
 * the press (or the record's cancel time) until it stops; once stopped, why in its own words with "Try again" (D2's
 * canceled and interrupted, INTERIM).
 */
export const RestoreWhy: React.FC<{
  job: UploadJob | null;
  sentence: string | null;
  /** Under a refusal: the photo unchanged (UIUX-restore-stop-QUESTION.md). */
  subline?: string | null;
  /** False for a refusal: the same photo would be refused the same way. */
  canTryAgain?: boolean;
  working: boolean;
  onCancel: () => Promise<unknown> | void;
  onTryAgain: () => void;
}> = ({ job, sentence, subline = null, canTryAgain = true, working, onCancel, onTryAgain }) => {
  const [asked, setAsked] = useState(false);
  useEffect(() => setAsked(false), [job?.id, working]);
  const held = asked || job?.cancelRequestedAtUTC !== undefined;
  const cancel = () => {
    if (held) return;
    setAsked(true);
    try {
      const answer = onCancel();
      if (answer && typeof (answer as Promise<unknown>).then === 'function') (answer as Promise<unknown>).catch(() => setAsked(false));
    } catch {
      setAsked(false);
    }
  };
  return (
    <div className={!working && !canTryAgain ? 'work-why restore-why is-refused' : 'work-why restore-why'} role={working ? undefined : 'alert'}>
      {!working && sentence !== null && <p>{sentence}</p>}
      {!working && subline !== null && <p className="restore-why__sub">{subline}</p>}
      {(working || canTryAgain) && (
        <div className="work-acts">
          {working ? (
            <SecondAction part={held ? 'work-cancel is-asked' : 'work-cancel'} label="Cancel" aria-disabled={held ? true : undefined} onClick={cancel} />
          ) : (
            <MainAction label="Try again" icon="arrow-clockwise" onClick={onTryAgain} />
          )}
        </div>
      )}
    </div>
  );
};

/** The stored picture's box and turn, so that once turned it fills the upright frame. */
function turnedPicture(rect: PhotoRect, orientation: DisplayOrientation): React.CSSProperties {
  const w = rect.widthPx;
  const h = rect.heightPx;
  const quarter = orientation.quarterTurnsClockwise % 2 === 1;
  const turn = orientation.quarterTurnsClockwise === 0 && !orientation.mirrored
    ? undefined
    : `rotate(${orientation.quarterTurnsClockwise * 90}deg)${orientation.mirrored ? ' scaleX(-1)' : ''}`;
  return quarter
    ? { width: `${h}px`, height: `${w}px`, left: `${(w - h) / 2}px`, top: `${(h - w) / 2}px`, transform: turn }
    : { width: `${w}px`, height: `${h}px`, left: '0px', top: '0px', transform: turn };
}

/** The next frame after the one painted, so the line is first drawn at the right edge and then glides. */
function afterPaint(callback: () => void): () => void {
  let cancelled = false;
  const run = () => !cancelled && callback();
  if (typeof window.requestAnimationFrame === 'function') {
    const outer = window.requestAnimationFrame(() => {
      inner = window.requestAnimationFrame(run);
    });
    let inner = 0;
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(outer);
      window.cancelAnimationFrame(inner);
    };
  }
  const timer = setTimeout(run, 16);
  return () => {
    cancelled = true;
    clearTimeout(timer);
  };
}

/** How far a wheel step zooms: one notch of a mouse (100) is about a sixth. */
const WHEEL_ZOOM = 0.0015;
/** A press that moves less than this is a press, not a drag. */
const PRESS_SLOP_PX = 3;

type Zoom = { z: number; tx: number; ty: number };
const WHOLE: Zoom = { z: 1, tx: 0, ty: 0 };

/**
 * The compare (D10; CMP_CSS and the page's script in .cache/uiux/restore): in the photo's own frame, the restored
 * picture over the original, cut at the line. The line starts at the right edge and glides to the middle; a press
 * moves it there with the same glide, a held drag follows with none; "Original" sends it to the right edge,
 * "Restored" to the left. D15 (decided by the lead): the wheel, or a pinch (which a trackpad sends as a wheel), zooms
 * both pictures together about the pointer, up to the photo's real pixels; a drag then moves both and a press still
 * moves the line; Esc, or a second press on the current label, returns to the whole photo. Zooming has no movement of
 * its own (the MOTION record states none).
 */
export const RestoreCompare: React.FC<{
  rect: PhotoRect;
  src: string;
  /** The original, drawn inside the compare so it zooms with the restoration. */
  originalSrc: string;
  fade?: string;
  orientation?: DisplayOrientation;
  /** The zoom that shows the photo's real pixels; never less than whole. */
  maxZoom?: number;
  /** Told whether the compare is zoomed in, so the marks can step aside. */
  onZoomed?: (zoomed: boolean) => void;
}> = ({ rect, src, originalSrc, fade, orientation = { quarterTurnsClockwise: 0, mirrored: false }, maxZoom = 1, onZoomed }) => {
  const box = useRef<HTMLDivElement>(null);
  const down = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const [split, setSplit] = useState(100);
  const [drag, setDrag] = useState(false);
  const [zoom, setZoom] = useState<Zoom>(WHOLE);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const zoomed = zoom.z > 1;
  useEffect(() => afterPaint(() => setSplit(50)), [src]);
  useEffect(() => onZoomed?.(zoomed), [zoomed, onZoomed]);

  // Points in the frame's own pixels, whatever size it is drawn at
  const local = (clientX: number, clientY: number) => {
    const r = box.current!.getBoundingClientRect();
    return {
      x: ((clientX - r.left) * rect.widthPx) / Math.max(1, r.width),
      y: ((clientY - r.top) * rect.heightPx) / Math.max(1, r.height),
      fraction: Math.max(0, Math.min(100, (100 * (clientX - r.left)) / Math.max(1, r.width))),
    };
  };
  const clamped = (next: Zoom): Zoom => ({
    z: next.z,
    tx: Math.min(0, Math.max(rect.widthPx * (1 - next.z), next.tx)),
    ty: Math.min(0, Math.max(rect.heightPx * (1 - next.z), next.ty)),
  });
  const limit = Math.max(1, maxZoom);

  // The wheel needs a listener that may cancel the page's own scroll
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const at = local(event.clientX, event.clientY);
      const current = zoomRef.current;
      const z = Math.max(1, Math.min(limit, current.z * Math.exp(-event.deltaY * WHEEL_ZOOM)));
      // The point under the pointer stays under it
      const px = (at.x - current.tx) / current.z;
      const py = (at.y - current.ty) / current.z;
      const next = z === 1 ? WHOLE : clamped({ z, tx: at.x - px * z, ty: at.y - py * z });
      // Held at once, so a burst of wheel steps in one frame (a trackpad's) adds up
      zoomRef.current = next;
      setZoom(next);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  });

  // Esc returns to the whole photo while zoomed in, before anything else hears it
  useEffect(() => {
    if (!zoomed) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      setZoom(WHOLE);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [zoomed]);

  const label = (edge: number) => () => {
    if (split === edge) setZoom(WHOLE);
    else setSplit(edge);
  };
  const layer = { transform: `translate(${zoom.tx}px, ${zoom.ty}px) scale(${zoom.z})` };
  const style = {
    left: `${rect.leftPx}px`,
    top: `${rect.topPx}px`,
    width: `${rect.widthPx}px`,
    height: `${rect.heightPx}px`,
    '--split': `${split}%`,
    ...(fade ? { maskImage: fade, WebkitMaskImage: fade } : {}),
  } as React.CSSProperties;
  return (
    <div
      ref={box}
      className={drag ? 'restore-compare is-drag' : 'restore-compare'}
      data-testid="restore-compare"
      data-hud-interactive="true"
      style={style}
      onPointerDown={(event) => {
        if ((event.target as Element).closest('.restore-compare__label')) return;
        box.current?.setPointerCapture?.(event.pointerId);
        down.current = { x: event.clientX, y: event.clientY, moved: false };
        if (!zoomed) setSplit(local(event.clientX, event.clientY).fraction);
      }}
      onPointerMove={(event) => {
        const start = down.current;
        if (!start) return;
        if (zoomed) {
          if (!start.moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < PRESS_SLOP_PX) return;
          const from = local(start.x, start.y);
          const to = local(event.clientX, event.clientY);
          start.moved = true;
          start.x = event.clientX;
          start.y = event.clientY;
          setZoom((current) => clamped({ z: current.z, tx: current.tx + to.x - from.x, ty: current.ty + to.y - from.y }));
          return;
        }
        setDrag(true);
        setSplit(local(event.clientX, event.clientY).fraction);
      }}
      onPointerUp={(event) => {
        const start = down.current;
        down.current = null;
        setDrag(false);
        if (zoomed && start && !start.moved) setSplit(local(event.clientX, event.clientY).fraction);
      }}
      onPointerCancel={() => {
        down.current = null;
        setDrag(false);
      }}
    >
      <div className="restore-compare__original">
        <div className="restore-compare__layer" style={layer}>
          <img src={originalSrc} alt="" draggable={false} />
        </div>
      </div>
      {/* Cut on an upright box; inside it the variant, stored as the camera wrote it, is turned upright: mirrored
          first, then turned clockwise, as the contract's orientation reads (the verdict on unit-restore-2) */}
      <div className="restore-compare__pic">
        <div className="restore-compare__layer" style={layer}>
          <img src={src} alt="" draggable={false} style={turnedPicture(rect, orientation)} />
        </div>
      </div>
      <span className="restore-compare__line" aria-hidden="true" />
      <SecondAction part="restore-compare__label is-left" label="Original" onClick={label(100)} />
      <SecondAction part="restore-compare__label is-right" label="Restored" onClick={label(0)} />
    </div>
  );
};
