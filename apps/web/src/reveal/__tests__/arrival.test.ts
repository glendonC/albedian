import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { JOB_STAGES, SolveJobSchema, type SolveJob } from '@albedian/contracts';
import { megabytes, sizeLine, stageRows, STAGE_WORDS, type Arrival, type ArrivalJob } from '../arrival.js';

// The working rows (arrive, approved 2026-11-12, laptop screen 3) and the failed job (failed, decided 2026-11-12):
// one row per stage of the solve job, counted only from the job's own record. The sample's numbers are the approved
// page's: 2,382,523 bytes shown as 2.4 MB, "2,080 of 4,160" on the row being worked.

const SEED = process.env.ALBEDIAN_FC_SEED ? { seed: Number(process.env.ALBEDIAN_FC_SEED) } : {};
const BYTES = 2_382_523;

const stage = (name: (typeof JOB_STAGES)[number], unit: string, done: number, total: number | null) => ({ stage: name, unit, done, total });
const STORE = stage('store', 'bytes', BYTES, BYTES);
const job = (state: string, stages: ArrivalJob['stages'], error?: string): ArrivalJob => ({ state, stages, ...(error ? { error } : {}) });

function arrival(over: Partial<Arrival> = {}): Arrival {
  return { fileName: 'night.jpg', byteSize: BYTES, stored: true, widthPx: 3024, heightPx: 4032, job: null, failure: null, ...over };
}
const states = (a: Arrival) => stageRows(a).map((row) => row.state);
const values = (a: Arrival) => stageRows(a).map((row) => row.value);
const fractions = (a: Arrival) => stageRows(a).map((row) => row.fraction);

describe('megabytes: a byte count as the approved page writes it', () => {
  it("writes the sample's 2,382,523 bytes as 2.4 MB, in millions of bytes with one decimal", () => {
    expect(megabytes(BYTES)).toBe('2.4 MB');
    expect(megabytes(2_350_000)).toBe('2.4 MB');
    expect(megabytes(2_349_999)).toBe('2.3 MB');
    expect(megabytes(1_000_000)).toBe('1.0 MB');
    expect(megabytes(104_857_600)).toBe('104.9 MB');
    expect(megabytes(2_000_000_000)).toBe('2000.0 MB');
  });

  it('never writes a small file as nothing: under a tenth of a megabyte it keeps one figure', () => {
    expect(megabytes(100_000)).toBe('0.1 MB');
    expect(megabytes(99_999)).toBe('0.1 MB');
    expect(megabytes(94_999)).toBe('0.09 MB');
    expect(megabytes(30_000)).toBe('0.03 MB');
    expect(megabytes(4_000)).toBe('0.004 MB');
    expect(megabytes(1)).toBe('0.000001 MB');
    expect(megabytes(0)).toBe('0 MB');
  });
});

describe("sizeLine: the file's own size under the logo", () => {
  it('writes the upright frame and the bytes as the approved page does', () => {
    expect(sizeLine(arrival())).toBe('3024 × 4032 · 2.4 MB');
  });

  it('writes the bytes alone until the receipt gives the frame', () => {
    expect(sizeLine(arrival({ stored: false, widthPx: null, heightPx: null }))).toBe('2.4 MB');
    expect(sizeLine(arrival({ widthPx: 3024, heightPx: null }))).toBe('2.4 MB');
  });
});

