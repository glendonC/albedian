// @vitest-environment jsdom
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';
import { describe, expect, it } from 'vitest';
import { AT_RULES_READ, UnreadForm, deadClasses, filesUnder, htmlNames, htmlStyles, refusalOf, sourceNames, stylesheetClasses } from './classGuard.js';

// The guard (classGuard.ts says its rule, how it reads, and what it does not do): every class a stylesheet of the web
// app names appears whole in a string of the web's sources. These rows say each part of the rule in its own words;
// the rows marked K and L are the cases of the two readers who judged the first guard, with the verdict each gets now.

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '..');
const webRoot = path.resolve(srcRoot, '..');

// Made inside the tests, so a wrong list fails an assertion rather than the file's loading
const stylesheets = () => filesUnder(srcRoot, (f) => f.endsWith('.css'));
const sourceFiles = () =>
  filesUnder(srcRoot, (f) => /\.(ts|tsx)$/.test(f) && !f.split('/').includes('__tests__') && !/\.test\.(ts|tsx)$/.test(f));
const pageStyles = () => htmlStyles(fs.readFileSync(path.join(webRoot, 'index.html'), 'utf8'));
/** Every stylesheet the main check reads: the files, then each of the page's inline style elements. */
const sheetsChecked = (): Array<[string, string]> => [
  ...stylesheets().map((file): [string, string] => [file, fs.readFileSync(path.join(srcRoot, file), 'utf8')]),
  ...pageStyles().map((css, i): [string, string] => [`index.html <style> ${i + 1}`, css]),
];
const usedNames = () =>
  new Set<string>([
    ...sourceFiles().flatMap((f) => [...sourceNames(fs.readFileSync(path.join(srcRoot, f), 'utf8'), f)]),
    ...htmlNames(fs.readFileSync(path.join(webRoot, 'index.html'), 'utf8')),
  ]);

describe('every class the web app styles is used whole by its sources', () => {
  it("styles no class that no source names, in the five files and the page's inline style (each named with its dead classes)", () => {
    const used = usedNames();
    const dead = sheetsChecked().map(([name, css]) => [name, deadClasses(stylesheetClasses(css, name), used)] as const).filter(([, names]) => names.length > 0);
    expect(Object.fromEntries(dead)).toEqual({});
  });

  it("reads the page's own inline style, shipped with every load", () => {
    expect(pageStyles()).toHaveLength(1);
    expect(sheetsChecked().map(([name]) => name)).toEqual([...stylesheets(), 'index.html <style> 1']);
    // A dead class written there fails like one in a file (the reader's probe)
    const probe = htmlStyles('<html><head><style>.alb-audit-dead-inline { color: red; }</style></head></html>');
    expect(probe.flatMap((css) => deadClasses(stylesheetClasses(css, 'index.html <style> 1'), usedNames()))).toEqual(['alb-audit-dead-inline']);
  });

  it('finds every stylesheet by walking the source folder, so a new one is guarded without a list', () => {
    expect(stylesheets()).toEqual(expect.arrayContaining(['styles/albedian.css', 'styles/hud-tokens.css', 'styles/look.css', 'styles/look-tokens.css', 'styles/home.css']));
    expect(stylesheets().every((f) => f.endsWith('.css'))).toBe(true);
  });

  it('reads the sources the app ships, not its tests', () => {
    expect(sourceFiles()).toContain('components/CelestialTargetCard.tsx');
    expect(sourceFiles().some((f) => f.includes('__tests__') || f.includes('.test.'))).toBe(false);
  });
});

type Classes = string[] | 'postcss refuses it' | 'refused';
const classesOf = (css: string): Classes => {
  try {
    return [...stylesheetClasses(css, 'x.css')].sort();
  } catch (error) {
    if ((error as Error).name === 'CssSyntaxError') return 'postcss refuses it';
    if (error instanceof UnreadForm) return 'refused';
    throw error;
  }
};

