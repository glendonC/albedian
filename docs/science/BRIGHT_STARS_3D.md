# Bright stars in 3D (Hipparcos 2007)

`apps/web/public/catalog/stars/hipparcos-bright-3d/stars.json` holds the 226 stars the G 12 star tiles take from
Hipparcos rather than Gaia DR3, at full precision, for a 3D flight. It is baked by `scripts/bake_bright_stars_3d.py`
into the bright-star supplement contract (`packages/contracts/src/bright-stars.ts`, mirrored by
`services/contracts/bright_stars.py`). `evidenceKind` is `catalog-derived`: every value is a catalogue value, none
measured by this app, and none is ever an `observed` or `catalog_matched` finding in a user's recording.

The tiles (`apps/web/public/catalog/stars/gaia-dr3/`, see `docs/science/CATALOG_STAR_TILES.md`) store these stars
lossily for drawing: positions moved to J2016.0, proper motions as 16-bit floats, distance bounds as 8-bit codes. This
file keeps what the catalogue gives, with its errors, and each star's row in the tiles, so a layer can move exactly
these stars without drawing them twice and the tiles stay untouched.

## Which stars

The tile bake's merge (`bright-star-merge.json`, action `use_hipparcos`) takes a star from Hipparcos when Gaia DR3
handles it poorly. Of the 226: 131 have no Gaia DR3 best neighbour, 91 have a best neighbour brighter than G 3
(saturated), 3 a best neighbour outside the G < 12 subset, and 1 a best neighbour that is a fainter companion. 156
are brighter than Hp 3; the faintest is Hp 5.86. 151 carry an IAU name (from the tiles' `names.json`), among them
Sirius, Canopus, Rigil Kentaurus and Toliman, Arcturus, Vega, Capella, Rigel, Procyon, Achernar and Betelgeuse.

## Source and licence

| Input (in `.cache/hipparcos-bright-3d/`, registered in `data/downloads.json`) | Rows | Bytes | SHA-256 |
|---|---|---|---|
| `hipparcos_bright_3d.csv` | 226 | 28,076 | `96b8f753deb086e26e3ac0bafdf219ff3ab4b8e3dba289d8f29714e32836e631` |

- One ADQL query of the ESA Gaia archive's TAP service (`https://gea.esac.esa.int/tap-server/tap/sync`), fetched
  2026-11-09T21:13:20Z with the project's User-Agent: Hipparcos, the New Reduction (van Leeuwen 2007, A&A 474, 653,
  doi:10.1051/0004-6361:20078357; table `public.hipparcos_newreduction`) left outer joined on hip with the Hipparcos
  Catalogue (ESA 1997, The Hipparcos and Tycho Catalogues, ESA SP-1200; table `public.hipparcos`) for V, for exactly
  the 226 HIP numbers, ordered by hip. The registry and the record give the full request URL; the bake rebuilds it
  from the merge log and the test checks the two agree.
- Licence: CC BY-NC 3.0 IGO, credit ESA [verified at the ESA Space Science Archives terms and conditions,
  https://www.cosmos.esa.int/web/esdc/terms-and-conditions, read 2026-11-09: data hosted in the ESA Space Science
  Archives are distributed under CC BY-NC 3.0 IGO; commercial use needs ESA's prior authorisation]. This is the
  licence the tiles' record already gives its Hipparcos source. `stars.json` is offered under the same terms, and its
  record (`apps/web/public/catalog/stars/hipparcos-bright-3d/PROVENANCE.json`) states the licence and credit.
- Also read from the tiles' folder, whose own record says where they came from: the Gaia DR3 best neighbour and the
  merge's reason (`gaiadr3.hipparcos2_best_neighbour`, Marrese et al. 2019), and the IAU names (IAU Catalog of Star
  Names, IAU WGSN).

## Fields

Each row is a tuple in the order of `columns`:

| Column | Unit | From |
|---|---|---|
| `hip` | | Hipparcos number |
| `name` | | IAU name from `names.json`, or null |
| `raDeg`, `decDeg` | degree | 2007 `ra`, `dec`: ICRS at epoch J1991.25 |
| `raErrorMas`, `decErrorMas` | mas | 2007 `e_ra_rad` (on RA*cos(Dec)), `e_de_rad` |
| `parallaxMas`, `parallaxErrorMas` | mas | 2007 `plx`, `e_plx` |
| `pmRaCosDecMasPerYr`, `pmDecMasPerYr` | mas/yr | 2007 `pm_ra` (includes cos(Dec)), `pm_de` |
| `pmRaErrorMasPerYr`, `pmDecErrorMasPerYr` | mas/yr | 2007 `e_pm_ra`, `e_pm_de` |
| `hpMag` | mag | 2007 `hp_mag` (Hipparcos Hp) |
| `vMag` | mag | 1997 `vmag` (Johnson V), or null |
| `bMinusV` | mag | 2007 `b_v`, or null |
| `solutionType`, `components` | | 2007 `sn`, `nc` |
| `gaiaSourceId` | | the merge's Gaia DR3 best neighbour, a decimal string, or null |
| `hipparcosReason` | | the merge's reason Hipparcos wins |
| `tileLayer`, `tilePixel`, `tileIndex` | | the star's row in the tiles' core blocks |

