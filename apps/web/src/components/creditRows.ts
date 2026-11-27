import { licenseAsksChangesIndicated, type AssetProvenanceRecord } from '@albedian/contracts';
import { AUDIO_CREDITS, AUDIO_NOTICES_URL, type AudioCredit } from '../audio/audioCredits.js';
import { realSkyChanges } from '../reveal/RealSkyLayer.js';
import { milkyWayInFlightChanges } from '../spatial/sky/flightMilkyWay.js';
import { milkyWayInPredictedSkyChanges } from '../spatial/sky/predictedSkyPicture.js';
import { milkyWay3dChanges, milkyWayCopyDrawn } from '../spatial/sky/panoramaGrade.js';
import landingMoment from '../spatial/webgpu/cislunar/landingMoment.json';
import { homeBackdropChanges } from './homeBackdrop.js';

/**
 * Every provenance record the app serves under apps/web/public. A test keeps this list equal to the PROVENANCE.json
 * files on disk, so a new record is credited as soon as it ships.
 */
export const PROVENANCE_RECORD_URLS: readonly string[] = [
  '/textures/PROVENANCE.json',
  '/catalog/constellations/PROVENANCE.json',
  '/catalog/deepsky/PROVENANCE.json',
  '/catalog/eclipses/PROVENANCE.json',
  '/catalog/exoplanets/PROVENANCE.json',
  '/catalog/stars/designation-links/PROVENANCE.json',
  '/catalog/stars/designations/PROVENANCE.json',
  '/catalog/stars/gaia-dr3/PROVENANCE.json',
  '/catalog/stars/hipparcos-bright-3d/PROVENANCE.json',
  '/fonts/geist/PROVENANCE.json',
  '/models/PROVENANCE.json',
  '/notices/phosphor/PROVENANCE.json',
  '/notices/unicode/PROVENANCE.json',
];

export interface LoadedRecord {
  url: string;
  record: AssetProvenanceRecord;
}

/**
 * The Credits page's rows, by what a person sees. The approved page's four come first, in its order and words
 * (credit-approved-2026-11-12); every other row, and the grouping itself, is [proposed]. A row with nothing to credit
 * is not shown (Type, until the app serves its own font with a record).
 */
export const CREDIT_ROWS = [
  { name: 'Milky Way photograph', approved: true },
  { name: 'Star catalogs', approved: true },
  { name: 'Type', approved: true },
  { name: 'Icons', approved: true },
  { name: 'Deep-sky objects', approved: false },
  { name: 'Constellations', approved: false },
  { name: 'Planets around other stars', approved: false },
  { name: 'Eclipses', approved: false },
  { name: 'The Moon', approved: false },
  { name: 'The Earth', approved: false },
  { name: 'Positions of the Sun, Earth and Moon', approved: false },
  { name: 'The Local Bubble', approved: false },
  { name: 'Spacecraft and spacesuit', approved: false },
  { name: 'Unicode character data', approved: false },
  { name: 'Sound', approved: false },
] as const;

export type CreditRowName = (typeof CREDIT_ROWS)[number]['name'];

