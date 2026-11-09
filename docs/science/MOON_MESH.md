# Moon mesh

`apps/web/public/models/nasa_lro_moon_3d.glb` is the Moon's shape for the WebGL landing, baked by
`scripts/bake_moon_mesh.py` from LOLA heights at true scale. It replaced a Blender mesh of unrecorded origin whose
relief was about 2.4 times the real range, and which carried an embedded image with no record; that gap is closed.

## Source and licence

- `ldem_16_uint.tif` from NASA's Scientific Visualization Studio, CGI Moon Kit (SVS 4720): Lunar Orbiter Laser
  Altimeter (LOLA) heights, 5760 x 2880, 16 samples per degree, unsigned half-metres above a 1727.4 km sphere.
  Registered in `data/downloads.json`, pinned by SHA-256 in `scripts/bake_moon_maps.py`, and recorded in both
  `apps/web/public/textures/PROVENANCE.json` and `apps/web/public/models/PROVENANCE.json` under the same source entry.
- Licence: NASA Images and Media Usage Guidelines (`LicenseRef-NASA-Media-Guidelines`): credit NASA, imply no
  endorsement.
- SVS says the kit's maps are made for appearance, not science. The mesh inherits that: it is the Moon's measured
  shape as SVS prepared it, not a geodetic product.

## What the mesh is

- A latitude-longitude sphere on a 1 degree grid: 361 x 181 = 65,341 vertices and 128,880 triangles (each pole band
  has one triangle per column, the rest two), about 2.9 MB, POSITION, NORMAL and TEXCOORD_0 only, no material or
  image.
- Each vertex's radius is 1727.4 km plus 0.5 m times the mean of the 16 x 16 LOLA samples in the 1 degree box
  centred on it; at each pole, the mean of the band within half a degree of it. True scale, no exaggeration:
  10 units = 1737.4 km, the scale `apps/web/src/spatial/webgl/cislunarRegime.ts` already applies.
- Conventions, kept from the mesh it replaced [measured on that mesh's vertices]: +y is lunar north, longitude 0 lies
  toward +x and 90 E toward -z, u = (longitude + 180) / 360 and v = (90 - latitude) / 180, so the app's
  equirectangular colour and bump maps (longitude -180 at the left, north at the top) line up. The seam column is
  doubled at +-180 for continuous UVs; each pole is a row of 361 vertices at one point, of which the copy at +180
  is used by no triangle (each pole band takes the copies of columns 0 to 359). Normals are area-weighted over
  the faces round each point, with the seam's and the poles' copies sharing one normal.

Measured on the baked mesh [tested]: radius 1729.6 to 1746.1 km (median 1736.6), a 16.5 km span, under 1 percent of
the radius (the replaced mesh spanned 2.7 percent). LOLA's highest and lowest samples, at 5.41 N 158.66 W and
70.34 S 172.47 W, fall on vertices in the top and bottom 0.1 percent of radii; the box means move the single most
extreme vertex a few degrees from them (to 3 N 161 W and 61 S 170 W).

## Limits

- 1 degree is about 30 km at the equator, so craters smaller than that are averaged into their boxes; the app's bump
  map, from the same LOLA source, draws finer relief in the lighting.
- The poles and the seam are where a latitude-longitude grid is weakest: triangles crowd at the poles and the seam
  shares normals across its doubled column.
- The mesh says nothing about the Moon's place or orientation in the sky; the landing places and lights it for
  composition, as its layer discloses.

## Checks

`tests/integration/test_moon_mesh.py` [tested]: the layout and the absence of any material or image; the UVs against
each vertex's own position; true-scale radii inside what ldem_16 can hold; outward normals; LOLA's extremes on extreme
vertices; the record (source, scale, size, the SVS caveat) and the closed gap; a made-up height field for the box
means, the seam wrap and the caps. Where the heights are cached, every vertex radius equals its box mean within 1 m,
and `uv run python scripts/bake_moon_mesh.py --verify` passes. Values are compared within a stated tolerance, never
byte for byte.
