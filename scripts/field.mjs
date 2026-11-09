#!/usr/bin/env node
// `pnpm field`: starts the API and the web app on this laptop for use away from home, with no internet needed.
//
// By default both answer only this machine: the API on 127.0.0.1:8000 and the web app's dev server on localhost:5173.
// `pnpm field --lan` is the opt-in for a phone on the same network. It builds the web app and serves that build
// with `vite preview` on every interface, never the dev server, which hands out any file in the repository by its
// /@fs/ path. The API stays on 127.0.0.1 behind the web app's /api proxy, which refuses media discover to any client
// that is not this machine (scripts/field-proxy-guard.mjs), and the API accepts exactly one more origin, this
// laptop's private network address on the web port.
//
// In both modes it also starts the music sources server (apps/web/spike/music/serveSources.mjs) on 127.0.0.1, and the
// web app's /music-sources/ proxy hands its samples to the laptop and the phone from the app's own origin.
// See docs/FIELD_USE.md.

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { fieldCheck, formatReport, realProbes } from './field-check.mjs';

export const API_PORT = 8000;
export const WEB_PORT = 5173;
/** serveSources.mjs's own default port */
export const MUSIC_PORT = 5199;
/** Where the app fetches the music from: a path on its own origin, which apps/web/vite.config.ts proxies */
export const MUSIC_BASE = '/music-sources/';
const MUSIC_SERVER = 'apps/web/spike/music/serveSources.mjs';
const MUSIC_SOURCES_DIR = '.cache/music/sources';
export const DEFAULT_ORIGINS = Object.freeze([`http://localhost:${WEB_PORT}`, `http://127.0.0.1:${WEB_PORT}`]);
const API_HOST = '127.0.0.1';
const HEALTH_WAIT_MS = 90_000;
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const USAGE = `Usage: pnpm field [--check] [--lan] [--api-port <port>] [--web-port <port>] [--music-port <port>]

Starts the Albedian API (http://${API_HOST}:${API_PORT}) and web app (http://localhost:${WEB_PORT}) on this laptop.
  --lan              build the web app and also serve that build to a phone on the same network at this
                     laptop's network address. Any device on that network can then open it, upload, solve
                     and send voice commands. Use it only on a network you trust. Off unless given.
  --check            start nothing: report what a field run needs on this laptop and what is missing.
  --api-port <port>  API port (default ${API_PORT}); --web-port <port> web app port (default ${WEB_PORT});
  --music-port <port> music sources server port on 127.0.0.1 (default ${MUSIC_PORT}).
Stop all three with Ctrl+C.`;

function parsePort(flag, value) {
  const port = Number(value);
  if (!/^\d+$/.test(String(value)) || port < 1024 || port > 65535) {
    throw new Error(`${flag} needs a port from 1024 to 65535, not ${value ?? 'nothing'}.`);
  }
  return port;
}

/** Parses the command line: only --lan, --check, --api-port, --web-port, --music-port and --help are accepted. */
export function parseFieldArgs(argv) {
  const options = { lan: false, help: false, check: false, apiPort: API_PORT, webPort: WEB_PORT, musicPort: MUSIC_PORT };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--lan') options.lan = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else if (arg === '--check') options.check = true;
    else if (arg === '--api-port') options.apiPort = parsePort(arg, argv[++i]);
    else if (arg === '--web-port') options.webPort = parsePort(arg, argv[++i]);
    else if (arg === '--music-port') options.musicPort = parsePort(arg, argv[++i]);
    else throw new Error(`Unknown option ${arg}. ${USAGE}`);
  }
  return options;
}

