import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import {
  NETWORK_IDLE_TIMEOUT_MS,
  PAGE_LOAD_TIMEOUT_MS,
  REPEAT_FRAME_ATTEMPTS,
  SETTLE_MS,
  captureRendererTarget,
  formatConsoleEvent,
  runCaptureLanding,
} from '../capture_landing/captureRunner.mjs';
import {
  DEFAULT_CHROME_PATH,
  DEFAULT_DEVTOOLS_URL_TIMEOUT_MS,
  buildChromeLaunchArgs,
  resolveChromePath,
  runWithHeadlessChrome,
  waitForDevToolsWsUrl,
} from '../capture_landing/chromeSession.mjs';
import {
  DEFAULT_CAPTURE_URL,
  DEFAULT_RENDERER,
  DEFAULT_SCENE,
  DEFAULT_SIZE,
  DEFAULT_TIME,
  USAGE_LINE,
  buildCaptureTargetUrl,
  parseCaptureArgs,
  parseViewportSize,
  resolveRendererKinds,
} from '../capture_landing/cliOptions.mjs';
import {
  CHUNK_CHARS,
  DEFAULT_EVALUATE_TIMEOUT_MS,
  DevTools,
  decodeDataUrlPayload,
  evaluateInPage,
  fetchStoredDataUrl,
  writeDataUrl,
} from '../capture_landing/devtoolsClient.mjs';
import {
  HELPERS_SCRIPT,
  MAX_BODY_RADIUS,
  PRELUDE_SCRIPT,
  STAR_MOUNT_NAMES,
  installCaptureHelpers,
  installCapturePrelude,
} from '../capture_landing/pageHelpers.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT_PATH = path.resolve(HERE, '../capture_landing.mjs');

/** Minimal RFC 6455 loopback server frame encoder/decoder for end-to-end CDP characterization. */
function encodeWsTextFrame(text) {
  const payload = Buffer.from(text, 'utf8');
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x81, len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x81;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}

