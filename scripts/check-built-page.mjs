// No #! line, as verify-public-tracked.mjs: run it with node.
/**
 * The built page check, which the web build runs after vite build (apps/web/package.json). It reads every built page
 * (*.html) and every built stylesheet (*.css) under the build folder, and fails the build when an address it reads
 * there is outside the app's own origin, or when it meets a form it does not decode. It reads pages and stylesheets
 * as the build writes them, in the forms listed below. This check is no guarantee of what a page loads (a page
 * written to mislead it among the cases); an enforced content security policy would be, and the app does not enforce
 * one yet. What it reads, and only that:
 * - in a page: the value of each attribute named in ADDRESS_ATTRIBUTES, on any element and in any case of its name;
 *   each candidate of a srcset or imagesrcset list; each item of a ping or archive list; the address of a meta refresh;
 *   and the CSS of its style elements and of its style attributes, read as a stylesheet is. Comments are not read.
 * - in a stylesheet: each url(), each string of an image-set() or -webkit-image-set(), and each @import, after CSS
 *   escapes are decoded. Comments are not read.
 * An attribute's value is decoded first: numeric character references, and the named ones in NAMED_REFERENCES. A named
 * reference not in that list is a form the check does not decode, and fails the build as such.
 * An address is then judged as a browser's URL parser reads it: leading and trailing spaces and control characters
 * dropped, tabs and line ends taken out, a backslash read as a slash. It is the app's own when it is then empty, a
 * fragment, a data: address or a path with neither scheme nor host; with a scheme (http, https, blob or another) or a
 * host (//host) it is outside.
 * Not read: what the built scripts fetch or load when they run (a browser's request log shows that, by hand), an
 * attribute not in the list, a CSS function or at-rule whose name is written with an escape (an escaped @import among
 * them), the inside of a data: address, and any built file that is not a page or a stylesheet. Nor these writings,
 * which a browser reads otherwise than the check: an element after an abrupt comment (<!--> or <!--->, which a
 * browser closes at once, while the check reads to the next -->), a url( left open at the end of a style attribute
 * or of a stylesheet (which CSS still reads as an address, while the check needs its closing bracket), an @import
 * whose string is left open at the end of a stylesheet (which CSS still reads), and a meta refresh whose address holds
 * an apostrophe (a well-formed address, which the check reads as ending at the apostrophe and so does not read).
 * Usage: node scripts/check-built-page.mjs <build folder>
 */
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

/** The attributes whose value the check reads as an address, on any element. */
export const ADDRESS_ATTRIBUTES = [
  'action', 'archive', 'background', 'cite', 'classid', 'codebase', 'data', 'dynsrc', 'formaction', 'href', 'icon',
  'imagesrcset', 'longdesc', 'lowsrc', 'manifest', 'ping', 'poster', 'profile', 'src', 'srcset', 'xlink:href',
];
const SRCSET_LISTS = new Set(['srcset', 'imagesrcset']);
const SPACE_LISTS = new Set(['ping', 'archive']);

/** The named character references the check decodes in an attribute's value; any other is not read. */
export const NAMED_REFERENCES = {
  amp: '&', apos: "'", bsol: '\\', colon: ':', equals: '=', gt: '>', lt: '<', NewLine: '\n', num: '#', percnt: '%',
  period: '.', quest: '?', quot: '"', sol: '/', Tab: '\t',
};

/** Whether an address a page or stylesheet would load is outside the app's own origin, read as a URL parser reads it. */
export function isOutside(address) {
  const value = address
    .replace(/^[\u0000-\u0020]+|[\u0000-\u0020]+$/g, '')
    .replace(/[\t\n\r]/g, '')
    .replace(/\\/g, '/');
  if (value === '' || value.startsWith('#')) return false;
  if (/^data:/i.test(value)) return false;
  if (value.startsWith('//')) return true;
  return /^[a-z][a-z0-9+.-]*:/i.test(value);
}

/**
 * An attribute's value with its character references decoded, or null when it holds a named reference the check does
 * not decode. A named reference with no semicolon before "=" or a letter or digit stays as written, as a browser keeps
 * it in an attribute.
 */