describe('the stylesheet side: postcss and postcss-selector-parser', () => {
  const ROWS: Array<[label: string, css: string, classes: Classes]> = [
    // The rule in its own words
    ['a class in a rule counts', '.a { }', ['a']],
    ['every class of a compound and every combinator counts', '.a > .b ~ .c + .d .e { }', ['a', 'b', 'c', 'd', 'e']],
    ['a class under :not() counts: no reasoning about it', '.btn:not(.is-off) { }', ['btn', 'is-off']],
    ['a class in a nested rule counts', '.p { .c { } }', ['c', 'p']],
    ['rules inside @keyframes are skipped by their parent node', '@keyframes k { from { } 50.5% { } }', []],
    ['a vendor keyframes block is refused: no stylesheet uses one', '@-webkit-keyframes k { 50.5% { } } .w { }', 'refused'],
    ['a class inside a keyframes block is no stop and styles nothing: skipped', '@keyframes k { .not-a-stop { } }', []],
    ['so is one inside an upper-case keyframes block', '@KEYFRAMES k { .not-a-stop { } }', []],
    ['and a vendor keyframes block holding a class is refused too', '@-webkit-keyframes k { .not-a-stop { } }', 'refused'],
    ['a class in a rule inside @media is not skipped', '@media (max-width: 720px) { .m { } }', ['m']],
    ['an @scope prelude is refused', '@scope (.card) { .title { } }', 'refused'],
    ['an attribute selector on class is refused', '[class~="alb-z"] { }', 'refused'],
    ['an attribute selector on any other attribute names no class', '[data-active="true"] .b { }', ['b']],
    ['an id is not a class', '#i.c { }', ['c']],
    ['a stylesheet postcss cannot parse fails', '.a { color: red', 'postcss refuses it'],
    // AUDIT K's cases
    ['K: a comment holding a rule', '/* .gone { } */ .kept { }', ['kept']],
    ['K: a comment between selectors', '.a/* .x */.b { }', ['a', 'b']],
    ['K: @import with a semicolon in its url (not the plain form)', '@import url("a;b.css");\n.real { }', 'refused'],
    ['K: @charset then a rule (no stylesheet uses @charset)', '@charset "UTF-8";\n.real { }', 'refused'],
    ['K: an attribute value with a dot', 'a[href$=".pdf"] { color: red; }', []],
    ['K: an attribute selector naming a class', '[class~="alb-z"] { color: red; }', 'refused'],
    ['K: an attribute value with a brace', '[data-x="{"] .a { } .b { }', ['a', 'b']],
    ['K: an escaped colon in a class', '.md\\:flex { display: flex; }', ['md:flex']],
    ['K: CSS nesting', '.parent { color: red; .child-dead { color: blue; } }', ['child-dead', 'parent']],
    ['K: an @scope block', '@scope (.card) { .title-dead { color: red; } }', 'refused'],
    ['K: an @starting-style block (not used)', '@starting-style { .fade-dead { opacity: 0; } }', 'refused'],
    ['K: @media inside @supports (not used)', '@supports (display: grid) { @media (min-width: 1px) { .deep { } } }', 'refused'],
    ['K: @media inside a rule (nesting)', '.p { @media (min-width: 1px) { .deep { } } }', ['deep', 'p']],
    ['K: a class inside :not()', '.btn:not(.is-off) { color: red; }', ['btn', 'is-off']],
    ['K: a keyframe stop with a decimal', '@keyframes k { 50.5% { opacity: 1; } }', []],
    ['K: an @media prelude with a decimal in em (not a form the stylesheets write)', '@media (min-width: 40.5em) { .x { } }', 'refused'],
    ['K: an @media prelude with a decimal in px', '@media (min-width: 40.5px) { .x { } }', ['x']],
    // AUDIT L's cases
    ['L: a class under two :not()', '.a:not(:not(.b-dead)) { color: red; }', ['a', 'b-dead']],
    ['L: three :not()', '.a:not(:not(:not(.b))) { }', ['a', 'b']],
    ['L: :not() inside :has()', '.a:has(:not(.b)) { }', ['a', 'b']],
    ['L: :has() inside :has()', '.a:has(.b:has(.c)) { }', ['a', 'b', 'c']],
    ['L: :has() with a relative combinator', '.a:has(> .b + .c) { }', ['a', 'b', 'c']],
    ['L: :NOT in capitals', '.a:NOT(.b) { }', ['a', 'b']],
    ['L: :not() with a list', '.a:not(.b, .c) .d { }', ['a', 'b', 'c', 'd']],
    ['L: a comment inside :not() holding a paren', '.a:not(/*)*/.b) { }', ['a', 'b']],
    ['L: a string inside :has() holding a paren', '.a:has([data-x=")"] .b) { }', ['a', 'b']],
    ['L: no spaces anywhere', '.a{color:red}.b{}', ['a', 'b']],
    ['L: a brace in a declaration string', '.a{content:"}"}.b{}', ['a', 'b']],
    ['L: a brace in a declaration comment', '.a{/* } */}.b{}', ['a', 'b']],
    ['L: an escaped quote in a declaration string', '.a{content:"\\"}"}.b{}', ['a', 'b']],
    ['L: a closing bracket in an attribute string', '.a[data-x="]"] .c { }', ['a', 'c']],
    ['L: an unquoted url holding a closing brace', '.a{background:url(x})}.b{}', ['a', 'b']],
    ['L: an unquoted url holding an opening brace', '.a{background:url(x{)}.b{}', ['a', 'b']],
    ['L: a custom property holding a block', '.a{--x:{b:c}}', ['a']],
    ['L: @MEDIA in capitals', '@MEDIA (MAX-WIDTH: 720PX) { .a { } }', ['a']],
    ['L: @supports selector() in a prelude', '@supports selector(.p) { .q { } }', 'refused'],
    ['L: @media with no space before its brace', '@media (max-width:720px){.a{}}', ['a']],
    ['L: @keyframes named by a string (not the form the stylesheets write)', '@keyframes "k" { from { } to { } }', 'refused'],
    ['L: a rule after a keyframes block with no space', '@keyframes k{from{}}.z{}', ['z']],
    ['L: an empty selector', '{ color: red }', []],
    ['L: a trailing comma', '.a, { }', ['a']],
    ['L: an id beside a class', '#i.c { }', ['c']],
    ['L: a namespace', 'svg|a { }', []],
    ['L: nth with of', '.a:nth-child(2n+1 of .b) { }', ['a', 'b']],
    ['L: a non-ASCII class', '.café { }', ['café']],
    ['L: a comment end searched early', '/*/ .a { } */ .b { }', ['b']],
    ['L: an unquoted @import url with a semicolon', '@import url(a;b.css); .real { }', 'refused'],
    ['L: nth-last-of-type', '.a:nth-last-of-type(2) { }', ['a']],
    ['L: a rule inside @font-face (not used)', '@font-face { .a { } }', 'refused'],
    ['L: a keyframe stop not at its start', '@keyframes k { foo from { } }', []],
    ['a line end escaped with a backslash inside a string, read as CSS reads it', '.a { content: "x\\\ny"; } .b { }', ['a', 'b']],
  ];
  it.each(ROWS)('%s', (_label, css, classes) => {
    expect(classesOf(css)).toEqual(classes);
  });
});

