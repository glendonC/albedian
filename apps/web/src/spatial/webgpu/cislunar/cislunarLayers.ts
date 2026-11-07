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
      "ESO's Milky Way photograph with its stars removed so every star drawn is a catalog star, registered to the catalog and graded for display. It also shows two planets, Venus and Jupiter, where its 2008 to 2009 shoot photographed them.",
    sources: [
      'ESO/S. Brunier, The Milky Way panorama (eso0932a), CC BY 4.0, modified: stars removed by scripts/bake_starless_panorama.py (eso_milkyway_diffuse_6k.webp)',
      'Registration fitted to Gaia DR3 and Hipparcos 2007 stars (eso0932a_registration.json)',
      'Provenance: apps/web/public/textures/PROVENANCE.json',
    ],
  },
  {
    id: 'cislunar.catalog-stars',
    kind: 'catalog_stars',
    title: 'Catalog stars',
    evidenceClass: 'simulated',
    disclosure:
      'Gaia DR3 and Hipparcos 2007 positions, magnitudes and colours to G 12; each star image is a modeled telescope point-spread function.',
    sources: [
      'Gaia DR3 to G 12 (Gaia Collaboration 2023)',
      'Hipparcos 2007 (van Leeuwen 2007)',
      'Colour: Gaia DR3 documentation section 5.5.1 and Ballesteros (2012)',
    ],
  },
  {
    id: 'cislunar.earth',
    kind: 'solar_system_body',
    title: 'Earth',
    evidenceClass: 'simulated',
    disclosure:
      "NASA day and night imagery on the Earth as it was turned and lit at 2025-03-07 11:00 UTC, at true scale; the clouds are not that day's, and city lights are drawn far brighter than an exposure for the day side shows them.",
    sources: [
      'NASA Blue Marble Next Generation, November 2004',
      'NASA Black Marble 2016',
      'Cloud and ocean masks (earth_clouds.png, earth_specular.jpg; provenance not recorded)',
      'Sun and Moon: JPL Horizons API, DE441 (landingMoment.json)',
      'Rotation: GMST (IAU 1982) after IAU 1976 precession (bodyOrientation.ts)',
    ],
  },
  {
    id: 'cislunar.atmosphere',
    kind: 'atmosphere',
    title: 'Atmosphere',
    evidenceClass: 'simulated',
    disclosure:
      'Modeled single scattering with hand-set colour terms, baked to a lookup table and tuned for appearance, lit by the Sun of 2025-03-07 11:00 UTC.',
    sources: ['scripts/bake_bruneton_lut_and_splats.py'],
  },
  {
    id: 'cislunar.moon',
    kind: 'solar_system_body',
    title: 'Moon',
    evidenceClass: 'simulated',
    disclosure:
      'NASA SVS lunar colour, made for appearance rather than science, and LOLA relief at true slopes, where the Moon was and as it was turned and lit at 2025-03-07 11:00 UTC, at true scale.',
    sources: [
      "NASA's Scientific Visualization Studio, CGI Moon Kit (SVS 4720): colour lroc_color_16bit_srgb_8k from the LROC WAC mosaic (moon_svs4720_color_8k.jpg)",
      'SVS 4720 LOLA heights ldem_16_uint, 16 per degree, as normals by scripts/bake_moon_maps.py (moon_svs4720_lola_normal_16ppd.webp)',
      'Colour scaled to the geometric albedo 0.12 of the NASA Moon fact sheet',
      'Position: JPL Horizons API, DE441 (landingMoment.json)',
      'Rotation: IAU WGCCRE lunar model with 13 libration terms (bodyOrientation.ts)',
    ],
  },
  {
    id: 'post.camera-optics',
    kind: 'camera_optics',
    title: 'Camera optics',
    evidenceClass: 'simulated',
    disclosure:
      'A simulated camera 15,500 km beyond the Moon. Modeled lens glare, chromatic aberration, streaks and vignette, then AgX; the Milky Way is shown far brighter than one exposure could record it.',
    sources: [],
  },
]);
