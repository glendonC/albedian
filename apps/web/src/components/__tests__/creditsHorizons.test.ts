// @vitest-environment node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import landingMoment from '../../spatial/webgpu/cislunar/landingMoment.json';
import { CREDIT_ROWS, HORIZONS_REFERENCE, PROVENANCE_RECORD_URLS, creditRows, horizonsRetrievedDay, type LoadedRecord } from '../creditRows.js';

// credits-change-lines-one, commit 2 (AUDIT-B's licence read of main 927612bd3, G1; DECIDED-BY-LEAD 23:37 on 26 Nov): the
// landing scene ships Sun, Earth and Moon values from NASA/JPL Horizons (spatial/webgpu/cislunar/landingMoment.json), so
// Credits gives them one row in the page's own form: the service named as the file names it, no licence (Horizons
// states none), the reference Horizons' own manual suggests with the day the data were retrieved, and its page

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '../../..');
const served = (): LoadedRecord[] =>
  PROVENANCE_RECORD_URLS.map((url) => ({ url, record: AssetProvenanceRecordSchema.parse(JSON.parse(fs.readFileSync(path.join(web, 'public', url), 'utf8'))) }));
const ROW = 'Positions of the Sun, Earth and Moon';

describe('credits-change-lines-one: the JPL Horizons values of the landing scene have their row', () => {
  it("quotes the manual's suggested reference word for word (version 4.98e, References for the Horizons system)", () => {
    expect(HORIZONS_REFERENCE).toBe(
      'Giorgini, JD and JPL Solar System Dynamics Group, NASA/JPL Horizons On-Line Ephemeris System, < https://ssd.jpl.nasa.gov/horizons/ >, data retrieved'
    );
  });

  it('gives the day the file records the data were retrieved, the latest of its queries, as YYYY-Mon-DD', () => {
    expect(horizonsRetrievedDay(['2026-11-04T23:03:35Z', '2026-11-04T23:03:39Z'])).toBe('2026-Nov-04');
    expect(horizonsRetrievedDay(['2026-01-31T23:59:59Z', '2026-12-01T00:00:00Z', '2026-06-15T12:00:00Z'])).toBe('2026-Dec-01');
    expect(horizonsRetrievedDay([])).toBeNull();
    expect(horizonsRetrievedDay(['not a time'])).toBeNull();
    // The shipped file's queries, all on one day
    expect(landingMoment.horizons.queries.length).toBeGreaterThan(0);
    for (const query of landingMoment.horizons.queries) expect(query.retrievedUtc.startsWith('2026-11-04T')).toBe(true);
  });

  it('stands its row after The Earth, not approved, with one block: the service, no licence, the reference, its page', () => {
    const names = CREDIT_ROWS.map((r) => r.name as string);
    expect(names.indexOf(ROW)).toBe(names.indexOf('The Earth') + 1);
    expect(CREDIT_ROWS.find((r) => r.name === ROW)?.approved).toBe(false);
    const row = creditRows(served(), []).find((r) => r.name === ROW);
    expect(row?.blocks).toEqual([
      {
        key: 'horizons',
        credits: ['NASA/JPL Horizons API'],
        licences: [],
        notices: [],
        acknowledgements: [],
        citations: [`${HORIZONS_REFERENCE} 2026-Nov-04.`],
        changes: [],
        href: 'https://ssd.jpl.nasa.gov/horizons/',
      },
    ]);
    expect(landingMoment.horizons.service).toBe('NASA/JPL Horizons API');
  });

  it('keeps the rows of the records as they were, the new row among them in the page order', () => {
    const rows = creditRows(served(), []);
    expect(rows.map((r) => r.name)).toEqual(CREDIT_ROWS.map((r) => r.name).filter((name) => rows.some((r) => r.name === name)));
    expect(rows.filter((r) => r.name === ROW)).toHaveLength(1);
  });
});
