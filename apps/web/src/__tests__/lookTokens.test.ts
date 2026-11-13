import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { LOOK_CSS_CUSTOM_PROPERTIES, LOOK_REDUCED_MOTION_CSS_OVERRIDES } from '@albedian/design-system';
import { LOOK_DECLARATIONS, type LookDeclaration } from './lookDeclarations.js';

// The approved controls' tokens live once in @albedian/design-system (look.ts) and once as CSS custom properties.
// These checks keep the two identical and keep look.css on the tokens: no colour of its own, the focus ring, the
// reduced-motion rule, and only classes the parts in apps/web/src/look/ name.

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '..');
const read = (file: string) => fs.readFileSync(path.join(srcRoot, file), 'utf8');
const tokensCss = read('styles/look-tokens.css');
const lookCss = read('styles/look.css');
const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const squash = (value: string) => value.replace(/\s+/g, ' ').trim();

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

/** The custom properties a block declares, by name. */
function declarations(body: string | null): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of withoutComments(body ?? '').split(';')) {
    const colon = raw.indexOf(':');
    if (colon > 0) out[raw.slice(0, colon).trim()] = squash(raw.slice(colon + 1));
  }
  return out;
}

const topRoot = (css: string) => blockBody(withoutComments(css).replace(/@media[^{]*\{[\s\S]*?\}\s*\}/g, ''), /:root\s*\{/);

describe('look-tokens.css matches @albedian/design-system', () => {
  it('declares exactly the package custom properties in its top-level :root', () => {
    expect(declarations(topRoot(tokensCss))).toEqual(
      Object.fromEntries(Object.entries(LOOK_CSS_CUSTOM_PROPERTIES).map(([k, v]) => [k, squash(String(v))]))
    );
  });

  it('overrides exactly the package durations under prefers-reduced-motion, and not the press', () => {
    const media = blockBody(withoutComments(tokensCss), /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/);
    expect(declarations(blockBody(media ?? '', /:root\s*\{/))).toEqual(LOOK_REDUCED_MOTION_CSS_OVERRIDES);
  });
});

describe('every declaration of the two stylesheets, pinned both ways (read with postcss)', () => {
  /** The declarations of a stylesheet as rows of the table, in document order. */
  const rowsOf = (file: LookDeclaration[0]): LookDeclaration[] => {
    const rows: LookDeclaration[] = [];
    postcss.parse(read(`styles/${file}`)).walkDecls((decl) => {
      const chain: string[] = [];
      for (let node = decl.parent; node && node.type !== 'root'; node = node.parent) {
        chain.unshift(node.type === 'rule' ? (node as postcss.Rule).selector.replace(/\s+/g, ' ') : `@${(node as postcss.AtRule).name} ${(node as postcss.AtRule).params}`);
      }
      rows.push([file, chain.join(' '), decl.prop, decl.value.replace(/\s+/g, ' ') + (decl.important ? ' !important' : '')]);
    });
    return rows;
  };
  const inFiles = [...rowsOf('look-tokens.css'), ...rowsOf('look.css')];
  const key = (row: LookDeclaration) => row.join(' | ');
  const tableKeys = new Set(LOOK_DECLARATIONS.map(key));
  const fileKeys = new Set(inFiles.map(key));

  it('finds every declaration of the files in the table, with its value', () => {
    expect(inFiles.map(key).filter((k) => !tableKeys.has(k))).toEqual([]);
  });

  it('finds every row of the table in the files', () => {
    expect(LOOK_DECLARATIONS.map(key).filter((k) => !fileKeys.has(k))).toEqual([]);
  });

  it('holds them once each, in the files\' order', () => {
    expect(inFiles).toEqual(LOOK_DECLARATIONS);
  });

  it.each([
    ['the controls', '.look-main, .look-small-main, .look-second, .look-icon, .look-views__item', '400 var(--look-fs) / 1.3 var(--look-font-sans)'],
    ['the tag', '.look-tag', '400 var(--look-tag-fs) / 1 var(--look-font-sans)'],
  ])('sets the line height of %s as the approved file does', (_label, rule, font) => {
    expect(inFiles.filter((r) => r[1] === rule && r[2] === 'font').map((r) => r[3])).toEqual([font]);
  });

  it('keeps the press scale under reduced motion: the approved file only turns transitions off', () => {
    expect(inFiles.filter((r) => r[2] === '--look-press-scale')).toEqual([['look-tokens.css', ':root', '--look-press-scale', '0.97']]);
  });
});

describe('look.css draws the parts from the tokens', () => {
  const css = withoutComments(lookCss);

  it('has no colour of its own: every colour is a --look-* token', () => {
    expect(css.match(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g) ?? []).toEqual([]);
    const colours = [...css.matchAll(/(?:^|[;{\s])(color|background|background-color|border-color|fill|stroke)\s*:\s*([^;}]+)/g)];
    expect(colours.length).toBeGreaterThan(0);
    for (const [, prop, value] of colours) {
      expect(value!.trim(), prop).toMatch(/^(var\(--look-[a-z0-9-]+\)|transparent|currentColor|none)$/);
    }
  });

  it('draws the keyboard focus ring as a 2 px ink outline, 2 px off, on every part', () => {
    const focus = blockBody(css, /\.look-main:focus-visible[^{]*\{/);
    expect(squash(focus ?? '')).toContain('outline: var(--look-focus-width) solid var(--look-ink)');
    expect(squash(focus ?? '')).toContain('outline-offset: var(--look-focus-offset)');
    const header = /([^{}]*:focus-visible[^{}]*)\{/.exec(css)?.[1] ?? '';
    for (const part of ['.look-main', '.look-small-main', '.look-second', '.look-icon', '.look-views__item']) {
      expect(header).toContain(`${part}:focus-visible`);
    }
  });

  it('times every transition with the motion tokens', () => {
    const transitions = [...css.matchAll(/transition\s*:\s*([^;}]+)/g)].map((m) => m[1]!.trim());
    expect(transitions.length).toBeGreaterThan(0);
    for (const t of transitions) {
      if (t === 'none !important') continue;
      for (const part of t.split(',')) {
        expect(part.trim(), t).toMatch(/var\(--look-dur-(spring|colour|press)\) var\(--look-ease-(spring|standard)\)$/);
      }
    }
  });

  it('turns every transition off under prefers-reduced-motion', () => {
    const media = blockBody(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/);
    expect(media).not.toBeNull();
    expect(squash(media ?? '')).toMatch(/\{ transition: none !important; \}/);
    for (const part of ['.look-main', '.look-small-main', '.look-second', '.look-icon', '.look-views__item', '.look-turn']) {
      expect(media).toContain(part);
    }
  });

  it('sets its type from the token stack', () => {
    for (const m of css.matchAll(/\bfont(?:-family)?\s*:\s*([^;}]+)/g)) expect(m[1]).toMatch(/var\(--look-font-sans\)$/);
  });

  it('styles only classes the parts name', () => {
    const parts = fs
      .readdirSync(path.join(srcRoot, 'look'))
      .filter((f) => f.endsWith('.tsx') || f.endsWith('.ts'))
      .map((f) => read(path.join('look', f)))
      .join('\n');
    const classes = new Set([...css.matchAll(/\.(look-[\w-]+)/g)].map((m) => m[1]!));
    expect(classes.size).toBeGreaterThan(0);
    const unnamed = [...classes].filter((c) => !new RegExp(`(^|[^\\w-])${c}(?![\\w-])`).test(parts));
    expect(unnamed).toEqual([]);
  });

  // The approved stylesheet's geometry and behaviour, rule by rule, with its numbers as tokens where it has one
  const APPROVED: ReadonlyArray<[selector: string, declarations: Record<string, string>]> = [
    ['.look-main__label', { padding: '0 16px', border: '1px solid var(--look-ink)', background: 'var(--look-ink)', color: 'var(--look-night)' }],
    ['.look-main__circle', { width: 'var(--look-h)', height: 'var(--look-h)', border: '1px solid var(--look-line)', 'margin-left': '0' }],
    ['.look-main:hover:not(:disabled) .look-main__label', { 'padding-right': 'calc(var(--look-h) + 16px)' }],
    ['.look-main:hover:not(:disabled) .look-main__circle', { 'margin-left': 'calc(var(--look-h) * -1)', background: 'var(--look-ink)', color: 'var(--look-night)' }],
    ['.look-main:hover:not(:disabled) .look-turn', { transform: 'rotate(0deg)' }],
    ['.look-turn', { transform: 'rotate(var(--look-arrow-start))' }],
    ['.look-small-main', { gap: '10px', padding: '0 4px 0 20px', background: 'var(--look-ink)', color: 'var(--look-night)' }],
    ['.look-small-main__circle', { width: 'calc(var(--look-h) - var(--look-inner-inset))', background: 'var(--look-night)' }],
    ['.look-second', { padding: '0 20px', border: '1px solid var(--look-line)', background: 'transparent' }],
    ['.look-second:hover:not(:disabled)', { background: 'var(--look-wash)' }],
    ['.look-second--with-circle', { gap: '12px', padding: '0 4px 0 20px' }],
    ['.look-second__circle', { width: 'calc(var(--look-h) - var(--look-inner-inset))', background: 'var(--look-ink)' }],
    ['.look-views__item + .look-views__item', { 'margin-left': '-1px' }],
    ['.look-views__item.is-current', { background: 'var(--look-ink)', color: 'var(--look-night)', 'z-index': '1' }],
    ['.look-icon', { width: 'var(--look-h)', border: '1px solid var(--look-line)' }],
    ['.look-icon:hover:not(:disabled)', { background: 'var(--look-ink)', color: 'var(--look-night)' }],
    ['.look-main:active:not(:disabled)', { transform: 'scale(var(--look-press-scale))' }],
    ['.look-main:disabled', { opacity: '0.44', cursor: 'default' }],
    ['.look-tag', { height: 'var(--look-tag-h)', padding: '0 var(--look-tag-pad-end) 0 var(--look-tag-pad-start)', gap: 'var(--look-tag-gap)' }],
    ['.look-main', { height: 'var(--look-h)', 'border-radius': '9999px', 'white-space': 'nowrap' }],
    ['.look-size-compact', { '--look-h': 'var(--look-h-compact)', '--look-fs': 'var(--look-fs-compact)', '--look-ic': 'var(--look-ic-compact)' }],
    ['.look-size-home', { '--look-h': 'var(--look-h-home)', '--look-fs': 'var(--look-fs-home)', '--look-ic': 'var(--look-ic-home)' }],
    ['.look-size-phone', { '--look-h': 'var(--look-h-phone)', '--look-fs': 'var(--look-fs-phone)', '--look-ic': 'var(--look-ic-phone)' }],
  ];
  it.each(APPROVED)('draws %s as the approved stylesheet does', (selector, expected) => {
    // Every rule whose selector list holds this selector, merged in order as the cascade would
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const header = new RegExp(`(?:^|[{},])\\s*(?:[^{}]*,\\s*)?${escaped}\\s*(?:,[^{}]*)?\\{`, 'g');
    const found: Record<string, string> = {};
    for (const match of css.matchAll(header)) {
      // The match ends on the rule's own opening brace
      Object.assign(found, declarations(blockBody(css.slice(match.index + match[0].length - 1), /\{/)));
    }
    for (const [prop, value] of Object.entries(expected)) expect(found[prop], `${selector} ${prop}`).toBe(value);
  });

  it('carries no em or en dash', () => {
    expect(new RegExp(`[${String.fromCharCode(0x2013, 0x2014)}]`).test(tokensCss + lookCss)).toBe(false);
  });
});
