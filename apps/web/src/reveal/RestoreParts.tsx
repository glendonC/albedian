import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { elapsedWords, useElapsedRows } from './elapsedRows.js';
import { drawableVersion, standsAlone, type ListedVersion } from './firstNeedCopies.js';
import { followParent, onFrameAtParent } from './clipSync.js';
import { useCompareArrival } from '../audio/music/soundtrackMoment.js';
import { DISCLOSURES, type DisplayOrientation, type UploadJob } from '@albedian/contracts';
import { LookIcon, MainAction, SecondAction } from '../look/index.js';
import type { PhotoRect } from '../components/sourcePhotoRect.js';
import { restorationRows, type RestoreRow } from './restoration.js';
import { madeAtWords } from '../services/apiClient.js';
import { megabytes } from './arrival.js';

const ROW_CLASS: Readonly<Record<RestoreRow['state'], string>> = {
  done: 'work-step is-done',
  now: 'work-step is-now',
  next: 'work-step is-next',
  failed: 'work-step is-failed',
};
const barWidth = (fraction: number) => `${Number((fraction * 100).toFixed(3))}%`;

/** The logo's foot on the reveal: .reveal-word's top (30 px) and the logo's 21 px type [inference: the type's size]. */
export const LOGO_FOOT_PX = 30 + 21;
/** D10's labels sit 24 px inside the frame's top corners. */
const LABEL_INSET_PX = 24;

/**
 * C19 item 2 (UIUX-walkasks-READY.md, decided 12:59): where the labels would stand above 12 px under the logo's foot
 * (a narrow window, where the photo's frame reaches the logo), both move down together to there, still level and
 * inside the frame; elsewhere none (the labels keep their 24 px).
 */
export function compareLabelTop(frameTopPx: number): number | null {
  const under = LOGO_FOOT_PX + 12;
  return frameTopPx + LABEL_INSET_PX < under ? under - frameTopPx : null;
}

