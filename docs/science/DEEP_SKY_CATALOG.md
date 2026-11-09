# Deep-sky catalogue (OpenNGC)

`apps/web/public/catalog/deepsky/openngc.json` is OpenNGC v20260501, the open NGC and IC database by Mattia Verga
(github.com/mattiaverga/OpenNGC), baked by `scripts/bake_deepsky_openngc.py` into the deep-sky catalogue contract
(`packages/contracts/src/deep-sky.ts`, mirrored by `services/contracts/deep_sky.py`). It gives the voice and text
parsers, and any layer that names a galaxy, cluster or nebula, one registered source for names, positions and sizes.

The southern-sky coverage audit (data-7, 2026-11-08) found no deep-sky table baked at all: the Magellanic Clouds,
the Carina Nebula, Omega Centauri, 47 Tucanae, the Jewel Box and the Coalsack had stars in the G 12 tiles and light
in the ESO panorama, but no name or position entry. Every one of them is a row here.

## Source and licence

| Input (in `.cache/m2/`, registered in `data/downloads.json`) | Upstream at tag v20260501 | Rows | Bytes | SHA-256 |
|---|---|---|---|---|
| `openngc_v20260501_NGC.csv` | `database_files/NGC.csv` | 13,969 | 3,876,288 | `840fe0c9ee1332e551b2e722a0e92726cd7b157914a3d2177602832aadd3aa9e` |
| `openngc_v20260501_addendum.csv` | `database_files/addendum.csv` | 64 | 17,484 | `1d8f0914e643ada325a5a94d88d8fefad6a4937a2f77cc34f21483af22b11983` |

- Licence: CC-BY-SA-4.0 for every file of the repository [verified: `.reuse/dep5` at tag v20260501 reads
  `Files: *` with `License: CC-BY-SA-4.0`; `LICENSES/CC-BY-SA-4.0.txt`; the README says OpenNGC "is released under
  CC-BY-SA-4.0 license"].
- Copyright 2023 Mattia Verga [verified: `.reuse/dep5` at tag v20260501 reads `Copyright: 2023 Mattia Verga`].
- Citation: the README at tag v20260501 gives the DOI `10.21938/y.1ejWUD_MQ6b_eDFoVbbw` (a VO DOI from GAVO,
  dc.zah.uni-heidelberg.de) [verified]. The record carries it, and the README's acknowledgements of NED,
  HyperLeda and SIMBAD, whose data OpenNGC merges.
- Obligations: credit OpenNGC and Mattia Verga, link the licence, and say what was changed. `openngc.json` is an
  adaptation, so it is shared under CC-BY-SA-4.0 too, and so is anything that adapts it further. The record
  (`apps/web/public/catalog/deepsky/PROVENANCE.json`) states both.
- Both files were fetched from `raw.githubusercontent.com/mattiaverga/OpenNGC/v20260501/database_files/` at
  2026-11-09T04:09:06Z and matched the copies already cached byte for byte [tested].

## Fields

`evidenceKind` is `catalog-derived`: positions, sizes and magnitudes come from the literature OpenNGC compiles
(NED, SIMBAD, HyperLeda, Corwin's NGC/IC positions, HEASARC tables), none measured by this app. They are never
`observed` or `catalog_matched` findings in a user's recording.

Each row is a tuple in the order of `columns`:

| Column | From OpenNGC | Notes |
|---|---|---|
| `name` | `Name` | Unique. Catalogue plus number (`NGC0104`, `IC1064`, `ESO056-115`, `C099`); NED components keep their suffix (`IC0080 NED01`). |
| `type` | `Type` | One of 21 codes; `types` gives OpenNGC's own label for each (NGC_guide.txt). |
| `raDeg`, `decDeg` | `RA`, `Dec` | J2000, converted from sexagesimal and rounded to 1e-6 degree. Null only on a nonexistent object (`NonEx`, 7 rows). |
| `constellation` | `Const` | IAU abbreviation; Serpens is `Se1` (Caput) or `Se2` (Cauda) as OpenNGC writes it. Null exactly when the position is. |
| `majorAxisArcmin`, `minorAxisArcmin` | `MajAx`, `MinAx` | Arcmin. A minor axis appears only with a major axis and is never the larger. |
| `positionAngleDeg` | `PosAng` | Major axis, north through east, 0 to 180. |
| `bMag`, `vMag` | `B-Mag`, `V-Mag` | Apparent total magnitudes. |
| `surfaceBrightness` | `SurfBr` | Galaxies only: mean B surface brightness within the 25 mag isophote, mag/arcsec2. |
| `messier` | `M` | 1 to 110. Each number belongs to one row that is not a duplicate; M102 is a `Dup` row that carries 101 (OpenNGC's reading of the M101/M102 question), so no row carries 102. |
| `ngc`, `ic` | `NGC`, `IC` | Cross references as OpenNGC writes them (`0281`, `5195A`, `0190 NED01`). A `Dup` row names its object here or in `messier`. 74 of these point at components that are not rows themselves. |
| `identifiers`, `commonNames` | `Identifiers`, `Common names` | Split at commas, items stripped, empty items dropped (in v20260501: 16 items stripped and 1 empty item dropped, all in `Identifiers`). |

A blank OpenNGC cell is null or an empty list, never filled.

## Changes from the source, and what is left out

- Positions converted from sexagesimal to degrees; list cells split; every other value kept as OpenNGC writes it.
- Columns not carried: J, H and K magnitudes, Hubble type, parallax, proper motion, radial velocity, redshift,
  central-star magnitudes and names, NED notes, OpenNGC notes and sources. A later version can add them under the
  same licence.
- `Dup` and `NonEx` rows are kept, so a parser can redirect a duplicate name to its object and say plainly that a
  nonexistent one does not exist, instead of guessing. Every target of all 652 duplicates is a row of the table, and
  each duplicate names at least one that is not itself a duplicate [tested]. Three also name another duplicate
  (IC0843 and IC4088 each other, NGC5620 IC1005), so a redirect should take the target that is not a duplicate.

## Checks

`tests/integration/test_deepsky_openngc.py` [tested]:
- the committed table parses under the contract, is one line, and holds 14,033 rows, 110 Messier values (every number
  from 1 to 110 but 102, and 101 twice: on M101 and on the duplicate row M102), all 21 types and all 89 constellation
  codes;
- the 16 real rows the contract tests use are the committed rows, and each southern showpiece carries its common
  name;
- every duplicate's targets resolve to rows, one of them a row that is not a duplicate;
- cross-catalogue: around OpenNGC's centres of Omega Centauri and 47 Tucanae, the committed Gaia DR3 G 12 tiles are
  at least five times denser within 15 arcmin than 30 to 60 arcmin out;
- the record names both inputs with their checksums, CC-BY-SA-4.0 and the share-alike condition; the registry
  agrees with the pins;
- where the CSVs are cached, the list cells tidied are exactly the 17 counted above, and the rebake is byte-identical (`uv run python scripts/bake_deepsky_openngc.py --verify`).

## Limits

- OpenNGC is a compilation; a position or size is as good as the source it took it from, and the table does not say
  which source each value came from (OpenNGC's `Sources` column, left out here) [open].
- A large object's centre and axes (the Clouds, the Carina Nebula) describe an outline, not its true shape; drawing
  one as an ellipse is a simplification a layer must disclose.
- The Coalsack's row is a position and a name only: OpenNGC gives it no size.
