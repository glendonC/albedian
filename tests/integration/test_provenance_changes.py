"""Every file a script made says how it differs from its sources (provenance-changes-1).

- Each script-made entry of every PROVENANCE.json under apps/web/public carries producedBy.changes, and its text is a
  constant of the script that made it (read with ast; only the starless bake is run on a small made-up photograph);
  every such constant is used.
- The licence table (packages/contracts/src/data/license-change-terms.json) knows every licence the records name, and
  treats one it does not know as asking that changes be indicated.
- scripts/verify_provenance.py refuses a script-made entry that says nothing, naming the licences that ask.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import pytest

from scripts.git_env import git_environment
from services.contracts.provenance import (
    LICENSE_CHANGE_TERMS,
    SPDX_LICENSE_IDS,
    AssetProvenanceRecord,
    license_asks_changes_indicated,
    license_change_clause,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "apps" / "web" / "public"
RECORDS = sorted(PUBLIC.rglob("PROVENANCE.json"))


def _load_check():
    spec = importlib.util.spec_from_file_location("verify_provenance", ROOT / "scripts" / "verify_provenance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vp = _load_check()


def _changes_constants(script: Path) -> dict[str, str]:
    """Every string, or dict of strings, assigned to a name holding CHANGES, anywhere in the script."""
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if "changes" in name.lower():
                value = ast.literal_eval(node.value)
                if isinstance(value, str):
                    found[name] = value
                elif isinstance(value, dict):
                    found.update({f"{name}[{key}]": text for key, text in value.items()})
    return found


def _script_entries() -> list[tuple[str, str, dict[str, Any]]]:
    entries = []
    for record in RECORDS:
        data = json.loads(record.read_text(encoding="utf-8"))
        for entry in data["files"] + data["fileSets"]:
            if entry["producedBy"]["method"] == "script":
                entries.append((str(record.relative_to(ROOT)), entry.get("path") or entry["checksums"], entry["producedBy"]))
    return entries


ENTRIES = _script_entries()


def test_there_are_script_made_entries_to_judge() -> None:
    assert len(RECORDS) >= 12 and len(ENTRIES) >= 23


@pytest.mark.parametrize(("record", "path", "producer"), ENTRIES, ids=[f"{r}::{p}" for r, p, _ in ENTRIES])
def test_each_script_made_file_says_how_it_differs_in_its_scripts_own_words(record: str, path: str, producer: dict) -> None:
    assert producer["changes"].strip() == producer["changes"] and producer["changes"]
    assert producer["changes"] in _changes_constants(ROOT / producer["script"]).values(), (record, path)


def test_no_note_still_holds_the_sentence_its_changes_say() -> None:
    # The four records that already said it in their note keep their words in the field, and the note drops them
    for record in RECORDS:
        data = json.loads(record.read_text(encoding="utf-8"))
        for entry in data["files"] + data["fileSets"]:
            producer = entry["producedBy"]
            if producer["method"] == "script":
                assert producer["changes"] not in entry.get("note", ""), (record, entry.get("path"))


def test_every_changes_constant_in_a_script_is_written_into_a_record() -> None:
    used: dict[str, set[str]] = {}
    for _, _, producer in ENTRIES:
        used.setdefault(producer["script"], set()).add(producer["changes"])
    for script, texts in used.items():
        assert set(_changes_constants(ROOT / script).values()) == texts, script


def test_the_records_still_parse_and_no_downloaded_file_claims_changes() -> None:
    for record in RECORDS:
        data = json.loads(record.read_text(encoding="utf-8"))
        AssetProvenanceRecord.model_validate(data)
        for entry in data["files"] + data["fileSets"]:
            if entry["producedBy"]["method"] == "downloaded-as-is":
                assert "changes" not in entry["producedBy"]


def test_the_licence_table_knows_every_licence_the_records_name() -> None:
    known = {row["license"] for row in LICENSE_CHANGE_TERMS["asks"] + LICENSE_CHANGE_TERMS["doesNotAsk"]}
    named = {s["license"]["spdx"] for r in RECORDS for s in json.loads(r.read_text(encoding="utf-8"))["sources"]}
    assert named - known == set()


def test_the_licence_table_names_each_licence_once_with_its_page_and_each_asking_clause() -> None:
    rows = LICENSE_CHANGE_TERMS["asks"] + LICENSE_CHANGE_TERMS["doesNotAsk"]
    ids = [row["license"] for row in rows]
    assert len(set(ids)) == len(ids)
    for row in rows:
        assert row["license"] in SPDX_LICENSE_IDS or row["license"].startswith("LicenseRef-")
        assert row["url"].startswith("https://")
    for row in LICENSE_CHANGE_TERMS["asks"]:
        assert row["clause"] and row["section"]


def test_a_licence_the_table_does_not_know_is_treated_as_asking() -> None:
    assert license_asks_changes_indicated("CC-BY-4.0") and license_asks_changes_indicated("CC-BY-NC-3.0-IGO")
    assert not license_asks_changes_indicated("MIT") and not license_asks_changes_indicated("CC0-1.0")
    assert license_asks_changes_indicated("LicenseRef-Not-In-The-Table")
    assert license_change_clause("CC-BY-SA-4.0")["section"] == "3(a)(1)(B)"
    assert license_change_clause("MIT") is None and license_change_clause("LicenseRef-Not-In-The-Table") is None


# The check's refusal, in a throwaway repository


def _repo(tmp_path: Path, spdx: str, *, changes: str | None) -> Path:
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=git_environment())
    (root / "scripts").mkdir()
    (root / "scripts" / "bake_sample.py").write_text("# a stand-in bake script\n")
    folder = root / "apps" / "web" / "public" / "data"
    folder.mkdir(parents=True)
    (folder / "sample.json").write_bytes(b"{}")
    producer: dict[str, Any] = {"method": "script", "script": "scripts/bake_sample.py"}
    if changes is not None:
        producer["changes"] = changes
    licence = {"spdx": spdx, "name": "A licence", "url": "https://example.org/terms"}
    record = {
        "format": "albedian.asset-provenance",
        "formatVersion": 1,
        "sources": [{"id": "sample", "title": "Sample", "url": "https://example.org/", "version": "1", "license": licence, "credit": "Sample"}],
        "files": [{"path": "sample.json", "sha256": hashlib.sha256(b"{}").hexdigest(), "sources": ["sample"], "producedBy": producer}],
        "fileSets": [],
    }
    (folder / "PROVENANCE.json").write_text(json.dumps(record, indent=2) + "\n")
    gaps = root / "scripts" / "provenance_gaps.json"
    gaps.write_text(json.dumps({"format": "albedian.asset-provenance-gaps", "formatVersion": 1, "gaps": []}) + "\n")
    return root


def _check(root: Path) -> list[str]:
    return vp.check(root, gaps_path=root / "scripts" / "provenance_gaps.json", baseline={}).errors


def _refusal(root: Path) -> str:
    # A refused record covers nothing, so its file is also reported as having no record, as for any invalid record
    errors = _check(root)
    assert any(e.endswith("sample.json: no provenance record (add it to a PROVENANCE.json in its folder)") for e in errors), errors
    (refusal,) = [e for e in errors if "no provenance record" not in e]
    return refusal


def test_the_check_passes_a_script_made_file_that_says_how_it_differs(tmp_path: Path) -> None:
    assert _check(_repo(tmp_path, "CC-BY-4.0", changes="A subset of the sample.")) == []


def test_the_check_refuses_one_that_says_nothing_quoting_the_clause_where_its_licence_asks(tmp_path: Path) -> None:
    error = _refusal(_repo(tmp_path, "CC-BY-4.0", changes=None))
    assert "sample.json is made by scripts/bake_sample.py but does not say how it differs from its sources" in error
    assert 'CC-BY-4.0 (3(a)(1)(B): "indicate if You modified the Licensed Material' in error


def test_the_check_refuses_it_whatever_the_licence(tmp_path: Path) -> None:
    error = _refusal(_repo(tmp_path, "MIT", changes=None))
    assert "does not say how it differs from its sources (producedBy.changes)" in error and "ask" not in error


def test_the_check_reads_a_licence_it_does_not_know_as_asking(tmp_path: Path) -> None:
    error = _refusal(_repo(tmp_path, "LicenseRef-Not-In-The-Table", changes=None))
    assert "LicenseRef-Not-In-The-Table (not in the licence table, so read as asking)" in error


def test_the_check_refuses_blank_changes_through_the_contract(tmp_path: Path) -> None:
    error = _refusal(_repo(tmp_path, "CC-BY-4.0", changes=" "))
    assert "is not a valid provenance record" in error


def test_the_check_refuses_empty_or_null_changes_through_the_contract_and_not_as_saying_nothing(tmp_path: Path) -> None:
    # A changes key that is present is the contract's to judge, whatever it holds: "" and null are refused as an
    # invalid record, never read as a record that says nothing (AUDIT N: the check's strict read of the key)
    for label, value in (("empty", ""), ("null", None)):
        root = _repo(tmp_path / label, "CC-BY-4.0", changes="")
        record = root / "apps" / "web" / "public" / "data" / "PROVENANCE.json"
        data = json.loads(record.read_text())
        data["files"][0]["producedBy"]["changes"] = value
        record.write_text(json.dumps(data, indent=2) + "\n")
        error = _refusal(root)
        assert "is not a valid provenance record" in error, (label, error)
        assert "does not say how it differs" not in error and "3(a)(1)(B)" not in error, (label, error)


def _source(source_id: str, spdx: str) -> dict[str, Any]:
    licence = {"spdx": spdx, "name": "A licence", "url": "https://example.org/terms"}
    return {"id": source_id, "title": "Sample", "url": "https://example.org/", "version": "1", "license": licence, "credit": "Sample"}


def _repo_with(tmp_path: Path, sources: list[dict[str, Any]], files: list[dict[str, Any]], file_sets: list[dict[str, Any]]) -> Path:
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=git_environment())
    (root / "scripts").mkdir()
    (root / "scripts" / "bake_sample.py").write_text("# a stand-in bake script\n")
    folder = root / "apps" / "web" / "public" / "data"
    folder.mkdir(parents=True)
    for name in ("a.json", "b.json", "L0/0.bin"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"{}")
    (folder / "SHA256SUMS").write_text(f"{hashlib.sha256(b'{}').hexdigest()}  L0/0.bin\n")
    record = {"format": "albedian.asset-provenance", "formatVersion": 1, "sources": sources, "files": files, "fileSets": file_sets}
    (folder / "PROVENANCE.json").write_text(json.dumps(record, indent=2) + "\n")
    gaps = root / "scripts" / "provenance_gaps.json"
    gaps.write_text(json.dumps({"format": "albedian.asset-provenance-gaps", "formatVersion": 1, "gaps": []}) + "\n")
    return root


def _entry(path: str, source: str, producer: dict[str, Any]) -> dict[str, Any]:
    return {"path": path, "sha256": hashlib.sha256(b"{}").hexdigest(), "sources": [source], "producedBy": producer}


SCRIPT_SAYING_NOTHING = {"method": "script", "script": "scripts/bake_sample.py"}


def test_the_check_quotes_the_clause_for_a_script_made_file_set_too(tmp_path: Path) -> None:
    sums = hashlib.sha256(f"{hashlib.sha256(b'{}').hexdigest()}  L0/0.bin\n".encode()).hexdigest()
    file_set = {"checksums": "SHA256SUMS", "sha256": sums, "sources": ["s"], "producedBy": SCRIPT_SAYING_NOTHING}
    root = _repo_with(tmp_path, [_source("s", "CC-BY-4.0")], [_entry("a.json", "s", {"method": "downloaded-as-is"}),
                      _entry("b.json", "s", {"method": "downloaded-as-is"})], [file_set])
    refusals = [e for e in _check(root) if "does not say how it differs" in e]
    assert len(refusals) == 1 and "SHA256SUMS is made by scripts/bake_sample.py" in refusals[0], refusals
    assert 'CC-BY-4.0 (3(a)(1)(B): "indicate if You modified the Licensed Material' in refusals[0]


def test_the_check_reads_the_licences_of_the_entrys_own_sources_not_the_records(tmp_path: Path) -> None:
    # The script-made entry is under MIT, which does not ask; the record's other source, under CC-BY-4.0, is not its own
    sources = [_source("mine", "MIT"), _source("other", "CC-BY-4.0")]
    files = [_entry("a.json", "mine", SCRIPT_SAYING_NOTHING), _entry("b.json", "other", {"method": "downloaded-as-is"})]
    root = _repo_with(tmp_path, sources, files, [])
    (root / "apps" / "web" / "public" / "data" / "SHA256SUMS").unlink()
    (root / "apps" / "web" / "public" / "data" / "L0" / "0.bin").unlink()
    refusals = [e for e in _check(root) if "does not say how it differs" in e]
    assert len(refusals) == 1 and "a.json is made by scripts/bake_sample.py" in refusals[0], refusals
    assert "ask" not in refusals[0] and "CC-BY-4.0" not in refusals[0], refusals[0]


@pytest.mark.parametrize(("record", "path", "producer"), ENTRIES, ids=[f"{r}::{p}" for r, p, _ in ENTRIES])
def test_each_sentence_reads_as_the_credits_page_shows_it(record: str, path: str, producer: dict) -> None:
    # Shown whole beside the credit: no file name, no "modified" (the row shows the credit already), no table of numbers
    text = producer["changes"]
    assert not re.search(r"\b[\w-]+\.(?:json|csv|txt|glb|fits|jpg|png|webp|bin|gz|tif)\b", text), text
    assert "modified" not in text.lower(), text
    assert not re.search(r"\d+(?:, \d+){3,}", text), text


# The stars-removed panorama's sentence is shown whole on Credits: each step its bake() runs, read here as the call that
# runs it, is named in the sentence by its words, once each and in the bake's order. A step the table does not list is
# not seen; the table is the limit.
STARLESS_STEPS = (
    ("srgb_to_linear", "converted to linear light"),
    ("star_mask", "masked as a disc"),
    ("smooth_grain", "smoothed by a median and a slight blur"),
    ("fill_masked", "filled in from their surroundings"),
    ("gaussian_filter", "blurred slightly once more"),
    ("preserve_regional_light", "brightness is rescaled"),
    ("linear_to_srgb", "turned back into ordinary colour values"),
    ("put_back_planets", "Venus and Jupiter are put back"),
    ("save", "saved as a lossless image"),
)


def _calls_in_source_order(function: ast.FunctionDef) -> list[str]:
    calls = sorted((n for n in ast.walk(function) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    return [c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "") for c in calls]


def test_the_starless_sentence_names_each_step_of_its_bake_in_the_bakes_order() -> None:
    script = ROOT / "scripts" / "bake_starless_panorama.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    bake = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bake")
    steps = [name for name, _ in STARLESS_STEPS]
    assert [call for call in _calls_in_source_order(bake) if call in steps] == steps
    text = _changes_constants(script)["CHANGES"]
    places = [text.find(words) for _, words in STARLESS_STEPS]
    assert all(text.count(words) == 1 for _, words in STARLESS_STEPS) and places == sorted(places), places


def test_bake_puts_venus_and_jupiter_back_as_photographed_while_removing_a_star(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run bake() end to end on a small made-up photograph (external file and catalogue reads stubbed, no local variable
    # name touched): the lossless WebP bytes it returns must hold the photograph's own sRGB pixels inside both planets'
    # windows (across the longitude seam too) while smoothing and filling a star outside them
    spec = importlib.util.spec_from_file_location("bake_starless_panorama_run", ROOT / "scripts" / "bake_starless_panorama.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    height, width = 40, 90
    rng = np.random.default_rng(42)
    photo8_orig = rng.integers(40, 90, size=(height, width, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:height, 0:width]
    photo8_orig[(xx - 25) ** 2 + (yy - 20) ** 2 <= 4] = np.array([250, 245, 235], dtype=np.uint8)
    photo8_orig[10, 6] = np.array([245, 20, 220], dtype=np.uint8)
    windows = [("Venus", 2.0, 10.0, 3), ("Jupiter", 50.5, 30.0, 3)]
    inside = np.zeros((height, width), dtype=bool)
    for _, cx, cy, r in windows:
        dx = np.minimum(np.abs(xx - cx), width - np.abs(xx - cx))
        win = np.hypot(dx, yy - cy) <= r
        inside |= win
        photo8_orig[win, 0] = 215 + ((xx[win] + yy[win]) % 7)
        photo8_orig[win, 1] = 138
        photo8_orig[win, 2] = 67

    photo_path = tmp_path / "photo.png"
    Image.fromarray(photo8_orig, "RGB").save(photo_path, "PNG")
    checked: list[bool] = []
    seen_photo: list[np.ndarray] = []
    monkeypatch.setattr(mod, "PHOTO_PATH", photo_path)
    monkeypatch.setattr(mod, "_check_inputs", lambda: checked.append(True))
    monkeypatch.setattr(mod, "load_registration", lambda: ("sol", "tbl"))
    monkeypatch.setattr(mod, "load_catalog", lambda _layers: (np.array([[1.0, 0.0, 0.0]]), np.array([5.0])))
    monkeypatch.setattr(mod, "catalog_pixels", lambda _icrs, _sol, _tbl, _geom: (np.array([25.0]), np.array([20.0])))
    monkeypatch.setattr(mod, "profile_radii", lambda _lum, _icrs, _mag, _x, _y: {b: {"radiusPx": 2} for b in mod.PROFILE_BINS})

    def fake_planet_windows(
        arg_photo: np.ndarray, arg_sol: object, arg_tbl: object, arg_geom: object
    ) -> list[tuple[str, float, float, int]]:
        assert (arg_sol, arg_tbl) == ("sol", "tbl") and arg_geom == mod.geometry(arg_photo)
        seen_photo.append(arg_photo.copy())
        return list(windows)

    monkeypatch.setattr(mod, "planet_windows", fake_planet_windows)

    webp_bytes, measured = mod.bake()
    assert checked == [True] and len(seen_photo) == 1
    assert np.allclose(seen_photo[0], mod.srgb_to_linear(photo8_orig.astype(np.float32) / 255.0), rtol=0.0, atol=1e-7)
    assert webp_bytes[12:16] == b"VP8L", webp_bytes[:20]
    decoded = np.asarray(Image.open(io.BytesIO(webp_bytes)).convert("RGB"))
    assert decoded.shape == photo8_orig.shape
    assert inside[10, width - 1] and inside[10, 5] and not inside[10, 6]
    assert np.array_equal(decoded[inside], photo8_orig[inside])
    assert int(decoded[20, 25].max()) < 130 and int(decoded[10, 6, 0]) < 130
    assert measured["planets"] == [
        {"name": "Venus", "x": 2.0, "y": 10.0, "radiusPx": 3},
        {"name": "Jupiter", "x": 50.5, "y": 30.0, "radiusPx": 3},
    ]


def test_the_planets_are_put_back_as_photographed_inside_each_whole_window_wrapping_in_longitude() -> None:
    # What is put back, run on made-up images: every pixel within a window's radius of its centre (across the seam
    # too) takes the photograph's value, and no pixel outside one does (AUDIT N and AUDIT G: held on the pixels)
    spec = importlib.util.spec_from_file_location("bake_starless_panorama", ROOT / "scripts" / "bake_starless_panorama.py")
    bake = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bake
    spec.loader.exec_module(bake)
    height, width = 40, 90
    photo8 = (np.arange(height * width * 3) % 251 + 1).astype(np.uint8).reshape(height, width, 3)
    out8 = np.zeros_like(photo8)
    windows = [("Venus", 2.0, 10.0, 4), ("Jupiter", 50.5, 30.0, 3)]
    bake.put_back_planets(out8, photo8, windows)
    yy, xx = np.mgrid[0:height, 0:width]
    expected = np.zeros((height, width), dtype=bool)
    for _, cx, cy, r in windows:
        dx = np.abs(xx - cx)
        expected |= np.hypot(np.minimum(dx, width - dx), yy - cy) <= r
    assert expected[10, width - 2] and not expected[10, width - 3] and int(expected.sum()) > 2 * 30
    assert np.array_equal(out8[expected], photo8[expected]) and not out8[~expected].any()
