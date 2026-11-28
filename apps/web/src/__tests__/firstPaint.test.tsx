// @vitest-environment jsdom
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import postcss from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SAMPLE_ID_PATTERN } from '@albedian/contracts';

vi.mock('../audio/ObservatoryAudioEngine.js', () => ({
  observatoryAudio: { toggle: vi.fn(() => false), setMasterVolume: vi.fn(), setScaleRegime: vi.fn(), playDetentClick: vi.fn() },
}));
vi.mock('../audio/SkyAudioFeed.js', () => ({ skyAudio: { follow: vi.fn(), setEvidenceClass: vi.fn() } }));
vi.mock('../components/CoalescingBar.js', () => ({ CoalescingBar: () => null }));
const { HomeScreen } = await import('../components/HomeScreen.js');

// first-paint-one (UIUX-first-paint-READY.md P1 to P5, DECIDED-BY-LEAD 20:44 on 27 Nov): before the script runs the page
// shows the home's first frame as far as it can be drawn without it: the home's black and its logo at the place, size
// and colour the home draws it (the home's own markup and rules, so nothing moves when the home takes over), the logo
// only in Geist and only at the home's address, and nothing else: no word, no button, no spinner.

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '../..');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const read = (file: string) => fs.readFileSync(path.join(web, file), 'utf8');
const page = new DOMParser().parseFromString(html, 'text/html');
const frame = () => page.getElementById('first-frame');
/** The page's one inline style, the only stylesheet the first frame may stand on. */
const inline = page.querySelector('head style')!.textContent!;
/**
 * first-frame-inline-one (DECIDED-BY-LEAD 02:59 on 28 Nov: no provenance record fits a file written here, so the script
 * is the page's own): the first frame's one script, inline in the page, and the content policy's hashes of inline scripts.
 */
const firstFrameScript = () => [...page.querySelectorAll('script')].filter((s) => !s.hasAttribute('src'));
const policy = fs.readFileSync(path.resolve(web, '../../scripts/content-policy.mjs'), 'utf8');
const SCRIPT_HASHES = JSON.parse(/export const SCRIPT_HASHES = (\[[^\]]*\]);/.exec(policy)?.[1] ?? '[]') as string[];
const scriptHash = (text: string) => `'sha256-${createHash('sha256').update(text, 'utf8').digest('base64')}'`;
/** The markup of an element with the mask ids made alike and the attributes a static page holds differently set aside. */
const alike = (el: Element, drop: readonly string[] = []) => {
  const copy = el.cloneNode(true) as Element;
  for (const node of [copy, ...copy.querySelectorAll('*')]) for (const name of drop) node.removeAttribute(name);
  return copy.outerHTML.replace(/look-logo-mask-[^"')]+/g, 'look-logo-mask');
};
/** A rule's declarations in a stylesheet, at its root or inside the @media whose condition is given. */
function rulesOf(css: string, selector: string, media: string | null = null): Record<string, string> {
  const found: Record<string, string> = {};
  postcss.parse(css).walkRules((rule) => {
    if (rule.selector.replace(/\s+/g, ' ') !== selector) return;
    const parent = rule.parent as { type: string; name?: string; params?: string } | undefined;
    const at = parent?.type === 'atrule' && parent.name === 'media' ? parent.params! : null;
    if (at !== media) return;
    rule.walkDecls((decl) => {
      found[decl.prop] = decl.value.replace(/\s+/g, ' ');
    });
  });
  return found;
}
const token = (name: string) => rulesOf(read('src/styles/look-tokens.css'), ':root')[name]!;
const app = (file: string, selector: string, media: string | null = null) => rulesOf(read(file), selector, media);
const own = (selector: string, media: string | null = null) => rulesOf(inline, selector, media);
const unimportant = (value: string) => value.replace(/\s*!important$/, '');

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
});
function homeHero(): Element {
  act(() => root.render(createElement(HomeScreen, { soundOn: false, onToggleSound: () => {}, onFileChosen: () => {} })));
  return container.querySelector('.home-hero')!;
}

