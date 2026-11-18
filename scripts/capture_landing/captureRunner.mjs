import fs from 'node:fs';
import path from 'node:path';
import { runWithHeadlessChrome } from './chromeSession.mjs';
import { buildCaptureTargetUrl, parseCaptureArgs } from './cliOptions.mjs';
import { evaluateInPage, fetchStoredDataUrl, writeDataUrl } from './devtoolsClient.mjs';
import { HELPERS_SCRIPT, PRELUDE_SCRIPT } from './pageHelpers.mjs';

export const SETTLE_MS = 3000;
export const PAGE_LOAD_TIMEOUT_MS = 60_000;
export const NETWORK_IDLE_TIMEOUT_MS = 120_000;
export const REPEAT_FRAME_ATTEMPTS = 12;

const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function formatConsoleEvent(msg) {
  if (msg.method === 'Runtime.consoleAPICalled') {
    const rendered = msg.params.args.map((a) => a.value ?? a.description ?? '').join(' ');
    return `[${msg.params.type}] ${rendered}`;
  }
  if (msg.method === 'Runtime.exceptionThrown') {
    return `[exception] ${JSON.stringify(msg.params.exceptionDetails).slice(0, 800)}`;
  }
  return null;
}

export async function captureRendererTarget(
  devtools,
  kind,
  {
    out,
    url: baseUrl,
    scene,
    t,
    width,
    height,
    settleMs = SETTLE_MS,
    pageLoadTimeoutMs = PAGE_LOAD_TIMEOUT_MS,
    networkIdleTimeoutMs = NETWORK_IDLE_TIMEOUT_MS,
    repeatFrameAttempts = REPEAT_FRAME_ATTEMPTS,
    repeatSleepMs = 1000,
    nowImpl = () => Date.now(),
    sleepImpl = defaultSleep,
    fsImpl = fs,
    logImpl = console.log,
  },
) {
  const url = buildCaptureTargetUrl({ baseUrl, scene, t, kind });
  const { targetId } = await devtools.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await devtools.send('Target.attachToTarget', { targetId, flatten: true });
  const consoleLines = [];
  const inflight = new Set();
  let lastNetwork = nowImpl();
  let loaded = false;

  const off = devtools.on((msg) => {
    if (msg.sessionId !== sessionId) return;
    const formatted = formatConsoleEvent(msg);
    if (formatted !== null) {
      consoleLines.push(formatted);
    } else if (msg.method === 'Network.requestWillBeSent') {
      inflight.add(msg.params.requestId);
      lastNetwork = nowImpl();
    } else if (msg.method === 'Network.loadingFinished' || msg.method === 'Network.loadingFailed') {
      inflight.delete(msg.params.requestId);
      lastNetwork = nowImpl();
    } else if (msg.method === 'Page.loadEventFired') {
      loaded = true;
    }
  });

  try {
    for (const domain of ['Page', 'Runtime', 'Network']) {
      await devtools.send(`${domain}.enable`, {}, sessionId);
    }
    await devtools.send(
      'Emulation.setDeviceMetricsOverride',
      { width, height, deviceScaleFactor: 1, mobile: false },
      sessionId,
    );
    await devtools.send('Page.addScriptToEvaluateOnNewDocument', { source: PRELUDE_SCRIPT }, sessionId);
    await devtools.send('Page.navigate', { url }, sessionId);

    const start = nowImpl();
    while (!loaded && nowImpl() - start < pageLoadTimeoutMs) {
      await sleepImpl(100);
    }
    while (
      nowImpl() - start < networkIdleTimeoutMs &&
      !(inflight.size === 0 && nowImpl() - lastNetwork > settleMs)
    ) {
      await sleepImpl(200);
    }

    await evaluateInPage(devtools, sessionId, HELPERS_SCRIPT);
    if (kind === 'webgpu') {
      const published = await evaluateInPage(
        devtools,
        sessionId,
        `(async () => { for (let i = 0; i < 600 && !window.__albedianCosmos; i++) await new Promise((r) => setTimeout(r, 100)); return !!window.__albedianCosmos; })()`,
      );
      if (!published) throw new Error('the WebGPU host never published window.__albedianCosmos');
      const settled = await evaluateInPage(
        devtools,
        sessionId,
        `window.__albedianCosmos.ready.then(() => window.__albedianCosmos.whenSettled(60000))`,
      );
      const backend = await evaluateInPage(devtools, sessionId, 'String(window.__albedianCosmos.backend)');
      consoleLines.push(`[capture] settled ${settled}, backend ${backend}`);
    }

    let previous = null;
    for (let i = 0; i < repeatFrameAttempts; i++) {
      const frame = await evaluateInPage(devtools, sessionId, `window.__capture.store('${kind}')`);
      if (previous && frame.sha256 === previous.sha256) break;
      previous = frame;
      if (i === repeatFrameAttempts - 1) {
        consoleLines.push('[capture] the frame never repeated exactly; kept the last');
      }
      await sleepImpl(repeatSleepMs);
    }

    writeDataUrl(
      path.join(out, `${kind}-full.png`),
      await fetchStoredDataUrl(devtools, sessionId, previous.length),
      fsImpl,
    );
    const geometry = await evaluateInPage(devtools, sessionId, `window.__capture.geometry('${kind}')`);
    await evaluateInPage(devtools, sessionId, 'window.__capture.setStarsVisible(false)');
    const dome = await evaluateInPage(devtools, sessionId, `window.__capture.store('${kind}')`);
    writeDataUrl(
      path.join(out, `${kind}-dome.png`),
      await fetchStoredDataUrl(devtools, sessionId, dome.length),
      fsImpl,
    );
    await evaluateInPage(devtools, sessionId, 'window.__capture.setStarsVisible(true)');
    fsImpl.writeFileSync(
      path.join(out, `${kind}-camera.json`),
      `${JSON.stringify({ ...geometry, url, size: [width, height], capturedAt: new Date().toISOString() }, null, 2)}\n`,
    );
  } finally {
    fsImpl.writeFileSync(path.join(out, `${kind}-console.txt`), `${consoleLines.join('\n')}\n`);
    off();
    await devtools.send('Target.closeTarget', { targetId });
  }
  logImpl(`${kind}: ${url} captured into ${out}`);
}

export async function runCaptureLanding(
  argv = process.argv.slice(2),
  {
    fsImpl = fs,
    withBrowser = runWithHeadlessChrome,
    captureTarget = captureRendererTarget,
    browserOptions = {},
    captureOptions = {},
  } = {},
) {
  const parsed = parseCaptureArgs(argv);
  if (!parsed) return null;
  fsImpl.mkdirSync(parsed.out, { recursive: true });
  await withBrowser({ width: parsed.width, height: parsed.height, ...browserOptions }, async (devtools) => {
    for (const kind of parsed.kinds) {
      await captureTarget(devtools, kind, { ...parsed, ...captureOptions });
    }
  });
  return parsed;
}
