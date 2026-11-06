#!/usr/bin/env node
/**
 * Renders the three music directions in headless Chrome, masters and measures them, and builds the
 * page the owner picks from (AUDIO_ENGINE.md section 13).
 *
 * Start the web app first (`pnpm --filter @albedian/web exec vite --port 5188 --strictPort`), then:
 *
 *   node apps/web/spike/music/render.mjs [--url http://localhost:5188] [--out .cache/music] [--clips organ-pulse,ambient-beats,recording] [--page-only]
 *
 * For each direction: main.ts renders it on an OfflineAudioContext (Chrome muted, a throwaway profile
 * deleted afterwards) and returns its facts and its 32-bit float samples. Then, with ffmpeg's EBU R128
 * meter (ebur128, true peak by 4x oversampling):
 * 1. measure the render's integrated loudness and true peak;
 * 2. apply the one gain that brings it to -16 LUFS;
 * 3. if the true peak would then exceed -1 dBTP, limit it instead with ffmpeg's alimiter at -1.5 dBFS
 *    on 192 kHz audio and correct the gain again, and say so in the facts;
 * 4. measure again, look for clicks (clicks.ts), encode AAC at 256 kb/s with no metadata.
 * The samples come from .cache/music/sources/ (sources.json lists each with its licence and SHA-256,
 * checked here before rendering), served by the dev server. Only Node's built-ins, an installed Chrome
 * and an installed ffmpeg are used. CHROME_PATH and FFMPEG_PATH override the defaults.
 */

import { spawn, spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    url: { type: 'string', default: 'http://localhost:5188' },
    out: { type: 'string', default: '.cache/music' },
    clips: { type: 'string', default: 'organ-pulse,ambient-beats,recording' },
    'page-only': { type: 'boolean', default: false },
  },
});
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FFMPEG = process.env.FFMPEG_PATH ?? 'ffmpeg';
const TARGET_LUFS = -16;
const TRUE_PEAK_CEILING_DBTP = -1;
const LIMIT_DBFS = -1.5;
const SAMPLE_RATE = 48000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const here = path.dirname(new URL(import.meta.url).pathname);
const repoRoot = path.resolve(here, '../../../..');
const out = path.resolve(args.out);
const sourcesDir = path.join(repoRoot, '.cache/music/sources');
const SOURCES = JSON.parse(fs.readFileSync(path.join(here, 'sources.json'), 'utf8'));
const LANDING_PICTURE = path.join(repoRoot, '.lead/pictures/landing-2025-03-07-1080p.jpg');

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

async function evaluate(devtools, sessionId, expression, timeoutMs = 600000) {
  const res = await devtools.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true, timeout: timeoutMs }, sessionId);
  if (res.exceptionDetails) throw new Error(`page: ${JSON.stringify(res.exceptionDetails).slice(0, 1600)}`);
  return res.result.value;
}

