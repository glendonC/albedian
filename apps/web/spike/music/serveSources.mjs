#!/usr/bin/env node
/**
 * Serves the music's samples to the app in development, so the three directions can be compared in
 * the real app (AUDIO_ENGINE.md section 13). The app's music layer fetches from
 * http://127.0.0.1:5199/ by default in development (VITE_ALBEDIAN_MUSIC_SOURCES overrides it):
 *
 *   node apps/web/spike/music/serveSources.mjs [--port 5199]
 *
 * then open the app with ?music=organ-pulse, ?music=ambient-beats or ?music=recording and turn
 * Audio on. Files come from .cache/music/sources/ (sources.json lists each with its licence and
 * SHA-256; render.mjs checks them). /recording-analysis.json is direction C's measured analysis
 * (beats, key and bar chords, from analysis.ts) as .cache/music/facts.json records it from the
 * last render. Vite's /@fs route cannot serve VCSL's file names that hold '#', hence this server.
 * Loopback only, Node's built-ins only.
 */

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({ options: { port: { type: 'string', default: '5199' } } });
const here = path.dirname(new URL(import.meta.url).pathname);
const repoRoot = path.resolve(here, '../../../..');
const sourcesDir = path.join(repoRoot, '.cache/music/sources');
const factsFile = path.join(repoRoot, '.cache/music/facts.json');
const cors = { 'Access-Control-Allow-Origin': '*' };

function recordingAnalysis() {
  if (!fs.existsSync(factsFile)) return null;
  const facts = JSON.parse(fs.readFileSync(factsFile, 'utf8'));
  return facts.clips?.find((clip) => clip.id === 'recording')?.analysis ?? null;
}

const server = http.createServer((req, res) => {
  let rel;
  try {
    rel = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname).replace(/^\/+/, '');
  } catch {
    res.writeHead(400, cors).end();
    return;
  }
  if (rel === 'recording-analysis.json') {
    const analysis = recordingAnalysis();
    if (!analysis) {
      res.writeHead(404, cors).end();
      return;
    }
    res.writeHead(200, { ...cors, 'Content-Type': 'application/json' }).end(JSON.stringify(analysis));
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

server.listen(Number(args.port), '127.0.0.1', () => {
  const count = fs.existsSync(sourcesDir) ? 'present' : 'missing (fetch them first; see sources.json)';
  console.log(`music sources on http://127.0.0.1:${args.port}/ (.cache/music/sources ${count})`);
});
