import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The two ESO images of the deep-sky views were filed under names of objects they do not show
 * (the data lane matched them at eso.org): "eso_lagoon_nebula.jpg" is eso1804a, Lupus 3, and
 * "eso_orion_nebula.jpg" is eso1006a, VISTA's near-infrared view of the Orion Nebula. They are
 * filed under their ESO ids, and the renderer loads them by those names.
 */
const textures = fileURLToPath(new URL('../../../public/textures/', import.meta.url));
const renderer = fs.readFileSync(fileURLToPath(new URL('../SparkPortalRenderer.ts', import.meta.url)), 'utf8');

describe('ESO nebula images', () => {
  it('are filed under their ESO ids', () => {
    expect(fs.existsSync(`${textures}eso1804a.jpg`)).toBe(true);
    expect(fs.existsSync(`${textures}eso1006a.jpg`)).toBe(true);
    expect(fs.readdirSync(textures).filter((f) => /lagoon|orion/i.test(f))).toEqual([]);
  });

  it('load Lupus 3 for the black hole only; scene 03 never sampled it', () => {
    // uDustTex was assigned eso1804a but bound to no material and declared in no shader
    expect(renderer).not.toMatch(/uDustTex/);
    expect(renderer.match(/eso1804a\.jpg/g)).toHaveLength(1);
  });

  it('are loaded by those names', () => {
    expect(renderer).toContain("'/textures/eso1804a.jpg'");
    expect(renderer).toContain("'/textures/eso1006a.jpg'");
    expect(renderer).not.toMatch(/eso_(lagoon|orion)_nebula/);
  });
});
