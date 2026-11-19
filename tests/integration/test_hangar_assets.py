"""The hangar's craft for the third-person flight (ASSETS lane, hangar-1): apps/web/public/models/hangar/.

- hangar.json and PROVENANCE.json are exactly what scripts/lookdev/hangar/write_hangar.py writes from the built files,
  and every file is within its stated size budget;
- every speed and fact names a NASA page and quotes it, and each number appears in its own quote;
- the levels of detail lose triangles in order, carry no node transform, and need no decoder (no Draco, no meshopt);
- the files are labelled simulated: the app draws the craft, nothing in them is measured from a photograph.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.lookdev.hangar import write_hangar
from scripts.lookdev.hangar.crafts import BUDGET_BYTES, CRAFTS, HANGAR

ROOT = Path(__file__).resolve().parents[2]
HANGAR_DIR = ROOT / "apps" / "web" / "public" / "models" / "hangar"
FACTS = json.loads((HANGAR_DIR / "hangar.json").read_text(encoding="utf-8"))
NASA_HOSTS = ("nasa.gov",)


def _nasa_page(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return urlparse(url).scheme == "https" and any(host == h or host.endswith("." + h) for h in NASA_HOSTS)


def test_the_facts_file_and_the_record_are_what_the_built_files_say() -> None:
    assert write_hangar.main(["--verify"]) == 0


def test_the_shared_record_keeps_the_moon_source_first_and_the_bubble_source_last() -> None:
    # The Moon and Local Bubble bakes each rewrite this record with their own source first and last: any other
    # source written after the bubble's makes the bubble bake's byte check fail.
    sources = [s["id"] for s in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["sources"]]
    assert sources[0] == write_hangar.MOON_SOURCE_ID and sources[-1] == write_hangar.BUBBLE_SOURCE_ID


def test_every_built_file_is_within_its_budget() -> None:
    for name, model in FACTS["models"].items():
        for level, f in model["files"].items():
            size = (HANGAR_DIR / f["path"]).stat().st_size
            assert size == f["bytes"] <= BUDGET_BYTES[level], (name, level)


def test_over_budget_is_refused() -> None:
    model = json.loads(json.dumps(FACTS))
    first = next(iter(model["models"].values()))
    first["files"]["lod1"]["bytes"] = first["files"]["lod1"]["budgetBytes"] + 1
    assert write_hangar.over_budget(model) and not write_hangar.over_budget(FACTS)


def test_every_entry_names_a_built_model_and_every_number_its_nasa_page_and_quote() -> None:
    assert FACTS["entries"], "no hangar entry"
    for entry in FACTS["entries"]:
        assert entry["model"] in FACTS["models"]
        travel = entry["travel"]
        if travel is not None:
            assert travel["speedKmS"] > 0 and travel["relativeTo"] == "Sun"
            assert _nasa_page(travel["page"]) and str(travel["speedKmS"]) in travel["quote"]
        for fact in entry["facts"]:
            assert _nasa_page(fact["page"]) and fact["quote"].strip() == fact["quote"] != ""
            if isinstance(fact["value"], (int, float)):
                assert str(fact["value"]) in fact["quote"], fact


def test_the_entries_are_the_recipes_own() -> None:
    built = set(FACTS["models"])
    assert FACTS["entries"] == [e for e in HANGAR if e["model"] in built]


def test_the_scale_comes_from_a_dimension_nasa_states() -> None:
    for name, model in FACTS["models"].items():
        sf = model["scaleFrom"]
        assert sf["metres"] == CRAFTS[name]["scale_from"]["metres"] > 0
        assert _nasa_page(sf["page"]) and str(sf["metres"]) in sf["quote"]


def _gltf(path: Path) -> dict:
    data = path.read_bytes()
    assert data[:4] == b"glTF" and struct.unpack_from("<I", data, 4)[0] == 2
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    return json.loads(data[20 : 20 + length])


def test_levels_fall_in_triangles_and_need_no_decoder_or_transform() -> None:
    for name, model in FACTS["models"].items():
        tris = [model["files"][level]["triangles"] for level in ("lod0", "lod1", "lod2")]
        assert tris[0] > tris[1] > tris[2] > 0, name
        for level in ("lod0", "lod1", "lod2"):
            gltf = _gltf(HANGAR_DIR / model["files"][level]["path"])
            assert set(gltf.get("extensionsUsed", [])) <= {"EXT_texture_webp"}, (name, level)
            assert not set(gltf.get("extensionsRequired", [])) - {"EXT_texture_webp"}
            for node in gltf["nodes"]:
                assert not {"matrix", "rotation", "scale", "translation"} & set(node), (name, level)


def test_the_true_size_is_metres_of_a_real_craft() -> None:
    for name, model in FACTS["models"].items():
        lo, hi = model["boundsM"]["min"], model["boundsM"]["max"]
        extent = [b - a for a, b in zip(lo, hi)]
        assert all(0 < e < 40 for e in extent), (name, extent)
        assert model["bodyRadiusM"] < max(extent)


def test_the_frame_and_the_evidence_class() -> None:
    assert FACTS["evidenceClass"] == "simulated"
    assert FACTS["frame"] == {"forward": "+Z", "up": "+Y", "unit": "metre", "origin": "the centre of the part the scale is taken from"}


@pytest.mark.parametrize("name", sorted(CRAFTS))
def test_every_photo_texture_with_a_nasa_mark_has_it_painted_out(name: str) -> None:
    # Recorded per recipe: NASA's insignia and logotype are not public domain (NASA's media guidelines)
    for material in CRAFTS[name]["materials"].values():
        for box in material.get("paint_out", []):
            x0, y0, x1, y1 = box["box"]
            assert 0 <= x0 < x1 and 0 <= y0 < y1 and box["what"]
