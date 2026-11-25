import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyRows, icrsToPanoramaRows, parsePanoramaRegistration, plateCarreeUv } from '../../sky/skyRegistration.js';
import { brightestSkyDirection } from '../craftLight.js';

// craft-light-two (DECIDED-BY-LEAD: every light keeps a source the scene has): the rim comes from where the craft's sky
// is brightest, found in the very pixels the craft's sky is built from, and turned into ICRS through the registration.

const here = path.dirname(fileURLToPath(import.meta.url));
const registration = parsePanoramaRegistration(JSON.parse(fs.readFileSync(path.resolve(here, '../../../../public/textures/eso0932a_registration.json'), 'utf8')));
const W = 360;
const H = 180;

/** A black sky with one bright patch, `size` pixels square, centred on pixel (cx, cy) (typed test data). */
function skyWithPatch(cx: number, cy: number, size: number, rgb: [number, number, number]): Uint8ClampedArray {
  const px = new Uint8ClampedArray(4 * W * H);
  for (let i = 0; i < W * H; i++) px[4 * i + 3] = 255;
  for (let y = cy - (size >> 1); y < cy - (size >> 1) + size; y++) {
    for (let x = cx - (size >> 1); x < cx - (size >> 1) + size; x++) {
      const i = 4 * (y * W + x);
      px[i] = rgb[0];
      px[i + 1] = rgb[1];
      px[i + 2] = rgb[2];
    }
  }
  return px;
}

describe('the rim\'s source', () => {
  it('points where the sky\'s brightest patch is, through the registration, back to that patch', () => {
    for (const [cx, cy] of [[90, 60], [270, 130], [180, 90]] as const) {
      const rim = brightestSkyDirection(skyWithPatch(cx, cy, 6, [255, 255, 255]), W, H, registration)!;
      expect(Math.hypot(...rim.direction)).toBeCloseTo(1, 9);
      const [u, v] = plateCarreeUv(applyRows(icrsToPanoramaRows(registration), [...rim.direction] as [number, number, number]));
      // Row 0 is the north: the patch's centre, in the picture's own pixels, within a pixel
      expect(Math.abs(u * W - cx)).toBeLessThan(1);
      expect(Math.abs((1 - v) * H - cy)).toBeLessThan(1);
    }
  });

  it('takes the patch\'s colour in linear light, its largest channel 1', () => {
    const rim = brightestSkyDirection(skyWithPatch(100, 80, 6, [255, 128, 64]), W, H, registration)!;
    const lin = (c: number) => ((c / 255 + 0.055) / 1.055) ** 2.4;
    expect(rim.tint[0]).toBeCloseTo(1, 9);
    expect(rim.tint[1]).toBeCloseTo(lin(128) / lin(255), 9);
    expect(rim.tint[2]).toBeCloseTo(lin(64) / lin(255), 9);
  });

  it('follows the brightest light, not the largest dim area', () => {
    const px = skyWithPatch(60, 90, 40, [40, 40, 40]);
    const bright = skyWithPatch(300, 50, 8, [255, 255, 255]);
    for (let i = 0; i < px.length; i++) px[i] = Math.max(px[i]!, bright[i]!);
    const rim = brightestSkyDirection(px, W, H, registration, 0.001)!;
    const [u] = plateCarreeUv(applyRows(icrsToPanoramaRows(registration), [...rim.direction] as [number, number, number]));
    expect(Math.abs(u * W - 300)).toBeLessThan(2);
  });

  it('takes only the top hundredth of the sphere by default, so a wide dim sky barely pulls it', () => {
    const px = skyWithPatch(60, 90, 40, [40, 40, 40]);
    const bright = skyWithPatch(300, 90, 8, [255, 255, 255]);
    for (let i = 0; i < px.length; i++) px[i] = Math.max(px[i]!, bright[i]!);
    const rim = brightestSkyDirection(px, W, H, registration)!;
    const [u] = plateCarreeUv(applyRows(icrsToPanoramaRows(registration), [...rim.direction] as [number, number, number]));
    expect(Math.abs(u * W - 300)).toBeLessThan(10);
  });

  it('gives none for a sky with no light', () => {
    expect(brightestSkyDirection(skyWithPatch(10, 10, 0, [0, 0, 0]), W, H, registration)).toBeNull();
  });
});