export function decodeAttribute(value) {
  let unread = false;
  const text = value.replace(/&(?:#(\d+);?|#[xX]([0-9a-fA-F]+);?|([a-zA-Z][a-zA-Z0-9]*)(;?))/g, (whole, dec, hex, name, semi, at) => {
    if (dec !== undefined || hex !== undefined) {
      const code = dec !== undefined ? Number(dec) : parseInt(hex, 16);
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : '\uFFFD';
    }
    if (semi === ';' && Object.hasOwn(NAMED_REFERENCES, name)) return NAMED_REFERENCES[name];
    if (semi === '' && /^[=a-zA-Z0-9]/.test(value.slice(at + whole.length))) return whole;
    unread = true;
    return whole;
  });
  return unread ? null : text;
}

/** A CSS value with its escapes decoded: a backslash and up to six hex digits (and one white space after), or any one character. */
export function decodeCss(value) {
  return value.replace(/\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(\r\n|[\s\S]))/g, (whole, hex, char) => {
    if (hex !== undefined) {
      const code = parseInt(hex, 16);
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : '\uFFFD';
    }
    return /^[\n\r\f]/.test(char) ? '' : char;
  });
}

/** The addresses of a srcset list: each candidate's address, before its descriptors, as a browser splits the list. */
export function srcsetAddresses(value) {
  const addresses = [];
  let i = 0;
  while (i < value.length) {
    while (i < value.length && /[\s,]/.test(value[i])) i++;
    if (i >= value.length) break;
    const start = i;
    while (i < value.length && !/\s/.test(value[i])) i++;
    const address = value.slice(start, i);
    if (address.endsWith(',')) {
      addresses.push(address.replace(/,+$/, ''));
      continue;
    }
    addresses.push(address);
    for (let depth = 0; i < value.length; i++) {
      if (value[i] === '(') depth++;
      else if (value[i] === ')') depth = Math.max(0, depth - 1);
      else if (value[i] === ',' && depth === 0) {
        i++;
        break;
      }
    }
  }
  return addresses;
}

/** The address of a meta refresh's content ("5; url=..."), or null when it names none. */
export function refreshAddress(content) {
  const m = /^\s*[\d.]*\s*[;,]?\s*(?:url\s*=\s*)?(['"]?)([^'"]*)\1?\s*$/i.exec(content);
  const address = m?.[2]?.trim() ?? '';
  return address === '' ? null : address;
}

const withoutCssComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const CSS_STRING = /'((?:[^'\\]|\\[\s\S])*)'|"((?:[^"\\]|\\[\s\S])*)"/g;

/** The end of a function's arguments that open at `from`: the index of its closing parenthesis, strings skipped. */
function closingParen(text, from) {
  let depth = 1;
  for (let i = from; i < text.length; i++) {
    const c = text[i];
    if (c === '"' || c === "'") {
      for (i++; i < text.length && text[i] !== c; i++) if (text[i] === '\\') i++;
    } else if (c === '(') depth++;
    else if (c === ')' && --depth === 0) return i;
  }
  return text.length;
}

/** The outside addresses a stylesheet would load, as [form, address]: each url(), each image-set() string, each @import. */
export function stylesheetOutsideAddresses(css) {
  const text = withoutCssComments(css);
  const found = [];
  // An unquoted url()'s escape may be followed by one white space, which is the escape's own (url(https\3a //x))
  const pattern = /@import\s+(?:'((?:[^'\\]|\\[\s\S])*)'|"((?:[^"\\]|\\[\s\S])*)")|url\(\s*(?:'((?:[^'\\]|\\[\s\S])*)'|"((?:[^"\\]|\\[\s\S])*)"|((?:[^)\s\\]|\\[0-9a-fA-F]{1,6}[ \t\n\r\f]?|\\[\s\S])*))\s*\)|((?:-webkit-)?image-set)\(/gi;
  for (const m of text.matchAll(pattern)) {
    if (m[6] !== undefined) {
      const inside = text.slice(m.index + m[0].length, closingParen(text, m.index + m[0].length));
      for (const s of inside.matchAll(CSS_STRING)) {
        const address = decodeCss(s[1] ?? s[2] ?? '');
        if (isOutside(address)) found.push([`${m[6].toLowerCase()}()`, address]);
      }
      continue;
    }
    const form = m[1] !== undefined || m[2] !== undefined ? '@import' : 'url()';
    const address = decodeCss(m[1] ?? m[2] ?? m[3] ?? m[4] ?? m[5] ?? '');
    if (isOutside(address)) found.push([form, address]);
  }
  return found;
}

