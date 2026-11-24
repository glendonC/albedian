import type * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { FRAME_STEP_WORDS } from '../../frameFinishing.js';
import { createCinemaOpticsPass, createCinemaOpticsUniforms } from '../skyDomeAndCinemaShader.js';

/**
 * streak-ghosts-one (LOOK, ruled by the lead at 14:32 on 24 Nov: "nothing that is not a star may read as one"). The
 * WebGL optics pass added an "anamorphic" streak by sampling the frame at eight fixed offsets 0.018 of its width apart,
 * so every bright star in the predicted sky drew a row of separate blue dots 26 px apart at 1440 wide (measured beside
 * Arcturus). No reference read live draws a streak (NASA's Eyes, ESO's photograph), so it is cut: the pass reads each
 * pixel only at its own place and at the colour fringe's small radial offset.
 */
function fragmentOf(): string {
  const pass = createCinemaOpticsPass(createCinemaOpticsUniforms());
  return (pass.material as THREE.ShaderMaterial).fragmentShader;
}

describe('WebGL cinema optics pass', () => {
  it('samples the frame only at the pixel and its colour fringe offset, never at fixed offsets along a row', () => {
    const shader = fragmentOf();
    const reads = shader.match(/texture2D\(\s*tDiffuse\s*,[^;]*\)/g) ?? [];
    expect(reads.map((read) => read.replace(/\s+/g, ''))).toEqual([
      'texture2D(tDiffuse,vUv+caOffset)',
      'texture2D(tDiffuse,vUv)',
      'texture2D(tDiffuse,vUv-caOffset)',
    ]);
    expect(shader).not.toMatch(/\bfor\s*\(/);
    expect(shader).not.toMatch(/vUv\s*[+-]\s*vec2\(/);
    expect(shader).not.toMatch(/anamorphic/);
  });

  it('keeps the colour fringes and the vignette as they were', () => {
    const shader = fragmentOf();
    expect(shader).toContain('vec2 caOffset = centered * r2 * caStrength;');
    expect(shader).toContain('float vignette = smoothstep(0.92, 0.18, r2);');
    expect(shader).toContain('vec3 finalCol = baseCol * mix(0.76, 1.0, vignette);');
  });

  it('says what the pass does, and no longer a streak', () => {
    const words = FRAME_STEP_WORDS.cinemaOptics(true);
    expect(words).toBe('given the camera optics (colour fringes toward the edges and a vignette)');
    expect(words).not.toMatch(/streak/);
  });
});
