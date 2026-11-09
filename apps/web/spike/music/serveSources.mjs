#!/usr/bin/env node
/**
 * Serves the music's samples to the app in development, so the soundtrack plays in the real app
 * (AUDIO_ENGINE.md section 13.7). The app's music layer fetches from http://127.0.0.1:5199/ by
 * default in development (VITE_ALBEDIAN_MUSIC_SOURCES overrides it):
 *
 *   node apps/web/spike/music/serveSources.mjs [--port 5199]
 *
 * then open the app, turn Audio on, and pick the Music setting (or add ?music=organ-pulse,
 * ?music=ambient-beats or ?music=recording to pin a piece). Files come from .cache/music/sources/
 * (sources.json lists each with its licence and SHA-256; render.mjs checks them).
 * /recording-analysis.json is the recording piece's measured analysis (beats, key and bar chords,
 * from analysis.ts) as .cache/music/facts.json records it from the last render. Vite's /@fs route
 * cannot serve VCSL's file names that hold '#', hence this server.
 *
 * Loopback only. It serves regular files inside its folder and nothing else: no path that leaves the
 * folder, plain or escaped, and no symbolic link, wherever it points. Node's built-ins only.
 */

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

export const LOOPBACK = '127.0.0.1';

const cors = { 'Access-Control-Allow-Origin': '*' };

/**
 * The file `rel` names inside `sourcesDir`, or null when it leaves the folder, passes through a
 * symbolic link, or is not a regular file.
 */
function servableFile(sourcesDir, rel) {
  const file = path.resolve(sourcesDir, rel);
  if (!file.startsWith(sourcesDir + path.sep)) return null;
  let real;
  let realRoot;
  try {
    real = fs.realpathSync(file);
    realRoot = fs.realpathSync(sourcesDir);
  } catch {
    return null;
  }
  // The same path relative to the real folder as to the folder: no link on the way, in or out
  if (path.relative(realRoot, real) !== path.relative(sourcesDir, file)) return null;
  return fs.statSync(real).isFile() ? real : null;
}

function recordingAnalysis(factsFile) {
  if (!fs.existsSync(factsFile)) return null;
  const facts = JSON.parse(fs.readFileSync(factsFile, 'utf8'));
  return facts.clips?.find((clip) => clip.id === 'recording')?.analysis ?? null;
}

/** The server for one sources folder and facts file. */
export function createSourcesServer({ sourcesDir, factsFile }) {
  const root = path.resolve(sourcesDir);
  return http.createServer((req, res) => {
    let rel;
    try {
      rel = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname).replace(/^\/+/, '');
    } catch {
      res.writeHead(400, cors).end();
      return;
    }
    if (rel === 'recording-analysis.json') {
      const analysis = recordingAnalysis(factsFile);
      if (!analysis) {
        res.writeHead(404, cors).end();
        return;
      }
      res.writeHead(200, { ...cors, 'Content-Type': 'application/json' }).end(JSON.stringify(analysis));
      return;
    }
    const file = rel.includes('\0') ? null : servableFile(root, rel);
    if (!file) {
      res.writeHead(404, cors).end();
      return;
    }
    res.writeHead(200, { ...cors, 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(file).size });
    fs.createReadStream(file).pipe(res);
  });
}

/** Listens on the loopback address only; resolves with the address (port 0 picks a free port). */
export function listenOnLoopback(server, port) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, LOOPBACK, () => {
      server.off('error', reject);
      resolve(server.address());
    });
  });
}

// Run as a command (not when a test imports it)
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values: args } = parseArgs({ options: { port: { type: 'string', default: '5199' } } });
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
  const sourcesDir = path.join(repoRoot, '.cache/music/sources');
  const server = createSourcesServer({ sourcesDir, factsFile: path.join(repoRoot, '.cache/music/facts.json') });
  const { port } = await listenOnLoopback(server, Number(args.port));
  const count = fs.existsSync(sourcesDir) ? 'present' : 'missing (fetch them first; see sources.json)';
  console.log(`music sources on http://${LOOPBACK}:${port}/ (.cache/music/sources ${count})`);
}
