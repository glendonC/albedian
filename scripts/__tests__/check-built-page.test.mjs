// @vitest-environment node
// The addresses the app's page and stylesheets name stay on the app's own origin, in the forms the check reads
// (web-fonts-1): the check the web build runs after vite build (scripts/check-built-page.mjs, over the built page and
// built stylesheets; its doc says which forms it reads and which it does not), and the same over the source page and
// every source stylesheet. It reads pages as the build writes them, and it is no guarantee of what a page loads: an
// enforced content security policy would be, and the app does not enforce one yet. What one load really asks for is
// also seen in a browser, by hand (said in the report).
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { STYLE_HASHES, styleHash } from '../content-policy.mjs';
import {
  builtOutsideAddresses,
  builtPolicyMisfits,
  decodeAttribute,
  decodeCss,
  isOutside,
  pageOutsideAddresses,
  policyMisfits,
  refreshAddress,
  srcsetAddresses,
  stylesheetOutsideAddresses,
} from '../check-built-page.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const webDir = path.join(repoRoot, 'apps/web');

describe("the app's page and stylesheets name no outside address the check reads", () => {
  it('names no outside address in its page, the preconnects and the font host gone', () => {
    const page = fs.readFileSync(path.join(webDir, 'index.html'), 'utf8');
    expect(pageOutsideAddresses(page)).toEqual([]);
    expect(page).not.toMatch(/preconnect/i);
    expect(page).not.toMatch(/fonts\.(googleapis|gstatic)\.com/i);
  });

  it('names no outside address in any stylesheet it ships', () => {
    const sheets = fs.readdirSync(path.join(webDir, 'src'), { recursive: true }).filter((f) => String(f).endsWith('.css'));
    expect(sheets.length).toBeGreaterThanOrEqual(6);
    const outside = sheets.flatMap((f) => stylesheetOutsideAddresses(fs.readFileSync(path.join(webDir, 'src', String(f)), 'utf8')).map(([form, a]) => `${f}: ${form}: ${a}`));
    expect(outside).toEqual([]);
  });

  it('says, when it passes, what it read and that it is no guarantee of what a page loads', () => {
    const dist = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-built-ok-'));
    try {
      fs.writeFileSync(path.join(dist, 'index.html'), '<script type="module" src="/assets/a.js"></script>');
      const r = spawnSync(process.execPath, [path.join(repoRoot, 'scripts/check-built-page.mjs'), dist], { encoding: 'utf8' });
      expect(r.status).toBe(0);
      expect(r.stdout.trim()).toBe(
        "[check-built-page] OK: in the forms the check reads, as the build writes its pages and stylesheets, no address is outside the app's own origin and no inline form falls outside the content policy; this check is no guarantee of what a page loads (an enforced content security policy would be, and the app does not enforce one yet)",
      );
    } finally {
      fs.rmSync(dist, { recursive: true, force: true });
    }
  });

  it('runs after every web build, on the built page and its styles', () => {
    const scripts = JSON.parse(fs.readFileSync(path.join(webDir, 'package.json'), 'utf8')).scripts;
    expect(scripts.build).toMatch(/&& vite build && node \.\.\/\.\.\/scripts\/check-built-page\.mjs dist$/);
  });
});

