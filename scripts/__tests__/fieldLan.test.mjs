// What `pnpm field --lan` hands a device on the network: the built app only, never a repository file, and every API
// route but media discover. The servers here bind 127.0.0.1 only, never the network, and every planted file sits in
// a temporary copy of the repository layout: a test must never write into data/uploads, which in a lane worktree is
// a link to the owner's clips.
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import fc from 'fast-check';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createSourcesServer, listenOnLoopback } from '../../apps/web/spike/music/serveSources.mjs';
import { DISCOVER_PATH, refuseOnNetwork } from '../field-proxy-guard.mjs';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const WEB_CONFIG = path.join(REPO_ROOT, 'apps/web/vite.config.ts');

// vite is a dependency of the web app only, so it is loaded from there, as `pnpm --filter @albedian/web exec vite` runs it
async function loadVite() {
  const webRequire = createRequire(path.join(REPO_ROOT, 'apps/web/package.json'));
  const viteDir = path.dirname(webRequire.resolve('vite/package.json'));
  return import(pathToFileURL(path.join(viteDir, 'dist/node/index.js')).href);
}

const fromNetwork = (url) => ({ url, socket: { remoteAddress: '192.168.1.30' } });
const fromLaptop = (url, remoteAddress = '127.0.0.1') => ({ url, socket: { remoteAddress } });

const DISCOVER_SPELLINGS = [
  '/api/media/discover',
  '/api/media/discover?folder=x',
  '/api/media/discover/',
  '/api/media/discover/sub',
  '/api/media/%64iscover',
  '/api/media%2Fdiscover',
  '/api//media/discover',
  '/api/media/./discover',
  '/api/media/x/../discover',
  '/api/MEDIA/Discover',
  '/api/media/discover#top',
];

describe('the /api proxy guard', () => {
  it('refuses media discover to a network peer in every spelling the API would route', () => {
    for (const url of DISCOVER_SPELLINGS) {
      expect(refuseOnNetwork(fromNetwork(url)), url).toBe(false);
    }
  });

  it('refuses a network peer a path with a malformed escape, since where it goes cannot be told', () => {
    expect(refuseOnNetwork(fromNetwork('/api/media/%zz'))).toBe(false);
    expect(refuseOnNetwork(fromNetwork('/api/ingest/%E0%A4%A'))).toBe(false);
  });

  it('lets a network peer reach every other API route', () => {
    for (const url of ['/api/health', '/api/ingest', '/api/ingest/ab12/solve', '/api/media/restore', '/api/voice/command']) {
      expect(refuseOnNetwork(fromNetwork(url)), url).toBeUndefined();
    }
  });

  it('lets this laptop reach media discover, so it keeps the full experience', () => {
    for (const address of ['127.0.0.1', '127.0.0.2', '::1', '::ffff:127.0.0.1']) {
      expect(refuseOnNetwork(fromLaptop('/api/media/discover', address)), address).toBeUndefined();
    }
  });

  it('counts only loopback addresses as this machine', () => {
    for (const address of ['10.0.0.7', '172.20.10.3', '0.0.0.0', '::', 'fe80::1', '2001:db8::1', '::ffff:192.168.1.30', '::ffff:10.0.0.7', '128.0.0.1']) {
      const req = { url: '/api/media/discover', socket: { remoteAddress: address } };
      expect(refuseOnNetwork(req), address).toBe(false);
    }
  });

  it('treats a request whose peer is unknown as a network peer', () => {
    expect(refuseOnNetwork({ url: '/api/media/discover', socket: {} })).toBe(false);
    expect(refuseOnNetwork({ url: '/api/media/discover' })).toBe(false);
  });

  it('PBT: any mix of escapes, case, doubled slashes and dot segments on discover is refused to a network peer', () => {
    const escape = (ch, upper) => {
      const hex = ch.charCodeAt(0).toString(16);
      return `%${upper ? hex.toUpperCase() : hex}`;
    };
    const spelledChar = (ch) =>
      fc.constantFrom('plain', 'upper', 'escape', 'ESCAPE').map((how) => {
        if (how === 'upper') return ch.toUpperCase();
        if (how === 'escape') return escape(ch, false);
        if (how === 'ESCAPE') return escape(ch, true);
        return ch;
      });
    const spelledSegment = (segment) => fc.tuple(...[...segment].map(spelledChar)).map((chars) => chars.join(''));
    const separator = fc.constantFrom('/', '//', '///', '/./', '/x/../', '/%2F', '/%2f');
    const tail = fc.constantFrom('', '/', '?a=1', '/?a=1', '#f');
    const spelledPath = fc
      .tuple(spelledSegment('api'), separator, spelledSegment('media'), separator, spelledSegment('discover'), tail)
      .map(([api, s1, media, s2, discover, end]) => `/${api}${s1}${media}${s2}${discover}${end}`);
    fc.assert(
      fc.property(spelledPath, (url) => refuseOnNetwork(fromNetwork(url)) === false),
      { examples: DISCOVER_SPELLINGS.map((url) => [url]) }
    );
  });

  it('PBT: a loopback peer is never refused, whatever the path', () => {
    fc.assert(fc.property(fc.string(), (tail) => refuseOnNetwork(fromLaptop(`/api/${tail}`)) === undefined), {
      examples: [['media/discover'], ['media/%64iscover']],
    });
  });

  it('is wired into the /api proxy of the web app config, for the dev server and for preview', async () => {
    const { resolveConfig } = await loadVite();
    const config = await resolveConfig({ configFile: WEB_CONFIG, logLevel: 'silent' }, 'serve');
    for (const proxy of [config.server.proxy, config.preview.proxy]) {
      const bypass = proxy['/api'].bypass;
      expect(typeof bypass).toBe('function');
      expect(bypass(fromNetwork(DISCOVER_PATH))).toBe(false);
      expect(bypass(fromLaptop(DISCOVER_PATH))).toBeUndefined();
    }
  });
});

