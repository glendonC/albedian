"""Asset provenance contract: Pydantic rules, properties, and the fixture shared with the Zod schema.

`tests/fixtures/provenance_contract.json` is written from `build_provenance_samples()` and parsed
by packages/contracts/src/__tests__/provenance.test.ts. Regenerate it after a deliberate contract
change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_provenance_contract.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from services.contracts import (
    ASSET_PROVENANCE_FORMAT,
    ASSET_PROVENANCE_FORMAT_VERSION,
    ASSET_PROVENANCE_RECORD_FILENAME,
    SPDX_LICENSE_IDS,
    AssetProvenanceRecord,
    dumps_record,
    parse_sha256sums,
    sha256_file,
)
from services.contracts import provenance as pv

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "provenance_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
CATALOG_RELEASE = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"

SAMPLE_NOTE = "Contract test sample; not a record of a real file."
# An astral character: one code point in Python, two UTF-16 code units in JavaScript
ASTRAL = "\U0001F30C"


def _digest(label: str) -> str:
    """A stand-in checksum for a sample entry: the SHA-256 of the sample's own label."""
    return hashlib.sha256(f"albedian provenance contract sample: {label}".encode()).hexdigest()


GAIA_SOURCE: dict[str, Any] = {
    "id": "gaia-dr3",
    "title": "Gaia Data Release 3",
    "url": "https://gea.esac.esa.int/archive/",
    "version": "DR3",
    "retrieved": "2026-11-03",
    "license": {
        "spdx": "CC-BY-NC-3.0-IGO",
        "name": "Creative Commons Attribution-NonCommercial 3.0 IGO",
        "url": "https://www.cosmos.esa.int/web/gaia-users/license",
        "conditions": "Commercial use needs prior authorisation from ESA.",
    },
    "credit": "ESA/Gaia/DPAC",
    "citation": "Gaia Collaboration, Vallenari, A., et al. 2023, A&A 674, A1",
    "doi": "10.1051/0004-6361/202243940",
}

ESO_SOURCE: dict[str, Any] = {
    "id": "eso-sample",
    "title": "ESO sample image",
    "url": "https://www.eso.org/public/images/",
    "retrieved": "2026-11-04",
    "license": {
        "spdx": "CC-BY-4.0",
        "name": "Creative Commons Attribution 4.0 International",
        "url": "https://creativecommons.org/licenses/by/4.0/",
    },
    "credit": "ESO",
}

NASA_SOURCE: dict[str, Any] = {
    "id": "nasa-sample",
    "title": "NASA sample image",
    "url": "https://science.nasa.gov/",
    "version": "2016",
    "license": {
        "spdx": "LicenseRef-NASA-Media-Guidelines",
        "name": "NASA Images and Media Usage Guidelines",
        "url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
    },
    "credit": "NASA",
    "sha256": _digest("nasa input"),
}

SOFTWARE_SOURCE: dict[str, Any] = {
    "id": "sample-software",
    "title": "Sample software",
    "url": "https://example.org/sample-software",
    "version": "1.0",
    "license": {
        "spdx": "BSD-3-Clause",
        "name": 'BSD 3-Clause "New" or "Revised" License',
        "url": "https://opensource.org/license/BSD-3-Clause",
    },
    "credit": "Sample Software authors",
}


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": ASSET_PROVENANCE_FORMAT,
        "formatVersion": ASSET_PROVENANCE_FORMAT_VERSION,
        "sources": [copy.deepcopy(ESO_SOURCE)],
        "files": [
            {
                "path": "sample_sky.jpg",
                "sha256": _digest("sample_sky.jpg"),
                "sources": ["eso-sample"],
                "producedBy": {"method": "downloaded-as-is"},
                "note": SAMPLE_NOTE,
            }
        ],
        "fileSets": [],
    }
    base.update(overrides)
    return base


def _with_source(**fields: Any) -> dict[str, Any]:
    source = copy.deepcopy(ESO_SOURCE)
    source.update(fields)
    return _record(sources=[source])


