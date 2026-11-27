import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { ACES_INPUT_SCALE, CRAFT_HIGHLIGHT_CEILING_INPUT, CRAFT_WHITE_CAP_INPUT, heldHighlight, highlightRollOff } from '../craftLight.js';
import { EDGE_FRAGMENT, HIGHLIGHT_FRAGMENT, addEdgeLight, createEdgeLightUniforms } from '../craftEdgeLight.js';

// foil-clip-one (DECIDED-BY-LEAD 27 Nov 12:04: the lit-white cap held for every light): New Horizons' gold foil is a
// metal at roughness 0.3, and the key's glint on it is many times the lit white the exposure's cap counts, so at 500 AU
// from Betelgeuse its rim went to white. Below the cap nothing changes; above it every light on the craft rolls off
// toward a ceiling that stays under white.

/** three r187's ACES (tonemapping_pars_fragment) and the sRGB output, as an 8 bit code per channel. */
function acesCode(input: readonly [number, number, number]): [number, number, number] {
  const rrt = (v: number) => (v * (v + 0.0245786) - 0.000090537) / (v * (0.983729 * v + 0.432951) + 0.238081);
  const [r, g, b] = input;
  const m = [0.59719 * r + 0.35458 * g + 0.04823 * b, 0.076 * r + 0.90834 * g + 0.01566 * b, 0.0284 * r + 0.13383 * g + 0.83777 * b].map(rrt) as [number, number, number];
  const o = [1.60475 * m[0] - 0.53108 * m[1] - 0.07367 * m[2], -0.10208 * m[0] + 1.10813 * m[1] - 0.00605 * m[2], -0.00327 * m[0] - 0.07276 * m[1] + 1.07602 * m[2]];
  const srgb = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  return o.map((c) => Math.round(255 * srgb(Math.min(1, Math.max(0, c))))) as [number, number, number];
}

describe('the craft\'s highlight shoulder', () => {
  it('leaves every surface at or under the lit-white cap as it was', () => {
    for (const x of [0, 0.01, 0.18, 0.5, 0.9, CRAFT_WHITE_CAP_INPUT]) expect(highlightRollOff(x)).toBe(x);
  });

  it('rises above the cap from its slope there, always climbing and never reaching the ceiling', () => {
    const e = 1e-6;
    expect((highlightRollOff(CRAFT_WHITE_CAP_INPUT + e) - CRAFT_WHITE_CAP_INPUT) / e).toBeCloseTo(1, 4);
    let last = CRAFT_WHITE_CAP_INPUT;
    for (const x of [1.01, 1.5, 2, 4, 8, 30, 60]) {
      const y = highlightRollOff(x);
      expect(y).toBeGreaterThan(last);
      expect(y).toBeLessThan(CRAFT_HIGHLIGHT_CEILING_INPUT);
      expect(y).toBeLessThan(x);
      last = y;
    }
    // Far past it the curve meets the ceiling in floating point, and never passes it
    for (const x of [300, 3000, 1e6, Number.MAX_VALUE]) expect(highlightRollOff(x)).toBeLessThanOrEqual(CRAFT_HIGHLIGHT_CEILING_INPUT);
    expect(highlightRollOff(1e6)).toBeCloseTo(CRAFT_HIGHLIGHT_CEILING_INPUT, 9);
  });

  it('keeps a white at its ceiling over the lit white and under the clip the film check counts', () => {
    expect(CRAFT_WHITE_CAP_INPUT).toBe(1);
    expect(CRAFT_HIGHLIGHT_CEILING_INPUT).toBe(4);
    expect(acesCode([1, 1, 1])).toEqual([206, 206, 206]);
    const top = acesCode([CRAFT_HIGHLIGHT_CEILING_INPUT, CRAFT_HIGHLIGHT_CEILING_INPUT, CRAFT_HIGHLIGHT_CEILING_INPUT]);
    expect(top[0]).toBeGreaterThan(240);
    expect(top[0]).toBeLessThanOrEqual(245);
  });

  it('holds a light by its largest channel, so its colour stays and no channel passes the ceiling', () => {
    const exposure = 1.3;
    // The gold foil's glint facing the key, 31, 20 and 2.5 times the lit white (three's GGX at roughness 0.3)
    const lit = (CRAFT_WHITE_CAP_INPUT * ACES_INPUT_SCALE) / exposure;
    const glint: [number, number, number] = [30.9 * lit, 20.3 * lit, 2.5 * lit];
    const held = heldHighlight(glint, exposure);
    const input = held.map((c) => (c * exposure) / ACES_INPUT_SCALE) as [number, number, number];
    expect(Math.max(...input)).toBeLessThan(CRAFT_HIGHLIGHT_CEILING_INPUT);
    expect(Math.max(...input)).toBeCloseTo(highlightRollOff(30.9), 9);
    expect(held[1] / held[0]).toBeCloseTo(20.3 / 30.9, 12);
    expect(held[2] / held[0]).toBeCloseTo(2.5 / 30.9, 12);
    const code = acesCode(input);
    expect(code.every((c) => c >= 254)).toBe(false);
    // Unheld, the same glint is white
    expect(acesCode([30.9, 20.3, 2.5]).slice(0, 2)).toEqual([255, 255]);
    // Fresnel at grazing makes the glint white whatever the metal; held, it stays under the clip in every channel
    expect(acesCode(heldHighlight([256.8 * lit, 226.9 * lit, 176.4 * lit], exposure).map((c) => (c * exposure) / ACES_INPUT_SCALE) as [number, number, number]).every((c) => c < 250)).toBe(true);
    // At or under the cap, the light is left as it was
    expect(heldHighlight([0.9 * lit, 0.5 * lit, 0.2 * lit], exposure)).toEqual([0.9 * lit, 0.5 * lit, 0.2 * lit]);
    expect(heldHighlight([0, 0, 0], exposure)).toEqual([0, 0, 0]);
  });
});

