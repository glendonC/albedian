// No #! line, as verify-public-tracked.mjs: run it with node.
/**
 * The built page check, which the web build runs after vite build (apps/web/package.json). It reads every built page
 * (*.html) and every built stylesheet (*.css) under the build folder, and fails the build when an address it reads
 * there is outside the app's own origin, or when an inline form in a built page falls outside the content policy. It
 * reads pages with jsdom, an HTML parser the web app already declares, so HTML comments, character references, unquoted
 * attributes, templates and style elements are read as a browser parses them, and reads stylesheets in the forms listed
 * below. This check is no guarantee of what a page loads (a page written to mislead it among the cases); an enforced
 * content security policy would be, and the app does not enforce one yet. What it reads, and only that:
 * - in a page (through jsdom, templates included): the value of each attribute named in ADDRESS_ATTRIBUTES, on any
 *   element and in any case of its name; each candidate of a srcset or imagesrcset list; each item of a ping or archive
 *   list; the address of a meta refresh (shared declarative refresh steps); and the CSS of its style elements and of
 *   its style attributes, read as a stylesheet is. Comments are not read.
 * - in a stylesheet: each url(), each string of an image-set() or -webkit-image-set(), and each @import, after CSS
 *   comments outside strings and unquoted url() tokens are stripped and CSS escapes are decoded.
 * An address is judged as a browser's URL parser reads it: leading and trailing spaces and control characters dropped,
 * tabs and line ends taken out, a backslash read as a slash. It is the app's own when it is then empty, a fragment, a
 * data: address or a path with neither scheme nor host; with a scheme (http, https, blob or another) or a host (//host)
 * it is outside.
 * Not read: what the built scripts fetch or load when they run (a browser's request log shows that, by hand), an
 * attribute not in the list, a CSS function or at-rule whose name is written with an escape (an escaped @import among
 * them), the inside of a data: address, any built file that is not a page or a stylesheet, a url( left open at the end
 * of a style attribute or of a stylesheet (which CSS still reads as an address, while the check needs its closing
 * bracket), and an @import whose string is left open at the end of a stylesheet (which CSS still reads).
 * It also holds each built page to the content policy (scripts/content-policy.mjs, which the preview server sends),
 * reading the same parsed document: each style element's text must hash to one of the policy's STYLE_HASHES, each script
 * element without a src must hash to one of its SCRIPT_HASHES (the first frame's script, first-frame-inline-one), and a
 * page may hold no event attribute (any attribute whose name begins with "on" and a letter is read as one) and no style
 * attribute, neither of which the policy allows; a template's content is read as the page is.
 * Nothing of the page is run or fetched.
 * Usage: node scripts/check-built-page.mjs <build folder>
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { SCRIPT_HASHES, STYLE_HASHES, scriptHash, styleHash } from './content-policy.mjs';

/** jsdom, from the web app, which declares it (the check lives outside the app and declares nothing of its own). */
const { JSDOM } = createRequire(path.join(path.dirname(fileURLToPath(import.meta.url)), '../apps/web/package.json'))('jsdom');

/** Every element of a parsed page in document order, a template's content read where the template stands. */
function* elementsOf(root) {
  for (const el of root.querySelectorAll('*')) {
    yield el;
    if (el.localName === 'template' && el.content) yield* elementsOf(el.content);
  }
}

/** The attributes whose value the check reads as an address, on any element. */
export const ADDRESS_ATTRIBUTES = [
  'action', 'archive', 'background', 'cite', 'classid', 'codebase', 'data', 'dynsrc', 'formaction', 'href', 'icon',
  'imagesrcset', 'longdesc', 'lowsrc', 'manifest', 'ping', 'poster', 'profile', 'src', 'srcset', 'xlink:href',
];
const SRCSET_LISTS = new Set(['srcset', 'imagesrcset']);
const SPACE_LISTS = new Set(['ping', 'archive']);

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

/** An attribute's value with its character references decoded by the HTML5 parser, as a browser reads it. */
export function decodeAttribute(value) {
  return new JSDOM(`<x a="${value.replace(/"/g, '&quot;')}"></x>`).window.document.querySelector('x').getAttribute('a');
}

const preprocessCss = (value) => value.replace(/\r\n|[\r\f]/g, '\n').replace(/\u0000/g, '\uFFFD');

