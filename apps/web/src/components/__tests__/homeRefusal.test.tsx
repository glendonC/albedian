// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HOME_SCREEN } from './homeEntry.js';

// A refused upload on the home, as the owner approved it on 2026-11-12 (refused-approved-2026-11-12.md): beside
// Upload, line 1 names the file, line 2 is the server's own reason as a sentence, never a status code; it stays until
// the next upload and is announced as a status.

vi.mock('../../spatial/SparkPortalRenderer.js', () => ({
  SparkPortalRenderer: class {
    setObserverCoordinates() {}
    setShowcaseMode() {}
    transitionToShowcaseScene() {}
    holdClock() {}
    nudgeMovement() {}
    nudgeLook() {}
    dragLook() {}
    zoomByWheel() {}
    resize() {}
    dispose() {}
    getRenderLayers() {
      return null;
    }
    getCameraSkyPose() {
      return null;
    }
  },
}));
vi.mock('../../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: {
    toggle: vi.fn(() => false),
    setMasterVolume: vi.fn(),
    setScaleRegime: vi.fn(),
    playDetentClick: vi.fn(),
  },
}));
vi.mock('../../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../CoalescingBar.js', () => ({ CoalescingBar: () => null }));

const { HomeScreen } = await import('../HomeScreen.js');
const { FILE_NAME_MAX_CHARS, FILE_NAME_TAIL_CHARS, nameParts, refusalSentence, shortFileName } = await import('../homeRefusal.js');
const { graphemes } = await import('../../text/graphemes.js');
const { UPLOAD_DETAIL_MAX_CHARS, UploadRefusedError, boundedDetail, uploadAndSolveMedia } = await import('../../services/apiClient.js');
const { LOOK_ICON_PATHS } = await import('../../look/index.js');

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../../../..');
const homeCss = fs.readFileSync(path.resolve(here, '../../styles/home.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

/** The body of the first block whose header starts with `header`, counting nested braces. */
function extractBlock(css: string, header: string): string | null {
  const start = css.indexOf(header);
  if (start < 0) return null;
  const open = css.indexOf('{', start);
  let depth = 1;
  for (let i = open + 1; i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}' && --depth === 0) return css.slice(open + 1, i);
  }
  return null;
}

/** The declarations of the first rule whose header starts with `header` (written with its brace), by property. */
function declarationsFor(css: string, header: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of (extractBlock(css, header) ?? '').split(';')) {
    const colon = raw.indexOf(':');
    if (colon > 0) out[raw.slice(0, colon).trim()] = raw.slice(colon + 1).trim();
  }
  return out;
}

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const noop = () => {};
const q = (selector: string) => container.querySelector(selector);
const REFUSAL = 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file';

/** A file whose bytes can be read, as a browser's can (jsdom's File has no arrayBuffer). */
function readableFile(name: string, type: string): File {
  const file = new File([new Uint8Array([1, 2, 3, 4])], name, { type });
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new Uint8Array([1, 2, 3, 4]).buffer });
  return file;
}

