# Local Bubble splats

`apps/web/public/models/lb_splats.json` holds one Gaussian splat per vertex of the Local Bubble mesh
(`local_bubble_oneill2024.glb`, 27,648 vertices), baked by `scripts/bake_local_bubble_splats.py` from the same table
the mesh comes from. It replaced a legacy file made from every other vertex of a mesh of unrecorded origin, with
footprints from tuned constants and one warm colour; that file was a provenance gap and is gone.

## Source and licence

- O'Neill, T. J., Zucker, C., Goodman, A. A., and Edenhofer, G. 2024, ApJ, 973, 136
  (doi:10.3847/1538-4357/ad47b5; arXiv:2403.04961): the Local Bubble's shell found as the first extinction peak along
  each line of sight in the Edenhofer et al. (2024) 3D dust map.
- Table: `ONeill2024_LocalBubble_ShellProperties_A0.5.fits`, Harvard Dataverse doi:10.7910/DVN/INB1RB, CC0, 786,432
  HEALPix nside 256 directions (40 left blank by the authors), registered in `data/downloads.json` and recorded in
  `apps/web/public/models/PROVENANCE.json`.
- Columns used [verified against the paper, section II.3 and Table 1, read 2026-11-09]: the peak distance `d`, the
  thickness `thick` (d_outer - d_inner, measured along the line of sight), the shell normal `nx, ny, nz`, and `Ag`,
  the integrated G-band extinction through the shell (A_G, in magnitudes) along each line of sight. The paper's
  inclination `gamma` is the angle between the normal and the line of sight: arccos|n . r| matches it within 0.6
  degree [tested].

## What each value is

Each row is `[x, y, z, r, g, b, sx, sy, sz, qx, qy, qz, qw, opacity]` in heliocentric Galactic Cartesian
coordinates, 1 unit = 100 pc, the layout `apps/web/src/spatial/splatRows.ts` reads. Within each of the 27,648 nside 48
cells the table rows whose nside 256 directions fall in it, 19 to 36 finite rows (median 30) [tested], are averaged,
as the mesh averages `d`.

| Value | Kind | How |
|---|---|---|
| centre | measured | the mesh vertex, at the cell's mean peak distance d, written as the shortest decimal that reads back as the same float32; the slab is centred there, although d_inner and d_outer need not be symmetric about d |
| orientation | measured | the splat's third axis along the cell's mean shell normal; each table normal is first turned to point away from the Sun, because the table's sign is arbitrary (48% of its normals point inward) |
| thickness along the normal | measured, projected | the table's thickness is along the line of sight; each row's thick times \|n . r\| puts it along the normal, and the cell's mean is drawn as a uniform slab's sigma, thickness / sqrt(12) |
| footprint across the normal | grid geometry, a presentation value | the cell's side, sqrt(4 pi / 27,648) times the vertex distance, as a slab sigma, side / sqrt(12); not a measurement |
| opacity | presentation mapping of a measured quantity | 1 - 10^(-0.4 A_G), the fraction of G-band light the shell absorbs along the line of sight from the Sun, with A_G the cell's mean |
| colour | simulated | one neutral grey (0.8, 0.8, 0.8) for every splat: dust extinction is not emission, so no colour stands for an emission line |

Measured on the baked file [tested]: shell distance 73 to 551 pc (median 171); thickness along the normal 0.8 to 213
pc (median 30.6); cell side 1.6 to 11.7 pc (median 3.6), so a splat is about nine times longer along the normal than
across it, as the shell is thicker than the grid is fine; opacity 0.0008 to 0.754 (median 0.019).

## Limits

- The opacity is the absorbed fraction along the line of sight from the Sun. A splat seen from elsewhere, at another
  angle through the shell, would absorb a different fraction; the value is a presentation mapping, not a radiative
  transfer result, and a layer drawing it must say so.
- 2,758 splats (10%) have opacity below 1/255, the smallest step of an 8-bit opacity, and may not draw at all where a
  renderer stores opacity in 8 bits. They are where the shell absorbs least.
- The colour is simulated. The table has no emission measure; a layer must disclose the grey as drawn by the app.
- The shell is the first extinction peak at the A'_0.5 threshold, as the paper defines it; the paper also gives
  a table at A'_0.9 and uncertainties from twelve map draws, which are not used here.
- Positions use the mesh's frame and scale. `LOCAL_BUBBLE_POSITION_SCALE` and the cavity offset in the web app were set
  for the legacy positions and are the render lane's to re-check.

## Checks

`tests/integration/test_local_bubble_splats.py` [tested]: a made-up two-cell table becomes exactly its splats (the
outward turn, the projection by |n . r|, the slab factor, the 0.4 of the opacity, the third axis), without the table;
every centre is its mesh vertex as float32; the colour is one grey; every orientation points away from the Sun; the
cross-normal footprint is the cell side as a slab sigma; thickness and opacity stay within the paper's ranges; the record states what is measured, what is a presentation
mapping and what is simulated; the gap is closed. Where the table is cached, each orientation, thickness and opacity
equals the table's cell mean within half a rounding step, and `uv run python scripts/bake_local_bubble_splats.py
--verify` passes. Values are compared within a stated tolerance, never byte for byte.
