import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The WebGL black hole view (landing scene 02) and its particles carried labels for physics the
 * code does not compute: a DNGR (James et al. 2015) and Kerr ray tracer, an ISCO at 2.65 r_s,
 * magneto-rotational instability, a blackbody disk, an Einstein-ring magnification and GPGPU
 * magnetohydrodynamic plasma (sweep item 19). The code is a hand-tuned composition; its labels
 * now say so, and these phrases stay out of the renderer.
 */
const REMOVED_PHRASES = [
  'DNGR',
  'von Tunzelmann',
  'Kip Thorne',
  'ISCO (2.65',
  'Magneto-rotational',
  'MRI',
  'Blackbody thermal',
  'einsteinAmplification',
  'Einstein ring starlight',
  'GPGPU',
  'Magnetohydrodynamic',
  'MHD',
];

const WEBGL_SOURCES = [
  '../SparkPortalRenderer.ts',
  '../webgl/skyDomeAndCinemaShader.ts',
  '../webgl/cislunarRegime.ts',
  '../webgl/solarNeighborhoodRegime.ts',
  '../webgl/deepSkyRegime.ts',
  '../webgl/planetarySurfaceRegime.ts',
  '../webgl/skyTargets.ts',
];
const source = WEBGL_SOURCES.map((rel) =>
  fs.readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')
).join('\n');

describe('no false physics labels in the WebGL renderer', () => {
  it('names none of the removed claims', () => {
    expect(REMOVED_PHRASES.filter((phrase) => new RegExp(`(?<![\\w-])${phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`).test(source))).toEqual([]);
  });
});