describe('what counts as outside', () => {
  const ROWS = [
    ['/fonts/geist/Geist-wght.woff2', false],
    ['fonts/x.woff2', false],
    ['./hud-tokens.css', false],
    ['data:font/woff2;base64,AAAA', false],
    ['DATA:image/png;base64,AA', false],
    ['#m', false],
    ['', false],
    ['https://fonts.googleapis.com/css2?family=Geist', true],
    ['http://example.com/a.css', true],
    ['HTTPS://EXAMPLE.COM/A', true],
    ['//cdn.example.com/x.js', true],
    ['blob:https://example.com/1', true],
    ['  https://example.com/x  ', true],
    // As a browser's URL parser reads it: controls and spaces at the ends dropped, tabs and line ends taken out, a
    // backslash read as a slash
    ['\u0001https://example.com/c', true],
    ['ht\ttps://example.com/t', true],
    ['https:\n//example.com/n', true],
    ['/\\example.com/b', true],
    ['\\\\example.com/b', true],
    ['/fonts\\x.woff2', false],
  ];
  it.each(ROWS)('%j is outside: %s', (address, outside) => {
    expect(isOutside(address)).toBe(outside);
  });

  it('finds the outside addresses in the forms it reads in a page, in document order, and none in a comment', () => {
    const page = [
      '<html><head>',
      '<link rel="preconnect" href="https://fonts.googleapis.com">',
      "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Geist'>",
      '<link rel="icon" href="data:image/svg+xml,%3Csvg%3E">',
      '<script type="module" src="/src/main.tsx"></script>',
      '<!-- <link rel="stylesheet" href="https://example.com/commented.css"> -->',
      '<noscript><link rel="stylesheet" href=https://example.com/n.css></noscript>',
      '<style>.a { background: url("https://example.com/s.png") }</style>',
      '</head><body><img src="//cdn.example.com/i.png"><div style="background-image: url(https://example.com/d.png)"></div>',
      '<template><script src="https://example.com/t.js"></script></template>',
      '</body></html>',
    ].join('\n');
    expect(pageOutsideAddresses(page)).toEqual([
      ['link href', 'https://fonts.googleapis.com'],
      ['link href', 'https://fonts.googleapis.com/css2?family=Geist'],
      ['link href', 'https://example.com/n.css'],
      ['style url()', 'https://example.com/s.png'],
      ['img src', '//cdn.example.com/i.png'],
      ['div style url()', 'https://example.com/d.png'],
      ['script src', 'https://example.com/t.js'],
    ]);
  });

  it("finds the outside addresses in a stylesheet's url() and @import, and none in a comment", () => {
    const css = [
      "@import 'https://example.com/a.css';",
      '@import url("https://example.com/b.css");',
      "@import './local.css';",
      "@font-face { font-family: X; src: url('https://example.com/x.woff2') format('woff2'), url(/fonts/x.woff2) format('woff2'); }",
      '.a { background: url(  "//cdn.example.com/p.png" ); }',
      '/* .c { background: url(https://example.com/commented.png) } */',
      '.b { background: url(data:image/png;base64,AA); mask: url(#m); }',
    ].join('\n');
    expect(stylesheetOutsideAddresses(css)).toEqual([
      ['@import', 'https://example.com/a.css'],
      ['url()', 'https://example.com/b.css'],
      ['url()', 'https://example.com/x.woff2'],
      ['url()', '//cdn.example.com/p.png'],
    ]);
  });

  it('reads every built page and stylesheet of a build folder, and nothing else there', () => {
    const dist = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-built-page-'));
    fs.mkdirSync(path.join(dist, 'assets'));
    fs.writeFileSync(path.join(dist, 'index.html'), '<link rel="stylesheet" href="/assets/a.css"><script src="https://example.com/x.js"></script>');
    fs.writeFileSync(path.join(dist, 'assets', 'a.css'), '@font-face{src:url(https://example.com/f.woff2)}.a{color:red}');
    fs.writeFileSync(path.join(dist, 'assets', 'b.js'), 'fetch("https://example.com/api")');
    try {
      expect(builtOutsideAddresses(dist)).toEqual([
        ['assets/a.css', 'url()', 'https://example.com/f.woff2'],
        ['index.html', 'script src', 'https://example.com/x.js'],
      ]);
    } finally {
      fs.rmSync(dist, { recursive: true, force: true });
    }
  });
});

