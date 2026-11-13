import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  HUD_CSS_CUSTOM_PROPERTIES,
  HUD_FONTS,
  HUD_REDUCED_MOTION_CSS_OVERRIDES,
} from '@albedian/design-system';

// The HUD tokens live once in @albedian/design-system and once as CSS custom properties. These checks keep the two
// identical, keep albedian.css and index.html on the same Geist stacks, and keep the retired families out.

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '../..');
const srcRoot = path.resolve(here, '..');
const read = (file: string) => fs.readFileSync(file, 'utf8');
const hudCss = read(path.join(srcRoot, 'styles/hud-tokens.css'));
const albedianCss = read(path.join(srcRoot, 'styles/albedian.css'));
const indexHtml = read(path.join(webRoot, 'index.html'));

const RETIRED_FAMILIES = /\b(Inter|Exo 2|IBM Plex Mono|JetBrains Mono|Space Grotesk)\b/;
const squash = (value: string) => value.replace(/\s+/g, ' ').trim();
const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/** The body of the first block whose header matches, counting nested braces. */
function blockBody(css: string, header: RegExp): string | null {
  const match = header.exec(css);
  if (!match) return null;
  const open = css.indexOf('{', match.index + match[0].length - 1);
  let depth = 1;
  for (let i = open + 1; i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}' && --depth === 0) return css.slice(open + 1, i);
  }
  return null;
}

/** Top-level text with every @media block removed. */
function topLevel(css: string): string {
  let out = css;
  for (;;) {
    const start = out.search(/@media\b/);
    if (start < 0) return out;
    const body = blockBody(out.slice(start), /@media[^{]*\{/);
    const end = out.indexOf(body ?? '', start) + (body?.length ?? 0) + 1;
    out = out.slice(0, start) + out.slice(end);
  }
}

function declarations(body: string, prefix: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of body.split(';')) {
    const colon = raw.indexOf(':');
    if (colon < 0) continue;
    const name = raw.slice(0, colon).trim();
    if (name.startsWith(prefix)) out[name] = squash(raw.slice(colon + 1));
  }
  return out;
}

const squashAll = (map: Record<string, string>) =>
  Object.fromEntries(Object.entries(map).map(([name, value]) => [name, squash(value)]));

function cssFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' || entry.name === '__tests__' ? [] : cssFiles(full);
    return entry.name.endsWith('.css') ? [full] : [];
  });
}

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' || entry.name === '__tests__' ? [] : sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

/** Every link in the page, noscript copies included, that asks a font host for a stylesheet. */
function fontLinkHrefs(html: string): string[] {
  return (html.match(/<link\b[^>]*>/gi) ?? [])
    .map((tag) => tag.match(/\bhref\s*=\s*"([^"]*)"/i)?.[1] ?? '')
    .filter((href) => href.startsWith('https://fonts.googleapis.com/css2'));
}

describe('hud-tokens.css matches @albedian/design-system', () => {
  it('declares exactly the package custom properties in its top-level :root', () => {
    const body = blockBody(topLevel(withoutComments(hudCss)), /:root\s*\{/);
    expect(body).not.toBeNull();
    expect(declarations(body ?? '', '--')).toEqual(squashAll(HUD_CSS_CUSTOM_PROPERTIES));
  });

  it('zeroes exactly the package durations under prefers-reduced-motion', () => {
    const media = blockBody(withoutComments(hudCss), /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/);
    expect(media).not.toBeNull();
    const body = blockBody(media ?? '', /:root\s*\{/);
    expect(declarations(body ?? '', '--')).toEqual(squashAll(HUD_REDUCED_MOTION_CSS_OVERRIDES));
  });
});

describe('albedian.css takes its type from the HUD tokens', () => {
  it('imports hud-tokens.css before anything else', () => {
    expect(withoutComments(albedianCss).trimStart().startsWith("@import './hud-tokens.css';")).toBe(true);
  });

  it('points its font variables at the HUD stacks', () => {
    const body = blockBody(topLevel(withoutComments(albedianCss)), /:root\s*\{/);
    const fonts = declarations(body ?? '', '--alb-font');
    expect(fonts).toEqual({
      '--alb-font-sans': 'var(--hud-font-sans)',
      '--alb-font-mono': 'var(--hud-font-mono)',
      '--alb-font-micro': 'var(--hud-font-mono)',
    });
  });
});

describe('index.html loads Geist and nothing retired', () => {
  // Until web-fonts-1 index.html asked a font host for Geist; the app now serves its own faces (styles/fonts.css,
  // held whole by fontsRecord.test.ts), so the page asks no font host and the stacks lead with the faces it serves.
  it("asks no font host; the stacks lead with the app's own faces, Geist and Geist Mono", () => {
    expect(fontLinkHrefs(indexHtml)).toEqual([]);
    expect(indexHtml).not.toMatch(/fonts\.(googleapis|gstatic)\.com/i);
    const fontsCss = withoutComments(read(path.join(srcRoot, 'styles/fonts.css')));
    const families = [...fontsCss.matchAll(/@font-face\s*\{[^}]*font-family:\s*'([^']+)'/g)].map((m) => m[1]);
    expect(families).toEqual(['Geist', 'Geist Mono']);
    expect(HUD_FONTS.sans.startsWith(`'${families[0]}',`)).toBe(true);
    expect(HUD_FONTS.mono.startsWith(`'${families[1]}',`)).toBe(true);
  });

  it('sets every inline font-family to a HUD stack', () => {
    const values = [...indexHtml.matchAll(/font-family:\s*([^;]+);/g)].map((m) => squash(m[1] ?? ''));
    expect(values.length).toBeGreaterThan(0);
    for (const value of values) expect([HUD_FONTS.sans, HUD_FONTS.mono]).toContain(value);
  });

  it('carries no em dash', () => {
    expect(indexHtml).not.toContain('\u2014');
  });

  it("is titled with the product's name alone, in one title element", () => {
    expect([...indexHtml.matchAll(/<title\b[^>]*>([\s\S]*?)<\/title>/gi)].map((m) => m[1])).toEqual(['Albedian']);
  });

  it('names no retired family in any stylesheet, font variable or inline font', () => {
    for (const href of fontLinkHrefs(indexHtml)) expect(decodeURIComponent(href).replace(/\+/g, ' ')).not.toMatch(RETIRED_FAMILIES);
    const sheets: Array<[string, string]> = [
      ['index.html', indexHtml],
      ...cssFiles(srcRoot).map((file): [string, string] => [path.relative(webRoot, file), read(file)]),
    ];
    for (const [name, text] of sheets) {
      for (const m of text.matchAll(/(font-family|--[a-z-]*font[a-z-]*)\s*:\s*([^;]+);/g)) {
        expect(m[2], `${name}: ${m[1]}`).not.toMatch(RETIRED_FAMILIES);
      }
    }
    for (const file of sourceFiles(srcRoot)) {
      for (const m of read(file).matchAll(/(fontFamily\s*:|\.font\s*=)\s*(['"`])([^'"`]*)\2/g)) {
        expect(m[3], path.relative(webRoot, file)).not.toMatch(RETIRED_FAMILIES);
      }
    }
  });
});
