import fs from 'node:fs';
import postcss from 'postcss';
import tokenizer from 'postcss/lib/tokenize';
import selectorParser from 'postcss-selector-parser';
import ts from 'typescript';

/**
 * The guard's rule: every class a stylesheet of the web app names appears whole in a string of the web's sources.
 *
 * Stylesheets (the .css files under src and the page's <style> elements) are read by postcss and their selectors by
 * postcss-selector-parser. That is the guard's reading, not the build's: Vite runs postcss on a sheet only for some
 * of its own steps, and the build's minifier, esbuild, parses every sheet again on its own, so on a malformed sheet
 * the two, and a browser, may read otherwise than postcss. Every class in a rule's selector counts, wherever it sits (under
 * :not(), :has(), in a nested rule). Rules inside a keyframes block are skipped by their parent's node (a stop is never a
 * class; an invalid class there styles nothing). One place, refusalOf, decides for every at-rule and every selector
 * node whether the guard reads it, and the default is refusal: it reads only the at-rules the stylesheets use today
 * (AT_RULES_READ: @font-face, @import, @keyframes and @media, each with the only preludes it may hold) and the selector
 * node kinds
 * it walks; a selector function in any prelude, in any case, an attribute selector on class and a custom selector are
 * refused by name, and so is a string run across a raw line end (postcss's own tokens show it). Each refusal is one
 * sentence naming the form, the file and the rule, and saying what to do instead. index.html's <style> elements are
 * read wherever its parser holds one, a template's content included.
 *
 * What it is for, and its limit, said once: it is a check against drift, a class left behind in the project's own
 * stylesheets, written in the plain forms the project uses. It is no defence against a stylesheet written to be
 * misread: whoever would write one can as easily take the guard out. It reads each stylesheet as its parser reads
 * it, and where a browser would read a malformed sheet another way, the guard does not see the difference, beyond
 * the forms it refuses by name (a string run across a line end is one: CSS ends it at the line end and reads on as
 * rules, while postcss runs it to the next quote).
 *
 * Sources are read by TypeScript's parser, which drives its scanner, so the grammar decides what is a regular
 * expression and where a template's holes are. A use is a class name standing whole, between white space or the
 * ends, in a string literal or in a part of a template literal; a token of a part that touches a hole is not whole.
 * Comments, regular expressions and JSX text are not uses. index.html is read by the DOM parser: its class attributes,
 * and its inline scripts through the same TypeScript reader.
 *
 * What it does not see, in plain words:
 * - A name in any string of the sources counts, so a label that happens to spell a class keeps a dead rule alive.
 *   That is the safe direction for a guard against removal mistakes.
 * - A class name built from parts at run time is not found whole, so its rule is reported dead (the guard fails; a
 *   name must be written whole).
 * - A stylesheet built or changed at run time (a style element or rule written by a script, the CSSOM, a style
 *   attribute) is not read; nor is a stylesheet linked from the page (index.html links the outside font host's, which
 *   names no class of the app), nor a page held in a srcdoc or a data address (both refused), nor a .css file
 *   outside src.
 * - @import is read in its plain form only, and the file it names is read because it is one of the files under src,
 *   not by following the import.
 * - Only what decides whether a rule can match is read: a class in a declaration (content: '.x') is not a class.
 */

/** A form the guard does not read: its message is the sentence refusing it. */
export class UnreadForm extends Error {}

/** The media features the stylesheets test today, each in the only form it is written in. */
const MEDIA_FEATURES = [
  /^\(\s*(?:max|min)-(?:width|height)\s*:\s*\d+(?:\.\d+)?px\s*\)$/i,
  /^\(\s*hover\s*:\s*(?:none|hover)\s*\)$/i,
  /^\(\s*pointer\s*:\s*(?:none|coarse|fine)\s*\)$/i,
  /^\(\s*prefers-reduced-motion\s*:\s*(?:reduce|no-preference)\s*\)$/i,
];

/**
 * The at-rules the web's stylesheets use today, each with the only preludes it may hold; every other at-rule, and every
 * other prelude, is refused, so a form nobody wrote down cannot pass. A new at-rule in a stylesheet means teaching it
 * here first.
 */
