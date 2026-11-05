#!/usr/bin/env node
/**
 * Captures the landing scene's canvas, as drawn, in headless Chrome: the frame as shown, the same
 * frame with the catalog star sprites hidden (the sky dome alone under the bodies), and the
 * camera and scene geometry that scripts/check_sky_dome_on_screen.py needs.
 *
 * It uses only Node's built-ins (the DevTools protocol over Node's WebSocket) and an installed
 * Chrome, run headless with a throwaway profile that is deleted afterwards. Start the web app
 * first (`pnpm dev`), then:
 *
 *   node scripts/capture_landing.mjs --out <dir> [--url http://localhost:5173] [--renderer webgl|webgpu|both]
 *     [--scene 0] [--t 4] [--size 1920x1080]
 *
 * For each renderer it writes <dir>/<renderer>-full.png, <renderer>-dome.png, <renderer>-camera.json
 * and <renderer>-console.txt. CHROME_PATH overrides the browser (default: Google Chrome on macOS).
 * The scene clock is held at --t seconds and the carousel frozen, so captures of the same commit
 * match and captures of two commits can be compared pixel for pixel.
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    out: { type: 'string' },
    url: { type: 'string', default: 'http://localhost:5173' },
    renderer: { type: 'string', default: 'both' },
    scene: { type: 'string', default: '0' },
    t: { type: 'string', default: '4' },
    size: { type: 'string', default: '1920x1080' },
  },
});
if (!args.out) {
  console.error('usage: node scripts/capture_landing.mjs --out <dir> [--url ...] [--renderer webgl|webgpu|both]');
  process.exit(2);
}
const [width, height] = args.size.split('x').map(Number);
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const SETTLE_MS = 3000;
// DevTools replies of 3 MB arrive; replies of 4.7 MB and more never do (measured with Chrome 156
// and Node's WebSocket), and a busy 1080p frame is a 4.7 MB data URL. Frames stay in the page,
// are compared by SHA-256 there, and come over in pieces of this many characters.
const CHUNK_CHARS = 1_000_000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Runs before the app: three.js announces every Scene and WebGLRenderer to this devtools hook
const PRELUDE = `
  window.__captureObserved = [];
  window.__THREE_DEVTOOLS__ = new EventTarget();
  window.__THREE_DEVTOOLS__.addEventListener('observe', (e) => window.__captureObserved.push(e.detail));
`;

// In-page helpers. The WebGPU host publishes window.__albedianCosmos; the WebGL renderer is found
// through the devtools hook (React StrictMode builds it twice in development; the live one is the
// one whose canvas is in the page).
const HELPERS = `
  window.__capture = {
    liveWebGl() {
      return window.__captureObserved.filter((o) => o.isWebGLRenderer && document.body.contains(o.domElement)).pop();
    },
    scene() {
      if (window.__albedianCosmos) return window.__albedianCosmos.renderer.scene;
      let live = null;
      for (const o of window.__captureObserved) {
        if (!o.isScene) continue;
        let cameras = 0;
        o.traverse((c) => { if (c.isPerspectiveCamera) cameras++; });
        if (cameras > 0) live = o;
      }
      return live;
    },
    camera() {
      if (window.__albedianCosmos) return window.__albedianCosmos.renderer.camera;
      let cam = null;
      this.scene().traverse((c) => { if (c.isPerspectiveCamera && !cam) cam = c; });
      return cam;
    },
    shown(o) {
      for (let p = o; p; p = p.parent) if (!p.visible) return false;
      return true;
    },
    setStarsVisible(visible) {
      let n = 0;
      this.scene().traverse((o) => {
        if (o.name === 'catalogStarField' || o.name === 'catalogStarSprites') { o.visible = visible; n++; }
      });
      return n;
    },
    frames(n) {
      return new Promise((resolve) => {
        const step = () => (n-- <= 0 ? resolve() : requestAnimationFrame(step));
        requestAnimationFrame(step);
      });
    },
    async png(kind) {
      if (kind === 'webgpu') return window.__albedianCosmos.capturePngDataUrl();
      await this.frames(3);
      return this.liveWebGl().domElement.toDataURL('image/png');
    },
    // Keeps the frame in the page and returns its length and SHA-256
    async store(kind) {
      this.stored = await this.png(kind);
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(this.stored));
      const sha256 = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
      return { length: this.stored.length, sha256 };
    },
    piece(start, end) {
      return this.stored.slice(start, end);
    },
    geometry(kind) {
      const cam = this.camera();
      const scene = this.scene();
      scene.updateMatrixWorld(true);
      const out = {
        kind,
        fov: cam.fov,
        aspect: cam.aspect,
        projectionMatrix: Array.from(cam.projectionMatrix.elements),
        matrixWorld: Array.from(cam.matrixWorld.elements),
      };
      // The visible catalog star mount: its world matrix maps ICRS unit vectors into the scene
      let mount = null;
      scene.traverse((o) => {
        if ((o.name === 'catalogStarField' || o.name === 'catalogStarSprites') && !mount && this.shown(o)) mount = o;
      });
      out.starMountMatrixWorld = mount ? Array.from(mount.matrixWorld.elements) : null;
      // Visible meshes in front of the sky as world-space bounding spheres (sky dome, star sprites
      // and instanced helpers left out)
      out.bodies = [];
      scene.traverse((o) => {
        if (!o.isMesh || !this.shown(o) || !o.geometry || o.geometry.isInstancedBufferGeometry) return;
        for (let p = o; p; p = p.parent) if (p.name === 'catalogStarSprites' || p.name === 'catalogStarField') return;
        if (!o.geometry.boundingSphere) o.geometry.computeBoundingSphere();
        const sphere = o.geometry.boundingSphere;
        const e = o.matrixWorld.elements;
        const scale = Math.max(Math.hypot(e[0], e[1], e[2]), Math.hypot(e[4], e[5], e[6]), Math.hypot(e[8], e[9], e[10]));
        const radius = sphere.radius * scale;
        if (radius > 500) return;
        const c = sphere.center.clone().applyMatrix4(o.matrixWorld);
        out.bodies.push({ name: o.name || o.geometry.type, center: [c.x, c.y, c.z], radius, matrixWorld: Array.from(e) });
      });
      return out;
    },
  };
  true;
`;

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
  const res = await devtools.send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true, timeout: 120000 },
    sessionId
  );
  if (res.exceptionDetails) throw new Error(`page: ${JSON.stringify(res.exceptionDetails).slice(0, 1200)}`);
  return res.result.value;
}

/** The frame the page stored last, brought over in pieces. */
async function fetchStored(devtools, sessionId, length) {
  let dataUrl = '';
  for (let start = 0; start < length; start += CHUNK_CHARS) {
    dataUrl += await evaluate(devtools, sessionId, `window.__capture.piece(${start}, ${start + CHUNK_CHARS})`);
  }
  if (dataUrl.length !== length) throw new Error(`frame arrived with ${dataUrl.length} of ${length} characters`);
  return dataUrl;
}