/** The row each provenance source is credited on. A test keeps every source of every served record in this table. */
export const SOURCE_ROW: Readonly<Record<string, CreditRowName>> = {
  eso0932a: 'Milky Way photograph',
  'hipparcos-2007': 'Star catalogs',
  'gaia-dr3': 'Star catalogs',
  'gaia-dr3-astrophysical-parameters': 'Star catalogs',
  'bailer-jones-2021': 'Star catalogs',
  'gaia-edr3-hipparcos2-best-neighbour': 'Star catalogs',
  'gaia-dr3-hipparcos2-best-neighbour': 'Star catalogs',
  'hipparcos-2007-bright-3d': 'Star catalogs',
  'hipparcos-1997': 'Star catalogs',
  'hyg-v41': 'Star catalogs',
  'iau-csn': 'Star catalogs',
  'geist-font-v1.9.2': 'Type',
  'phosphor-icons-core-2.1.1': 'Icons',
  'openngc-v20260501-ngc': 'Deep-sky objects',
  'openngc-v20260501-addendum': 'Deep-sky objects',
  'd3-celestial-constellation-lines': 'Constellations',
  'd3-celestial-constellation-bounds': 'Constellations',
  'nasa-exoplanet-archive-pscomppars': 'Planets around other stars',
  'nasa-gsfc-5mkse': 'Eclipses',
  'nasa-gsfc-5mkle': 'Eclipses',
  'nasa-svs-4720': 'The Moon',
  'nasa-svs-4720-lroc-color-8k': 'The Moon',
  'nasa-svs-4720-ldem-16': 'The Moon',
  'nasa-black-marble-2016': 'The Earth',
  'natural-earth-10m-ocean': 'The Earth',
  'natural-earth-10m-lakes': 'The Earth',
  'nasa-blue-marble-2002-clouds': 'The Earth',
  'noaa-etopo-2022-60s': 'The Earth',
  'nasa-bmng-2004-03-base': 'The Earth',
  'oneill-2024-local-bubble': 'The Local Bubble',
  'nasa-3d-resources': 'Spacecraft and spacesuit',
  'nasa-3d-models-digitalspace-corporation': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-brian-e-kumanchik': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-brian-e-kumanchik-nasa-christian-a-lopez': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-christopher-r-meaney': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-goddard-space-flight-center': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-janice-c-lee-nasa-robert-l-hurt': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-jet-propulsion-laboratory': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-johnson-space-center': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-jpl-eyes-on-the-solar-system': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-jsc-integrated-graphics-operations-and-analysis-laboratory': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-larc-advanced-concepts-lab': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-matthew-j-garcia': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-michael-d-carbajal': 'Spacecraft and spacesuit',
  'nasa-3d-models-nasa-visualization-technology-applications-and-development-vtad': 'Spacecraft and spacesuit',
  'nasa-science-mission-pages': 'Spacecraft and spacesuit',
  'unicode-ucd-15.1.0': 'Unicode character data',
  'unicode-ucd-15.0.0': 'Unicode character data',
  'unicode-ucd-16.0.0': 'Unicode character data',
  'unicode-ucd-17.0.0': 'Unicode character data',
};

/**
 * What the app itself changes as it draws a source, line by line, in its own words, where no record can say it; the
 * lines are [proposed]. The panorama: on the home, worded from the numbers that frame and shade it; in the 3D view,
 * every step of the one grade list both renderers read (sky/panoramaGrade.ts), with the placement and the whole frame's
 * finishing from its lists (spatial/frameFinishing.ts); the copy the WebGPU renderer draws, by the app's own steps, how
 * it differs being its record's (recordChangeLines). Which sources owe change lines is the contract's rule, not this
 * table (recordChangeLines).
 */
export const CHANGES: Readonly<Record<string, () => readonly string[]>> = {
  eso0932a: () => [homeBackdropChanges(), milkyWay3dChanges(), milkyWayCopyDrawn(), realSkyChanges(), milkyWayInFlightChanges(), milkyWayInPredictedSkyChanges()],
};

/**
 * credits-change-lines-one (AUDIT-B's licence read, B1; DECIDED-BY-LEAD 23:37 on 26 Nov): for a source whose licence
 * asks that changes be indicated, by the contract's own rule (licenseAsksChangesIndicated, data/license-change-terms.json;
 * a licence the table does not know is read as asking), each file and each file set a script made from it: the record's
 * own sentence on how it differs from its sources, character for character, from the script producer's `changes`
 * field, and no words of the app's own. A record without the field gives no line; a source no record names gives none.
 */
export function recordChangeLines(loaded: readonly LoadedRecord[], sourceId: string): string[] {
  const source = loaded.flatMap(({ record }) => record.sources).find((s) => s.id === sourceId);
  if (!source || !licenseAsksChangesIndicated(source.license.spdx)) return [];
  return loaded.flatMap(({ record }) =>
    [...record.files, ...record.fileSets].flatMap((entry) => {
      if (!entry.sources.includes(sourceId) || entry.producedBy.method !== 'script') return [];
      const changes = (entry.producedBy as { changes?: unknown }).changes;
      return typeof changes === 'string' && changes.length > 0 ? [changes] : [];
    })
  );
}

/** The words a record's licence conditions use to say where the app serves a notice beside its files. */
const SERVED_AT = 'served with the app at ';

/**
 * Whether a licence's conditions name this served path as where the app serves a notice: the words "served with the
 * app at", the path, then the end, white space, or punctuation that ends the path (a full stop only before white space
 * or the end, so /a/LICENSE.txt does not name /a/LICENSE).
 */