/** One CSS escape starting at `from` (the index after `\`): [decoded, nextIndex], on preprocessed text. */
function readCssEscape(text, from) {
  if (from >= text.length) return ['\uFFFD', from];
  const hexMatch = /^[0-9a-fA-F]{1,6}/.exec(text.slice(from));
  if (hexMatch) {
    let end = from + hexMatch[0].length;
    if (end < text.length && /[\t\n ]/.test(text[end])) end++;
    const code = parseInt(hexMatch[0], 16);
    const decoded = code > 0 && code <= 0x10ffff && (code < 0xd800 || code > 0xdfff) ? String.fromCodePoint(code) : '\uFFFD';
    return [decoded, end];
  }
  return [text[from], from + 1];
}

/** A CSS value with its escapes decoded: a backslash and up to six hex digits (and one white space after, CR LF included), or any one character. */
export function decodeCss(value) {
  const input = preprocessCss(value);
  let out = '';
  for (let i = 0; i < input.length; ) {
    if (input[i] === '\\') {
      if (input[i + 1] === '\n') i += 2;
      else {
        const [ch, next] = readCssEscape(input, i + 1);
        out += ch;
        i = next;
      }
    } else {
      out += input[i++];
    }
  }
  return out;
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

const HTML_SPACE = '[\\t\\n\\f\\r ]';
const REFRESH_PREFIX = new RegExp(
  `^${HTML_SPACE}*[\\d.]+(?:${HTML_SPACE}*[;,]|${HTML_SPACE})${HTML_SPACE}*(?:url${HTML_SPACE}*=${HTML_SPACE}*)?([\\s\\S]*)$`,
  'i',
);
const HTML_SPACE_ENDS = new RegExp(`^${HTML_SPACE}+|${HTML_SPACE}+$`, 'g');

/** The address of a meta refresh's content ("5; url=..."), or null when it names none, by HTML5's refresh steps. */
export function refreshAddress(content) {
  const m = REFRESH_PREFIX.exec(content);
  if (!m) return null;
  const rest = m[1];
  let raw = rest;
  if (rest[0] === '"' || rest[0] === "'") {
    const inner = rest.slice(1);
    const end = inner.indexOf(rest[0]);
    raw = end >= 0 ? inner.slice(0, end) : inner;
  }
  const address = raw.replace(HTML_SPACE_ENDS, '');
  return address === '' ? null : address;
}

const isValidEscape = (text, i) => text[i] === '\\' && text[i + 1] !== '\n';

function wordAtomEnd(text, i) {
  const c = text[i];
  if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c === '_' || c === '-' || c >= '\u0080') {
    return i + 1;
  }
  if (isValidEscape(text, i)) return readCssEscape(text, i + 1)[1];
  return -1;
}

function readWord(text, from) {
  let i = from;
  for (let next; (next = wordAtomEnd(text, i)) > i; i = next);
  return [text.slice(from, i), i];
}

function consumeBadUrlRemnants(text, from) {
  let i = from;
  while (i < text.length) {
    if (text[i] === ')') return i + 1;
    if (isValidEscape(text, i)) [, i] = readCssEscape(text, i + 1);
    else i++;
  }
  return i;
}

function readUrlToken(text, from) {
  let i = from;
  let value = '';
  while (i < text.length) {
    const c = text[i];
    if (c === ')') return [{ type: 'url', value }, i + 1];
    if (/[\t\n ]/.test(c)) {
      while (i < text.length && /[\t\n ]/.test(text[i])) i++;
      if (i < text.length && text[i] === ')') return [{ type: 'url', value }, i + 1];
      return [{ type: 'bad-url' }, consumeBadUrlRemnants(text, i)];
    }
    if (c === '"' || c === "'" || c === '(' || (c === '\\' && text[i + 1] === '\n')) {
      return [{ type: 'bad-url' }, consumeBadUrlRemnants(text, i + 1)];
    }
    if (c === '\\') {
      const [ch, next] = readCssEscape(text, i + 1);
      value += ch;
      i = next;
    } else {
      value += c;
      i++;
    }
  }
  // Open url( at EOF: listed in Not read
  return [{ type: 'bad-url' }, i];
}