describe('a form the guard does not read is refused by one sentence, never passed over', () => {
  const refusedBy = (css: string) => {
    try {
      stylesheetClasses(css, 'styles/x.css');
      return null;
    } catch (error) {
      return error instanceof UnreadForm ? error.message : `not refused: ${String(error)}`;
    }
  };
  const OFF_LIST = (rule: string) =>
    `styles/x.css: "${rule}" is an at-rule the guard does not read (the stylesheets use only @import, @keyframes and @media); teach classGuard.ts to read it, or write its rules without it.`;
  const HOLDS: Record<string, string> = {
    import: "one quoted path to a stylesheet beside it ('./name.css') and nothing after it",
    keyframes: 'one name',
    media: 'media features joined by "and": a width or height in px, hover, pointer or prefers-reduced-motion',
  };
  const PRELUDE = (rule: string, name: string) =>
    `styles/x.css: "${rule}" has a prelude the guard does not read: @${name} may hold ${HOLDS[name]}; teach classGuard.ts to read it, or write it as the stylesheets do.`;
  const SELECTOR_FN = (rule: string) =>
    `styles/x.css: "${rule}" names a selector in its condition, which the guard does not read; test a property instead, and write the class in a rule's selector.`;
  const LINE_END = (start: string) =>
    `styles/x.css: the string ${start} runs across a line end, where CSS ends a string and the guard's parser does not; write it on one line, or escape the line end with a backslash.`;
  const ATTRIBUTE = (rule: string) =>
    `styles/x.css: "${rule}" matches class with an attribute selector, which the guard does not read; name the class as .name in the selector instead.`;
  const ROWS: Array<[label: string, css: string, sentence: string]> = [
    ['an attribute selector on class with =', '[class="x"] { }', ATTRIBUTE('[class="x"]')],
    ['with ~=', '[class~="x"] { }', ATTRIBUTE('[class~="x"]')],
    ['with |=', '[class|="x"] { }', ATTRIBUTE('[class|="x"]')],
    ['with ^=', '[class^="x"] { }', ATTRIBUTE('[class^="x"]')],
    ['with $=', '[class$="x"] { }', ATTRIBUTE('[class$="x"]')],
    ['with *=', '[class*="x"] { }', ATTRIBUTE('[class*="x"]')],
    ['with the i flag', '[class~="x" i] { }', ATTRIBUTE('[class~="x" i]')],
    ['in capitals', '.a [CLASS~="x"] { }', ATTRIBUTE('.a [CLASS~="x"]')],
    ['present with no value', '[class] { }', ATTRIBUTE('[class]')],
    ['inside :not()', '.a:not([class~="x"]) { }', ATTRIBUTE('.a:not([class~="x"])')],
    ['an @scope prelude', '@scope (.card) { .t { } }', OFF_LIST('@scope (.card)')],
    ['an @scope prelude with a limit', '@scope (.a) to (.b) { .t { } }', OFF_LIST('@scope (.a) to (.b)')],
    ['@supports selector()', '@supports selector(.p) { .q { } }', 'styles/x.css: "@supports selector(.p)" names a selector in its condition, which the guard does not read; test a property instead, and write the class in a rule\'s selector.'],
    ['@supports not selector()', '@supports not selector(.p) { .q { } }', 'styles/x.css: "@supports not selector(.p)" names a selector in its condition, which the guard does not read; test a property instead, and write the class in a rule\'s selector.'],
    ['@custom-selector', '@custom-selector :--heading .h1, .h2;', OFF_LIST('@custom-selector :--heading .h1, .h2')],
    ['a custom selector in use', ':--heading .a { }', 'styles/x.css: ":--heading .a" uses the custom selector :--heading, which the guard does not read; write its classes in the selector instead.'],
    ['an at-rule nobody listed', '@frobnicate (x) { .a { } }', OFF_LIST('@frobnicate (x)')],
    // Every at-rule off the list, used or not elsewhere, by the one sentence
    ['@supports with a property', '@supports (display: grid) { .a { } }', OFF_LIST('@supports (display: grid)')],
    ['@font-face', '@font-face { font-family: x; }', OFF_LIST('@font-face')],
    ['@charset', '@charset "UTF-8";', OFF_LIST('@charset "UTF-8"')],
    ['@layer', '@layer base { .a { } }', OFF_LIST('@layer base')],
    ['@container', '@container (min-width: 1px) { .a { } }', OFF_LIST('@container (min-width: 1px)')],
    ['@starting-style', '@starting-style { .a { } }', OFF_LIST('@starting-style')],
    ['a vendor @keyframes', '@-webkit-keyframes k { from { } }', OFF_LIST('@-webkit-keyframes k')],
    ['@namespace', '@namespace svg url(http://www.w3.org/2000/svg);', OFF_LIST('@namespace svg url(http://www.w3.org/2000/svg)')],
    ['a name that is a property of every object', '@constructor { .a { } }', OFF_LIST('@constructor')],
    // A prelude off the list, for each at-rule on it
    ['@media print', '@media print { .a { } }', PRELUDE('@media print', 'media')],
    ['@media written across lines, shown on one', '@media print\n    and (hover: none) { .a { } }', PRELUDE('@media print and (hover: none)', 'media')],
    ['@media with a media type', '@media screen and (max-width: 720px) { .a { } }', PRELUDE('@media screen and (max-width: 720px)', 'media')],
    ['@media with a list', '@media (max-width: 720px), (hover: none) { .a { } }', PRELUDE('@media (max-width: 720px), (hover: none)', 'media')],
    ['@media with not', '@media not (hover: none) { .a { } }', PRELUDE('@media not (hover: none)', 'media')],
    ['@media with a feature nobody listed', '@media (orientation: portrait) { .a { } }', PRELUDE('@media (orientation: portrait)', 'media')],
    ['@media with a width in em', '@media (max-width: 40em) { .a { } }', PRELUDE('@media (max-width: 40em)', 'media')],
    ['@media with a made-up feature', '@media (x) { .a { } }', PRELUDE('@media (x)', 'media')],
    ['@import by url()', '@import url("./a.css");', PRELUDE('@import url("./a.css")', 'import')],
    ['@import with a layer', "@import './a.css' layer(base);", PRELUDE("@import './a.css' layer(base)", 'import')],
    ['@import with a media query', "@import './a.css' screen;", PRELUDE("@import './a.css' screen", 'import')],
    ['@import from another host', "@import 'https://example.com/a.css';", PRELUDE("@import 'https://example.com/a.css'", 'import')],
    ['@import from another folder', "@import '../a.css';", PRELUDE("@import '../a.css'", 'import')],
    ['@import of a file that is no stylesheet', "@import './a.js';", PRELUDE("@import './a.js'", 'import')],
    ['@keyframes named by a string', '@keyframes "k" { from { } }', PRELUDE('@keyframes "k"', 'keyframes')],
    ['@keyframes with two names', '@keyframes a b { from { } }', PRELUDE('@keyframes a b', 'keyframes')],
    // The media forms the reader found loose, each pinned: a value hover or pointer does not take, a word other than and
    ['hover with a value it does not take', '@media (hover: maybe) { .a { } }', PRELUDE('@media (hover: maybe)', 'media')],
    ['pointer with a value it does not take', '@media (pointer: sometimes) { .a { } }', PRELUDE('@media (pointer: sometimes)', 'media')],
    ['features joined by or', '@media (max-width: 720px) or (hover: none) { .a { } }', PRELUDE('@media (max-width: 720px) or (hover: none)', 'media')],
    // A string run across a raw line end: CSS ends it at the line end and reads on as rules; the guard's parser does not
    ['a string across a line end in a declaration', '.a { content: "x\n} .b { color: red; } .c { content: "; }', LINE_END('"x…')],
    ['a string across a line end in a custom property', ':root { --x: "a\n}\n.dead { color: red }\n:root { --y: " }', LINE_END('"a…')],
    ['a string across a line end in a selector', '[data-x="a\nb"] .a { }', LINE_END('"a…')],
    ['a string across a carriage return', '.a { content: "x\r" }', LINE_END('"x…')],
    // A selector function in any prelude, in any case (the reader's two forms)
    ['@import with supports(selector())', "@import url('x.css') supports(selector(.p));", SELECTOR_FN("@import url('x.css') supports(selector(.p))")],
    ['@import with SELECTOR() in capitals', "@import './hud-tokens.css' supports(SELECTOR(.p));", SELECTOR_FN("@import './hud-tokens.css' supports(SELECTOR(.p))")],
    ['@supports SELECTOR() in capitals', '@supports SELECTOR(.p) { .q { } }', SELECTOR_FN('@supports SELECTOR(.p)')],
    ['@supports Selector () spaced', '@supports Selector (.p) { .q { } }', SELECTOR_FN('@supports Selector (.p)')],
    ['@media holding selector()', '@media (max-width: 720px) and selector(.p) { .q { } }', SELECTOR_FN('@media (max-width: 720px) and selector(.p)')],
  ];
  it.each(ROWS)('refuses %s, naming the form, the file and the rule', (_label, css, sentence) => {
    expect(refusedBy(css)).toBe(sentence);
  });

  it.each([
    ['a dead class through an attribute selector', '[class~="alb-audit-dead-attr"] { color: red; }'],
    ['a dead class in an @scope prelude', '@scope (.alb-audit-dead-scope) { .alb-scan-glyph { color: red; } }'],
    ['a dead class in @supports selector()', '@supports selector(.alb-audit-dead-supports) { .alb-scan-glyph { color: red; } }'],
    ['a dead class in an @import condition (the second reader\'s R5)', '@import url("x.css") supports(selector(.alb-audit-dead-import));'],
    ['a dead class in @supports SELECTOR() in capitals (its G3)', '@supports SELECTOR(.alb-audit-dead-caps) { .alb-scan-glyph { color: red; } }'],
    ['a dead class between two strings run across line ends (the third reader\'s first)', ':root { --audit-h: "a\n}\n.alb-audit-h-dead { color: rgb(255, 0, 0) }\n:root { --audit-h2: " }'],
  ])("fails the reader's probe: %s added to albedian.css", (_label, rule) => {
    const css = `${fs.readFileSync(path.join(srcRoot, 'styles/albedian.css'), 'utf8')}\n${rule}\n`;
    expect(() => stylesheetClasses(css, 'styles/albedian.css')).toThrow(UnreadForm);
  });

  it('refuses a node kind the selector parser might hand back that nobody listed', () => {
    const node = { type: 'a-kind-nobody-listed' } as unknown as Parameters<typeof refusalOf>[0] extends { node: infer N } ? N : never;
    expect(refusalOf({ kind: 'node', node, selector: '.a' }, 'styles/x.css')).toBe(
      'styles/x.css: ".a" holds the node kind a-kind-nobody-listed, which the guard does not read; teach classGuard.ts to read it, or write the selector without it.'
    );
  });

  it('reads exactly the at-rules the stylesheets use today, each in every form it is written in', () => {
    expect([...AT_RULES_READ.keys()]).toEqual(['import', 'keyframes', 'media']);
    const TODAY = [
      "@import './hud-tokens.css';",
      '@import "./hud-tokens.css";',
      '@keyframes alb-ask-palette-in { from { } to { } }',
      '@media (hover: none) and (pointer: coarse) { .a { } }',
      '@media (max-height: 480px) { .a { } }',
      '@media (max-height: 500px) and (pointer: coarse) { .a { } }',
      '@media (max-width: 640px) { .a { } }',
      '@media (max-width: 720px) and (min-height: 501px) { .a { } }',
      '@media (max-width: 900px) { .a { } }',
      '@media (prefers-reduced-motion: reduce) { .a { } }',
      '@media (min-width: 1px) and (hover: hover) and (pointer: fine) { .a { } }',
      '@media (prefers-reduced-motion: no-preference) { .a { } }',
    ];
    for (const css of TODAY) expect(refusedBy(css), css).toBeNull();
    // Every at-rule in today's stylesheets is on the list, in a prelude it reads
    const used = sheetsChecked().flatMap(([, css]) => {
      const rules: string[] = [];
      postcss.parse(css).walkAtRules((rule) => {
        rules.push(rule.name.toLowerCase());
      });
      return rules;
    });
    expect([...new Set(used)].sort()).toEqual(['import', 'keyframes', 'media']);
    expect(refusedBy('a[href$=".pdf"] .c:hover::before, #i > .d + .e ~ .f { }')).toBeNull();
  });
});