function createLoopbackCdpServer(onCdpCommand) {
  const sockets = new Set();
  const server = http.createServer();
  server.on('upgrade', (req, socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
    const key = req.headers['sec-websocket-key'];
    const accept = crypto
      .createHash('sha1')
      .update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest('base64');
    socket.write(
      [
        'HTTP/1.1 101 Switching Protocols',
        'Upgrade: websocket',
        'Connection: Upgrade',
        `Sec-WebSocket-Accept: ${accept}`,
        '\r\n',
      ].join('\r\n'),
    );

    let buf = Buffer.alloc(0);
    const sendJson = (obj) => {
      if (!socket.destroyed) socket.write(encodeWsTextFrame(JSON.stringify(obj)));
    };

    socket.on('data', (chunk) => {
      buf = Buffer.concat([buf, chunk]);
      while (buf.length >= 2) {
        const opcode = buf[0] & 0x0f;
        if (opcode === 0x08) {
          socket.end();
          return;
        }
        const masked = (buf[1] & 0x80) !== 0;
        let len = buf[1] & 0x7f;
        let offset = 2;
        if (len === 126) {
          if (buf.length < 4) return;
          len = buf.readUInt16BE(2);
          offset = 4;
        } else if (len === 127) {
          if (buf.length < 10) return;
          len = Number(buf.readBigUInt64BE(2));
          offset = 10;
        }
        const maskBytes = masked ? 4 : 0;
        if (buf.length < offset + maskBytes + len) return;
        const payload = Buffer.from(buf.subarray(offset + maskBytes, offset + maskBytes + len));
        if (masked) {
          const mask = buf.subarray(offset, offset + 4);
          for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
        }
        buf = buf.subarray(offset + maskBytes + len);
        if (opcode === 0x01) {
          const msg = JSON.parse(payload.toString('utf8'));
          onCdpCommand(msg, sendJson);
        }
      }
    });
  });

  return {
    async listen() {
      await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
      const addr = server.address();
      return addr.port;
    },
    async close() {
      for (const s of sockets) s.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

describe('capture_landing.mjs end-to-end CLI characterization', () => {
  it('re-exports the complete explicit named facade without triggering CLI side effects on import', async () => {
    const captureLandingFacade = await import('../capture_landing.mjs');
    expect(Object.keys(captureLandingFacade).sort()).toEqual([
      'CHUNK_CHARS',
      'DEFAULT_CAPTURE_URL',
      'DEFAULT_CHROME_PATH',
      'DEFAULT_DEVTOOLS_URL_TIMEOUT_MS',
      'DEFAULT_EVALUATE_TIMEOUT_MS',
      'DEFAULT_RENDERER',
      'DEFAULT_SCENE',
      'DEFAULT_SIZE',
      'DEFAULT_TIME',
      'DevTools',
      'HELPERS_SCRIPT',
      'MAX_BODY_RADIUS',
      'NETWORK_IDLE_TIMEOUT_MS',
      'PAGE_LOAD_TIMEOUT_MS',
      'PRELUDE_SCRIPT',
      'REPEAT_FRAME_ATTEMPTS',
      'SETTLE_MS',
      'STAR_MOUNT_NAMES',
      'USAGE_LINE',
      'buildCaptureTargetUrl',
      'buildChromeLaunchArgs',
      'captureRendererTarget',
      'decodeDataUrlPayload',
      'evaluateInPage',
      'fetchStoredDataUrl',
      'formatConsoleEvent',
      'installCaptureHelpers',
      'installCapturePrelude',
      'parseCaptureArgs',
      'parseViewportSize',
      'resolveChromePath',
      'resolveRendererKinds',
      'runCaptureLanding',
      'runWithHeadlessChrome',
      'waitForDevToolsWsUrl',
      'writeDataUrl',
    ]);
  });

  it('exits with code 2 and usage line on stderr when --out is missing', () => {
    const res = spawnSync(process.execPath, [SCRIPT_PATH], { encoding: 'utf8' });
    expect(res.status).toBe(2);
    expect(res.stderr.trim()).toBe(
      'usage: node scripts/capture_landing.mjs --out <dir> [--url ...] [--renderer webgl|webgpu|both]',
    );
  });

  it(
    'drives a loopback DevTools session via CHROME_PATH, writes PNGs + camera JSON + console log, and deletes the temp profile',
    { timeout: 25_000 },
    async () => {
      const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'capture-char-'));
      const outDir = path.join(tmpRoot, 'out');
      const argsRecordFile = path.join(tmpRoot, 'chrome-args.json');
      const fakeChromeScript = path.join(tmpRoot, 'fake-chrome.mjs');

      const fullBytes = Buffer.from('89504e470d0a1a0a01020304', 'hex');
      const domeBytes = Buffer.from('89504e470d0a1a0a05060708', 'hex');
      const fullDataUrl = `data:image/png;base64,${fullBytes.toString('base64')}`;
      const domeDataUrl = `data:image/png;base64,${domeBytes.toString('base64')}`;
      let starsHidden = false;
      let storedUrl = '';
      const navigatedUrls = [];

      const cdp = createLoopbackCdpServer((msg, sendJson) => {
        const { id, method, params, sessionId } = msg;
        if (method === 'Target.createTarget') {
          sendJson({ id, result: { targetId: 't-1' } });
          return;
        }
        if (method === 'Target.attachToTarget') {
          sendJson({ id, result: { sessionId: 's-1' } });
          return;
        }
        if (method === 'Page.navigate') {
          navigatedUrls.push(params.url);
          sendJson({ id, sessionId, result: {} });
          sendJson({
            sessionId,
            method: 'Runtime.consoleAPICalled',
            params: { type: 'info', args: [{ value: 'scene ready' }] },
          });
          sendJson({ sessionId, method: 'Page.loadEventFired', params: {} });
          return;
        }
        if (method === 'Runtime.evaluate') {
          const expr = params.expression;
          if (expr.includes('__capture =') || expr.includes('installCaptureHelpers')) {
            sendJson({ id, sessionId, result: { result: { value: true } } });
            return;
          }
          if (expr.startsWith('window.__capture.store(')) {
            storedUrl = starsHidden ? domeDataUrl : fullDataUrl;
            const sha256 = crypto.createHash('sha256').update(storedUrl).digest('hex');
            sendJson({ id, sessionId, result: { result: { value: { length: storedUrl.length, sha256 } } } });
            return;
          }
          const pieceMatch = expr.match(/window\.__capture\.piece\((\d+),\s*(\d+)\)/);
          if (pieceMatch) {
            const slice = storedUrl.slice(Number(pieceMatch[1]), Number(pieceMatch[2]));
            sendJson({ id, sessionId, result: { result: { value: slice } } });
            return;
          }
          if (expr.startsWith('window.__capture.geometry(')) {
            sendJson({
              id,
              sessionId,
              result: {
                result: {
                  value: {
                    kind: 'webgl',
                    fov: 55.4,
                    aspect: 640 / 360,
                    projectionMatrix: Array.from({ length: 16 }, (_, i) => (i % 5 === 0 ? 1 : 0)),
                    matrixWorld: Array.from({ length: 16 }, (_, i) => (i % 5 === 0 ? 1 : 0)),
                    starMountMatrixWorld: Array.from({ length: 16 }, (_, i) => (i % 5 === 0 ? 1 : 0)),
                    bodies: [{ name: 'cislunarMoon', center: [0, 0, -10], radius: 1.7, matrixWorld: Array(16).fill(0) }],
                  },
                },
              },
            });
            return;
          }
          if (expr === 'window.__capture.setStarsVisible(false)') {
            starsHidden = true;
            sendJson({ id, sessionId, result: { result: { value: 1 } } });
            return;
          }
          if (expr === 'window.__capture.setStarsVisible(true)') {
            starsHidden = false;
            sendJson({ id, sessionId, result: { result: { value: 1 } } });
            return;
          }
        }
        sendJson({ id, sessionId, result: {} });
      });

      const port = await cdp.listen();
      try {
        fs.writeFileSync(
          fakeChromeScript,
          [
            `#!/usr/bin/env node`,
            `import fs from 'node:fs';`,
            `fs.writeFileSync(${JSON.stringify(argsRecordFile)}, JSON.stringify(process.argv.slice(2)));`,
            `process.stderr.write('DevTools listening on ws://127.0.0.1:${port}/devtools/browser/mock\\n');`,
            `setInterval(() => {}, 1000);`,
            `process.on('SIGTERM', () => process.exit(0));`,
          ].join('\n'),
          { mode: 0o755 },
        );

        const res = await new Promise((resolve, reject) => {
          import('node:child_process').then(({ spawn }) => {
            const child = spawn(
              process.execPath,
              [
                SCRIPT_PATH,
                '--out',
                outDir,
                '--url',
                'http://127.0.0.1:5173',
                '--renderer',
                'webgl',
                '--scene',
                '2',
                '--t',
                '7',
                '--size',
                '640x360',
              ],
              { env: { ...process.env, CHROME_PATH: fakeChromeScript }, stdio: ['ignore', 'pipe', 'pipe'] },
            );
            let stdout = '';
            let stderr = '';
            child.stdout.on('data', (d) => (stdout += d.toString()));
            child.stderr.on('data', (d) => (stderr += d.toString()));
            child.on('error', reject);
            child.on('close', (code) => resolve({ code, stdout, stderr }));
          });
        });

        expect(res.code, res.stderr).toBe(0);
        expect(navigatedUrls).toEqual(['http://127.0.0.1:5173/?scene=2&freeze=1&t=7']);

        const chromeArgs = JSON.parse(fs.readFileSync(argsRecordFile, 'utf8'));
        expect(chromeArgs).toContain('--headless=new');
        expect(chromeArgs).toContain('--window-size=640,360');
        const profileArg = chromeArgs.find((a) => a.startsWith('--user-data-dir='));
        expect(profileArg).toBeTruthy();
        const profileDir = profileArg.slice('--user-data-dir='.length);
        expect(fs.existsSync(profileDir)).toBe(false);

        expect(fs.readFileSync(path.join(outDir, 'webgl-full.png'))).toEqual(fullBytes);
        expect(fs.readFileSync(path.join(outDir, 'webgl-dome.png'))).toEqual(domeBytes);

        const camJson = JSON.parse(fs.readFileSync(path.join(outDir, 'webgl-camera.json'), 'utf8'));
        expect(camJson.kind).toBe('webgl');
        expect(camJson.size).toEqual([640, 360]);
        expect(camJson.url).toBe('http://127.0.0.1:5173/?scene=2&freeze=1&t=7');
        expect(camJson.bodies).toHaveLength(1);
        expect(camJson.bodies[0].name).toBe('cislunarMoon');

        const consoleTxt = fs.readFileSync(path.join(outDir, 'webgl-console.txt'), 'utf8');
        expect(consoleTxt).toContain('[info] scene ready');
      } finally {
        await cdp.close();
        fs.rmSync(tmpRoot, { recursive: true, force: true });
      }
    },
  );
});

describe('cliOptions argument parsing, viewport bounds, and URL construction', () => {
  it('defines defaults and validates renderer kinds and WxH viewport dimensions', () => {
    expect(DEFAULT_CAPTURE_URL).toBe('http://localhost:5173');
    expect(DEFAULT_RENDERER).toBe('both');
    expect(DEFAULT_SCENE).toBe('0');
    expect(DEFAULT_TIME).toBe('4');
    expect(DEFAULT_SIZE).toBe('1920x1080');
    expect(USAGE_LINE).toBe(
      'usage: node scripts/capture_landing.mjs --out <dir> [--url ...] [--renderer webgl|webgpu|both]',
    );

    expect(resolveRendererKinds('both')).toEqual(['webgl', 'webgpu']);
    expect(resolveRendererKinds('webgl')).toEqual(['webgl']);
    expect(resolveRendererKinds('webgpu')).toEqual(['webgpu']);
    expect(() => resolveRendererKinds('canvas2d')).toThrow(/renderer/i);

    // Inside and outside viewport size bounds
    expect(parseViewportSize('1x1')).toEqual([1, 1]);
    expect(parseViewportSize('3840x2160')).toEqual([3840, 2160]);
    for (const invalid of ['0x1080', '1920x0', '-10x10', '1920', '1920x1080x2', 'abcxdef', '19.5x10']) {
      expect(() => parseViewportSize(invalid), invalid).toThrow(/size/i);
    }

    const parsed = parseCaptureArgs(['--out', '/tmp/cap']);
    expect(parsed).toEqual({
      out: '/tmp/cap',
      url: 'http://localhost:5173',
      renderer: 'both',
      kinds: ['webgl', 'webgpu'],
      scene: '0',
      t: '4',
      width: 1920,
      height: 1080,
    });
    expect(parseCaptureArgs([])).toBeNull();
  });

  it('builds deterministic capture URLs without double slashes and sets renderer=webgpu only for webgpu', () => {
    expect(
      buildCaptureTargetUrl({ baseUrl: 'http://localhost:5173/', scene: '0', t: '4', kind: 'webgl' }),
    ).toBe('http://localhost:5173/?scene=0&freeze=1&t=4');
    expect(
      buildCaptureTargetUrl({ baseUrl: 'http://localhost:5173', scene: '1', t: '9', kind: 'webgpu' }),
    ).toBe('http://localhost:5173/?scene=1&freeze=1&t=9&renderer=webgpu');
  });

  it('PBT: parseViewportSize round-trips positive integer pairs and buildCaptureTargetUrl preserves query parameters', () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 16_384 }),
        fc.integer({ min: 1, max: 16_384 }),
        fc.integer({ min: 0, max: 9 }),
        fc.integer({ min: 0, max: 120 }),
        fc.constantFrom('webgl', 'webgpu'),
        (w, h, sceneNum, tNum, kind) => {
          expect(parseViewportSize(`${w}x${h}`)).toEqual([w, h]);
          const targetUrl = new URL(
            buildCaptureTargetUrl({
              baseUrl: 'http://127.0.0.1:5173/',
              scene: String(sceneNum),
              t: String(tNum),
              kind,
            }),
          );
          expect(targetUrl.pathname).toBe('/');
          expect(targetUrl.searchParams.get('scene')).toBe(String(sceneNum));
          expect(targetUrl.searchParams.get('freeze')).toBe('1');
          expect(targetUrl.searchParams.get('t')).toBe(String(tNum));
          expect(targetUrl.searchParams.get('renderer')).toBe(kind === 'webgpu' ? 'webgpu' : null);
        },
      ),
      {
        seed,
        numRuns: 60,
        examples: [
          [1, 1, 0, 0, 'webgl'],
          [3840, 2160, 3, 12, 'webgpu'],
        ],
      },
    );
  });
});