/** True for an IPv4 address in 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16. */
export function isPrivateIPv4(address) {
  const parts = String(address).split('.').map(Number);
  if (parts.length !== 4 || parts.some((p) => !Number.isInteger(p) || p < 0 || p > 255)) return false;
  const [a, b] = parts;
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/** The first private IPv4 address among the interfaces, or null. Loopback, link-local and IPv6 are skipped. */
export function pickLanAddress(interfaces = os.networkInterfaces()) {
  for (const entries of Object.values(interfaces)) {
    for (const entry of entries ?? []) {
      const ipv4 = entry.family === 'IPv4' || entry.family === 4;
      if (ipv4 && !entry.internal && isPrivateIPv4(entry.address)) return entry.address;
    }
  }
  return null;
}

/** The two commands to run, their environments and the URLs to print. Starts nothing. */
export function fieldPlan({ lan, lanAddress = null, env, apiPort = API_PORT, webPort = WEB_PORT, musicPort = MUSIC_PORT }) {
  if (lan && !lanAddress) {
    throw new Error('--lan found no private network address on this laptop. Join the same Wi-Fi or hotspot as the phone first.');
  }
  if (lan && !isPrivateIPv4(lanAddress)) {
    throw new Error(`--lan: ${lanAddress} is not a private network address; refusing to open the app on it.`);
  }
  const phoneUrl = lan ? `http://${lanAddress}:${webPort}` : null;
  const local = [`http://localhost:${webPort}`, `http://127.0.0.1:${webPort}`];
  const origins = lan ? [...local, phoneUrl] : local;

  // The shell's own origin and host lists never reach the API here: a wildcard set there must not open it.
  const apiEnv = { ...env, ALBEDIAN_CORS_ORIGINS: origins.join(',') };
  delete apiEnv.ALBEDIAN_TRUSTED_HOSTS;

  return {
    // Under --lan the network gets the built app alone: the dev server would hand out any repository file by /@fs/ path
    // Vite bakes VITE_ values into the build, so the music base for the --lan build is set here
    build: lan
      ? { command: 'pnpm', args: ['--filter', '@albedian/web', 'build'], env: { ...env, VITE_ALBEDIAN_MUSIC_SOURCES: MUSIC_BASE } }
      : null,
    api: {
      command: 'uv',
      // No access log: it prints each request line with its query string, so a GET route that took a capture time
      // or place there, now or later, would put it on the screen
      args: ['run', 'uvicorn', 'services.api:app', '--host', API_HOST, '--port', String(apiPort), '--no-access-log'],
      env: apiEnv,
    },
    web: {
      command: 'pnpm',
      args: [
        '--filter',
        '@albedian/web',
        'exec',
        'vite',
        ...(lan ? ['preview'] : []),
        '--host',
        lan ? '0.0.0.0' : 'localhost',
        '--port',
        String(webPort),
        '--strictPort',
      ],
      // The dev server and preview both read the proxy targets from here (apps/web/vite.config.ts)
      env: {
        ...env,
        ALBEDIAN_API_TARGET: `http://${API_HOST}:${apiPort}`,
        ALBEDIAN_MUSIC_TARGET: `http://${API_HOST}:${musicPort}`,
        VITE_ALBEDIAN_MUSIC_SOURCES: MUSIC_BASE,
      },
    },
    // serveSources.mjs binds 127.0.0.1 itself; only the web app's /music-sources/ proxy reaches it
    music: { command: process.execPath, args: [MUSIC_SERVER, '--port', String(musicPort)], env: { ...env } },
    healthUrl: `http://${API_HOST}:${apiPort}/api/health`,
    laptopUrl: `http://localhost:${webPort}`,
    phoneUrl,
  };
}

function portIsFree(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen({ port, host, exclusive: true }, () => server.close(() => resolve(true)));
  });
}

