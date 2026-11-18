#!/usr/bin/env node
/**
 * Captures the landing scene's canvas, as drawn, in headless Chrome: the frame as shown, the same
 * frame with the catalog star sprites hidden (the sky dome alone under the bodies), and the
 * camera and scene geometry that scripts/check_sky_dome_on_screen.py needs.
 *
 * Usage:
 *   node scripts/capture_landing.mjs --out <dir> [--url http://localhost:5173] [--renderer webgl|webgpu|both]
 *     [--scene 0] [--t 4] [--size 1920x1080]
 */

import { pathToFileURL } from 'node:url';
import {
  NETWORK_IDLE_TIMEOUT_MS,
  PAGE_LOAD_TIMEOUT_MS,
  REPEAT_FRAME_ATTEMPTS,
  SETTLE_MS,
  captureRendererTarget,
  formatConsoleEvent,
  runCaptureLanding,
} from './capture_landing/captureRunner.mjs';
import {
  DEFAULT_CHROME_PATH,
  DEFAULT_DEVTOOLS_URL_TIMEOUT_MS,
  buildChromeLaunchArgs,
  resolveChromePath,
  runWithHeadlessChrome,
  waitForDevToolsWsUrl,
} from './capture_landing/chromeSession.mjs';
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
} from './capture_landing/cliOptions.mjs';
import {
  CHUNK_CHARS,
  DEFAULT_EVALUATE_TIMEOUT_MS,
  DevTools,
  decodeDataUrlPayload,
  evaluateInPage,
  fetchStoredDataUrl,
  writeDataUrl,
} from './capture_landing/devtoolsClient.mjs';
import {
  HELPERS_SCRIPT,
  MAX_BODY_RADIUS,
  PRELUDE_SCRIPT,
  STAR_MOUNT_NAMES,
  installCaptureHelpers,
  installCapturePrelude,
} from './capture_landing/pageHelpers.mjs';

export {
  CHUNK_CHARS,
  DEFAULT_CAPTURE_URL,
  DEFAULT_CHROME_PATH,
  DEFAULT_DEVTOOLS_URL_TIMEOUT_MS,
  DEFAULT_EVALUATE_TIMEOUT_MS,
  DEFAULT_RENDERER,
  DEFAULT_SCENE,
  DEFAULT_SIZE,
  DEFAULT_TIME,
  DevTools,
  HELPERS_SCRIPT,
  MAX_BODY_RADIUS,
  NETWORK_IDLE_TIMEOUT_MS,
  PAGE_LOAD_TIMEOUT_MS,
  PRELUDE_SCRIPT,
  REPEAT_FRAME_ATTEMPTS,
  SETTLE_MS,
  STAR_MOUNT_NAMES,
  USAGE_LINE,
  buildCaptureTargetUrl,
  buildChromeLaunchArgs,
  captureRendererTarget,
  decodeDataUrlPayload,
  evaluateInPage,
  fetchStoredDataUrl,
  formatConsoleEvent,
  installCaptureHelpers,
  installCapturePrelude,
  parseCaptureArgs,
  parseViewportSize,
  resolveChromePath,
  resolveRendererKinds,
  runCaptureLanding,
  runWithHeadlessChrome,
  waitForDevToolsWsUrl,
  writeDataUrl,
};

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await runCaptureLanding(process.argv.slice(2));
  if (result === null) {
    console.error(USAGE_LINE);
    process.exit(2);
  }
}
