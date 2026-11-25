# Asset Provenance

Every file the web app serves from `apps/web/public/` says where it came from, which version
or when it was retrieved, under which licence, with what credit line, its SHA-256, and either
the script in this repository that made it or that it was downloaded as is (roadmap Track 2
M1). The credits panel and every layer's disclosure read these records, so each credit a
licence asks for can ship visibly in the app.

## The record

One `PROVENANCE.json` per asset folder describes the files beside it; a path may reach into a
subfolder. The format is a contract: `AssetProvenanceRecordSchema` in
`packages/contracts/src/provenance.ts`, mirrored by `AssetProvenanceRecord` in
`services/contracts/provenance.py`, with shared samples in
`tests/fixtures/provenance_contract.json` that both sides parse.

```json
{
  "format": "albedian.asset-provenance",
  "formatVersion": 1,
  "sources": [
    {
      "id": "eso0932a",
      "title": "The Milky Way panorama",
      "url": "https://www.eso.org/public/images/eso0932a/",
      "version": "Large JPEG, 6000x3000",
      "license": { "spdx": "CC-BY-4.0", "name": "Creative Commons Attribution 4.0 International", "url": "https://creativecommons.org/licenses/by/4.0/" },
      "credit": "ESO/S. Brunier"
    }
  ],
  "files": [
    { "path": "eso_milkyway_panorama.jpg", "sha256": "...", "sources": ["eso0932a"], "producedBy": { "method": "downloaded-as-is" } }
  ],
  "fileSets": []
}
```

- **sources**: each upstream dataset, image or program, with its URL, a `version` or a
  `retrieved` date (at least one), its licence, the credit line to show, and optionally a
  citation, DOI, acknowledgement, the input's SHA-256 and a note.
- **files**: one entry per file, pinned by SHA-256, naming its sources and how it was made:
  `{"method": "downloaded-as-is"}` or `{"method": "script", "script": "<repo path>", "command": "...",
  "changes": "..."}`.
- **fileSets**: a `SHA256SUMS` list beside the record (one `<sha256>  <relative path>` line per
  file) pinned by its own SHA-256. A bake that writes thousands of files, such as the catalog
  star tiles, records them through one list instead of one entry each.

Assets made in this repository cite their producing script by its repository-relative path in
`producedBy.script`, never the repository's URL.

**Changes.** A file a script made is a changed copy of its sources, so `producedBy.changes` says
how, in plain words, from what the script does to the sources' bytes: a subset, a reformatting,
a join, a computation. It is required on the script producer and absent on a downloaded file.
The script holds the sentence as a named constant and writes it into its record (or, for a
record kept by hand, the constant states it); `tests/integration/test_provenance_changes.py`
checks that every entry's sentence is a constant of the script that made it.

**Licences that ask for it.** `packages/contracts/src/data/license-change-terms.json`, read by
both contracts (`license_asks_changes_indicated` and `licenseAsksChangesIndicated`), lists the
licences whose terms ask that changes be indicated, each with its clause quoted from its own
page and that page's address, and those read and found not to ask. A licence it does not know
is treated as asking, so the web's Credits rows can show the changes line where a licence asks.

**Licences.** `spdx` is an identifier from the pinned SPDX License List subset
(`SPDX_LICENSE_IDS`: CC-BY-4.0, CC-BY-SA-4.0, CC-BY-NC-4.0, CC-BY-NC-3.0-IGO, CC0-1.0,
ODbL-1.0, MIT, Apache-2.0, BSD-3-Clause, OFL-1.1), or `LicenseRef-<name>` with a URL to the
terms for anything the list lacks (NASA's media guidelines, for example), as SPDX itself
prescribes. Add an identifier only after checking it at https://spdx.org/licenses/, on both
sides of the contract, with the fixture regenerated.

**Text.** No field starts or ends with whitespace or an invisible format character (the
contract lists them, because JavaScript and Python regex engines disagree on some), lengths
count Unicode code points, and an optional field is left out rather than set to null.

## Writing a record

A bake script builds an `AssetProvenanceRecord` and writes it with `dumps_record`, which gives
the same bytes for the same record every time; `sha256_file` and `parse_sha256sums` come from the
same module. A bake that replaces its whole output folder must write the record itself, or the
next bake deletes it.

## The records

- **`catalog/stars/gaia-dr3/PROVENANCE.json`**, written by `scripts/bake_catalog_star_tiles.py`
  on every bake and compared by its `--verify`. One file set, `SHA256SUMS`, covers all 7,671
  files of the release; the record names its six sources (Gaia DR3 and its astrophysical
  parameters, Bailer-Jones et al. 2021 distances, Hipparcos 2007, the Gaia to Hipparcos
  crossmatch and the IAU Catalog of Star Names), each with its credit and licence.
- **`models/PROVENANCE.json`**, written by `scripts/bake_local_bubble_mesh.py` on every bake and
  compared by its `--verify`, covers `local_bubble_oneill2024.glb` from O'Neill et al. (2024,
  ApJ 973, 136; Harvard Dataverse `doi:10.7910/DVN/INB1RB`, `CC0-1.0`).