describe('each form the check reads, refused by its form and its address', () => {
  // The reader's forms (AUDIT M, web-fonts-1), and the others a page can ask with that the reader listed: each page
  // asks one outside address in one form, and the check names that form and that address
  const FORMS = [
    ['a base address', '<base href="https://example.com/">', [['base href', 'https://example.com/']]],
    ['an image candidate list', '<img src="/a.png" srcset="/a.png 1x, https://example.com/a2.png 2x">', [['img srcset', 'https://example.com/a2.png']]],
    ['a preload candidate list', '<link rel="preload" as="image" imagesrcset="https://example.com/p.png 1x">', [['link imagesrcset', 'https://example.com/p.png']]],
    ["a video's poster", '<video poster="https://example.com/p.jpg"></video>', [['video poster', 'https://example.com/p.jpg']]],
    ["an object's data", '<object data="https://example.com/o.svg"></object>', [['object data', 'https://example.com/o.svg']]],
    ["a track's source", '<video><track src="https://example.com/t.vtt"></video>', [['track src', 'https://example.com/t.vtt']]],
    ["an image input's source", '<input type="image" src="https://example.com/i.png">', [['input src', 'https://example.com/i.png']]],
    ["an svg image's address", '<svg><image href="https://example.com/s.png"/><image xlink:href="https://example.com/x.png"/></svg>', [['image href', 'https://example.com/s.png'], ['image xlink:href', 'https://example.com/x.png']]],
    ['a meta refresh', '<meta http-equiv="Refresh" content="0; url=https://example.com/next">', [['meta refresh', 'https://example.com/next']]],
    ['a meta refresh whose http-equiv holds a character reference', '<meta http-equiv="&#114;efresh" content="0; url=https://example.com/r">', [['meta refresh', 'https://example.com/r']]],
    ["a body's background", '<body background="https://example.com/b.png">', [['body background', 'https://example.com/b.png']]],
    ['a CSS image-set', '<style>.a { background-image: image-set("https://example.com/i.png" 1x, url(/x.png) 2x); }</style>', [['style image-set()', 'https://example.com/i.png']]],
    ['a CSS image-set in a style attribute', '<div style="background: -webkit-image-set(\'https://example.com/w.png\' 1x)"></div>', [['div style -webkit-image-set()', 'https://example.com/w.png']]],
    ['a scheme written as a numeric reference', '<img src="https&#58;//example.com/e.png">', [['img src', 'https://example.com/e.png']]],
    ['a scheme written as a hex reference', '<img src="https&#x3A;//example.com/h.png">', [['img src', 'https://example.com/h.png']]],
    ['a scheme written as a named reference', '<img src="https&colon;&sol;&sol;example.com/n.png">', [['img src', 'https://example.com/n.png']]],
    ['a host after a slash and a backslash', '<img src="/\\example.com/b.png">', [['img src', '/\\example.com/b.png']]],
    ['a tab inside the scheme', '<img src="ht&Tab;tps://example.com/t.png">', [['img src', 'ht\ttps://example.com/t.png']]],
    ['a scheme written with a CSS escape', '<style>.a { background: url(https\\3a //example.com/c.png); }</style>', [['style url()', 'https://example.com/c.png']]],
    ['a reference the check does not decode', '<img src="https&nope;//example.com/r.png">', [['img src, a character reference the check does not decode', 'https&nope;//example.com/r.png']]],
    ["a link's ping list", '<a href="/b" ping="/own https://example.com/p">', [['a ping', 'https://example.com/p']]],
    ['an attribute holding ">" before the address', '<img alt="a > b" src="https://example.com/q.png">', [['img src', 'https://example.com/q.png']]],
    ['an element and an attribute in upper case', '<IMG SRC="https://example.com/u.png">', [['img src', 'https://example.com/u.png']]],
  ];
  it.each(FORMS)('%s', (_name, page, refused) => {
    expect(pageOutsideAddresses(page), refused.map(([form, address]) => `${form}: ${address}`).join('; ')).toEqual(refused);
  });

  // The same forms holding the app's own addresses: none is refused
  const OWN = [
    ['an own base address', '<base href="/">'],
    ['a candidate list of own addresses and a data address with commas', '<img srcset="data:image/png;base64,AA,BB 1x, /b.png 2x, /c.png 100w">'],
    ['a meta refresh to an own path, and one with no address', '<meta http-equiv="refresh" content="5; url=/next"><meta http-equiv="refresh" content="30">'],
    ['an own query string holding "&" before "="', '<img src="/a.png?x=1&y=2">'],
    ['an attribute the check does not read', '<div data-src="https://example.com/x.png" title="See https://example.com"></div>'],
    ['a style element and attribute with own addresses', '<style>.a { background: image-set("/a.png" 1x, url(/b.png) 2x) }</style><div style="background: url(/c.png)"></div>'],
  ];
  it.each(OWN)('%s', (_name, page) => {
    expect(pageOutsideAddresses(page)).toEqual([]);
  });

  it('decodes the references a browser decodes in an attribute, and gives back none it does not read', () => {
    expect(decodeAttribute('a&#58;b&#x2F;c&amp;d&colon;')).toBe('a:b/c&d:');
    expect(decodeAttribute('/a?x=1&y=2')).toBe('/a?x=1&y=2');
    expect(decodeAttribute('&nope;')).toBeNull();
    expect(decodeAttribute('x &amp y')).toBeNull();
    expect(decodeAttribute('&#0;')).toBe('�');
  });

  it('decodes CSS escapes: hex with its one white space, any other character, and a line end continued', () => {
    expect(decodeCss('https\\3a //x')).toBe('https://x');
    expect(decodeCss('\\68 ttps:\\/\\/x')).toBe('https://x');
    expect(decodeCss('a\\\nb')).toBe('ab');
  });

  it('splits a candidate list as a browser does, and reads the address of a refresh', () => {
    expect(srcsetAddresses('/a.png 1x, /b.png 2x')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('data:image/png;base64,AA,BB 1x,/c.png')).toEqual(['data:image/png;base64,AA,BB', '/c.png']);
    expect(srcsetAddresses('/a.png, /b.png')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('/a.png 1x (x, y), /b.png')).toEqual(['/a.png', '/b.png']);
    expect(refreshAddress('0; url=https://example.com/a')).toBe('https://example.com/a');
    expect(refreshAddress("0;URL='https://example.com/b'")).toBe('https://example.com/b');
    expect(refreshAddress('0, https://example.com/c')).toBe('https://example.com/c');
    expect(refreshAddress('30')).toBeNull();
  });
});

