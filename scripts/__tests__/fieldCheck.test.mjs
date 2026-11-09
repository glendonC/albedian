// pnpm field --check: what a trip run needs and is missing, read through fake probes so nothing real is touched.
// The report holds counts and versions only: never a file name, never a path under data/, never a key.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { DISK_ADVICE_GB, HEIC_ADVICE, fieldCheck, formatReport, realProbes } from '../field-check.mjs';

const MUSIC_MANIFEST = 'apps/web/spike/music/sources.json';
const SOURCES = '.cache/music/sources/';

function fakeProbes(over = {}) {
  const commands = {
    'pnpm --version': { ok: true, stdout: '10.7.1\n' },
    'uv --version': { ok: true, stdout: 'uv 0.12.5 (Homebrew 2026-08-14 aarch64-apple-darwin)\n' },
    'ffmpeg -hide_banner -version': { ok: true, stdout: 'ffmpeg version 8.1.1 Copyright (c) 2000-2026 the FFmpeg developers\nbuilt with clang\n' },
    'ffprobe -hide_banner -version': { ok: true, stdout: 'ffprobe version 8.1.1 Copyright (c) 2007-2026 the FFmpeg developers\n' },
    'ffprobe -hide_banner -h': { ok: true, stdout: '-show_streams  show streams info\n-show_stream_groups  show stream groups info\n' },
    'git ls-files -z apps/web/public/catalog': {
      ok: true,
      stdout: 'apps/web/public/catalog/stars/gaia-dr3/L0/0.core.bin\0apps/web/public/catalog/deepsky/openngc.json\0',
    },
    ...over.commands,
  };
  const music = over.music ?? [
    { path: 'vcsl/Chimes/C#4.wav', bytes: 10 },
    { path: 'scottbuckley/sb_signaltonoise_nomelody.mp3', bytes: 20 },
  ];
  const files = {
    'apps/web/public/catalog/stars/gaia-dr3/L0/0.core.bin': 5,
    'apps/web/public/catalog/deepsky/openngc.json': 7,
    ...Object.fromEntries(music.map((file) => [SOURCES + file.path, file.bytes])),
    ...over.files,
  };
  for (const rel of over.absent ?? []) delete files[rel];
  const links = new Set(over.links ?? []);
  const dirs = new Set(['node_modules', 'apps/web/node_modules', 'packages/contracts/node_modules', '.venv', ...(over.dirs ?? [])]);
  for (const rel of over.absentDirs ?? []) dirs.delete(rel);
  const busy = new Set(over.busyPorts ?? []);
  const portCalls = [];
  return {
    portCalls,
    nodeVersion: over.nodeVersion ?? 'v24.15.0',
    env: over.env ?? {},
    run: (command, args) => commands[[command, ...args].join(' ')] ?? { ok: false, stdout: '' },
    readJson: (rel) =>
      ({
        'package.json': { engines: { node: '>=22.0.0' }, packageManager: 'pnpm@10.7.1' },
        [MUSIC_MANIFEST]: { files: music },
      })[rel] ?? null,
    fileSize: (rel) => (links.has(rel) ? null : (files[rel] ?? null)),
    servedSize: (folder, rel) => {
      const key = `${folder}/${rel}`;
      return links.has(key) ? null : (files[key] ?? null);
    },
    isLink: (rel) => links.has(rel),
    isDir: (rel) => dirs.has(rel) && !links.has(rel),
    workspaceNodeModules: () => ['node_modules', 'apps/web/node_modules', 'packages/contracts/node_modules'],
    freeBytes: () => over.freeBytes ?? 550e9,
    portFree: async (port, host) => {
      portCalls.push([port, host]);
      return !busy.has(port);
    },
  };
}

const PORTS = { api: 8000, web: 5173, music: 5199 };
const run = (over, lan = false) => fieldCheck({ probes: fakeProbes(over), ports: PORTS, lan });
const line = (result, name) => result.lines.find((entry) => entry.name === name);

