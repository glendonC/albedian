# Dust Bricks Pilot

Roadmap Track 2 M4 (real dust), section 4 job 4. The WebGPU spike chose a ray-marched volume
for dust ([RENDERER_WEBGPU_SPIKE.md](../architecture/RENDERER_WEBGPU_SPIKE.md), section 5)
and noted that finer data needs sparse bricks. This pilot resamples a real 3D dust map onto
Cartesian bricks and measures what each layout costs and what it loses. It ships nothing: its
output stays in `.cache/dust/bricks-pilot/`, outside git.

Labels follow [AGENTS.md](../../AGENTS.md): [tested], [verified] with its source,
[measured], [inference], [proposed], [open].

## Data

| Dataset | Files used | Size | Licence | Credit |
|---|---|---|---|---|
| Edenhofer et al. 2024, A parsec-scale Galactic 3D dust map out to 1.25 kpc from the Sun, A&A, doi:10.1051/0004-6361/202347628; data v1.0 (2023-08-01), doi:10.5281/zenodo.8187943 | `mean_and_std_healpix.fits` | 3,252,715,200 bytes | CC BY 4.0 | Edenhofer, Zucker, Frank, Saydjari, Speagle, Finkbeiner and Ensslin 2024 |
| O'Neill et al. 2024, The Local Bubble is a Local Chimney: A New Model from 3D Dust Mapping; Harvard Dataverse version 2.1 (2024-10-21), doi:10.7910/DVN/INB1RB | 16 FITS files, here `ONeill2024_LocalBubble_Shell_xyz.fits` | 3,211,367,040 bytes | CC0 1.0 | O'Neill et al. 2024 |

Both datasets are cached under `.cache/dust/`, and every file matches its
provider's MD5 [tested]; SHA-256 sums are in `.cache/dust/SHA256SUMS`. The Dataverse record's
nine interactive HTML figures were not downloaded: they hold no data.

What the files hold [tested, read from their FITS headers]:

- **Edenhofer:** extinction density as float32 on HEALPix sky pixels (nside 256, nested,
  Galactic coordinates) times 516 distance shells, mean and standard deviation, 1.62 GB each.
  The shells run from 68.6 to 1,248.1 pc and are log-spaced: 0.39 pc wide at 70 pc, 1.7 pc at
  300 pc and 5.6 pc at 1 kpc. Inside 68.6 pc the map gives only an integrated column per
  direction, not a 3D density.
- **O'Neill:** the Local Bubble shell as a float32 cube of 651 cubed voxels at 2 pc, from -650
  to 650 pc on each axis (1.1 GB), plus per-direction shell properties (distance, thickness,
  peak density, A_G, surface normal, inner and outer edges, each with its uncertainty) at
  nside 256.

**Units.** Both give extinction density in ZGR23 units per parsec (Zhang, Green and Rix 2023).
A_V at 540 nm is 2.8 times the ZGR23 extinction [verified, Edenhofer et al. 2024, arXiv:2308.01295
v2, section 7]. Other bands need the ZGR23 extinction curve (doi:10.5281/zenodo.7692680). The
spike reddens with CCM89 (R_V 3.1); with this map the consistent curve is ZGR23's [inference].
Downloading that curve is deferred to the renderer dust integration.

## Method

`scripts/bake_dust_bricks.py` reads the FITS files directly (numpy memory maps and a small
header reader; no astropy) and uses the HEALPix code in `services/science/catalog_tiles.py`.

1. **Grid.** A heliocentric Galactic Cartesian grid (x toward l = 0, y toward l = 90 deg, z
   toward b = 90 deg) at 8, 4 or 2 pc, cut into bricks of 16 cubed voxels.
2. **Sampling.** Each voxel centre takes the density of the shell and HEALPix pixel that hold
   it (nearest neighbour). Centres inside 68.6 pc or beyond 1,248 pc have no 3D density and
   stay empty. A brick is *touched* when any of its centres has data.
3. **Check.** A ray from the Sun stays in one HEALPix pixel, so the map's own column along it
   is exact: each shell's density times its width. The pilot integrates 1,000 random rays
   through the bricks (nearest voxel, step a quarter voxel) and compares.
4. **Layouts.** *Threshold*: keep only bricks whose peak density exceeds a threshold.
   *Two-level*: a base of block means (one value per 4 cubed voxels) on every touched brick,
   plus the full-resolution detail of bricks whose residual (fine minus base) exceeds a
   threshold. The base keeps each block's mean, so cutting a brick's detail loses only the
   variation inside its blocks.

The tests ([tested], `tests/integration/test_dust_bricks.py`) pin the FITS reader, the
sampling geometry, brick packing and two-level means on synthetic maps, and run three checks
on the real map: its layout; that 52.8% of the column to 1.25 kpc lies at |b| < 10 deg, which
covers 18.6% of the sky in that sample (so the map is in Galactic coordinates); and that rays
through 4 pc bricks of the inner 300 pc reproduce the map's columns (median error 2.5%, 95th
percentile 16%).

## Results [measured]

Reproduce with `uv run python scripts/bake_dust_bricks.py --voxel 8 4 2 --rays 1000`; it took
365 s on an Apple M5 Max and wrote `.cache/dust/bricks-pilot/summary.json`. Column
errors are relative to the map's exact column along each ray. Bytes are float16 density, one
value per voxel. For scale, the median A_V to 1,248 pc over 1,500 random directions is 0.16 mag
(maximum 4.2 mag).