/** Opens the harness in headless Chrome (muted, a throwaway profile deleted afterwards) and hands `use` an evaluator. */
async function withMusicPage(consoleFile, use) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-music-'));
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
        const line = `[${msg.params.type}] ${msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`;
        consoleLines.push(line);
        if (line.includes('[music]')) console.log(`  ${line}`);
      } else if (msg.method === 'Runtime.exceptionThrown') {
        consoleLines.push(`[exception] ${JSON.stringify(msg.params.exceptionDetails).slice(0, 800)}`);
      }
    });
    for (const domain of ['Page', 'Runtime']) await devtools.send(`${domain}.enable`, {}, sessionId);
    await devtools.send('Page.navigate', { url: `${args.url}/spike/music/` }, sessionId);
    const ready = await evaluate(
      devtools,
      sessionId,
      `(async () => { for (let i = 0; i < 600 && !window.__albedianMusic; i++) await new Promise((r) => setTimeout(r, 100)); return !!window.__albedianMusic; })()`
    );
    if (!ready) throw new Error('the harness never published window.__albedianMusic');
    const result = await use((expression, timeoutMs) => evaluate(devtools, sessionId, expression, timeoutMs));
    await devtools.send('Target.closeTarget', { targetId });
    ws.close();
    return result;
  } finally {
    fs.writeFileSync(consoleFile, consoleLines.join('\n') + '\n');
    chrome.kill('SIGTERM');
    await sleep(500);
    fs.rmSync(profile, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// WAV, ffmpeg, measures
// ---------------------------------------------------------------------------

function writeWavFloat(file, interleaved, channels = 2, sampleRate = SAMPLE_RATE) {
  const data = Buffer.from(interleaved.buffer, interleaved.byteOffset, interleaved.byteLength);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(3, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * 4, 28);
  header.writeUInt16LE(channels * 4, 32);
  header.writeUInt16LE(32, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  fs.writeFileSync(file, Buffer.concat([header, data]));
}

function ffmpeg(argsList) {
  const result = spawnSync(FFMPEG, ['-hide_banner', '-nostdin', ...argsList], { encoding: 'utf8', maxBuffer: 1 << 28 });
  if (result.status !== 0) throw new Error(`ffmpeg failed: ${result.stderr.slice(-1200)}`);
  return result.stderr;
}

/** Integrated loudness (LUFS), loudness range (LU) and true peak (dBTP), by ffmpeg's EBU R128 meter. */
function measureLoudness(file) {
  const log = ffmpeg(['-i', file, '-filter_complex', 'ebur128=peak=true:framelog=quiet', '-f', 'null', '-']);
  const summary = log.slice(log.lastIndexOf('Summary:'));
  const num = (re) => {
    const m = re.exec(summary);
    if (!m) throw new Error(`no ${re} in ebur128's summary`);
    return Number(m[1]);
  };
  return { integratedLufs: num(/I:\s+(-?[\d.]+|-inf) LUFS/), loudnessRangeLu: num(/LRA:\s+(-?[\d.]+) LU/), truePeakDbtp: num(/True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS/) };
}

/** Decodes a file to interleaved 32-bit float stereo at 48 kHz. */
function decodeFloat(file) {
  const result = spawnSync(FFMPEG, ['-hide_banner', '-nostdin', '-i', file, '-f', 'f32le', '-ac', '2', '-ar', String(SAMPLE_RATE), '-'], { maxBuffer: 1 << 30 });
  if (result.status !== 0) throw new Error(`ffmpeg could not decode ${file}`);
  const bytes = result.stdout;
  return new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
}

function scaled(interleaved, gainDb) {
  const g = 10 ** (gainDb / 20);
  const outBuf = new Float32Array(interleaved.length);
  for (let i = 0; i < interleaved.length; i++) outBuf[i] = interleaved[i] * g;
  return outBuf;
}

/**
 * Brings a render to -16 LUFS with one gain; if that would put the true peak above -1 dBTP, limits it
 * at -1.5 dBFS on 192 kHz audio instead and corrects the gain (at most three passes). Says which.
 */
function master(id, raw) {
  const wavDir = path.join(out, 'wav');
  const rawFile = path.join(wavDir, `${id}.render.wav`);
  writeWavFloat(rawFile, raw);
  const before = measureLoudness(rawFile);
  let gainDb = TARGET_LUFS - before.integratedLufs;
  const masterFile = path.join(wavDir, `${id}.master.wav`);
  writeWavFloat(masterFile, scaled(raw, gainDb));
  let after = measureLoudness(masterFile);
  let limiter = null;
  if (after.truePeakDbtp > TRUE_PEAK_CEILING_DBTP) {
    const peakBeforeLimiter = after.truePeakDbtp;
    const gainedFile = path.join(wavDir, `${id}.gained.wav`);
    for (let pass = 0; pass < 3; pass++) {
      writeWavFloat(gainedFile, scaled(raw, gainDb));
      ffmpeg(['-y', '-i', gainedFile, '-af', `aresample=192000,alimiter=limit=${(10 ** (LIMIT_DBFS / 20)).toFixed(6)}:attack=5:release=60:level=0,aresample=${SAMPLE_RATE}`, '-c:a', 'pcm_f32le', masterFile]);
      after = measureLoudness(masterFile);
      if (Math.abs(after.integratedLufs - TARGET_LUFS) <= 0.3 && after.truePeakDbtp <= TRUE_PEAK_CEILING_DBTP) break;
      gainDb += TARGET_LUFS - after.integratedLufs;
    }
    fs.rmSync(gainedFile, { force: true });
    limiter = {
      what: `ffmpeg alimiter at ${LIMIT_DBFS} dBFS (attack 5 ms, release 60 ms) on audio resampled to 192 kHz, then back to 48 kHz`,
      why: `one gain to ${TARGET_LUFS} LUFS would have put the true peak at ${peakBeforeLimiter.toFixed(2)} dBTP, above ${TRUE_PEAK_CEILING_DBTP} dBTP`,
    };
  }
  return { before, gainDb, after, limiter, masterFile };
}

/** Mono fold-down (0.5 * L + 0.5 * R) loudness, mono-vs-stereo power (dB), and L-R correlation. */
function measureMono(masterFile, interleaved) {
  const log = ffmpeg(['-i', masterFile, '-filter_complex', 'pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,ebur128=peak=true:framelog=quiet', '-f', 'null', '-']);
  const summary = log.slice(log.lastIndexOf('Summary:'));
  const m = /I:\s+(-?[\d.]+|-inf) LUFS/.exec(summary);
  const integratedLufs = m ? Number(m[1]) : null;
  const n = interleaved.length / 2;
  let ll = 0;
  let rr = 0;
  let lr = 0;
  let mm = 0;
  for (let i = 0; i < n; i++) {
    const l = interleaved[2 * i];
    const r = interleaved[2 * i + 1];
    ll += l * l;
    rr += r * r;
    lr += l * r;
    const mono = 0.5 * (l + r);
    mm += mono * mono;
  }
  const correlation = lr / Math.sqrt(Math.max(1e-30, ll * rr));
  const monoVsStereoDb = 10 * Math.log10(mm / Math.max(1e-30, 0.5 * (ll + rr)));
  return { integratedLufs, monoVsStereoDb: Number(monoVsStereoDb.toFixed(2)), correlation: Number(correlation.toFixed(2)) };
}

let clickModule = null;
async function findClicks(interleaved, percussionOnsetsSec = []) {
  clickModule ??= await import(pathToFileURL(path.join(here, 'clicks.ts')).href);
  const n = interleaved.length / 2;
  const left = new Float32Array(n);
  const right = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    left[i] = interleaved[2 * i];
    right[i] = interleaved[2 * i + 1];
  }
  const report = clickModule.detectClicks([left, right], SAMPLE_RATE, { maxReport: 10000 });
  const nearPercussion = (t) => percussionOnsetsSec.some((p) => t - p >= -0.005 && t - p <= 0.08);
  let onPercussion = 0;
  for (const c of report.clicks) {
    if (nearPercussion(c.timeSec)) onPercussion++;
  }
  const awayFromPercussion = report.count - onPercussion;
  return {
    count: report.count,
    onPercussion,
    awayFromPercussion,
    method: report.method,
    clicks: report.clicks.slice(0, 10).map((c) => ({ channel: c.channel, timeSec: Number(c.timeSec.toFixed(4)), strength: Number(c.strength.toFixed(1)), onPercussion: nearPercussion(c.timeSec) })),
  };
}

function encodeAac(wav, m4a) {
  ffmpeg(['-y', '-i', wav, '-c:a', 'aac', '-b:a', '256k', '-map_metadata', '-1', '-fflags', '+bitexact', m4a]);
}

/** Every source file the renders read, checked against sources.json before anything is rendered. */
function checkSources() {
  let checked = 0;
  for (const s of SOURCES.files) {
    const file = path.join(sourcesDir, s.path);
    if (!fs.existsSync(file)) continue;
    const sha = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    if (sha !== s.sha256) throw new Error(`${s.path} does not match its SHA-256 in sources.json`);
    checked++;
  }
  return checked;
}

/**
 * Serves .cache/music/sources/ on a loopback port for the length of the render. The dev server's
 * /@fs route cannot serve VCSL's file names that hold '#' (it answers with the app's index page).
 */
function serveSources() {
  const cors = { 'Access-Control-Allow-Origin': '*' };
  const server = http.createServer((req, res) => {
    let rel;
    try {
      rel = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname).replace(/^\/+/, '');
    } catch {
      res.writeHead(400, cors).end();
      return;
    }
    const file = path.resolve(sourcesDir, rel);
    if (!file.startsWith(sourcesDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404, cors).end();
      return;
    }
    res.writeHead(200, { ...cors, 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(file).size });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

function renderRecord() {
  const head = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).stdout.trim();
  const dirty = spawnSync('git', ['status', '--porcelain', '--', 'apps/web/src/audio', 'apps/web/spike/music'], { cwd: repoRoot, encoding: 'utf8' }).stdout.trim() !== '';
  return { commit: head, uncommittedAudioChanges: dirty, renderedAt: new Date().toISOString().slice(0, 10) };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

async function renderAll() {
  fs.mkdirSync(path.join(out, 'wav'), { recursive: true });
  fs.mkdirSync(path.join(out, 'clips'), { recursive: true });
  const checked = checkSources();
  console.log(`sources: ${checked} files match sources.json`);
  const server = await serveSources();
  const sourcesBase = `http://127.0.0.1:${server.address().port}/`;
  const ids = args.clips.split(',').filter(Boolean);
  const factsFile = path.join(out, 'facts.json');
  const previous = fs.existsSync(factsFile) ? JSON.parse(fs.readFileSync(factsFile, 'utf8')) : { clips: [] };
  const byId = new Map(previous.clips.map((c) => [c.id, c]));
  await withMusicPage(path.join(out, 'console.txt'), async (page) => {
    for (const id of ids) {
      const started = Date.now();
      const { facts, frames, chunks } = await page(`window.__albedianMusic.render(${JSON.stringify(id)}, { sourcesBase: ${JSON.stringify(sourcesBase)} })`);
      console.log(`  ${id}: facts received, ${chunks} chunks to fetch`);
      const raw = new Float32Array(2 * frames);
      let offset = 0;
      for (let c = 0; c < chunks; c++) {
        const b64 = await page(`window.__albedianMusic.chunk(${c})`);
        const bytes = Buffer.from(b64, 'base64');
        const part = new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
        raw.set(part, offset);
        offset += part.length;
      }
      const mastered = master(id, raw);
      const final = decodeFloat(mastered.masterFile);
      const mono = measureMono(mastered.masterFile, final);
      const clicks = await findClicks(final, facts.percussionOnsetsSec ?? []);
      encodeAac(mastered.masterFile, path.join(out, 'clips', `${id}.m4a`));
      const notes = facts.dataNotes.length;
      byId.set(id, {
        ...facts,
        mastering: {
          targetLufs: TARGET_LUFS,
          render: mastered.before,
          gainDb: Number(mastered.gainDb.toFixed(2)),
          limiter: mastered.limiter,
          master: mastered.after,
          mono,
          meter: "ffmpeg's ebur128 filter (EBU R128, ITU-R BS.1770; true peak by 4x oversampling)",
        },
        clicks,
        renderSeconds: Math.round((Date.now() - started) / 1000),
      });
      fs.writeFileSync(factsFile, JSON.stringify({ render: renderRecord(), clips: [...byId.values()] }, null, 1));
      console.log(
        `${id}: ${facts.durationSec.toFixed(1)} s, ${notes} star notes; render ${mastered.before.integratedLufs} LUFS, gain ${mastered.gainDb.toFixed(2)} dB${mastered.limiter ? ', limited' : ''}; master ${mastered.after.integratedLufs} LUFS, ${mastered.after.truePeakDbtp} dBTP, LRA ${mastered.after.loudnessRangeLu} LU; mono ${mono.integratedLufs} LUFS (corr ${mono.correlation}); clicks ${clicks.awayFromPercussion} away / ${clicks.onPercussion} on percussion`
      );
    }
  }).finally(() => server.close());
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const fmt = (n, digits = 0) => (n == null || !Number.isFinite(n) ? 'n/a' : Number(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }));
const LETTERS = { 'organ-pulse': 'A', 'ambient-beats': 'B', recording: 'C' };

function sourceRecord(file) {
  return SOURCES.files.find((s) => s.path === file) ?? null;
}

function starName(s) {
  return s.name ? `${s.name} (${s.designation ?? s.id})` : s.designation ?? s.id;
}

function clipBlock(c) {
  const letter = LETTERS[c.id];
  const inChord = c.inChord ?? { notes: c.dataNotes.length, outside: null };
  const measured = c.mastering;
  const stars = c.stars
    .map(
      (s) => `<tr><td>${esc(starName(s))}</td><td>${fmt(s.teffK)}</td><td>${fmt(s.luminositySolar, s.luminositySolar < 10 ? 2 : 0)}</td><td>${fmt(s.distancePc, 1)}</td><td>${fmt(s.receivedLevelDb, 1)}</td><td>${fmt(s.notes)}</td></tr>`
    )
    .join('');
  const chordRuns = [];
  for (const ch of c.chords) {
    const last = chordRuns.at(-1);
    if (last && last.chord === ch.chord && last.source === ch.source) last.to = ch.bar;
    else chordRuns.push({ from: ch.bar, to: ch.bar, chord: ch.chord, source: ch.source });
  }
  const chords = chordRuns.map((r) => `<li><span class="mono">${r.from === r.to ? `bar ${r.from}` : `bars ${r.from} to ${r.to}`}</span> ${esc(r.chord)} <span class="muted">(${esc(r.source)})</span></li>`).join('');
  const parts = c.parts.map((p) => `<li><span class="kind kind-${esc(p.kind)}">${esc(p.kind)}</span> <strong>${esc(p.name)}</strong>: ${esc(p.plays)}. <span class="muted">${esc(p.source)}</span></li>`).join('');
  const samples = [...new Set(c.samplesUsed.map((s) => sourceRecord(s.file)?.credit ?? s.file))].map((t) => `<li>${esc(t)}</li>`).join('');
  const tuned = c.samplesUsed.filter((s) => s.tuningCents != null);
  const tuning = tuned.length ? `Each pitched sample's tuning was measured on loading and corrected: ${fmt(Math.min(...tuned.map((s) => s.tuningCents)), 1)} to ${fmt(Math.max(...tuned.map((s) => s.tuningCents)), 1)} cents from equal temperament at A4 = 440 Hz.` : '';
  const m = c.dataMapping;
  const recording = c.recording ? `<p class="credit">Recording: ${esc(c.recording.credit)} Excerpt from ${fmt(c.recording.fromSec, 1)} s, ${fmt(c.recording.durationSec, 1)} s long, faded in and out; the stars' notes are added on top.</p>` : `<p class="credit">Music: Albedian. Composed for this page; every part and its instrument source is listed below.</p>`;
  return `<section class="clip" id="clip-${esc(c.id)}">
  <div class="clip-head"><span class="letter">${letter}</span><div><h2>${esc(c.title.replace(/^[ABC]\. /, ''))}</h2><p class="summary">${esc(c.summary)}</p></div></div>
  <audio controls preload="none" src="clips/${esc(c.id)}.m4a"></audio>
  <p class="meta mono">${fmt(c.durationSec, 0)} s · ${esc(c.key)} · ${fmt(c.bpm, c.bpm % 1 ? 1 : 0)} beats a minute · ${fmt(c.dataNotes.length)} notes played by ${fmt(c.stars.length)} stars</p>
  ${recording}
  <details class="facts"><summary>What you hear, and what each note is made from</summary>
    <h3>Parts</h3><ul class="parts">${parts}</ul>
    <h3>How the stars play</h3>
    <ul class="how">
      <li><strong>Which stars:</strong> the catalog stars on screen at the landing (${fmt(c.landing.starsOnScreen)} brighter than magnitude ${fmt(c.landing.limitingMagnitude, 1)}, ${fmt(c.landing.completeOnScreen)} of them with a Gaia DR3 temperature, luminosity and distance), the loudest by received light first.</li>
      <li><strong>Pitch:</strong> a tone of the chord sounding then, between ${esc(m.lowNote)} and ${esc(m.highNote)}, picked by the star's temperature on a log scale from ${fmt(m.teffLowK)} K to ${fmt(m.teffHighK)} K: cooler stars lower, hotter stars higher.</li>
      <li><strong>Loudness:</strong> from the star's received light (luminosity over distance squared), ${fmt(4 * m.dbPerDb, 0)} dB per magnitude, from 0 dB down to ${fmt(m.floorDb)} dB.</li>
      <li><strong>Timing:</strong> left to right across the screen, one note per step of the beat.</li>
      <li><strong>Place:</strong> left or right as the star sits on screen.</li>
      <li>A star missing any of the three values plays nothing. The brightest stars here (${esc(c.landing.silent.slice(0, 6).map((s) => s.name ?? s.designation ?? s.id).join(', '))}) carry no Gaia DR3 temperature or luminosity, so they are silent.</li>
    </ul>
    <h3>The stars that play</h3>
    <div class="table-wrap"><table><thead><tr><th>Star</th><th>Temperature (K)</th><th>Luminosity (Sun = 1)</th><th>Distance (pc)</th><th>Received level (dB)</th><th>Notes</th></tr></thead><tbody>${stars}</tbody></table></div>
    <p class="muted">Temperature: Gaia DR3 GSP-Phot. Luminosity: Gaia DR3 FLAME. Distance: Bailer-Jones et al. (2021) median. All read from the app's star tiles.</p>
    <h3>Chords</h3><ul class="chords">${chords}</ul>
    ${c.analysis ? `<p class="muted">${esc(c.analysis.statement)}</p>` : ''}
    <h3>Measured</h3>
    <ul class="measured mono">
      <li>Loudness ${fmt(measured.master.integratedLufs, 1)} LUFS, true peak ${fmt(measured.master.truePeakDbtp, 1)} dBTP, loudness range ${fmt(measured.master.loudnessRangeLu, 1)} LU (${esc(measured.meter)}).${measured.mono ? ` Mono fold-down (0.5 L + 0.5 R): ${fmt(measured.mono.integratedLufs, 1)} LUFS (${measured.mono.monoVsStereoDb >= 0 ? '+' : ''}${fmt(measured.mono.monoVsStereoDb, 2)} dB vs stereo power, L/R correlation ${fmt(measured.mono.correlation, 2)}).` : ''}</li>
      <li>Gain ${measured.gainDb >= 0 ? '+' : ''}${fmt(measured.gainDb, 2)} dB from the render's ${fmt(measured.render.integratedLufs, 1)} LUFS.${measured.limiter ? ` Limited: ${esc(measured.limiter.why)}; ${esc(measured.limiter.what)}.` : ' No limiter.'}</li>
      <li>Clicks found: ${fmt(c.clicks.awayFromPercussion ?? c.clicks.count)} away from percussion, ${fmt(c.clicks.onPercussion ?? 0)} within -5 to +80 ms of a percussion onset (${fmt(c.clicks.count)} total; ${esc(c.clicks.method)}).</li>
      <li>Star notes in their chord: ${fmt(inChord.notes - (inChord.outside ?? 0))} of ${fmt(inChord.notes)}.</li>
    </ul>
    <h3>Samples</h3><ul class="samples">${samples}</ul>
    <p class="muted">${esc(tuning)}</p>
  </details>
</section>`;
}

function writePage() {
  const factsPath = path.join(out, 'facts.json');
  const factsFile = JSON.parse(fs.readFileSync(factsPath, 'utf8'));
  factsFile.render = renderRecord();
  fs.writeFileSync(factsPath, JSON.stringify(factsFile, null, 1));
  const order = ['organ-pulse', 'ambient-beats', 'recording'];
  const clips = order.map((id) => factsFile.clips.find((c) => c.id === id)).filter(Boolean);
  const page = path.join(out, 'page');
  fs.mkdirSync(path.join(page, 'clips'), { recursive: true });
  for (const c of clips) fs.copyFileSync(path.join(out, 'clips', `${c.id}.m4a`), path.join(page, 'clips', `${c.id}.m4a`));
  fs.copyFileSync(factsPath, path.join(page, 'facts.json'));
  fs.copyFileSync(LANDING_PICTURE, path.join(page, 'landing.jpg'));
  fs.writeFileSync(path.join(page, 'notices.txt'), noticesText(clips));
  const render = factsFile.render;
  const landing = clips[0]?.landing;
  const choices = clips.map((c) => `<button type="button" class="choice" data-choice="${esc(c.id)}" aria-pressed="false" disabled>${LETTERS[c.id]}. ${esc(c.title.replace(/^[ABC]\. /, ''))}</button>`).join('');
  const html = `<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Albedian Music Directions</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap">
<style>${PAGE_STYLE}</style>
<main>
<header class="hero">
  <img src="landing.jpg" alt="The landing: the Moon's far side large and the Earth small beside it, in front of the centre of the Milky Way, on 7 March 2025 at 11:00 UTC" width="1920" height="1080">
  <div class="intro">
    <p class="eyebrow">Albedian · music over the landing</p>
    <h1>Three directions. Pick the one that feels right.</h1>
    <p class="lede">Each clip is about a minute of music for the opening view, the real moment above. Listen on headphones or good speakers, then choose one, with a note if you like.</p>
    <p class="lede">No sound reaches us from space. This is composed music; the notes the stars play are chosen by their catalog values, as each clip says.</p>
    ${landing ? `<p class="muted">The stars that play are the catalog stars on screen at this view: centre RA ${fmt(landing.centreRaDeg, 2)}°, Dec ${fmt(landing.centreDecDeg, 2)}°, a ${fmt(landing.fovDeg)}° field, at ${esc(landing.utc)}.</p>` : ''}
  </div>
</header>
${clips.map(clipBlock).join('\n')}
<section class="answer" aria-labelledby="pick-title">
  <h2 id="pick-title">Your pick</h2>
  <div class="choices">${choices}<button type="button" class="choice" data-choice="none" aria-pressed="false" disabled>None of these</button></div>
  <label class="note-label" for="pick-note">What you liked or did not (optional)</label>
  <textarea id="pick-note" rows="3" disabled></textarea>
  <div class="row"><button type="button" id="pick-save" disabled>Save note</button><span id="pick-state" class="muted" role="status">Not answered</span></div>
  <p class="banner" id="save-banner" role="status" hidden></p>
</section>
<footer>
  <p>Rendered on ${esc(render.renderedAt)} from commit ${esc(render.commit.slice(0, 10))}${render.uncommittedAudioChanges ? ' with uncommitted changes' : ''} at 48 kHz, mastered to ${TARGET_LUFS} LUFS and encoded as AAC at 256 kb/s.</p>
  <p>Credits</p>
  <ul>
    <li>A and B: Music: Albedian. Pipe organ sampled by Simon Dalzell of Ivy Audio; organ, chimes, vibraphone, glockenspiel, claves, cymbal, hi-hat, claps and shaker from the Versilian Community Sample Library (VCSL), Versilian Studios LLC, CC0 1.0.</li>
    ${clips.some((c) => c.recording) ? `<li>C: ${esc(clips.find((c) => c.recording).recording.credit)} Glockenspiel from VCSL, CC0 1.0.</li>` : ''}
    <li>Room in A: St. Nicholas Chapel, Aachen Cathedral, impulse response by Martin Zerwas and Selin Kayku (Zenodo, doi:10.5281/zenodo.23312556), CC BY 4.0, modified.</li>
    <li>Star data: ESA/Gaia/DPAC (Gaia DR3, CC BY-NC 3.0 IGO) and ESA Hipparcos (Hipparcos 2007); distances from Bailer-Jones et al. 2021.</li>
    <li>Landing picture: rendered by Albedian from JPL Horizons positions; NASA and catalog sources as the app credits them.</li>
  </ul>
  <details id="notices-details"><summary>Licences and notices</summary><pre id="notices">Open to load the notices.</pre></details>
</footer>
</main>
<script>${PAGE_SCRIPT}</script>
`;
  fs.writeFileSync(path.join(page, 'index.html'), html);
  const bytes = fs.readdirSync(path.join(page, 'clips')).reduce((n, f) => n + fs.statSync(path.join(page, 'clips', f)).size, 0);
  console.log(`page: ${path.join(page, 'index.html')} (${(Buffer.byteLength(html) / 1024).toFixed(0)} KB), ${clips.length} clips (${(bytes / 1e6).toFixed(1)} MB)`);
}

function noticesText(clips) {
  const used = new Set(clips.flatMap((c) => c.samplesUsed.map((s) => s.file)));
  for (const c of clips) if (c.recording) used.add(c.recording.file);
  const lines = ['Albedian music directions: every file these clips use, with its licence.', ''];
  for (const s of SOURCES.files.filter((f) => used.has(f.path))) {
    lines.push(`${s.path}`, `  ${s.credit}`, `  Licence: ${s.licence} (${s.licencePage})`, `  From: ${s.url}`, `  SHA-256: ${s.sha256}`, '');
  }
  lines.push('CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/legalcode', 'CC BY 4.0: https://creativecommons.org/licenses/by/4.0/legalcode', '');
  return lines.join('\n');
}

const PAGE_STYLE = `
:root {
  --bg: #f3f2ef; --surface: #ffffff; --fg: #15171c; --muted: #5d6270; --line: #dcdad4;
  --accent: #9a4f0c; --composed: #3d4f7a; --data: #9a4f0c; --recording: #5b6b2e;
  --font-ui: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --font-read: "Newsreader", Georgia, serif;
  --font-data: "IBM Plex Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #07090d; --surface: #10141b; --fg: #e8eaf0; --muted: #98a0b0; --line: #232a36; --accent: #f2a65a; --composed: #9db3e8; --data: #f2a65a; --recording: #bfd38a; color-scheme: dark; }
}
:root[data-theme="dark"] { --bg: #07090d; --surface: #10141b; --fg: #e8eaf0; --muted: #98a0b0; --line: #232a36; --accent: #f2a65a; --composed: #9db3e8; --data: #f2a65a; --recording: #bfd38a; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.55 var(--font-ui); padding: 0 max(16px, env(safe-area-inset-right, 0px)) 64px max(16px, env(safe-area-inset-left, 0px)); }
main { max-width: 820px; margin: 0 auto; display: flex; flex-direction: column; gap: 36px; }
.hero { display: flex; flex-direction: column; gap: 20px; margin-inline: calc(-1 * max(16px, env(safe-area-inset-left, 0px))); }
.hero img { width: 100%; height: auto; display: block; aspect-ratio: 16 / 9; object-fit: cover; background: #000; }
.intro { display: flex; flex-direction: column; gap: 12px; padding-inline: max(16px, env(safe-area-inset-left, 0px)); }
h1, h2, h3 { font-family: var(--font-read); font-weight: 500; margin: 0; text-wrap: balance; }
h1 { font-size: 2.1rem; line-height: 1.15; }
h2 { font-size: 1.5rem; line-height: 1.2; }
h3 { font-size: 1.05rem; margin-top: 14px; }
p { margin: 0; }
.eyebrow { font: 500 0.75rem/1.2 var(--font-data); letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
.lede { font-family: var(--font-read); font-size: 1.12rem; max-width: 62ch; }
.muted { color: var(--muted); }
.mono, .meta { font-family: var(--font-data); font-size: 0.8rem; }
.clip { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.clip-head { display: flex; gap: 14px; align-items: flex-start; }
.letter { flex: none; width: 2.2rem; height: 2.2rem; border-radius: 50%; display: grid; place-items: center; font: 600 1rem var(--font-data); border: 1px solid var(--accent); color: var(--accent); }
.summary { color: var(--muted); max-width: 64ch; }
audio { width: 100%; }
.credit { font-size: 0.9rem; }
.facts summary { cursor: pointer; color: var(--muted); }
.facts ul { padding-left: 1.1rem; margin: 6px 0; display: flex; flex-direction: column; gap: 4px; }
.kind { font: 500 0.7rem/1 var(--font-data); text-transform: uppercase; letter-spacing: 0.05em; padding: 2px 6px; border-radius: 4px; border: 1px solid currentColor; }
.kind-composed { color: var(--composed); } .kind-data { color: var(--data); } .kind-recording { color: var(--recording); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; font: 0.8rem/1.4 var(--font-data); min-width: 560px; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; }
.answer { display: flex; flex-direction: column; gap: 12px; padding-top: 8px; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; }
button { font: 600 0.95rem var(--font-ui); padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--fg); cursor: pointer; }
button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
button:disabled { opacity: 0.55; cursor: default; }
textarea { width: 100%; font: 15px/1.5 var(--font-ui); padding: 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--surface); color: var(--fg); }
.row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.banner { border: 1px solid var(--line); background: var(--surface); padding: 10px 14px; border-radius: 8px; color: var(--muted); }
footer { color: var(--muted); font-size: 0.85rem; display: flex; flex-direction: column; gap: 6px; }
footer ul { padding-left: 1.1rem; margin: 0; }
pre { white-space: pre-wrap; font: 0.75rem/1.4 var(--font-data); }
`;

const PAGE_SCRIPT = `
(() => {
  const banner = document.getElementById('save-banner');
  const stateEl = document.getElementById('pick-state');
  const note = document.getElementById('pick-note');
  const saveNote = document.getElementById('pick-save');
  const buttons = [...document.querySelectorAll('.choice')];
  const state = { choice: null, note: '', updatedAt: null };
  let chain = Promise.resolve();
  const show = (text) => { banner.textContent = text; banner.hidden = false; };
  const time = (iso) => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch { return iso; } };
  function paint() {
    for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.choice === state.choice));
    if (document.activeElement !== note && !note.dataset.dirty) note.value = state.note || '';
    stateEl.textContent = state.updatedAt ? 'Saved ' + time(state.updatedAt) : 'Not answered';
  }
  function save(db) {
    const body = { choice: state.choice, note: state.note || '', updatedAt: new Date().toISOString() };
    chain = chain.then(() => db.doc('answers/pick').set(body)).then(
      () => { state.updatedAt = body.updatedAt; delete note.dataset.dirty; paint(); },
      (error) => { stateEl.textContent = 'Not saved: ' + ((error && error.message) || 'the answer could not be stored'); }
    );
    stateEl.textContent = 'Saving';
  }
  async function start() {
    const api = window.claude;
    const db = api && typeof api.use === 'function' ? await api.use('db') : null;
    if (!db) { show('Your pick can be saved only when this page is opened as its Artifact on claude.ai. The clips and their facts work everywhere.'); return; }
    for (const b of buttons) { b.disabled = false; b.addEventListener('click', () => { state.choice = b.dataset.choice; paint(); save(db); }); }
    note.disabled = false; saveNote.disabled = false;
    note.addEventListener('input', () => { note.dataset.dirty = '1'; });
    saveNote.addEventListener('click', () => { state.note = note.value; save(db); });
    const timer = setTimeout(() => show('Saved answers cannot be reached yet. A new pick is kept once the store answers.'), 10000);
    db.collection('answers').onSnapshot(
      (snap) => {
        if (!snap.metadata.fromCache) { clearTimeout(timer); banner.hidden = true; }
        for (const d of snap.docs) {
          if (d.id !== 'pick') continue;
          const data = d.data() || {};
          state.choice = typeof data.choice === 'string' ? data.choice : null;
          state.note = typeof data.note === 'string' ? data.note : '';
          state.updatedAt = typeof data.updatedAt === 'string' ? data.updatedAt : null;
          paint();
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
    } catch { notices.textContent = 'The notices could not be loaded here; they are in notices.txt beside this page.'; }
  });
  const audios = [...document.querySelectorAll('audio')];
  for (const a of audios) a.addEventListener('play', () => { for (const other of audios) if (other !== a) other.pause(); });
  start();
})();
`;

if (!args['page-only']) await renderAll();
writePage();