/** Chooses `file` with the home's own picker. */
function chooseOnHome(file: File) {
  const input = container.querySelector<HTMLInputElement>('[data-testid="home-file-input"]');
  if (!input) throw new Error('No home on screen: there is no file to choose.');
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

describe('refusalSentence: the server reason as line 2, its first letter capital and a full stop added', () => {
  const ROWS: Array<[string, string | null, string | null]> = [
    ['the type refusal', REFUSAL, 'Not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file.'],
    ['an empty upload', 'the upload is empty', 'The upload is empty.'],
    ['a size refusal', 'larger than the 1048576-byte limit for a still', 'Larger than the 1048576-byte limit for a still.'],
    ['a mismatch', 'declared as image/png, but the bytes are video/mp4', 'Declared as image/png, but the bytes are video/mp4.'],
    ['a reason already a sentence', 'Already a sentence.', 'Already a sentence.'],
    ['a reason ending in a question mark', 'is this a photo?', 'Is this a photo?'],
    ['a reason ending in an exclamation mark', 'too big!', 'Too big!'],
    ['a reason with space around it', '  padded reason  ', 'Padded reason.'],
    ['a reason starting with a digit', '3 files at once', '3 files at once.'],
    ['a one letter reason', 'x', 'X.'],
    ['an empty reason', '', null],
    ['a reason of spaces only', '   ', null],
    ['no reason at all', null, null],
  ];
  it.each(ROWS)('writes %s as the approved sentence', (_label, detail, expected) => {
    expect(refusalSentence(detail)).toBe(expected);
  });

  it("turns every refusal reason the store can give into one sentence (services/ingest/store.py's own words)", () => {
    const store = fs.readFileSync(path.join(repoRoot, 'services/ingest/store.py'), 'utf8');
    const reasons = [...store.matchAll(/Refused\(\s*\d{3},\s*f?"([^"]+)"/g)].map((m) => m[1]!);
    expect(reasons.length).toBeGreaterThanOrEqual(5);
    for (const reason of reasons) {
      const sentence = refusalSentence(reason)!;
      expect(sentence[0], reason).toBe(sentence[0]!.toUpperCase());
      expect(sentence.endsWith('.') && !sentence.endsWith('..'), reason).toBe(true);
      expect(sentence.slice(1, -1), reason).toBe(reason.slice(1));
    }
  });
});

describe("UploadRefusedError: the server's own detail, or none", () => {
  const ANSWERS: Array<[string, number, string, string | null, string]> = [
    ['a detail sentence', 415, JSON.stringify({ detail: REFUSAL }), REFUSAL, REFUSAL],
    ['a detail with space around it', 422, JSON.stringify({ detail: '  the upload is empty ' }), 'the upload is empty', 'the upload is empty'],
    ["a proxy's page", 413, '<html>Request Entity Too Large</html>', null, 'the server answered HTTP 413'],
    ['a list of errors', 422, JSON.stringify({ detail: [{ loc: ['body'], msg: 'field required' }] }), null, 'the server answered HTTP 422'],
    ['a blank detail', 415, JSON.stringify({ detail: '   ' }), null, 'the server answered HTTP 415'],
    ['no body', 400, '', null, 'the server answered HTTP 400'],
  ];
  it.each(ANSWERS)('reads %s', async (_label, status, body, detail, reason) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status })));
    const error = await uploadAndSolveMedia(readableFile('notes.pdf', 'application/pdf'), 'notes.pdf').catch((e) => e);
    expect(error).toBeInstanceOf(UploadRefusedError);
    expect(error).toMatchObject({ status, detail, reason });
  });
});

