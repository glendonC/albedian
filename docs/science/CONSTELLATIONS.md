# Constellation lines and boundaries (d3-celestial)

`apps/web/public/catalog/constellations/lines.json` and `bounds.json` are the constellation figures and the IAU
constellation boundaries from d3-celestial by Olaf Frohn (github.com/ofrohn/d3-celestial) at commit
`d2e20e104b86429d90ac8227a5b021262b45d75a`, baked by `scripts/bake_constellations_d3celestial.py` into the
constellation contract (`packages/contracts/src/constellations.ts`, mirrored by `services/contracts/constellations.py`).
They give the `constellationLines` overlay, and any layer that names the constellation a point lies in, one
registered source.

The southern-sky audit (data-7, 2026-11-08) found no constellation lines or boundaries anywhere in the repo.

## What each file is

- **Lines** are the IAU constellation figures with the d3-celestial author's edits: its readme gives their source as
  the IAU constellation pages, with "name positions and some line modifications" of its own. They are one person's
  edits of the IAU figures, not an IAU product. Figures are a convention for drawing a constellation, not a
  property of the sky.
- **Boundaries** are Davenhall and Leggett (1989), Catalogue of Constellation Boundary Data (VizieR VI/49): the
  boundaries Delporte drew and the IAU adopted, along lines of right ascension and declination of equinox B1875.

`evidenceKind` is `catalog-derived` for both: nothing here is measured from a user's recording, and neither file is
an `observed` or `catalog_matched` finding.

## Source and licence

| Input (in `.cache/d3celestial/`, registered in `data/downloads.json`) | Upstream at the commit | Bytes | SHA-256 |
|---|---|---|---|
| `constellations.lines.json` | `data/constellations.lines.json` | 27,136 | `294f66bef5d5cf50b1e17f16d2efa1d97a15131612c68dd935adef6e7373e13c` |
| `constellations.bounds.json` | `data/constellations.bounds.json` | 40,714 | `f2e2687af6b20b24567879f838c21874d412efcc93ecc1966be07e78431cc196` |

- The commit, of 2020-03-20, is the last to change either file. Both were fetched from
  `raw.githubusercontent.com/ofrohn/d3-celestial/<commit>/data/` with the Albedian User-Agent [tested: sizes and
  checksums above].
- Licence: BSD-3-Clause [verified: the repository's LICENSE at the commit, read through the GitHub contents API].
  The licence asks that a redistribution keep the copyright notice, the conditions and the disclaimer, in the
  documentation or other materials. They are kept in `apps/web/public/catalog/constellations/PROVENANCE.json` and
  here, word for word:

```text
Copyright (c) 2015, Olaf Frohn
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Fields

Both files hold 89 entries, one per IAU constellation, by abbreviation, with Serpens split into Se1 (Serpens Caput)
and Se2 (Serpens Cauda) as OpenNGC writes it, so a layer can join them with the deep-sky table. Each entry has
d3-celestial's display `rank`: 1 for the 22 most prominent figures, 2 for 24, 3 for 43. A point is
`[raDeg, decDeg]`.

| File | Per entry | Equinox |
|---|---|---|
| `lines.json` | `polylines`: 1 or more polylines of 2 or more points; 893 points in all, on 757 distinct stars | `equinox: "J2000"` |
| `bounds.json` | `ring`: one closed ring (its last point is its first); 1,659 points in all | `vertexEquinox: "J2000"`, `edgeEquinox: "B1875"` |

Changed from the source, and nothing else: right ascension, which d3-celestial writes as a longitude from -180 to 180,
is wrapped into 0 to 360 at the source's 4 decimals; the rank, a string there, is an integer; and the two features
d3-celestial calls `Ser` become `Se1` (every right ascension below 250 degrees) and `Se2` (every one above).

## Boundary edges are B1875 lines: densify them

The boundary vertices are J2000 positions of corners defined at equinox B1875 [tested: precessed back with the IAU
1976 matrix in `services/science/catalog.py`, 99.6% of them land within 1 arcsec of a whole arcminute of declination
and within 0.1 s of a whole second of right ascension; as given, 2% do]. Each edge runs along a constant right
ascension or declination of B1875, which at J2000 is neither a line of constant J2000 coordinates nor a great circle.
A segment drawn straight between two J2000 vertices can sit arcminutes off the true boundary.

So a layer that draws or tests an edge must densify it along B1875 right ascension or declination: precess both
vertices to B1875, step the coordinate that changes, and precess each step back to J2000. To place a point, do as
Roman (1987) does: precess it to B1875 and test it against the B1875 edges (`tests/integration/
test_constellations_d3celestial.py` has such a test). Octans and Ursa Minor ring the south and north celestial poles,
and their rings run round all 24 hours of right ascension.

## Checks

`tests/integration/test_constellations_d3celestial.py` [tested]:
- both committed files parse under the contract, are one line each, and hold all 89 codes with the ranks above;
- the 7 real entries of each file the contract tests use are the committed entries;
- line vertices against the Gaia DR3 and Hipparcos stars of the G 12 tiles, each star moved from the tiles' epoch
  (J2016.0) back to J2000 by its own proper motion: all 757 within 20 arcsec of a star brighter than magnitude 7, and
  at least 99% within 5 arcsec (measured: median 0.14 arcsec, 99.5% within 5, worst 17.7 in Ursa Major; without the
  proper motion the worst is 52 arcsec, at Alpha Centauri);
- boundaries, placed by the B1875 edges: Acrux in Crux, Rigil Kentaurus in Centaurus and Canopus in Carina; all 410
  IAU-named stars of `names.json` in exactly the constellation the IAU lists for each; all 14,026 positioned
  OpenNGC objects of `openngc.json` in exactly the constellation OpenNGC gives each; only Octans and Ursa Minor ring a
  pole;
- the record keeps the BSD notice and says the lines are not an IAU product; the registry agrees with the pins;
- where the files are cached, the rebake is byte-identical (`uv run python scripts/bake_constellations_d3celestial.py
  --verify`).

## Limits

- The figures are a drawing convention, with one author's edits; another atlas joins some stars differently.
- A figure's points are J2000 star positions; stars move, so at another epoch a line end drifts off its star by the
  star's proper motion (52 arcsec between J2000 and the tiles' J2016.0 for Alpha Centauri [tested]).
- The boundaries are exact only along the B1875 edges described above.