describe("first-paint-one: the page's own first frame", () => {
  it("stands in the app's root, where the home's first commit replaces it: the home's black and its hero only", () => {
    const f = frame()!;
    expect(f.parentElement?.id).toBe('root');
    expect(page.getElementById('root')!.children).toHaveLength(1);
    expect(f.className).toBe('home-screen first-frame');
    expect([...f.children].map((c) => c.className)).toEqual(['home-hero']);
    expect([...f.querySelector('.home-hero')!.children].map((c) => c.className)).toEqual(['look-logo', 'home-row']);
  });

  it("carries the home's own logo, letter for letter (P1)", () => {
    const logo = homeHero().querySelector(':scope > .look-logo')!;
    expect(logo.getAttribute('style')).toBe('--look-logo-fs: 132px;');
    expect(alike(frame()!.querySelector('.look-logo')!)).toBe(alike(logo, ['style']));
  });

  it("holds only Upload's box under the logo, empty and hidden from reading, so nothing in it can be pressed (P1, P3)", () => {
    const row = frame()!.querySelector('.home-row')!;
    expect(row.getAttribute('aria-hidden')).toBe('true');
    expect(row.children).toHaveLength(0);
    expect(row.textContent).toBe('');
  });

  it('draws nothing else: one word, no script inside it, no style attribute (P3)', () => {
    const f = frame()!;
    expect(f.textContent!.replace(/\s+/g, '')).toBe('albedian');
    expect(f.querySelectorAll('[style], script, button, a, input').length).toBe(0);
  });

  it("stands on the page's own style alone, with no stylesheet loaded: black, in place, its logo hidden until data-logo", () => {
    const iframe = document.createElement('iframe');
    document.body.appendChild(iframe);
    const doc = iframe.contentDocument!;
    doc.open();
    doc.write(html);
    doc.close();
    // No stylesheet but the page's own: no link to one in the page as served (the plain start's page has none)
    expect(doc.querySelectorAll('link[rel="stylesheet"]').length).toBe(0);
    const view = iframe.contentWindow!;
    const f = doc.getElementById('first-frame')!;
    const hero = f.querySelector('.home-hero')!;
    expect([view.getComputedStyle(f).position, view.getComputedStyle(f).backgroundColor]).toEqual(['fixed', 'rgb(0, 0, 0)']);
    expect([view.getComputedStyle(hero).position, view.getComputedStyle(hero).left, view.getComputedStyle(hero).bottom]).toEqual(['absolute', '48px', '56px']);
    expect(view.getComputedStyle(hero).visibility).toBe('hidden');
    f.setAttribute('data-logo', '');
    expect(view.getComputedStyle(hero).visibility).toBe('visible');
    expect(view.getComputedStyle(f.querySelector('.look-logo__mark')!).display).toBe('block');
    iframe.remove();
  });

  it("copies every value from the app rule it mirrors, so the frame and the home cannot drift apart (P1)", () => {
    // home.css .home-screen and .home-hero, at the laptop's size
    const screen = app('src/styles/home.css', '.home-screen');
    expect(own('.first-frame')).toEqual({ position: screen.position, inset: screen.inset, 'z-index': screen['z-index'], overflow: screen.overflow, background: token('--look-field') });
    const hero = app('src/styles/home.css', '.home-hero');
    expect(own('.first-frame .home-hero')).toEqual(hero);
    expect(own('.first-frame:not([data-logo]) .home-hero')).toEqual({ visibility: 'hidden' });
    // look.css .look-logo with its tokens, at the home's 132 px (HomeScreen's Logo sizePx), its place from home.css
    const logo = app('src/styles/look.css', '.look-logo');
    expect(own('.first-frame .look-logo')).toEqual({
      '--first-frame-fs': '132px',
      display: logo.display,
      'align-items': logo['align-items'],
      'margin-left': app('src/styles/home.css', '.home-hero .look-logo')['margin-left'],
      color: token('--look-logo-ink'),
    });
    expect(own('.first-frame .look-logo__word')).toEqual({
      font: `${token('--look-logo-weight')} var(--first-frame-fs) / 1 ${token('--look-font-sans')}`,
      'letter-spacing': token('--look-logo-tracking'),
    });
    const mark = (k: string) => `calc(var(--first-frame-fs) * ${token(k)})`;
    expect(own('.first-frame .look-logo__mark')).toEqual({
      display: 'block',
      flex: 'none',
      width: mark('--look-logo-mark-scale'),
      height: mark('--look-logo-mark-scale'),
      'margin-left': mark('--look-logo-mark-left'),
      'margin-top': mark('--look-logo-mark-up'),
    });
    expect(app('src/styles/look.css', '.look-logo__mark')).toMatchObject({ display: 'block', flex: 'none' });
    // Upload's height: the home size's on the laptop, the phone size's at phone width (home.css's .home-hero .look-main)
    expect(own('.first-frame .home-row')).toEqual({ height: token('--look-h-home') });
    expect(app('src/styles/home.css', '.home-hero .look-main', '(max-width: 720px)')['--look-h']).toBe('var(--look-h-phone)');
    expect(own('.first-frame .home-row', '(max-width: 720px)')).toEqual({ height: token('--look-h-phone') });
    // The narrow and short windows, in the same order as home.css, so the same one wins where two hold
    for (const media of ['(max-width: 900px)', '(max-width: 720px)', '(max-height: 480px)']) {
      const theirs = app('src/styles/home.css', '.home-hero .look-logo', media);
      const mine = own('.first-frame .look-logo', media);
      expect(mine['--first-frame-fs'], media).toBe(unimportant(theirs['--look-logo-fs']!));
      if (theirs['margin-left']) expect(mine['margin-left'], media).toBe(theirs['margin-left']);
    }
    expect(own('.first-frame .home-hero', '(max-width: 720px)')).toEqual(app('src/styles/home.css', '.home-hero', '(max-width: 720px)'));
    expect(own('.first-frame .home-hero', '(max-height: 480px)')).toEqual(app('src/styles/home.css', '.home-hero', '(max-height: 480px)'));
    const order = (css: string) => [...css.matchAll(/@media \(max-(?:width|height): \d+px\)/g)].map((m) => m[0]).filter((m) => ['@media (max-width: 900px)', '@media (max-width: 720px)', '@media (max-height: 480px)'].includes(m));
    expect([...new Set(order(inline))]).toEqual(['@media (max-width: 900px)', '@media (max-width: 720px)', '@media (max-height: 480px)']);
    // fonts.css's Geist, so the face is known before any app stylesheet arrives (P2)
    expect(rulesOf(inline, '@font-face')).toEqual({});
    const face = (css: string) => /@font-face\s*\{[^}]*font-family:\s*'Geist';[^}]*\}/.exec(css)![0].replace(/\s+/g, ' ');
    expect(face(inline)).toBe(face(read('src/styles/fonts.css')));
  });

  it("asks for Geist early from its own origin, and runs its one inline script after the frame, before the app's", () => {
    const preload = page.querySelector('head link[rel="preload"][as="font"]')!;
    expect(preload.getAttribute('href')).toBe('/fonts/geist/Geist-wght.woff2');
    expect(preload.getAttribute('type')).toBe('font/woff2');
    expect(preload.hasAttribute('crossorigin')).toBe(true);
    const scripts = [...page.querySelectorAll('script')];
    const inlineScripts = firstFrameScript();
    expect(inlineScripts).toHaveLength(1);
    const first = inlineScripts[0]!;
    // A classic script, run as the parser reaches it: after the frame's markup, before the app's own module
    expect(first.getAttribute('type')).toBeNull();
    expect(page.getElementById('root')!.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const app = scripts.find((s) => s.getAttribute('src') === '/src/main.tsx')!;
    expect(first.compareDocumentPosition(app) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Nothing is fetched for it, and no copy of it stands in the public folder, where a file needs a provenance record
    expect(scripts.some((s) => /first-frame/.test(s.getAttribute('src') ?? ''))).toBe(false);
    expect(fs.existsSync(path.join(web, 'public/first-frame.js'))).toBe(false);
  });

  it("holds the inline script to the content policy's one script hash, so a change to it is a change to the policy", () => {
    expect(SCRIPT_HASHES).toEqual([scriptHash(firstFrameScript()[0]!.textContent!)]);
  });
});