describe('the bounds: nothing a server or a file name says can grow the message without end', () => {
  it('holds the bounds as three constants: 32 characters of a name, its last 12 kept, 240 of a reason', () => {
    expect([FILE_NAME_MAX_CHARS, FILE_NAME_TAIL_CHARS, UPLOAD_DETAIL_MAX_CHARS]).toEqual([32, 12, 240]);
  });

  const NAMES: Array<[label: string, name: string, shown: string]> = [
    ['a short name', 'notes.pdf', 'notes.pdf'],
    ['the longest name that fits', 'n'.repeat(FILE_NAME_MAX_CHARS), 'n'.repeat(FILE_NAME_MAX_CHARS)],
    [
      'one past it, cut in the middle, the ending kept',
      `${'n'.repeat(FILE_NAME_MAX_CHARS - 9)}-12345.pdf`,
      `${'n'.repeat(FILE_NAME_MAX_CHARS - FILE_NAME_TAIL_CHARS - 1)}\u2026${`${'n'.repeat(FILE_NAME_MAX_CHARS - 9)}-12345.pdf`.slice(-FILE_NAME_TAIL_CHARS)}`,
    ],
    ['thousands of characters', 'x'.repeat(3000), `${'x'.repeat(FILE_NAME_MAX_CHARS - FILE_NAME_TAIL_CHARS - 1)}\u2026${'x'.repeat(FILE_NAME_TAIL_CHARS)}`],
    // Characters as a person sees them: a cut falls between them, never inside one
    ['twelve emoji and no extension, within the bound', '\u{1F600}'.repeat(12), '\u{1F600}'.repeat(12)],
    ['characters outside the basic plane', '\u{12000}'.repeat(40), `${'\u{12000}'.repeat(19)}\u2026${'\u{12000}'.repeat(12)}`],
    ['emoji with a skin tone', '\u{1F44D}\u{1F3FD}'.repeat(40), `${'\u{1F44D}\u{1F3FD}'.repeat(19)}\u2026${'\u{1F44D}\u{1F3FD}'.repeat(12)}`],
    [
      'a family emoji joined by zero width joiners',
      '\u{1F469}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}'.repeat(40),
      `${'\u{1F469}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}'.repeat(19)}\u2026${'\u{1F469}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}'.repeat(12)}`,
    ],
    ['letters with a combining accent', 'e\u0301'.repeat(40), `${'e\u0301'.repeat(19)}\u2026${'e\u0301'.repeat(12)}`],
    [
      'a flag of two regional indicators after one letter',
      `a${'\u{1F1EF}\u{1F1F5}'.repeat(40)}`,
      `a${'\u{1F1EF}\u{1F1F5}'.repeat(18)}\u2026${'\u{1F1EF}\u{1F1F5}'.repeat(12)}`,
    ],
  ];
  it.each(NAMES)('shows %s within the bound', (_label, name, shown) => {
    expect(shortFileName(name)).toBe(shown);
    expect(graphemes(shortFileName(name)).length).toBeLessThanOrEqual(FILE_NAME_MAX_CHARS);
    // Every character shown is one of the name's, whole, or the cut's ellipsis
    const own = new Set(graphemes(name));
    expect(graphemes(shortFileName(name)).filter((g) => g !== '\u2026' && !own.has(g))).toEqual([]);
  });

  it('keeps the last twelve characters as a person sees them as the tail, never part of one', () => {
    const thumb = '\u{1F44D}\u{1F3FD}';
    expect(nameParts(thumb.repeat(15))).toEqual({ head: thumb.repeat(3), tail: thumb.repeat(12) });
    expect(nameParts(`x${'e\u0301'.repeat(12)}`)).toEqual({ head: 'x', tail: 'e\u0301'.repeat(12) });
    expect(nameParts('notes.pdf')).toEqual({ head: '', tail: 'notes.pdf' });
    expect(nameParts('')).toEqual({ head: '', tail: '' });
  });

  it('splits text into characters as a person sees them', () => {
    const ROWS: Array<[text: string, parts: string[]]> = [
      ['', []],
      ['ab', ['a', 'b']],
      ['\u{12000}\u{12001}', ['\u{12000}', '\u{12001}']],
      ['\u{1F44D}\u{1F3FD}a', ['\u{1F44D}\u{1F3FD}', 'a']],
      ['\u{1F469}\u200D\u{1F467}', ['\u{1F469}\u200D\u{1F467}']],
      ['e\u0301e', ['e\u0301', 'e']],
      ['\r\n', ['\r\n']],
      ['\u{1F1EF}\u{1F1F5}\u{1F1EB}\u{1F1F7}', ['\u{1F1EF}\u{1F1F5}', '\u{1F1EB}\u{1F1F7}']],
    ];
    for (const [text, parts] of ROWS) expect(graphemes(text), JSON.stringify(text)).toEqual(parts);
  });

  const words = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join(' ');
  const DETAILS: Array<[label: string, detail: string, taken: string | null]> = [
    ['a server reason', 'the upload is empty', 'the upload is empty'],
    ['the longest that fits', 'r'.repeat(UPLOAD_DETAIL_MAX_CHARS), 'r'.repeat(UPLOAD_DETAIL_MAX_CHARS)],
    ['one past it with no space, cut at the bound', 'r'.repeat(UPLOAD_DETAIL_MAX_CHARS + 1), `${'r'.repeat(UPLOAD_DETAIL_MAX_CHARS)}\u2026`],
    ['thousands of characters with spaces, cut at a word', words(1000), null],
    ['nothing but spaces', '   ', null],
    [
      'emoji with a skin tone one past it, cut between whole characters',
      `a${'\u{1F44D}\u{1F3FD}'.repeat(UPLOAD_DETAIL_MAX_CHARS)}`,
      `a${'\u{1F44D}\u{1F3FD}'.repeat(UPLOAD_DETAIL_MAX_CHARS - 1)}\u2026`,
    ],
    ['the longest that fits, in characters outside the basic plane', '\u{12000}'.repeat(UPLOAD_DETAIL_MAX_CHARS), '\u{12000}'.repeat(UPLOAD_DETAIL_MAX_CHARS)],
  ];
  it.each(DETAILS)('takes %s from an answer within the bound', (label, detail, taken) => {
    const got = boundedDetail(detail);
    if (label.startsWith('thousands')) {
      expect(got!.endsWith('\u2026')).toBe(true);
      expect(graphemes(got!).length).toBeLessThanOrEqual(UPLOAD_DETAIL_MAX_CHARS + 1);
      expect(detail.startsWith(got!.slice(0, -1))).toBe(true);
      expect(detail[got!.length - 1]).toBe(' ');
    } else {
      expect(got).toBe(taken);
    }
  });

  it('ends a cut reason with its ellipsis, not a full stop', () => {
    expect(refusalSentence(`the upload is ${'r'.repeat(300)}\u2026`)?.endsWith('r\u2026')).toBe(true);
  });

  it('bounds the reason the client takes from a refusal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'z'.repeat(5000) }), { status: 415 })));
    const error = await uploadAndSolveMedia(readableFile('notes.pdf', 'application/pdf'), 'notes.pdf').catch((e) => e);
    expect(error.detail).toBe(`${'z'.repeat(UPLOAD_DETAIL_MAX_CHARS)}\u2026`);
  });
});