async function waitForHealth(healthUrl, deadline, isStopped) {
  while (Date.now() < deadline && !isStopped()) {
    try {
      const res = await fetch(healthUrl, { signal: AbortSignal.timeout(2_000) });
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

/** How many sample files the music has, by count only: their names are not printed. */
export function musicSourcesLine(sourcesDir) {
  if (!fs.existsSync(sourcesDir)) {
    return `Music: ${MUSIC_SOURCES_DIR} is missing, so the music stays silent (docs/FIELD_USE.md says how to fetch it).`;
  }
  const files = fs.readdirSync(sourcesDir, { recursive: true, withFileTypes: true }).filter((entry) => entry.isFile());
  return `Music: ${files.length} sample files in ${MUSIC_SOURCES_DIR}, served at ${MUSIC_BASE}.`;
}

async function main() {
  let options;
  try {
    options = parseFieldArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    process.exit(2);
  }
  if (options.help) {
    console.log(USAGE);
    return;
  }
  if (options.check) {
    const ports = { api: options.apiPort, web: options.webPort, music: options.musicPort };
    const result = await fieldCheck({ probes: realProbes(REPO_ROOT), ports, lan: options.lan });
    console.log(formatReport(result));
    process.exit(result.exitCode);
  }

  let plan;
  try {
    plan = fieldPlan({
      lan: options.lan,
      lanAddress: options.lan ? pickLanAddress() : null,
      env: process.env,
      apiPort: options.apiPort,
      webPort: options.webPort,
      musicPort: options.musicPort,
    });
  } catch (error) {
    console.error(error.message);
    process.exit(2);
  }

  for (const [port, host, what] of [
    [options.apiPort, API_HOST, 'the API'],
    [options.webPort, options.lan ? '0.0.0.0' : 'localhost', 'the web app'],
    [options.musicPort, API_HOST, 'the music sources server'],
  ]) {
    if (!(await portIsFree(port, host))) {
      console.error(`Port ${port} is already in use, so ${what} cannot start. Stop whatever is using it and run pnpm field again.`);
      process.exit(1);
    }
  }

  if (plan.build) {
    console.log('Building the web app for --lan; only this build is served to the network.');
    const started = Date.now();
    const result = spawnSync(plan.build.command, plan.build.args, { cwd: REPO_ROOT, env: plan.build.env, stdio: 'inherit' });
    if (result.error || result.status !== 0) {
      const why = result.error?.message ?? result.signal ?? `exit code ${result.status}`;
      console.error(`The web app build failed (${why}), so --lan started nothing. Fix the build, or run pnpm field without --lan.`);
      process.exit(1);
    }
    console.log(`Built in ${Math.round((Date.now() - started) / 1000)} s.`);
  }

  const children = [];
  let stopping = false;
  const stopAll = (signal = 'SIGTERM') => {
    stopping = true;
    for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill(signal);
  };

  for (const [name, step] of [
    ['API', plan.api],
    ['music sources server', plan.music],
    ['web app', plan.web],
  ]) {
    const child = spawn(step.command, step.args, { cwd: REPO_ROOT, env: step.env, stdio: 'inherit' });
    child.on('error', (error) => {
      console.error(`Could not start the ${name} (${step.command}): ${error.message}`);
      stopAll();
      process.exitCode = 1;
    });
    child.on('exit', (code, signal) => {
      if (!stopping) {
        console.error(`The ${name} stopped (${signal ?? `exit code ${code}`}); stopping the rest.`);
        process.exitCode = code ?? 1;
        stopAll();
      }
    });
    children.push(child);
  }

  process.on('SIGINT', () => stopAll('SIGINT'));
  process.on('SIGTERM', () => stopAll('SIGTERM'));

  if (!(await waitForHealth(plan.healthUrl, Date.now() + HEALTH_WAIT_MS, () => stopping))) {
    if (!stopping) {
      console.error(`The API did not answer ${plan.healthUrl} within ${HEALTH_WAIT_MS / 1000} s; stopping.`);
      process.exitCode = 1;
      stopAll();
    }
    return;
  }

  console.log(`\nAlbedian is ready. On this laptop (full experience): ${plan.laptopUrl}`);
  console.log(musicSourcesLine(path.join(REPO_ROOT, MUSIC_SOURCES_DIR)));
  if (plan.phoneUrl) {
    console.log(`Phone on the same network: ${plan.phoneUrl}`);
    console.log('Over plain http the phone gets upload, solve and the sky, but no WebGPU views, microphone or speech.');
    console.log('Any device on this network can open it, upload, solve and send voice commands; stop it when you are done.');
  }
  console.log('Stop all three with Ctrl+C.\n');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
