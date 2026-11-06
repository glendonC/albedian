import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const cssPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../styles/albedian.css'
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

describe('landing overlay narrow-width and landscape layout rules', () => {
  const css = fs.readFileSync(cssPath, 'utf8');

  it('allows the landing action buttons to wrap and keeps the secondary button label on one line', () => {
    const actions = declarationsFor(css, '.alb-nazyurtal-actions');
    expect(actions['flex-wrap']).toBe('wrap');

    const secondary = declarationsFor(css, '.alb-nazyurtal-btn-secondary');
    expect(secondary['white-space']).toBe('nowrap');
  });

  it('collapses the two-column footer split to one column below 900px and clears the right column bounds', () => {
    const media900 = extractBlock(css, '@media (max-width: 900px)');
    expect(media900).not.toBeNull();

    const split = declarationsFor(media900!, '.alb-nazyurtal-split');
    expect(split['grid-template-columns']).toBe('1fr');
    expect(split['gap']).toBe('16px');

    const right = declarationsFor(media900!, '.alb-nazyurtal-right');
    expect(right['max-width']).toBe('none');
    expect(right['min-height']).toBe('0');
  });

  it('uses a 16px horizontal gutter, a 24px bottom inset, a scaled headline and flexible buttons below 720px', () => {
    const media720 = extractBlock(css, '@media (max-width: 720px)');
    expect(media720).not.toBeNull();

    const header = declarationsFor(media720!, '.alb-lusion-header');
    expect(header['padding']).toBe('20px 16px');

    const footer = declarationsFor(media720!, '.alb-nazyurtal-footer');
    expect(footer['left']).toBe('16px');
    expect(footer['right']).toBe('16px');
    expect(footer['bottom']).toBe('24px');

    const headline = declarationsFor(media720!, '.alb-nazyurtal-headline');
    expect(headline['font-size']).toBe('clamp(32px, 9vw, 46px)');

    const buttons = declarationsFor(
      media720!,
      '.alb-nazyurtal-btn-primary,\n  .alb-nazyurtal-btn-secondary'
    );
    expect(buttons['flex']).toBe('1 1 auto');
  });

  it('compacts the landing footer and headline on short landscape viewports below 480px tall', () => {
    const media480h = extractBlock(css, '@media (max-height: 480px)');
    expect(media480h).not.toBeNull();

    const footer = declarationsFor(media480h!, '.alb-nazyurtal-footer');
    expect(footer['bottom']).toBe('16px');

    const split = declarationsFor(media480h!, '.alb-nazyurtal-split');
    expect(split['grid-template-columns']).toBe('1fr 1fr');
    expect(split['gap']).toBe('24px');

    const headline = declarationsFor(media480h!, '.alb-nazyurtal-headline');
    expect(headline['font-size']).toBe('clamp(24px, 6vh, 32px)');
  });

  it('places the landing responsive rules immediately after the secondary button hover block, before the optics drawer', () => {
    const afterHover = css.indexOf('.alb-nazyurtal-btn-secondary:hover');
    const media900Idx = css.indexOf('@media (max-width: 900px)');
    const drawerIdx = css.indexOf('.alb-zzzz-optics-drawer');
    expect(afterHover).toBeGreaterThan(0);
    expect(media900Idx).toBeGreaterThan(afterHover);
    expect(media900Idx).toBeLessThan(drawerIdx);
  });
});