function writeDataUrl(file, dataUrl) {
  fs.writeFileSync(file, Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64'));
}

async function capture(devtools, kind) {
  const params = new URLSearchParams({ scene: args.scene, freeze: '1', t: args.t });
  if (kind === 'webgpu') params.set('renderer', 'webgpu');
  const url = `${args.url}/?${params}`;
  const { targetId } = await devtools.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await devtools.send('Target.attachToTarget', { targetId, flatten: true });
  const consoleLines = [];
  const inflight = new Set();
  let lastNetwork = Date.now();
  let loaded = false;
  const off = devtools.on((msg) => {
    if (msg.sessionId !== sessionId) return;
    if (msg.method === 'Runtime.consoleAPICalled') {
      consoleLines.push(`[${msg.params.type}] ${msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`);
    } else if (msg.method === 'Runtime.exceptionThrown') {
      consoleLines.push(`[exception] ${JSON.stringify(msg.params.exceptionDetails).slice(0, 800)}`);
    } else if (msg.method === 'Network.requestWillBeSent') {
      inflight.add(msg.params.requestId);
      lastNetwork = Date.now();
    } else if (msg.method === 'Network.loadingFinished' || msg.method === 'Network.loadingFailed') {
      inflight.delete(msg.params.requestId);
      lastNetwork = Date.now();
    } else if (msg.method === 'Page.loadEventFired') {
      loaded = true;
    }
  });
  try {
    for (const domain of ['Page', 'Runtime', 'Network']) await devtools.send(`${domain}.enable`, {}, sessionId);
    await devtools.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
    await devtools.send('Page.addScriptToEvaluateOnNewDocument', { source: PRELUDE }, sessionId);
    await devtools.send('Page.navigate', { url }, sessionId);

    const start = Date.now();
    while (!loaded && Date.now() - start < 60000) await sleep(100);
    // Textures and catalog tiles: wait until the network has been idle for SETTLE_MS
    while (Date.now() - start < 120000 && !(inflight.size === 0 && Date.now() - lastNetwork > SETTLE_MS)) await sleep(200);
    await evaluate(devtools, sessionId, HELPERS);
    if (kind === 'webgpu') {
      const published = await evaluate(
        devtools,
        sessionId,
        `(async () => { for (let i = 0; i < 600 && !window.__albedianCosmos; i++) await new Promise((r) => setTimeout(r, 100)); return !!window.__albedianCosmos; })()`
      );
      if (!published) throw new Error('the WebGPU host never published window.__albedianCosmos');
      const settled = await evaluate(devtools, sessionId, `window.__albedianCosmos.ready.then(() => window.__albedianCosmos.whenSettled(60000))`);
      consoleLines.push(`[capture] settled ${settled}, backend ${await evaluate(devtools, sessionId, 'String(window.__albedianCosmos.backend)')}`);
    }
    // The clock is held, so a settled frame repeats exactly; keep capturing until two match
    let previous = null;
    for (let i = 0; i < 12; i++) {
      const frame = await evaluate(devtools, sessionId, `window.__capture.store('${kind}')`);
      if (previous && frame.sha256 === previous.sha256) break;
      previous = frame;
      if (i === 11) consoleLines.push('[capture] the frame never repeated exactly; kept the last');
      await sleep(1000);
    }
    writeDataUrl(path.join(args.out, `${kind}-full.png`), await fetchStored(devtools, sessionId, previous.length));
    const geometry = await evaluate(devtools, sessionId, `window.__capture.geometry('${kind}')`);
    await evaluate(devtools, sessionId, 'window.__capture.setStarsVisible(false)');
    const dome = await evaluate(devtools, sessionId, `window.__capture.store('${kind}')`);
    writeDataUrl(path.join(args.out, `${kind}-dome.png`), await fetchStored(devtools, sessionId, dome.length));
    await evaluate(devtools, sessionId, 'window.__capture.setStarsVisible(true)');
    fs.writeFileSync(
      path.join(args.out, `${kind}-camera.json`),
      JSON.stringify({ ...geometry, url, size: [width, height], capturedAt: new Date().toISOString() }, null, 2) + '\n'
    );
  } finally {
    fs.writeFileSync(path.join(args.out, `${kind}-console.txt`), consoleLines.join('\n') + '\n');
    off();
    await devtools.send('Target.closeTarget', { targetId });
  }
  console.log(`${kind}: ${url} captured into ${args.out}`);
}

fs.mkdirSync(args.out, { recursive: true });
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-capture-'));
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--user-data-dir=${profile}`,
    '--remote-debugging-port=0',
    `--window-size=${width},${height}`,
    '--force-device-scale-factor=1',
    '--hide-scrollbars',
    '--enable-unsafe-webgpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--mute-audio',
    '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] }
);
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
  const kinds = args.renderer === 'both' ? ['webgl', 'webgpu'] : [args.renderer];
  for (const kind of kinds) await capture(devtools, kind);
  ws.close();
} finally {
  chrome.kill('SIGTERM');
  await sleep(500);
  fs.rmSync(profile, { recursive: true, force: true });
}
