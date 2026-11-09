import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { fieldPlan } from '../../../../../scripts/field.mjs';
import { LOOPBACK, createSourcesServer, listedSourcePaths, listenOnLoopback } from '../serveSources.mjs';

/**
 * The development sources server (serveSources.mjs) on a folder made for the test: a listed file it
 * should serve, an unlisted file in the folder and a file beside the folder it must never serve, and
 * symbolic links inside the folder (listed, so only the link rule refuses them), one pointing out of
 * it and one pointing at a file inside it.
 */
let tmp;
let server;
let port;

const SAMPLE = Buffer.from('a few bytes standing in for a sample');
const ANALYSIS = { file: 'scottbuckley/sb_signaltonoise_nomelody.mp3', bpm: 75 };

beforeAll(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-sources-'));
  const sources = path.join(tmp, 'sources');
  fs.mkdirSync(path.join(sources, 'vcsl'), { recursive: true });
  fs.writeFileSync(path.join(sources, 'vcsl', 'a #1.wav'), SAMPLE);
  fs.writeFileSync(path.join(sources, 'vcsl', 'unlisted.wav'), Buffer.from('a file sources.json does not list'));
  fs.writeFileSync(path.join(tmp, 'secret.txt'), 'outside the sources folder');
  fs.symlinkSync(path.join(tmp, 'secret.txt'), path.join(sources, 'vcsl', 'out.wav'));
  fs.symlinkSync(path.join(sources, 'vcsl', 'a #1.wav'), path.join(sources, 'inside.wav'));
  fs.symlinkSync(tmp, path.join(sources, 'up'));
  const facts = path.join(tmp, 'facts.json');
  fs.writeFileSync(facts, JSON.stringify({ clips: [{ id: 'recording', analysis: ANALYSIS }] }));
  const listed = ['vcsl/a #1.wav', 'vcsl/out.wav', 'inside.wav', 'up/secret.txt', 'up/sources/vcsl/a #1.wav'];
  server = createSourcesServer({ sourcesDir: sources, factsFile: facts, listed });
  ({ port } = await listenOnLoopback(server, 0));
});

afterAll(async () => {
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** GETs `rawPath` exactly as written, with no normalising of dots or escapes. */
function get(rawPath) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: LOOPBACK, port, path: rawPath, method: 'GET' }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
    req.end();
  });
}