def _without_source_field(name: str) -> dict[str, Any]:
    source = {k: v for k, v in copy.deepcopy(ESO_SOURCE).items() if k != name}
    return _record(sources=[source])


def _with_license(**fields: Any) -> dict[str, Any]:
    source = copy.deepcopy(ESO_SOURCE)
    source["license"].update(fields)
    return _record(sources=[source])


def _with_file(**fields: Any) -> dict[str, Any]:
    record = _record()
    record["files"][0].update(fields)
    return record


def build_provenance_samples() -> dict[str, Any]:
    valid_inputs = [
        _record(),
        # A bake output: a checksum list covers the tiles, the record pins the list
        _record(
            sources=[copy.deepcopy(GAIA_SOURCE)],
            files=[],
            fileSets=[
                {
                    "checksums": "SHA256SUMS",
                    "sha256": _digest("SHA256SUMS"),
                    "sources": ["gaia-dr3"],
                    "producedBy": {
                        "method": "script",
                        "script": "scripts/bake_catalog_star_tiles.py",
                        "command": "uv run python scripts/bake_catalog_star_tiles.py --release gaia-dr3",
                    },
                    "note": SAMPLE_NOTE,
                }
            ],
        ),
        # Several sources, a licence without an SPDX id, a derived file, a nested path
        _record(
            sources=[copy.deepcopy(NASA_SOURCE), copy.deepcopy(SOFTWARE_SOURCE)],
            files=[
                {
                    "path": "maps/sample_night_8k.jpg",
                    "sha256": _digest("maps/sample_night_8k.jpg"),
                    "sources": ["nasa-sample", "sample-software"],
                    "producedBy": {"method": "script", "script": "scripts/lookdev/bake_earth_textures.py"},
                    "note": SAMPLE_NOTE,
                },
                {
                    "path": "sample_lut.png",
                    "sha256": _digest("sample_lut.png"),
                    "sources": ["sample-software"],
                    "producedBy": {"method": "script", "script": "scripts/bake_bruneton_lut_and_splats.py"},
                },
            ],
        ),
        # A credit at the limit counted in code points: the astral character counts once on both
        # sides (UTF-16 would count it twice); interior spaces are fine
        _with_source(credit=ASTRAL + "c" * (pv.CREDIT_MAX_LENGTH - 3) + " " + ASTRAL),
    ]
    valid = [AssetProvenanceRecord.model_validate(r).to_contract_dict() for r in valid_inputs]
    two_sources = _record(sources=[copy.deepcopy(ESO_SOURCE), copy.deepcopy(SOFTWARE_SOURCE)])
    duplicate_ids = _record(sources=[copy.deepcopy(ESO_SOURCE), copy.deepcopy(ESO_SOURCE)])
    duplicate_paths = _record()
    duplicate_paths["files"].append(copy.deepcopy(duplicate_paths["files"][0]))
    set_entry = {
        "checksums": "SHA256SUMS",
        "sha256": _digest("SHA256SUMS"),
        "sources": ["eso-sample"],
        "producedBy": {"method": "downloaded-as-is"},
    }
    set_shadows_file = _record(
        fileSets=[
            {
                "checksums": "sample_sky.jpg",
                "sha256": _digest("set"),
                "sources": ["eso-sample"],
                "producedBy": {"method": "downloaded-as-is"},
            }
        ]
    )
    invalid = [
        {"rule": "wrong format name", "record": _record(format="albedian.asset-sources")},
        {"rule": "unknown format version", "record": _record(formatVersion=2)},
        {"rule": "boolean format version", "record": _record(formatVersion=True)},
        {"rule": "no sources", "record": _record(sources=[])},
        {"rule": "no files and no file sets", "record": _record(files=[], fileSets=[])},
        {"rule": "missing fileSets", "record": {k: v for k, v in _record().items() if k != "fileSets"}},
        {"rule": "unknown record field", "record": _record(evidenceClass="observed")},
        {"rule": "source id outside the slug pattern", "record": _with_source(id="ESO Sample")},
        {"rule": "repeated source id", "record": duplicate_ids},
        {"rule": "source never referenced", "record": two_sources},
        {"rule": "file names an undefined source", "record": _with_file(sources=["eso-sample", "nasa-sample"])},
        {"rule": "file names a source twice", "record": _with_file(sources=["eso-sample", "eso-sample"])},
        {"rule": "file names no source", "record": _with_file(sources=[])},
        {"rule": "neither version nor retrieval date", "record": _without_source_field("retrieved")},
        {"rule": "impossible retrieval date", "record": _with_source(retrieved="2026-02-30")},
        {"rule": "retrieval date not ISO", "record": _with_source(retrieved="04/11/2026")},
        {"rule": "url not http", "record": _with_source(url="ftp://ftp.eso.org/image.jpg")},
        {"rule": "url with a space", "record": _with_source(url="https://www.eso.org/a b.jpg")},
        {"rule": "blank title", "record": _with_source(title=" ")},
        {"rule": "title with a trailing space", "record": _with_source(title="ESO sample image ")},
        # JavaScript's \s counts U+FEFF and Rust's counts U+0085; the contract lists its blanks
        {"rule": "title starting with a byte order mark", "record": _with_source(title="\ufeffESO sample image")},
        {"rule": "title ending with a next-line character", "record": _with_source(title="ESO sample image\u0085")},
        {"rule": "title ending with a zero-width space", "record": _with_source(title="ESO sample image\u200b")},
        {"rule": "url with a byte order mark", "record": _with_source(url="https://www.eso.org/\ufeffimage.jpg")},
        {"rule": "credit too long", "record": _with_source(credit="c" * (pv.CREDIT_MAX_LENGTH + 1))},
        {
            "rule": "credit one code point too long with astral characters",
            "record": _with_source(credit=ASTRAL + "c" * (pv.CREDIT_MAX_LENGTH - 2) + " " + ASTRAL),
        },
        {"rule": "doi outside the DOI pattern", "record": _with_source(doi="doi:10.1051/0004-6361/202243940")},
        {"rule": "input checksum in upper case", "record": _with_source(sha256=_digest("x").upper())},
        *(
            {"rule": f"null source {name}", "record": _with_source(**{name: None})}
            for name in ("version", "retrieved", "citation", "doi", "acknowledgement", "sha256", "note")
        ),
        {"rule": "null licence conditions", "record": _with_license(conditions=None)},
        {"rule": "unknown source field", "record": _with_source(evidenceClass="observed")},
        {"rule": "licence id not on the pinned SPDX list", "record": _with_license(spdx="CC-BY-4")},
        {"rule": "licence reference with a space", "record": _with_license(spdx="LicenseRef-NASA Media")},
        {"rule": "licence without its terms url", "record": _record(sources=[{**ESO_SOURCE, "license": {"spdx": "CC-BY-4.0", "name": "CC BY 4.0"}}])},
        {"rule": "unknown licence field", "record": _with_license(commercial=False)},
        {"rule": "path climbing out of the folder", "record": _with_file(path="../textures/sample_sky.jpg")},
        {"rule": "absolute path", "record": _with_file(path="/apps/web/public/sample_sky.jpg")},
        {"rule": "hidden file", "record": _with_file(path=".sample_sky.jpg")},
        {"rule": "empty path segment", "record": _with_file(path="maps//sample_sky.jpg")},
        {"rule": "backslash path", "record": _with_file(path="maps\\sample_sky.jpg")},
        {"rule": "the record file itself", "record": _with_file(path=ASSET_PROVENANCE_RECORD_FILENAME)},
        {"rule": "repeated path", "record": duplicate_paths},
        {"rule": "checksum list path repeats a file path", "record": set_shadows_file},
        {"rule": "file checksum too short", "record": _with_file(sha256=_digest("x")[:63])},
        {"rule": "unknown production method", "record": _with_file(producedBy={"method": "generated"})},
        {"rule": "script method without a script", "record": _with_file(producedBy={"method": "script"})},
        {"rule": "script path outside the path pattern", "record": _with_file(producedBy={"method": "script", "script": "../bake.py"})},
        {"rule": "downloaded as is with a script", "record": _with_file(producedBy={"method": "downloaded-as-is", "script": "scripts/x.py"})},
        {"rule": "null script command", "record": _with_file(producedBy={"method": "script", "script": "scripts/x.py", "command": None})},
        {"rule": "null file note", "record": _with_file(note=None)},
        {"rule": "null file set note", "record": _record(files=[], fileSets=[{**set_entry, "note": None}])},
    ]
    return {
        "format": ASSET_PROVENANCE_FORMAT,
        "formatVersion": ASSET_PROVENANCE_FORMAT_VERSION,
        "recordFilename": ASSET_PROVENANCE_RECORD_FILENAME,
        "spdxLicenseIds": list(SPDX_LICENSE_IDS),
        "patterns": {
            "id": pv.PROVENANCE_ID_PATTERN,
            "path": pv.PROVENANCE_PATH_PATTERN,
            "sha256": pv.SHA256_PATTERN,
            "httpUrl": pv.HTTP_URL_PATTERN,
            "doi": pv.DOI_PATTERN,
            "isoDate": pv.ISO_DATE_PATTERN,
            "licenseRef": pv.LICENSE_REF_PATTERN,
            "text": pv.TEXT_PATTERN,
        },
        "limits": {
            "shortTextMaxLength": pv.SHORT_TEXT_MAX_LENGTH,
            "creditMaxLength": pv.CREDIT_MAX_LENGTH,
            "textMaxLength": pv.TEXT_MAX_LENGTH,
            "urlMaxLength": pv.URL_MAX_LENGTH,
            "pathMaxLength": pv.PATH_MAX_LENGTH,
        },
        "valid": valid,
        "invalid": invalid,
    }


