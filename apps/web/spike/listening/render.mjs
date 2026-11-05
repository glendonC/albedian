#!/usr/bin/env node
/**
 * Renders the listening session's clips in headless Chrome and encodes AAC copies.
 *
 * Start the web app first (`pnpm --filter @albedian/web exec vite --port 5188 --strictPort`), then:
 *
 *   node apps/web/spike/listening/render.mjs [--url http://localhost:5188] [--out .cache/listening] [--clips id,id]
 *
 * Each clip is rendered by the real engine on an OfflineAudioContext (main.ts), so nothing plays
 * aloud, and Chrome runs with its audio muted and a throwaway profile that is deleted afterwards.
 * It writes <out>/wav/<clip>.wav (16-bit masters), <out>/clips/<clip>.m4a (AAC 256 kb/s, metadata
 * stripped), <out>/facts.json (what every clip plays, with each value's source), <out>/items.json
 * and <out>/console.txt, then the listening page in <out>/page/ (index.html with the items and
 * facts written in, the clips, facts.json and notices.txt), for publishing as a claude.ai Artifact
 * that keeps the owner's answers in its database. `--page-only` rebuilds the page from the files
 * already in <out>. It uses only Node's built-ins, an installed Chrome and an installed ffmpeg.
 * CHROME_PATH and FFMPEG_PATH override the defaults.
 */

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    url: { type: 'string', default: 'http://localhost:5188' },
    out: { type: 'string', default: '.cache/listening' },
    clips: { type: 'string' },
    'page-only': { type: 'boolean', default: false },
  },
});
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FFMPEG = process.env.FFMPEG_PATH ?? 'ffmpeg';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class DevTools {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Set();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString());
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} ${msg.error.data ?? ''}`));
        else resolve(msg.result);
      } else if (msg.method) {
        for (const listener of this.listeners) listener(msg);
      }
    });
  }

  send(method, params = {}, sessionId) {
    const id = ++this.nextId;
    this.ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

async function evaluate(devtools, sessionId, expression) {
  const res = await devtools.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, timeout: 180000 }, sessionId);
  if (res.exceptionDetails) throw new Error(`page: ${JSON.stringify(res.exceptionDetails).slice(0, 1600)}`);
  return res.result.value;
}

function encodeAac(wav, m4a) {
  const result = spawnSync(
    FFMPEG,
    ['-hide_banner', '-loglevel', 'error', '-y', '-i', wav, '-c:a', 'aac', '-b:a', '256k', '-map_metadata', '-1', '-fflags', '+bitexact', '-flags:a', '+bitexact', '-movflags', '+faststart', m4a],
    { stdio: ['ignore', 'inherit', 'inherit'] }
  );
  if (result.status !== 0) throw new Error(`ffmpeg failed on ${wav}`);
}

const out = path.resolve(args.out);
const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../../..');

async function renderAll() {
  for (const dir of ['wav', 'clips']) fs.mkdirSync(path.join(out, dir), { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-listening-'));
  const chrome = spawn(
    CHROME,
    ['--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--mute-audio', '--no-first-run', '--no-default-browser-check', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', 'about:blank'],
    { stdio: ['ignore', 'ignore', 'pipe'] }
  );
  const consoleLines = [];
  try {
    const wsUrl = await new Promise((resolve, reject) => {
      let stderr = '';
      const timer = setTimeout(() => reject(new Error('Chrome did not report a DevTools URL')), 20000);
      chrome.stderr.on('data', (d) => {
        stderr += d.toString();
        const m = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
        if (m) {
          clearTimeout(timer);
          resolve(m[1]);
        }
      });
      chrome.on('exit', (code) => reject(new Error(`Chrome exited with ${code}`)));
    });
    const ws = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    const devtools = new DevTools(ws);
    const { targetId } = await devtools.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await devtools.send('Target.attachToTarget', { targetId, flatten: true });
    devtools.on((msg) => {
      if (msg.sessionId !== sessionId) return;
      if (msg.method === 'Runtime.consoleAPICalled') {
        consoleLines.push(`[${msg.params.type}] ${msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`);
      } else if (msg.method === 'Runtime.exceptionThrown') {
        consoleLines.push(`[exception] ${JSON.stringify(msg.params.exceptionDetails).slice(0, 800)}`);
      }
    });
    for (const domain of ['Page', 'Runtime']) await devtools.send(`${domain}.enable`, {}, sessionId);
    await devtools.send('Page.navigate', { url: `${args.url}/spike/listening/` }, sessionId);
    const ready = await evaluate(
      devtools,
      sessionId,
      `(async () => { for (let i = 0; i < 600 && !window.__albedianListening; i++) await new Promise((r) => setTimeout(r, 100)); return !!window.__albedianListening; })()`
    );
    if (!ready) throw new Error('the listening page never published window.__albedianListening');

    const items = await evaluate(devtools, sessionId, 'JSON.parse(JSON.stringify(window.__albedianListening.items))');
    const allIds = await evaluate(devtools, sessionId, '[...window.__albedianListening.clipIds]');
    const ids = args.clips ? args.clips.split(',') : allIds;
    const facts = [];
    for (const id of ids) {
      const started = Date.now();
      const result = await evaluate(devtools, sessionId, `window.__albedianListening.render(${JSON.stringify(id)})`);
      const wav = path.join(out, 'wav', `${id}.wav`);
      fs.writeFileSync(wav, Buffer.from(result.wavBase64, 'base64'));
      encodeAac(wav, path.join(out, 'clips', `${id}.m4a`));
      facts.push(result.facts);
      const f = result.facts;
      console.log(`${id}: ${f.durationSec} s, reverb ${f.reverb.status}, bed ${f.bed.renderer}${f.bed.fallbackReason ? ` (${f.bed.fallbackReason})` : ''}, peak ${f.output.peak.map((p) => p.toFixed(3)).join('/')}, ${Date.now() - started} ms`);
    }
    const head = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).stdout.trim();
    const dirty = spawnSync('git', ['status', '--porcelain', '--', 'apps/web/src/audio', 'apps/web/spike/listening'], { cwd: repoRoot, encoding: 'utf8' }).stdout.trim() !== '';
    const render = { commit: head, uncommittedAudioChanges: dirty, renderedAt: new Date().toISOString().slice(0, 10) };
    fs.writeFileSync(path.join(out, 'facts.json'), JSON.stringify({ render, clips: facts }, null, 2) + '\n');
    fs.writeFileSync(path.join(out, 'items.json'), JSON.stringify(items, null, 2) + '\n');
    await devtools.send('Target.closeTarget', { targetId });
    ws.close();
  } finally {
    fs.writeFileSync(path.join(out, 'console.txt'), consoleLines.join('\n') + '\n');
    chrome.kill('SIGTERM');
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// The listening page
// ---------------------------------------------------------------------------

const esc = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const grouped = (n) => Math.round(n).toLocaleString('en-US');
const fmtK = (t) => (t == null ? 'none' : `${grouped(t)} K`);
const fmtL = (l) => (l == null ? 'none' : l >= 100 ? `${grouped(l)} L☉` : `${Number(l.toPrecision(3))} L☉`);
const fmtPc = (d) => (d == null ? 'none' : d >= 100 ? `${grouped(d)} pc` : `${d.toFixed(d < 10 ? 2 : 1)} pc`);
const fmtDb = (x) => (x == null || !Number.isFinite(x) ? 'silent' : `${x.toFixed(1).replace('-', '−')} dBFS`);
const fmtHz = (x) => (x == null ? '' : `${grouped(x)} Hz`);
const fmtS = (s) => `${s.toFixed(1)} s`;

function stated(value) {
  const kind = value.kind === 'test' ? 'test' : 'published';
  const badge = value.kind === 'test' ? 'test value, not a measurement' : `published: ${value.label}`;
  return `<li><span class="kind kind-${kind}">${esc(badge)}</span> ${esc(Number(value.value.toPrecision(6)))} ${esc(value.unit)} <span class="ref">${esc(value.reference)}</span></li>`;
}

function singleVoices(sources, snapshot) {
  return sources
    .map((source) => {
      const voice = snapshot.voices.find((v) => v.id === source.id);
      const title = source.star
        ? `${esc(source.star)} <span class="designation">${esc(source.designation ?? '')}</span>`
        : esc(source.pulsar ?? source.id);
      const values = [];
      if (source.star) {
        values.push(`<li><span class="kind kind-catalog">catalog</span> ${fmtK(source.teffK)} GSP-Phot temperature, ${fmtL(source.catalogLuminositySolar)} FLAME luminosity, ${fmtPc(source.catalogDistancePc)} Bailer-Jones distance</li>`);
      }
      for (const device of source.devices) values.push(`<li><span class="kind kind-device">listening device</span> ${esc(device)}</li>`);
      for (const value of source.stated) values.push(stated(value));
      const plays = voice
        ? `plays at ${fmtDb(voice.levelDb)}${voice.centroidHz ? `, centroid ${fmtHz(voice.centroidHz)}` : ''}${voice.pitchHz ? `, identity pitch ${fmtHz(voice.pitchHz)}` : ''}${voice.beatHz != null ? `, beating ${voice.beatHz} Hz` : ''}${voice.dustLowpassHz != null ? `, dust low-pass ${fmtHz(voice.dustLowpassHz)}` : ''}${voice.pulseRateHz != null ? `, ${voice.pulseRateHz} pulses a second` : ''}`
        : 'silent';
      return `<div class="voice"><p class="voice-title">${title} <span class="plays">${esc(plays)}</span></p><ul class="values">${values.join('')}</ul></div>`;
    })
    .join('');
}

function skyVoices(sources, snapshot) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const rows = snapshot.voices
    .map((v) => {
      const s = byId.get(v.id) ?? {};
      const name = s.star ? esc(s.star) : `<span class="designation">${esc(v.id.replace(/^gaia-dr3:/, 'tile '))}</span>`;
      return `<tr><td>${name}</td><td>${fmtK(v.teffK)}</td><td>${fmtL(s.luminositySolar)}</td><td>${fmtPc(s.distancePc)}</td><td>${fmtDb(v.levelDb)}</td><td>${fmtHz(v.centroidHz)}</td><td>${fmtHz(v.pitchHz)}</td></tr>`;
    })
    .join('');
  const silent = snapshot.silent.filter((o) => o.reason === 'missing_input');
  const silentNames = silent.map((o) => byId.get(o.id)?.star).filter(Boolean);
  const silentLine = silent.length
    ? `<p class="silent">Also on screen and silent, with no Gaia DR3 temperature or luminosity: ${silentNames.length ? esc(silentNames.join(', ')) : ''}${silentNames.length < silent.length ? `${silentNames.length ? ' and ' : ''}${silent.length - silentNames.length} unnamed` : ''}.</p>`
    : '';
  return `<p class="voice-intro"><span class="kind kind-catalog">catalog</span> The ${snapshot.voices.length} loudest of the catalog stars on screen, as the app's sky feed passes them (Gaia DR3 temperature and FLAME luminosity, Bailer-Jones distance).</p>
<div class="table-wrap"><table><thead><tr><th>Star</th><th>Temperature</th><th>Luminosity</th><th>Distance</th><th>Level</th><th>Centroid</th><th>Pitch</th></tr></thead><tbody>${rows}</tbody></table></div>${silentLine}`;
}

function stepBlock(step) {
  const parts = [];
  if (Array.isArray(step.sources)) {
    const sky = step.sources.length > 0 && step.sources.every((s) => s.devices.length === 0 && !s.pulsar);
    parts.push(sky ? skyVoices(step.sources, step.snapshot) : step.sources.length ? singleVoices(step.sources, step.snapshot) : '<p class="voice-intro">No voices: the bed alone.</p>');
  }
  if (step.skyBrightness && step.skyBrightness !== 'unchanged') {
    const bed = step.snapshot.bed;
    parts.push(`<ul class="values">${stated(step.skyBrightness)}<li>Skyglow bed ${bed.levelDb == null ? 'silent' : fmtDb(bed.levelDb)}${bed.silentBecause ? ` (${esc(bed.silentBecause)})` : ''}, ${esc(bed.renderer === 'omnitone-foa' ? 'Omnitone first-order ambisonics' : bed.renderer === 'stereo-diffuse' ? 'plain stereo' : bed.renderer)}</li></ul>`);
  }
  for (const note of step.notes) parts.push(`<p class="note"><span class="kind kind-device">listening device</span> ${esc(note)}</p>`);
  return `<div class="step"><p class="at">${fmtS(step.atSec)}</p><div class="step-body">${parts.join('')}</div></div>`;
}

function clipBlock(facts) {
  const steps = facts.steps;
  const shown = steps.length > 12 ? steps.slice(0, 1) : steps;
  const moving = steps.length > 12 ? `<p class="note">${steps.length} steps over ${fmtS(facts.durationSec)}; the first is shown. The full record is in facts.json.</p>` : '';
  const meta = [
    fmtS(facts.durationSec),
    facts.regime === 'cislunar' ? 'cislunar scale (the landing)' : 'under the sky',
    facts.reverb.requested ? 'chapel reverb' : 'dry',
    facts.harmonicCeilingHz ? `harmonics cut above ${fmtHz(facts.harmonicCeilingHz)}` : null,
  ].filter(Boolean);
  return `<figure class="clip" id="clip-${esc(facts.clip)}">
<figcaption><span class="clip-label">${esc(facts.label)}</span><span class="clip-meta">${esc(meta.join(' · '))}</span></figcaption>
<audio controls preload="none" src="clips/${esc(facts.clip)}.m4a"></audio>
<details class="facts"><summary>What plays, and where each value comes from</summary>${shown.map(stepBlock).join('')}${moving}</details>
</figure>`;
}

function itemBlock(item, factsById) {
  const label = item.section10 === 0 ? 'Item 0 · first listen' : `Item ${item.section10} · AUDIO_ENGINE.md section 10`;
  return `<section class="item" id="item-${esc(item.id)}" data-item="${esc(item.id)}">
<p class="eyebrow">${esc(label)}</p>
<h2>${esc(item.title)}</h2>
<p class="question">${esc(item.question)}</p>
${item.clips.map((clip) => clipBlock(factsById.get(clip.id))).join('\n')}
<div class="answer">
  <div class="verdict" role="group" aria-label="Your answer to item ${item.section10}">
    <button type="button" class="choice" id="yes-${esc(item.id)}" data-verdict="yes" aria-pressed="false" disabled>Yes</button>
    <button type="button" class="choice" id="no-${esc(item.id)}" data-verdict="no" aria-pressed="false" disabled>No</button>
  </div>
  <label class="note-label" for="note-${esc(item.id)}">Note</label>
  <textarea id="note-${esc(item.id)}" rows="2" placeholder="What you heard, or what to change" disabled></textarea>
  <div class="answer-foot"><button type="button" class="save-note" id="save-${esc(item.id)}" disabled>Save note</button><span class="state" id="state-${esc(item.id)}" aria-live="polite">Not answered</span></div>
</div>
</section>`;
}

const PAGE_STYLE = `
/* One reading column of items; each item is a question, its clips with their facts, and an answer row */
:root {
  --bg: #f2f4f7;
  --surface: #ffffff;
  --fg: #161c26;
  --muted: #586274;
  --line: #d5dae3;
  --accent: #a4570a;
  --accent-soft: #f6e7d6;
  --catalog: #23557e;
  --test: #8a2f4d;
  --device: #4c5a2c;
  --font-ui: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --font-read: "Newsreader", Georgia, "Times New Roman", serif;
  --font-data: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0d1118; --surface: #151b25; --fg: #e6e9ef; --muted: #9aa4b5; --line: #2a3342;
    --accent: #f0a85a; --accent-soft: #3a2a19; --catalog: #8cc0ea; --test: #f08cad; --device: #b8cc84;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0d1118; --surface: #151b25; --fg: #e6e9ef; --muted: #9aa4b5; --line: #2a3342;
  --accent: #f0a85a; --accent-soft: #3a2a19; --catalog: #8cc0ea; --test: #f08cad; --device: #b8cc84;
  color-scheme: dark;
}
body {
  background: var(--bg);
  color: var(--fg);
  font: 15px/1.55 var(--font-ui);
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 32px 64px;
}
main { max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 40px; }
h1, h2 { font-family: var(--font-read); font-weight: 500; text-wrap: balance; margin: 0; }
h1 { font-size: 2.1rem; line-height: 1.15; }
h2 { font-size: 1.45rem; line-height: 1.25; }
p { margin: 0; }
.lede { font-family: var(--font-read); font-size: 1.12rem; color: var(--fg); max-width: 62ch; }
.intro { display: flex; flex-direction: column; gap: 14px; }
.howto { display: flex; flex-direction: column; gap: 6px; color: var(--muted); max-width: 64ch; }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 0.85rem; color: var(--muted); }
.banner { border: 1px solid var(--line); background: var(--surface); padding: 10px 14px; border-radius: 6px; color: var(--muted); }
.item { display: flex; flex-direction: column; gap: 14px; padding-top: 28px; border-top: 1px solid var(--line); }
.eyebrow { font: 500 0.75rem/1.2 var(--font-data); letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); }
.question { font-family: var(--font-read); font-size: 1.08rem; max-width: 64ch; }
.clip { margin: 0; display: flex; flex-direction: column; gap: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-width: 0; }
figcaption { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; }
.clip-label { font-weight: 600; }
.clip-meta { font: 0.8rem/1.4 var(--font-data); color: var(--muted); }
audio { width: 100%; max-width: 100%; }
.facts summary { cursor: pointer; color: var(--muted); font-size: 0.9rem; }
.facts[open] summary { margin-bottom: 8px; }
.step { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr); gap: 10px; padding-block: 8px; border-top: 1px dashed var(--line); }
.at { font: 0.8rem/1.6 var(--font-data); color: var(--muted); font-variant-numeric: tabular-nums; }
.step-body { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.voice { display: flex; flex-direction: column; gap: 4px; }
.voice-title { font-weight: 600; }
.plays, .designation, .ref { font: 400 0.8rem/1.4 var(--font-data); color: var(--muted); }
.ref { display: block; }
.values { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; font-size: 0.9rem; }
.kind { font: 500 0.7rem/1 var(--font-data); letter-spacing: 0.04em; text-transform: uppercase; padding: 3px 6px; border-radius: 4px; border: 1px solid currentColor; white-space: nowrap; }
.kind-catalog { color: var(--catalog); }
.kind-test { color: var(--test); }
.kind-published { color: var(--catalog); }
.kind-device { color: var(--device); }
.voice-intro, .silent, .note { font-size: 0.9rem; color: var(--muted); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; font: 0.8rem/1.4 var(--font-data); font-variant-numeric: tabular-nums; min-width: 34rem; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { font-weight: 500; color: var(--muted); }
.answer { display: flex; flex-direction: column; gap: 8px; }
.verdict { display: flex; gap: 8px; }
.choice, .save-note { font: 600 0.95rem/1 var(--font-ui); padding: 10px 18px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--fg); cursor: pointer; }
.choice[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg); }
.choice:disabled, .save-note:disabled, textarea:disabled { opacity: 0.55; cursor: not-allowed; }
.choice:focus-visible, .save-note:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.note-label { font-size: 0.85rem; color: var(--muted); }
textarea { font: 0.95rem/1.45 var(--font-ui); color: var(--fg); background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; width: 100%; box-sizing: border-box; resize: vertical; }
.answer-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.state { font: 0.8rem/1.4 var(--font-data); color: var(--muted); }
footer { display: flex; flex-direction: column; gap: 10px; padding-top: 28px; border-top: 1px solid var(--line); color: var(--muted); font-size: 0.9rem; }
footer ul { margin: 0; padding-left: 1.1rem; display: flex; flex-direction: column; gap: 4px; }
footer pre { white-space: pre-wrap; font: 0.75rem/1.5 var(--font-data); max-height: 24rem; overflow: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 10px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; transition: none; } }
`;

const PAGE_SCRIPT = `
(() => {
  const ids = [...document.querySelectorAll('[data-item]')].map((el) => el.dataset.item);
  const state = Object.fromEntries(ids.map((id) => [id, { verdict: null, note: '' }]));
  const chains = new Map();
  const banner = document.getElementById('save-banner');
  const el = (prefix, id) => document.getElementById(prefix + '-' + id);
  const show = (text) => { banner.textContent = text; banner.hidden = false; };
  const time = (iso) => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch { return iso; } };

  function paint(id) {
    const s = state[id];
    for (const verdict of ['yes', 'no']) el(verdict, id).setAttribute('aria-pressed', String(s.verdict === verdict));
    const note = el('note', id);
    if (document.activeElement !== note && !note.dataset.dirty) note.value = s.note || '';
    el('state', id).textContent = s.updatedAt ? 'Saved ' + time(s.updatedAt) : 'Not answered';
  }

  function save(db, id) {
    const doc = db.doc('answers/' + id);
    const body = { verdict: state[id].verdict, note: state[id].note || '', updatedAt: new Date().toISOString() };
    const next = (chains.get(id) || Promise.resolve()).then(() => doc.set(body)).then(
      () => { state[id].updatedAt = body.updatedAt; delete el('note', id).dataset.dirty; paint(id); },
      (error) => { el('state', id).textContent = 'Not saved: ' + ((error && error.message) || 'the answer could not be stored'); }
    );
    chains.set(id, next);
    el('state', id).textContent = 'Saving';
  }

  async function start() {
    const api = window.claude;
    const db = api && typeof api.use === 'function' ? await api.use('db') : null;
    if (!db) {
      show('Answers can be saved only when this page is opened as its Artifact on claude.ai. The clips and their facts work everywhere.');
      return;
    }
    for (const id of ids) {
      for (const verdict of ['yes', 'no']) {
        const button = el(verdict, id);
        button.disabled = false;
        button.addEventListener('click', () => { state[id].verdict = verdict; paint(id); save(db, id); });
      }
      const note = el('note', id);
      const saveNote = el('save', id);
      note.disabled = false;
      saveNote.disabled = false;
      note.addEventListener('input', () => { note.dataset.dirty = '1'; });
      saveNote.addEventListener('click', () => { state[id].note = note.value; save(db, id); });
    }
    const timer = setTimeout(() => show('Saved answers cannot be reached yet. New answers are kept once the store answers.'), 10000);
    db.collection('answers').onSnapshot(
      (snap) => {
        if (!snap.metadata.fromCache) { clearTimeout(timer); banner.hidden = true; }
        for (const d of snap.docs) {
          if (!(d.id in state)) continue;
          const data = d.data() || {};
          state[d.id] = {
            verdict: data.verdict === 'yes' || data.verdict === 'no' ? data.verdict : null,
            note: typeof data.note === 'string' ? data.note : '',
            updatedAt: typeof data.updatedAt === 'string' ? data.updatedAt : undefined,
          };
          paint(d.id);
        }
      },
      (error) => { clearTimeout(timer); show('Saved answers are unavailable in this view (' + ((error && error.code) || 'error') + ').'); }
    );
  }

  const notices = document.getElementById('notices');
  document.getElementById('notices-details').addEventListener('toggle', async (event) => {
    if (!event.target.open || notices.dataset.loaded) return;
    try {
      const response = await fetch('notices.txt');
      if (!response.ok) throw new Error(String(response.status));
      notices.textContent = await response.text();
      notices.dataset.loaded = '1';
    } catch {
      notices.textContent = 'The notices file could not be loaded here; it ships beside this page as notices.txt.';
    }
  });
  start();
})();
`;

function writePage() {
  const items = JSON.parse(fs.readFileSync(path.join(out, 'items.json'), 'utf8'));
  const factsFile = JSON.parse(fs.readFileSync(path.join(out, 'facts.json'), 'utf8'));
  const factsById = new Map(factsFile.clips.map((f) => [f.clip, f]));
  const missing = items.flatMap((item) => item.clips.map((c) => c.id)).filter((id) => !factsById.has(id));
  if (missing.length) throw new Error(`no facts for ${missing.join(', ')}; render them first`);
  const page = path.join(out, 'page');
  fs.mkdirSync(path.join(page, 'clips'), { recursive: true });
  for (const id of factsById.keys()) fs.copyFileSync(path.join(out, 'clips', `${id}.m4a`), path.join(page, 'clips', `${id}.m4a`));
  fs.copyFileSync(path.join(out, 'facts.json'), path.join(page, 'facts.json'));
  fs.copyFileSync(path.join(repoRoot, 'apps/web/src/audio/assets/THIRD_PARTY_NOTICES.txt'), path.join(page, 'notices.txt'));
  const render = factsFile.render;
  const html = `<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Albedian Listening Session</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap">
