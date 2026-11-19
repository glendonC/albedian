import { useEffect, useState } from 'react';
import type { CelestialFinding } from '@albedian/contracts';

/**
 * Deep-sky objects on the photo (D23, UIUX-deepsky-READY.md, decided by the lead 2026-11-19 06:54): which are drawn,
 * with which symbol, and in which words. A deep-sky finding carries only its centre and V, so the OpenNGC type and B
 * are read from the catalog file the app already ships, by the finding's catalog row, until the finding carries them.
 */

/** The naked-eye limit: objects no fainter than V 6.0 (B where OpenNGC gives no V) are drawn on the photo. */
export const DEEP_SKY_LIMIT = 6.0;

/** The shipped OpenNGC table (apps/web/public/catalog/deepsky, its PROVENANCE.json beside it). */
export const DEEP_SKY_CATALOG_URL = '/catalog/deepsky/openngc.json';

export interface DeepSkyRow {
  /** OpenNGC's type code ("GCl", "OCl", "Cl+N", "PN", "G", ...). */
  type: string;
  vMag: number | null;
  bMag: number | null;
}
export type DeepSkyTable = ReadonlyMap<string, DeepSkyRow>;

/** Each row by its OpenNGC name, from the file's own columns; a file not of that format gives no table. */
export function parseDeepSkyTable(file: unknown): DeepSkyTable | null {
  if (typeof file !== 'object' || file === null) return null;
  const { format, columns, rows } = file as { format?: unknown; columns?: unknown; rows?: unknown };
  if (format !== 'albedian.deep-sky-catalog' || !Array.isArray(columns) || !Array.isArray(rows)) return null;
  const at = (name: string) => columns.indexOf(name);
  const [name, type, v, b] = [at('name'), at('type'), at('vMag'), at('bMag')];
  if (name < 0 || type < 0 || v < 0 || b < 0) return null;
  const magnitude = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
  const table = new Map<string, DeepSkyRow>();
  for (const row of rows) {
    if (!Array.isArray(row) || typeof row[name] !== 'string' || typeof row[type] !== 'string') continue;
    table.set(row[name], { type: row[type], vMag: magnitude(row[v]), bMag: magnitude(row[b]) });
  }
  return table;
}

/** A finding made from an OpenNGC row (services/science/deep_sky_catalog.py). */
export function isDeepSky(finding: CelestialFinding): boolean {
  return finding.id.startsWith('finding-openngc-') && finding.catalogRef?.objectType === 'deep_sky';
}

export function deepSkyRowOf(finding: CelestialFinding, table: DeepSkyTable | null): DeepSkyRow | null {
  const id = finding.catalogRef?.catalogSourceId;
  return table !== null && id !== undefined ? (table.get(id) ?? null) : null;
}

/** The magnitude the choice is made by: V, else B where OpenNGC gives no V (the server's decision magnitude). */
export function decisionMagnitude(row: DeepSkyRow): number | null {
  return row.vMag ?? row.bMag;
}

export type DeepSkySymbol = 'circle' | 'globular' | 'cluster_nebula' | 'planetary' | 'galaxy';

/** The symbol for a type (Stellarium Web Engine's, as decided): a cross for a globular cluster, a square around a circle for a cluster with nebula, ticks for a planetary nebula, an ellipse for a galaxy, a circle for the rest. */
export function deepSkySymbol(type: string): DeepSkySymbol {
  if (type === 'GCl') return 'globular';
  if (type === 'Cl+N') return 'cluster_nebula';
  if (type === 'PN') return 'planetary';
  if (type === 'G') return 'galaxy';
  return 'circle';
}

/** A type in words, as decided for the card's small line; none for a type the record does not word. */
const KIND_WORDS: Readonly<Record<string, string>> = {
  OCl: 'Open cluster',
  GCl: 'Globular cluster',
  'Cl+N': 'Star cluster and nebula',
  PN: 'Planetary nebula',
  G: 'Galaxy',
  HII: 'Emission nebula',
  EmN: 'Emission nebula',
  RfN: 'Reflection nebula',
  Neb: 'Nebula',
};
export function deepSkyKindWords(type: string): string | null {
  return Object.hasOwn(KIND_WORDS, type) ? KIND_WORDS[type]! : null;
}

/** Only a common name is written on the photo: a title that is not "NGC n", "IC n" or "H n". */
export function hasCommonName(title: string): boolean {
  return !/^(NGC|IC|H) ?\d/.test(title);
}

let loading: Promise<DeepSkyTable | null> | null = null;
/** The table, read once for the page; a failed read is tried again the next time it is wanted. */
function loadDeepSkyTable(): Promise<DeepSkyTable | null> {
  loading ??= fetch(DEEP_SKY_CATALOG_URL)
    .then((response) => (response.ok ? response.json() : null))
    .then(parseDeepSkyTable)
    .catch(() => null)
    .then((table) => {
      if (table === null) loading = null;
      return table;
    });
  return loading;
}

/** For tests: forget the table read for the page. */
export function forgetDeepSkyTable(): void {
  loading = null;
}

/** The table once read, when wanted (a calibrated photo with deep-sky findings); none before, or when it cannot be read. */
export function useDeepSkyTable(wanted: boolean): DeepSkyTable | null {
  const [table, setTable] = useState<DeepSkyTable | null>(null);
  useEffect(() => {
    if (!wanted || table !== null) return undefined;
    let live = true;
    void loadDeepSkyTable().then((read) => {
      if (live && read !== null) setTable(read);
    });
    return () => {
      live = false;
    };
  }, [wanted, table]);
  return wanted ? table : null;
}