export function namesServedPath(conditions: string, served: string): boolean {
  const escaped = `${SERVED_AT}${served}`.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`${escaped}(?=$|[\\s,;:)]|\\.(?:$|\\s))`).test(conditions);
}

/**
 * The notices the records serve, by source: a file of a record is a source's notice when the record lists it for that
 * source and the source's licence conditions name the path the app serves it at (the record's folder and the file's
 * path).
 */
export function servedNotices(loaded: readonly LoadedRecord[]): Map<string, string[]> {
  const notices = new Map<string, string[]>();
  for (const { url, record } of loaded) {
    const folder = url.slice(0, url.lastIndexOf('/') + 1);
    for (const file of record.files) {
      const served = `${folder}${file.path}`;
      for (const id of file.sources) {
        const source = record.sources.find((s) => s.id === id);
        if (!source || !namesServedPath(source.license.conditions ?? '', served)) continue;
        notices.set(id, [...(notices.get(id) ?? []), served]);
      }
    }
  }
  return notices;
}

/** The approved page's order inside a row, by credit: Hipparcos (credited ESA, as its catalogue page asks), then Gaia. */
const FIRST_CREDITS: Partial<Record<CreditRowName, readonly string[]>> = {
  'Star catalogs': ['ESA', 'ESA/Gaia/DPAC'],
};

export interface CreditLicence {
  /** The licence's own name, as the record words it. */
  name: string;
  /** The licence's own page. */
  href: string;
}

/** One credit on a row: its lines as one block, and a link to the source's own page where its records hold one. */
export interface CreditBlock {
  key: string;
  credits: string[];
  licences: CreditLicence[];
  /** The notices the app serves beside the source's files, each linked as "Notice" [proposed]. */
  notices: CreditLicence[];
  acknowledgements: string[];
  citations: string[];
  changes: string[];
  href: string | null;
}

export interface CreditRow {
  key: string;
  name: CreditRowName;
  approved: boolean;
  blocks: CreditBlock[];
}

/** A record's terms that ask the user to cite it: then its own citation is shown, word for word. */
const ASKS_TO_CITE = /\bcite\b/i;

/** A served source that no row of the page credits: the page names it rather than leave it out. */
export class UnmappedSourceError extends Error {}

/** A source's own page: a link of at most 200 characters that is not a query. */
function pageOf(url: string | undefined): string | null {
  return url && url.length <= 200 && !url.includes('?') ? url : null;
}

const pushOnce = <T>(list: T[], item: T, same: (a: T, b: T) => boolean = (a, b) => a === b) => {
  if (!list.some((x) => same(x, item))) list.push(item);
};

/**
 * The Sound row: the audio engine's works, each in its own line, then the lines of each soundtrack piece heard this
 * visit (the sound control shows none), each line once, and the notices their licences travel in.
 */
function soundBlock(audio: readonly AudioCredit[], heard: readonly string[]): CreditBlock {
  return {
    key: 'audio',
    credits: [...new Set([...audio.map((credit) => credit.line), ...heard])],
    licences: [{ name: 'Notices', href: AUDIO_NOTICES_URL }],
    notices: [],
    acknowledgements: [],
    citations: [],
    changes: [],
    href: null,
  };
}

/**
 * The reference the Horizons manual suggests (version 4.98e of 25 Aug 2026, "References for the Horizons system", read
 * on its page on 26 Nov 2026), word for word up to the day the data were retrieved, which follows it. Horizons states no
 * licence (credits-change-lines-one; AUDIT-B's licence read, G1).
 */
export const HORIZONS_REFERENCE =
  'Giorgini, JD and JPL Solar System Dynamics Group, NASA/JPL Horizons On-Line Ephemeris System, < https://ssd.jpl.nasa.gov/horizons/ >, data retrieved';
const HORIZONS_PAGE = 'https://ssd.jpl.nasa.gov/horizons/';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

/** The latest of these retrieval times as the reference's YYYY-Mon-DD (UTC), or null when none reads as a time. */
export function horizonsRetrievedDay(retrievedUtc: readonly string[]): string | null {
  const times = retrievedUtc.map((t) => Date.parse(t)).filter((t) => Number.isFinite(t));
  if (times.length === 0) return null;
  const day = new Date(Math.max(...times));
  return `${day.getUTCFullYear()}-${MONTHS[day.getUTCMonth()]}-${String(day.getUTCDate()).padStart(2, '0')}`;
}