/** Tokenize a stylesheet per CSS Syntax Module Level 3 Section 4.3 for @import, url() and image-set(). */
function cssTokens(css) {
  const text = preprocessCss(css);
  const tokens = [];
  for (let i = 0; i < text.length; ) {
    const c = text[i];
    if (c === '/' && text[i + 1] === '*') {
      const closedAt = text.slice(i + 2).indexOf('*/');
      i = closedAt >= 0 ? i + 4 + closedAt : text.length;
      continue;
    }
    if (/[\t\n ]/.test(c)) {
      while (i < text.length && /[\t\n ]/.test(text[i])) i++;
      tokens.push({ type: 'ws' });
      continue;
    }
    if (c === '"' || c === "'") {
      let j = i + 1;
      let value = '';
      let closed = false;
      while (j < text.length) {
        if (text[j] === c) {
          j++;
          closed = true;
          break;
        }
        if (text[j] === '\n') break;
        if (text[j] === '\\') {
          if (j + 1 >= text.length) j++;
          else if (text[j + 1] === '\n') j += 2;
          else {
            const [ch, next] = readCssEscape(text, j + 1);
            value += ch;
            j = next;
          }
        } else {
          value += text[j++];
        }
      }
      tokens.push(closed ? { type: 'string', value } : j >= text.length ? { type: 'eof-string', value } : { type: 'bad-string' });
      i = j;
      continue;
    }
    if (c === '@' || c === '#') {
      const [raw, next] = readWord(text, i + 1);
      tokens.push(c === '@' && raw.toLowerCase() === 'import' ? { type: 'at-import' } : { type: 'other' });
      i = next;
      continue;
    }
    if (wordAtomEnd(text, i) > i) {
      const [raw, next] = readWord(text, i);
      const plain = raw.toLowerCase();
      if (text[next] === '(') {
        if (plain === 'url') {
          let ws = next + 1;
          while (ws < text.length && /[\t\n ]/.test(text[ws])) ws++;
          if (text[ws] === '"' || text[ws] === "'") {
            tokens.push({ type: 'fn', name: 'url' });
            i = next + 1;
          } else {
            const [tok, end] = readUrlToken(text, ws);
            tokens.push(tok);
            i = end;
          }
        } else {
          tokens.push({ type: 'fn', name: plain });
          i = next + 1;
        }
      } else {
        tokens.push({ type: 'other' });
        i = next;
      }
      continue;
    }
    if (c === '(') tokens.push({ type: 'paren-open' });
    else if (c === ')') tokens.push({ type: 'paren-close' });
    else tokens.push({ type: 'other' });
    i++;
  }
  return tokens;
}

/** The outside addresses a stylesheet would load, as [form, address]: each url(), each image-set() string, each @import. */
export function stylesheetOutsideAddresses(css) {
  const tokens = cssTokens(css);
  const found = [];
  const scopes = [];
  const skipWs = (idx) => {
    while (idx < tokens.length && tokens[idx].type === 'ws') idx++;
    return idx;
  };
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (tok.type === 'at-import') {
      const m = skipWs(i + 1);
      if (tokens[m]?.type === 'string' && isOutside(tokens[m].value)) found.push(['@import', tokens[m].value]);
    } else if (tok.type === 'url') {
      if (isOutside(tok.value)) found.push(['url()', tok.value]);
    } else if (tok.type === 'fn') {
      scopes.push(tok.name === 'image-set' || tok.name === '-webkit-image-set' ? `${tok.name}()` : null);
      if (tok.name === 'url') {
        const m = skipWs(i + 1);
        const n = skipWs(m + 1);
        if (tokens[m]?.type === 'string' && tokens[n]?.type === 'paren-close' && isOutside(tokens[m].value)) {
          found.push(['url()', tokens[m].value]);
        }
      }
    } else if (tok.type === 'paren-open') {
      scopes.push(null);
    } else if (tok.type === 'paren-close') {
      scopes.pop();
    } else if ((tok.type === 'string' || tok.type === 'eof-string') && scopes.at(-1)) {
      if (isOutside(tok.value)) found.push([scopes.at(-1), tok.value]);
    }
  }
  return found;
}

/**
 * The outside addresses a page would load, as [form, address] in document order, the page read by jsdom: each address
 * attribute of each element (its form "<element> <attribute>"), with a meta refresh's address and the CSS of each style
 * element and style attribute where they stand, a template's content included.
 */