describe('the development sources server', () => {
  it('serves a listed file in its folder, with a name that needs escaping, and allows no other origin', async () => {
    const res = await get('/vcsl/a%20%231.wav');
    expect(res.status).toBe(200);
    expect(res.body.equals(SAMPLE)).toBe(true);
    // The app and the field run reach it through the same-origin /music-sources/ proxy: no CORS needed
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
    const refused = await get('/vcsl/nothing-here.wav');
    expect(refused.headers['access-control-allow-origin']).toBeUndefined();
  });

  it('serves only the files sources.json lists: any other file in its folder is refused', async () => {
    const res = await get('/vcsl/unlisted.wav');
    expect(res.status).toBe(404);
    expect(res.body.toString('utf8')).not.toContain('does not list');
  });

  it('as a command, serves the files the committed sources.json lists, the recording and every sample among them', () => {
    const listed = listedSourcePaths(new URL('../sources.json', import.meta.url).pathname);
    expect(listed.size).toBeGreaterThan(100);
    expect(listed.has('scottbuckley/sb_signaltonoise_nomelody.mp3')).toBe(true);
    for (const file of listed) expect(file.startsWith('/') || file.includes('..'), file).toBe(false);
  });

  it("serves the recording's measured analysis from the facts file", async () => {
    const res = await get('/recording-analysis.json');
    expect(res.status).toBe(200);
    expect(JSON.parse(res.body.toString('utf8'))).toEqual(ANALYSIS);
  });

  it('answers 404 to every way out of its folder, plain or escaped', async () => {
    for (const raw of [
      '/../secret.txt',
      '/%2e%2e/secret.txt',
      '/%2E%2E%2Fsecret.txt',
      '/vcsl/%2e%2e%2f%2e%2e%2fsecret.txt',
      '/vcsl/..%2f..%2fsecret.txt',
      '/%2e%2e%5csecret.txt',
      '/%252e%252e/secret.txt',
      `/${encodeURIComponent(path.join(tmp, 'secret.txt'))}`,
      '/vcsl/a%20%231.wav%00',
      '/',
      '/vcsl',
    ]) {
      const res = await get(raw);
      expect(res.status, raw).toBe(404);
      expect(res.body.toString('utf8'), raw).not.toContain('outside the sources folder');
    }
  });

  it('answers 400 to a path that is not valid percent-encoding', async () => {
    expect((await get('/vcsl/%E0%A4%A')).status).toBe(400);
  });

  it('refuses symbolic links inside its folder, whether they point out of it or into it', async () => {
    for (const raw of ['/vcsl/out.wav', '/inside.wav', '/up/secret.txt', '/up/sources/vcsl/a%20%231.wav']) {
      const res = await get(raw);
      expect(res.status, raw).toBe(404);
    }
  });

  it('listens on the loopback address only', async () => {
    expect(server.address()).toMatchObject({ address: LOOPBACK, family: 'IPv4' });
    // Another address of this machine reaches nothing on that port
    const other = Object.values(os.networkInterfaces())
      .flat()
      .find((a) => a && a.family === 'IPv4' && !a.internal);
    if (!other) return;
    const refused = await new Promise((resolve) => {
      const socket = net.connect({ host: other.address, port });
      socket.setTimeout(2000);
      socket.on('connect', () => {
        socket.destroy();
        resolve(false);
      });
      socket.on('error', () => resolve(true));
      socket.on('timeout', () => {
        socket.destroy();
        resolve(true);
      });
    });
    expect(refused).toBe(true);
  });
});

describe('the sources server as pnpm field starts it', () => {
  it('reads the committed sources.json for its list: a listed file is served, an unlisted one refused', async () => {
    // The very command scripts/field.mjs runs, on a folder made for the test instead of .cache/music
    const step = fieldPlan({ lan: false, env: { ...process.env }, musicPort: 0 }).music;
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-field-music-'));
    const listedFile = 'scottbuckley/sb_signaltonoise_nomelody.mp3';
    fs.mkdirSync(path.join(dir, 'scottbuckley'), { recursive: true });
    fs.writeFileSync(path.join(dir, listedFile), SAMPLE);
    fs.writeFileSync(path.join(dir, 'scottbuckley', 'not-listed.mp3'), SAMPLE);
    const repoRoot = new URL('../../../../../', import.meta.url).pathname;
    const child = spawn(step.command, [...step.args, '--sources', dir, '--facts', path.join(dir, 'facts.json')], { cwd: repoRoot, env: step.env });
    try {
      const port = await new Promise((resolve, reject) => {
        let out = '';
        child.stdout.on('data', (chunk) => {
          out += chunk;
          const match = /http:\/\/127\.0\.0\.1:(\d+)\//.exec(out);
          if (match) resolve(Number(match[1]));
        });
        child.on('exit', (code) => reject(new Error(`serveSources.mjs exited (${code}) before listening: ${out}`)));
      });
      const ask = (rawPath) =>
        new Promise((resolve, reject) => {
          http.get({ host: LOOPBACK, port, path: rawPath }, (res) => {
            const chunks = [];
            res.on('data', (c) => chunks.push(c));
            res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
          }).on('error', reject);
        });
      // The planted bytes, so the file came from the folder given, not from a real cache on this machine
      const listedRes = await ask(`/${listedFile}`);
      expect(listedRes.status).toBe(200);
      expect(listedRes.body.equals(SAMPLE)).toBe(true);
      expect((await ask('/scottbuckley/not-listed.mp3')).status).toBe(404);
    } finally {
      child.kill();
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