describe('stageRows: one row per stage, in the pipeline order, with the approved words', () => {
  it('names the five stages Saved, Opened, Measured, Matching, Checking', () => {
    expect(JOB_STAGES).toEqual(['store', 'decode', 'detect', 'solve', 'verify']);
    expect(STAGE_WORDS).toEqual({ store: 'Saved', decode: 'Opened', detect: 'Measured', solve: 'Matching', verify: 'Checking' });
    const rows = stageRows(arrival());
    expect(rows.map((row) => row.stage)).toEqual([...JOB_STAGES]);
    expect(rows.map((row) => row.word)).toEqual(['Saved', 'Opened', 'Measured', 'Matching', 'Checking']);
  });

  it('shows the approved working screen from its job: three done, Matching at 2,080 of 4,160, Checking not started', () => {
    const a = arrival({
      job: job('running', [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)]),
    });
    expect(states(a)).toEqual(['done', 'done', 'done', 'now', 'next']);
    expect(values(a)).toEqual(['2.4 MB', null, null, '2,080 of 4,160', null]);
    expect(fractions(a)).toEqual([null, null, null, 0.5, null]);
  });

  it('while the file is still being sent, marks Saved as the row being worked and counts nothing', () => {
    const a = arrival({ stored: false, widthPx: null, heightPx: null });
    expect(states(a)).toEqual(['now', 'next', 'next', 'next', 'next']);
    expect(values(a)).toEqual([null, null, null, null, null]);
    expect(fractions(a)).toEqual([null, null, null, null, null]);
  });

  it('once stored with no job record yet, shows Saved done with its bytes and claims no stage is being worked', () => {
    const a = arrival();
    expect(states(a)).toEqual(['done', 'next', 'next', 'next', 'next']);
    expect(values(a)).toEqual(['2.4 MB', null, null, null, null]);
  });

  it('claims no stage is being worked while the job waits in the queue', () => {
    expect(states(arrival({ job: job('queued', [STORE]) }))).toEqual(['done', 'next', 'next', 'next', 'next']);
  });

  it('marks the stage after the last finished one as worked on, with no count, when a running job has not reported it yet', () => {
    const a = arrival({ job: job('running', [STORE, stage('decode', 'frames', 1, 1)]) });
    expect(states(a)).toEqual(['done', 'done', 'now', 'next', 'next']);
    expect(values(a)).toEqual(['2.4 MB', null, null, null, null]);
    expect(fractions(a)).toEqual([null, null, null, null, null]);
    // and a job that has only just started is opening the file
    expect(states(arrival({ job: job('running', [STORE]) }))).toEqual(['done', 'now', 'next', 'next', 'next']);
  });

  it('marks no row as worked when a running job has every stage full: nothing is left that the app knows of', () => {
    const all = [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'steps', 1, 1), stage('verify', 'steps', 1, 1)];
    expect(states(arrival({ job: job('running', all) }))).toEqual(['done', 'done', 'done', 'done', 'done']);
  });

  it('shows a count on a finished row only when it is more than one, and the bytes always', () => {
    const clip = arrival({
      job: job('running', [STORE, stage('decode', 'frames', 240, 240), stage('detect', 'frames', 240, 240), stage('solve', 'candidates', 0, 364)]),
    });
    expect(values(clip)).toEqual(['2.4 MB', '240', '240', '0 of 364', null]);
    expect(fractions(clip)).toEqual([null, null, null, 0, null]);
    const one = arrival({ job: job('running', [stage('store', 'bytes', 1, 1), stage('decode', 'frames', 1, 1), stage('detect', 'frames', 0, 1)]) });
    expect(values(one)).toEqual(['0.000001 MB', null, null, null, null]);
  });

  // FIELD's walk-0410, item 4 (the lead's 21:08 answer): a photo being read showed "Measured 0 of 1" for many seconds.
  // A still has one frame and nothing to count (D10, as the restoration's rows already have it), so its worked row
  // carries no count and no bar, and the time passed stands in the count's place (C19, useElapsedRows)
  it("gives a still's one frame no count on the row being worked, so the time passed stands there", () => {
    const measuring = arrival({ job: job('running', [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 0, 1)]) });
    expect(states(measuring)).toEqual(['done', 'done', 'now', 'next', 'next']);
    expect(values(measuring)[2]).toBeNull();
    expect(fractions(measuring)[2]).toBeNull();
    const opening = arrival({ job: job('running', [STORE, stage('decode', 'frames', 0, 1)]) });
    expect(values(opening)[1]).toBeNull();
    expect(fractions(opening)[1]).toBeNull();
    // A clip's frames are counted as before
    expect(values(arrival({ job: job('running', [STORE, stage('decode', 'frames', 2, 240)]) }))[1]).toBe('2 of 240');
  });

  it("counts a clip's frames on the row being worked the same way, with thousands grouped", () => {
    const a = arrival({ job: job('running', [STORE, stage('decode', 'frames', 1200, 14400)]) });
    expect(states(a)).toEqual(['done', 'now', 'next', 'next', 'next']);
    expect(values(a)[1]).toBe('1,200 of 14,400');
    expect(fractions(a)[1]).toBeCloseTo(1200 / 14400, 12);
  });

  it('shows no count and no bar on a row whose total is not known yet', () => {
    const a = arrival({ job: job('running', [STORE, stage('decode', 'frames', 37, null)]) });
    expect(states(a)).toEqual(['done', 'now', 'next', 'next', 'next']);
    expect(values(a)[1]).toBeNull();
    expect(fractions(a)[1]).toBeNull();
  });

  it('counts a stage that ended short of its total as it ended, never rounded up to its total', () => {
    // The sample's own finished job: the search stopped at 29 of 364 candidates
    const ended = job('succeeded', [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 29, 364), stage('verify', 'steps', 1, 1)]);
    const a = arrival({ job: ended });
    expect(states(a)).toEqual(['done', 'done', 'done', 'done', 'done']);
    expect(values(a)).toEqual(['2.4 MB', null, null, '29 of 364', null]);
    expect(fractions(a)).toEqual([null, null, null, null, null]);
    // still short while a later stage runs
    const later = arrival({ job: job('running', [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 29, 364), stage('verify', 'steps', 0, 1)]) });
    expect(states(later)).toEqual(['done', 'done', 'done', 'done', 'now']);
    // Checking's single step is nothing to count either (checkcount-1, the lead's yes of 23:4x): no count, so C19's seconds
    expect(values(later)).toEqual(['2.4 MB', null, null, '29 of 364', null]);
    expect(fractions(later)[4]).toBeNull();
  });

  it('takes the bytes of the Saved row from the job once it has them, else from the receipt', () => {
    const a = arrival({ byteSize: 5_000_000, job: job('running', [stage('store', 'bytes', 4_000_000, 4_000_000)]) });
    expect(values(a)[0]).toBe('4.0 MB');
    expect(values(arrival({ byteSize: 5_000_000 }))[0]).toBe('5.0 MB');
  });

  it('reads a real SolveJob of the contract', () => {
    const real: SolveJob = SolveJobSchema.parse({
      id: 'job_4f055db773c14698',
      sourceAssetSha256: '4f055db773c14698'.padEnd(64, '0'),
      state: 'running',
      createdAtUTC: '2026-11-18T00:00:00Z',
      startedAtUTC: '2026-11-18T00:00:01Z',
      stages: [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)],
    });
    expect(values(arrival({ job: real }))).toEqual(['2.4 MB', null, null, '2,080 of 4,160', null]);
  });

  it('ignores a stage that is not one of the solve job (a restoration stage on another kind of job)', () => {
    const a = arrival({ job: job('running', [STORE, stage('decode', 'frames', 1, 1), { stage: 'encode', unit: 'frames', done: 3, total: 9 }]) });
    expect(stageRows(a)).toHaveLength(5);
    expect(states(a)).toEqual(['done', 'done', 'now', 'next', 'next']);
  });
});

