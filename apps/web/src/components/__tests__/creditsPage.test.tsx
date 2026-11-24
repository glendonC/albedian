// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import { AUDIO_CREDITS, AUDIO_NOTICES_URL, appHeardMusic, audioCreditLines } from '../../audio/audioCredits.js';
import { RECORDING } from '../../audio/music/directions.js';
import {
  CHANGES,
  CREDIT_ROWS,
  PROVENANCE_RECORD_URLS,
  SOURCE_ROW,
  creditRows,
  namesServedPath,
  recordChangeLines,
  servedNotices,
  type CreditRow,
  type LoadedRecord,
} from '../creditRows.js';
import { CREDITS_WAIT_MS, CreditsPage } from '../CreditsPage.js';

// The Credits page as the owner approved it (credit-approved-2026-11-12): rows by what a person sees, the approved four
// first; credits, licences, acknowledgements, citations and script-change lines from the served provenance records (and
// Sound from the audio engine's credits), notice links for served notices, and the app's own shader and backdrop change
// lines where a licence asks.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const publicRoot = path.resolve(here, '../../../public');

/** Every provenance record the app serves, read from disk, as the page reads them from the server. */
function recordsOnDisk(): LoadedRecord[] {
  const walk = (dir: string): string[] =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? walk(path.join(dir, e.name)) : e.name === 'PROVENANCE.json' ? [path.join(dir, e.name)] : []
    );
  return walk(publicRoot)
    .sort()
    .map((file) => ({
      url: `/${path.relative(publicRoot, file).split(path.sep).join('/')}`,
      record: AssetProvenanceRecordSchema.parse(JSON.parse(fs.readFileSync(file, 'utf8'))),
    }));
}

const loaded = recordsOnDisk();
const sources = loaded.flatMap((r) => r.record.sources);
// Built here so a source with no row fails the mapping test by its assertion, and the others find no rows
const rows: CreditRow[] = (() => {
  try {
    return creditRows(loaded);
  } catch {
    return [];
  }
})();
const rowNamed = (name: string) => rows.find((r) => r.name === name);
const ASKS_FOR_CHANGES = /modifications must be indicated|say that the data were changed/;

