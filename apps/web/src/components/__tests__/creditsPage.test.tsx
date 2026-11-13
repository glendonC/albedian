// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';
import { AUDIO_CREDITS, AUDIO_NOTICES_URL } from '../../audio/audioCredits.js';
import { CHANGES, CREDIT_ROWS, PROVENANCE_RECORD_URLS, SOURCE_ROW, creditRows, type CreditRow, type LoadedRecord } from '../creditRows.js';
import { homeBackdropChanges } from '../homeBackdrop.js';
import { CREDITS_WAIT_MS, CreditsPage } from '../CreditsPage.js';

// The Credits page as the owner approved it (credit-approved-2026-11-12): rows by what a person sees, the approved four
// first; every word from a provenance record the app serves or from the audio engine's credits; each row carrying what
// its licence asks (its page linked, an acknowledgement word for word, a change stated where one is owed).

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
    // Type has no record yet (the font comes in web-fonts-1), so it is not shown until one credits it
    expect(rows.map((r) => r.name).slice(0, 3)).toEqual(['Milky Way photograph', 'Star catalogs', 'Icons']);
    expect(rowNamed('Type')).toBeUndefined();
    expect(rows.map((r) => r.name)).toEqual(CREDIT_ROWS.map((r) => r.name).filter((name) => name !== 'Type'));
  });

  it("credits every source on its row, in the record's own words", () => {
    for (const source of sources) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      expect(row?.blocks.some((b) => b.credits.includes(source.credit)), source.id).toBe(true);
    }
  });

  it("links the licence page of every source on that source's row, under the licence's own name", () => {
    for (const source of sources) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      const licences = row?.blocks.flatMap((b) => b.licences) ?? [];
      expect(licences.some((l) => l.href === source.license.url && l.name === source.license.name), source.id).toBe(true);
    }
  });

  it('lists each licence once in a credit, however many of its sources share it', () => {
    for (const block of rows.flatMap((r) => r.blocks)) {
      expect(block.licences.length, block.key).toBe(new Set(block.licences.map((l) => l.href)).size);
    }
    // The Gaia credit's sources all share one licence, so it is listed once
    expect(rowNamed('Star catalogs')?.blocks.find((b) => b.credits[0] === 'ESA/Gaia/DPAC')?.licences).toHaveLength(1);
  });

  it('shows every acknowledgement a record holds, word for word, on its row', () => {
    const owed = sources.filter((s) => s.acknowledgement);
    expect(owed.length).toBeGreaterThan(0);
    for (const source of owed) {
      const row = rowNamed(SOURCE_ROW[source.id]!);
      expect(row?.blocks.some((b) => b.acknowledgements.includes(source.acknowledgement!)), source.id).toBe(true);
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

  it("states a change wherever a record's licence asks for one, worded from what the app does", () => {
    expect(Object.keys(CHANGES).sort()).toEqual(['eso0932a', 'openngc-v20260501-addendum', 'openngc-v20260501-ngc']);
    for (const id of Object.keys(CHANGES)) {
      const source = sources.find((s) => s.id === id)!;
      expect(source.license.conditions ?? '', id).toMatch(ASKS_FOR_CHANGES);
      const row = rowNamed(SOURCE_ROW[id]!);
      expect(row?.blocks.some((b) => b.changes.includes(CHANGES[id]!())), id).toBe(true);
    }
    expect(CHANGES.eso0932a!()).toBe(homeBackdropChanges());
    expect(CHANGES['openngc-v20260501-ngc']!()).toBe('The data were changed.');
    // The one other record whose terms ask for it ships its file byte for byte (its note says so); it is put to the lead
    const asked = new Set(sources.filter((s) => ASKS_FOR_CHANGES.test(s.license.conditions ?? '')).map((s) => s.id));
    expect([...asked].filter((id) => !(id in CHANGES))).toEqual(['eso1804a']);
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

  it("credits the audio engine's works under Sound, in their own lines, linking their notices", () => {
    const sound = rowNamed('Sound');
    expect(sound?.blocks.flatMap((b) => b.credits)).toEqual(AUDIO_CREDITS.map((c) => c.line));
    expect(sound?.blocks.flatMap((b) => b.licences)).toEqual([{ name: 'Notices', href: AUDIO_NOTICES_URL }]);
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
    expect(lines?.innerHTML.split('<br>').map((l) => l.replace(/<[^>]+>/g, ''))).toEqual([
      'ESO/S. Brunier',
      'Creative Commons Attribution 4.0 International',
      homeBackdropChanges(),
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
    // Its acknowledgement and its citation are drawn, word for word
    const exoplanetSource = sources.find((s) => s.id === 'nasa-exoplanet-archive-pscomppars')!;
    expect(exoplanets?.textContent).toContain(exoplanetSource.acknowledgement!);
    expect(exoplanets?.textContent).toContain(exoplanetSource.citation!);
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
    ['.credits-row:last-child', { 'border-bottom': '1px solid var(--look-hair)' }],
    ['.credits-label', { 'font-size': '13px', color: 'var(--look-ink-3)', 'letter-spacing': '-0.005em' }],
    ['.credits-lines', { margin: '0', 'font-size': '16px', 'line-height': '1.4', 'letter-spacing': '-0.01em' }],
    ['.credits-link', { position: 'absolute', right: '-36px', top: '50%', 'margin-top': '-8px', color: 'var(--look-ink-2)', '--look-ic': '16px' }],
  ];
  it.each(LAYOUT)('draws %s as the approved page does', (selector, expected) => {
    const css = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8');
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const body = new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`).exec(css)?.[1] ?? '';
    const found: Record<string, string> = {};
    for (const raw of body.split(';')) {
      const colon = raw.indexOf(':');
      if (colon > 0) found[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim().replace(/\s+/g, ' ');
    }
    for (const [prop, value] of Object.entries(expected)) expect(found[prop], `${selector} ${prop}`).toBe(value);
  });
});
