import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The deep-sky regime keeps one ESO image: eso1804a, the Lupus 3 star formation region, behind the
 * modelled black hole. It was filed as "eso_lagoon_nebula.jpg", the name of an object it does not
 * show (the data lane matched it at eso.org). The Orion view is deleted: ESO's VISTA image of the
 * Orion Nebula (eso1006a) on a plane in front of the camera, not at its place on the sky, and
 * splats baked from a NASA model of another object (nasa_nebula_splats.json, provenance not
 * recorded). Neither file nor any code that drew or baked them remains.
 */
const here = (rel: string) => fileURLToPath(new URL(rel, import.meta.url));
const read = (rel: string) => fs.readFileSync(here(rel), 'utf8');
const textures = here('../../../public/textures/');
const models = here('../../../public/models/');
const renderer = read('../SparkPortalRenderer.ts');

describe('deep-sky imagery', () => {
  it('loads the Lupus 3 image for the black hole only; scene 03 never sampled it', () => {
    // uDustTex was assigned eso1804a but bound to no material and declared in no shader
    expect(renderer).not.toMatch(/uDustTex/);
    expect(renderer.match(/eso1804a\.jpg/g)).toHaveLength(1);
  });

  it('keeps the Lupus 3 image under its ESO id, loaded by that name', () => {
    expect(fs.existsSync(`${textures}eso1804a.jpg`)).toBe(true);
    expect(renderer).toContain("'/textures/eso1804a.jpg'");
    expect(fs.readdirSync(textures).filter((f) => /lagoon|orion/i.test(f))).toEqual([]);
    expect(renderer).not.toMatch(/eso_(lagoon|orion)_nebula/);
  });

  it('has no Orion view: no scene, image, splats or splat bake', () => {
    expect(fs.existsSync(`${textures}eso1006a.jpg`)).toBe(false);
    expect(fs.existsSync(`${models}nasa_nebula_splats.json`)).toBe(false);
    const sources = {
      renderer,
      cosmosRenderer: read('../cosmosRenderer.ts'),
      splatBuilders: read('../volumetricSplatsAndLut.ts'),
      layers: read('../webglRenderLayers.ts'),
      bake: read('../../../../../scripts/bake_bruneton_lut_and_splats.py'),
    };
    for (const [name, source] of Object.entries(sources)) {
      expect(source.match(/orion_nebula|eso1006a|nasa_nebula_splats|NebulaSplat|uOrionVistaTex|ORION_/g), name).toBeNull();
    }
  });
});