describe('devtoolsClient JSON-RPC correlation, chunked transfer, and data URL decoding', () => {
  function makeMockWs() {
    const listeners = new Map();
    const sent = [];
    return {
      sent,
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
      },
      send(payload) {
        sent.push(JSON.parse(payload));
      },
      emitMessage(data) {
        for (const fn of listeners.get('message') ?? []) fn({ data });
      },
      emitClose() {
        for (const fn of listeners.get('close') ?? []) fn({});
      },
    };
  }

  it('correlates RPC ids, forwards sessionId, dispatches events, and rejects pending calls on error or close', async () => {
    expect(CHUNK_CHARS).toBe(1_000_000);
    expect(DEFAULT_EVALUATE_TIMEOUT_MS).toBe(120_000);

    const ws = makeMockWs();
    const dt = new DevTools(ws);
    const events = [];
    const off = dt.on((m) => events.push(m));

    const p1 = dt.send('Target.createTarget', { url: 'about:blank' });
    const p2 = dt.send('Page.enable', {}, 'sess-9');
    expect(ws.sent[0]).toEqual({ id: 1, method: 'Target.createTarget', params: { url: 'about:blank' } });
    expect(ws.sent[1]).toEqual({ id: 2, method: 'Page.enable', params: {}, sessionId: 'sess-9' });

    ws.emitMessage(Buffer.from(JSON.stringify({ method: 'Page.loadEventFired', sessionId: 'sess-9' })));
    expect(events).toHaveLength(1);
    off();
    ws.emitMessage(JSON.stringify({ method: 'Page.loadEventFired', sessionId: 'sess-9' }));
    expect(events).toHaveLength(1);

    ws.emitMessage(JSON.stringify({ id: 1, result: { targetId: 't-9' } }));
    await expect(p1).resolves.toEqual({ targetId: 't-9' });

    ws.emitMessage(JSON.stringify({ id: 2, error: { message: 'bad domain', data: 'extra' } }));
    await expect(p2).rejects.toThrow('bad domain extra');

    const p3 = dt.send('Runtime.evaluate', { expression: '1+1' }, 'sess-9');
    ws.emitClose();
    await expect(p3).rejects.toThrow(/closed/i);
  });

  it('evaluates expressions, surfaces page exceptionDetails, reassembles chunked data URLs, and writes PNG bytes', async () => {
    const fakeDevtools = {
      calls: [],
      async send(method, params, sessionId) {
        this.calls.push({ method, params, sessionId });
        if (params.expression === 'throw_err') {
          return { exceptionDetails: { text: 'ReferenceError' } };
        }
        const m = params.expression.match(/piece\((\d+),\s*(\d+)\)/);
        if (m) {
          return { result: { value: 'ABCDEFGHIJ'.slice(Number(m[1]), Number(m[2])) } };
        }
        return { result: { value: 42 } };
      },
    };

    await expect(evaluateInPage(fakeDevtools, 's1', '6*7')).resolves.toBe(42);
    await expect(evaluateInPage(fakeDevtools, 's1', 'throw_err')).rejects.toThrow(/page:.*ReferenceError/);

    const assembled = await fetchStoredDataUrl(fakeDevtools, 's1', 10, 4);
    expect(assembled).toBe('ABCDEFGHIJ');

    const badDevtools = {
      async send() {
        return { result: { value: 'short' } };
      },
    };
    await expect(fetchStoredDataUrl(badDevtools, 's1', 10, 10)).rejects.toThrow('frame arrived with 5 of 10 characters');

    const bytes = Buffer.from([137, 80, 78, 71, 1, 2, 3]);
    const url = `data:image/png;base64,${bytes.toString('base64')}`;
    expect(decodeDataUrlPayload(url)).toEqual(bytes);
    expect(() => decodeDataUrlPayload('not-a-data-url')).toThrow(/data URL/i);

    const written = [];
    writeDataUrl('/fake/out.png', url, {
      writeFileSync(f, b) {
        written.push([f, b]);
      },
    });
    expect(written).toEqual([['/fake/out.png', bytes]]);
  });

  it('PBT: fetchStoredDataUrl reassembles arbitrary payload strings across arbitrary positive chunk sizes', async () => {
    const seedEnv = Number(process.env.ALBEDIAN_FC_SEED);
    const seed = Number.isInteger(seedEnv) && seedEnv > 0 ? seedEnv : 20261117;
    await fc.assert(
      fc.asyncProperty(
        fc.string({ minLength: 0, maxLength: 250 }),
        fc.integer({ min: 1, max: 64 }),
        async (payload, chunkSize) => {
          const dt = {
            async send(_m, params) {
              const match = params.expression.match(/piece\((\d+),\s*(\d+)\)/);
              return { result: { value: payload.slice(Number(match[1]), Number(match[2])) } };
            },
          };
          const got = await fetchStoredDataUrl(dt, 's', payload.length, chunkSize);
          expect(got).toBe(payload);
        },
      ),
      {
        seed,
        numRuns: 50,
        examples: [
          ['', 10],
          ['exact8ch', 4],
          ['odd9chars', 4],
        ],
      },
    );
  });
});

