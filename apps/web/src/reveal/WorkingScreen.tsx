import React, { useEffect, useRef, useState } from 'react';
import { LookIcon, Logo, MainAction, SecondAction } from '../look/index.js';
import type { PhotoRect } from '../components/sourcePhotoRect.js';
import { sizeLine, stageRows, type Arrival, type StageRow } from './arrival.js';
import { PhotoCorners } from './PhotoCorners.js';
import '../styles/look-tokens.css';
import '../styles/look.css';
import '../styles/reveal.css';

export interface WorkingScreenProps {
  arrival: Arrival;
  /** Where the photo is drawn on screen, for the corners; null while no photo is on screen. */
  photoRect: PhotoRect | null;
  /** Starts the same work again (the same solve, or the same file sent again). */
  onTryAgain: () => void;
  /** A photo or clip chosen with Upload another. */
  onUploadAnother: (file: File) => void;
  /** The logo, pressed: back to the home, as the app's logo goes everywhere after it. The upload goes on. */
  onHome?: () => void;
  /** Cancel, pressed, while the job waits or works (D2): asks the server to cancel the job; a refusal lets it go. */
  onCancel?: () => Promise<unknown> | void;
}

const ROW_CLASS: Readonly<Record<StageRow['state'], string>> = {
  done: 'work-step is-done',
  now: 'work-step is-now',
  next: 'work-step is-next',
  failed: 'work-step is-failed',
};

/** A bar's width from its real fraction, to a thousandth of a percent. */
const barWidth = (fraction: number) => `${Number((fraction * 100).toFixed(3))}%`;

/**
 * The approved working screen (arrive, 2026-11-12): the photo waits untouched where the reveal will place it, with the
 * logo, the file's own size, and one row per stage of the solve job counted from the job's record (arrival.ts). When
 * the job fails (failed, decided 2026-11-12) the job's own sentence sits above the rows with Try again and Upload
 * another, and the stage it stopped at keeps its count. Nothing here moves by itself: a row changes only when the
 * job's record does.
 */
export const WorkingScreen: React.FC<WorkingScreenProps> = ({ arrival, photoRect, onTryAgain, onUploadAnother, onHome, onCancel }) => {
  const fileInput = useRef<HTMLInputElement>(null);
  const failure = arrival.failure;
  const job = arrival.job;
  // Cancel, where Try again sits on a failed job, while the job waits or works (UIUX-jobs-QUESTION.md, D2, INTERIM)
  const cancellable = failure === null && onCancel !== undefined && job !== null && (job.state === 'queued' || job.state === 'running');
  const [asked, setAsked] = useState(false);
  useEffect(() => setAsked(false), [job?.id]);
  // Pressed and greyed from the press until the job has really stopped; the record's cancel time says so too
  const held = asked || job?.cancelRequestedAtUTC !== undefined;
  const cancel = () => {
    if (held) return;
    setAsked(true);
    try {
      const answer = onCancel?.();
      if (answer && typeof (answer as Promise<unknown>).then === 'function') (answer as Promise<unknown>).catch(() => setAsked(false));
    } catch {
      setAsked(false);
    }
  };

  return (
    <div
      className={failure ? 'work-screen is-failed' : 'work-screen'}
      data-testid="working-screen"
      data-hud-interactive="true"
    >
      {photoRect && <PhotoCorners rect={photoRect} />}
      <button type="button" className="work-word" data-testid="working-home" onClick={() => onHome?.()}>
        <Logo sizePx={21} />
      </button>
      <p className="work-size">{sizeLine(arrival)}</p>
      {failure && (
        <div className="work-why" role="alert">
          <p>{failure.sentence}</p>
          <div className="work-acts">
            {failure.canTryAgain && <MainAction label="Try again" icon="arrow-clockwise" onClick={onTryAgain} />}
            <SecondAction label="Upload another" onClick={() => fileInput.current?.click()} />
          </div>
        </div>
      )}
      {cancellable && (
        <div className="work-why">
          {job.state === 'queued' && (
            <p>
              Queued
              {(job.queueAhead ?? 0) > 0 && <span>{` · ${job.queueAhead} ahead`}</span>}
            </p>
          )}
          <div className="work-acts">
            <SecondAction part={held ? 'work-cancel is-asked' : 'work-cancel'} label="Cancel" aria-disabled={held ? true : undefined} onClick={cancel} />
          </div>
        </div>
      )}
      <ol className="work-steps">
        {stageRows(arrival).map((row) => (
          <li key={row.stage} className={ROW_CLASS[row.state]} aria-current={row.state === 'now' ? 'step' : undefined}>
            <span className="work-step__mark" aria-hidden="true">
              {row.state === 'done' ? (
                <LookIcon name="check" />
              ) : row.state === 'failed' ? (
                <LookIcon name="x" />
              ) : (
                <i className="work-step__dot" />
              )}
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
      {failure && (
        <input
          ref={fileInput}
          className="work-file-input"
          data-testid="working-file-input"
          type="file"
          accept="image/*,video/*"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            // The same file chosen again still counts as a choice
            event.currentTarget.value = '';
            if (file) onUploadAnother(file);
          }}
        />
      )}
    </div>
  );
};
