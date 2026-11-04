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
      "id": "eso1804a",
      "title": "Star formation region Lupus 3",
      "url": "https://www.eso.org/public/images/eso1804a/",
      "version": "screen-size JPEG",
      "license": { "spdx": "CC-BY-4.0", "name": "Creative Commons Attribution 4.0 International", "url": "https://creativecommons.org/licenses/by/4.0/" },
      "credit": "ESO/R. Colombari"
    }
  ],
  "files": [
    { "path": "eso1804a.jpg", "sha256": "...", "sources": ["eso1804a"], "producedBy": { "method": "downloaded-as-is" } }
  ],
  "fileSets": []
}
```

- **sources**: each upstream dataset, image or program, with its URL, a `version` or a
  `retrieved` date (at least one), its licence, the credit line to show, and optionally a
  citation, DOI, acknowledgement, the input's SHA-256 and a note.
- **files**: one entry per file, pinned by SHA-256, naming its sources and how it was made:
  `{"method": "downloaded-as-is"}` or `{"method": "script", "script": "<repo path>", "command": "..."}`.
- **fileSets**: a `SHA256SUMS` list beside the record (one `<sha256>  <relative path>` line per
  file) pinned by its own SHA-256. A bake that writes thousands of files, such as the catalog
  star tiles, records them through one list instead of one entry each.

Assets made in this repository cite their producing script by its repository-relative path in
`producedBy.script`, never the repository's URL.

**Licences.** `spdx` is an identifier from the pinned SPDX License List subset
(`SPDX_LICENSE_IDS`: CC-BY-4.0, CC-BY-SA-4.0, CC-BY-NC-4.0, CC-BY-NC-3.0-IGO, CC0-1.0,
ODbL-1.0, MIT, Apache-2.0, BSD-3-Clause), or `LicenseRef-<name>` with a URL to the terms for
anything the list lacks (NASA's media guidelines, for example), as SPDX itself prescribes. Add
an identifier only after checking it at https://spdx.org/licenses/, on both sides of the
contract, with the fixture regenerated.

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
- **`textures/PROVENANCE.json`**, kept by hand, covers eight files: the ESO panorama (eso0932a)
  and the two ESO nebula images (eso1006a, eso1804a), downloaded as is and byte-identical to
  ESO's downloads; the two SVS 4720 Moon maps, likewise; the Earth day and night maps, which
  `scripts/lookdev/bake_earth_textures.py` makes from pinned NASA inputs; and the panorama's
  registration, which `scripts/register_sky_panorama.py` fits. A script that changes one of
  these files must update its entry, or the check fails.

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
- a file's bytes, or a SHA256SUMS list's, differ from what its record pins;
- a file is covered twice.

Gitignored files under the folder are counted and left out, because git never publishes them.
A local `pnpm build` still copies them into `dist/`; this check does not look there.

`uv run pytest` runs the check on this repository (`test_this_repository_passes` in
`tests/integration/test_asset_provenance.py`), so every lane's gates and CI's Python jobs
enforce it. On 2026-11-04 it hashed 7,698 files (226 MB) in 0.8 s on the development
machine [measured].

## Known gaps

`scripts/provenance_gaps.json` lists the files that have no record yet, each with its reason.
It may only shrink. `BASELINE_GAPS` in the check pins every file that lacked a record when the
check arrived, by path and SHA-256, and an entry fails when:

- its path is not in the baseline (a new asset needs a record, not a gap);
- its file has a record now, or is gone (remove the entry);
- its bytes changed (a changed asset needs a record).

The list started with 16 entries: six textures (the atmosphere lookup table, which a script
makes from hand-set parameters with no outside data; three Earth maps that match three.js
example textures or are unidentified; two files due for deletion), all seven files in
`models/`, and the three tracked splats.

To close a gap, write the file's record and delete its entry in the same change.