export function pageOutsideAddresses(html) {
  const found = [];
  for (const el of elementsOf(new JSDOM(html).window.document)) {
    const element = el.localName.toLowerCase();
    const refresh = element === 'meta' && (el.getAttribute('http-equiv') ?? '').trim().toLowerCase() === 'refresh';
    for (const name of el.getAttributeNames()) {
      const read = ADDRESS_ATTRIBUTES.includes(name) || name === 'style' || (refresh && name === 'content');
      if (!read) continue;
      const value = el.getAttribute(name);
      const form = `${element} ${name}`;
      if (name === 'style') {
        found.push(...stylesheetOutsideAddresses(value).map(([f, address]) => [`${form} ${f}`, address]));
        continue;
      }
      let addresses;
      if (name === 'content') {
        const refreshed = refreshAddress(value);
        addresses = refreshed === null ? [] : [refreshed];
      } else if (SRCSET_LISTS.has(name)) {
        addresses = srcsetAddresses(value);
      } else if (SPACE_LISTS.has(name)) {
        addresses = value.split(/[\s,]+/);
      } else {
        addresses = [value];
      }
      for (const address of addresses) if (isOutside(address)) found.push([name === 'content' ? 'meta refresh' : form, address]);
    }
    if (element === 'style') {
      found.push(...stylesheetOutsideAddresses(el.textContent).map(([f, address]) => [`style ${f}`, address]));
    }
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

/**
 * What of a page the content policy would refuse, as [form, what] in document order, the page read by jsdom: a style
 * element whose text's hash is not one of the policy's style hashes, a script element without a src whose text's hash is
 * not one of its script hashes (named by its first sixty characters, trimmed), an on... event attribute, a style
 * attribute.
 */
export function policyMisfits(html, hashes = STYLE_HASHES, scriptHashes = SCRIPT_HASHES) {
  const found = [];
  for (const el of elementsOf(new JSDOM(html).window.document)) {
    if (el.localName === 'style') {
      const hash = styleHash(el.textContent ?? '');
      if (!hashes.includes(hash)) found.push(['style element, its hash not in the content policy', hash]);
    }
    if (el.localName === 'script' && !el.hasAttribute('src') && !scriptHashes.includes(scriptHash(el.textContent ?? ''))) {
      found.push(['inline script, its hash not in the content policy', (el.textContent ?? '').trim().slice(0, 60)]);
    }
    for (const name of el.getAttributeNames()) {
      const lower = name.toLowerCase();
      if (!/^on[a-z]/.test(lower) && lower !== 'style') continue;
      const what = lower === 'style' ? 'a style attribute' : 'an event attribute';
      found.push([`${el.localName} ${lower}, ${what} the content policy allows none of`, el.getAttribute(name) ?? '']);
    }
  }
  return found;
}

/** What the content policy would refuse in the built pages of a build folder, as [file, form, what], files sorted. */
export function builtPolicyMisfits(dir) {
  const files = fs.readdirSync(dir, { recursive: true }).map(String).filter((f) => /\.html$/.test(f)).sort();
  return files.flatMap((file) => policyMisfits(fs.readFileSync(path.join(dir, file), 'utf8')).map(([form, what]) => [file.split(path.sep).join('/'), form, what]));
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const dir = process.argv[2];
  if (!dir || !fs.existsSync(dir)) {
    console.error('[check-built-page] no build folder given, or none there');
    process.exit(1);
  }
  const outside = builtOutsideAddresses(dir);
  if (outside.length > 0) {
    console.error(`[check-built-page] ${outside.length} address(es) the check reads in the built pages and stylesheets are outside the app's own origin:`);
    for (const [file, form, address] of outside) console.error(`  ${file}: ${form}: ${address}`);
    process.exit(1);
  }
  const misfits = builtPolicyMisfits(dir);
  if (misfits.length > 0) {
    console.error(`[check-built-page] ${misfits.length} inline form(s) in the built pages that the content policy would refuse:`);
    for (const [file, form, what] of misfits) console.error(`  ${file}: ${form}: ${what}`);
    process.exit(1);
  }
  console.log("[check-built-page] OK: in the forms the check reads, as the build writes its pages and stylesheets, no address is outside the app's own origin and no inline form falls outside the content policy; this check is no guarantee of what a page loads (an enforced content security policy would be, and the app does not enforce one yet)");
}
