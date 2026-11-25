import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * The deep-sky regime and its modelled black hole are cut (regimes-cut-one, DECIDED-BY-LEAD 24 Nov 17:33), with
 * eso1804a, the Lupus 3 photograph it drew behind the hole. The Orion view was deleted before: ESO's VISTA image of the
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
  it('has no Lupus 3 image: no file, record or load, gone with the black-hole scene', () => {
    expect(renderer).not.toMatch(/uDustTex|eso1804a|uLupus3Tex/);
    expect(fs.existsSync(`${textures}eso1804a.jpg`)).toBe(false);
    expect(read('../../../public/textures/PROVENANCE.json')).not.toMatch(/eso1804a/);
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

  it('has no flight to a nebula and no nebula model: flyToTargetIn3D and nasa_pillars_3d.glb are gone', () => {
    // flyToTargetIn3D had no caller, and its M42, nebula and Carina branch led to a deep-sky
    // regime that draws nothing for them; the model, which nothing loaded, had no provenance
    expect(renderer).not.toMatch(/flyToTargetIn3D/);
    expect(fs.existsSync(`${models}nasa_pillars_3d.glb`)).toBe(false);
  });
});