describe('pnpm field --check', () => {
  it('reports every need as ok on a ready machine and exits 0', async () => {
    const result = await run({ env: {} });
    expect(result.lines.map((entry) => entry.name)).toEqual([
      'node', 'pnpm', 'uv', 'dependencies', 'ffmpeg', 'ffprobe', 'heic', 'music', 'catalogs', 'voice', 'disk', 'ports',
    ]);
    expect(result.lines.filter((entry) => entry.status !== 'ok')).toEqual([]);
    expect(result.exitCode).toBe(0);
    expect(line(result, 'ffmpeg').detail).toContain('ffmpeg version 8.1.1');
    expect(line(result, 'music').detail).toContain('2 of 2');
    expect(line(result, 'catalogs').detail).toContain('2 of 2');
  });

  it('treats a missing ffprobe or ffmpeg as required: exit 1', async () => {
    const result = await run({ commands: { 'ffprobe -hide_banner -version': { ok: false, stdout: '' } } });
    expect(line(result, 'ffprobe').status).toBe('missing');
    expect(result.exitCode).toBe(1);
    const noFfmpeg = await run({ commands: { 'ffmpeg -hide_banner -version': { ok: false, stdout: '' } } });
    expect(line(noFfmpeg, 'ffmpeg').status).toBe('missing');
  });

  it('gives the plain HEIC advice when ffprobe cannot read stream groups, and still exits 0', async () => {
    const result = await run({ commands: { 'ffprobe -hide_banner -h': { ok: true, stdout: '-show_streams  show streams info\n' } } });
    expect(line(result, 'heic')).toEqual({ name: 'heic', status: 'advice', detail: HEIC_ADVICE });
    expect(HEIC_ADVICE).toBe(
      "iPhone HEIC photos will be refused: install ffmpeg 7.1 or later, or set the iPhone's Camera Formats to Most Compatible (JPEG)"
    );
    expect(result.exitCode).toBe(0);
  });

  it('counts the music files present at their listed size, as advice, without naming any', async () => {
    const music = [
      { path: 'vcsl/Chimes/C#4.wav', bytes: 10 },
      { path: 'vcsl/Chimes/D4.wav', bytes: 11 },
      { path: 'scottbuckley/sb_signaltonoise_nomelody.mp3', bytes: 20 },
    ];
    const result = await run({ music, files: { [`${SOURCES}vcsl/Chimes/D4.wav`]: 999 }, absent: [`${SOURCES}scottbuckley/sb_signaltonoise_nomelody.mp3`] });
    const entry = line(result, 'music');
    expect(entry.status).toBe('advice');
    expect(entry.detail).toContain('1 of 3');
    for (const name of ['Chimes', 'C#4', 'D4', 'scottbuckley']) expect(entry.detail).not.toContain(name);
    expect(result.exitCode).toBe(0);
  });

  it('counts a music file reached through a link as missing, as the music server would', async () => {
    const result = await run({ links: [`${SOURCES}vcsl/Chimes/C#4.wav`] });
    expect(line(result, 'music').detail).toContain('1 of 2');
  });

  it('treats a missing catalog file as required: exit 1', async () => {
    const result = await run({ absent: ['apps/web/public/catalog/stars/gaia-dr3/L0/0.core.bin'] });
    expect(line(result, 'catalogs').status).toBe('missing');
    expect(line(result, 'catalogs').detail).toContain('1 of 2');
    expect(result.exitCode).toBe(1);
  });

  it('treats node_modules linked into another checkout, or a missing .venv, as required', async () => {
    const linked = await run({ links: ['apps/web/node_modules'] });
    expect(line(linked, 'dependencies').status).toBe('missing');
    expect(line(linked, 'dependencies').detail).toContain('pnpm install --frozen-lockfile --offline');
    expect(linked.exitCode).toBe(1);
    const noVenv = await run({ absentDirs: ['.venv'] });
    expect(line(noVenv, 'dependencies').status).toBe('missing');
    expect(line(noVenv, 'dependencies').detail).toContain('uv sync');
  });

  it('treats a Node older than package.json asks for as required, and another pnpm as advice', async () => {
    const oldNode = await run({ nodeVersion: 'v20.11.0' });
    expect(line(oldNode, 'node').status).toBe('missing');
    expect(oldNode.exitCode).toBe(1);
    expect(line(await run({ nodeVersion: 'v22.0.0' }), 'node').status).toBe('ok');
    const otherPnpm = await run({ commands: { 'pnpm --version': { ok: true, stdout: '9.0.0\n' } } });
    expect(line(otherPnpm, 'pnpm').status).toBe('advice');
    expect(line(otherPnpm, 'pnpm').detail).toContain('10.7.1');
  });

  it('says whether live Claude is on, and the key only as set or not set', async () => {
    const secret = 'sk-planted-secret-value';
    const on = line(await run({ env: { ALBEDIAN_ENABLE_LIVE_CLAUDE: '1', ANTHROPIC_API_KEY: secret } }), 'voice');
    expect(on.detail).toMatch(/live Claude on/);
    expect(on.detail).toMatch(/key set/);
    expect(on.detail).not.toContain(secret);
    const blankKey = line(await run({ env: { ALBEDIAN_ENABLE_LIVE_CLAUDE: '1', ANTHROPIC_API_KEY: '  ' } }), 'voice');
    expect(blankKey.status).toBe('advice');
    expect(blankKey.detail).toMatch(/key not set/);
    const off = line(await run({ env: { ALBEDIAN_ENABLE_LIVE_CLAUDE: 'true', ANTHROPIC_API_KEY: secret } }), 'voice');
    expect(off.detail).toMatch(/live Claude off/);
    expect(off.detail).toMatch(/built-in rules/);
    expect(off.detail).toMatch(/typed commands work offline/);
  });

  it('gives advice when the disk has under the threshold free', async () => {
    const result = await run({ freeBytes: (DISK_ADVICE_GB - 1) * 1e9 });
    expect(line(result, 'disk').status).toBe('advice');
    expect(result.exitCode).toBe(0);
    expect(DISK_ADVICE_GB).toBe(10);
  });

  it('checks the three ports on the hosts pnpm field would use, a busy one as advice', async () => {
    const probes = fakeProbes({ busyPorts: [5173] });
    const result = await fieldCheck({ probes, ports: PORTS, lan: true });
    expect(probes.portCalls).toEqual([
      [8000, '127.0.0.1'],
      [5173, '0.0.0.0'],
      [5199, '127.0.0.1'],
    ]);
    expect(line(result, 'ports').status).toBe('advice');
    expect(line(result, 'ports').detail).toContain('5173');
  });

  it('prints one line per check and never a file name, a path under data/ or a key', async () => {
    const secret = 'sk-planted-secret-value';
    const music = [{ path: 'vcsl/PlantedName#1.wav', bytes: 3 }];
    const result = await run({
      music,
      absent: ['apps/web/public/catalog/stars/gaia-dr3/L0/0.core.bin', `${SOURCES}vcsl/PlantedName#1.wav`],
      env: { ALBEDIAN_ENABLE_LIVE_CLAUDE: '1', ANTHROPIC_API_KEY: secret },
      commands: { 'ffprobe -hide_banner -h': { ok: true, stdout: '' } },
    });
    const report = formatReport(result);
    expect(report.trim().split('\n')).toHaveLength(result.lines.length + 1);
    for (const banned of ['PlantedName', 'gaia-dr3', '0.core.bin', 'openngc', 'data/', secret]) {
      expect(report, banned).not.toContain(banned);
    }
    expect(report).toMatch(/^MISSING /m);
    expect(report).toMatch(/^advice /m);
  });
});

