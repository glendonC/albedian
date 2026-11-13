#!/usr/bin/env node
/**
 * Run by hand, before any change to the home's layout is reported: no test, CI step or package script runs it.
 *
 * Checks, in headless Chrome, that nothing a refused upload says can move the home's Upload or its logo, that the
 * message stays inside the window within its bounds (two lines for the file, two for the reason), and that every part
 * of the message is drawn inside its box, the name's head giving way before its ending does. For each window size it
 * measures Upload and the logo with no refusal, then refuses a run of files whose names and reasons sit at the bounds
 * and far past them, in many scripts and in the widest characters, and measures again.
 *
 * Its own limit: a name made only of zero-width characters has an ending with no width, so the check that the
 * file's ending is on screen cannot hold for it (it looks for an ending with a width); such a name is not among the
 * cases.
 *
 * It uses only Node's built-ins (the DevTools protocol over Node's WebSocket) and an installed Chrome, run headless
 * with a throwaway profile that is deleted afterwards. The refusals never reach a server: the browser answers the
 * upload itself (the DevTools Fetch domain) with status 415 and the case's reason. Start the web app first
 * (`pnpm dev`), then:
 *
 *   node scripts/check_home_layout.mjs [--url http://localhost:5173] [--out <dir>]
 *
 * It prints one line per window size and case, writes a screenshot of each to <dir> when given, and exits 1 when any
 * check fails. CHROME_PATH overrides the browser (default: Google Chrome on macOS).
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: { url: { type: 'string', default: 'http://localhost:5173' }, out: { type: 'string' } },
});
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const SIZES = [
  { name: 'laptop', width: 1440, height: 900 },
  { name: 'phone', width: 390, height: 844 },
  { name: 'small-phone', width: 320, height: 568 },
];
const STORE_REASON = 'not a JPEG, PNG, TIFF, HEIC, QuickTime or MP4 file';
const words = (chars) => {
  let text = '';
  for (let i = 0; text.length < chars; i++) text += `${text ? ' ' : ''}word${i}`;
  return text.slice(0, chars);
};
const rep = (unit, n) => unit.repeat(n);
const spaced = (word, chars) => {
  let text = '';
  while (text.length < chars) text += `${text ? ' ' : ''}${word}`;
  return text;
};
// [label, file name, reason or null]; the bounds are homeRefusal.ts FILE_NAME_MAX_CHARS (32) and apiClient.ts
// UPLOAD_DETAIL_MAX_CHARS (240)
const CASES = [
  ['a short name and a store reason', 'notes.pdf', STORE_REASON],
  ['no reason', 'notes.pdf', null],
  ['the longest name that fits (32)', `${'n'.repeat(28)}.pdf`, STORE_REASON],
  ['one past it (33)', `${'n'.repeat(29)}.pdf`, STORE_REASON],
  ['a 244-character name', `${'n'.repeat(240)}.pdf`, STORE_REASON],
  ['the longest reason that fits (240)', 'notes.pdf', words(240)],
  ['one past it (241)', 'notes.pdf', words(241)],
  ['3,000 characters with no space', 'notes.pdf', 'r'.repeat(3000)],
  ['about 5,700 characters', 'notes.pdf', words(5700)],
  ['thousands of characters in both', `${'x'.repeat(3000)}.jpg`, words(5700)],
  // The second reader's cases (AUDIT F, arrive-home v2): scripts, the widest characters, and characters a person
  // sees as one though they are several code points
  ['an empty name', '', STORE_REASON],
  ['a one-letter name, no detail', 'a', null],
  ['Chinese: a 3,000-character name and a 4,200-character reason, no space', `${rep('\u661f', 3000)}.jpg`, rep('\u6587\u4ef6\u683c\u5f0f\u4e0d\u652f\u6301', 600)],
  ['Thai: 500 and 4,000 characters, no space', rep('\u0e44\u0e1f\u0e25\u0e4c', 125), rep('\u0e44\u0e21\u0e48\u0e23\u0e2d\u0e07\u0e23\u0e31\u0e1a\u0e44\u0e1f\u0e25\u0e4c\u0e19\u0e35\u0e49', 250)],
  ['Arabic: a name and a reason with spaces, right to left', `${spaced('\u0635\u0648\u0631\u0629', 300)}.jpg`, spaced('\u0627\u0644\u0645\u0644\u0641 \u063a\u064a\u0631 \u0645\u062f\u0639\u0648\u0645', 3000)],
  ['emoji: 600 in the name, 2,000 in the reason', `${rep('\u{1F30C}', 600)}.png`, rep('\u{1F6AB}', 2000)],
  ['combining marks: 1,000 in the name, 3,000 in the reason', `${rep('e\u0301', 1000)}.jpg`, rep('a\u0301', 3000)],
  ['a phone-style name ending in eight emoji', `IMG_${rep('\u{1F30C}', 8)}.jpg`, STORE_REASON],
  ['twelve wide CJK characters (no cut)', rep('\u753b', 12), STORE_REASON],
  ['fifteen CJK characters, no extension', '\u591c\u7a7a\u306e\u5199\u771f\u3092\u64ae\u308a\u307e\u3057\u305f\u6700\u9ad8\u306e\u591c', STORE_REASON],
  ['32 W, the widest Latin letter (no cut)', rep('W', 32), STORE_REASON],
  ['W: 5,000 in the name and in the reason', `${rep('W', 5000)}.jpg`, rep('W', 5000)],
  ['a name holding a newline and tabs', `${rep('night\t', 40)}\n${rep('sky ', 20)}.jpg`, STORE_REASON],
  ['a reason that is one 3,000-character path', 'notes.pdf', rep('/very-long-path-segment', 130)],
  ['twelve emoji, no extension', rep('\u{1F30C}', 12), STORE_REASON],
  ['forty emoji, no extension (a cut name)', rep('\u{1F30C}', 40), STORE_REASON],
  ['twelve cuneiform signs', rep('\u{1242B}', 12), STORE_REASON],
  ['a name ending in twelve of the widest glyph (U+FDFD)', `${rep('n', 50)}${rep('\uFDFD', 12)}`, STORE_REASON],
  ['a reason of the widest glyph', 'notes.pdf', rep('\uFDFD', 40)],
  ['forty family emoji joined by zero width joiners', rep('\u{1F469}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}', 40), STORE_REASON],
  ['forty thumbs with a skin tone', rep('\u{1F44D}\u{1F3FD}', 40), STORE_REASON],
];

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-home-layout-'));
const chrome = spawn(CHROME, ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--mute-audio', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const portFile = path.join(profile, 'DevToolsActivePort');
for (let tries = 0; !fs.existsSync(portFile); tries++) {
  if (tries > 150) throw new Error('chrome did not start');
  await sleep(100);
}
await sleep(200);
const [port, wsPath] = fs.readFileSync(portFile, 'utf8').trim().split('\n');
const ws = new WebSocket(`ws://127.0.0.1:${port}${wsPath}`);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});
let id = 0;
const pending = new Map();
const listeners = [];
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(new Error(JSON.stringify(msg.error)));
    else resolve(msg.result);
  } else if (msg.method) for (const listener of listeners) listener(msg);
};
const send = (method, params = {}, sessionId) =>
  new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

// In the page: the boxes of Upload, the logo and the message, and the message's lines against their line heights
const MEASURE = `(() => {
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; };
  const file = document.querySelector('.home-refusal__file');
  const reason = document.querySelector('.home-refusal__reason');
  const lineHeight = (el) => (el ? parseFloat(getComputedStyle(el).lineHeight) : 0);
  return {
    upload: box(document.querySelector('button.look-main')),
    logo: box(document.querySelector('.look-logo')),
    message: box(document.querySelector('.home-refusal')),
    fileLines: file ? file.getBoundingClientRect().height / lineHeight(file) : 0,
    endingShown: (() => {
      const tail = document.querySelector('.home-refusal__tail');
      const msg = document.querySelector('.home-refusal');
      if (!tail || !msg) return false;
      const t = tail.getBoundingClientRect();
      const m = msg.getBoundingClientRect();
      // An empty name has no ending to show
      if (tail.textContent === '') return true;
      return t.width >= tail.scrollWidth - 0.5 && t.right <= m.right + 0.5 && t.width > 0;
    })(),
    reasonLines: reason ? reason.getBoundingClientRect().height / lineHeight(reason) : 0,
    // Every part the message draws, against the message's own box
    partsOutside: (() => {
      const msg = document.querySelector('.home-refusal');
      if (!msg) return ['no message'];
      const m = msg.getBoundingClientRect();
      const parts = ['.look-i', '.home-refusal__open', '.home-refusal__name', '.home-refusal__head', '.home-refusal__tail', '.home-refusal__reason'];
      return parts.filter((sel) => {
        const el = msg.querySelector(sel);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.left < m.left - 0.5 || r.right > m.right + 0.5 || r.top < m.top - 0.5 || r.bottom > m.bottom + 0.5;
      });
    })(),
    // The ending gives way only once the head has: a cut tail with any of the head still drawn is a fault
    endingWhole: (() => {
      const tail = document.querySelector('.home-refusal__tail');
      return !!tail && tail.scrollWidth <= tail.clientWidth + 0.5;
    })(),
    headWidth: document.querySelector('.home-refusal__head')?.getBoundingClientRect().width ?? 0,
    text: document.querySelector('.home-refusal')?.innerText ?? '',
    window: { w: innerWidth, h: innerHeight },
  };
})()`;
const CHOOSE = (name) => `(() => {
  window.__previousHome = document.querySelector('[data-testid=home-screen]');
  const input = document.querySelector('[data-testid=home-file-input]');
  const files = new DataTransfer();
  files.items.add(new File([new Uint8Array([1, 2, 3, 4])], ${JSON.stringify(name)}, { type: 'application/pdf' }));
  input.files = files.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
})()`;
const CAME_BACK = `(() => {
  const home = document.querySelector('[data-testid=home-screen]');
  return !!home && home !== window.__previousHome && (document.querySelector('.home-refusal')?.innerText ?? '').length > 0;
})()`;

const same = (a, b) => !!a && !!b && ['x', 'y', 'w', 'h'].every((k) => Math.abs(a[k] - b[k]) <= 0.5);
let failed = 0;
try {
  for (const size of SIZES) {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const s = (method, params) => send(method, params, sessionId);
    const evaluate = async (expression) => {
      const r = await s('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(`evaluate failed: ${JSON.stringify(r.exceptionDetails).slice(0, 300)}`);
      return r.result?.value;
    };
    const waitFor = async (expression, ms = 20000) => {
      for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(100)) if (await evaluate(expression)) return;
      throw new Error(`waited ${ms} ms for ${expression.slice(0, 80)}`);
    };
    await s('Page.enable');
    await s('Runtime.enable');
    await s('Emulation.setDeviceMetricsOverride', { width: size.width, height: size.height, deviceScaleFactor: 1, mobile: false });
    // The upload is answered here, never by a server
    let answer = null;
    await s('Fetch.enable', { patterns: [{ urlPattern: '*/api/ingest*', requestStage: 'Request' }] });
    listeners.push((msg) => {
      if (msg.sessionId !== sessionId || msg.method !== 'Fetch.requestPaused') return;
      const body = answer === null ? 'refused' : JSON.stringify({ detail: answer });
      void s('Fetch.fulfillRequest', {
        requestId: msg.params.requestId,
        responseCode: 415,
        responseHeaders: [{ name: 'content-type', value: answer === null ? 'text/plain' : 'application/json' }],
        body: Buffer.from(body).toString('base64'),
      });
    });
    await s('Page.navigate', { url: args.url });
    await waitFor("!!document.querySelector('[data-testid=home-screen]') && !!document.querySelector('button.look-main')");
    await sleep(1500);
    const base = await evaluate(MEASURE);
    console.log(`${size.name} ${size.width}x${size.height} without a refusal: Upload at ${base.upload.x},${base.upload.y} (${base.upload.w}x${base.upload.h}), logo at ${base.logo.x},${base.logo.y}`);
    for (const [index, [label, name, reason]] of CASES.entries()) {
      answer = reason;
      await evaluate(CHOOSE(name));
      await waitFor(CAME_BACK);
      await sleep(400);
      const m = await evaluate(MEASURE);
      const inside = m.message && m.message.x >= 0 && m.message.y >= 0 && m.message.x + m.message.w <= m.window.w + 0.5 && m.message.y + m.message.h <= m.window.h + 0.5;
      const checks = {
        'Upload unmoved': same(m.upload, base.upload),
        'logo unmoved': same(m.logo, base.logo),
        'message inside the window': inside,
        'file on two lines at most': m.fileLines > 0 && m.fileLines <= 2.05,
        "the file's ending on screen": m.endingShown || !m.endingWhole,
        'every part inside the message box': m.partsOutside.length === 0,
        'the head gone before the ending gives way': m.endingWhole || m.headWidth <= 0.5,
        'reason on two lines at most': m.reasonLines <= 2.05,
      };
      const bad = Object.entries(checks).filter(([, ok]) => !ok).map(([k]) => k);
      if (bad.length) failed++;
      const msg = m.message;
      console.log(`${size.name} ${bad.length ? 'FAIL' : 'ok  '} ${label}: message at ${msg ? `${Math.round(msg.x)},${Math.round(msg.y)} ${Math.round(msg.w)}x${Math.round(msg.h)}` : 'none'}, file lines ${m.fileLines.toFixed(2)}, reason lines ${m.reasonLines.toFixed(2)}, ending ${m.endingWhole ? 'whole' : 'given way'}${m.partsOutside.length ? `, outside the box: ${m.partsOutside.join(' ')}` : ''}${bad.length ? `; failed: ${bad.join(', ')}` : ''}`);
      if (args.out) {
        fs.mkdirSync(args.out, { recursive: true });
        const shot = await s('Page.captureScreenshot', { format: 'png' });
        fs.writeFileSync(path.join(args.out, `${size.name}-${String(index + 1).padStart(2, '0')}.png`), Buffer.from(shot.data, 'base64'));
      }
    }
    await send('Target.closeTarget', { targetId });
  }
} finally {
  ws.close();
  chrome.kill();
  await sleep(300);
  fs.rmSync(profile, { recursive: true, force: true });
}
console.log(failed === 0 ? 'every check held' : `${failed} case(s) failed`);
process.exit(failed === 0 ? 0 : 1);