describe('the /music-sources/ proxy', () => {
  it('proxies /music-sources/ to the music sources server on this machine, prefix stripped, for the dev server and for preview', async () => {
    const { resolveConfig } = await loadVite();
    const saved = process.env.ALBEDIAN_MUSIC_TARGET;
    delete process.env.ALBEDIAN_MUSIC_TARGET;
    try {
      const config = await resolveConfig({ configFile: WEB_CONFIG, logLevel: 'silent' }, 'serve');
      for (const proxy of [config.server.proxy, config.preview.proxy]) {
        const music = proxy['/music-sources/'];
        expect(music.target).toBe('http://127.0.0.1:5199');
        expect(music.rewrite('/music-sources/vcsl/C%234.wav')).toBe('/vcsl/C%234.wav');
      }
    } finally {
      if (saved !== undefined) process.env.ALBEDIAN_MUSIC_TARGET = saved;
    }
  });
});

describe('vite preview with the web app config, as --lan serves it', () => {
  const MARKER = 'planted stand-in for an uploaded clip';
  const NOTES_MARKER = 'planted stand-in for the lead notes';
  let root;
  let vite;
  let preview;
  let devServer;
  let api;
  let music;
  const apiSeen = [];
  const savedTarget = process.env.ALBEDIAN_API_TARGET;
  const savedMusicTarget = process.env.ALBEDIAN_MUSIC_TARGET;
  const SAMPLE = 'stand-in sample bytes for a chime at C sharp 4';

  const portOf = (server) => server.address().port;

  // A raw request, so a path such as /../ reaches the server exactly as written
  function get(port, rawPath) {
    return new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path: rawPath, method: 'GET' }, (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => (body += chunk));
        res.on('end', () => resolve({ status: res.statusCode, body }));
      });
      req.on('error', reject);
      req.end();
    });
  }

  beforeAll(async () => {
    vite = await loadVite();
    // The real path: macOS's tmpdir is a link, and Vite checks file access on real paths
    root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-field-lan-')));
    // The repository's layout: a pnpm workspace at the root, the built app in apps/web/dist, uploads and notes beside it
    fs.writeFileSync(path.join(root, 'pnpm-workspace.yaml'), 'packages:\n  - "apps/*"\n');
    fs.mkdirSync(path.join(root, 'apps/web/dist'), { recursive: true });
    fs.writeFileSync(path.join(root, 'apps/web/package.json'), '{"name":"stand-in-web","private":true}\n');
    fs.writeFileSync(path.join(root, 'apps/web/dist/index.html'), '<!doctype html><title>built app stand-in</title>\n');
    fs.mkdirSync(path.join(root, 'data/uploads'), { recursive: true });
    fs.writeFileSync(path.join(root, 'data/uploads/planted-clip.txt'), `${MARKER}\n`);
    fs.mkdirSync(path.join(root, '.lead'), { recursive: true });
    fs.writeFileSync(path.join(root, '.lead/planted-notes.md'), `${NOTES_MARKER}\n`);

    api = http.createServer((req, res) => {
      apiSeen.push(req.url);
      res.setHeader('content-type', 'application/json');
      res.end('{"standIn":true}');
    });
    await new Promise((resolve) => api.listen(0, '127.0.0.1', resolve));
    process.env.ALBEDIAN_API_TARGET = `http://127.0.0.1:${portOf(api)}`;

    // The music sources folder as the field run serves it, with the audio lane's own server: one sample whose name
    // holds '#' as VCSL's do, and two links inside the folder that point out of it, at the planted clip and its folder
    const sources = path.join(root, '.cache/music/sources');
    fs.mkdirSync(path.join(sources, 'vcsl/Chimes'), { recursive: true });
    fs.writeFileSync(path.join(sources, 'vcsl/Chimes/C#4.wav'), SAMPLE);
    fs.symlinkSync(path.join(root, 'data/uploads/planted-clip.txt'), path.join(sources, 'escape.txt'));
    fs.symlinkSync(path.join(root, 'data/uploads'), path.join(sources, 'out'));
    music = createSourcesServer({ sourcesDir: sources, factsFile: path.join(root, '.cache/music/facts.json') });
    const { port: musicPort } = await listenOnLoopback(music, 0);
    process.env.ALBEDIAN_MUSIC_TARGET = `http://127.0.0.1:${musicPort}`;

    preview = await vite.preview({
      root: path.join(root, 'apps/web'),
      configFile: WEB_CONFIG,
      logLevel: 'silent',
      preview: { host: '127.0.0.1', port: 0, strictPort: true, open: false },
    });

    // The dev server --lan used to run, as a control: it must hand out the same planted file, or the refusal is vacuous
    devServer = await vite.createServer({
      root: path.join(root, 'apps/web'),
      configFile: false,
      logLevel: 'silent',
      optimizeDeps: { noDiscovery: true, include: [] },
      server: { host: '127.0.0.1', port: 0, strictPort: true, hmr: false, watch: null },
    });
    await devServer.listen();
  }, 60_000);

  afterAll(async () => {
    if (savedTarget === undefined) delete process.env.ALBEDIAN_API_TARGET;
    else process.env.ALBEDIAN_API_TARGET = savedTarget;
    if (savedMusicTarget === undefined) delete process.env.ALBEDIAN_MUSIC_TARGET;
    else process.env.ALBEDIAN_MUSIC_TARGET = savedMusicTarget;
    await new Promise((resolve) => (music ? music.close(resolve) : resolve()));
    await preview?.close();
    await devServer?.close();
    await new Promise((resolve) => (api ? api.close(resolve) : resolve()));
    if (root) fs.rmSync(root, { recursive: true, force: true });
  });

  it('control: the dev server hands out a planted file under data/uploads by its /@fs/ path', async () => {
    const res = await get(portOf(devServer.httpServer), `/@fs${root}/data/uploads/planted-clip.txt`);
    expect(res.status).toBe(200);
    expect(res.body).toContain(MARKER);
  });

  it('serves the built app', async () => {
    const res = await get(portOf(preview.httpServer), '/');
    expect(res.status).toBe(200);
    expect(res.body).toContain('built app stand-in');
  });

  it('refuses a planted file under data/uploads and the notes, however the path is written', async () => {
    const port = portOf(preview.httpServer);
    const paths = [
      `/@fs${root}/data/uploads/planted-clip.txt`,
      `/@fs${root}/.lead/planted-notes.md`,
      `/@fs${root}/apps/web/dist/../../../data/uploads/planted-clip.txt`,
      '/../../data/uploads/planted-clip.txt',
      '/%2e%2e/%2e%2e/data/uploads/planted-clip.txt',
      '/..%2f..%2fdata/uploads/planted-clip.txt',
      '/data/uploads/planted-clip.txt',
      '/.lead/planted-notes.md',
    ];
    for (const rawPath of paths) {
      const res = await get(port, rawPath);
      expect(res.body, rawPath).not.toContain(MARKER);
      expect(res.body, rawPath).not.toContain(NOTES_MARKER);
    }
  });

  it('serves a music sample through /music-sources/, a # in its name included', async () => {
    const res = await get(portOf(preview.httpServer), '/music-sources/vcsl/Chimes/C%234.wav');
    expect(res.status).toBe(200);
    expect(res.body).toBe(SAMPLE);
  });

  it('never serves a file outside the music sources folder through /music-sources/, by path or by a link inside it', async () => {
    const port = portOf(preview.httpServer);
    const paths = [
      '/music-sources/../../../data/uploads/planted-clip.txt',
      '/music-sources/%2e%2e/%2e%2e/%2e%2e/data/uploads/planted-clip.txt',
      '/music-sources/..%2f..%2f..%2fdata/uploads/planted-clip.txt',
      `/music-sources/${encodeURIComponent(path.join(root, 'data/uploads/planted-clip.txt'))}`,
      '/music-sources/escape.txt',
      '/music-sources/out/planted-clip.txt',
    ];
    for (const rawPath of paths) {
      const res = await get(port, rawPath);
      expect(res.body, rawPath).not.toContain(MARKER);
    }
  });

  it('forwards /api to the API on 127.0.0.1, and this laptop still reaches media discover', async () => {
    const port = portOf(preview.httpServer);
    for (const route of ['/api/health', DISCOVER_PATH]) {
      const res = await get(port, route);
      expect(res.status, route).toBe(200);
      expect(res.body, route).toContain('standIn');
    }
    expect(apiSeen).toEqual(['/api/health', DISCOVER_PATH]);
  });
});