describe('the real probes, on a temporary folder', () => {
  it('count a music file only as the music server would serve it: no link on the way, nothing outside', () => {
    const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-field-check-')));
    try {
      const sources = path.join(root, '.cache/music/sources');
      fs.mkdirSync(path.join(sources, 'vcsl/Chimes'), { recursive: true });
      fs.writeFileSync(path.join(sources, 'vcsl/Chimes/C#4.wav'), 'abcd');
      fs.mkdirSync(path.join(root, 'elsewhere'));
      fs.writeFileSync(path.join(root, 'elsewhere/x.wav'), 'abcd');
      fs.symlinkSync(path.join(root, 'elsewhere/x.wav'), path.join(sources, 'link.wav'));
      fs.symlinkSync(path.join(root, 'elsewhere'), path.join(sources, 'linkdir'));
      const probes = realProbes(root);
      expect(probes.servedSize('.cache/music/sources', 'vcsl/Chimes/C#4.wav')).toBe(4);
      expect(probes.servedSize('.cache/music/sources', 'link.wav')).toBeNull();
      expect(probes.servedSize('.cache/music/sources', 'linkdir/x.wav')).toBeNull();
      // Three levels up from the sources folder is the root, where the file is real and outside the folder
      expect(probes.servedSize('.cache/music/sources', '../../../elsewhere/x.wav')).toBeNull();
      expect(probes.servedSize('.cache/music/sources', 'vcsl/none.wav')).toBeNull();
      expect(probes.fileSize('.cache/music/sources/link.wav')).toBeNull();
      expect(probes.fileSize('elsewhere/x.wav')).toBe(4);
      expect(probes.isLink('.cache/music/sources/linkdir')).toBe(true);
      expect(probes.isDir('.cache/music/sources/linkdir')).toBe(false);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it('list node_modules for the root and each workspace package that has a package.json', () => {
    const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-field-check-')));
    try {
      for (const dir of ['apps/web', 'apps/notes', 'packages/contracts']) fs.mkdirSync(path.join(root, dir), { recursive: true });
      fs.writeFileSync(path.join(root, 'apps/web/package.json'), '{}');
      fs.writeFileSync(path.join(root, 'packages/contracts/package.json'), '{}');
      expect(realProbes(root).workspaceNodeModules()).toEqual([
        'node_modules',
        'apps/web/node_modules',
        'packages/contracts/node_modules',
      ]);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
