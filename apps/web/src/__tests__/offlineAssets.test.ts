import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Away from home the laptop may have no internet, or a link that never answers. A stylesheet from another
// host in <head>, or an @import of one, holds the first paint until the browser gives up on it. Web fonts may
// still come from a font host, but only through a link that starts as media="print" and switches itself on
// when it loads, so the page paints with system fonts first and never waits on the network.

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

function withoutNoscript(html: string): string {
  return html.replace(/<noscript>[\s\S]*?<\/noscript>/gi, '');
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

  it('loads every external stylesheet in index.html without blocking the first paint', () => {
    const external = linkTags(withoutNoscript(indexHtml)).filter(
      (tag) => /\bstylesheet\b/i.test(attr(tag, 'rel') ?? '') && isExternal(attr(tag, 'href'))
    );
    expect(external.length).toBeGreaterThan(0);
    for (const tag of external) {
      expect(attr(tag, 'media'), tag).toBe('print');
      expect(attr(tag, 'onload'), tag).toBe("this.media='all'");
    }
  });

  it('keeps a noscript copy of each deferred font stylesheet', () => {
    const deferred = linkTags(withoutNoscript(indexHtml))
      .filter((tag) => attr(tag, 'media') === 'print' && isExternal(attr(tag, 'href')))
      .map((tag) => attr(tag, 'href'));
    const noscript = (indexHtml.match(/<noscript>[\s\S]*?<\/noscript>/gi) ?? []).join('');
    const fallback = linkTags(noscript).map((tag) => attr(tag, 'href'));
    expect(deferred.length).toBeGreaterThan(0);
    for (const href of deferred) expect(fallback).toContain(href);
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