describe('stageRows: a job that failed (failed, decided 2026-11-12)', () => {
  const failure = { sentence: 'The solve stopped on an internal error.', canTryAgain: true };

  it('marks the stage it stopped at, keeps its last count and bar, and dims the stages after it', () => {
    const a = arrival({
      failure,
      job: job('failed', [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 2080, 4160)], failure.sentence),
    });
    expect(states(a)).toEqual(['done', 'done', 'done', 'failed', 'next']);
    expect(values(a)).toEqual(['2.4 MB', null, null, '2,080 of 4,160', null]);
    expect(fractions(a)).toEqual([null, null, null, 0.5, null]);
  });

  it('marks the stage after the last finished one when the job stopped before reporting it', () => {
    const a = arrival({ failure, job: job('failed', [STORE, stage('decode', 'frames', 1, 1)], failure.sentence) });
    expect(states(a)).toEqual(['done', 'done', 'failed', 'next', 'next']);
    expect(values(a)).toEqual(['2.4 MB', null, null, null, null]);
  });

  it('marks Saved when the file never reached the server', () => {
    const a = arrival({ stored: false, widthPx: null, heightPx: null, failure: { sentence: 'x', canTryAgain: true } });
    expect(states(a)).toEqual(['failed', 'next', 'next', 'next', 'next']);
    expect(values(a)).toEqual([null, null, null, null, null]);
  });

  it('marks the first stage not known to be done when the solve gave no job record', () => {
    expect(states(arrival({ failure }))).toEqual(['done', 'failed', 'next', 'next', 'next']);
  });

  it('marks the last stage when every stage reported itself done and the job failed all the same', () => {
    const all = [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'steps', 1, 1), stage('verify', 'steps', 1, 1)];
    expect(states(arrival({ failure, job: job('failed', all, failure.sentence) }))).toEqual(['done', 'done', 'done', 'done', 'failed']);
  });

  it('marks the last stage when the job succeeded but its result could not be read: a failure is never shown as all done', () => {
    const all = [STORE, stage('decode', 'frames', 1, 1), stage('detect', 'frames', 1, 1), stage('solve', 'candidates', 29, 364), stage('verify', 'steps', 1, 1)];
    const a = arrival({ failure: { sentence: 'The sky solve for your photo stopped before it finished.', canTryAgain: true }, job: job('succeeded', all) });
    expect(states(a)).toEqual(['done', 'done', 'done', 'done', 'failed']);
  });

  it.each(['failed', 'canceled', 'interrupted'])('shows a %s job as stopped even before the app has worded it', (state) => {
    const a = arrival({ job: job(state, [STORE, stage('decode', 'frames', 0, 1)]) });
    expect(states(a)).toEqual(['done', 'failed', 'next', 'next', 'next']);
  });
});

