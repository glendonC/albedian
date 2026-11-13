import type { AssetProvenanceRecord } from '@albedian/contracts';
import { AUDIO_CREDITS, AUDIO_NOTICES_URL, type AudioCredit } from '../audio/audioCredits.js';
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
  { name: 'Lupus 3 photograph', approved: false },
  { name: 'The Local Bubble', approved: false },
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
  eso1804a: 'Lupus 3 photograph',
  'oneill-2024-local-bubble': 'The Local Bubble',
  'unicode-ucd-15.1.0': 'Unicode character data',
  'unicode-ucd-15.0.0': 'Unicode character data',
  'unicode-ucd-16.0.0': 'Unicode character data',
  'unicode-ucd-17.0.0': 'Unicode character data',
};

/**
 * What the app changed, for each source whose record's terms ask that changes be indicated. The panorama's words come
 * from the numbers that crop and shade it on the home; OpenNGC's terms ask to say that the data were changed.
 */
export const CHANGES: Readonly<Record<string, () => string>> = {
  eso0932a: () => homeBackdropChanges(),
  'openngc-v20260501-ngc': () => 'The data were changed.',
  'openngc-v20260501-addendum': () => 'The data were changed.',
};

/** The approved page's order inside a row, by credit: Hipparcos, then Gaia. */
const FIRST_CREDITS: Partial<Record<CreditRowName, readonly string[]>> = {
  'Star catalogs': ['ESA Hipparcos', 'ESA/Gaia/DPAC'],
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

/** The Sound row: the audio engine's works, each in its own line, and the notices their licences travel in. */
function soundBlock(audio: readonly AudioCredit[]): CreditBlock {
  return {
    key: 'audio',
    credits: audio.map((credit) => credit.line),
    licences: [{ name: 'Notices', href: AUDIO_NOTICES_URL }],
    acknowledgements: [],
    citations: [],
    changes: [],
    href: null,
  };
}

/**
 * The Credits page's rows, read from the records: on each row, one block per credit, with the licence of each of its
 * sources linked, each acknowledgement word for word, each citation its terms ask for, each owed change, and the
 * source's own page. A source named by
 * several records is credited once.
 */
export function creditRows(loaded: readonly LoadedRecord[], audio: readonly AudioCredit[] = AUDIO_CREDITS): CreditRow[] {
  const blocksByRow = new Map<CreditRowName, CreditBlock[]>();
  const seen = new Set<string>();
  for (const source of loaded.flatMap((r) => r.record.sources)) {
    if (seen.has(source.id)) continue;
    seen.add(source.id);
    const rowName = SOURCE_ROW[source.id];
    if (!rowName) throw new UnmappedSourceError(`${source.id} has no row on the Credits page`);
    const blocks = blocksByRow.get(rowName) ?? [];
    blocksByRow.set(rowName, blocks);
    let block = blocks.find((b) => b.credits[0] === source.credit);
    if (!block) {
      block = { key: `credit:${source.credit}`, credits: [source.credit], licences: [], acknowledgements: [], citations: [], changes: [], href: null };
      blocks.push(block);
    }
    pushOnce(block.licences, { name: source.license.name, href: source.license.url }, (a, b) => a.href === b.href);
    if (source.acknowledgement) pushOnce(block.acknowledgements, source.acknowledgement);
    if (source.citation && ASKS_TO_CITE.test(source.license.conditions ?? '')) pushOnce(block.citations, source.citation);
    const change = CHANGES[source.id];
    if (change) pushOnce(block.changes, change());
    block.href ??= pageOf(source.url);
  }
  if (audio.length > 0) blocksByRow.set('Sound', [soundBlock(audio)]);
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