describe('creditRows: rows by what a person sees, every source credited from the records', () => {
  it('fetches every record the app serves, and nothing else', () => {
    expect([...PROVENANCE_RECORD_URLS].sort()).toEqual(loaded.map((r) => r.url).sort());
  });

  it('maps every source of every served record to a row, and names no source that is not served', () => {
    const ids = new Set(sources.map((s) => s.id));
    expect([...ids].filter((id) => !(id in SOURCE_ROW))).toEqual([]);
    expect(Object.keys(SOURCE_ROW).filter((id) => !ids.has(id))).toEqual([]);
    const names = new Set<string>(CREDIT_ROWS.map((r) => r.name));
    expect(Object.values(SOURCE_ROW).filter((name) => !names.has(name))).toEqual([]);
  });

  it("puts the approved page's four rows first, in its order and words, and marks every other row proposed", () => {
    expect(CREDIT_ROWS.slice(0, 4).map((r) => [r.name, r.approved])).toEqual([
      ['Milky Way photograph', true],
      ['Star catalogs', true],
      ['Type', true],
      ['Icons', true],
    ]);
    expect(CREDIT_ROWS.slice(4).every((r) => !r.approved)).toBe(true);
    // Every row is shown, Type among the approved four now that the app serves its own font with a record
    expect(rows.map((r) => r.name)).toEqual(CREDIT_ROWS.map((r) => r.name));
    expect(rowNamed('Type')?.blocks.map((b) => [b.credits, b.licences.map((l) => l.name)])).toEqual([[['Geist'], ['SIL Open Font License 1.1']]]);
  });

  it("credits every source on its row, in the record's own words", () => {
    for (const source of sources) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      expect(row?.blocks.some((b) => b.credits.includes(source.credit)), source.id).toBe(true);
    }
  });

  it("links every licence a credit's sources name, under the licence's own name, once per name", () => {
    for (const source of sources) {
      const block = rowNamed(SOURCE_ROW[source.id]!)?.blocks.find((b) => b.credits[0] === source.credit);
      const licence = block?.licences.find((l) => l.name === source.license.name);
      expect(licence, source.id).toBeDefined();
      // The page linked is one this licence's sources give
      expect(sources.some((s) => s.credit === source.credit && s.license.name === licence?.name && s.license.url === licence?.href), source.id).toBe(true);
    }
  });

  it('shows no line twice in a credit: a licence once by name, each notice distinct, an acknowledgement that is its credit not again', () => {
    for (const block of rows.flatMap((r) => r.blocks)) {
      const lines = [
        ...block.credits,
        ...block.licences.map((l) => l.name),
        ...block.notices.map((n) => n.name),
        ...block.acknowledgements,
        ...block.citations,
        ...block.changes,
      ];
      expect(lines.length, block.key).toBe(new Set(lines).size);
    }
    // The eclipses' two sources share a credit, a licence by name and an acknowledgement that is the credit itself
    const eclipses = rowNamed('Eclipses')?.blocks;
    expect(eclipses?.map((b) => [b.credits, b.licences.map((l) => l.name), b.acknowledgements])).toEqual([
      [["Eclipse Predictions by Fred Espenak and Jean Meeus (NASA's GSFC)"], ['NASA Eclipse Web Site Reproduction Permission'], []],
    ]);
  });

  it('lists each licence once by name in a credit, however many of its sources share it', () => {
    for (const block of rows.flatMap((r) => r.blocks)) {
      expect(block.licences.length, block.key).toBe(new Set(block.licences.map((l) => l.name)).size);
    }
    // The Gaia credit's sources all share one licence, so it is listed once
    expect(rowNamed('Star catalogs')?.blocks.find((b) => b.credits[0] === 'ESA/Gaia/DPAC')?.licences).toHaveLength(1);
  });

  it('shows every acknowledgement a record holds, word for word, on its row', () => {
    const owed = sources.filter((s) => s.acknowledgement);
    expect(owed.length).toBeGreaterThan(0);
    for (const source of owed) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      // Shown as its own line, or as the credit line it equals word for word
      expect(row?.blocks.some((b) => b.acknowledgements.includes(source.acknowledgement!) || b.credits.includes(source.acknowledgement!)), source.id).toBe(true);
    }
  });

  it("shows a record's own citation word for word wherever its terms ask to cite", () => {
    const asked = sources.filter((s) => /\bcite\b/i.test(s.license.conditions ?? ''));
    expect(asked.map((s) => s.id).sort()).toEqual(['iau-csn', 'iau-csn', 'nasa-exoplanet-archive-pscomppars']);
    for (const source of asked) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      expect(source.citation, source.id).toBeTruthy();
      expect(row?.blocks.some((b) => b.citations.includes(source.citation!)), source.id).toBe(true);
    }
    // A citation the terms do not ask for is not shown
    expect(rows.flatMap((r) => r.blocks.flatMap((b) => b.citations))).toHaveLength(2);
  });

  it("states a change wherever a record's licence asks for one, worded from what the app does and from the records", () => {
    expect(Object.keys(CHANGES).sort()).toEqual(['eso0932a', 'eso1804a', 'openngc-v20260501-addendum', 'openngc-v20260501-ngc']);
    for (const id of Object.keys(CHANGES)) {
      const source = sources.find((s) => s.id === id)!;
      expect(source.license.conditions ?? '', id).toMatch(ASKS_FOR_CHANGES);
      const row = rowNamed(SOURCE_ROW[id]!);
      const block = row?.blocks.find((b) => b.credits[0] === source.credit);
      const blockSources = sources.filter((s) => SOURCE_ROW[s.id] === SOURCE_ROW[id] && s.credit === source.credit);
      const combined = [...new Set(blockSources.flatMap((s) => [...(CHANGES[s.id]?.() ?? []), ...recordChangeLines(loaded, s.id)]))];
      // Every line of the block's shader/backdrop and record changes, in order, once
      expect(block?.changes, id).toEqual(combined);
    }
    // The panorama's five and Lupus 3's two are held to the code by creditChanges.test.ts; OpenNGC comes from its record
    expect(CHANGES.eso0932a!()).toHaveLength(5);
    expect(recordChangeLines(loaded, 'eso0932a')).toHaveLength(2);
    expect(CHANGES.eso1804a!()).toHaveLength(2);
    expect(recordChangeLines(loaded, 'eso1804a')).toEqual([]);
    expect(CHANGES['openngc-v20260501-ngc']!()).toEqual([]);
    expect(CHANGES['openngc-v20260501-addendum']!()).toEqual([]);
    const deepskyChanges = recordChangeLines(loaded, 'openngc-v20260501-ngc');
    expect(deepskyChanges).toHaveLength(1);
    expect(deepskyChanges[0]).toContain("Both of the catalogue's tables kept row for row");
    // Every source whose terms ask for it has its changes stated
    const asked = new Set(sources.filter((s) => ASKS_FOR_CHANGES.test(s.license.conditions ?? '')).map((s) => s.id));
    expect([...asked].filter((id) => !(id in CHANGES))).toEqual([]);
    // Two OpenNGC sources on one credit say openngc.json's sentence once
    expect(rowNamed('Deep-sky objects')?.blocks.flatMap((b) => b.changes)).toEqual(deepskyChanges);
  });

  it("links each source's own page, never a query of a thousand characters", () => {
    const hrefs = rows.flatMap((r) => r.blocks.map((b) => b.href)).filter((h): h is string => h !== null);
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(href.includes('?'), href).toBe(false);
      expect(href.length, href).toBeLessThanOrEqual(200);
    }
    expect(rowNamed('Milky Way photograph')?.blocks[0]?.href).toBe('https://www.eso.org/public/images/eso0932a/');
  });

  it('orders Star catalogs as the approved page does: ESA Hipparcos, then ESA/Gaia/DPAC', () => {
    expect(rowNamed('Star catalogs')?.blocks.slice(0, 2).map((b) => b.credits[0])).toEqual(['ESA Hipparcos', 'ESA/Gaia/DPAC']);
  });

  it('links each notice a record serves beside its files from that source\'s row, and only those', () => {
    const linked = rows.flatMap((r) => r.blocks.flatMap((b) => b.notices.map((n) => [r.name, n.name, n.href])));
    expect(linked).toEqual([
      ['Type', 'Notice', '/fonts/geist/OFL.txt'],
      ['Icons', 'Notice', '/notices/phosphor/LICENSE'],
      ['Constellations', 'Notice', '/catalog/constellations/LICENSE'],
      ['Unicode character data', 'Notice', '/notices/unicode/UNICODE-LICENSE.txt'],
    ]);
    // Each is a file the app serves, listed in its record for the source whose terms name it
    for (const [, , href] of linked) {
      expect(fs.existsSync(path.join(publicRoot, href!)), href).toBe(true);
      const record = loaded.find((r) => href!.startsWith(r.url.replace(/PROVENANCE\.json$/, '')))!;
      const file = record.record.files.find((f) => f.path === href!.slice(record.url.length - 'PROVENANCE.json'.length));
      expect(file?.sources.length, href).toBeGreaterThan(0);
    }
    // Every file the app serves that is named as a licence or a notice is linked
    const walk = (dir: string): string[] =>
      fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]));
    const named = walk(publicRoot)
      .filter((file) => /licen[cs]e|notice|^ofl\./i.test(path.basename(file)))
      .map((file) => `/${path.relative(publicRoot, file).split(path.sep).join('/')}`);
    expect(named.sort()).toEqual(linked.map(([, , href]) => href).sort());
    // The constellation notice serves both d3-celestial sources
    expect(servedNotices(loaded).get('d3-celestial-constellation-lines')).toEqual(['/catalog/constellations/LICENSE']);
    expect(servedNotices(loaded).get('d3-celestial-constellation-bounds')).toEqual(['/catalog/constellations/LICENSE']);
  });

  it('takes a file as a notice only where its own record lists it for a source whose terms name where it is served', () => {
    const at = 'served with the app at ';
    const rowsOfNaming: Array<[conditions: string, served: string, named: boolean]> = [
      [`${at}/a/LICENSE.`, '/a/LICENSE', true],
      [`${at}/a/LICENSE`, '/a/LICENSE', true],
      [`${at}/a/LICENSE (docs/x.md carries it too): text`, '/a/LICENSE', true],
      [`${at}/a/LICENSE, and with every copy`, '/a/LICENSE', true],
      [`${at}/a/LICENSE; then`, '/a/LICENSE', true],
      [`${at}/a/LICENSE: keep it`, '/a/LICENSE', true],
      [`${at}/a/LICENSE\twith a tab`, '/a/LICENSE', true],
      [`${at}/a/LICENSE.\nNext line`, '/a/LICENSE', true],
      [`${at}/a/LICENSE.txt.`, '/a/LICENSE', false],
      [`${at}/a/LICENSES.`, '/a/LICENSE', false],
      [`${at}/a/LICENSE/b.`, '/a/LICENSE', false],
      [`${at}/a/LICENSE-2.`, '/a/LICENSE', false],
      [`${at}/b/LICENSE.`, '/a/LICENSE', false],
      [`${at}/x/a/LICENSE.`, '/a/LICENSE', false],
      ['it is /a/LICENSE beside this record.', '/a/LICENSE', false],
      [`${at}/a/UNICODE-LICENSExtxt.`, '/a/UNICODE-LICENSE.txt', false],
      [`${at}/a/UNICODE-LICENSE.txt.`, '/a/UNICODE-LICENSE.txt', true],
      ['', '/a/LICENSE', false],
    ];
    for (const [conditions, served, named] of rowsOfNaming) expect(namesServedPath(conditions, served), conditions).toBe(named);

    const phosphor = loaded.find((r) => r.url === '/notices/phosphor/PROVENANCE.json')!;
    const id = phosphor.record.sources[0]!.id;
    const altered = (change: (record: LoadedRecord['record']) => LoadedRecord['record']) => [
      { url: phosphor.url, record: change(structuredClone(phosphor.record)) },
    ];
    expect(servedNotices([phosphor]).get(id)).toEqual(['/notices/phosphor/LICENSE']);
    // Its terms no longer name where the notice is served
    const unnamed = altered((r) => ({ ...r, sources: r.sources.map((s) => ({ ...s, license: { ...s.license, conditions: 'Keep the notice.' } })) }));
    expect(servedNotices(unnamed).get(id)).toBeUndefined();
    // The file is listed for no source
    const unlisted = altered((r) => ({ ...r, files: r.files.map((f) => (f.path === 'LICENSE' ? { ...f, sources: [] } : f)) }));
    expect(servedNotices(unlisted).get(id)).toBeUndefined();
    // The record lists no such file
    const missing = altered((r) => ({ ...r, files: r.files.filter((f) => f.path !== 'LICENSE') }));
    expect(servedNotices(missing).get(id)).toBeUndefined();
    // The same terms read from a record served elsewhere name a different path, so nothing there is a notice
    expect(servedNotices([{ ...phosphor, url: '/elsewhere/PROVENANCE.json' }]).get(id)).toBeUndefined();
  });

  it("credits the audio engine's works under Sound, in their own lines, linking their notices", () => {
    const sound = rowNamed('Sound');
    expect(sound?.blocks.flatMap((b) => b.credits)).toEqual(AUDIO_CREDITS.map((c) => c.line));
    expect(sound?.blocks.flatMap((b) => b.licences)).toEqual([{ name: 'Notices', href: AUDIO_NOTICES_URL }]);
  });

  it("adds each heard piece's own lines to Sound after the engine's works, each once, under the same notices", () => {
    const engine = AUDIO_CREDITS.map((c) => c.line);
    const soundOf = (heard: readonly string[]) => creditRows(loaded, AUDIO_CREDITS, heard).find((r) => r.name === 'Sound')!;
    expect(soundOf([]).blocks.flatMap((b) => b.credits)).toEqual(engine);
    const withPieces = soundOf(['A piece, its player (CC BY 4.0)', 'Its samples (CC0 1.0)', engine[0]!, 'Its samples (CC0 1.0)']);
    expect(withPieces.blocks).toHaveLength(1);
    expect(withPieces.blocks[0]!.credits).toEqual([...engine, 'A piece, its player (CC BY 4.0)', 'Its samples (CC0 1.0)']);
    expect(withPieces.blocks[0]!.licences).toEqual([{ name: 'Notices', href: AUDIO_NOTICES_URL }]);
    // The other rows are the same rows
    expect(creditRows(loaded, AUDIO_CREDITS, ['A piece']).filter((r) => r.name !== 'Sound')).toEqual(rows.filter((r) => r.name !== 'Sound'));
    // With no engine work and nothing heard there is no Sound row; a heard piece alone still has one
    expect(creditRows(loaded, []).some((r) => r.name === 'Sound')).toBe(false);
    expect(creditRows(loaded, [], ['A piece']).find((r) => r.name === 'Sound')!.blocks[0]!.credits).toEqual(['A piece']);
  });
});