describe('the home shows a refused upload beside Upload', () => {
  function renderHome(refusal: { fileName: string; detail: string | null } | null) {
    act(() => {
      root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: noop, onFileChosen: noop, refusal }));
    });
  }

  it("names the file on line 1 with the warning icon, and gives the server's reason as a sentence on line 2", () => {
    renderHome({ fileName: 'notes.pdf', detail: REFUSAL });
    const status = q('.home-refusal')!;
    expect(status.getAttribute('role')).toBe('status');
    const lines = [...status.children];
    expect(lines.map((l) => l.className)).toEqual(['home-refusal__file', 'home-refusal__reason']);
    expect(lines[0]!.textContent).toBe("Albedian can't open notes.pdf");
    expect(lines[1]!.textContent).toBe('Not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file.');
    const icon = lines[0]?.querySelector('svg.look-i');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
    expect(icon?.querySelector('path')?.getAttribute('d')).toBe(LOOK_ICON_PATHS['warning-circle']);
    expect(lines[0]?.firstElementChild?.getAttribute('class')).toBe('look-i');
  });

  it('sits right after Upload in the same row', () => {
    renderHome({ fileName: 'notes.pdf', detail: REFUSAL });
    const row = q('.home-hero .home-row')!;
    expect([...row.children].map((c) => c.className.split(' ')[0])).toEqual(['look-main', 'home-refusal']);
  });

  it('shows line 1 alone when the server gave no reason, and never a status code', () => {
    renderHome({ fileName: 'big.mov', detail: null });
    const status = q('.home-refusal')!;
    expect(status.children.length).toBe(1);
    expect(status.textContent).toBe("Albedian can't open big.mov");
    expect(q('.home-refusal__reason')).toBeNull();
    expect(q(HOME_SCREEN)!.textContent).not.toMatch(/HTTP|\b[45]\d\d\b/);
  });

  it('keeps an empty status region on a home with nothing refused, so a refusal is announced when it arrives', () => {
    renderHome(null);
    const status = q('.home-refusal')!;
    expect(status.getAttribute('role')).toBe('status');
    expect(status.textContent).toBe('');
    expect(status.children.length).toBe(0);
    renderHome({ fileName: 'notes.pdf', detail: REFUSAL });
    expect(q('.home-refusal')).toBe(status);
    expect(status.textContent).toBe("Albedian can't open notes.pdfNot a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file.");
  });

  it('puts the status region on screen before its words, so a home that comes back with a refusal announces it', () => {
    const observer = new MutationObserver(() => {});
    observer.observe(container, { childList: true, subtree: true });
    renderHome({ fileName: 'notes.pdf', detail: REFUSAL });
    const records = observer.takeRecords();
    observer.disconnect();
    const region = q('.home-refusal')!;
    expect(region.textContent).toContain("Albedian can't open notes.pdf");
    // The words went into a region already on the page, not in with it
    expect(records.some((r) => r.target === region && r.addedNodes.length > 0)).toBe(true);
  });

  it('draws the icon at 18 px and the lines in the approved type, beside Upload 22 px from its circle', () => {
    // Upload alone makes the row; the message hangs beside it, 22 px from its circle and centred on it
    expect(declarationsFor(homeCss, '.home-row {')).toMatchObject({ position: 'relative', display: 'inline-flex' });
    expect(declarationsFor(homeCss, '.home-refusal {')).toEqual({
      '--look-ic': '18px',
      display: 'grid',
      'grid-template-columns': 'minmax(0, 1fr)',
      gap: '4px',
      position: 'absolute',
      left: 'calc(100% + 22px)',
      top: '50%',
      transform: 'translateY(-50%)',
      width: 'max-content',
      'max-width': 'min(420px, calc(100vw - 100% - 22px - 48px - 32px))',
      'overflow-x': 'clip',
    });
    expect(declarationsFor(homeCss, '.home-refusal__file {')).toMatchObject({
      display: 'flex',
      'align-items': 'center',
      gap: '8px',
      'font-size': '15px',
      'line-height': '1.3',
      'letter-spacing': '-0.01em',
      color: 'var(--look-ink)',
    });
    expect(declarationsFor(homeCss, '.home-refusal__reason {')).toMatchObject({
      'padding-left': '26px',
      'font-size': '14px',
      'line-height': '1.4',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-2)',
    });
  });

  it('bounds the message both ways: two lines for the file, two for the reason, nothing drawn past its box', () => {
    // Every rule inside the message, whole: a declaration added or dropped fails here. The box (above) is one column
    // that may shrink below the name's width and clips what would cross its side ('overflow-x: clip'), so nothing it
    // holds is drawn past it, whatever the characters' widths; every part below gives way before that clip is reached.
    // The file: the words may wrap once, so it takes two lines at most
    expect(declarationsFor(homeCss, '.home-refusal__words {')).toEqual({ display: 'flex', 'flex-wrap': 'wrap', 'column-gap': '0.25em', 'min-width': '0' });
    // "Albedian can't open" stays on one line and gives way with an ellipsis in a box narrower than itself
    expect(declarationsFor(homeCss, '.home-refusal__open {')).toEqual({
      'min-width': '0',
      overflow: 'hidden',
      'text-overflow': 'ellipsis',
      'white-space': 'nowrap',
    });
    // The name stays on one line, as wide as its line at most
    expect(declarationsFor(homeCss, '.home-refusal__name {')).toEqual({ display: 'flex', 'min-width': '0', 'max-width': '100%', 'white-space': 'nowrap' });
    // Its head gives way first, to nothing
    expect(declarationsFor(homeCss, '.home-refusal__head {')).toEqual({ 'min-width': '0', overflow: 'hidden', 'text-overflow': 'ellipsis' });
    // Its tail keeps its width until it alone is wider than the name's line, then gives way with an ellipsis
    expect(declarationsFor(homeCss, '.home-refusal__tail {')).toEqual({
      flex: 'none',
      'max-width': '100%',
      overflow: 'hidden',
      'text-overflow': 'ellipsis',
    });
    // The reason: two lines, broken anywhere, clipped at its own box
    expect(declarationsFor(homeCss, '.home-refusal__reason {')).toEqual({
      margin: '0',
      'padding-left': '26px',
      'font-size': '14px',
      'line-height': '1.4',
      'letter-spacing': '-0.005em',
      color: 'var(--look-ink-2)',
      display: '-webkit-box',
      '-webkit-box-orient': 'vertical',
      '-webkit-line-clamp': '2',
      overflow: 'hidden',
      'overflow-wrap': 'anywhere',
    });
    const narrow = extractBlock(homeCss, '@media (max-width: 720px)')!;
    expect(declarationsFor(narrow, '.home-refusal {')).toMatchObject({ 'max-width': 'calc(100vw - 100% - 22px - 32px)' });
    expect(declarationsFor(narrow, '.home-row {')).toEqual({});
  });

  it('shows a long file name cut in the middle, keeping its ending', () => {
    const name = `${'a'.repeat(240)}-final.pdf`;
    renderHome({ fileName: name, detail: null });
    expect(q('.home-refusal__file')?.textContent).toBe(`Albedian can't open ${shortFileName(name)}`);
    expect(q('.home-refusal__tail')?.textContent).toBe(shortFileName(name).slice(-FILE_NAME_TAIL_CHARS));
    expect(q('.home-refusal__tail')?.textContent?.endsWith('-final.pdf')).toBe(true);
  });
});