/**
 * The landing scene's Sun, Earth and Moon, from NASA/JPL Horizons (spatial/webgpu/cislunar/landingMoment.json): the
 * service as the file names it, no licence, the manual's reference with the day the file records, and Horizons' page.
 */
function horizonsBlock(): CreditBlock | null {
  const day = horizonsRetrievedDay(landingMoment.horizons.queries.map((query) => query.retrievedUtc));
  if (day === null) return null;
  return {
    key: 'horizons',
    credits: [landingMoment.horizons.service],
    licences: [],
    notices: [],
    acknowledgements: [],
    citations: [`${HORIZONS_REFERENCE} ${day}.`],
    changes: [],
    href: HORIZONS_PAGE,
  };
}

/**
 * The Credits page's rows, read from the records: on each row, one block per credit, with the licence of each of its
 * sources linked, each notice the app serves beside its files, each acknowledgement word for word, each citation its
 * terms ask for, each owed change, and the source's own page. A source named by several records is credited once.
 */
export function creditRows(
  loaded: readonly LoadedRecord[],
  audio: readonly AudioCredit[] = AUDIO_CREDITS,
  heard: readonly string[] = []
): CreditRow[] {
  const blocksByRow = new Map<CreditRowName, CreditBlock[]>();
  const seen = new Set<string>();
  const notices = servedNotices(loaded);
  for (const source of loaded.flatMap((r) => r.record.sources)) {
    if (seen.has(source.id)) continue;
    seen.add(source.id);
    const rowName = SOURCE_ROW[source.id];
    if (!rowName) throw new UnmappedSourceError(`${source.id} has no row on the Credits page`);
    const blocks = blocksByRow.get(rowName) ?? [];
    blocksByRow.set(rowName, blocks);
    let block = blocks.find((b) => b.credits[0] === source.credit);
    if (!block) {
      block = {
        key: `credit:${source.credit}`,
        credits: [source.credit],
        licences: [],
        notices: [],
        acknowledgements: [],
        citations: [],
        changes: [],
        href: null,
      };
      blocks.push(block);
    }
    // A licence once by its name: sources that share it may each link its own page; the first is shown
    pushOnce(block.licences, { name: source.license.name, href: source.license.url }, (a, b) => a.name === b.name);
    for (const href of notices.get(source.id) ?? []) pushOnce(block.notices, { name: 'Notice', href }, (a, b) => a.href === b.href);
    // An acknowledgement that is the credit word for word is already shown as the credit's line
    if (source.acknowledgement && !block.credits.includes(source.acknowledgement)) pushOnce(block.acknowledgements, source.acknowledgement);
    if (source.citation && ASKS_TO_CITE.test(source.license.conditions ?? '')) pushOnce(block.citations, source.citation);
    for (const line of CHANGES[source.id]?.() ?? []) pushOnce(block.changes, line);
    for (const line of recordChangeLines(loaded, source.id)) pushOnce(block.changes, line);
    block.href ??= pageOf(source.url);
  }
  for (const blocks of blocksByRow.values()) {
    for (const block of blocks) {
      if (block.notices.length <= 1) continue;
      const fileNames = block.notices.map((n) => n.href.slice(n.href.lastIndexOf('/') + 1));
      const uniqueFiles = new Set(fileNames).size === fileNames.length;
      block.notices = block.notices.map((n, idx) => ({
        name: `Notice (${uniqueFiles ? fileNames[idx] : n.href})`,
        href: n.href,
      }));
    }
  }
  const horizons = horizonsBlock();
  if (horizons) blocksByRow.set('Positions of the Sun, Earth and Moon', [horizons]);
  if (audio.length + heard.length > 0) blocksByRow.set('Sound', [soundBlock(audio, heard)]);
  return CREDIT_ROWS.flatMap(({ name, approved }) => {
    const blocks = blocksByRow.get(name);
    if (!blocks) return [];
    const first = FIRST_CREDITS[name] ?? [];
    const rank = (b: CreditBlock) => {
      const at = first.indexOf(b.credits[0]!);
      return at < 0 ? first.length : at;
    };
    return [{ key: `row:${name}`, name, approved, blocks: [...blocks].sort((a, b) => rank(a) - rank(b)) }];
  });
}