describe("the first frame's script: the logo only in Geist and only at the home's address", () => {
  const SHA = 'a'.repeat(64);
  async function run(search: string, fonts: 'loads' | 'unmatched' | 'fails' | 'none') {
    document.body.innerHTML = '<div id="root"><div id="first-frame" class="home-screen first-frame"></div></div>';
    window.history.replaceState(null, '', `/${search}`);
    const load = vi.fn((font: string) => (fonts === 'loads' ? Promise.resolve([font]) : fonts === 'unmatched' ? Promise.resolve([]) : Promise.reject(new Error('no font'))));
    Object.defineProperty(document, 'fonts', { configurable: true, value: fonts === 'none' ? undefined : { load } });
    // The page's own inline script, run as the page runs it: as a classic script's text in the page's global scope
    new Function(firstFrameScript()[0]!.textContent!)();
    await new Promise((r) => setTimeout(r, 0));
    return { home: document.getElementById('first-frame')!.hasAttribute('data-logo'), load };
  }
  afterEach(() => {
    window.history.replaceState(null, '', '/');
    delete (document as unknown as { fonts?: unknown }).fonts;
  });

  it("shows the logo at the home's address once Geist 600 has loaded", async () => {
    const { home, load } = await run('', 'loads');
    expect(home).toBe(true);
    expect(load.mock.calls[0]![0]).toMatch(/^600 \d+px Geist$/);
  });

  it('keeps the logo out while Geist cannot be had, or where the browser cannot say', async () => {
    expect((await run('', 'fails')).home).toBe(false);
    // Resolved with no face: no Geist declared yet, so it would be a fallback face
    expect((await run('', 'unmatched')).home).toBe(false);
    expect((await run('', 'none')).home).toBe(false);
  });

  it("keeps it out at an upload's address, as openUpload.ts reads one, and shows it where the value is no upload", async () => {
    expect((await run(`?upload=${SHA}`, 'loads')).home).toBe(false);
    expect((await run('?upload=nothing', 'loads')).home).toBe(true);
    expect((await run(`?upload=${SHA}&upload=${SHA}`, 'loads')).home).toBe(true);
    expect((await run(`?upload=${SHA.toUpperCase()}`, 'loads')).home).toBe(true);
  });

  it("keeps it out at a sample night's address too, as App.tsx reads one where no upload is named, and shows it where none is", async () => {
    expect((await run('?sample=night-starry-sky-2', 'loads')).home).toBe(false);
    expect((await run(`?upload=nothing&sample=night-starry-sky-2`, 'loads')).home).toBe(false);
    expect((await run('?sample=Night-starry', 'loads')).home).toBe(true);
    expect((await run('?sample=night-a&sample=night-b', 'loads')).home).toBe(true);
    expect((await run(`?sample=night-${'a'.repeat(41)}`, 'loads')).home).toBe(true);
  });

  it("reads a sample night's id by the contract's own pattern", () => {
    expect(firstFrameScript()[0]!.textContent!).toContain(`/${SAMPLE_ID_PATTERN.source}/.test(`);
  });
});