def test_provenance_fixture_is_current() -> None:
    samples = build_provenance_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(json.dumps(samples, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python provenance contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_provenance_contract.py"
    )


def test_every_invalid_sample_is_rejected_and_every_valid_one_round_trips() -> None:
    samples = build_provenance_samples()
    for sample in samples["valid"]:
        assert AssetProvenanceRecord.model_validate(sample).to_contract_dict() == sample
    for case in samples["invalid"]:
        with pytest.raises(ValidationError):
            AssetProvenanceRecord.model_validate(case["record"])


def test_records_serialize_deterministically_and_parse_back(tmp_path: Path) -> None:
    record = AssetProvenanceRecord.model_validate(build_provenance_samples()["valid"][2])
    text = dumps_record(record)
    assert text.endswith("}\n") and text == dumps_record(AssetProvenanceRecord.model_validate(json.loads(text)))
    # Key order follows the contract, so a rewrite of an unchanged record is byte for byte the same
    assert list(json.loads(text)) == ["format", "formatVersion", "sources", "files", "fileSets"]
    path = tmp_path / ASSET_PROVENANCE_RECORD_FILENAME
    path.write_text(text, encoding="utf-8")
    assert sha256_file(path) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_sha256sums_parser_accepts_the_bake_format_only() -> None:
    a, b = _digest("a"), _digest("b")
    assert parse_sha256sums(f"{a}  L0/0.core.bin\n{b}  manifest.json\n") == [(a, "L0/0.core.bin"), (b, "manifest.json")]
    for bad in (
        f"{a} L0/0.core.bin\n",  # one space
        f"{a} *L0/0.core.bin\n",  # binary marker
        f"{a.upper()}  L0/0.core.bin\n",
        f"{a}  ../L0/0.core.bin\n",
        f"{a}  L0/0.core.bin\n{b}  L0/0.core.bin\n",  # repeated path
        f"{a}  L0/0.core.bin\n\n{b}  manifest.json\n",  # blank line
        f"{a}  L0/0.core.bin",  # no final newline
        "",
    ):
        with pytest.raises(ValueError):
            parse_sha256sums(bad)


@pytest.mark.skipif(not (CATALOG_RELEASE / "SHA256SUMS").exists(), reason="catalog release not baked")
def test_sha256sums_parser_reads_the_real_catalog_release() -> None:
    """Real data: the committed star tile checksums parse, and sampled entries match their files."""
    text = (CATALOG_RELEASE / "SHA256SUMS").read_text(encoding="utf-8")
    entries = parse_sha256sums(text)
    assert len(entries) == len(text.splitlines()) > 5000
    paths = [p for _, p in entries]
    assert {"manifest.json", "names.json", "bright-star-merge.json", "L0/0.core.bin", "L5/99.detail.bin"} <= set(paths)
    for digest, rel in entries[:: len(entries) // 25]:
        assert sha256_file(CATALOG_RELEASE / rel) == digest, rel


def test_every_pinned_licence_id_is_an_spdx_short_identifier() -> None:
    # The pinned list holds License List ids, never LicenseRef ids, and nothing appears twice
    assert len(set(SPDX_LICENSE_IDS)) == len(SPDX_LICENSE_IDS)
    for spdx in SPDX_LICENSE_IDS:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", spdx) and not spdx.startswith("LicenseRef-")


# =============================================================================================
# PBT-PROV-1: the model accepts exactly the records the contract rules allow
# =============================================================================================

SOURCE_IDS = ("src-a", "src-b", "src-c")
PATHS = ("sky.jpg", "maps/moon_2k.jpg", "L3/17.core.bin", "SHA256SUMS", "names.json")
TEXT = st.text(alphabet="abc XYZ.,", min_size=1, max_size=40).map(str.strip).filter(bool)
LICENSES = st.one_of(
    st.sampled_from(SPDX_LICENSE_IDS),
    st.from_regex(r"LicenseRef-[A-Za-z0-9.-]{1,12}", fullmatch=True),
).map(lambda spdx: {"spdx": spdx, "name": "Some licence", "url": "https://example.org/terms"})


@st.composite
def valid_source(draw: st.DrawFn, source_id: str) -> dict[str, Any]:
    source: dict[str, Any] = {
        "id": source_id,
        "title": draw(TEXT),
        "url": draw(st.sampled_from(["https://www.eso.org/public/images/", "http://archive.example.org/x?y=1"])),
        "license": draw(LICENSES),
        "credit": draw(TEXT),
    }
    when = draw(st.sampled_from(["version", "retrieved", "both"]))
    if when in ("version", "both"):
        source["version"] = draw(TEXT)
    if when in ("retrieved", "both"):
        source["retrieved"] = draw(st.sampled_from(["2026-11-04", "2024-02-29", "1999-12-31"]))
    for name in ("citation", "acknowledgement", "note"):
        if draw(st.booleans()):
            source[name] = draw(TEXT)
    if draw(st.booleans()):
        source["doi"] = "10.5281/zenodo.8187943"
    return source


PRODUCERS = st.sampled_from(
    [
        {"method": "downloaded-as-is"},
        {"method": "script", "script": "scripts/bake_catalog_star_tiles.py"},
        {"method": "script", "script": "scripts/x.py", "command": "uv run python scripts/x.py"},
    ]
)


@st.composite
def valid_record(draw: st.DrawFn) -> dict[str, Any]:
    ids = list(SOURCE_IDS[: draw(st.integers(1, len(SOURCE_IDS)))])
    sources = [draw(valid_source(i)) for i in ids]
    paths = draw(st.lists(st.sampled_from(PATHS), min_size=1, max_size=len(PATHS), unique=True))
    split = draw(st.integers(0, len(paths)))
    entries: list[dict[str, Any]] = []
    for path in paths:
        refs = draw(st.lists(st.sampled_from(ids), min_size=1, max_size=len(ids), unique=True))
        entries.append({"sources": refs, "producedBy": draw(PRODUCERS), "sha256": _digest(path), "path": path})
    # Every source must be referenced somewhere
    for i in ids:
        if not any(i in e["sources"] for e in entries):
            entries[0]["sources"].append(i)
    files = [dict(e) for e in entries[:split]]
    file_sets = [{("checksums" if k == "path" else k): v for k, v in e.items()} for e in entries[split:]]
    return _record(sources=sources, files=files, fileSets=file_sets)


def _first_entry(record: dict[str, Any]) -> dict[str, Any]:
    return (record["files"] or record["fileSets"])[0]


def _set_entry_path(record: dict[str, Any], value: str) -> None:
    entry = _first_entry(record)
    entry["path" if record["files"] else "checksums"] = value


# Each fault breaks one rule whatever the record it is applied to
FAULTS: dict[str, Any] = {
    "format": lambda r: r.update(format="albedian.asset-sources"),
    "formatVersion": lambda r: r.update(formatVersion=2),
    "no entries": lambda r: r.update(files=[], fileSets=[]),
    "extra field": lambda r: r.update(extra=1),
    "bad source id": lambda r: r["sources"][0].update(id="Bad Id"),
    "repeated source": lambda r: r["sources"].append(copy.deepcopy(r["sources"][0])),
    "orphan source": lambda r: r["sources"].append({**copy.deepcopy(r["sources"][0]), "id": "orphan"}),
    "undefined reference": lambda r: _first_entry(r)["sources"].append("undefined-source"),
    "no date or version": lambda r: [r["sources"][0].pop(k, None) for k in ("version", "retrieved")],
    "impossible date": lambda r: r["sources"][0].update(retrieved="2025-02-29"),
    "unlisted licence": lambda r: r["sources"][0]["license"].update(spdx="GPL-2.0-or-later"),
    "padded credit": lambda r: r["sources"][0].update(credit=" ESO"),
    "blank-padded title": lambda r: r["sources"][0].update(title="\ufeff" + r["sources"][0]["title"]),
    "long credit": lambda r: r["sources"][0].update(credit="c" * (pv.CREDIT_MAX_LENGTH + 1)),
    "bad url": lambda r: r["sources"][0].update(url="www.eso.org"),
    "null doi": lambda r: r["sources"][0].update(doi=None),
    "climbing path": lambda r: _set_entry_path(r, "../sky.jpg"),
    "record file path": lambda r: _set_entry_path(r, ASSET_PROVENANCE_RECORD_FILENAME),
    "bad checksum": lambda r: _first_entry(r).update(sha256="0" * 63),
    "no references": lambda r: _first_entry(r).update(sources=[]),
    "unknown method": lambda r: _first_entry(r).update(producedBy={"method": "copied"}),
}


@st.composite
def record_with_fault(draw: st.DrawFn) -> tuple[dict[str, Any], str | None]:
    record = draw(valid_record())
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS))))
    if fault is not None:
        FAULTS[fault](record)
    return record, fault


@settings(max_examples=300, deadline=None)
@given(case=record_with_fault())
@example(case=(build_provenance_samples()["valid"][1], None))  # file sets only, no files
@example(case=(build_provenance_samples()["valid"][2], None))  # a LicenseRef licence and a nested path
def test_pbt_prov_1_model_accepts_exactly_the_records_the_rules_allow(case: tuple[dict[str, Any], str | None]) -> None:
    """PBT-PROV-1 (Python half): validation succeeds exactly when no rule is broken."""
    record, fault = case
    try:
        parsed = AssetProvenanceRecord.model_validate(record)
    except ValidationError:
        assert fault is not None, "a record that keeps every rule was rejected"
        return
    assert fault is None, f"a record with the fault {fault!r} was accepted"
    # Round trip: the wire form parses back to the same record
    assert AssetProvenanceRecord.model_validate(parsed.to_contract_dict()) == parsed
