import React, { useEffect, useRef, useState } from 'react';
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
export const RestoreSteps: React.FC<{ job: UploadJob }> = ({ job }) => (
  <ol className="work-steps restore-steps" data-testid="restore-steps">
    {restorationRows(job).map((row) => (
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

/**
 * The compare (D10; CMP_CSS and the page's script in .cache/uiux/restore): in the photo's own frame, the restored
 * picture over the original (drawn beneath by the photo stage), cut at the line. The line starts at the right edge
 * and glides to the middle; a press moves it there with the same glide, a held drag follows with none; "Original"
 * sends it to the right edge, "Restored" to the left.
 */
export const RestoreCompare: React.FC<{ rect: PhotoRect; src: string; fade?: string; orientation?: DisplayOrientation }> = ({
  rect,
  src,
  fade,
  orientation = { quarterTurnsClockwise: 0, mirrored: false },
}) => {
  const box = useRef<HTMLDivElement>(null);
  const down = useRef(false);
  const [split, setSplit] = useState(100);
  const [drag, setDrag] = useState(false);
  useEffect(() => afterPaint(() => setSplit(50)), [src]);
  const at = (clientX: number) => {
    const r = box.current!.getBoundingClientRect();
    return Math.max(0, Math.min(100, (100 * (clientX - r.left)) / Math.max(1, r.width)));
  };
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
        down.current = true;
        setSplit(at(event.clientX));
      }}
      onPointerMove={(event) => {
        if (!down.current) return;
        setDrag(true);
        setSplit(at(event.clientX));
      }}
      onPointerUp={() => {
        down.current = false;
        setDrag(false);
      }}
      onPointerCancel={() => {
        down.current = false;
        setDrag(false);
      }}
    >
      {/* Cut on an upright box; inside it the variant, stored as the camera wrote it, is turned upright: mirrored
          first, then turned clockwise, as the contract's orientation reads (the verdict on unit-restore-2) */}
      <div className="restore-compare__pic">
        <img src={src} alt="" draggable={false} style={turnedPicture(rect, orientation)} />
      </div>
      <span className="restore-compare__line" aria-hidden="true" />
      <SecondAction part="restore-compare__label is-left" label="Original" onClick={() => setSplit(100)} />
      <SecondAction part="restore-compare__label is-right" label="Restored" onClick={() => setSplit(0)} />
    </div>
  );
};
