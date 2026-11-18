import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DevTools } from './devtoolsClient.mjs';

export const DEFAULT_CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
export const DEFAULT_DEVTOOLS_URL_TIMEOUT_MS = 20_000;

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function resolveChromePath(env = process.env) {
  return env.CHROME_PATH ?? DEFAULT_CHROME_PATH;
}

/** Builds headless Chrome flags using a throwaway `--user-data-dir` profile. */
export function buildChromeLaunchArgs({ profileDir, width, height }) {
  return [
    '--headless=new',
    `--user-data-dir=${profileDir}`,
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
  ];
}

/** Waits for headless Chrome's stderr to report its `ws://` DevTools listening endpoint. */
export function waitForDevToolsWsUrl(chromeProc, timeoutMs = DEFAULT_DEVTOOLS_URL_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    let stderr = '';
    const timer = setTimeout(() => {
      reject(new Error('Chrome did not report a DevTools URL'));
    }, timeoutMs);
    chromeProc.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
      const match = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    chromeProc.on('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`Chrome exited with ${code}`));
    });
  });
}

/** Spawns headless Chrome, connects a `DevTools` client, and cleans up the process + temp profile. */
export async function runWithHeadlessChrome(
  {
    width,
    height,
    chromePath = resolveChromePath(),
    spawnImpl = spawn,
    WebSocketImpl = WebSocket,
    fsImpl = fs,
    tmpRoot = os.tmpdir(),
    cleanupDelayMs = 500,
    sleepImpl = defaultSleep,
    wsUrlTimeoutMs = DEFAULT_DEVTOOLS_URL_TIMEOUT_MS,
  },
  callback,
) {
  const profile = fsImpl.mkdtempSync(path.join(tmpRoot, 'albedian-capture-'));
  const chrome = spawnImpl(chromePath, buildChromeLaunchArgs({ profileDir: profile, width, height }), {
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let ws = null;
  try {
    const wsUrl = await waitForDevToolsWsUrl(chrome, wsUrlTimeoutMs);
    ws = new WebSocketImpl(wsUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    const devtools = new DevTools(ws);
    await callback(devtools);
    ws.close();
  } finally {
    if (ws && typeof ws.close === 'function') {
      try {
        ws.close();
      } catch {
        // Ignore duplicate close errors
      }
    }
    chrome.kill('SIGTERM');
    await sleepImpl(cleanupDelayMs);
    fsImpl.rmSync(profile, { recursive: true, force: true });
  }
}