describe('pageHelpers in-page Three.js / WebGPU capture and geometry extraction', () => {
  it('installs __THREE_DEVTOOLS__ prelude and deduplicated star-mount / body geometry helpers both directly and via script string', async () => {
    expect(STAR_MOUNT_NAMES).toEqual(['catalogStarField', 'catalogStarSprites']);
    expect(MAX_BODY_RADIUS).toBe(500);
    expect(PRELUDE_SCRIPT).toContain('__THREE_DEVTOOLS__');
    expect(HELPERS_SCRIPT).toContain('__capture');

    const win = {};
    new Function('window', PRELUDE_SCRIPT)(win);
    expect(win.__captureObserved).toEqual([]);
    win.__THREE_DEVTOOLS__.dispatchEvent(Object.assign(new Event('observe'), { detail: { id: 'scene-a' } }));
    expect(win.__captureObserved).toEqual([{ id: 'scene-a' }]);

    // Construct a mock Three.js scene tree exercising every branch of installCaptureHelpers
    const liveCanvas = { toDataURL: () => 'data:image/png;base64,QUJD' };
    const detachedCanvas = { toDataURL: () => 'detached' };
    const doc = { body: { contains: (el) => el === liveCanvas } };

    const camObj = {
      isPerspectiveCamera: true,
      fov: 26,
      aspect: 16 / 9,
      projectionMatrix: { elements: Array.from({ length: 16 }, (_, i) => i + 1) },
      matrixWorld: { elements: Array.from({ length: 16 }, (_, i) => (i % 5 === 0 ? 1 : 0)) },
    };

    const starMount = {
      name: 'catalogStarSprites',
      visible: true,
      parent: null,
      matrixWorld: { elements: Array(16).fill(2) },
    };
    const starChildMesh = {
      isMesh: true,
      name: 'starInstanced',
      visible: true,
      parent: starMount,
      geometry: { boundingSphere: { radius: 1, center: { clone: () => ({ applyMatrix4: () => ({ x: 0, y: 0, z: 0 }) }) } } },
      matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    };
    const skyDomeMesh = {
      isMesh: true,
      name: 'skyDome',
      visible: true,
      parent: null,
      geometry: { boundingSphere: { radius: 500.1, center: { clone: () => ({ applyMatrix4: () => ({ x: 0, y: 0, z: 0 }) }) } } },
      matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    };
    const exactBoundaryMesh = {
      isMesh: true,
      name: 'boundary500',
      visible: true,
      parent: null,
      geometry: { boundingSphere: { radius: 500, center: { clone: () => ({ applyMatrix4: () => ({ x: 0, y: 0, z: 0 }) }) } } },
      matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    };
    let computedSphere = false;
    const moonMesh = {
      isMesh: true,
      name: 'cislunarMoon',
      visible: true,
      parent: null,
      geometry: {
        type: 'SphereGeometry',
        boundingSphere: null,
        computeBoundingSphere() {
          computedSphere = true;
          this.boundingSphere = {
            radius: 2.5,
            center: { clone: () => ({ applyMatrix4: () => ({ x: 1, y: 2, z: -3 }) }) },
          };
        },
      },
      // Non-uniform scale: x=2, y=3, z=1 -> max scale = 3 -> world radius = 7.5 <= 500
      matrixWorld: { elements: [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 1, 2, -3, 1] },
    };
    const hiddenParent = { visible: false, parent: null };
    const hiddenMesh = {
      isMesh: true,
      name: 'hiddenBody',
      visible: true,
      parent: hiddenParent,
      geometry: { boundingSphere: { radius: 1, center: { clone: () => ({ applyMatrix4: () => ({ x: 0, y: 0, z: 0 }) }) } } },
      matrixWorld: { elements: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    };

    const nodes = [camObj, starMount, starChildMesh, skyDomeMesh, exactBoundaryMesh, moonMesh, hiddenMesh];
    const sceneObj = {
      isScene: true,
      updated: false,
      updateMatrixWorld(force) {
        this.updated = force;
      },
      traverse(cb) {
        for (const n of [this, ...nodes]) cb(n);
      },
    };
    for (const n of nodes) {
      if (n.parent === null) n.parent = sceneObj;
    }
    sceneObj.visible = true;
    sceneObj.parent = null;

    win.__captureObserved = [
      { isWebGLRenderer: true, domElement: detachedCanvas },
      { isWebGLRenderer: true, domElement: liveCanvas },
      sceneObj,
    ];

    let rafTicks = 0;
    const prevRaf = globalThis.requestAnimationFrame;
    globalThis.requestAnimationFrame = (cb) => {
      rafTicks++;
      cb();
    };
    try {
      const ok = new Function('window', 'document', `return ${HELPERS_SCRIPT}`)(win, doc);
      expect(ok).toBe(true);
      expect(installCapturePrelude).toBeTypeOf('function');
      expect(installCaptureHelpers).toBeTypeOf('function');

      expect(win.__capture.liveWebGl().domElement).toBe(liveCanvas);
      expect(win.__capture.scene()).toBe(sceneObj);
      expect(win.__capture.camera()).toBe(camObj);

      const stored = await win.__capture.store('webgl');
      expect(rafTicks).toBe(4);
      expect(stored.length).toBe('data:image/png;base64,QUJD'.length);
      expect(stored.sha256).toBe(crypto.createHash('sha256').update('data:image/png;base64,QUJD').digest('hex'));
      expect(win.__capture.piece(0, 10)).toBe('data:image');

      const geo = win.__capture.geometry('webgl');
      expect(sceneObj.updated).toBe(true);
      expect(computedSphere).toBe(true);
      expect(geo.kind).toBe('webgl');
      expect(geo.fov).toBe(26);
      expect(geo.starMountMatrixWorld).toEqual(Array(16).fill(2));
      expect(geo.bodies.map((b) => b.name)).toEqual(['boundary500', 'cislunarMoon']);
      expect(geo.bodies[1]).toEqual({
        name: 'cislunarMoon',
        center: [1, 2, -3],
        radius: 7.5,
        matrixWorld: [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1, 0, 1, 2, -3, 1],
      });

      expect(win.__capture.setStarsVisible(false)).toBe(1);
      expect(starMount.visible).toBe(false);
      expect(win.__capture.geometry('webgl').starMountMatrixWorld).toBeNull();
      expect(win.__capture.setStarsVisible(true)).toBe(1);
      expect(starMount.visible).toBe(true);
    } finally {
      globalThis.requestAnimationFrame = prevRaf;
    }
  });
});

describe('chromeSession and captureRunner lifecycle and error handling', () => {
  it('resolves Chrome paths, builds headless flags, parses DevTools WS URLs, and cleans up on error', async () => {
    expect(DEFAULT_CHROME_PATH).toBe('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
    expect(DEFAULT_DEVTOOLS_URL_TIMEOUT_MS).toBe(20_000);
    expect(resolveChromePath({})).toBe(DEFAULT_CHROME_PATH);
    expect(resolveChromePath({ CHROME_PATH: '/custom/chrome' })).toBe('/custom/chrome');

    const flags = buildChromeLaunchArgs({ profileDir: '/tmp/prof', width: 1280, height: 720 });
    expect(flags).toContain('--headless=new');
    expect(flags).toContain('--user-data-dir=/tmp/prof');
    expect(flags).toContain('--remote-debugging-port=0');
    expect(flags).toContain('--window-size=1280,720');
    expect(flags).toContain('--enable-unsafe-webgpu');
    expect(flags.at(-1)).toBe('about:blank');

    // Chunked stderr emission resolves ws URL
    const procOk = new EventEmitter();
    procOk.stderr = new EventEmitter();
    const wsPromise = waitForDevToolsWsUrl(procOk, 5000);
    procOk.stderr.emit('data', Buffer.from('DevTools listening on '));
    procOk.stderr.emit('data', Buffer.from('ws://127.0.0.1:9222/devtools/browser/abc\n'));
    await expect(wsPromise).resolves.toBe('ws://127.0.0.1:9222/devtools/browser/abc');

    // Early process exit rejects immediately
    const procExit = new EventEmitter();
    procExit.stderr = new EventEmitter();
    const exitPromise = waitForDevToolsWsUrl(procExit, 5000);
    procExit.emit('exit', 127);
    await expect(exitPromise).rejects.toThrow('Chrome exited with 127');

    // runWithHeadlessChrome kills Chrome and removes profile even if callback throws
    let killedWith = null;
    let removedDir = null;
    const fakeProc = new EventEmitter();
    fakeProc.stderr = new EventEmitter();
    fakeProc.kill = (sig) => {
      killedWith = sig;
    };
    class FakeWs {
      constructor(url) {
        this.url = url;
        this.closed = false;
      }
      addEventListener(type, fn) {
        if (type === 'open') queueMicrotask(() => fn());
      }
      send() {}
      close() {
        this.closed = true;
      }
    }

    await expect(
      runWithHeadlessChrome(
        {
          width: 800,
          height: 600,
          chromePath: '/bin/chrome',
          spawnImpl: () => {
            queueMicrotask(() =>
              fakeProc.stderr.emit('data', Buffer.from('DevTools listening on ws://127.0.0.1:9999/x\n')),
            );
            return fakeProc;
          },
          WebSocketImpl: FakeWs,
          fsImpl: {
            mkdtempSync: (prefix) => `${prefix}123`,
            rmSync: (dir) => {
              removedDir = dir;
            },
          },
          sleepImpl: async () => {},
          cleanupDelayMs: 0,
        },
        async () => {
          throw new Error('boom inside session');
        },
      ),
    ).rejects.toThrow('boom inside session');

    expect(killedWith).toBe('SIGTERM');
    expect(removedDir).toMatch(/albedian-capture-123$/);
  });

  it('runs webgpu captureRendererTarget, logs non-repeating frame fallback, and cleans up target in finally', async () => {
    expect(SETTLE_MS).toBe(3000);
    expect(PAGE_LOAD_TIMEOUT_MS).toBe(60_000);
    expect(NETWORK_IDLE_TIMEOUT_MS).toBe(120_000);
    expect(REPEAT_FRAME_ATTEMPTS).toBe(12);

    expect(
      formatConsoleEvent({
        method: 'Runtime.consoleAPICalled',
        params: { type: 'warn', args: [{ value: 'tile' }, { description: 'slow' }] },
      }),
    ).toBe('[warn] tile slow');
    expect(
      formatConsoleEvent({
        method: 'Runtime.exceptionThrown',
        params: { exceptionDetails: { text: 'TypeError' } },
      }),
    ).toContain('[exception]');

    const writtenFiles = new Map();
    const closedTargets = [];
    let storeCall = 0;
    const listeners = new Set();
    const fakeDevtools = {
      on(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
      },
      async send(method, params, sessionId) {
        if (method === 'Target.createTarget') return { targetId: 'tgt-gpu' };
        if (method === 'Target.attachToTarget') return { sessionId: 'ses-gpu' };
        if (method === 'Target.closeTarget') {
          closedTargets.push(params.targetId);
          return {};
        }
        if (method === 'Page.navigate') {
          for (const l of listeners) {
            l({ sessionId: 'ses-gpu', method: 'Page.loadEventFired', params: {} });
          }
          return {};
        }
        if (method === 'Runtime.evaluate') {
          const expr = params.expression;
          if (expr.includes('!window.__albedianCosmos')) return { result: { value: true } };
          if (expr.includes('whenSettled')) return { result: { value: true } };
          if (expr.includes('window.__albedianCosmos.backend')) return { result: { value: 'webgpu' } };
          if (expr.startsWith('window.__capture.store(')) {
            storeCall++;
            const payload = `data:image/png;base64,${Buffer.from(`frame-${storeCall}`).toString('base64')}`;
            fakeDevtools._lastPayload = payload;
            // Every call returns a distinct sha256 to exercise the 12-attempt non-repeating fallback
            return { result: { value: { length: payload.length, sha256: `sha-${storeCall}` } } };
          }
          if (expr.startsWith('window.__capture.piece(')) {
            return { result: { value: fakeDevtools._lastPayload } };
          }
          if (expr.startsWith('window.__capture.geometry(')) {
            return { result: { value: { kind: 'webgpu', fov: 26, aspect: 1.77, bodies: [] } } };
          }
          return { result: { value: 1 } };
        }
        return {};
      },
    };

    let tickNow = 1000;
    await captureRendererTarget(fakeDevtools, 'webgpu', {
      out: '/mem',
      url: 'http://127.0.0.1:5173',
      scene: '0',
      t: '4',
      width: 1920,
      height: 1080,
      settleMs: 0,
      repeatSleepMs: 0,
      nowImpl: () => (tickNow += 10),
      sleepImpl: async () => {},
      fsImpl: {
        writeFileSync(file, data) {
          writtenFiles.set(file, data);
        },
      },
      logImpl: () => {},
    });

    expect(closedTargets).toEqual(['tgt-gpu']);
    expect(listeners.size).toBe(0);
    expect(writtenFiles.has('/mem/webgpu-full.png')).toBe(true);
    expect(writtenFiles.has('/mem/webgpu-dome.png')).toBe(true);
    expect(writtenFiles.has('/mem/webgpu-camera.json')).toBe(true);
    const consoleLog = writtenFiles.get('/mem/webgpu-console.txt');
    expect(consoleLog).toContain('[capture] settled true, backend webgpu');
    expect(consoleLog).toContain('[capture] the frame never repeated exactly; kept the last');

    // runCaptureLanding orchestrates both renderers when --renderer both is passed
    const capturedKinds = [];
    await runCaptureLanding(['--out', '/mem2', '--renderer', 'both'], {
      fsImpl: { mkdirSync() {} },
      withBrowser: async (_opts, cb) => cb('fake-dt'),
      captureTarget: async (_dt, kind) => {
        capturedKinds.push(kind);
      },
    });
    expect(capturedKinds).toEqual(['webgl', 'webgpu']);
  });
});