/** The restoration job's own steps, in the counts' place, drawn as the working screen draws a solve's (D10). */
export const RestoreSteps: React.FC<{ job: UploadJob }> = ({ job }) => {
  // C19 item 1: a step with no total shows the time passed
  const rows = useElapsedRows(restorationRows(job), job.id);
  return (
  <ol className="work-steps restore-steps" data-testid="restore-steps">
    {rows.map((row) => (
      <li key={row.stage} className={ROW_CLASS[row.state]} aria-current={row.state === 'now' ? 'step' : undefined}>
        <span className="work-step__mark" aria-hidden="true">
          {row.state === 'done' ? <LookIcon name="check" /> : row.state === 'failed' ? <LookIcon name="x" /> : <i className="work-step__dot" />}
        </span>
        <span className="work-step__word">{row.word}</span>
        {/* C19 item 1a: the seconds passed beside a count that has stood still for 10 s */}
        {row.since !== null && <span className="work-step__since">{row.since}</span>}
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

type ClipShown = { src: string; orientation: DisplayOrientation };

const sameOrientation = (a: DisplayOrientation, b: DisplayOrientation) =>
  a.quarterTurnsClockwise === b.quarterTurnsClockwise && a.mirrored === b.mirrored;

/**
 * One restored clip of D34's compare: muted, on the stage original's clock (clipSync.ts). On a version switch
 * (clipswap-1, DECIDED-BY-LEAD 12:40 on 24 Nov) the incoming copy is drawn transparent beside the outgoing one, decoding
 * on the same clock, and says once when it has a frame at the player's time.
 */
const RestoredClip: React.FC<{
  parent: HTMLVideoElement | null | undefined;
  src: string;
  style: React.CSSProperties;
  incoming?: boolean;
  onFrame?: () => void;
  /** Told once the clip has its first frame to draw (restored-disclosure-one). */
  onFirstFrame?: () => void;
}> = ({ parent, src, style, incoming = false, onFrame, onFirstFrame }) => {
  const own = useRef<HTMLVideoElement>(null);
  const frame = useRef(onFrame);
  frame.current = onFrame;
  const first = useRef(onFirstFrame);
  first.current = onFirstFrame;
  useEffect(() => {
    const child = own.current;
    if (!child) return undefined;
    const told = () => first.current?.();
    if (child.readyState >= 2) told();
    child.addEventListener('loadeddata', told);
    return () => child.removeEventListener('loadeddata', told);
  }, []);
  useEffect(() => {
    const child = own.current;
    if (!parent || !child) return undefined;
    const stopFollow = followParent(parent, child);
    const syncReady = () => {
      if (child.playbackRate !== parent.playbackRate) child.playbackRate = parent.playbackRate;
      if (child.loop !== parent.loop) child.loop = parent.loop;
      if (!child.seeking && Math.abs(child.currentTime - parent.currentTime) > 0) child.currentTime = parent.currentTime;
      if (!parent.paused && child.paused) void Promise.resolve(child.play()).catch(() => {});
    };
    syncReady();
    child.addEventListener('loadedmetadata', syncReady);
    return () => {
      child.removeEventListener('loadedmetadata', syncReady);
      stopFollow();
    };
  }, [parent]);
  useEffect(() => {
    const child = own.current;
    if (!incoming || !parent || !child) return undefined;
    return onFrameAtParent(parent, child, () => frame.current?.());
  }, [incoming, parent]);
  return <video ref={own} src={src} muted playsInline preload="auto" aria-hidden="true" style={incoming ? { ...style, opacity: 0 } : style} />;
};

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
  /**
   * A clip (D34): the stage's own playing original, the parent of the one clock. The restored clip is drawn over it,
   * muted, cut at the line, following it by Topaz's rule (clipSync.ts); the original is not drawn again, and the clip
   * does not zoom.
   */
  clipParent?: HTMLVideoElement | null;
  /**
   * At phone width (D10 item 5): the compare's own box is the phone's slot, and both pictures stay where the stage
   * draws the photo (rect), which covers the slot, so the box cuts them; the labels sit 16 px in.
   */
  slot?: PhotoRect;
  /**
   * Where the labels are drawn when the star marks stand over the compare: a place after the marks and before the answer
   * and the star card, so the labels stand above the marks and take the press (AUDIT-B's finding, the lead 04:27 on
   * 22 Nov). They keep the compare's box, classes, label top and fade, so they sit and look as they did.
   */
  labelHost?: HTMLElement | null;
  /**
   * Told true from the restored picture's first drawn frame (a still's image loaded, a clip's first frame), and false
   * when the compare goes (restored-disclosure-one): the Restored sentence stands only while a restored version is
   * drawn. A version switch keeps a restored picture drawn throughout (clipswap-1), so it stays true.
   */
  onRestoredDrawn?: (drawn: boolean) => void;
}> = ({ rect, src, originalSrc, fade, orientation = { quarterTurnsClockwise: 0, mirrored: false }, maxZoom = 1, onZoomed, clipParent, slot, labelHost = null, onRestoredDrawn }) => {
  const clip = clipParent !== undefined;
  const boxRect = slot ?? rect;
  // compare-music-1 (DECIDED-BY-LEAD 12:41 on 24 Nov): the compare's arrival counts as a reveal for the music; it is
  // keyed by its upload, so a version switch is no arrival
  useCompareArrival();
  const [restoredDrawn, setRestoredDrawn] = useState(false);
  const tellDrawn = useRef(onRestoredDrawn);
  tellDrawn.current = onRestoredDrawn;
  useEffect(() => {
    tellDrawn.current?.(restoredDrawn);
  }, [restoredDrawn]);
  useEffect(
    () => () => {
      tellDrawn.current?.(false);
    },
    []
  );
  const firstDrawn = () => setRestoredDrawn(true);
  const stillRef = useRef<HTMLImageElement>(null);
  // A still already decoded before its load listener was attached (a cached image) is drawn at once
  useEffect(() => {
    const img = stillRef.current;
    if (img && img.complete && img.naturalWidth > 0) setRestoredDrawn(true);
  }, []);
  // clipswap-1: on a playing or paused clip's version switch the restored clip drawn stays drawn until the one switched
  // to has a frame at the player's time, then they swap in one frame, so "Restored" never stands over the unrestored
  // original; a press back to the drawn version drops the incoming copy, a press to a third replaces it
  const [lastClip, setLastClip] = useState<ClipShown>({ src, orientation });
  const [held, setHeld] = useState<ClipShown | null>(null);
  if (lastClip.src !== src || !sameOrientation(lastClip.orientation, orientation)) {
    setLastClip({ src, orientation });
    if (lastClip.src !== src) setHeld((h) => (!clip || !clipParent ? null : h === null ? lastClip : h.src === src ? null : h));
  }
  const holding = clip && clipParent ? held : null;
  const box = useRef<HTMLDivElement>(null);
  const down = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const [split, setSplit] = useState(100);
  const [drag, setDrag] = useState(false);
  const [zoom, setZoom] = useState<Zoom>(WHOLE);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const zoomed = zoom.z > 1;
  useEffect(() => afterPaint(() => setSplit(50)), []);
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

  useEffect(() => {
    const current = zoomRef.current;
    if (current.z === 1 && current.tx === 0 && current.ty === 0) return;
    const z = Math.min(limit, current.z);
    const next = z <= 1 ? WHOLE : clamped({ z, tx: current.tx, ty: current.ty });
    if (next.z !== current.z || next.tx !== current.tx || next.ty !== current.ty) {
      zoomRef.current = next;
      setZoom(next);
    }
  }, [limit, rect.widthPx, rect.heightPx]);

  // The wheel needs a listener that may cancel the page's own scroll, only while the compare can zoom (D15, D36)
  useEffect(() => {
    const el = box.current;
    if (!el || limit <= 1) return undefined;
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

  const resetZoom = () => {
    zoomRef.current = WHOLE;
    setZoom(WHOLE);
  };

  // Esc returns to the whole photo while zoomed in, before anything else hears it
  useEffect(() => {
    if (!zoomed) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      resetZoom();
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [zoomed]);

  const label = (edge: number) => () => {
    if (split === edge) resetZoom();
    else setSplit(edge);
  };
  const transform = `translate(${zoom.tx}px, ${zoom.ty}px) scale(${zoom.z})`;
  // In a slot, each picture's layer is placed where the stage draws the photo, from the slot's corner
  const layer: React.CSSProperties = slot
    ? { left: `${rect.leftPx - slot.leftPx}px`, top: `${rect.topPx - slot.topPx}px`, width: `${rect.widthPx}px`, height: `${rect.heightPx}px`, transform }
    : { transform };
  // A clip's labels sit 18 px down at the window's middle band, clear of the logo and the views (D34 item 1); the
  // phone's 16 px in (D10 item 5)
  const labelTop = clip || slot ? null : compareLabelTop(rect.topPx);
  const boxStyle = {
    left: `${boxRect.leftPx}px`,
    top: `${boxRect.topPx}px`,
    width: `${boxRect.widthPx}px`,
    height: `${boxRect.heightPx}px`,
    ...(labelTop === null ? {} : { '--compare-label-top': `${labelTop}px` }),
    ...(fade ? { maskImage: fade, WebkitMaskImage: fade } : {}),
  } as React.CSSProperties;
  const style = { ...boxStyle, '--split': `${split}%` } as React.CSSProperties;
  const compareClasses = ['restore-compare', clip && 'is-clip', slot && 'is-phone', drag && 'is-drag'].filter(Boolean).join(' ');
  const labelHostClasses = ['restore-compare is-labels', clip && 'is-clip', slot && 'is-phone'].filter(Boolean).join(' ');
  const labels = (
    <>
      <SecondAction part="restore-compare__label is-left" label="Original" onClick={label(100)} />
      <SecondAction part="restore-compare__label is-right" label="Restored" onClick={label(0)} />
    </>
  );
  return (
    <div
      ref={box}
      className={compareClasses}
      data-testid="restore-compare"
      data-hud-interactive="true"
      style={style}
      onPointerDown={(event) => {
        if (event.button > 0) return;
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
          const next = clamped({ z: zoomRef.current.z, tx: zoomRef.current.tx + to.x - from.x, ty: zoomRef.current.ty + to.y - from.y });
          zoomRef.current = next;
          setZoom(next);
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
      {!clip && (
        <div className="restore-compare__original">
          <div className="restore-compare__layer" style={layer}>
            <img src={originalSrc} alt="" draggable={false} />
          </div>
        </div>
      )}
      {/* Cut on an upright box; inside it the variant, stored as the camera wrote it, is turned upright: mirrored
          first, then turned clockwise, as the contract's orientation reads (the verdict on unit-restore-2) */}
      <div className="restore-compare__pic">
        <div className="restore-compare__layer" style={layer}>
          {clip ? (
            <>
              {holding && <RestoredClip key={holding.src} parent={clipParent} src={holding.src} style={turnedPicture(rect, holding.orientation)} />}
              <RestoredClip
                key={src}
                parent={clipParent}
                src={src}
                style={turnedPicture(rect, orientation)}
                incoming={holding !== null}
                onFrame={() => setHeld(null)}
                onFirstFrame={firstDrawn}
              />
            </>
          ) : (
            <img ref={stillRef} src={src} alt="" draggable={false} style={turnedPicture(rect, orientation)} onLoad={firstDrawn} />
          )}
        </div>
      </div>
      <span className="restore-compare__line" aria-hidden="true" />
      {labelHost === null
        ? labels
        : createPortal(
            <div className={labelHostClasses} data-hud-interactive="true" style={boxStyle}>
              {labels}
            </div>,
            labelHost
          )}
    </div>
  );
};

/**
 * D10 item 5 and D34 at phone width (UIUX-restoration-READY.md; rsp-cmp; crp-cmp): under the compare (or at top 604 px
 * above D16's player on a clip) the fixed sentence of REQ-RST-9.3, verbatim, with the info icon, and Download compact
 * where Ask out loud sits, saving the restoration shown.
 */
export const RestorePhoneFoot: React.FC<{
  onDownload: () => void;
  clip?: boolean;
  /** Whether a restored version is drawn: the sentence stands only then (restored-disclosure-one); Download stays. */
  restoredDrawn: boolean;
  /**
   * A clip's chosen version's row while the original stands alone because of it (pending-row-phone-one,
   * UIUX-versionwait-READY.md, its addition of 03:48): it stands in the sentence's place above the player.
   */
  slotRow?: React.ReactNode;
  /** download-check-one: Download was pressed for the version shown, so its circle holds a check in the arrow's place. */
  saved?: boolean;
}> = ({ onDownload, clip = false, restoredDrawn, slotRow = null, saved = false }) => (
  <>
    {!restoredDrawn && clip && slotRow !== null && <div className="restore-phone-slot">{slotRow}</div>}
    {restoredDrawn && (
      <p className={clip ? 'restore-phone-note is-clip' : 'restore-phone-note'}>
        <LookIcon name="info" />
        <span>{DISCLOSURES.restoration}</span>
      </p>
    )}
    <div className="restore-phone-go">
      <MainAction label="Download" icon={saved ? 'check' : 'download-simple'} size="phone" onClick={onDownload} />
    </div>
  </>
);

/**
 * D38 items 2 and 3 at phone width (UIUX-restorephone-READY.md, UIUX-clipphone-READY.md section 4; rp-clip): while the
 * restoration works, D2's phone row in the counts' place (or at top 590 px above D16's player on a clip) with the step
 * the job is on in D10's words, or "Queued" and the jobs ahead; Cancel where Ask out loud sat, held pressed from the
 * press (or the record's cancel time) until the job stops, as on the laptop.
 */
export const RestorePhoneWait: React.FC<{ job: UploadJob; onCancel: () => Promise<unknown> | void; clip?: boolean; inert?: boolean }> = ({
  job,
  onCancel,
  clip = false,
  inert = false,
}) => {
  const rows = useElapsedRows(restorationRows(job), job.id);
  const now = rows.find((row) => row.state === 'now') ?? null;
  const [asked, setAsked] = useState(false);
  useEffect(() => setAsked(false), [job.id]);
  const held = asked || job.cancelRequestedAtUTC !== undefined;
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
  const ahead = job.queueAhead ?? 0;
  return (
    <div className={clip ? 'restore-phone-wait is-clip' : 'restore-phone-wait'} inert={inert}>
      {job.state === 'queued' ? (
        <div className="work-phone-row">
          <b>
            Queued
            {ahead > 0 && <span>{` · ${ahead} ahead`}</span>}
          </b>
        </div>
      ) : now ? (
        <div className="work-phone-row">
          <b>{now.word}</b>
          {now.since !== null && <span className="work-phone-row__since">{now.since}</span>}
          <span className="work-phone-row__v">{now.value}</span>
          {now.fraction !== null && (
            <span className="work-phone-row__bar">
              <b style={{ width: barWidth(now.fraction) }} />
            </span>
          )}
        </div>
      ) : null}
      <div className="restore-phone-go">
        <SecondAction part={held ? 'work-cancel is-asked' : 'work-cancel'} label="Cancel" size="phone" aria-disabled={held ? true : undefined} onClick={cancel} />
      </div>
    </div>
  );
};

/**
 * D38 item 5 with the rulings of 15:43, 16:19 and UIUX-clipphone-READY.md section 4: a stopped restoration at phone
 * width in D37's layout with D21's sentences: the sentence (and under a refusal "Your photo is unchanged." or "Your
 * clip is unchanged.") in one block rising from 14 px above the row (at 668 on a photo, 590 on a clip), the step it
 * stopped at with A1's x in D2's row, and Try again alone at 772 when the same work may start again.
 */
export const RestorePhoneStopped: React.FC<{
  job: UploadJob | null;
  sentence: string | null;
  subline: string | null;
  canTryAgain: boolean;
  onTryAgain: () => void;
  /** At the block's head, while predicted marks stay drawn: their tag (and an estimate's pose sentence), item A's. */
  lead?: React.ReactNode;
  clip?: boolean;
  inert?: boolean;
}> = ({ job, sentence, subline, canTryAgain, onTryAgain, lead = null, clip = false, inert = false }) => {
  const stopped = job ? (restorationRows(job).find((row) => row.state === 'failed') ?? null) : null;
  return (
    <div className={clip ? 'restore-phone-stopped is-clip' : 'restore-phone-stopped'} inert={inert}>
      {!clip && <span className="work-phone-fade" aria-hidden="true" />}
      <div className="restore-phone-why" role="alert">
        {lead !== null && <div className="restore-phone-why__lead">{lead}</div>}
        {sentence !== null && <p className="restore-phone-why__s">{sentence}</p>}
        {subline !== null && <p className="restore-phone-why__sub">{subline}</p>}
      </div>
      {stopped && (
        <div className="work-phone-row is-stopped">
          <span className="work-phone-row__x" aria-hidden="true">
            <LookIcon name="x" />
          </span>
          <b>{stopped.word}</b>
          <span className="work-phone-row__v">{stopped.value}</span>
          {stopped.fraction !== null && (
            <span className="work-phone-row__bar">
              <b style={{ width: barWidth(stopped.fraction) }} />
            </span>
          )}
        </div>
      )}
      {canTryAgain && (
        <div className="restore-phone-go">
          <MainAction label="Try again" icon="arrow-clockwise" size="phone" onClick={onTryAgain} />
        </div>
      )}
    </div>
  );
};

/**
 * D36 (UIUX-restorations-READY.md, decided by the lead 13:45): under Download, only when the photo has two or more
 * ready restorations, one row each, newest first, with no heading: the time it was made, its size at the right, the
 * check on the one shown, and under each older one "Made by an earlier version of Albedian." A row pressed shows it.
 */
/** The second line of a restored version no copy that plays here matched (UIUX-versionwait-READY.md, addition stamped 02:22). */
export const NO_COPY_LINE = 'No copy that plays here matched it closely enough, so it is not shown; Download saves it.';

/**
 * The second line of a restored version whose copy stopped some other way than its gate's refusal; a press tries again
 * (UIUX-versionwait-READY.md, addition stamped 04:47).
 */
export const COPY_STOPPED_LINE = 'The copy that plays here stopped before it was finished, so it is not shown; press to try again.';

/**
 * The second line of a restored version the playback route refuses as not copyable before any copy is tried, from the
 * route's own sentence without its "yet" (UIUX-versionwait-READY.md, its addition for EXPORT's firstneed-web-words).
 */
export const NOT_COPYABLE_LINE = 'No copy that plays here is made for a clip stored this way, so it is not shown; Download saves it.';

type VersionRowProps = {
  version: ListedVersion;
  /** Its place in the list, newest first: an older one says it was made by an earlier version (D36). */
  index: number;
  /** The version drawn, which takes the check (D36's "the one shown"). */
  shownId: string | null;
  /** The row pressed, which `aria-pressed` follows; the drawn one when none was (UIUX-versionwait-READY.md item 6). */
  pressedId: string | null;
  onShow: (id: string) => void;
  /** A row whose copy stopped asks for it again when pressed. */
  onRetry?: (id: string) => void;
};

/**
 * One version's row, in the list under Download or, on the phone, in the disclosure's place above the player
 * (pending-row-phone-one): its time, its size or the seconds its copy has taken, its mark and its second lines.
 */
export const RestoreVersionRow: React.FC<VersionRowProps> = ({ version, index, shownId, pressedId, onShow, onRetry }) => {
  // The pending row's seconds, once a second while its copy is being made (C19 item 1), under Reduce Motion too
  const making = version.copy === 'making';
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!making) return undefined;
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [making]);
  const drawn = drawableVersion(version);
  // A version not drawn keeps its row: one whose copy is being made or stopped is pressable (the swap waits for its
  // copy); one no copy matched, the route cannot copy, or whose copy is not served is not (aria-disabled), with no check
  const pressable = drawn || making || version.copy === 'stopped';
  const press = () => {
    if (!pressable) return;
    if (version.copy === 'stopped') onRetry?.(version.id);
    onShow(version.id);
  };
  return (
    <button
      type="button"
      className="restore-versions__row"
      aria-pressed={pressable && version.id === pressedId}
      aria-disabled={pressable ? undefined : true}
      aria-busy={making ? true : undefined}
      onClick={press}
    >
      <span className="restore-versions__time">{madeAtWords(version.createdAtUTC)}</span>
      <span className="restore-versions__size">
        {making ? elapsedWords((Date.now() - (version.copySince ?? Date.now())) / 1000) : megabytes(version.byteSize)}
      </span>
      <span className="restore-versions__mark">
        {drawn && version.id === shownId ? (
          <LookIcon name="check" />
        ) : making ? (
          <i className="work-step__dot restore-versions__dot" />
        ) : version.copy === 'stopped' ? (
          <LookIcon name="x" />
        ) : null}
      </span>
      {index > 0 && <span className="restore-versions__older">Made by an earlier version of Albedian.</span>}
      {version.noCopy && <span className="restore-versions__older">{NO_COPY_LINE}</span>}
      {version.copy === 'stopped' && <span className="restore-versions__older">{COPY_STOPPED_LINE}</span>}
      {version.copy === 'not_copyable' && <span className="restore-versions__older">{NOT_COPYABLE_LINE}</span>}
    </button>
  );
};

export const RestoreVersions: React.FC<{
  versions: readonly ListedVersion[];
  /** The version drawn, which takes the check (D36's "the one shown"). */
  shownId: string | null;
  /** The row pressed, which `aria-pressed` follows; the drawn one when none was (UIUX-versionwait-READY.md item 6). */
  pressedId?: string | null;
  onShow: (id: string) => void;
  /** A row whose copy stopped asks for it again when pressed. */
  onRetry?: (id: string) => void;
  /**
   * A version whose row stands elsewhere (on the phone, in the disclosure's place above the player): left out here,
   * the others keeping their places, newest first (pending-row-phone-one).
   */
  hiddenId?: string | null;
}> = ({ versions, shownId, pressedId = shownId, onShow, onRetry, hiddenId = null }) => {
  // D36: no list with one ready restoration, unless that one is not drawn here, so its row says what it is waiting for
  if (versions.length < 2 && !(versions[0] !== undefined && standsAlone(versions[0]))) return null;
  if (!versions.some((version) => version.id !== hiddenId)) return null;
  return (
    <ul className="restore-versions" data-testid="restore-versions">
      {versions.map((version, i) =>
        version.id === hiddenId ? null : (
          <li key={version.id}>
            <RestoreVersionRow version={version} index={i} shownId={shownId} pressedId={pressedId} onShow={onShow} onRetry={onRetry} />
          </li>
        )
      )}
    </ul>
  );
};
