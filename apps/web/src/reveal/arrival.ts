import { JOB_STAGES, orientedSize, type IngestReceipt, type ObservationSolution } from '@albedian/contracts';
import type { UploadEvent } from '../services/apiClient.js';

/**
 * The working screen's rows (arrive, approved 2026-11-12, laptop screen 3) and the failed job's (failed, decided
 * 2026-11-12), worked out from what the app really holds: the chosen file, the server's receipt, and the solve job's
 * own record. Every number shown is a number of that record. Nothing is estimated, timed or turned into a
 * percentage; a stage whose total is not known shows no count and no bar.
 */

export type ArrivalStage = (typeof JOB_STAGES)[number];

/** The approved words for the solve job's stages, in the pipeline's order. */
export const STAGE_WORDS: Readonly<Record<ArrivalStage, string>> = {
  store: 'Saved',
  decode: 'Opened',
  detect: 'Measured',
  solve: 'Matching',
  verify: 'Checking',
};

/** The part of a job record the rows read: a SolveJob or an UploadJob of the contract both fit. */
export interface ArrivalJob {
  /** The job's id, for Cancel; absent in records the rows alone read. */
  readonly id?: string;
  readonly state: string;
  /** How many jobs are ahead of a queued one (the runner's served count). */
  readonly queueAhead?: number;
  /** When a cancel was asked of a running job. */
  readonly cancelRequestedAtUTC?: string;
  readonly stages: ReadonlyArray<{ readonly stage: string; readonly unit: string; readonly done: number; readonly total: number | null }>;
  readonly error?: string;
}

/** An upload on its way to a solved sky: what the working screen shows, and nothing the app does not know. */
export interface Arrival {
  fileName: string;
  /** The file's bytes: as the browser reports the chosen file, then as the receipt counts the stored one. */
  byteSize: number;
  /** Whether the server's receipt has arrived: the file is stored from then on. */
  stored: boolean;
  /** The frame as it is shown, upright, from the receipt; null until then. */
  widthPx: number | null;
  heightPx: number | null;
  /** The solve job's newest record, or null while the app holds none. */
  job: ArrivalJob | null;
  /** Set when the upload or the solve ended with no sky: the sentence shown, and whether the same work can start again. */
  failure: { sentence: string; canTryAgain: boolean } | null;
  /** The sky the solve predicted from an early pose, once served (the lock-on, D25); predicted throughout. */
  early?: ObservationSolution;
  /** A solve sent from the person's alignment (D24 step 3): the rings at their pose, as shares of the shown photo. */
  aligned?: ReadonlyArray<{ id: string; name: string; u: number; v: number }>;
}

export type StageState = 'done' | 'now' | 'next' | 'failed';

export interface StageRow {
  stage: ArrivalStage;
  word: string;
  state: StageState;
  /** The row's count as written, or null for none. */
  value: string | null;
  /** done over total for the bar of the row being worked or stopped at; null draws no bar. */
  fraction: number | null;
}

/** Job states that end a job without a result. */
const STOPPED_STATES: ReadonlySet<string> = new Set(['failed', 'canceled', 'interrupted']);

const grouped = (n: number) => n.toLocaleString('en-US');

/**
 * A byte count in millions of bytes, as the approved page writes the sample's 2,382,523 bytes ("2.4 MB"): one decimal
 * from a tenth of a megabyte up. Below that one significant figure ("0.04 MB"), so a small file is never "0.0 MB" and
 * every file reads in one unit (C28, UIUX-webuxpieces-READY.md item 4).
 */
export function megabytes(bytes: number): string {
  if (bytes <= 0) return '0 MB';
  const mb = bytes / 1_000_000;
  if (mb >= 0.1) return `${mb.toFixed(1)} MB`;
  const oneFigure = Number(mb.toPrecision(1));
  return `${oneFigure.toFixed(Math.max(1, -Math.floor(Math.log10(oneFigure))))} MB`;
}

/** The line under the logo: the upright frame once the receipt gives it, and the file's bytes. */
export function sizeLine(arrival: Pick<Arrival, 'byteSize' | 'widthPx' | 'heightPx'>): string {
  const bytes = megabytes(arrival.byteSize);
  if (arrival.widthPx === null || arrival.heightPx === null) return bytes;
  return `${arrival.widthPx} × ${arrival.heightPx} · ${bytes}`;
}

type Entry = ArrivalJob['stages'][number];
const complete = (entry: Entry) => entry.total !== null && entry.done >= entry.total;

/**
 * One row per stage. A row is done once a later stage has reported, or its own count is full; the row being worked is
 * known only for a running job (the last stage that reported, or the one after it when that one is full), and for the
 * file still being sent. A job that stopped marks that same row as failed and keeps its last count. A queued job, and
 * a stored file with no job record yet, show no row as worked: the app does not know of one.
 */
