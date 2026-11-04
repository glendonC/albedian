import { RenderLayerSetSchema, type RenderLayerSet } from '@albedian/contracts';

/**
 * What the cislunar landing scene draws, in draw order, with each layer's evidence class and
 * the disclosure the UI shows. Every layer is simulated: real catalog data and NASA imagery,
 * placed, oriented and lit from a composed viewpoint, not an observation or an ephemeris.
 * Parsed by the contract at load, so a layer without a valid disclosure fails here.
 */
export const CISLUNAR_RENDER_LAYERS: RenderLayerSet = RenderLayerSetSchema.parse([
  {
    id: 'cislunar.sky',
    kind: 'sky_background',
    title: 'Sky panorama',
    evidenceClass: 'simulated',
    disclosure:
      "ESO's Milky Way photograph and NASA's plot of catalog stars, registered to the catalog stars and graded for display. The photograph also shows Venus and Jupiter where they stood in 2008 and 2009.",
    sources: [
      'ESO/S. Brunier, The Milky Way panorama (eso0932a), CC BY 4.0',
      'NASA/Goddard Space Flight Center Scientific Visualization Studio, Deep Star Maps (1024x512 print map)',
      'Registration fitted to Gaia DR3 and Hipparcos 2007 stars (eso0932a_registration.json)',
      'Provenance: apps/web/public/textures/sky_texture_sources.json',
    ],
  },
  {
    id: 'cislunar.catalog-stars',
    kind: 'catalog_stars',
    title: 'Catalog stars',
    evidenceClass: 'simulated',
    disclosure:
      'Gaia DR3 and Hipparcos 2007 positions, magnitudes and colours; each star image is a modeled telescope point-spread function.',
    sources: [
      'Gaia DR3 (Gaia Collaboration 2023)',
      'Hipparcos 2007 (van Leeuwen 2007)',
      'Colour: Gaia DR3 documentation section 5.5.1 and Ballesteros (2012)',
    ],
  },
  {
    id: 'cislunar.earth',
    kind: 'solar_system_body',
    title: 'Earth',
    evidenceClass: 'simulated',
    disclosure: 'NASA day and night imagery on a globe placed, turned and lit for composition, not at an ephemeris position.',
    sources: [
      'NASA Blue Marble Next Generation, November 2004',
      'NASA Black Marble 2016',
      'Cloud and ocean masks (earth_clouds.png, earth_specular.jpg; provenance not recorded)',
    ],
  },
  {
    id: 'cislunar.atmosphere',
    kind: 'atmosphere',
    title: 'Atmosphere',
    evidenceClass: 'simulated',
    disclosure: 'Modeled single scattering with hand-set colour terms, baked to a lookup table and tuned for appearance.',
    sources: ['scripts/bake_bruneton_lut_and_splats.py'],
  },
  {
    id: 'cislunar.moon',
    kind: 'solar_system_body',
    title: 'Moon',
    evidenceClass: 'simulated',
    disclosure: 'Lunar albedo and relief maps on a Moon placed and lit for composition, not at an ephemeris position.',
    sources: [
      'LRO albedo (lro_moon_2k.jpg, 1024x512; provenance not recorded)',
      'LOLA-derived relief (lro_moon_disp.jpg; provenance not recorded)',
    ],
  },
  {
    id: 'post.camera-optics',
    kind: 'camera_optics',
    title: 'Camera optics',
    evidenceClass: 'simulated',
    disclosure: 'Modeled lens glare, chromatic aberration, anamorphic streaks and vignette, then AgX tone mapping.',
    sources: [],
  },
]);