**Every touched brick:**

| Voxel | Bricks | Size | Build time | Peak memory | Column error, median / 95th percentile |
|---|---|---|---|---|---|
| 8 pc | 4,824 | 40 MB | 6 s | 1.8 GB | 3.4% / 26% |
| 4 pc | 34,752 | 285 MB | 29 s | 1.9 GB | 2.0% / 15% |
| 2 pc | 262,112 | 2,147 MB | 247 s | 2.7 GB | 1.1% / 9.3% |

**Threshold (keep bricks whose peak exceeds it), 4 pc:**

| Threshold (ZGR23/pc) | Bricks | Size | Column error, median / 95th percentile | A_V lost, median / max |
|---|---|---|---|---|
| 1e-4 | 8,722 | 71 MB | 18% / 84% | 0.023 / 0.061 mag |
| 3e-4 | 6,142 | 50 MB | 27% / 96% | 0.035 / 0.11 mag |
| 1e-3 | 4,038 | 33 MB | 37% / 100% | 0.048 / 0.24 mag |
| 3e-3 | 2,417 | 20 MB | 52% / 100% | 0.067 / 0.82 mag |

At 2 pc the same thresholds lose more: a 22% median column error at 1e-4 (405 MB) and 77% at
3e-3 (65 MB).

**Two-level (block-mean base plus detail where the residual exceeds the threshold):**

| Detail voxel | Base voxel | Residual threshold | Detail bricks | Size with base | Column error, median / 95th percentile |
|---|---|---|---|---|---|
| 8 pc | 32 pc | none (base only) | 0 | 0.6 MB | 14% / 56% |
| 8 pc | 32 pc | 1e-4 | 1,461 | 13 MB | 3.8% / 26% |
| 4 pc | 16 pc | none (base only) | 0 | 4.4 MB | 8.1% / 39% |
| 4 pc | 16 pc | 1e-3 | 3,290 | 31 MB | 3.1% / 16% |
| 4 pc | 16 pc | 3e-3 | 1,792 | 19 MB | 3.9% / 20% |
| 2 pc | 8 pc | none (base only) | 0 | 34 MB | 4.2% / 23% |
| 2 pc | 8 pc | 1e-3 | 10,694 | 121 MB | 2.1% / 12% |
| 2 pc | 8 pc | 3e-3 | 4,385 | 69 MB | 2.5% / 14% |

**O'Neill shell** at its native 2 pc: 68,921 bricks of 16 cubed. 1,376 have a peak above 1e-4
ZGR23/pc (11 MB as float16), 705 above 1e-3 (5.8 MB), 427 above 3e-3 (3.5 MB). Bricked in 2 s.

## Findings

1. **Diffuse dust carries much of the column, so a peak threshold is the wrong cut.** A brick
   whose peak is low still holds dust along a long path. Dropping those bricks loses a median
   18% of the column at 4 pc even at the lowest threshold, and the whole column of many
   high-latitude rays (95th percentile errors reach 100%). [measured]
2. **A two-level layout keeps the column at a fraction of the size.** At 4 pc, a 16 pc base
   plus detail where the residual exceeds 1e-3 takes 31 MB, a ninth of the full 285 MB, for a
   3.1% median error against 2.0%. At 2 pc, an 8 pc base plus detail above 3e-3 takes 69 MB,
   a thirtieth of 2.1 GB, for 2.5% against 1.1%. [measured]
3. **The pilot's errors are upper bounds for the renderer.** The 95th percentile errors (9 to
   26%) come from nearest-voxel sampling of thin structures; the GPU's trilinear filtering
   should do better. [inference, not measured]
4. **The shell model is different.** O'Neill's shell is zero away from the bubble wall, so a
   threshold drops empty space rather than diffuse dust. 11 MB holds every brick above 1e-4.
   [measured]
5. **Baking is cheap.** The 2 pc build of the whole 1.25 kpc sphere takes about 4 minutes and
   2.7 GB of memory; the whole pilot peaked at 5.2 GB. [measured]

## Recommendation for production dust baking [proposed]

- Bake Edenhofer as two levels: a 16 pc base for the whole 1.25 kpc sphere and 4 pc detail
  bricks where the residual exceeds 1e-3, about 31 MB of float16, on the scale of the spike's
  64 MB test grid, which ray-marched in 1.85 ms at half resolution at 4K. Whether 31 MB
  marches as fast depends on how the renderer walks bricks [open].
- Add 2 pc detail later where it shows (the solar neighbourhood, Orion, Taurus), sized by
  this pilot's 2 pc rows.
- Bake the O'Neill shell at 2 pc with a peak threshold (about 11 MB), to replace the
  GLB-derived Local Bubble splats.
- Keep the standard deviation as an optional second channel for the inspector, at the same
  layout and twice the bytes.
- Render reddening with the ZGR23 curve rather than CCM89 once that curve is integrated.

## Not covered

- GPU frame time for these layouts, trilinear sampling, and level-of-detail transitions.
- Dust beyond 1.25 kpc: Bayestar19 and the 15.7 GB 2 pc Edenhofer grid need a yes first.
- The 68.6 pc inner sphere: the map's integrated inner column could attenuate light per
  direction, but it has no 3D shape.
