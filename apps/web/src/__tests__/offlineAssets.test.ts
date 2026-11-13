import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Away from home the laptop may have no internet, or a link that never answers. A stylesheet from another
// host in <head>, or an @import of one, holds the first paint until the browser gives up on it. Until web-fonts-1
// the web fonts came from a font host through a link that started as media="print" and switched itself on when it
// loaded, with a noscript copy. The app now serves its own faces (styles/fonts.css, public/fonts/geist), so the page
// loads no stylesheet from another host at all, and each face is swapped in when it loads (font-display: swap), so
// the first paint never waits on a font.

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '../..');
const srcRoot = path.resolve(here, '..');

function cssFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : cssFiles(full);
    return entry.name.endsWith('.css') ? [full] : [];
  });
}

function linkTags(html: string): string[] {
  return html.match(/<link\b[^>]*>/gi) ?? [];
}

function attr(tag: string, name: string): string | undefined {
  const match = tag.match(new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i'));
  return match ? (match[1] ?? match[2]) : undefined;
}

const isExternal = (href: string | undefined): boolean => href !== undefined && /^(https?:)?\/\//i.test(href);

describe('offline first paint', () => {
  const indexHtml = fs.readFileSync(path.join(webRoot, 'index.html'), 'utf8');

  it('loads no stylesheet from another host in index.html, noscript copies included', () => {
    const stylesheets = linkTags(indexHtml).filter((tag) => /\bstylesheet\b/i.test(attr(tag, 'rel') ?? ''));
    expect(stylesheets.filter((tag) => isExternal(attr(tag, 'href')))).toEqual([]);
  });

  it("keeps no deferred stylesheet and no noscript copy; every face is the app's own, swapped in", () => {
    expect(linkTags(indexHtml).filter((tag) => attr(tag, 'media') === 'print')).toEqual([]);
    expect(indexHtml).not.toMatch(/<noscript\b/i);
    const faces = cssFiles(srcRoot).flatMap((file) =>
      [...fs.readFileSync(file, 'utf8').matchAll(/@font-face\s*\{([^}]*)\}/g)].map((m): [string, string] => [
        path.relative(webRoot, file),
        m[1] ?? '',
      ])
    );
    expect(faces.length).toBeGreaterThan(0);
    for (const [file, body] of faces) {
      expect(body, file).toMatch(/font-display:\s*swap;/);
      const sources = [...body.matchAll(/url\(\s*['"]?([^'")]+)/g)].map((m) => m[1]);
      expect(sources.length, file).toBeGreaterThan(0);
      for (const src of sources) expect(src, file).toMatch(/^\/(?!\/)/);
    }
  });

  it('has no @import of another host in any stylesheet under apps/web/src', () => {
    const files = cssFiles(srcRoot);
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const css = fs.readFileSync(file, 'utf8');
      expect(css, path.relative(webRoot, file)).not.toMatch(/@import\s+(url\(\s*)?['"]?(https?:)?\/\//i);
    }
  });
});
