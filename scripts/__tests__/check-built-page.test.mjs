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
import { SCRIPT_HASHES, STYLE_HASHES, scriptHash, styleHash } from '../content-policy.mjs';
import {
  ADDRESS_ATTRIBUTES,
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
    for (const spike of ['apps/web/spike/listening/render.mjs', 'apps/web/spike/music/render.mjs']) {
      const source = fs.readFileSync(path.join(repoRoot, spike), 'utf8');
      expect(source, spike).not.toMatch(/fonts\.(googleapis|gstatic)\.com/i);
      expect(source, spike).not.toMatch(/rel\s*=\s*["']?preconnect/i);
      const pageStyle = /const PAGE_STYLE = `([\s\S]*?)`;/.exec(source)?.[1];
      const pageHead = /const html = `(<meta charset="utf-8">[\s\S]*?)<main>/.exec(source)?.[1];
      expect(pageStyle, `${spike} PAGE_STYLE`).toBeDefined();
      expect(pageHead, `${spike} writePage head`).toBeDefined();
      expect(stylesheetOutsideAddresses(pageStyle), `${spike} PAGE_STYLE`).toEqual([]);
      expect(pageOutsideAddresses(pageHead.replace('${PAGE_STYLE}', pageStyle)), `${spike} writePage`).toEqual([]);
    }
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
    ['\u0000https://example.com/0', true],
    ['\u0001https://example.com/c', true],
    ['\u001fhttps://example.com/1f', true],
    ['\u0020https://example.com/20', true],
    ['\u0021https://example.com/21', false],
    ['https://example.com/trail\u0000', true],
    ['   ', false],
    ['ht\ttps://example.com/t', true],
    ['https:\n//example.com/n', true],
    ['ht\rtps://example.com/r', true],
    ['ht\u0008tps://example.com/b8', false],
    ['ht\vtps://example.com/v', false],
    ['ht\ftps://example.com/f', false],
    ['ht\u000etps://example.com/e', false],
    ['/\\example.com/b', true],
    ['\\\\example.com/b', true],
    ['/fonts\\x.woff2', false],
    ['/path?next=https://example.com', false],
    ['custom+v1.0-x:foo', true],
    ['1http://example.com/x', false],
    ['a_b:foo', false],
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
    ['a CSS image-set string with an escaped quote before ")"', '<style>.a { background-image: image-set("https://example.com/a\\"b)c.png" 1x); }</style>', [['style image-set()', 'https://example.com/a"b)c.png']]],
    ['a CSS IMAGE-SET in capitals with single-quoted ")" and nested url() before a second string', "<style>.a { background-image: IMAGE-SET(url(/own.png) 1x, 'https://example.com/s\\'b)c.png' 2x); }</style>", [['style image-set()', "https://example.com/s'b)c.png"]]],
    ['a CSS image-set in a style attribute', '<div style="background: -webkit-image-set(\'https://example.com/w.png\' 1x)"></div>', [['div style -webkit-image-set()', 'https://example.com/w.png']]],
    ['a scheme written as a numeric reference', '<img src="https&#58;//example.com/e.png">', [['img src', 'https://example.com/e.png']]],
    ['a scheme written as a hex reference', '<img src="https&#x3A;//example.com/h.png">', [['img src', 'https://example.com/h.png']]],
    ['a scheme written as a named reference', '<img src="https&colon;&sol;&sol;example.com/n.png">', [['img src', 'https://example.com/n.png']]],
    ['a host after a slash and a backslash', '<img src="/\\example.com/b.png">', [['img src', '/\\example.com/b.png']]],
    ['a tab inside the scheme', '<img src="ht&Tab;tps://example.com/t.png">', [['img src', 'ht\ttps://example.com/t.png']]],
    ['a scheme written with a CSS escape', '<style>.a { background: url(https\\3a //example.com/c.png); }</style>', [['style url()', 'https://example.com/c.png']]],
    ['a scheme written with a CSS escape and CR LF', '<style>.a { background: url(https\\3a\r\n//example.com/crlf.png); }</style>', [['style url()', 'https://example.com/crlf.png']]],
    ['a quoted CSS url continued across CR LF', '<style>.a { background: url("https:\\\r\n//example.com/cont.png"); }</style>', [['style url()', 'https://example.com/cont.png']]],
    ['a CSS @import with no space before its quote', '<style>@import"https://example.com/tight.css";@import\'https://example.com/single.css\';</style>', [['style @import', 'https://example.com/tight.css'], ['style @import', 'https://example.com/single.css']]],
    ['a CSS @import with a comment before its quote', '<style>@import/*c*/"https://example.com/c.css";</style>', [['style @import', 'https://example.com/c.css']]],
    ['a CSS rule after strings holding comment-open characters', "<style>.a::before { content: \"/*\"; }\n.b { background: url(\"https://example.com/after-str.png\"); }\n/* end */\n.c::before { content: '/*'; }\n.d { background: url('https://example.com/after-single.png'); }\n/* end2 */</style>", [['style url()', 'https://example.com/after-str.png'], ['style url()', 'https://example.com/after-single.png']]],
    ['a CSS unquoted url holding comment-open characters in lower and upper case, and a comment after a quoted url() string', '<style>.a { background: url(https://example.com/*p*/u.png); } .b { background: URL(https://example.com/*p*/upper.png); } /* end */ .c { background: url("https://example.com/q.png" /*c*/); } .d { background: url(https\\://example.com/a\\ b\\)c.png); }</style>', [['style url()', 'https://example.com/*p*/u.png'], ['style url()', 'https://example.com/*p*/upper.png'], ['style url()', 'https://example.com/q.png'], ['style url()', 'https://example.com/a b)c.png']]],
    ['a noscript in the body holding an outside image', '<body><noscript><img src="https://example.com/noscript-body.png"></noscript></body>', [['img src', 'https://example.com/noscript-body.png']]],
    ['a link after an inline script holding "<!--" and a later HTML comment', '<script>const s = "<!--";</script><link rel="stylesheet" href="https://example.com/after-script.css"><!-- footer -->', [['link href', 'https://example.com/after-script.css']]],
    ['an unquoted meta refresh content holding "="', '<meta http-equiv=refresh content=0;url=https://example.com/u>', [['meta refresh', 'https://example.com/u']]],
    ['a quoted meta refresh followed by a semicolon', '<meta http-equiv="refresh" content="0; url=\'https://example.com/semi\';">', [['meta refresh', 'https://example.com/semi']]],
    ['an unquoted meta refresh address holding an apostrophe', "<meta http-equiv=\"refresh\" content=\"0; url=https://example.com/don't\">", [['meta refresh', "https://example.com/don't"]]],
    ['a space-separated meta refresh with no semicolon', '<meta http-equiv="refresh" content="0 https://example.com/space">', [['meta refresh', 'https://example.com/space']]],
    ['an unquoted srcset holding "=" and an encoded space', '<img srcset=/own.png?v=1,&#32;https://example.com/srcset.png>', [['img srcset', 'https://example.com/srcset.png']]],
    ['an img with an apostrophe in an unquoted attribute before src', "<img alt=don't src='https://example.com/apos.png'>", [['img src', 'https://example.com/apos.png']]],
    ['a style element whose attribute quotes ">" and "</style>"', '<style data-x="a>b</style>">body{background:url(https://example.com/in-style.png)}</style>', [['style url()', 'https://example.com/in-style.png']]],
    ['a style element closed with an end-tag attribute, and an unclosed style at end', '<style>@import "https://example.com/s1.css";</style x><style>@import "https://example.com/s2.css";', [['style @import', 'https://example.com/s1.css'], ['style @import', 'https://example.com/s2.css']]],
    ['an img after an abrupt HTML comment', '<!--><img src="https://example.com/abrupt1.png"><!---><img src="https://example.com/abrupt2.png">', [['img src', 'https://example.com/abrupt1.png'], ['img src', 'https://example.com/abrupt2.png']]],
    ['an svg feImage with mixed-case localName, lower-cased in the reported form', '<svg><feImage href="https://example.com/fe.png"/></svg>', [['feimage href', 'https://example.com/fe.png']]],
    ['an svg template element where content is undefined, holding an outside image', '<svg><template><image href="https://example.com/svgt.png"/></template></svg>', [['image href', 'https://example.com/svgt.png']]],
    ['a style element carrying both a style attribute and CSS body, reported in order', '<style style="background: url(https://example.com/attr.png)">.a { background: url(https://example.com/body.png) }</style>', [['style style url()', 'https://example.com/attr.png'], ['style url()', 'https://example.com/body.png']]],
    ['a meta refresh carrying a style attribute before content, both reported in order', '<meta style="background: url(https://example.com/ms.png)" http-equiv="  Refresh  " content="0; url=https://example.com/m">', [['meta style url()', 'https://example.com/ms.png'], ['meta refresh', 'https://example.com/m']]],
    ['a template with a style attribute, an inside img, and a sibling img, in document order', '<template style="background: url(https://example.com/ts.png)"><img src="https://example.com/ti.png"></template><img src="https://example.com/after.png">', [['template style url()', 'https://example.com/ts.png'], ['img src', 'https://example.com/ti.png'], ['img src', 'https://example.com/after.png']]],
    ["an object's archive list separated by a comma with no space", '<object archive="  /own.jar,https://example.com/a.jar  "></object>', [['object archive', 'https://example.com/a.jar']]],
    ["a link's ping list", '<a href="/b" ping="/own https://example.com/p">', [['a ping', 'https://example.com/p']]],
    ['an attribute holding ">" before the address', '<img alt="a > b" src="https://example.com/q.png">', [['img src', 'https://example.com/q.png']]],
    ['an element and an attribute in upper case', '<IMG SRC="https://example.com/u.png">', [['img src', 'https://example.com/u.png']]],
  ];
  it.each(FORMS)('%s', (_name, page, refused) => {
    let got;
    expect(() => {
      got = pageOutsideAddresses(page);
    }).not.toThrow();
    expect(got, refused.map(([form, address]) => `${form}: ${address}`).join('; ')).toEqual(refused);
  });

  // The same forms holding the app's own addresses: none is refused
  const OWN = [
    ['an own base address', '<base href="/">'],
    ['a candidate list of own addresses and a data address with commas', '<img srcset="data:image/png;base64,AA,BB 1x, /b.png 2x, /c.png 100w">'],
    ['a meta refresh to an own path, one with no address, and an unread attribute on a refresh meta', '<meta http-equiv="refresh" content="5; url=/next"><meta http-equiv="refresh" content="30" data-x="https://example.com/not-read">'],
    ['a non-refresh meta or non-meta element with http-equiv and content', '<meta name="description" content="0; url=https://example.com/not-refresh"><div http-equiv="refresh" content="0; url=https://example.com/not-meta"></div>'],
    ['an own query string holding "&" before "=", underscores and bare parameters', '<img src="/a.png?x=1&y=2"><img src="/api/media?x=1&asset_id=2"><img src="/api/media?x=1&raw"><img src="https&nope;//example.com/r.png">'],
    ['an attribute the check does not read', '<div data-src="https://example.com/x.png" title="See https://example.com"></div>'],
    ['a style element and attribute with own addresses and comment-split tokens', '<style>.a { background: image-set("/a.png" 1x, url(/b.png) 2x); x: ur/*c*/l("https://example.com/split.png"); }</style><div style="background: url(/c.png)"></div>'],
  ];
  it.each(OWN)('%s', (_name, page) => {
    let got;
    expect(() => {
      got = pageOutsideAddresses(page);
    }).not.toThrow();
    expect(got).toEqual([]);
  });

  it('handles CSS Syntax Level 3 token boundaries: C1-C5, bad-string/bad-url tokens, ident/at/hash/dimension boundaries, and EOF forms', () => {
    // AUDIT F C1: custom function/hash/dimension/at-rule ending in url must not enter <url-token> mode
    expect(
      stylesheetOutsideAddresses('.a { --x: my-url(a/*); } "*/); }\n.b { content: "/*"; background: url("https://example.com/leak1.png"); }\n/* end */'),
    ).toEqual([['url()', 'https://example.com/leak1.png']]);
    for (const prefix of [
      'aurl', 'zurl', 'Aurl', 'Zurl', '0url', '9url', '_url', '-url', '--url', '\u0080url', '\u0000url',
      '@url', '#url', '#0url', '\\#url', '\\20 url', '10url', '1.5e+2url', 'url0', 'url9', 'url_a', 'URL_A',
    ]) {
      expect(stylesheetOutsideAddresses(`.a { --x: ${prefix}(https://example.com/not-url.png); }`), prefix).toEqual([]);
      expect(
        stylesheetOutsideAddresses(`.a { --x: ${prefix}(a /*); } "*/); }\n.b { content: "/*"; background: url("https://example.com/p.png"); }\n/* end */`),
        `${prefix} comment`,
      ).toEqual([['url()', 'https://example.com/p.png']]);
    }
    expect(stylesheetOutsideAddresses('.a { --x: \u007furl(https://example.com/del.png); }')).toEqual([
      ['url()', 'https://example.com/del.png'],
    ]);
    expect(stylesheetOutsideAddresses('.a { --x: \\\nurl(https://example.com/after-bad-escape.png); }')).toEqual([
      ['url()', 'https://example.com/after-bad-escape.png'],
    ]);
    expect(stylesheetOutsideAddresses('#import "https://example.com/not-import.css";')).toEqual([]);
    expect(stylesheetOutsideAddresses('@IMPORT "https://example.com/upper-import.css";')).toEqual([
      ['@import', 'https://example.com/upper-import.css'],
    ]);
    expect(stylesheetOutsideAddresses('@@import "https://example.com/double-at.css";')).toEqual([
      ['@import', 'https://example.com/double-at.css'],
    ]);
    expect(stylesheetOutsideAddresses('/*/ url("https://example.com/inside-open-comment.png") */')).toEqual([]);
    expect(stylesheetOutsideAddresses('/**/ @import "https://example.com/after-empty-comment.css";')).toEqual([
      ['@import', 'https://example.com/after-empty-comment.css'],
    ]);
    // AUDIT F C2: unclosed string on line 1 (<bad-string-token> at unescaped newline, LF/CR/CRLF/FF) must not swallow subsequent lines or @import/image-set
    for (const nl of ['\n', '\r', '\r\n', '\f']) {
      expect(
        stylesheetOutsideAddresses(`.a { content: "unclosed${nl}; }\n.b { content: "/*"; background: url("https://example.com/leak2.png"); }\n/* end */`),
      ).toEqual([['url()', 'https://example.com/leak2.png']]);
      expect(
        stylesheetOutsideAddresses(`@import "unclosed${nl}; .b { background: url("https://example.com/after-bad-import.png"); }`),
      ).toEqual([['url()', 'https://example.com/after-bad-import.png']]);
    }
    expect(
      stylesheetOutsideAddresses('.a { background-image: image-set("unclosed\n, "https://example.com/after-bad-set.png" 1x); }'),
    ).toEqual([['image-set()', 'https://example.com/after-bad-set.png']]);
    // AUDIT F C3: <bad-url-token> holding /* and space/quote/( or escaped \) consumes remnants to unescaped ) without opening a comment or accepting the URL
    expect(
      stylesheetOutsideAddresses('.a { background: url(foo/*bar baz); }\n.b { background: url("https://example.com/leak3.png"); }\n/* end */'),
    ).toEqual([['url()', 'https://example.com/leak3.png']]);
    expect(
      stylesheetOutsideAddresses('.a { background: url(foo bar\\)/*not-comment); }\n.b { background: url("https://example.com/leak3b.png"); }\n/* end */'),
    ).toEqual([['url()', 'https://example.com/leak3b.png']]);
    expect(
      stylesheetOutsideAddresses('.a { background-image: image-set(url(foo "bar) 1x, "https://example.com/leak3c.png" 2x); }'),
    ).toEqual([['image-set()', 'https://example.com/leak3c.png']]);
    for (const badCh of ['"', "'", '(']) {
      expect(stylesheetOutsideAddresses(`.a { background: url(https://example.com/a${badCh}b.png); }`), badCh).toEqual([]);
      expect(
        stylesheetOutsideAddresses(`.a { background: url(foo${badCh}/*bar); }\n.b { background: url("https://example.com/leak3q.png"); }\n/* end */`),
        `${badCh} comment`,
      ).toEqual([['url()', 'https://example.com/leak3q.png']]);
    }
    // AUDIT F C4 & C5: escaped \) and \" inside unquoted url() inside image-set(), plus nested non-url functions like type("...") and closed scopes
    expect(
      stylesheetOutsideAddresses('.a { background-image: image-set(url(/own\\)a.png) 1x, "https://example.com/leak4.png" 2x); }'),
    ).toEqual([['image-set()', 'https://example.com/leak4.png']]);
    expect(
      stylesheetOutsideAddresses('.a { background-image: image-set(url(/own\\"a.png) 1x, "https://example.com/a)b.png" 2x); }'),
    ).toEqual([['image-set()', 'https://example.com/a)b.png']]);
    expect(
      stylesheetOutsideAddresses('.a { background-image: image-set(url(/own.png) type("https://example.com/not-image-mime"), ("https://example.com/in-parens.png") 1x, "https://example.com/real.png" 2x); content: "https://example.com/after-closed-set.png"; }'),
    ).toEqual([['image-set()', 'https://example.com/real.png']]);
    // Raw CSS CR LF hex escapes, padded whitespace in unquoted url(), unclosed image-set/comments/strings/url( at EOF, escaped at-rule/function names (Not read)
    expect(stylesheetOutsideAddresses('.a { background: url(  https\\3a\r\n//example.com/crlf.png  ); }')).toEqual([
      ['url()', 'https://example.com/crlf.png'],
    ]);
    expect(stylesheetOutsideAddresses('.b { background-image: image-set("https://example.com/open-set.png" 1x')).toEqual([
      ['image-set()', 'https://example.com/open-set.png'],
    ]);
    expect(stylesheetOutsideAddresses('.b { background-image: image-set("https://example.com/eof-str.png')).toEqual([
      ['image-set()', 'https://example.com/eof-str.png'],
    ]);
    expect(stylesheetOutsideAddresses('.b { background-image: image-set("https://example.com/eof-bs\\')).toEqual([
      ['image-set()', 'https://example.com/eof-bs'],
    ]);
    expect(stylesheetOutsideAddresses('@import "https://example.com/open-import.css')).toEqual([]);
    expect(stylesheetOutsideAddresses('.a { background: url("https://example.com/open-q.png"')).toEqual([]);
    expect(stylesheetOutsideAddresses('.a { background: url(https://example.com/open-u.png')).toEqual([]);
    expect(stylesheetOutsideAddresses('@\\69 mport "https://example.com/escaped-import.css"; .a { background: u\\72 l("https://example.com/escaped-url.png"); }')).toEqual([]);
    expect(stylesheetOutsideAddresses('.a { --x: my-url("https://example.com/custom-fn.png"); }')).toEqual([]);
    expect(stylesheetOutsideAddresses('.a { color: red; } /* unclosed url(https://example.com/eof.png)')).toEqual([]);
    expect(stylesheetOutsideAddresses('.a { w: url(/*c*/"https://example.com/bad-q.png"); y: url(https:\\\n//example.com/bad1.png); z: url(https:\\\r//example.com/bad2.png); u: url(https:\\\f//example.com/bad3.png); }')).toEqual([]);
    expect(decodeCss('abc\\')).toBe('abc\uFFFD');
    expect(decodeCss('a\u0000b\\\u0000')).toBe('a\uFFFDb\uFFFD');
  });

  it('reads every attribute listed in ADDRESS_ATTRIBUTES on any element', () => {
    expect(ADDRESS_ATTRIBUTES).toHaveLength(21);
    for (const attr of ADDRESS_ATTRIBUTES) {
      expect(pageOutsideAddresses(`<x ${attr}="https://example.com/a"></x>`), attr).toEqual([[`x ${attr}`, 'https://example.com/a']]);
    }
  });

  it('decodes attribute character references as HTML5 does, keeping query parameters with underscores or no "="', () => {
    expect(decodeAttribute('a&#58;b&#x2F;c&amp;d&colon;')).toBe('a:b/c&d:');
    expect(decodeAttribute('/a?x=1&y=2')).toBe('/a?x=1&y=2');
    expect(decodeAttribute('/api/media?x=1&asset_id=2')).toBe('/api/media?x=1&asset_id=2');
    expect(decodeAttribute('/api/media?x=1&raw')).toBe('/api/media?x=1&raw');
    expect(decodeAttribute('&nope;')).toBe('&nope;');
    expect(decodeAttribute('x &amp y')).toBe('x & y');
    expect(decodeAttribute('x &amp=1')).toBe('x &amp=1');
    expect(decodeAttribute('a"b&colon;c')).toBe('a"b:c');
    expect(decodeAttribute('a"b"c&colon;d')).toBe('a"b"c:d');
    expect(decodeAttribute('&#0;')).toBe('�');
  });

  it('decodes CSS escapes: hex with its one white space (CR LF included), lone surrogates and out-of-range to U+FFFD, and line ends continued', () => {
    expect(decodeCss('https\\3a //x')).toBe('https://x');
    expect(decodeCss('https\\3a\t//x')).toBe('https://x');
    expect(decodeCss('https\\3a\n//x')).toBe('https://x');
    expect(decodeCss('https\\3a\r//x')).toBe('https://x');
    expect(decodeCss('https\\3a\f//x')).toBe('https://x');
    expect(decodeCss('https\\3a\r\n//x')).toBe('https://x');
    expect(decodeCss('https\\3a  x')).toBe('https: x');
    expect(decodeCss('https\\3a\r\n\nx')).toBe('https:\nx');
    expect(decodeCss('https\\3a\vx')).toBe('https:\vx');
    expect(decodeCss('\\68 ttps:\\/\\/x')).toBe('https://x');
    expect(decodeCss('\\9 x\\00003a1\\09afAF ')).toBe('\tx:1' + String.fromCodePoint(0x09afaf));
    expect(decodeCss('\\/ x\\: x\\` x\\g x\\@ x\\G x\\_ x\\z x')).toBe('/ x: x` xg x@ xG x_ xz x');
    expect(decodeCss('a\\\nb\\\r\nc\\\rd\\\fe\\\tf\\\vg\\ h')).toBe('abcde\tf\vg h');
    expect(decodeCss('\\0')).toBe('\uFFFD');
    expect(decodeCss('\\1')).toBe('\u0001');
    expect(decodeCss('\\d7ff')).toBe('\uD7FF');
    expect(decodeCss('\\d800')).toBe('\uFFFD');
    expect(decodeCss('\\dfff')).toBe('\uFFFD');
    expect(decodeCss('\\e000')).toBe('\uE000');
    expect(decodeCss('\\10ffff')).toBe(String.fromCodePoint(0x10ffff));
    let outOfRange;
    expect(() => {
      outOfRange = decodeCss('\\110000');
    }).not.toThrow();
    expect(outOfRange).toBe('\uFFFD');
  });

  it('splits a candidate list as a browser does, and reads the address of a refresh', () => {
    expect(srcsetAddresses('/a.png 1x, /b.png 2x')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('data:image/png;base64,AA,BB 1x,/c.png')).toEqual(['data:image/png;base64,AA,BB', '/c.png']);
    expect(srcsetAddresses('/a.png, /b.png')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('/a.png,, /b.png')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('/a.png 1x (x, y), /b.png')).toEqual(['/a.png', '/b.png']);
    expect(srcsetAddresses('/a.png 1x ), /b.png')).toEqual(['/a.png', '/b.png']);
    expect(refreshAddress('0; url=https://example.com/a')).toBe('https://example.com/a');
    expect(refreshAddress('.5; url=https://example.com/dot')).toBe('https://example.com/dot');
    expect(refreshAddress("0;URL='https://example.com/b'")).toBe('https://example.com/b');
    expect(refreshAddress("0; url='https://example.com/semi';")).toBe('https://example.com/semi');
    expect(refreshAddress("0; url=\"https://example.com/a'b\";")).toBe("https://example.com/a'b");
    expect(refreshAddress('0; "https://example.com/q"; extra')).toBe('https://example.com/q');
    expect(refreshAddress('0 ; "https://example.com/sp"; extra')).toBe('https://example.com/sp');
    expect(refreshAddress('0;"https://example.com/tight"; extra')).toBe('https://example.com/tight');
    expect(refreshAddress('0; url = "https://example.com/eq"; extra')).toBe('https://example.com/eq');
    expect(refreshAddress('0; url= "https://example.com/eq2"; extra')).toBe('https://example.com/eq2');
    expect(refreshAddress('0; url="  https://example.com/lead"')).toBe('https://example.com/lead');
    expect(refreshAddress('0; url=https://example.com/trail  ')).toBe('https://example.com/trail');
    expect(refreshAddress('0; url="\vhttps://example.com/v"')).toBe('\vhttps://example.com/v');
    expect(refreshAddress("0; url=https://example.com/don't")).toBe("https://example.com/don't");
    expect(refreshAddress("0; url='https://example.com/open")).toBe('https://example.com/open');
    expect(refreshAddress('0, https://example.com/c')).toBe('https://example.com/c');
    expect(refreshAddress('0 https://example.com/d')).toBe('https://example.com/d');
    expect(refreshAddress('\t\n\f\r 0\t;\nurl\f=\rhttps://example.com/ws')).toBe('https://example.com/ws');
    expect(refreshAddress('0\thttps://example.com/t')).toBe('https://example.com/t');
    expect(refreshAddress('0\nhttps://example.com/n')).toBe('https://example.com/n');
    expect(refreshAddress('0\fhttps://example.com/f')).toBe('https://example.com/f');
    expect(refreshAddress('0\rhttps://example.com/r')).toBe('https://example.com/r');
    expect(refreshAddress('0\vhttps://example.com/v')).toBeNull();
    expect(refreshAddress('30')).toBeNull();
    expect(refreshAddress('30   ')).toBeNull();
    expect(refreshAddress('30; url=')).toBeNull();
    expect(refreshAddress('0; url=""; https://example.com/after')).toBeNull();
    expect(refreshAddress('0; url="   "')).toBeNull();
    expect(refreshAddress('; url=https://example.com/no-time')).toBeNull();
    expect(refreshAddress('abc 0; url=https://example.com/prefix')).toBeNull();
    expect(refreshAddress(' url/local')).toBeNull();
    expect(refreshAddress('10px')).toBeNull();
    expect(refreshAddress('url/local')).toBeNull();
  });
});

describe('the built page holds nothing inline the content policy would refuse', () => {
  it("passes the app's own page, whose one inline style and one inline script are the policy's hashes, and reads both", () => {
    const page = fs.readFileSync(path.join(webDir, 'index.html'), 'utf8');
    expect(policyMisfits(page)).toEqual([]);
    // With no hash allowed, the same page is refused for its style: the style is read, not passed over
    expect(policyMisfits(page, [])).toEqual([['style element, its hash not in the content policy', STYLE_HASHES[0]]]);
    // first-frame-inline-one: and with no script hash allowed, for its first frame's script, read the same way
    const script = [...page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)].filter((m) => !/\bsrc\s*=/i.test(m[1]))[0][2];
    expect(SCRIPT_HASHES).toEqual([scriptHash(script)]);
    expect(policyMisfits(page, STYLE_HASHES, [])).toEqual([['inline script, its hash not in the content policy', script.trim().slice(0, 60)]]);
  });

  // An inline script passes only where its text hashes to one of the policy's script hashes, as a browser holds it
  const SCRIPTS = [scriptHash('x()')];
  it.each([
    ['an inline script whose text is the allowed hash', '<script>x()</script>', []],
    ['the same script with one more space, a different hash', '<script>x() </script>', [['inline script, its hash not in the content policy', 'x()']]],
    ['the same script in capitals of its tag', '<SCRIPT>x()</SCRIPT>', []],
    ['the allowed script twice', '<script>x()</script><script>x()</script>', []],
    ['the allowed script beside one that is not, the other reported', '<script>x()</script><script>y()</script>', [['inline script, its hash not in the content policy', 'y()']]],
    ['the allowed script inside a template', '<template><script>x()</script></template>', []],
    ['the allowed script with an event attribute, which stays refused', '<script onload="z()">x()</script>', [['script onload, an event attribute the content policy allows none of', 'z()']]],
    ['a style whose text is the allowed script hash, still refused as a style', '<style>x()</style>', [['style element, its hash not in the content policy', styleHash('x()')]]],
  ])('%s', (_name, page, refused) => {
    expect(policyMisfits(page, [], SCRIPTS)).toEqual(refused);
  });

  it("refuses a script whose text is an allowed style's hash: a style hash lets no script through", () => {
    expect(policyMisfits('<script>x()</script>', [styleHash('x()')], [])).toEqual([['inline script, its hash not in the content policy', 'x()']]);
  });

  // Each inline form the policy would refuse, with what the check names, and the forms it lets through; the page is
  // read by an HTML parser (jsdom), so each is read as a browser parses it
  const OWN = '<style>body { margin: 0 }</style>';
  const hashes = [styleHash('body { margin: 0 }')];
  const MISFITS = [
    ['a style whose hash the policy holds', OWN, []],
    ['a style it does not', '<style>body { margin: 1px }</style>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')]]],
    ['a script with a source', '<script type="module" src="/assets/a.js"></script>', []],
    ['an inline script', '<script>fetch("/x")</script>', [['inline script, its hash not in the content policy', 'fetch("/x")']]],
    ['an inline module script', '<script type="module">\n  import "/a.js";\n</script>', [['inline script, its hash not in the content policy', 'import "/a.js";']]],
    ['an event attribute in lower case', '<link rel="stylesheet" href="/a.css" onload="this.media=\'all\'">', [["link onload, an event attribute the content policy allows none of", "this.media='all'"]]],
    ['an event attribute in upper case', '<IMG SRC="/a.png" ONERROR="x()">', [['img onerror, an event attribute the content policy allows none of', 'x()']]],
    ['a style attribute on a div', '<div style="color: red"></div>', [['div style, a style attribute the content policy allows none of', 'color: red']]],
    ['each of them in a comment', '<!-- <script>x()</script><div style="a" onclick="b"></div><style>c</style> -->', []],
    ['an attribute whose name holds "on" after its start', '<div data-onclick="x" aria-label="on"></div>', []],
    ['any attribute named "on" and a letter, from a to z, read as an event attribute', '<div ona="1" onz="2"></div>', [['div ona, an event attribute the content policy allows none of', '1'], ['div onz, an event attribute the content policy allows none of', '2']]],
    ['an attribute named "on" alone, "on" and no letter, or "style" with more characters, not read as one', '<meta name="viewport" content="width=device-width"><div on="1" on-x="2" on1="3" stylex="4" data-style="5"></div>', []],
    // The reader's three (AUDIT K, web-csp-1): each form in capitals, and a script between two comments
    ['a style element in capitals', '<STYLE>body { margin: 1px }</STYLE>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')]]],
    ['a script element in capitals', '<SCRIPT>x()</SCRIPT>', [['inline script, its hash not in the content policy', 'x()']]],
    ['an inline script between two comments', '<!-- a --><script>x()</script><!-- b -->', [['inline script, its hash not in the content policy', 'x()']]],
    // Each pattern read whole: every comment, every style and every script of a page, each on its own
    ['two comments, each holding a form', '<!-- <script>a()</script> --><p>between</p><!-- <div style="b"></div> -->', []],
    ['two style elements, the second not the policy\'s', `${OWN}<style>x</style>`, [['style element, its hash not in the content policy', styleHash('x')]]],
    ['two scripts, the first with a source', '<script src="/b.js"></script><script>a()</script>', [['inline script, its hash not in the content policy', 'a()']]],
    ['a script whose double-quoted attribute holds ">"', '<script data-x="a>b">x()</script>', [['inline script, its hash not in the content policy', 'x()']]],
    ["a script whose single-quoted attribute holds \">\"", "<script data-x='a>b'>x()</script>", [['inline script, its hash not in the content policy', 'x()']]],
    // AUDIT G's three (web-csp-1 v2): a style over several lines, a comment over several lines, a script ended by "</script >"
    ['a style element over several lines', '<style>\n  body { margin: 1px }\n</style>', [['style element, its hash not in the content policy', styleHash('\n  body { margin: 1px }\n')]]],
    ['a comment over several lines holding a form', '<!--\n<div style="a"></div>\n-->', []],
    ['an inline script ended by "</script >"', '<script>x()</script >', [['inline script, its hash not in the content policy', 'x()']]],
    // The reader's two notes: a comment closed by "--!>", and an end tag with an attribute, each read as a browser reads it
    ['a script after a comment closed by "--!>" before a later comment', '<!-- a --!><script>x()</script><!-- b -->', [['inline script, its hash not in the content policy', 'x()']]],
    ['an inline script ended by an end tag with an attribute', '<script>x()</script data-y="z"><p>after</p>', [['inline script, its hash not in the content policy', 'x()']]],
    ['an inline script inside a template', '<template><script>x()</script></template>', [['inline script, its hash not in the content policy', 'x()']]],
    ['an inline script inside a template that is inside another', '<template><template><div><script>x()</script></div></template></template>', [['inline script, its hash not in the content policy', 'x()']]],
    ['a long inline script, trimmed and cut to sixty characters', `<script>  a${'x'.repeat(58)}bc  </script>`, [['inline script, its hash not in the content policy', `a${'x'.repeat(58)}b`]]],
    // AUDIT H's eleven (web-csp-1 v3) and full-walk order/branch pins
    ['an event attribute on an allowed style element', '<style onload="x()">body { margin: 0 }</style>', [['style onload, an event attribute the content policy allows none of', 'x()']]],
    ['an event attribute on a disallowed style element, both reported in order', '<style onload="x()">body { margin: 1px }</style>', [['style element, its hash not in the content policy', styleHash('body { margin: 1px }')], ['style onload, an event attribute the content policy allows none of', 'x()']]],
    ['an inline script that also carries an event attribute, both reported in order', '<script onclick="x()">y()</script>', [['inline script, its hash not in the content policy', 'y()'], ['script onclick, an event attribute the content policy allows none of', 'x()']]],
    ['an event attribute on an external script with a source', '<script src="/assets/a.js" onload="x()"></script>', [['script onload, an event attribute the content policy allows none of', 'x()']]],
    ['an inline script inside an svg element named template, where content is undefined', '<svg><template><script>x()</script></template></svg>', [['inline script, its hash not in the content policy', 'x()']]],
    ['a template with a style attribute before the inline script inside its content', '<template style="color: red"><script>x()</script></template>', [['template style, a style attribute the content policy allows none of', 'color: red'], ['inline script, its hash not in the content policy', 'x()']]],
    ['an inline script inside a template followed by a sibling inline script, in document order', '<template><script>a()</script></template><script>b()</script>', [['inline script, its hash not in the content policy', 'a()'], ['inline script, its hash not in the content policy', 'b()']]],
    ['two disallowed style elements, both reported in document order', '<style>a{}</style><style>b{}</style>', [['style element, its hash not in the content policy', styleHash('a{}')], ['style element, its hash not in the content policy', styleHash('b{}')]]],
    ['two inline scripts, both reported in document order', '<script>a()</script><script>b()</script>', [['inline script, its hash not in the content policy', 'a()'], ['inline script, its hash not in the content policy', 'b()']]],
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
    expect(r.out).toContain("[check-built-page] 1 address(es) the check reads in the built pages and stylesheets are outside the app's own origin:");
    expect(r.out).toContain('index.html: img src: https://example.com/i.png');
  });

  it('fails a build whose page holds an inline form the content policy would refuse, naming file, form and what', () => {
    const r = run({ 'index.html': `<head>${OWN_STYLE}</head>`, 'sub/page.html': '<script>x()</script>' });
    expect(r.status).toBe(1);
    expect(r.out).toContain('[check-built-page] 1 inline form(s) in the built pages that the content policy would refuse:');
    expect(r.out).toContain('sub/page.html: inline script, its hash not in the content policy: x()');
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
      ['a.html', 'inline script, its hash not in the content policy', 'a()'],
      ['a/z.html', 'inline script, its hash not in the content policy', 'az()'],
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
