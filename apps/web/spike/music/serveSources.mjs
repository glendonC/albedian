#!/usr/bin/env node
/**
 * Serves the music's samples to the app in development and in the field run, so the soundtrack plays
 * in the real app (AUDIO_ENGINE.md section 13.7). The app fetches them from its own origin at
 * /music-sources/, which Vite proxies to this server on 127.0.0.1:5199 (vite.config.ts;
 * ALBEDIAN_MUSIC_TARGET moves it, VITE_ALBEDIAN_MUSIC_SOURCES sets another base). pnpm field starts it:
 *
 *   node apps/web/spike/music/serveSources.mjs [--port 5199] [--sources <dir>] [--facts <file>]
 *
 * then open the app, turn Audio on, and pick the Music setting (or add ?music=organ-pulse,
 * ?music=ambient-beats or ?music=recording to pin a piece). Files come from .cache/music/sources/
 * (sources.json lists each with its licence and SHA-256; render.mjs checks them).
 * /recording-analysis.json is the recording piece's measured analysis (beats, key and bar chords,
 * from analysis.ts) as .cache/music/facts.json records it from the last render. Vite's /@fs route
 * cannot serve VCSL's file names that hold '#', hence this server.
 *
 * Loopback only, and only the files sources.json lists (with their licences and SHA-256) plus the
 * analysis: no other file in the folder, no path that leaves it, plain or escaped, and no symbolic link,
 * wherever it points. The app and the field run reach it through the web app's own origin (the
 * /music-sources/ proxy in vite.config.ts), so it sends no cross-origin header. Node's built-ins only.
 */

import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

export const LOOPBACK = '127.0.0.1';

/** The relative paths a sources.json lists, the only files the server sends. */
export function listedSourcePaths(sourcesJsonFile) {
  const { files } = JSON.parse(fs.readFileSync(sourcesJsonFile, 'utf8'));
  return new Set(files.map((file) => file.path));
}

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

/** The server for one sources folder and facts file, sending only the `listed` relative paths. */
export function createSourcesServer({ sourcesDir, factsFile, listed }) {
  const root = path.resolve(sourcesDir);
  const allowed = new Set(listed);
  return http.createServer((req, res) => {
    let rel;
    try {
      rel = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname).replace(/^\/+/, '');
    } catch {
      res.writeHead(400).end();
      return;
    }
    if (rel === 'recording-analysis.json') {
      const analysis = recordingAnalysis(factsFile);
      if (!analysis) {
        res.writeHead(404).end();
        return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(analysis));
      return;
    }
    const file = !allowed.has(rel) || rel.includes('\0') ? null : servableFile(root, rel);
    if (!file) {
      res.writeHead(404).end();
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': fs.statSync(file).size });
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
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
  const { values: args } = parseArgs({
    options: {
      port: { type: 'string', default: '5199' },
      // The folder and facts file default to the render's cache; a test points them at its own
      sources: { type: 'string', default: path.join(repoRoot, '.cache/music/sources') },
      facts: { type: 'string', default: path.join(repoRoot, '.cache/music/facts.json') },
    },
  });
  const sourcesDir = path.resolve(args.sources);
  // The list is always the committed sources.json beside this file, whatever folder is served
  const listed = listedSourcePaths(path.join(path.dirname(fileURLToPath(import.meta.url)), 'sources.json'));
  const server = createSourcesServer({ sourcesDir, factsFile: path.resolve(args.facts), listed });
  const { port } = await listenOnLoopback(server, Number(args.port));
  const count = fs.existsSync(sourcesDir) ? 'present' : 'missing (fetch them first; see sources.json)';
  console.log(`music sources on http://${LOOPBACK}:${port}/ (${path.relative(repoRoot, sourcesDir) || sourcesDir} ${count}, ${listed.size} files listed)`);
}
