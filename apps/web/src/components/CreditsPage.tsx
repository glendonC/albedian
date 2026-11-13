import React, { useEffect, useRef, useState } from 'react';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import { IconButton, LookIcon } from '../look/index.js';
import { PROVENANCE_RECORD_URLS, UnmappedSourceError, creditRows, type CreditRow, type LoadedRecord } from './creditRows.js';

/** How long the page waits for each record before it says that one did not arrive. */
export const CREDITS_WAIT_MS = 10_000;

type CreditsState = { kind: 'reading' } | { kind: 'read'; rows: CreditRow[] } | { kind: 'failed'; failures: string[] };

/** A record that could not be read, said in the page's own words with the record named. */
class RecordFailure extends Error {}

/**
 * Reads one record within the bound. Each way it can fail is said in the page's own words, naming the record; the
 * wording is [proposed] (no record holds it). The parser's own messages never reach the screen.
 */
async function readRecord(url: string): Promise<LoadedRecord> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CREDITS_WAIT_MS);
  const late = () => new RecordFailure(`${url} did not arrive within ${CREDITS_WAIT_MS / 1000} seconds`);
  try {
    let response: Response;
    try {
      response = await fetch(url, { signal: controller.signal });
    } catch {
      throw controller.signal.aborted ? late() : new RecordFailure(`${url} did not answer`);
    }
    if (!response.ok) throw new RecordFailure(`${url} answered with status ${response.status}`);
    let text: string;
    try {
      text = await response.text();
    } catch {
      throw controller.signal.aborted ? late() : new RecordFailure(`${url} was cut off before its end`);
    }
    if (/^\s*</.test(text)) throw new RecordFailure(`${url} sent a web page, not its record`);
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new RecordFailure(`${url} could not be read as JSON`);
    }
    const parsed = AssetProvenanceRecordSchema.safeParse(body);
    if (!parsed.success) throw new RecordFailure(`${url} is not a provenance record`);
    return { url, record: parsed.data };
  } finally {
    clearTimeout(timer);
  }
}

/** Reads every served record; any that fail are all named, and then no rows are shown, never a short list. */
async function readRecords(): Promise<{ loaded: LoadedRecord[] } | { failures: string[] }> {
  const results = await Promise.allSettled(PROVENANCE_RECORD_URLS.map(readRecord));
  const failures = results.flatMap((r) =>
    r.status === 'rejected' ? [r.reason instanceof RecordFailure ? r.reason.message : 'a record could not be read'] : []
  );
  if (failures.length > 0) return { failures };
  return { loaded: results.map((r) => (r as PromiseFulfilledResult<LoadedRecord>).value) };
}

/**
 * The Credits page, laid out as the owner approved it for the iPhone: a back button, the title, then rows divided by
 * hairlines, each a small grey label over its lines as one block, with the small open-link icon at the right edge of a
 * credit whose source has a page. Every word in a row comes from a provenance record the app serves or from the audio
 * engine's credits. Until the records arrive the page shows only its title: no count, no spinner.
 */
export const CreditsPage: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const [state, setState] = useState<CreditsState>({ kind: 'reading' });
  const back = useRef(onBack);
  back.current = onBack;
  const pageRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let current = true;
    void readRecords().then((read) => {
      if (!current) return;
      if ('failures' in read) return setState({ kind: 'failed', failures: read.failures });
      try {
        setState({ kind: 'read', rows: creditRows(read.loaded) });
      } catch (error) {
        // A served source with no row is named; anything else is said plainly, never in the error's own words
        setState({ kind: 'failed', failures: [error instanceof UnmappedSourceError ? error.message : 'the records could not be laid out'] });
      }
    });
    return () => {
      current = false;
    };
  }, []);

  // Esc goes back, as the back button does; the page takes focus so a keyboard starts on it
  useEffect(() => {
    pageRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      event.preventDefault();
      back.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return (
    <section className="credits-page" data-testid="credits-page" aria-labelledby="credits-title" ref={pageRef} tabIndex={-1}>
      <IconButton label="Back" icon="arrow-left" onClick={() => back.current()} />
      <h1 id="credits-title" className="credits-title">
        Credits
      </h1>
      {state.kind === 'failed' && (
        <div className="credits-failed" role="alert">
          <p>The credits could not be read.</p>
          {state.failures.map((failure) => (
            <p key={failure}>{`${failure}.`}</p>
          ))}
        </div>
      )}
      {state.kind === 'read' && (
        <ul className="credits-rows">
          {state.rows.map((row) => (
            <li key={row.key} className="credits-row">
              <span className="credits-label">{row.name}</span>
              {row.blocks.map((block) => (
                <div key={block.key} className="credits-block">
                  <p className="credits-lines">
                    {[
                      ...block.credits.map((line) => <React.Fragment key={`c:${line}`}>{line}</React.Fragment>),
                      ...block.licences.map((licence) => (
                        <a key={`l:${licence.href}`} className="credits-licence" href={licence.href} target="_blank" rel="noopener noreferrer">
                          {licence.name}
                        </a>
                      )),
                      ...block.acknowledgements.map((line) => <React.Fragment key={`a:${line}`}>{line}</React.Fragment>),
                      ...block.citations.map((line) => <React.Fragment key={`t:${line}`}>{line}</React.Fragment>),
                      ...block.changes.map((line) => <React.Fragment key={`m:${line}`}>{line}</React.Fragment>),
                    ].flatMap((line, index) => (index === 0 ? [line] : [<br key={`br:${index}`} />, line]))}
                  </p>
                  {block.href && (
                    <a
                      className="credits-link"
                      href={block.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`${block.credits[0]}: the source`}
                    >
                      <LookIcon name="arrow-square-out" />
                    </a>
                  )}
                </div>
              ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