describe('index.html: its inline style elements, read as the files are', () => {
  it.each([
    ['one style element', '<html><head><style>.a { }</style></head></html>', ['.a { }']],
    ['two, one in the body', '<html><head><style>.a { }</style></head><body><style>.b { }</style></body></html>', ['.a { }', '.b { }']],
    ['none', '<html><head></head></html>', []],
    ['one inside a template in the body', '<html><body><template><style>.t { }</style></template></body></html>', ['.t { }']],
    ['one inside a template in the head', '<html><head><template><style>.t { }</style></template></head></html>', ['.t { }']],
    ['one inside a template inside a template', '<html><body><template><div><template><style>.u { }</style></template></div></template></body></html>', ['.u { }']],
    [
      'all of them in document order, a template between',
      '<html><head><style>.a { }</style></head><body><template><style>.t { }</style></template><style>.b { }</style></body></html>',
      ['.a { }', '.t { }', '.b { }'],
    ],
    ['one in an inline svg', '<html><body><svg><style>.s { }</style></svg></body></html>', ['.s { }']],
    ['one in noscript', '<html><body><noscript><style>.n { }</style></noscript></body></html>', ['.n { }']],
    ['one inside an svg element named template, which is no template: read once', '<html><body><svg><template><style>.s { }</style></template></svg></body></html>', ['.s { }']],
  ])('finds %s', (_label, html, styles) => {
    expect(htmlStyles(html)).toEqual(styles);
  });

  it('refuses a page held in a data address, in a frame, an object or an embed, by its sentence', () => {
    const ROWS: Array<[html: string, element: string]> = [
      ['<html><body><iframe src="data:text/html,<style>.x{}</style>"></iframe></body></html>', 'iframe'],
      ['<html><body><iframe src="  DATA:text/html;base64,AA"></iframe></body></html>', 'iframe'],
      ['<html><body><object data="data:text/html,<style>.x{}</style>"></object></body></html>', 'object'],
      ['<html><body><embed src="data:text/html,x"></body></html>', 'embed'],
    ];
    for (const [html, element] of ROWS) {
      expect(() => htmlStyles(html), html).toThrow(
        new UnreadForm(`index.html: a <${element}> holds a page of its own in a data address, which the guard does not read; serve that page as a file instead.`)
      );
    }
    // A data address that is no page is read as before: an image's, and a frame's own file
    expect(htmlStyles('<html><body><img src="data:image/png;base64,AA"><iframe src="/page.html"></iframe><style>.a { }</style></body></html>')).toEqual(['.a { }']);
  });

  it('refuses a page that holds a page of its own in srcdoc, by its sentence', () => {
    expect(() => htmlStyles('<html><body><iframe srcdoc="<style>.x { }</style>"></iframe></body></html>')).toThrow(
      new UnreadForm('index.html: a <iframe> holds a page of its own in srcdoc, which the guard does not read; serve that page as a file instead.')
    );
  });

  it("fails the second reader's R4: a dead class in a style inside a template of the page", () => {
    const page = fs.readFileSync(path.join(webRoot, 'index.html'), 'utf8').replace('</body>', '<template><style>.alb-audit-dead-template { color: red; }</style></template></body>');
    const dead = htmlStyles(page).flatMap((css, i) => deadClasses(stylesheetClasses(css, `index.html <style> ${i + 1}`), usedNames()));
    expect(dead).toEqual(['alb-audit-dead-template']);
  });
});

