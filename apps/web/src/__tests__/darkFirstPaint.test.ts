import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// dark-first-paint-one version two (DECIDED-BY-LEAD 18:32 on 24 Nov, MOBILE's break: Open on the phone's result showed a
// full white page for about a third of a second before the dark reveal painted; the lead's 19:25 line after version one
// changed the page's inline style, which the content security policy holds to its hash): the page declares itself dark
// in its color-scheme meta alone, before anything loads, and its one inline style stays exactly as the policy has it.

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.resolve(here, '../../index.html'), 'utf8');
const head = html.slice(0, html.indexOf('</head>'));
const inlineCss = /<style>([\s\S]*?)<\/style>/.exec(head)?.[1] ?? '';
// The content security policy's own list of style hashes (scripts/content-policy.mjs), and its hash as a browser takes it
const policy = fs.readFileSync(path.resolve(here, '../../../../scripts/content-policy.mjs'), 'utf8');
const STYLE_HASHES = JSON.parse(/export const STYLE_HASHES = (\[[^\]]*\]);/.exec(policy)![1]!) as string[];
const styleHash = (text: string) => `'sha256-${createHash('sha256').update(text, 'utf8').digest('base64')}'`;

describe('the page is dark before anything loads', () => {
  it('declares a dark colour scheme in its head, before any style or script', () => {
    const meta = /<meta\s+name="color-scheme"\s+content="dark"\s*\/?>/.exec(head);
    expect(meta).not.toBeNull();
    const at = meta!.index;
    for (const later of ['<style', '<script', '<link rel="stylesheet"']) {
      const where = head.indexOf(later);
      if (where >= 0) expect(at, later).toBeLessThan(where);
    }
  });

  it("leaves the page's one inline style exactly as the content security policy's hash holds it", () => {
    const styles = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi)].map((m) => m[1]!);
    expect(styles).toHaveLength(1);
    expect(styles.map(styleHash)).toEqual(STYLE_HASHES);
    // The colour scheme is the meta's alone: the inline style declares none
    expect(inlineCss).not.toMatch(/color-scheme/);
  });
});