const TAG = /<([a-zA-Z][^\s/>]*)((?:"[^"]*"|'[^']*'|[^'">])*)>/g;
const ATTRIBUTE = /([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

/**
 * The outside addresses a page would load, as [form, address] in document order: each address attribute of each
 * element (its form "<element> <attribute>"), with a meta refresh's address and the CSS of each style element and style
 * attribute where they stand. A value holding a character reference the check does not decode is given as such.
 */
export function pageOutsideAddresses(html) {
  const text = html.replace(/<!--[\s\S]*?-->/g, '');
  const found = [];
  const styles = [...text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi)];
  for (const tag of text.matchAll(TAG)) {
    const element = tag[1].toLowerCase();
    const attributes = [...tag[2].matchAll(ATTRIBUTE)].map((a) => [a[1].toLowerCase(), a[2] ?? a[3] ?? a[4] ?? '']);
    const refresh = element === 'meta' && attributes.some(([name, value]) => name === 'http-equiv' && decodeAttribute(value)?.trim().toLowerCase() === 'refresh');
    for (const [name, raw] of attributes) {
      const read = ADDRESS_ATTRIBUTES.includes(name) || name === 'style' || (refresh && name === 'content');
      if (!read) continue;
      const value = decodeAttribute(raw);
      const form = `${element} ${name}`;
      if (value === null) {
        found.push([`${form}, a character reference the check does not decode`, raw]);
        continue;
      }
      if (name === 'style') {
        found.push(...stylesheetOutsideAddresses(value).map(([f, address]) => [`${form} ${f}`, address]));
        continue;
      }
      const addresses = refresh && name === 'content'
        ? [refreshAddress(value)].filter((a) => a !== null)
        : SRCSET_LISTS.has(name)
          ? srcsetAddresses(value)
          : SPACE_LISTS.has(name)
            ? value.split(/[\s,]+/).filter(Boolean)
            : [value];
      for (const address of addresses) if (isOutside(address)) found.push([refresh && name === 'content' ? 'meta refresh' : form, address]);
    }
    const style = styles.find((s) => s.index === tag.index);
    if (style) found.push(...stylesheetOutsideAddresses(style[1]).map(([f, address]) => [`style ${f}`, address]));
  }
  return found;
}

/** Every outside address in the built pages and stylesheets of a build folder, as [file, form, address], files sorted. */
export function builtOutsideAddresses(dir) {
  const files = fs.readdirSync(dir, { recursive: true }).map(String).filter((f) => /\.(html|css)$/.test(f)).sort();
  return files.flatMap((file) => {
    const text = fs.readFileSync(path.join(dir, file), 'utf8');
    const found = file.endsWith('.html') ? pageOutsideAddresses(text) : stylesheetOutsideAddresses(text);
    return found.map(([form, address]) => [file.split(path.sep).join('/'), form, address]);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const dir = process.argv[2];
  if (!dir || !fs.existsSync(dir)) {
    console.error('[check-built-page] no build folder given, or none there');
    process.exit(1);
  }
  const outside = builtOutsideAddresses(dir);
  if (outside.length > 0) {
    console.error(`[check-built-page] ${outside.length} address(es) the check reads in the built pages and stylesheets are outside the app's own origin, or not decoded:`);
    for (const [file, form, address] of outside) console.error(`  ${file}: ${form}: ${address}`);
    process.exit(1);
  }
  console.log("[check-built-page] OK: in the forms the check reads, as the build writes its pages and stylesheets, no address is outside the app's own origin; this check is no guarantee of what a page loads (an enforced content security policy would be, and the app does not enforce one yet)");
}
