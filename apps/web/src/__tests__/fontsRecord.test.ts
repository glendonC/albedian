// @vitest-environment node
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { AssetProvenanceRecordSchema } from '@albedian/contracts';

// The app serves Geist itself (web-fonts-1): the release tag's own files byte for byte, pinned in the download registry,
// recorded as downloaded as is, with the licence beside them, and drawn by the faces the app's stylesheets name.

const here = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(here, '../..');
const repoRoot = path.resolve(webDir, '../..');
const fontsDir = path.join(webDir, 'public/fonts/geist');
const read = (file: string) => fs.readFileSync(file, 'utf8');
const sha256 = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const record = () => AssetProvenanceRecordSchema.parse(JSON.parse(read(path.join(fontsDir, 'PROVENANCE.json'))));
const registry = () => new Map(
  (JSON.parse(read(path.join(repoRoot, 'data/downloads.json'))).downloads as Array<{ path: string; url: string; bytes: number; sha256: string; licence: string }>).map((e) => [e.path, e])
);
const COMMIT = 'ef2597cfc42f4797676df105517ccc0a1cfa6569';

describe("the app's own Geist", () => {
  it('serves the three files the record lists, and nothing else, each the very file the registry pins', () => {
    expect(fs.readdirSync(fontsDir).sort()).toEqual(['Geist-wght.woff2', 'GeistMono-wght.woff2', 'OFL.txt', 'PROVENANCE.json']);
    const pins = registry();
    expect(record().files.map((f) => f.path)).toEqual(['Geist-wght.woff2', 'GeistMono-wght.woff2', 'OFL.txt']);
    for (const file of record().files) {
      const onDisk = path.join(fontsDir, file.path);
      const pin = pins.get(`.cache/geist-font/v1.9.2/${file.path}`);
      expect(file.sha256, file.path).toBe(sha256(onDisk));
      expect(pin?.sha256, file.path).toBe(file.sha256);
      expect(pin?.bytes, file.path).toBe(fs.statSync(onDisk).size);
      expect(pin?.licence, file.path).toBe('OFL-1.1');
      // From the release tag's own commit, by the project's fetch script, no other host
      expect(pin?.url.startsWith(`https://raw.githubusercontent.com/vercel/geist-font/${COMMIT}/`), file.path).toBe(true);
      expect(file.producedBy, file.path).toEqual({ method: 'downloaded-as-is' });
    }
  });

  it('carries the licence as released, named where it is served, and the approved Type row words', () => {
    const [source] = record().sources;
    expect(source?.license.spdx).toBe('OFL-1.1');
    expect(source?.license.name).toBe('SIL Open Font License 1.1');
    expect(source?.license.conditions ?? '').toContain('served with the app at /fonts/geist/OFL.txt');
    expect(source?.credit).toBe('Geist');
    const ofl = read(path.join(fontsDir, 'OFL.txt'));
    expect(ofl).toContain('SIL OPEN FONT LICENSE Version 1.1');
    expect(ofl).not.toMatch(/with Reserved Font Name/i);
  });

  it('draws Geist and Geist Mono from those files, swapped in when loaded, as the outside host asked', () => {
    const css = read(path.join(webDir, 'src/styles/fonts.css'));
    const faces = [...css.matchAll(/@font-face\s*\{([^}]*)\}/g)].map((m) => m[1]!);
    expect(faces).toHaveLength(2);
    const face = (body: string) => ({
      family: /font-family:\s*'([^']+)'/.exec(body)?.[1],
      src: /src:\s*url\('([^']+)'\)\s*format\('woff2'\)/.exec(body)?.[1],
      weight: /font-weight:\s*([^;]+);/.exec(body)?.[1],
      style: /font-style:\s*([^;]+);/.exec(body)?.[1],
      display: /font-display:\s*([^;]+);/.exec(body)?.[1],
    });
    expect(faces.map(face)).toEqual([
      { family: 'Geist', src: '/fonts/geist/Geist-wght.woff2', weight: '100 900', style: 'normal', display: 'swap' },
      { family: 'Geist Mono', src: '/fonts/geist/GeistMono-wght.woff2', weight: '100 900', style: 'normal', display: 'swap' },
    ]);
    // Loaded with the app, before its other stylesheets
    expect(read(path.join(webDir, 'src/App.tsx'))).toContain("import './styles/fonts.css';\nimport './styles/albedian.css';");
  });

  it('names in the type tokens only the families it serves', () => {
    const families = (file: string, token: string) => new RegExp(`${token}:\\s*'([^']+)'`).exec(read(path.join(webDir, 'src/styles', file)))?.[1];
    expect(families('look-tokens.css', '--look-font-sans')).toBe('Geist');
    expect(families('hud-tokens.css', '--hud-font-sans')).toBe('Geist');
    expect(families('hud-tokens.css', '--hud-font-mono')).toBe('Geist Mono');
  });
});