describe('the app: a refusal brings the home back with the message, until the next upload', () => {
  type Answer = () => Promise<Response>;
  /** The app on a fetch whose /api/ingest answers come from `answers`, in order; returns the calls made. */
  async function renderApp(answers: Answer[]) {
    const { App } = await import('../../App.js');
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url === '/api/media/discover') return new Response(JSON.stringify({ items: [] }), { status: 200 });
        const next = answers.shift();
        if (!next) throw new Error(`no answer planned for ${url}`);
        return next();
      })
    );
    let made = 0;
    Object.defineProperty(URL, 'createObjectURL', { value: () => `blob:refusal-${++made}`, configurable: true, writable: true });
    await act(async () => {
      root.render(createElement(App));
    });
    return calls;
  }
  afterEach(() => {
    delete (URL as unknown as Record<string, unknown>).createObjectURL;
  });
  const refuse = (status: number, detail: string | null) => async () =>
    new Response(detail === null ? 'no reason' : JSON.stringify({ detail }), { status });
  const settle = () => act(async () => await new Promise((r) => setTimeout(r, 0)));
  /** Lets React and the fetch answers run, 10 ms at a time, until `check` passes; fails after 2 s. */
  async function waitUntil(check: () => void) {
    for (let waited = 0; ; waited += 10) {
      await act(async () => await new Promise((r) => setTimeout(r, 10)));
      try {
        return check();
      } catch (error) {
        if (waited >= 2000) throw error;
      }
    }
  }

  it('leaves the home at once, then comes back to it with the message when the server refuses', async () => {
    const calls = await renderApp([refuse(415, REFUSAL)]);
    chooseOnHome(readableFile('notes.pdf', 'application/pdf'));
    expect(q(HOME_SCREEN)).toBeNull();
    await waitUntil(() => expect(q(HOME_SCREEN)).not.toBeNull());
    expect(q('.home-refusal')!.textContent).toBe(
      "Albedian can't open notes.pdfNot a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file."
    );
    // Refused, so nothing is solved
    expect(calls.filter((c) => c !== '/api/media/discover')).toEqual(['/api/ingest']);
  });

  it('clears the message with the next upload, and a later home shows none', async () => {
    let release: (r: Response) => void = noop;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    await renderApp([refuse(413, null), () => pending]);
    chooseOnHome(readableFile('big.mov', 'video/quicktime'));
    await waitUntil(() => expect(q('.home-refusal')?.textContent).toBe("Albedian can't open big.mov"));
    chooseOnHome(readableFile('night.jpg', 'image/jpeg'));
    expect(q(HOME_SCREEN)).toBeNull();
    act(() => (q('.alb-lusion-brand-btn') as HTMLButtonElement).click());
    expect(q(HOME_SCREEN)).not.toBeNull();
    expect(q('.home-refusal')!.textContent).toBe('');
    release(new Response('', { status: 503 }));
    await settle();
  });

  it('keeps an older upload\'s refusal off the home once another file was chosen', async () => {
    let refuseFirst: (r: Response) => void = noop;
    const first = new Promise<Response>((resolve) => (refuseFirst = resolve));
    const second = new Promise<Response>(noop);
    await renderApp([() => first, () => second]);
    chooseOnHome(readableFile('first.pdf', 'application/pdf'));
    act(() => (q('.alb-lusion-brand-btn') as HTMLButtonElement).click());
    chooseOnHome(readableFile('second.jpg', 'image/jpeg'));
    refuseFirst(new Response(JSON.stringify({ detail: REFUSAL }), { status: 415 }));
    await settle();
    await settle();
    expect(q(HOME_SCREEN)).toBeNull();
  });

  it.each([
    ['a 5xx answer', refuse(500, 'boom'), 'The upload of "notes.jpg" did not finish: boom.'],
    ['no answer', async () => Promise.reject(new TypeError('Load failed')), 'The upload of "notes.jpg" did not finish: no answer from the Albedian server (Load failed).'],
  ])('stays off the home after %s, which proves no refusal, and says so as main does', async (_label, answer, banner) => {
    await renderApp([answer as Answer]);
    chooseOnHome(readableFile('notes.jpg', 'image/jpeg'));
    await waitUntil(() => expect(q('[data-testid="voice-status-banner"]')?.textContent).toContain(banner));
    expect(q(HOME_SCREEN)).toBeNull();
  });
});