describe('CreditsPage: the approved layout, and every failure named', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const byUrl = new Map(loaded.map((r) => [r.url, JSON.stringify(r.record)]));
  type Answer = (signal?: AbortSignal) => Promise<Response>;
  /** A fetch that serves every record from disk, except where `special` answers otherwise. */
  const serve = (special: Record<string, Answer> = {}) =>
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (special[url]) return special[url]!(init?.signal ?? undefined);
      return new Response(byUrl.get(url) ?? '', { status: byUrl.has(url) ? 200 : 404 });
    });

  async function renderPage(onBack = () => {}) {
    await act(async () => {
      root.render(createElement(CreditsPage, { onBack }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  /** A body that sends its first part, then breaks off while the page reads it, as a connection closed mid-body. */
  const brokenOffBody = () => {
    let sent = false;
    return new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent) return controller.error(new TypeError('terminated'));
        sent = true;
        controller.enqueue(new TextEncoder().encode('{"format": "albedian.asset-provenance", "sources": ['));
      },
    });
  };
  const q = (selector: string) => container.querySelector(selector);
  const failureLines = () => [...(q('.credits-failed')?.querySelectorAll('p') ?? [])].map((p) => p.textContent);

  it('takes focus when it opens, so a keyboard and a screen reader start on it', async () => {
    vi.stubGlobal('fetch', serve());
    await renderPage();
    expect(document.activeElement?.getAttribute('data-testid')).toBe('credits-page');
  });

  it('shows the title, then each row: its grey label over its lines as one block, the licence linked, a source link where there is one', async () => {
    vi.stubGlobal('fetch', serve());
    await renderPage();
    expect(q('h1')?.textContent).toBe('Credits');
    const shown = [...container.querySelectorAll('.credits-row')];
    expect(shown.map((r) => r.querySelector('.credits-label')?.textContent)).toEqual(rows.map((r) => r.name));
    const first = shown[0]!;
    const lines = first.querySelector('.credits-lines');
    // B4's short lines, and the lines its licence asks for: ESO's terms ask that modifications be indicated (the verdict
    // on credits-1)
    expect(lines?.innerHTML.split('<br>').map((l) => l.replace(/<[^>]+>/g, ''))).toEqual([
      'ESO/S. Brunier',
      'Creative Commons Attribution 4.0 International',
      ...CHANGES.eso0932a!(),
      ...recordChangeLines(loaded, 'eso0932a'),
    ]);
    const licence = first.querySelector('a.credits-licence');
    expect(licence?.getAttribute('href')).toBe('https://creativecommons.org/licenses/by/4.0/');
    expect(licence?.getAttribute('target')).toBe('_blank');
    expect(licence?.getAttribute('rel')).toBe('noopener noreferrer');
    const link = first.querySelector('a.credits-link');
    expect(link?.getAttribute('href')).toBe('https://www.eso.org/public/images/eso0932a/');
    expect(link?.getAttribute('aria-label')).toBe('ESO/S. Brunier: the source');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(link?.querySelector('svg.look-i')?.getAttribute('aria-hidden')).toBe('true');
    // A block whose records hold no page of their own shows no link out, and still links its licence
    const exoplanets = shown.find((r) => r.querySelector('.credits-label')?.textContent === 'Planets around other stars');
    expect(exoplanets?.querySelector('a.credits-link')).toBeNull();
    expect(exoplanets?.querySelector('a.credits-licence')?.getAttribute('href')).toBe('https://exoplanetarchive.ipac.caltech.edu/docs/acknowledge.html');
    // Its acknowledgement and its citation are drawn, word for word: its terms ask for both
    const exoplanetSource = sources.find((s) => s.id === 'nasa-exoplanet-archive-pscomppars')!;
    expect(exoplanets?.textContent).toContain(exoplanetSource.acknowledgement!);
    expect(exoplanets?.textContent).toContain(exoplanetSource.citation!);
    // A notice is drawn as a link line after the licence, opening apart from the app
    const constellations = shown.find((r) => r.querySelector('.credits-label')?.textContent === 'Constellations');
    const notice = constellations?.querySelector('a.credits-licence[href="/catalog/constellations/LICENSE"]');
    expect(notice?.textContent).toBe('Notice');
    expect(notice?.getAttribute('target')).toBe('_blank');
    expect(notice?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(constellations?.querySelector('.credits-lines')?.innerHTML.split('<br>').map((l) => l.replace(/<[^>]+>/g, ''))).toEqual([
      'd3-celestial, Olaf Frohn (github.com/ofrohn/d3-celestial)',
      'BSD 3-Clause License',
      'Notice',
    ]);
  });

  it("credits each soundtrack piece heard this visit on the Sound row, as soon as it is heard (the sound control shows no credits)", async () => {
    vi.stubGlobal('fetch', serve());
    await renderPage();
    const soundLines = () => {
      const row = [...container.querySelectorAll('.credits-row')].find((r) => r.querySelector('.credits-label')?.textContent === 'Sound')!;
      return row.querySelector('.credits-lines')!.innerHTML.split('<br>').map((l) => l.replace(/<[^>]+>/g, ''));
    };
    const engine = AUDIO_CREDITS.map((c) => c.line);
    expect(appHeardMusic.get()).toEqual([]);
    expect(soundLines()).toEqual([...engine, 'Notices']);
    act(() => appHeardMusic.add('recording'));
    expect(soundLines()).toEqual([...audioCreditLines(['recording']), 'Notices']);
    expect(soundLines()).toContain(RECORDING.credit);
    // Opened again later in the visit: the piece is still credited
    act(() => root.unmount());
    root = createRoot(container);
    await renderPage();
    expect(soundLines()).toContain(RECORDING.credit);
  });

  it('shows no count, spinner or row before the records arrive', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    await renderPage();
    expect(container.querySelectorAll('.credits-row')).toHaveLength(0);
    expect(q('[data-testid="credits-page"]')?.textContent).toBe('Credits');
  });

  const URL_ = '/textures/PROVENANCE.json';
  const FAILURES: Array<[label: string, answer: Answer, sentence: string]> = [
    ['a web page sent with status 200', async () => new Response('<!doctype html><title>Not here</title>', { status: 200 }), `${URL_} sent a web page, not its record.`],
    ['no answer', async () => Promise.reject(new TypeError('Failed to fetch')), `${URL_} did not answer.`],
    ['a body cut short', async () => new Response('{"format": "albedian.asset-provenance", "sources": [', { status: 200 }), `${URL_} could not be read as JSON.`],
    ['a wrong status', async () => new Response('', { status: 404 }), `${URL_} answered with status 404.`],
    ['JSON that is no record', async () => new Response('{"format": "something else"}', { status: 200 }), `${URL_} is not a provenance record.`],
    ['a body broken off while it is read', async () => new Response(brokenOffBody(), { status: 200 }), `${URL_} was cut off before its end.`],
  ];
  it.each(FAILURES)('names the record and says what went wrong in its own words for %s', async (_label, answer, sentence) => {
    vi.stubGlobal('fetch', serve({ [URL_]: answer }));
    await renderPage();
    expect(container.querySelectorAll('.credits-row')).toHaveLength(0);
    expect(q('.credits-failed')?.getAttribute('role')).toBe('alert');
    expect(failureLines()).toEqual(['The credits could not be read.', sentence]);
    expect(q('.credits-failed')?.textContent ?? '').not.toMatch(/Unexpected|JSON input|token|Failed to fetch|Zod|expected/);
  });

  it('names a served source that has no row, in its own words, instead of leaving it out', async () => {
    const textures = JSON.parse(byUrl.get(URL_)!);
    textures.sources.push({ ...textures.sources[0], id: 'a-source-with-no-row' });
    textures.files[0].sources.push('a-source-with-no-row');
    vi.stubGlobal('fetch', serve({ [URL_]: async () => new Response(JSON.stringify(textures), { status: 200 }) }));
    await renderPage();
    expect(failureLines()).toEqual(['The credits could not be read.', 'a-source-with-no-row has no row on the Credits page.']);
  });

  it('names every record that failed, not only the first', async () => {
    vi.stubGlobal('fetch', serve({ [URL_]: FAILURES[1]![1], '/models/PROVENANCE.json': FAILURES[3]![1] }));
    await renderPage();
    expect(failureLines()).toEqual(['The credits could not be read.', `${URL_} did not answer.`, '/models/PROVENANCE.json answered with status 404.']);
  });

  it('waits for the records ten seconds, one constant, then names the one that did not arrive', async () => {
    expect(CREDITS_WAIT_MS).toBe(10_000);
    vi.useFakeTimers();
    const never: Answer = (signal) =>
      new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
    vi.stubGlobal('fetch', serve({ [URL_]: never }));
    await act(async () => {
      root.render(createElement(CreditsPage, { onBack: () => {} }));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CREDITS_WAIT_MS - 1);
    });
    expect(q('.credits-failed')).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(failureLines()).toEqual(['The credits could not be read.', `${URL_} did not arrive within 10 seconds.`]);
  });

  it('goes back with its named back button, and with Esc', async () => {
    vi.stubGlobal('fetch', serve());
    const onBack = vi.fn();
    await renderPage(onBack);
    const back = q('button.look-icon') as HTMLButtonElement;
    expect(back.getAttribute('aria-label')).toBe('Back');
    act(() => back.click());
    expect(onBack).toHaveBeenCalledTimes(1);
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onBack).toHaveBeenCalledTimes(2);
  });

  it('stays on another key, and on an Esc something else already answered', async () => {
    vi.stubGlobal('fetch', serve());
    const onBack = vi.fn();
    await renderPage(onBack);
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
      const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      handled.preventDefault();
      window.dispatchEvent(handled);
    });
    expect(onBack).not.toHaveBeenCalled();
  });

  // The approved Credits page's values (phone3/build.mjs, c-credits), rule by rule
  const LAYOUT: ReadonlyArray<[selector: string, declarations: Record<string, string>]> = [
    ['.credits-title', { font: '300 40px / 1.05 var(--look-font-sans)', 'letter-spacing': '-0.045em' }],
    ['.credits-row', { position: 'relative', display: 'grid', gap: '6px', padding: '16px 36px 16px 0', 'border-top': '1px solid var(--look-hair)' }],
    // The motion record (MOTION-READY-web.md, "Credits page"): the page's frame in, the title's words 120 ms later,
    // each row 20 ms after the line above it
    ['.credits-page', { animation: 'look-mo-frame-in var(--look-mo-dur-frame-in) var(--look-mo-ease-frame-in) both' }],
    ['.credits-title', { animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) var(--look-mo-delay-words-in) both' }],
    ['.credits-row', { animation: 'look-mo-words-in var(--look-mo-dur-words-in) var(--look-mo-ease-std) calc(var(--look-mo-delay-words-in) + var(--look-mo-stagger) * (var(--credits-i) + 1)) both' }],
    // Out: the words go in words out, and the page in frame out over the home
    ['.credits-page.is-leaving', { animation: 'look-mo-frame-out var(--look-mo-dur-frame-out) var(--look-mo-ease-frame-out) both' }],
    ['.credits-page.is-leaving .credits-title', { animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' }],
    ['.credits-page.is-leaving .credits-row', { animation: 'look-mo-words-out var(--look-mo-dur-words-out) var(--look-mo-ease-std) both' }],
    ['.credits-row:last-child', { 'border-bottom': '1px solid var(--look-hair)' }],
    ['.credits-label', { 'font-size': '13px', color: 'var(--look-ink-3)', 'letter-spacing': '-0.005em' }],
    ['.credits-lines', { margin: '0', 'font-size': '16px', 'line-height': '1.4', 'letter-spacing': '-0.01em' }],
    ['.credits-link', { position: 'absolute', right: '-36px', top: '50%', 'margin-top': '-8px', color: 'var(--look-ink-2)', '--look-ic': '16px' }],
  ];
  it.each(LAYOUT)('draws %s as the approved page does', (selector, expected) => {
    // read without comments, so a rule with its note above it is found as well
    const css = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const body = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? '';
    const found: Record<string, string> = {};
    for (const raw of body.split(';')) {
      const colon = raw.indexOf(':');
      if (colon > 0) found[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim().replace(/\s+/g, ' ');
    }
    for (const [prop, value] of Object.entries(expected)) expect(found[prop], `${selector} ${prop}`).toBe(value);
  });

  it('numbers each row for its place in the stagger, from 0 at the top', async () => {
    vi.stubGlobal('fetch', serve());
    await renderPage();
    const rows = [...container.querySelectorAll<HTMLElement>('.credits-row')];
    expect(rows.length).toBeGreaterThan(1);
    rows.forEach((row, i) => expect(row.style.getPropertyValue('--credits-i')).toBe(String(i)));
  });

  it('documents accurately where row labels, notice links and shader/backdrop change lines come from', () => {
    const pageSource = fs.readFileSync(path.resolve(here, '../CreditsPage.tsx'), 'utf8');
    expect(pageSource).not.toContain('Every word in a row comes from a provenance record');
    expect(pageSource).toContain("the page's row labels");
    expect(pageSource).toContain('notice links for served notice files');
    expect(pageSource).toContain('B4');
    expect(pageSource).toContain('where a licence asks');
  });

  it('keeps on the page every line a source asks for beyond its credit (the verdict on credits-1): changes indicated, acknowledgements word for word, a citation where its terms ask to cite', async () => {
    vi.stubGlobal('fetch', serve());
    await renderPage();
    const page = q('[data-testid="credits-page"]')!.textContent!;
    const asked = sources.filter((s) => /indicat|changed|acknowledg|\bcite\b/i.test(s.license.conditions ?? ''));
    // The sources the auditor named are among them
    for (const id of ['eso0932a', 'eso1804a', 'openngc-v20260501-ngc', 'nasa-exoplanet-archive-pscomppars', 'iau-csn']) expect(asked.map((s) => s.id), id).toContain(id);
    for (const source of asked) {
      for (const line of [...(CHANGES[source.id]?.() ?? []), ...recordChangeLines(loaded, source.id)]) expect(page, source.id).toContain(line);
      if (source.acknowledgement) expect(page, source.id).toContain(source.acknowledgement);
      if (source.citation && /\bcite\b/i.test(source.license.conditions ?? '')) expect(page, source.id).toContain(source.citation);
    }
    // OpenNGC's terms ask to say the data were changed: its record says how
    expect(recordChangeLines(loaded, 'openngc-v20260501-ngc').length).toBeGreaterThan(0);
    // Gaia's acknowledgement is the wording ESA's linked terms ask for; it stays too
    expect(page).toContain(sources.find((s) => s.id === 'gaia-dr3')!.acknowledgement!);
  });

  it('keys licences by name so distinct licence names sharing one URL render without duplicate React keys, and disambiguates multiple notices on one credit', async () => {
    const base = loaded.find((r) => r.url === '/notices/phosphor/PROVENANCE.json')!;
    const sharedUrl = 'https://example.org/shared-terms';
    const unicodeRec = loaded.find((r) => r.url === '/notices/unicode/PROVENANCE.json')!;
    const recordSameRow = structuredClone(base.record);
    recordSameRow.sources = [
      {
        ...base.record.sources[0]!,
        id: 'unicode-ucd-15.0.0',
        credit: 'Unicode Shared',
        license: {
          ...base.record.sources[0]!.license,
          name: 'Terms A',
          url: sharedUrl,
          conditions: 'served with the app at /notices/unicode/LICENSE and served with the app at /notices/unicode/NOTICE.',
        },
      },
      {
        ...base.record.sources[0]!,
        id: 'unicode-ucd-15.1.0',
        credit: 'Unicode Shared',
        license: {
          ...base.record.sources[0]!.license,
          name: 'Terms B',
          url: sharedUrl,
        },
      },
    ];
    recordSameRow.files = [
      { ...unicodeRec.record.files[0]!, path: 'LICENSE', sources: ['unicode-ucd-15.0.0'] },
      { ...unicodeRec.record.files[0]!, path: 'NOTICE', sources: ['unicode-ucd-15.0.0'] },
    ];
    const built = creditRows([{ url: '/notices/unicode/PROVENANCE.json', record: recordSameRow }], []);
    const block = built[0]!.blocks[0]!;
    expect(block.licences).toEqual([
      { name: 'Terms A', href: sharedUrl },
      { name: 'Terms B', href: sharedUrl },
    ]);
    expect(block.notices).toEqual([
      { name: 'Notice (LICENSE)', href: '/notices/unicode/LICENSE' },
      { name: 'Notice (NOTICE)', href: '/notices/unicode/NOTICE' },
    ]);

    // Two notices with the same filename in two folders disambiguate by full path
    const recordFolderB = structuredClone(base.record);
    recordFolderB.sources = [
      {
        ...base.record.sources[0]!,
        id: 'unicode-ucd-16.0.0',
        credit: 'Unicode Shared',
        license: {
          ...base.record.sources[0]!.license,
          name: 'Terms A',
          url: sharedUrl,
          conditions: 'served with the app at /notices/unicode-b/LICENSE.',
        },
      },
    ];
    recordFolderB.files = [{ ...unicodeRec.record.files[0]!, path: 'LICENSE', sources: ['unicode-ucd-16.0.0'] }];
    const recordFolderA = structuredClone(recordSameRow);
    recordFolderA.files = [{ ...unicodeRec.record.files[0]!, path: 'LICENSE', sources: ['unicode-ucd-15.0.0'] }];
    recordFolderA.sources[0]!.license.conditions = 'served with the app at /notices/unicode/LICENSE.';
    const sameBasename = creditRows(
      [
        { url: '/notices/unicode/PROVENANCE.json', record: recordFolderA },
        { url: '/notices/unicode-b/PROVENANCE.json', record: recordFolderB },
      ],
      [],
    );
    expect(sameBasename[0]!.blocks[0]!.notices).toEqual([
      { name: 'Notice (/notices/unicode/LICENSE)', href: '/notices/unicode/LICENSE' },
      { name: 'Notice (/notices/unicode-b/LICENSE)', href: '/notices/unicode-b/LICENSE' },
    ]);

    // Render CreditsPage with distinct licence names sharing one URL + two notices, verifying zero React duplicate-key error
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      vi.stubGlobal(
        'fetch',
        serve({
          '/notices/unicode/PROVENANCE.json': async () =>
            new Response(
              JSON.stringify({
                ...unicodeRec.record,
                sources: [
                  ...recordSameRow.sources,
                  ...unicodeRec.record.sources.filter((s) => s.id !== 'unicode-ucd-15.0.0' && s.id !== 'unicode-ucd-15.1.0'),
                ],
                files: [...unicodeRec.record.files, ...recordSameRow.files],
              }),
              { status: 200 },
            ),
        }),
      );
      await renderPage();
      const errors = errorSpy.mock.calls.map((args) => args.map(String).join(' '));
      expect(errors).toEqual([]);
      const unicodeRow = [...container.querySelectorAll('.credits-row')].find(
        (r) => r.querySelector('.credits-label')?.textContent === 'Unicode character data',
      );
      const links = [...(unicodeRow?.querySelectorAll('a.credits-licence') ?? [])].map((a) => [a.textContent, a.getAttribute('href')]);
      expect(links).toContainEqual(['Terms A', sharedUrl]);
      expect(links).toContainEqual(['Terms B', sharedUrl]);
      expect(links).toContainEqual(['Notice (LICENSE)', '/notices/unicode/LICENSE']);
      expect(links).toContainEqual(['Notice (NOTICE)', '/notices/unicode/NOTICE']);
    } finally {
      errorSpy.mockRestore();
    }
  });
});