describe('the built page holds nothing inline the content policy would refuse', () => {
  it("passes the app's own page, whose one inline style is the policy's hash, and reads that style", () => {
    const page = fs.readFileSync(path.join(webDir, 'index.html'), 'utf8');
    expect(policyMisfits(page)).toEqual([]);
    // With no hash allowed, the same page is refused for its style: the style is read, not passed over
    expect(policyMisfits(page, [])).toEqual([['style element, its hash not in the content policy', STYLE_HASHES[0]]]);
  });

  // Each inline form the policy would refuse, with what the check names, and the forms it lets through; the page is
  // read by an HTML parser (jsdom), so each is read as a browser parses it
  const OWN = '<style>body { margin: 0 }</style>';
  const hashes = [styleHash('body { margin: 0 }')];
  const MISFITS = [
    ['a style whose hash the policy holds', OWN, []],
    ['a style it does not', '<style>body { margin: 1px }</style>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')]]],
    ['a script with a source', '<script type="module" src="/assets/a.js"></script>', []],
    ['an inline script', '<script>fetch("/x")</script>', [['inline script, which the content policy allows none of', 'fetch("/x")']]],
    ['an inline module script', '<script type="module">\n  import "/a.js";\n</script>', [['inline script, which the content policy allows none of', 'import "/a.js";']]],
    ['an event attribute in lower case', '<link rel="stylesheet" href="/a.css" onload="this.media=\'all\'">', [["link onload, an event attribute the content policy allows none of", "this.media='all'"]]],
    ['an event attribute in upper case', '<IMG SRC="/a.png" ONERROR="x()">', [['img onerror, an event attribute the content policy allows none of', 'x()']]],
    ['a style attribute on a div', '<div style="color: red"></div>', [['div style, a style attribute the content policy allows none of', 'color: red']]],
    ['each of them in a comment', '<!-- <script>x()</script><div style="a" onclick="b"></div><style>c</style> -->', []],
    ['an attribute whose name holds "on" after its start', '<div data-onclick="x" aria-label="on"></div>', []],
    ['any attribute named "on" and a letter, from a to z, read as an event attribute', '<div ona="1" onz="2"></div>', [['div ona, an event attribute the content policy allows none of', '1'], ['div onz, an event attribute the content policy allows none of', '2']]],
    ['an attribute named "on" alone, "on" and no letter, or "style" with more characters, not read as one', '<meta name="viewport" content="width=device-width"><div on="1" on-x="2" on1="3" stylex="4" data-style="5"></div>', []],
    // The reader's three (AUDIT K, web-csp-1): each form in capitals, and a script between two comments
    ['a style element in capitals', '<STYLE>body { margin: 1px }</STYLE>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')]]],
    ['a script element in capitals', '<SCRIPT>x()</SCRIPT>', [['inline script, which the content policy allows none of', 'x()']]],
    ['an inline script between two comments', '<!-- a --><script>x()</script><!-- b -->', [['inline script, which the content policy allows none of', 'x()']]],
    // Each pattern read whole: every comment, every style and every script of a page, each on its own
    ['two comments, each holding a form', '<!-- <script>a()</script> --><p>between</p><!-- <div style="b"></div> -->', []],
    ['two style elements, the second not the policy\'s', `${OWN}<style>x</style>`, [['style element, its hash not in the content policy', styleHash('x')]]],
    ['two scripts, the first with a source', '<script src="/b.js"></script><script>a()</script>', [['inline script, which the content policy allows none of', 'a()']]],
    ['a script whose double-quoted attribute holds ">"', '<script data-x="a>b">x()</script>', [['inline script, which the content policy allows none of', 'x()']]],
    ["a script whose single-quoted attribute holds \">\"", "<script data-x='a>b'>x()</script>", [['inline script, which the content policy allows none of', 'x()']]],
    // AUDIT G's three (web-csp-1 v2): a style over several lines, a comment over several lines, a script ended by "</script >"
    ['a style element over several lines', '<style>\n  body { margin: 1px }\n</style>', [['style element, its hash not in the content policy', styleHash('\n  body { margin: 1px }\n')]]],
    ['a comment over several lines holding a form', '<!--\n<div style="a"></div>\n-->', []],
    ['an inline script ended by "</script >"', '<script>x()</script >', [['inline script, which the content policy allows none of', 'x()']]],
    // The reader's two notes: a comment closed by "--!>", and an end tag with an attribute, each read as a browser reads it
    ['a script after a comment closed by "--!>" before a later comment', '<!-- a --!><script>x()</script><!-- b -->', [['inline script, which the content policy allows none of', 'x()']]],
    ['an inline script ended by an end tag with an attribute', '<script>x()</script data-y="z"><p>after</p>', [['inline script, which the content policy allows none of', 'x()']]],
    ['an inline script inside a template', '<template><script>x()</script></template>', [['inline script, which the content policy allows none of', 'x()']]],
    ['an inline script inside a template that is inside another', '<template><template><div><script>x()</script></div></template></template>', [['inline script, which the content policy allows none of', 'x()']]],
    ['a long inline script, trimmed and cut to sixty characters', `<script>  a${'x'.repeat(58)}bc  </script>`, [['inline script, which the content policy allows none of', `a${'x'.repeat(58)}b`]]],
    // AUDIT H's eleven (web-csp-1 v3) and full-walk order/branch pins
    ['an event attribute on an allowed style element', '<style onload="x()">body { margin: 0 }</style>', [['style onload, an event attribute the content policy allows none of', 'x()']]],
    ['an event attribute on a disallowed style element, both reported in order', '<style onload="x()">body { margin: 1px }</style>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')], ['style onload, an event attribute the content policy allows none of', 'x()']]],
    ['an inline script that also carries an event attribute, both reported in order', '<script onclick="x()">y()</script>', [['inline script, which the content policy allows none of', 'y()'], ['script onclick, an event attribute the content policy allows none of', 'x()']]],
    ['an event attribute on an external script with a source', '<script src="/assets/a.js" onload="x()"></script>', [['script onload, an event attribute the content policy allows none of', 'x()']]],
    ['an inline script inside an svg element named template, where content is undefined', '<svg><template><script>x()</script></template></svg>', [['inline script, which the content policy allows none of', 'x()']]],
    ['a template with a style attribute before the inline script inside its content', '<template style="color: red"><script>x()</script></template>', [['template style, a style attribute the content policy allows none of', 'color: red'], ['inline script, which the content policy allows none of', 'x()']]],
    ['an inline script inside a template followed by a sibling inline script, in document order', '<template><script>a()</script></template><script>b()</script>', [['inline script, which the content policy allows none of', 'a()'], ['inline script, which the content policy allows none of', 'b()']]],
    ['two disallowed style elements, both reported in document order', '<style>a{}</style><style>b{}</style>', [['style element, its hash not in the content policy', styleHash('a{}')], ['style element, its hash not in the content policy', styleHash('b{}')]]],
    ['two inline scripts, both reported in document order', '<script>a()</script><script>b()</script>', [['inline script, which the content policy allows none of', 'a()'], ['inline script, which the content policy allows none of', 'b()']]],
    ['a script with an empty src attribute, which HTML treats as external', '<script src="">ignored()</script>', []],
    ['an event attribute whose name has a digit after its leading letter', '<div ona1="1"></div>', [['div ona1, an event attribute the content policy allows none of', '1']]],
    ['both a style attribute and an event attribute on one element, both reported', '<div style="color: red" onclick="x()"></div>', [['div style, a style attribute the content policy allows none of', 'color: red'], ['div onclick, an event attribute the content policy allows none of', 'x()']]],
  ];
  it.each(MISFITS)('%s', (_name, page, refused) => {
    let got;
    expect(() => {
      got = policyMisfits(page, hashes);
    }).not.toThrow();
    expect(got, refused.map(([form, what]) => `${form}: ${what}`).join('; ')).toEqual(refused);
  });

  it("reads the policy's own hashes when none are given", () => {
    expect(STYLE_HASHES.length).toBeGreaterThan(0);
    expect(policyMisfits(OWN)).toEqual([['style element, its hash not in the content policy', hashes[0]]]);
  });
});

