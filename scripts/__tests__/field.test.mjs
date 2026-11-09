import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  API_PORT,
  DEFAULT_ORIGINS,
  MUSIC_BASE,
  MUSIC_PORT,
  WEB_PORT,
  fieldPlan,
  musicSourcesLine,
  parseFieldArgs,
  pickLanAddress,
} from '../field.mjs';

const interfaces = {
  lo0: [
    { address: '127.0.0.1', family: 'IPv4', internal: true },
    { address: '::1', family: 'IPv6', internal: true },
  ],
  awdl0: [{ address: 'fe80::1', family: 'IPv6', internal: false }],
  bridge0: [{ address: '169.254.10.4', family: 'IPv4', internal: false }],
  en0: [
    { address: 'fe80::2', family: 'IPv6', internal: false },
    { address: '192.168.1.20', family: 'IPv4', internal: false },
  ],
};

function hostArg(args) {
  const i = args.indexOf('--host');
  return i === -1 ? undefined : args[i + 1];
}

describe('field start plan', () => {
  it('keeps the API and the web app on this machine by default', () => {
    const plan = fieldPlan({ lan: false, env: {} });
    expect(hostArg(plan.api.args)).toBe('127.0.0.1');
    expect(plan.api.args).toContain(String(API_PORT));
    expect(hostArg(plan.web.args)).toBe('localhost');
    expect(plan.web.args).toContain('--strictPort');
    expect(plan.api.env.ALBEDIAN_CORS_ORIGINS).toBe(DEFAULT_ORIGINS.join(','));
    expect(plan.web.env.ALBEDIAN_API_TARGET).toBe(`http://127.0.0.1:${API_PORT}`);
    expect(plan.phoneUrl).toBeNull();
  });

  it('runs the dev server on this machine by default, with no build first', () => {
    const plan = fieldPlan({ lan: false, env: {} });
    expect(plan.build).toBeNull();
    expect(plan.web.args.slice(0, 4)).toEqual(['--filter', '@albedian/web', 'exec', 'vite']);
    expect(plan.web.args).not.toContain('preview');
  });

  it('with --lan builds the web app first and serves only that build with vite preview', () => {
    const plan = fieldPlan({ lan: true, lanAddress: '192.168.1.20', env: { KEEP: '1' } });
    expect(plan.build.command).toBe('pnpm');
    expect(plan.build.args).toEqual(['--filter', '@albedian/web', 'build']);
    expect(plan.build.env.KEEP).toBe('1');
    // The dev server hands out any file in the repository by its /@fs/ path; preview serves apps/web/dist alone
    expect(plan.web.args.slice(0, 5)).toEqual(['--filter', '@albedian/web', 'exec', 'vite', 'preview']);
    expect(hostArg(plan.web.args)).toBe('0.0.0.0');
    expect(plan.web.args).toContain('--strictPort');
    expect(plan.web.args).toContain(String(WEB_PORT));
  });

  it('starts uvicorn without its access log, which would print query strings', () => {
    for (const lan of [false, true]) {
      const plan = fieldPlan({ lan, lanAddress: '192.168.1.20', env: {} });
      expect(plan.api.args).toContain('--no-access-log');
    }
  });

  it('replaces an inherited origin list, so a wildcard in the shell never reaches the API', () => {
    const plan = fieldPlan({ lan: false, env: { ALBEDIAN_CORS_ORIGINS: '*', ALBEDIAN_TRUSTED_HOSTS: '*' } });
    expect(plan.api.env.ALBEDIAN_CORS_ORIGINS).toBe(DEFAULT_ORIGINS.join(','));
    expect(plan.api.env.ALBEDIAN_TRUSTED_HOSTS).toBeUndefined();
  });

  it('with --lan opens only the web app to the network and adds exactly one origin', () => {
    const plan = fieldPlan({ lan: true, lanAddress: '192.168.1.20', env: {} });
    expect(hostArg(plan.api.args)).toBe('127.0.0.1');
    expect(hostArg(plan.web.args)).toBe('0.0.0.0');
    const origins = plan.api.env.ALBEDIAN_CORS_ORIGINS.split(',');
    expect(origins).toEqual([...DEFAULT_ORIGINS, `http://192.168.1.20:${WEB_PORT}`]);
    expect(origins.some((origin) => origin.includes('*'))).toBe(false);
    expect(plan.phoneUrl).toBe(`http://192.168.1.20:${WEB_PORT}`);
  });

  it('refuses --lan without a private network address', () => {
    expect(() => fieldPlan({ lan: true, lanAddress: null, env: {} })).toThrow(/no private network address/);
    expect(() => fieldPlan({ lan: true, lanAddress: '8.8.8.8', env: {} })).toThrow(/not a private network address/);
  });

  it('picks the private IPv4 address and skips loopback, link-local and IPv6', () => {
    expect(pickLanAddress(interfaces)).toBe('192.168.1.20');
    expect(pickLanAddress({ lo0: interfaces.lo0, bridge0: interfaces.bridge0 })).toBeNull();
    expect(pickLanAddress({ en1: [{ address: '172.20.10.3', family: 'IPv4', internal: false }] })).toBe('172.20.10.3');
    expect(pickLanAddress({ en1: [{ address: '203.0.113.9', family: 'IPv4', internal: false }] })).toBeNull();
  });

  it('accepts only --lan, --help and the three port options', () => {
    const defaults = { lan: false, help: false, apiPort: API_PORT, webPort: WEB_PORT, musicPort: MUSIC_PORT };
    expect(parseFieldArgs([])).toEqual(defaults);
    expect(parseFieldArgs(['--lan'])).toEqual({ ...defaults, lan: true });
    expect(parseFieldArgs(['--help'])).toEqual({ ...defaults, help: true });
    expect(parseFieldArgs(['--api-port', '8100', '--web-port', '5180'])).toEqual({ ...defaults, apiPort: 8100, webPort: 5180 });
    expect(() => parseFieldArgs(['--host', '0.0.0.0'])).toThrow(/Unknown option --host/);
    expect(() => parseFieldArgs(['--api-port'])).toThrow(/needs a port/);
    expect(() => parseFieldArgs(['--web-port', '80'])).toThrow(/needs a port/);
    expect(parseFieldArgs(['--music-port', '5299'])).toEqual({ ...defaults, musicPort: 5299 });
    expect(() => parseFieldArgs(['--music-port', 'x'])).toThrow(/needs a port/);
  });

  it('starts the music sources server on this machine only, at the music port', () => {
    for (const lan of [false, true]) {
      const plan = fieldPlan({ lan, lanAddress: '192.168.1.20', env: {}, musicPort: 5299 });
      expect(plan.music.command).toBe(process.execPath);
      // serveSources.mjs binds 127.0.0.1 itself and takes no host option
      expect(plan.music.args).toEqual(['apps/web/spike/music/serveSources.mjs', '--port', '5299']);
      expect(plan.web.env.ALBEDIAN_MUSIC_TARGET).toBe('http://127.0.0.1:5299');
    }
    expect(MUSIC_PORT).toBe(5199);
  });

  it('points the app at the music through its own origin, in the dev server and in the --lan build', () => {
    expect(MUSIC_BASE).toBe('/music-sources/');
    const shell = { VITE_ALBEDIAN_MUSIC_SOURCES: 'http://127.0.0.1:5199/' };
    const plain = fieldPlan({ lan: false, env: shell });
    expect(plain.web.env.VITE_ALBEDIAN_MUSIC_SOURCES).toBe(MUSIC_BASE);
    const lan = fieldPlan({ lan: true, lanAddress: '192.168.1.20', env: shell });
    // Vite bakes VITE_ values into the build, so the build step is where the phone's music base is set
    expect(lan.build.env.VITE_ALBEDIAN_MUSIC_SOURCES).toBe(MUSIC_BASE);
    expect(lan.web.env.VITE_ALBEDIAN_MUSIC_SOURCES).toBe(MUSIC_BASE);
  });

  it('carries other ports into the origins, the proxy target and the health check', () => {
    const plan = fieldPlan({ lan: true, lanAddress: '10.0.0.7', env: {}, apiPort: 8100, webPort: 5180 });
    expect(plan.api.env.ALBEDIAN_CORS_ORIGINS).toBe('http://localhost:5180,http://127.0.0.1:5180,http://10.0.0.7:5180');
    expect(plan.web.env.ALBEDIAN_API_TARGET).toBe('http://127.0.0.1:8100');
    expect(plan.healthUrl).toBe('http://127.0.0.1:8100/api/health');
    expect(plan.web.args).toContain('5180');
  });
});

describe('the music line pnpm field prints', () => {
  it('counts the sample files without naming any, and says plainly when the folder is missing', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'albedian-field-music-'));
    try {
      const sources = path.join(root, 'sources');
      expect(musicSourcesLine(sources)).toMatch(/is missing, so the music stays silent/);
      fs.mkdirSync(path.join(sources, 'vcsl/Chimes'), { recursive: true });
      fs.writeFileSync(path.join(sources, 'vcsl/Chimes/C#4.wav'), 'a');
      fs.writeFileSync(path.join(sources, 'notes.txt'), 'b');
      fs.symlinkSync(path.join(sources, 'notes.txt'), path.join(sources, 'link.txt'));
      const line = musicSourcesLine(sources);
      expect(line).toContain('2 sample files');
      for (const name of ['C#4', 'notes', 'link', 'vcsl']) expect(line).not.toContain(name);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