describe('stageRows: properties over any job record', () => {
  const unit = fc.constantFrom('frames', 'candidates', 'steps');
  const entry = (name: (typeof JOB_STAGES)[number]) =>
    fc
      .record({ total: fc.option(fc.integer({ min: 1, max: 100_000 }), { nil: null }), frac: fc.double({ min: 0, max: 1, noNaN: true }), unit })
      .map(({ total, frac, unit: u }) => stage(name, name === 'store' ? 'bytes' : u, total === null ? Math.floor(frac * 1000) : Math.floor(frac * total), total));
  const anyJob = fc
    .record({
      upTo: fc.integer({ min: 1, max: 5 }),
      store: entry('store'),
      decode: entry('decode'),
      detect: entry('detect'),
      solve: entry('solve'),
      verify: entry('verify'),
      state: fc.constantFrom('queued', 'running', 'succeeded', 'failed', 'canceled', 'interrupted'),
    })
    .map(({ upTo, state, ...all }) => job(state, JOB_STAGES.slice(0, upTo).map((name) => all[name])));

  it('PBT: at most one row is worked or stopped, every row before it is done and every row after it not started', () => {
    fc.assert(
      fc.property(anyJob, fc.boolean(), (j, failed) => {
        const rows = stageRows(arrival({ job: j, failure: failed ? { sentence: 's', canTryAgain: true } : null }));
        expect(rows).toHaveLength(5);
        const at = rows.findIndex((row) => row.state === 'now' || row.state === 'failed');
        expect(rows.filter((row) => row.state === 'now' || row.state === 'failed').length).toBeLessThanOrEqual(1);
        const firstNext = rows.findIndex((row) => row.state === 'next');
        const lastDone = rows.map((row) => row.state).lastIndexOf('done');
        if (at >= 0) {
          expect(lastDone).toBe(at - 1);
          if (firstNext >= 0) expect(firstNext).toBe(at + 1);
        } else if (firstNext >= 0) {
          expect(lastDone).toBe(firstNext - 1);
        }
      }),
      { numRuns: 300, ...SEED }
    );
  });

  it('PBT: a bar is drawn only on the one worked or stopped row, from done over total, and never past its ends', () => {
    fc.assert(
      fc.property(anyJob, fc.boolean(), (j, failed) => {
        const rows = stageRows(arrival({ job: j, failure: failed ? { sentence: 's', canTryAgain: true } : null }));
        for (const row of rows) {
          if (row.fraction === null) continue;
          expect(['now', 'failed']).toContain(row.state);
          expect(row.fraction).toBeGreaterThanOrEqual(0);
          expect(row.fraction).toBeLessThanOrEqual(1);
          const reported = j.stages.find((s) => s.stage === row.stage)!;
          expect(reported.total).not.toBeNull();
          expect(row.fraction).toBe(reported.done / reported.total!);
          expect(row.value).toBe(`${reported.done.toLocaleString('en-US')} of ${reported.total!.toLocaleString('en-US')}`);
        }
      }),
      { numRuns: 300, ...SEED }
    );
  });

  it('PBT: every number a row shows is a number of its own stage record: nothing is estimated', () => {
    fc.assert(
      fc.property(anyJob, (j) => {
        for (const row of stageRows(arrival({ job: j }))) {
          if (row.value === null) continue;
          const reported = j.stages.find((s) => s.stage === row.stage);
          expect(reported).toBeDefined();
          const shown = row.value.replace(/ MB$/, '').split(' of ').map((part) => Number(part.replace(/,/g, '')));
          const own = row.stage === 'store' ? [Number(megabytes(reported!.done).replace(/ MB$/, ''))] : [reported!.done, reported!.total];
          for (const n of shown) expect(own).toContain(n);
        }
      }),
      { numRuns: 300, ...SEED }
    );
  });

  it('PBT: a succeeded job shows every stage done, whatever its counts', () => {
    fc.assert(
      fc.property(anyJob, (j) => {
        expect(states(arrival({ job: { ...j, state: 'succeeded' } }))).toEqual(['done', 'done', 'done', 'done', 'done']);
      }),
      { numRuns: 100, ...SEED }
    );
  });
});