Every value is the archive's, parsed from its text and written back unchanged; a blank cell is null (none of the 226
lacks V or B-V). The query also read the 1997 `multflag` and `ccdm`; they are not written.

## Epoch and frame

Positions are ICRS at J1991.25, Hipparcos's own epoch, and are not moved. A layer moves them with the proper motion to
the epoch it draws; the tiles hold them at J2016.0, 24.75 years later, moved linearly along the tangent plane
(`propagate_linear` in `services/science/catalog_tiles.py`). The supplement carries no radial velocity, so perspective
acceleration is left out, as the tiles leave it out.

Each tile place was found by HIP number among the rows the tiles flag as Hipparcos entries. Every one points at a row
with that HIP number, the Hp band flag and the tiles' 1 mmag code of the same Hp, at the direction the row's position
gives when moved to J2016.0: the largest offset is 0.000043 arcsec, within the 0.001 arcsec the codec keeps for
32-bit positions [tested]. Unmoved, even the slowest of the 226 sits 0.023 arcsec from its tile row, so the check can
tell. The 151 named stars sit where `names.json` puts them [tested]. A tile rebake that moves rows fails these tests.

## The parallax rule

No distance is written. The contract states the rule: a parallax p mas with standard error e mas gives a distance
only where p > 0 and e / p is at most 0.1: 1000 / p parsecs, with the 1 sigma range 1000 / (p + e) to 1000 / (p - e).
Above that threshold the inverse is a biased estimator, so no 1000 / p distance is given there; a layer that needs a
distance must choose and disclose its own estimator. A parallax at or below zero gives no distance.

In this file, 197 of the 226 stars have a distance under the rule [tested]. Of the others, 26 have e / p above 0.1,
4 of them above 0.2 (HIP 23416, 26311, 35904 and 107259), and 3 have a parallax at or below zero: HIP 26220
(-52.82 ± 33.69 mas), HIP 26224 (-27.24 ± 8.21 mas) and HIP 71502 (-27.33 ± 69.91 mas). All 29 are kept with their
values; they have no distance.

## Checks against published values

Each value below was read at its source before it went into `tests/integration/test_bright_stars_3d.py`. The journal
pages refused automated reading (aanda.org answered 403; the ADS abstract page asked for human verification), so the
arXiv texts were read.

- Sirius (HIP 32349): 379.21 ± 1.58 mas, which is van Leeuwen 2007's value as Bond et al. 2017 (ApJ 840, 70;
  arXiv:1703.10625v1, Table 5) tabulate it. The independent check is Gatewood & Gatewood 1978 (ApJ 225, 191), a
  ground-based compilation of over 300 plates, 0.3777 ± 0.0031 arcsec as the same table gives it: 1.51 mas apart, 0.43
  combined sigma. Under the rule Sirius is at 2.637 pc (2.626 to 2.648). Bond et al.'s own adopted 0.3789 ± 0.0014
  arcsec is a weighted mean that includes Hipparcos, so it does not check Hipparcos.
- Rigil Kentaurus (HIP 71683): 754.81 ± 4.11 mas. Kervella, Mignard, Merand & Thevenin 2016 (A&A 594, A107;
  arXiv:1610.06079v1, Sect. 3.1 and Table 1) find 747.17 ± 0.61 mas for the A-B pair, from the orbit and the radial
  velocities: 7.64 mas apart, 1.84 combined sigma. Under the rule Rigil Kentaurus is at 1.3248 pc (1.3177 to 1.3321);
  Kervella et al.'s 1.338 pc lies outside that 1 sigma range and inside the 2 sigma one. They note their value lies
  between the 1997 and 2007 Hipparcos values.
- Toliman (HIP 71681) and Rigil Kentaurus (HIP 71683) are one binary, but the 2007 reduction gives them two parallaxes:
  Toliman's is 796.92 ± 25.90 mas, 42.11 mas more than Rigil Kentaurus's (1.61 combined sigma) and 1.92 sigma from
  Kervella et al.'s 747.17. Placed each by its own parallax, Toliman sits about 14,400 au nearer than Rigil Kentaurus
  along the line of sight, whereas the orbit's semimajor axis is 17.592 arcsec (Kervella et al. 2016, Table 1), about
  23.5 au at their parallax. Both rows sit in tile pixel 10 of layer 0. A layer that draws the pair in 3D must choose,
  for example one distance for both from Kervella et al., and say so.

## Rebake and verify

    uv run python scripts/fetch_download.py .cache/hipparcos-bright-3d/hipparcos_bright_3d.csv
    uv run python scripts/bake_bright_stars_3d.py
    uv run python scripts/bake_bright_stars_3d.py --verify

`--verify` compares `stars.json` and `PROVENANCE.json` with a fresh bake byte for byte; that is sound here because
every value is parsed from the archive's text and written back with no arithmetic. Without the cache, the tests still
check the committed table against the contract, the merge log, the names and the tiles, and the record against the
registry and the tiles' record.