describe('the highlight shoulder in the craft\'s shader', () => {
  const patched = () => {
    const material = new THREE.MeshStandardMaterial();
    addEdgeLight(new THREE.Mesh(new THREE.BoxGeometry(), material), createEdgeLightUniforms());
    const shader = { uniforms: {} as Record<string, unknown>, fragmentShader: THREE.ShaderLib.physical.fragmentShader, vertexShader: THREE.ShaderLib.physical.vertexShader };
    material.onBeforeCompile(shader as never, undefined as never);
    return shader.fragmentShader;
  };

  it('holds the whole outgoing light, the edge light with it, last before the output', () => {
    const f = patched();
    const at = f.indexOf(HIGHLIGHT_FRAGMENT);
    expect(at).toBeGreaterThan(f.indexOf(EDGE_FRAGMENT));
    expect(f.indexOf(EDGE_FRAGMENT)).toBeGreaterThan(0);
    expect(at).toBeLessThan(f.indexOf('#include <opaque_fragment>'));
    expect(f.slice(at + HIGHLIGHT_FRAGMENT.length, f.indexOf('#include <opaque_fragment>')).trim()).toBe('');
  });

  it('is the shoulder above, by the largest channel at the exposure three tone maps with, only where three tone maps', () => {
    expect(HIGHLIGHT_FRAGMENT).toBe(
      [
        '#ifdef TONE_MAPPING',
        '{',
        '  float craftPeak = max( max( outgoingLight.r, outgoingLight.g ), outgoingLight.b ) * toneMappingExposure / 0.6;',
        '  if ( craftPeak > 1.0 ) outgoingLight *= ( 1.0 + 3.0 * ( 1.0 - exp( - ( craftPeak - 1.0 ) / 3.0 ) ) ) / craftPeak;',
        '}',
        '#endif',
      ].join('\n'),
    );
    expect(ACES_INPUT_SCALE).toBe(0.6);
    // Three's ACES reads the same exposure uniform (tonemapping_pars_fragment)
    expect(THREE.ShaderChunk.tonemapping_pars_fragment).toContain('uniform float toneMappingExposure;');
    expect(THREE.ShaderChunk.tonemapping_pars_fragment).toContain('color *= toneMappingExposure / 0.6;');
  });
});