export const AT_RULES_READ: ReadonlyMap<string, { holds: string; reads: (prelude: string) => boolean }> = new Map([
  ['font-face', { holds: 'nothing (its rule has no prelude)', reads: (prelude: string) => prelude === '' }],
  [
    'import',
    {
      holds: "one quoted path to a stylesheet beside it ('./name.css') and nothing after it",
      reads: (prelude: string) => /^(['"])\.\/[\w-]+(?:\.[\w-]+)*\.css\1$/.test(prelude),
    },
  ],
  ['keyframes', { holds: 'one name', reads: (prelude: string) => /^-?[a-z_][\w-]*$/i.test(prelude) }],
  [
    'media',
    {
      holds: 'media features joined by "and": a width or height in px, hover, pointer or prefers-reduced-motion',
      reads: (prelude: string) => prelude.split(/\s+and\s+/i).every((feature) => MEDIA_FEATURES.some((form) => form.test(feature))),
    },
  ],
]);

const AT_RULE_NAMES = [...AT_RULES_READ.keys()].map((name) => `@${name}`);
const AT_RULES_LISTED = `${AT_RULE_NAMES.slice(0, -1).join(', ')} and ${AT_RULE_NAMES.at(-1)}`;

/** The selector parser's node kinds the guard walks; anything else it hands back is refused. */
const READ_NODES = new Set(['root', 'selector', 'class', 'tag', 'id', 'universal', 'combinator', 'pseudo', 'nesting', 'attribute', 'comment', 'string']);

type Form = { kind: 'at-rule'; rule: postcss.AtRule } | { kind: 'node'; node: selectorParser.Node; selector: string };

/** The one place that decides whether the guard reads a form: null when it does, else the sentence refusing it. */
export function refusalOf(form: Form, file: string): string | null {
  if (form.kind === 'at-rule') {
    const name = form.rule.name.toLowerCase();
    const prelude = form.rule.params.replace(/\s+/g, ' ').trim();
    const shown = `"@${form.rule.name}${prelude ? ` ${prelude}` : ''}"`;
    // A selector function names classes wherever a prelude holds one, whatever the at-rule and the case
    if (/selector\s*\(/i.test(prelude)) {
      return `${file}: ${shown} names a selector in its condition, which the guard does not read; test a property instead, and write the class in a rule's selector.`;
    }
    const read = AT_RULES_READ.get(name);
    if (!read) {
      return `${file}: ${shown} is an at-rule the guard does not read (the stylesheets use only ${AT_RULES_LISTED}); teach classGuard.ts to read it, or write its rules without it.`;
    }
    if (!read.reads(prelude)) {
      return `${file}: ${shown} has a prelude the guard does not read: @${name} may hold ${read.holds}; teach classGuard.ts to read it, or write it as the stylesheets do.`;
    }
    return null;
  }
  const { node, selector } = form;
  const shown = `"${selector.replace(/\s+/g, ' ').trim()}"`;
  if (!READ_NODES.has(node.type)) return `${file}: ${shown} holds the node kind ${node.type}, which the guard does not read; teach classGuard.ts to read it, or write the selector without it.`;
  if (node.type === 'attribute' && node.attribute.toLowerCase() === 'class') {
    return `${file}: ${shown} matches class with an attribute selector, which the guard does not read; name the class as .name in the selector instead.`;
  }
  if (node.type === 'pseudo' && node.value.startsWith(':--')) {
    return `${file}: ${shown} uses the custom selector ${node.value}, which the guard does not read; write its classes in the selector instead.`;
  }
  return null;
}

/**
 * Every class named in a rule's selector of a stylesheet. A stylesheet postcss cannot parse throws its error; a form the
 * guard does not read throws UnreadForm with the sentence refusing it.
 */
export function stylesheetClasses(css: string, file = 'a stylesheet'): Set<string> {
  const broken = lineEndString(css);
  if (broken !== null) {
    const start = `${broken.split(/[\n\r\f]/)[0]}\u2026`;
    throw new UnreadForm(`${file}: the string ${start} runs across a line end, where CSS ends a string and the guard's parser does not; write it on one line, or escape the line end with a backslash.`);
  }
  const names = new Set<string>();
  postcss.parse(css).walk((item) => {
    if (item.type === 'atrule') {
      const refused = refusalOf({ kind: 'at-rule', rule: item }, file);
      if (refused) throw new UnreadForm(refused);
      return;
    }
    if (item.type !== 'rule') return;
    const parent = item.parent;
    if (parent?.type === 'atrule' && (parent as postcss.AtRule).name.toLowerCase() === 'keyframes') return;
    selectorParser((root) => {
      root.walk((node) => {
        const refused = refusalOf({ kind: 'node', node, selector: item.selector }, file);
        if (refused) throw new UnreadForm(refused);
        if (node.type === 'class') names.add(node.value);
      });
    }).processSync(item.selector);
  });
  return names;
}

/**
 * The first string, by postcss's own tokens, that holds a raw line end, or null. A line end escaped with a backslash
 * is CSS's line continuation and is not one; CR LF is one line end, as CSS reads it, so a backslash escapes both.
 */
function lineEndString(css: string): string | null {
  const tokens = tokenizer(new postcss.Input(css));
  while (!tokens.endOfFile()) {
    const token = tokens.nextToken();
    if (token?.[0] === 'string' && /[\n\r\f]/.test(token[1].replace(/\\(?:\r\n|[\s\S])/g, ''))) return token[1];
  }
  return null;
}

/**
 * The text of every <style> element of a page, in document order, wherever its HTML parser holds one: the head, the
 * body, an inline svg or noscript, and inside a template's content, which the DOM's own search never enters. A page
 * holding a page of its own in an iframe's srcdoc, or in a data address (a frame's or an embed's src, an object's
 * data), throws UnreadForm: the guard does not read it.
 */
export function htmlStyles(html: string, file = 'index.html'): string[] {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const styles: string[] = [];
  const visit = (root: ParentNode) => {
    for (const element of root.querySelectorAll('style, template, [srcdoc], iframe[src], frame[src], embed[src], object[data]')) {
      if (element.hasAttribute('srcdoc')) {
        throw new UnreadForm(`${file}: a <${element.localName}> holds a page of its own in srcdoc, which the guard does not read; serve that page as a file instead.`);
      }
      const address = element.getAttribute(element.localName === 'object' ? 'data' : 'src') ?? '';
      if (/^\s*data:/i.test(address) && element.localName !== 'style' && element.localName !== 'template') {
        throw new UnreadForm(`${file}: a <${element.localName}> holds a page of its own in a data address, which the guard does not read; serve that page as a file instead.`);
      }
      if (element instanceof HTMLTemplateElement) visit(element.content);
      else if (element.localName === 'style') styles.push(element.textContent ?? '');
    }
  };
  visit(doc);
  return styles;
}

/** The whole names in the strings of a TypeScript, TSX or JavaScript source. */
export function sourceNames(text: string, fileName: string): Set<string> {
  const kind = fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : fileName.endsWith('.ts') ? ts.ScriptKind.TS : ts.ScriptKind.JS;
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, false, kind);
  const names = new Set<string>();
  // A part's token that touches a hole is not whole, so the end token at a hole goes; where white space stands
  // between them, the split has put an empty token at that end, which goes instead
  const add = (part: string, holeBefore: boolean, holeAfter: boolean) => {
    const tokens = part.split(/\s+/);
    if (holeBefore) tokens.shift();
    if (holeAfter) tokens.pop();
    for (const token of tokens) if (token) names.add(token);
  };
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) add(node.text, false, false);
    else if (ts.isTemplateHead(node)) add(node.text, false, true);
    else if (ts.isTemplateMiddle(node)) add(node.text, true, true);
    else if (ts.isTemplateTail(node)) add(node.text, true, false);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return names;
}

/** The names index.html uses: its class attributes, and the strings of its inline scripts. */
export function htmlNames(html: string): Set<string> {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const names = new Set<string>();
  for (const element of doc.querySelectorAll('[class]')) for (const name of element.classList) names.add(name);
  for (const script of doc.querySelectorAll('script')) {
    for (const name of sourceNames(script.textContent ?? '', 'inline.js')) names.add(name);
  }
  return names;
}

/** The styled classes no source uses, sorted. */
export function deadClasses(styled: Iterable<string>, used: ReadonlySet<string>): string[] {
  return [...new Set(styled)].filter((name) => !used.has(name)).sort();
}

/** Every path under a folder that passes the test, relative to the folder (POSIX separators), sorted. */
export function filesUnder(dir: string, take: (relative: string) => boolean): string[] {
  return (fs.readdirSync(dir, { recursive: true }) as string[]).filter(take).sort();
}