describe('the source side: TypeScript strings, whole names only', () => {
  const ROWS: Array<[label: string, file: string, text: string, name: string, isUse: boolean]> = [
    // The rule in its own words
    ['a string literal', 'a.ts', 'export const c = "alb-c";', 'alb-c', true],
    ['a JSX class attribute', 'a.tsx', 'export const C = () => <i className="alb-a alb-c" />;', 'alb-c', true],
    ['a no-substitution template', 'a.ts', 'export const c = `alb-c`;', 'alb-c', true],
    ['a template part with space before its hole', 'a.ts', 'export const c = (s: string) => `alb-c ${s}`;', 'alb-c', true],
    ['a template part touching its hole', 'a.ts', 'export const c = (s: string) => `alb-c--${s}`;', 'alb-c--', false],
    ['a middle part between holes, spaced', 'a.ts', 'export const c = (s: string) => `${s} alb-c ${s}`;', 'alb-c', true],
    ['a middle part touching the hole before it', 'a.ts', 'export const c = (s: string) => `${s}alb-c ${s}`;', 'alb-c', false],
    ['a middle part touching the hole after it', 'a.ts', 'export const c = (s: string) => `${s} alb-c${s}`;', 'alb-c', false],
    ['a tail touching its hole', 'a.ts', 'export const c = (s: string) => `${s}alb-c`;', 'alb-c', false],
    ['a tail with space after its hole', 'a.ts', 'export const c = (s: string) => `${s} alb-c`;', 'alb-c', true],
    ['a regular expression is not a use', 'a.ts', 'export const re = /alb-c/;', 'alb-c', false],
    ['a line comment is not a use', 'a.ts', '// alb-c\nexport {};', 'alb-c', false],
    ['JSX text is not a use', 'a.tsx', 'export const C = () => <div>alb-c</div>;', 'alb-c', false],
    ['a name with a dot before it is not whole', 'a.ts', "document.querySelector('.alb-c');", 'alb-c', false],
    ['names split by a newline are each whole', 'a.ts', "export const c = 'alb-a\\nalb-c';", 'alb-c', true],
    ['a string cast with angle brackets in a .ts file, read as TypeScript', 'a.ts', "export const c = <string>'alb-c';", 'alb-c', true],
    // AUDIT K's cases
    ['K: a name only in a comment', 'a.ts', '// alb-c is gone\nexport {};', 'alb-c', false],
    ['K: a name in a log string', 'a.ts', 'console.log("alb-c");', 'alb-c', true],
    ['K: a name followed by a dot in an import path', 'a.ts', "import './alb-x.css';", 'alb-x', false],
    ['K: a name in a querySelector', 'a.ts', "document.querySelector('.alb-x');", 'alb-x', false],
    ['K: a name as a prefix built at run time', 'a.tsx', 'export const C = (state: string) => <i className={`alb-x--${state}`} />;', 'alb-x--on', false],
    ['K: a name with a double-hyphen suffix', 'a.tsx', 'export const C = () => <i className="alb-x--on" />;', 'alb-x', false],
    ['K: a name after a dollar sign', 'a.ts', "export const s = '$alb-x';", 'alb-x', false],
    ['K: a name after a dot in a path', 'a.ts', "export const s = 'styles.alb-x';", 'alb-x', false],
    // AUDIT L's cases
    ['L: a name in a regular expression', 'a.ts', 'export const re = /alb-c/;', 'alb-c', false],
    ['L: a pinned comment', 'a.ts', '/*! alb-c */\nexport const x = 1;', 'alb-c', false],
    ['L: a JSDoc comment', 'a.ts', '/** alb-c */\nexport const x = 1;', 'alb-c', false],
    ['L: a JSX comment', 'a.tsx', 'export const C = () => <div>{/* alb-c */}</div>;', 'alb-c', false],
    ['L: a comment at the end with no newline', 'a.ts', 'export const x = 1; // alb-c', 'alb-c', false],
    ['L: comment text inside a template string', 'a.ts', 'export const t = `/* alb-c */`;', 'alb-c', true],
    ['L: a string that is never a class', 'a.ts', 'export const label = "alb-c";', 'alb-c', true],
    ['L: a string literal type', 'a.ts', "export type T = 'alb-c';", 'alb-c', true],
    ['L: an angle-bracket cast in a .ts file, the name in a comment', 'a.ts', 'const y = 1;\nexport const x = <number>y; // alb-c', 'alb-c', false],
  ];
  it.each(ROWS)('%s', (_label, file, text, name, isUse) => {
    expect(sourceNames(text, file).has(name)).toBe(isUse);
  });

  const DEAD: Array<[label: string, css: string, source: string, dead: string[]]> = [
    ['K: a :not() rule whose class no source names', '.btn:not(.is-off) { color: red; }', 'export const C = () => <i className="btn" />;', ['is-off']],
    ['L: two :not() on a rule no source can match', '.a:not(:not(.b-dead)) { color: red; }', 'export const C = () => <i className="a" />;', ['b-dead']],
  ];
  it.each(DEAD)('%s is reported dead', (_label, css, source, dead) => {
    expect(deadClasses(stylesheetClasses(css), sourceNames(source, 'a.tsx'))).toEqual(dead);
  });

  it('reports a styled name that is only built from parts, as dead', () => {
    const uses = sourceNames('export const C = (state: string) => <i className={`alb-x alb-x--${state}`} />;', 'a.tsx');
    expect(deadClasses(stylesheetClasses('.alb-x { } .alb-x--on { }'), uses)).toEqual(['alb-x--on']);
  });
});

describe('index.html: class attributes, and inline scripts through the same reader', () => {
  const ROWS: Array<[label: string, html: string, name: string, isUse: boolean]> = [
    ['a class attribute', '<div class="alb-a alb-c"></div>', 'alb-c', true],
    ['a name in an HTML comment', '<!-- alb-c --><div></div>', 'alb-c', false],
    ['L: a name in an unclosed HTML comment', '<!-- alb-c', 'alb-c', false],
    ['a string in an inline script', '<script>document.body.className = "alb-c";</script>', 'alb-c', true],
    ['L: a name in a script comment', '<script>// alb-c\n</script>', 'alb-c', false],
    ['a name in the page text', '<p>alb-c</p>', 'alb-c', false],
  ];
  it.each(ROWS)('%s', (_label, html, name, isUse) => {
    expect(htmlNames(html).has(name)).toBe(isUse);
  });
});