export function stageRows(arrival: Arrival): StageRow[] {
  // Read by the solve job's own stage names below, so a stage of another kind of job is never looked up
  const entries = new Map<string, Entry>();
  for (const entry of arrival.job?.stages ?? []) entries.set(entry.stage, entry);
  if (!entries.has('store') && arrival.stored) {
    entries.set('store', { stage: 'store', unit: 'bytes', done: arrival.byteSize, total: arrival.byteSize });
  }

  const state = arrival.job?.state;
  const succeeded = state === 'succeeded' && arrival.failure === null;
  const stopped = arrival.failure !== null || (state !== undefined && STOPPED_STATES.has(state));
  const working = !stopped && (state === 'running' || (arrival.job === null && !arrival.stored));

  let last = -1;
  JOB_STAGES.forEach((name, index) => {
    if (entries.has(name)) last = index;
  });
  const lastEntry = last >= 0 ? entries.get(JOB_STAGES[last]!)! : null;
  const lastFull = lastEntry !== null && complete(lastEntry);
  const final = JOB_STAGES.length - 1;
  // The row being worked, or stopped at: none when the app knows of none
  let at = -1;
  if (!succeeded && (stopped || working)) {
    if (!lastFull) at = Math.max(0, last);
    else if (last < final) at = last + 1;
    else at = stopped ? final : -1;
  }

  return JOB_STAGES.map((name, index): StageRow => {
    const entry = entries.get(name) ?? null;
    const row = (rowState: StageState, value: string | null = null, fraction: number | null = null): StageRow => ({
      stage: name,
      word: STAGE_WORDS[name],
      state: rowState,
      value,
      fraction,
    });
    if (index === at) {
      // A still's one frame is nothing to count (D10, as the restoration's rows have it): its worked row carries no count,
      // so the time passed stands there (C19; FIELD's walk-0410 saw "Measured 0 of 1" for many seconds); Checking's single
      // step likewise (checkcount-1)
      const counted = entry !== null && entry.total !== null && entry.unit !== 'bytes' && !(entry.total === 1 && (entry.unit === 'frames' || entry.unit === 'steps'));
      return counted
        ? row(stopped ? 'failed' : 'now', `${grouped(entry.done)} of ${grouped(entry.total!)}`, entry.done / entry.total!)
        : row(stopped ? 'failed' : 'now');
    }
    const done = succeeded || (at >= 0 ? index < at : index < last || (index === last && lastFull));
    if (!done) return row('next');
    if (entry === null) return row('done');
    if (entry.unit === 'bytes') return row('done', megabytes(entry.done));
    if (entry.total === null) return row('done');
    if (entry.done < entry.total) return row('done', `${grouped(entry.done)} of ${grouped(entry.total)}`);
    return row('done', entry.total > 1 ? grouped(entry.total) : null);
  });
}

/** The frame as it is shown: the receipt's measured size, turned upright by the photo's own orientation tag. */
function uprightFrame(receipt: IngestReceipt): { widthPx: number | null; heightPx: number | null } {
  const asset = receipt.sourceAsset;
  if (!(asset.width > 1 && asset.height > 1)) return { widthPx: null, heightPx: null };
  return orientedSize(asset.width, asset.height, asset.displayOrientation);
}

/**
 * The working screen's facts after one more step of an upload (reveal/arrival.ts): the receipt stores the file and
 * gives its bytes and frame, a job record replaces the one before it, a failure keeps what was known and adds its
 * sentence, and a solved sky ends the arrival. An event for an upload no longer on screen changes nothing.
 */
export function arrivalAfter(arrival: Arrival | null, event: UploadEvent): Arrival | null {
  if (arrival === null) return null;
  switch (event.kind) {
    case 'stored':
      return {
        ...arrival,
        stored: true,
        byteSize: event.receipt.sourceAsset.byteSize ?? arrival.byteSize,
        ...uprightFrame(event.receipt),
      };
    case 'job':
      return { ...arrival, job: event.job };
    case 'early':
      return { ...arrival, early: event.solution };
    case 'failed':
      return { ...arrival, job: event.job ?? arrival.job, failure: { sentence: event.sentence, canTryAgain: true } };
    case 'solved':
      return null;
  }
}

/** A file just chosen: nothing about it is known but its name and the bytes the browser counts. */
export function arrivalOf(file: Pick<File, 'name' | 'size'>): Arrival {
  return { fileName: file.name, byteSize: file.size, stored: false, widthPx: null, heightPx: null, job: null, failure: null };
}