<style>${PAGE_STYLE}</style>
<main>
<header class="intro">
  <h1>Albedian Listening Session</h1>
  <p class="lede">${items.length} items about how Albedian's sky sounds. Each clip was rendered by the app's own audio engine from the values it names. Listen, then answer yes or no, with a note if you like.</p>
  <p class="lede">No sound reaches us from space. Each clip is made from the values it names, catalog, published or labelled test values, with stated design choices; none is a recording of space.</p>
  <div class="howto">
    <p>Use headphones: each voice is placed around your head. Every clip plays at the app's starting volume.</p>
    <p>Under each clip, the facts list what plays and where every value comes from.</p>
  </div>
  <div class="legend"><span><span class="kind kind-catalog">catalog</span> Gaia DR3 and Bailer-Jones values from the app's star tiles</span><span><span class="kind kind-published">published</span> a value with its citation</span><span><span class="kind kind-test">test value</span> not a measurement; for a sound the app has no data for yet</span><span><span class="kind kind-device">listening device</span> a change made only so you can compare</span></div>
  <p class="banner" id="save-banner" role="status" hidden></p>
</header>
${items.map((item) => itemBlock(item, factsById)).join('\n')}
<footer>
  <p>Rendered on ${esc(render.renderedAt)} from commit ${esc(render.commit.slice(0, 10))}${render.uncommittedAudioChanges ? ' with uncommitted audio changes' : ''}, at 48 kHz, then encoded as AAC at 256 kb/s.</p>
  <p>Credits</p>
  <ul>
    <li>Star data: ESA/Gaia/DPAC (Gaia DR3, CC BY-NC 3.0 IGO) and ESA Hipparcos (Hipparcos 2007); distances from Bailer-Jones et al. 2021.</li>
    <li>Pulsars: ATNF Pulsar Catalogue v2.6.1 (Manchester et al. 2005).</li>
    <li>Reverb: St. Nicholas Chapel, Aachen Cathedral, impulse response by Martin Zerwas and Selin Kayku (Zenodo, doi:10.5281/zenodo.23312556), CC BY 4.0, modified.</li>
    <li>Ambisonic bed: Omnitone, Copyright Google Inc., Apache-2.0, with the Google/SADIE binaural filters, Copyright (c) 2017, University of York, Apache-2.0.</li>
  </ul>
  <details id="notices-details"><summary>Licences and notices</summary><pre id="notices">Open to load the notices.</pre></details>
</footer>
</main>
<script>${PAGE_SCRIPT}</script>
`;
  fs.writeFileSync(path.join(page, 'index.html'), html);
  const bytes = fs.readdirSync(path.join(page, 'clips')).reduce((n, f) => n + fs.statSync(path.join(page, 'clips', f)).size, 0);
  console.log(`page: ${path.join(page, 'index.html')} (${(Buffer.byteLength(html) / 1024).toFixed(0)} KB), ${factsById.size} clips (${(bytes / 1e6).toFixed(1)} MB)`);
}

if (!args['page-only']) await renderAll();
writePage();
