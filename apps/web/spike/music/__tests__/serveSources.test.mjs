import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { LOOPBACK, createSourcesServer, listenOnLoopback } from '../serveSources.mjs';

/**
 * The development sources server (serveSources.mjs) on a folder made for the test: real files it
 * should serve, a file beside the folder it must never serve, and symbolic links inside the folder,
 * one pointing out of it and one pointing at a file inside it.
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
  fs.writeFileSync(path.join(tmp, 'secret.txt'), 'outside the sources folder');
  fs.symlinkSync(path.join(tmp, 'secret.txt'), path.join(sources, 'vcsl', 'out.wav'));
  fs.symlinkSync(path.join(sources, 'vcsl', 'a #1.wav'), path.join(sources, 'inside.wav'));
  fs.symlinkSync(tmp, path.join(sources, 'up'));
  const facts = path.join(tmp, 'facts.json');
  fs.writeFileSync(facts, JSON.stringify({ clips: [{ id: 'recording', analysis: ANALYSIS }] }));
  server = createSourcesServer({ sourcesDir: sources, factsFile: facts });
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
  it('serves a file in its folder, with a name that needs escaping, to any page', async () => {
    const res = await get('/vcsl/a%20%231.wav');
    expect(res.status).toBe(200);
    expect(res.body.equals(SAMPLE)).toBe(true);
    expect(res.headers['access-control-allow-origin']).toBe('*');
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