- **`textures/PROVENANCE.json`** covers the ESO panorama (eso0932a), downloaded as is and
  byte-identical to ESO's download; the two 1k
  SVS 4720 Moon maps, likewise; the 8k Moon colour map and the LOLA normal map, whose entries
  `scripts/bake_moon_maps.py` rewrites on every bake from two pinned SVS 4720 inputs; the Earth
  day and night maps, which
  `scripts/lookdev/bake_earth_textures.py` makes from pinned NASA inputs; the panorama's
  registration, which `scripts/register_sky_panorama.py` fits; and the Natural Earth water mask,
  NASA cloud layer, ETOPO 2022 height map and unshaded Blue Marble day map, whose entries
  `scripts/bake_earth_maps.py` rewrites on every bake. A script that changes one of these files
  must update its entry, or the check fails.
- **`fonts/geist/PROVENANCE.json`** covers `Geist-wght.woff2`, `GeistMono-wght.woff2` and
  `OFL.txt`, downloaded as is from Vercel's Geist release `v1.9.2` (`OFL-1.1`) and pinned in
  `data/downloads.json`.

NASA states its terms as media guidelines rather than a licence, so NASA sources carry
`LicenseRef-NASA-Media-Guidelines` with the guidelines' URL. The IAU's star names file says
only "Creative Commons Attribution", with no version, so it carries
`LicenseRef-IAU-Creative-Commons-Attribution` with the file's URL.

## The check

`uv run python scripts/verify_provenance.py` (or `pnpm verify:provenance`) looks at every
file git would publish under `apps/web/public/`:
tracked files, and untracked files that are not ignored, so a new asset fails before it is
committed. It fails when:

- a file has neither a record nor an allowlist entry;
- a record does not follow the contract, names a file that is not there, or names a producing
  script that does not exist;
- a file a script made does not say how it differs from its sources; the refusal names the
  licences among its sources whose terms ask that changes be indicated, from the table;
- a file's bytes, or a SHA256SUMS list's, differ from what its record pins;
- a file is covered twice.

Gitignored files under the folder are counted and left out, because git never publishes them.
A local `pnpm build` still copies them into `dist/`; this check does not look there.

`uv run pytest` runs the check on this repository (`test_this_repository_passes` in
`tests/integration/test_asset_provenance.py`), so local test suites and CI's Python jobs
enforce it. Hashing all 7,698 files (226 MB) took 0.8 s on an Apple M5 Max development
machine [measured].

## Known gaps

`scripts/provenance_gaps.json` lists the files that have no record yet, each with its reason.
It may only shrink. `BASELINE_GAPS` in the check pins, by path and SHA-256, the legacy files
that still lack a provenance record, and an entry fails when:

- its path is not in the baseline (a new asset needs a record, not a gap);
- its file has a record now, or is gone (remove the entry);
- its bytes changed (a changed asset needs a record).

`BASELINE_GAPS` pins one entry, the one open gap: the atmosphere lookup table
(`textures/bruneton_atmo_lut.png`, which a script makes from hand-set parameters with no outside
data). `models/lb_splats.json` left the gaps when it was rebaked from the O'Neill et al. (2024) shell
table with a record (docs/science/LOCAL_BUBBLE_SPLATS.md), and `models/nasa_lro_moon_3d.glb` when it was
baked from LOLA heights at true scale with a record (docs/science/MOON_MESH.md). Any deleted
legacy asset is removed from `BASELINE_GAPS` at the same time so it can return only with a
provenance record.

The WebGPU spike's two sample splats, `snow-street.spz` and `valley.spz`, left the tracked tree
because no licence is stated at their source, and so they cannot carry a record. They live only in
the shared, untracked `.cache/splats/` for local spike runs, with the bytes their baseline pins
named. Deleting a file from the tree does not remove it from the repository: git history still
holds both splats in every commit before their removal. The same change dropped the pins of the
three legacy Earth maps that commit cbf96557 had already deleted.

To close a gap, write the file's record and delete its entry in the same change.