describe('the command the web build runs', () => {
  const CHECK = path.join(repoRoot, 'scripts/check-built-page.mjs');
  const OWN_STYLE = fs.readFileSync(path.join(webDir, 'index.html'), 'utf8').match(/<style\b[^>]*>[\s\S]*?<\/style\s*>/i)[0];
  /** A build folder holding these files, the command run on it, the folder removed: its exit and its output. */
  function run(files) {
    const dist = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-built-check-'));
    try {
      for (const [file, body] of Object.entries(files)) {
        fs.mkdirSync(path.dirname(path.join(dist, file)), { recursive: true });
        fs.writeFileSync(path.join(dist, file), body);
      }
      const r = spawnSync(process.execPath, [CHECK, dist], { encoding: 'utf8' });
      return { status: r.status, out: r.stdout + r.stderr };
    } finally {
      fs.rmSync(dist, { recursive: true, force: true });
    }
  }

  it('passes a build whose page and stylesheets the check finds nothing in, with its OK line', () => {
    const r = run({ 'index.html': `<head>${OWN_STYLE}</head><script type="module" src="/assets/a.js"></script>`, 'assets/a.css': '.a{background:url(/b.png)}' });
    expect(r.status).toBe(0);
    expect(r.out).toContain("[check-built-page] OK: in the forms the check reads, as the build writes its pages and stylesheets, no address is outside the app's own origin and no inline form falls outside the content policy; this check is no guarantee of what a page loads (an enforced content security policy would be, and the app does not enforce one yet)");
  });

  it('fails a build with an outside address, naming file, form and address', () => {
    const r = run({ 'index.html': `<head>${OWN_STYLE}</head><img src="https://example.com/i.png">` });
    expect(r.status).toBe(1);
    expect(r.out).toContain('index.html: img src: https://example.com/i.png');
  });

  it('fails a build whose page holds an inline form the content policy would refuse, naming file, form and what', () => {
    const r = run({ 'index.html': `<head>${OWN_STYLE}</head>`, 'sub/page.html': '<script>x()</script>' });
    expect(r.status).toBe(1);
    expect(r.out).toContain('[check-built-page] 1 inline form(s) in the built pages that the content policy would refuse:');
    expect(r.out).toContain('sub/page.html: inline script, which the content policy allows none of: x()');
    // Every .html page of the folder, sorted by path; b/c.html holds the app's own style, and non-.html files are skipped
    expect(builtPolicyMisfitsIn({
      'a.html': '<script>a()</script>',
      'a/z.html': '<script>az()</script>',
      'index.html': '<div style="a"></div>',
      'b/c.html': OWN_STYLE,
      'b/d.html': '<style>x</style>',
      'note.html.txt': '<script>y()</script>',
      'notehtml': '<script>w()</script>',
    })).toEqual([
      ['a.html', 'inline script, which the content policy allows none of', 'a()'],
      ['a/z.html', 'inline script, which the content policy allows none of', 'az()'],
      ['b/d.html', 'style element, its hash not in the content policy', styleHash('x')],
      ['index.html', 'div style, a style attribute the content policy allows none of', 'a'],
    ]);
  });

  /** builtPolicyMisfits over a folder made of these files. */
  function builtPolicyMisfitsIn(files) {
    const dist = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-built-misfits-'));
    try {
      for (const [file, body] of Object.entries(files)) {
        fs.mkdirSync(path.dirname(path.join(dist, file)), { recursive: true });
        fs.writeFileSync(path.join(dist, file), body);
      }
      return builtPolicyMisfits(dist);
    } finally {
      fs.rmSync(dist, { recursive: true, force: true });
    }
  }
});
