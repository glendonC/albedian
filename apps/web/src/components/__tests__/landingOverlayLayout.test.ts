import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// The home replaced the landing footer (arrive-home); its layout rules live in home.css
const cssPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../styles/home.css'
);

/**
 * Extracts the body of a top-level CSS rule or @media block matching `header`.
 */
export function extractBlock(css: string, header: string): string | null {
  const idx = css.indexOf(header);
  if (idx < 0) return null;
  const openBrace = css.indexOf('{', idx + header.length);
  if (openBrace < 0) return null;
  let depth = 1;
  for (let i = openBrace + 1; i < css.length; i++) {
    if (css[i] === '{') depth++;
    else if (css[i] === '}') {
      depth--;
      if (depth === 0) return css.slice(openBrace + 1, i);
    }
  }
  return null;
}

/**
 * Extracts declarations (`prop: value`) for `selector` inside a CSS fragment.
 */
export function declarationsFor(fragment: string, selector: string): Record<string, string> {
  const body = extractBlock(fragment, selector);
  if (!body) return {};
  const out: Record<string, string> = {};
  for (const raw of body.split(';')) {
    const colon = raw.indexOf(':');
    if (colon < 0) continue;
    const prop = raw.slice(0, colon).replace(/\/\*[\s\S]*?\*\//g, '').trim();
    const val = raw.slice(colon + 1).replace(/\/\*[\s\S]*?\*\//g, '').trim();
    if (prop && val) out[prop] = val;
  }
  return out;
}

describe('home narrow-width and landscape layout rules', () => {
  const css = fs.readFileSync(cssPath, 'utf8');

  it('keeps the top controls on one line and Upload as wide as its word, placed as the mockup at 1440', () => {
    const top = declarationsFor(css, '.home-top');
    expect(top['flex-wrap']).toBe('nowrap');
    expect(top['gap']).toBe('8px');
    expect(top['right']).toBe('32px');
    expect(top['top']).toBe('26px');

    const hero = declarationsFor(css, '.home-hero');
    expect(hero['justify-items']).toBe('start');
    expect(hero['left']).toBe('48px');
    expect(hero['bottom']).toBe('56px');
    expect(hero['gap']).toBe('36px');

    const logo = declarationsFor(css, '.home-hero .look-logo');
    expect(logo['margin-left']).toBe('-6px');
  });

  it('scales the logo with the window below 900px wide, from 132px down to no less than 56px', () => {
    const media900 = extractBlock(css, '@media (max-width: 900px)');
    expect(media900).not.toBeNull();

    const logo = declarationsFor(media900!, '.home-hero .look-logo');
    expect(logo['--look-logo-fs']).toBe('clamp(56px, 14.6vw, 132px) !important');
  });

  it('uses a 16px gutter, a 24px bottom inset and a 24px gap below 720px, the top controls 16px in', () => {
    const media720 = extractBlock(css, '@media (max-width: 720px)');
    expect(media720).not.toBeNull();

    const top = declarationsFor(media720!, '.home-top');
    expect(top['right']).toBe('16px');
    expect(top['top']).toBe('16px');

    const hero = declarationsFor(media720!, '.home-hero');
    expect(hero['left']).toBe('16px');
    expect(hero['bottom']).toBe('24px');
    expect(hero['gap']).toBe('24px');
  });

  it('compacts the home on short landscape windows below 480px tall', () => {
    const media480h = extractBlock(css, '@media (max-height: 480px)');
    expect(media480h).not.toBeNull();

    const hero = declarationsFor(media480h!, '.home-hero');
    expect(hero['bottom']).toBe('20px');
    expect(hero['gap']).toBe('20px');

    const logo = declarationsFor(media480h!, '.home-hero .look-logo');
    expect(logo['--look-logo-fs']).toBe('clamp(40px, 16vh, 132px) !important');
  });

  it('places the responsive rules after the base hero rule, widest first, the short-window rule last', () => {
    const heroIdx = css.indexOf('.home-hero {');
    const media900Idx = css.indexOf('@media (max-width: 900px)');
    const media720Idx = css.indexOf('@media (max-width: 720px)');
    const media480hIdx = css.indexOf('@media (max-height: 480px)');
    expect(heroIdx).toBeGreaterThan(0);
    expect(media900Idx).toBeGreaterThan(heroIdx);
    expect(media720Idx).toBeGreaterThan(media900Idx);
    expect(media480hIdx).toBeGreaterThan(media720Idx);
  });
});
