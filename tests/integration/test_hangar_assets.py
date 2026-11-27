"""The hangar's craft for the third-person flight (ASSETS lane, hangar-1): apps/web/public/models/hangar/.

- hangar.json and PROVENANCE.json are exactly what scripts/lookdev/hangar/write_hangar.py writes from the built files,
  and every file is within its stated size budget;
- every speed and fact names a NASA page and quotes it, and each number appears in its own quote;
- the levels of detail lose triangles in order, carry no node transform, and need no decoder (no Draco, no meshopt);
- the files are labelled simulated: the app draws the craft, nothing in them is measured from a photograph.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import struct
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pytest

from scripts.lookdev.hangar import write_hangar
from scripts.lookdev.hangar.crafts import BUDGET_BYTES, CACHE, CRAFTS, HANGAR, LOD_RATIO
from scripts.lookdev.hangar.geometry import by_part_at

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
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    for name, model in FACTS["models"].items():
        for level, f in model["files"].items():
            size = (HANGAR_DIR / f["path"]).stat().st_size
            # a level's budget rises by its occlusion's own bytes and by nothing else (craft-ao-one, the lead's ruling)
            occlusion = 0
            if level != "picker":
                gltf = _gltf(HANGAR_DIR / f["path"])
                for mesh in gltf["meshes"]:
                    for prim in mesh["primitives"]:
                        if "_AO" in prim["attributes"]:
                            occlusion += gltf["accessors"][prim["attributes"]["_AO"]]["count"] * 4
            assert f.get("occlusionBytes", 0) == occlusion, (name, level)
            base = {**BUDGET_BYTES, **CRAFTS[name].get("budget_bytes", {})}[level]
            assert size == f["bytes"] <= f["budgetBytes"] == base + occlusion, (name, level)
            # a craft's own budget is for its close view; at a far level only with its reason named in the recipe and in
            # the file's record (the station's lattice parts, which no decimation reduces: the lead's ruling of
            # 2026-11-27); never past twice the shared one at the close view, three times at a far one
            reason = CRAFTS[name].get("budget_reason", {}).get(level)
            assert level == "lod0" or base == BUDGET_BYTES[level] or reason, (name, level)
            assert base <= (2 if level == "lod0" else 3) * BUDGET_BYTES[level], (name, level)
            if reason:
                assert reason in record[f"hangar/{f['path']}"]["note"], (name, level)
    assert {n for n in CRAFTS if CRAFTS[n].get("budget_reason")} == {"iss", "shuttle"}
    assert set(CRAFTS["iss"]["budget_reason"]) == {"lod1", "lod2"} and set(CRAFTS["shuttle"]["budget_reason"]) == {"lod2"}


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
            assert _nasa_page(travel["page"])
            derived = travel.get("derivedFrom")
            if derived is None:
                assert str(travel["speedKmS"]) in travel["quote"]
            else:
                # NASA's own figure in other units, converted: one mile 1.609344 km, one year 365.25 days
                assert derived["quoted"] in travel["quote"]
                km_s = derived["milesPerYear"] * 1.609344 / (365.25 * 86400)
                assert abs(km_s - travel["speedKmS"]) < 0.05, km_s
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
        assert _nasa_page(sf["page"]) and sf["quote"]
        if sf.get("native"):
            # NASA's model built in metres: the suited height must sit in the size NASA states a suit fits
            assert CRAFTS[name]["scale_from"]["native"] is True
            height = model["boundsM"]["max"][1] - model["boundsM"]["min"][1]
            assert 1.5 < height < 2.3, (name, height)
            # a quote with no height in it checks nothing: the record then says the size is the model's own
            states_height = re.search(r"\d+\s*(ft|feet|m\b|meters?|')", sf["quote"]) is not None
            assert (sf.get("size") == "model") is not states_height, (name, sf)
        else:
            assert sf["metres"] == CRAFTS[name]["scale_from"]["metres"] > 0
            # a part picked by a slab of the import frame says so, with the slab
            assert sf.get("partBand") == CRAFTS[name]["scale_from"].get("band")
            # a size measured between footpads says how many
            assert sf.get("partPads") == CRAFTS[name]["scale_from"].get("pads")
            # a size measured tip to tip across an axis says which axis
            assert sf.get("partSpanAcross") == CRAFTS[name]["scale_from"].get("span_across")
            # a model true to its scale part only says so, and none of its entries states a span, unless the span is
            # the very dimension it is scaled from (the whole model's width, part null)
            assert (sf.get("trueTo") == "part") is (CRAFTS[name]["scale_from"].get("true_to") == "part")
            if sf.get("trueTo") == "part" and sf["part"] is not None:
                for entry in (e for e in FACTS["entries"] if e["model"] == name):
                    assert not any("span" in f["key"].lower() for f in entry["facts"]), (name, entry["id"])
            if "quotedFt" in sf:
                # NASA gives it in feet: the quote holds that number and the metres are it times 0.3048
                assert str(sf["quotedFt"]) in sf["quote"] and abs(sf["quotedFt"] * 0.3048 - sf["metres"]) < 1e-9
            elif "quotedCm" in sf:
                # NASA gives it in centimetres: the quote holds that number and the metres are it over 100
                assert str(sf["quotedCm"]) in sf["quote"] and abs(sf["quotedCm"] / 100 - sf["metres"]) < 1e-9
            else:
                assert str(sf["metres"]) in sf["quote"]
    # Mars Global Surveyor's wings do not hold NASA's sizes at its dish's scale (AUDIT-B's read of hangar-28)
    assert FACTS["models"]["mgs"]["scaleFrom"].get("trueTo") == "part"
    # nor do MAVEN's wings at its dish's scale (AUDIT-B's read of hangar-30)
    assert FACTS["models"]["maven"]["scaleFrom"].get("trueTo") == "part"
    # Perseverance's mast lies stowed on its deck in NASA's model, below NASA's 2.2 m height: scaled from its wheels
    assert FACTS["models"]["perseverance"]["scaleFrom"].get("trueTo") == "part"
    assert FACTS["models"]["perseverance"]["scaleFrom"].get("quotedCm") == 52.5


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
            # a rigged suit's joints carry their own places and turns; every other node none
            joints = {j for skin in gltf.get("skins", []) for j in skin["joints"]}
            for i, node in enumerate(gltf["nodes"]):
                assert i in joints or not {"matrix", "rotation", "scale", "translation"} & set(node), (name, level)
            if CRAFTS[name].get("drop_animations"):
                assert "animations" not in gltf, (name, level)
    assert CRAFTS["perseverance"].get("drop_animations") is True


def _primitive_triangles(gltf: dict) -> dict[str, int]:
    mats = [re.sub(r"\.\d+$", "", m.get("name", f"mat_{i}")) for i, m in enumerate(gltf.get("materials", []))]
    out: dict[str, int] = {}
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            key = mats[prim["material"]] if "material" in prim else "<none>"
            out[key] = out.get(key, 0) + gltf["accessors"][prim["indices"]]["count"] // 3
    return out


def _glb_spans(gltf: dict) -> list[float]:
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            pos = gltf["accessors"][prim["attributes"]["POSITION"]]
            lo = [min(a, b) for a, b in zip(lo, pos["min"])]
            hi = [max(a, b) for a, b in zip(hi, pos["max"])]
    return [b - a for a, b in zip(lo, hi)]


def test_decimation_never_lets_a_boom_or_truss_starve_primary_primitives_in_lod2() -> None:
    # A dense lattice boom of many disconnected struts hits Blender's 4-triangle-per-part floor under whole-mesh
    # collapse decimation and can starve the bus, dish and solar panels in lod2 (SPATIAL mo-1's catch on Mars Odyssey).
    for name, model in FACTS["models"].items():
        g0 = _gltf(HANGAR_DIR / model["files"]["lod0"]["path"])
        g1 = _gltf(HANGAR_DIR / model["files"]["lod1"]["path"])
        g2 = _gltf(HANGAR_DIR / model["files"]["lod2"]["path"])
        p0, p1, p2 = _primitive_triangles(g0), _primitive_triangles(g1), _primitive_triangles(g2)
        tot0, tot2 = sum(p0.values()), sum(p2.values())
        for mat, tris0 in p0.items():
            share0 = tris0 / tot0
            share2 = p2.get(mat, 0) / tot2
            assert share2 - share0 <= 0.5, (name, mat, round(share0, 3), round(share2, 3))
        if CRAFTS[name].get("decimate_by_material") or CRAFTS[name].get("decimate_by_part"):
            assert set(p1) == set(p0) and all(v > 0 for v in p1.values()), (name, p1)
            assert set(p2) == set(p0) and all(v > 0 for v in p2.values()), (name, p2)
            for axis, (s0, s2) in enumerate(zip(_glb_spans(g0), _glb_spans(g2))):
                assert s2 >= 0.95 * s0, (name, axis, s0, s2)
    assert CRAFTS["odyssey"].get("decimate_by_material") is True


def _glb_parts(path: Path) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray]:
    """Each loose part of a level (triangles joined by shared corners, corners at one place to 0.1 mm being one) as its
    bounding box, and every corner the level draws."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + length])
    blob = data[20 + length + 8 :]
    kinds = {5123: np.uint16, 5125: np.uint32, 5126: np.float32}

    def read(i: int, width: int) -> np.ndarray:
        acc = gltf["accessors"][i]
        view = gltf["bufferViews"][acc["bufferView"]]
        assert view.get("byteStride", 0) in (0, width * np.dtype(kinds[acc["componentType"]]).itemsize)
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return np.frombuffer(blob, kinds[acc["componentType"]], acc["count"] * width, offset).reshape(-1, width)

    pos, tri, base = [], [], 0
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            p = read(prim["attributes"]["POSITION"], 3).astype(np.float64)
            tri.append(read(prim["indices"], 1).reshape(-1, 3).astype(np.int64) + base)
            pos.append(p)
            base += len(p)
    points = np.concatenate(pos)
    _, corner = np.unique(np.round(points / 1e-4).astype(np.int64), axis=0, return_inverse=True)
    t = corner.reshape(-1)[np.concatenate(tri)]
    n = int(t.max()) + 1
    edges = np.concatenate([t[:, [0, 1]], t[:, [1, 2]]])
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(n, n))
    _, label = connected_components(graph, directed=False)
    place = np.zeros((n, 3))
    place[corner.reshape(-1)] = points
    used = np.unique(t)
    boxes = [(place[used[label[used] == k]].min(0), place[used[label[used] == k]].max(0)) for k in np.unique(label[used])]
    return boxes, place[used]


# For a craft decimated part by part at its far levels whose lod0 is a share of its whole mesh: the size and width from
# which a lod0 sheet or body must keep a corner at every level, and how many such parts lod0 has at least.
BIG_PARTS = {"iss": (10.0, 0.5, 90)}


def test_a_craft_decimated_part_by_part_keeps_every_part_its_level_draws() -> None:
    # One collapse ratio over InSight's ~1,900 loose parts shredded its deck and left two legs without struts or
    # footpads in lod1 and lod2 (53 lod0 parts of 3 cm and over gone from lod1, 79 of 6.5 cm and over from lod2, the
    # largest 27 cm): a craft decimated part by part leaves out only the parts under its level's size, measured before
    # decimation, and every larger lod0 part still has corners in its place (its box grown by 1 cm) at every level.
    # The station's lod1 dropped its 20 m radiators and its lod2 its 35 m solar arrays under one ratio over its ~22,600
    # loose parts (14,878 lod0 parts of 3 cm and over gone from lod1, 17,525 of 6.5 cm and over from lod2): it is
    # decimated part by part at lod1 and lod2, its lod0 under one ratio still.
    by_part = [name for name in FACTS["models"] if CRAFTS[name].get("decimate_by_part")]
    assert {"insight", "voyager", "iss", "shuttle", "cassini", "osirisrex"} <= set(by_part)
    assert CRAFTS["iss"]["decimate_by_part"] == ["lod1", "lod2"]
    for name in by_part:
        # the provenance sentence of a by-part level says hidden inner faces of bulky parts are left out
        assert CRAFTS[name].get("drop_hidden_faces") == ["lod1", "lod2"], name
        boxes0, _ = _glb_parts(HANGAR_DIR / FACTS["models"][name]["files"]["lod0"]["path"])
        for level in ("lod1", "lod2"):
            assert by_part_at(CRAFTS[name], level), (name, level)
            under = CRAFTS[name].get("drop_parts_under_m", {}).get(level, 0.0)
            # in its place: a corner within its box grown by 1 cm, or by a fiftieth of the level's cut where that is more
            # (the station's 17.8 m rods 5 mm thick keep corners 2.6 to 3.9 cm from their ends at a 2.5 m cut)
            grow = max(0.01, under / 50)
            _, points = _glb_parts(HANGAR_DIR / FACTS["models"][name]["files"][level]["path"])
            recipe = CRAFTS[name]
            if by_part_at(recipe, "lod0") or {**LOD_RATIO, **recipe.get("lod_ratio", {})}["lod0"] == 1.0 or not recipe.get("weld_levels"):
                # lod0 shares the far levels' parts: decimated by part itself, NASA's whole mesh (the reader welds corners
                # at 0.1 mm, as the far levels are welded), or one share over the same welded mesh (a share keeps parts)
                checked = [(lo, hi) for lo, hi in boxes0 if np.linalg.norm(hi - lo) >= under]
            else:
                # lod0 is one share over the whole mesh, so its parts are not the far levels' parts (a panel inside a
                # module is a part there, inner faces at the far levels): each of its sheets and bodies at least the
                # craft's size, wide enough, keeps a corner in place (the station: 96, of which main's files lost 21 at
                # lod1 and 76 at lod2)
                big, wide, least = BIG_PARTS[name]
                checked = [(lo, hi) for lo, hi in boxes0 if np.linalg.norm(hi - lo) >= big and np.sort(hi - lo)[1] >= wide]
                assert len(checked) >= least, (name, len(checked))
            gone = [
                (round(float(np.linalg.norm(hi - lo)), 3), [round(float(x), 2) for x in (lo + hi) / 2])
                for lo, hi in checked
                if not np.all((points >= lo - grow) & (points <= hi + grow), axis=1).any()
            ]
            assert not gone, (name, level, len(gone), sorted(gone, reverse=True)[:5])


def test_each_craft_is_credited_as_its_own_nasa_model_page_credits_it() -> None:
    # AUDIT-B's read of main 927612bd3 (R2): the craft built at JPL were credited "NASA" alone. Each model's own page
    # among NASA's 3D Resources, read 2026-11-26, names a Source (most often the model's maker; only Perseverance's
    # says NASA/Jet Propulsion Laboratory), and five name none, which stay "NASA". Every file built from a model is
    # credited by its page's line and names that page; one record source per credit line.
    from scripts.lookdev.hangar.crafts import MODEL_CREDITS

    raw = json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))
    record = {f["path"]: f for f in raw["files"]}
    sources = {s["id"]: s for s in raw["sources"]}
    assert set(MODEL_CREDITS) == set(FACTS["models"]) == set(CRAFTS)
    for name, given in MODEL_CREDITS.items():
        assert given["page"].startswith("https://science.nasa.gov/"), name
        for level in ("lod0", "lod1", "lod2", "picker"):
            path = f"hangar/{name}/{name}_{level}.{'webp' if level == 'picker' else 'glb'}"
            credited = [sources[s]["credit"] for s in record[path]["sources"] if s != write_hangar.PAGES_SOURCE]
            assert credited == [given["credit"]], (path, credited)
            assert given["page"] in record[path]["note"], path
    assert MODEL_CREDITS["perseverance"]["credit"] == "NASA/Jet Propulsion Laboratory"
    assert MODEL_CREDITS["iss"]["credit"] == "NASA/JSC/Integrated Graphics, Operations, and Analysis Laboratory"
    assert MODEL_CREDITS["hubble"]["credit"] == "DigitalSpace Corporation"
    assert MODEL_CREDITS["cassini"]["credit"] == "NASA/Brian E. Kumanchik"
    assert {n for n, g in MODEL_CREDITS.items() if g["credit"] == "NASA"} == {"gemini", "juno", "messenger", "mro", "near"}
    hangar = [s for s in sources.values() if s["id"] == write_hangar.MODELS_SOURCE or s["id"].startswith(write_hangar.CREDIT_SOURCE_PREFIX)]
    assert len({s["credit"] for s in hangar}) == len(hangar) == len({g["credit"] for g in MODEL_CREDITS.values()}) == 15
    assert all(s["title"] == "NASA 3D Resources" and s["license"] == sources[write_hangar.MODELS_SOURCE]["license"] for s in hangar)
    # the facts file is built from every model, so it names every hangar source
    assert set(record["hangar/hangar.json"]["sources"]) == {s["id"] for s in hangar} | {write_hangar.PAGES_SOURCE}


def test_a_credit_source_no_model_names_leaves_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))
    stale = {**raw["sources"][0], "id": write_hangar.CREDIT_SOURCE_PREFIX + "a-maker-no-page-names", "credit": "Nobody"}
    raw["sources"].insert(1, stale)
    copy = tmp_path / "PROVENANCE.json"
    copy.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(write_hangar, "RECORD_PATH", copy)
    written = write_hangar.build_record(list(FACTS["models"]))
    assert stale["id"] not in {s["id"] for s in written["sources"]}
    assert [s["id"] for s in written["sources"]] == [s["id"] for s in raw["sources"] if s is not stale]


def test_decimating_part_by_part_is_every_level_for_true_and_the_named_levels_for_a_list() -> None:
    assert [by_part_at({"decimate_by_part": True}, lv) for lv in ("lod0", "lod1", "lod2")] == [True, True, True]
    assert [by_part_at({"decimate_by_part": ["lod1", "lod2"]}, lv) for lv in ("lod0", "lod1", "lod2")] == [False, True, True]
    assert [by_part_at(r, "lod1") for r in ({}, {"decimate_by_part": False}, {"decimate_by_part": "lod1"})] == [False] * 3
    # each level's provenance sentence says how that level was made: the station's lod0 under one ratio
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    for name in FACTS["models"]:
        for level in ("lod1", "lod2"):
            said = record[f"hangar/{name}/{name}_{level}.glb"]["producedBy"]["changes"]
            assert ("each loose part reduced on its own" in said) == by_part_at(CRAFTS[name], level), (name, level)
    assert "each loose part reduced on its own" not in record["hangar/iss/iss_lod0.glb"]["producedBy"]["changes"]


def test_the_true_size_is_metres_of_a_real_craft() -> None:
    for name, model in FACTS["models"].items():
        lo, hi = model["boundsM"]["min"], model["boundsM"]["max"]
        extent = [b - a for a, b in zip(lo, hi)]
        # metres, not inches or centimetres: nothing in the hangar is past the station's 109 m
        assert all(0 < e < 120 for e in extent), (name, extent)
        assert model["bodyRadiusM"] < max(extent)


def test_a_picker_rendered_from_the_build_has_a_clear_background_and_says_why() -> None:
    from PIL import Image

    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    rendered = [m for m in FACTS["models"] if CRAFTS[m]["source"].get("picker_render_reason")]
    assert "newhorizons" in rendered and "perseverance" in rendered and "insight" in rendered
    for m in rendered:
        with Image.open(HANGAR_DIR / m / f"{m}_picker.webp") as im:
            assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0, m
        assert "rendered by Blender's Cycles" in record[f"hangar/{m}/{m}_picker.webp"]["producedBy"]["changes"]


def test_footpad_spacing_is_the_mean_distance_between_pads_seen_from_above() -> None:
    from math import cos, radians, sin

    from scripts.lookdev.hangar.geometry import mean_plan_distance

    # three pads on an equilateral triangle of side 2.21, at different heights: the heights do not count
    r = 2.21 / (3**0.5)
    pads = [(r * cos(radians(a)), r * sin(radians(a)), z) for a, z in ((90, 0.0), (210, 0.4), (330, -0.3))]
    assert abs(mean_plan_distance(pads) - 2.21) < 1e-9
    # the plane is across the axis named up: the same pads lying across Y measure the same
    assert abs(mean_plan_distance([(x, z, y) for x, y, z in pads], up=1) - 2.21) < 1e-9
    # an uneven triangle gives the mean of its three sides
    assert abs(mean_plan_distance([(0, 0, 0), (3, 0, 0), (0, 4, 0)]) - 4.0) < 1e-9
    with pytest.raises(ValueError):
        mean_plan_distance([(0, 0, 0)])


def test_a_rotor_span_is_the_largest_distance_across_its_mast() -> None:
    from math import cos, radians, sin

    from scripts.lookdev.hangar.geometry import max_plan_distance

    # two coaxial two-blade rotors of 1.2 m on a mast along X, at different heights and blade angles, each blade a
    # strip of points from the hub to its tip: tip to tip is 1.2 whatever the angles, and the heights do not count
    pts = []
    for height, angle in ((0.0, 20.0), (0.3, 115.0)):
        for k in range(-30, 31):
            r = 0.6 * k / 30
            pts.append((height, r * cos(radians(angle)), r * sin(radians(angle))))
    assert abs(max_plan_distance(pts, up=0) - 1.2) < 1e-9
    # seen along another axis the same points measure something else (here the rotors' spacing plays in)
    assert max_plan_distance(pts, up=2) != pytest.approx(1.2)
    # a square's farthest pair is its diagonal, from the hull's corners alone
    square = [(x, y, 0.0) for x in (0.0, 0.5, 1.0) for y in (0.0, 0.5, 1.0)]
    assert abs(max_plan_distance(square) - 2**0.5) < 1e-9
    with pytest.raises(ValueError):
        max_plan_distance([(0.0, 1.0, 2.0), (5.0, 1.0, 2.0)], up=0)


def test_the_frame_and_the_evidence_class() -> None:
    assert FACTS["evidenceClass"] == "simulated"
    assert FACTS["frame"] == {"forward": "+Z", "up": "+Y", "unit": "metre", "origin": "the centre of the part the scale is taken from"}


@pytest.mark.parametrize("name", sorted(CRAFTS))
def test_every_photo_texture_with_a_nasa_mark_has_it_painted_out(name: str) -> None:
    # Recorded per recipe: NASA's insignia and logotype are not public domain (NASA's media guidelines)
    for material in [*CRAFTS[name].get("materials", {}).values(), *CRAFTS[name].get("material_rules", [])]:
        for box in material.get("paint_out", []):
            x0, y0, x1, y1 = box["box"]
            assert 0 <= x0 < x1 and 0 <= y0 < y1 and box["what"]


# NASA's "blade" texture (1024 by 512, the same file in the Ingenuity and Perseverance models) holds a plate with the
# JPL logo and a plate with the NASA insignia; boxes in its own pixels, x0 y0 x1 y1 from the top left.
BLADE_PLATES = ((20, 165, 152, 208), (887, 398, 1020, 508))


def _glb_images(path: Path) -> dict[str, "np.ndarray"]:
    import io

    from PIL import Image

    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + length])
    blob = data[20 + length + 8 :]
    out = {}
    for i, image in enumerate(gltf.get("images", [])):
        view = gltf["bufferViews"][image["bufferView"]]
        raw = blob[view.get("byteOffset", 0) : view.get("byteOffset", 0) + view["byteLength"]]
        with Image.open(io.BytesIO(raw)) as picture:
            out[image.get("name", str(i))] = np.asarray(picture.convert("RGB"), dtype=np.float64)
    return out


@pytest.mark.parametrize(
    "name", ["gemini", "odyssey", "pioneer10", "messenger", "stardust", "clementine", "deepimpact", "hubble", "newhorizons"]
)
def test_the_phone_level_textures_decode_under_eight_megabytes(name: str) -> None:
    # tex-budget-one (the lead, 15:59 on 24 Nov, from SPATIAL's flight-perf-audit): these five crafts' phone levels
    # decoded 10.7 to 13.9 MB of textures (width x height x 4 bytes each); their recipes now cap the phone level's
    # texture edge, by resizing only, so each decodes under 8 MB. The recipe names its cap. tex-budget-two (27 Nov): the
    # four still over 8 MB at a 512 px edge (Clementine 8.1, Deep Impact 9.2, Hubble 9.4, New Horizons 9.4 MB).
    edge = CRAFTS[name].get("texture_edge", {}).get("lod2")
    assert edge is not None and edge <= 384, (name, edge)
    images = _glb_images(HANGAR_DIR / FACTS["models"][name]["files"]["lod2"]["path"])
    assert images and all(max(px.shape[:2]) <= edge for px in images.values()), name
    decoded = sum(px.shape[0] * px.shape[1] * 4 for px in images.values())
    assert decoded < 8_000_000, (name, decoded)


@pytest.mark.parametrize("name", ["ingenuity", "perseverance"])
def test_the_blade_texture_ships_without_the_jpl_and_nasa_plates_at_every_level(name: str) -> None:
    # SUBMISSION's risks R1.1 on 24 Nov: Ingenuity's files carried both plates while the record said no NASA mark is
    # shown. Each plate's box, scaled to the level's copy and kept 3 source pixels inside its edge, is one flat colour
    # after painting (a standard deviation under 12 of 255 per channel, against about 60 or more over a logo). The
    # record's sentence for the close level says both marks were painted over.
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    sentence = record[f"hangar/{name}/{name}_lod0.glb"]["producedBy"]["changes"]
    assert "JPL logo" in sentence and "NASA insignia" in sentence and "painted over" in sentence, sentence
    for level in ("lod0", "lod1", "lod2"):
        blades = {k: v for k, v in _glb_images(HANGAR_DIR / name / f"{name}_{level}.glb").items() if k.startswith("blade")}
        assert blades, (name, level)
        for image_name, px in blades.items():
            h, w, _ = px.shape
            for x0, y0, x1, y1 in BLADE_PLATES:
                sx, sy = w / 1024, h / 512
                crop = px[int((y0 + 3) * sy) : int((y1 - 3) * sy), int((x0 + 3) * sx) : int((x1 - 3) * sx)]
                assert crop.size and crop.std(axis=(0, 1)).max() < 12, (name, level, image_name, (x0, y0, x1, y1))


def _leaning_corner_share(path: Path, degrees: float) -> float:
    """Share of triangle corner area whose shading normal leans more than `degrees` off its triangle's face normal."""
    import numpy as np

    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + length])
    blob = data[20 + length + 8 :]

    def read(i: int, width: int) -> "np.ndarray":
        acc = gltf["accessors"][i]
        view = gltf["bufferViews"][acc["bufferView"]]
        kind = {5123: np.uint16, 5125: np.uint32, 5126: np.float32}[acc["componentType"]]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return np.frombuffer(blob, kind, acc["count"] * width, offset).reshape(-1, width).astype(np.float64)

    limit = np.cos(np.radians(degrees))
    lean = total = 0.0
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            p, n = read(prim["attributes"]["POSITION"], 3), read(prim["attributes"]["NORMAL"], 3)
            t = read(prim["indices"], 1).reshape(-1, 3).astype(np.int64)
            face = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
            area = 0.5 * np.linalg.norm(face, axis=1)
            ok = area > 1e-12
            face = face[ok] / (2 * area[ok, None])
            for k in range(3):
                lean += float(area[ok][np.einsum("ij,ij->i", n[t[ok, k]], face) < limit].sum()) / 3
            total += float(area[ok].sum())
    return lean / total


def _area_weighted_share(path: Path, degrees: float) -> float:
    """Share of corners whose normal lies within `degrees` of the area-weighted mean of the face normals of the corners
    at the same position carrying the same normal (one smoothing group)."""
    import numpy as np

    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20 : 20 + length])
    blob = data[20 + length + 8 :]

    def read(i: int, width: int) -> "np.ndarray":
        acc = gltf["accessors"][i]
        view = gltf["bufferViews"][acc["bufferView"]]
        kind = {5123: np.uint16, 5125: np.uint32, 5126: np.float32}[acc["componentType"]]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return np.frombuffer(blob, kind, acc["count"] * width, offset).reshape(-1, width).astype(np.float64)

    pos, nrm, face, area = [], [], [], []
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            p, n = read(prim["attributes"]["POSITION"], 3), read(prim["attributes"]["NORMAL"], 3)
            t = read(prim["indices"], 1).reshape(-1, 3).astype(np.int64)
            f = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
            a = 0.5 * np.linalg.norm(f, axis=1)
            ok = a > 1e-12
            t, f, a = t[ok], f[ok] / (2 * a[ok, None]), a[ok]
            for k in range(3):
                pos.append(p[t[:, k]])
                nrm.append(n[t[:, k]])
                face.append(f)
                area.append(a)
    p, n, f, a = (np.concatenate(x) for x in (pos, nrm, face, area))
    key = np.concatenate([np.round(p / 1e-5), np.round(n * 1000)], axis=1).astype(np.int64)
    _, group = np.unique(key, axis=0, return_inverse=True)
    group = group.reshape(-1)
    acc = np.zeros((group.max() + 1, 3))
    np.add.at(acc, group, f * a[:, None])
    mean = acc / np.linalg.norm(acc, axis=1, keepdims=True)
    return float((np.einsum("ij,ij->i", n, mean[group]) >= np.cos(np.radians(degrees))).mean())


def test_a_suit_given_level_normals_shades_flat_panels_flat_at_every_level() -> None:
    # UIUX's look gap seven on 24 Nov: at 3840 wide the EMU's backpack showed dark smears and hard facets, its sharp
    # edges marked at 40 degrees before decimation and the coarse faces then smoothed across real corners (11 to 20.5
    # percent of its corner area leaned over 30 degrees off its own face). Normals made again after each level's
    # decimation and weighted by face area keep that under 5 percent at lod0 and lod1 (every suit's lod1 was 8 to 13.7)
    # and under 10 percent at lod2, 5 percent of NASA's triangles, where fabric creases under 70 degrees are meant to be
    # smoothed (20 to 27.5 before); no vertex or texture coordinate moves.
    # The weighting itself: each corner's normal is the area-weighted mean of the face normals of its smoothing group
    # (the corners at one position carrying one normal) within 2 degrees, for 99 to 100 percent of corners against 43
    # to 46 percent without it; and the record's sentence says so.
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    suits = [name for name, recipe in CRAFTS.items() if recipe.get("level_normals")]
    assert {"emu", "mercury_suit", "mark3", "aces", "gemini_suit"} <= set(suits)
    for name in suits:
        assert CRAFTS[name]["level_normals"]["weighted"] is True
        sentence = record[f"hangar/{name}/{name}_lod0.glb"]["producedBy"]["changes"]
        assert "weighted by the area of the faces around it, no vertex or texture moved" in sentence, (name, sentence)
        for level in ("lod0", "lod1", "lod2"):
            path = HANGAR_DIR / FACTS["models"][name]["files"][level]["path"]
            share = _leaning_corner_share(path, 30.0)
            assert share < (0.10 if level == "lod2" else 0.05), (name, level, round(share, 4))
            matched = _area_weighted_share(path, 2.0)
            assert matched >= 0.98, (name, level, round(matched, 4))


def _glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20 : 20 + length]), data[20 + length + 8 :]


def _accessor(gltf: dict, blob: bytes, i: int) -> "np.ndarray":
    acc = gltf["accessors"][i]
    view = gltf["bufferViews"][acc["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
    kind = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}[acc["componentType"]]
    assert view.get("byteStride", np.dtype(kind).itemsize * width) == np.dtype(kind).itemsize * width
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    out = np.frombuffer(blob, kind, acc["count"] * width, offset).reshape(-1, width).astype(np.float64)
    return out / np.iinfo(kind).max if acc.get("normalized") else out


def _node_worlds(gltf: dict, turns: dict[int, "np.ndarray"] | None = None) -> list["np.ndarray"]:
    """Each node's world matrix; `turns` replaces some nodes' rotations (a pose)."""
    parent = {c: i for i, n in enumerate(gltf["nodes"]) for c in n.get("children", [])}
    turns = turns or {}

    def local(index: int) -> "np.ndarray":
        node = gltf["nodes"][index]
        x, y, z, w = turns[index] if index in turns else node.get("rotation", (0.0, 0.0, 0.0, 1.0))
        turn = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        out = np.eye(4)
        out[:3, :3] = turn @ np.diag(node.get("scale", (1.0, 1.0, 1.0)))
        out[:3, 3] = node.get("translation", (0.0, 0.0, 0.0))
        return out

    worlds: dict[int, "np.ndarray"] = {}

    def world(i: int) -> "np.ndarray":
        if i not in worlds:
            worlds[i] = (world(parent[i]) if i in parent else np.eye(4)) @ local(i)
        return worlds[i]

    return [world(i) for i in range(len(gltf["nodes"]))]


def _whole_parts(points: "np.ndarray", triangles: "np.ndarray") -> "np.ndarray":
    """A label per vertex: its loose part, joined by shared triangles and by shared places within 0.1 mm (as the build
    finds a suit's parts to rig)."""
    parent = np.arange(len(points))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    _, first, place = np.unique(np.round(points / 1e-4).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    same = np.stack([np.arange(len(points)), first[place.reshape(-1)]], axis=1)
    for a, b in np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], same]):
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[ra] = rb
    return np.array([find(v) for v in range(len(points))])


def _rigged_level(path: Path) -> dict:
    """A rigged level read from its glTF alone: its points, joints and weights over all primitives, its whole parts,
    the bone carrying each whole part (None for a weighted one), and its points in each pose, skinned as glTF draws
    them (each joint's posed world matrix times its inverse bind matrix, blended by the weights)."""
    gltf, blob = _glb(path)
    (skin,) = gltf["skins"]
    (mesh_node,) = [i for i, n in enumerate(gltf["nodes"]) if "skin" in n]
    points, which, weight, triangles = [], [], [], []
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            triangles.append(_accessor(gltf, blob, prim["indices"]).reshape(-1, 3).astype(np.int64) + sum(map(len, points)))
            points.append(_accessor(gltf, blob, prim["attributes"]["POSITION"]))
            which.append(_accessor(gltf, blob, prim["attributes"]["JOINTS_0"]).astype(np.int64))
            weight.append(_accessor(gltf, blob, prim["attributes"]["WEIGHTS_0"]))
    points, which, weight = np.concatenate(points), np.concatenate(which), np.concatenate(weight)
    part = _whole_parts(points, np.concatenate(triangles))
    inverse_bind = _accessor(gltf, blob, skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    names = [gltf["nodes"][j]["name"] for j in skin["joints"]]
    carried = (weight > 1e-3).sum(axis=1)
    bone_of = {}
    for label in np.unique(part):
        rows = part == label
        on = set(which[rows][weight[rows] > 1e-3].tolist())
        bone_of[int(label)] = names[on.pop()] if (carried[rows] == 1).all() and len(on) == 1 else None
    posed = {}
    for anim in gltf["animations"]:
        turns = {
            c["target"]["node"]: _accessor(gltf, blob, anim["samplers"][c["sampler"]]["output"])[0]
            for c in anim["channels"]
            if c["target"]["path"] == "rotation"
        }
        worlds = _node_worlds(gltf, turns)
        to_mesh = np.linalg.inv(worlds[mesh_node])
        matrices = np.array([to_mesh @ worlds[j] @ inverse_bind[k] for k, j in enumerate(skin["joints"])])
        moved = np.zeros_like(points)
        for k in range(4):
            m = matrices[which[:, k]]
            moved += weight[:, k, None] * (np.einsum("nij,nj->ni", m[:, :3, :3], points) + m[:, :3, 3])
        posed[anim["name"]] = moved
    return {"points": points, "part": part, "bone_of": bone_of, "posed": posed}


def _openings(level: dict, touch_m: float = 0.005) -> dict[tuple[int, int], dict[str, float]]:
    """For every two whole parts touching at rest (a corner of one within `touch_m` of the other), how far that contact
    opens in each pose: the farthest any of its touching corners ends from the other part."""
    from scipy.spatial import cKDTree

    points, part = level["points"], level["part"]
    pairs = cKDTree(points).query_pairs(touch_m, output_type="ndarray")
    pairs = pairs[part[pairs[:, 0]] != part[pairs[:, 1]]]
    contact: dict[tuple[int, int], set[int]] = {}
    for a, b in pairs:
        for x, y in ((a, b), (b, a)):
            contact.setdefault((int(part[x]), int(part[y])), set()).add(int(x))
    out: dict[tuple[int, int], dict[str, float]] = {}
    for pose, moved in level["posed"].items():
        trees: dict[int, "cKDTree"] = {}
        for (pa, pb), corners in contact.items():
            if pb not in trees:
                trees[pb] = cKDTree(moved[part == pb])
            gap = float(trees[pb].query(moved[sorted(corners)])[0].max())
            out.setdefault((pa, pb), {})[pose] = gap
    return out


RIGGED = sorted(name for name, recipe in CRAFTS.items() if recipe.get("rig"))


@pytest.mark.parametrize("name", RIGGED)
def test_a_rigged_suit_carries_its_skeleton_and_three_poses_at_every_level(name: str) -> None:
    # suit-rig-one (the lead's order of 01:01 on 25 Nov): both suits rigged in Blender by their own parts, nothing added
    # or reshaped, the skeleton at every level, three poses the app blends: hover, cruise, brake; the record's sentence,
    # rebuilt from NASA's model, says it was rigged and posed by the app (never the word "modified": the Credits row
    # marks a changed file itself, test_provenance_changes.py). At rest every joint's matrix is the identity, so no
    # vertex moves until a pose turns a bone; each vertex's weights sum to one in glTF's one set of four (the build
    # weights the full mesh on two bones, and a level's reduction blends neighbours, so up to three at lod0); a whole
    # part hangs on one bone (the EMU's fabric alone is weighted across a joint); and a part past a recipe's hold plane
    # (the helmet above the shoulders, the EMU's pack behind them: in the first trial the pack's rails hung on the right
    # upper arm and flew off in the cruise) hangs on the hold's bone.
    assert RIGGED == ["emu", "z2"]
    rig = CRAFTS[name]["rig"]
    bones = [b["name"] for b in rig["bones"]]
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    sentence = record[f"hangar/{name}/{name}_lod0.glb"]["producedBy"]["changes"]
    assert "Rigged and posed by the app: a skeleton of" in sentence, sentence
    for level in ("lod0", "lod1", "lod2"):
        gltf, blob = _glb(HANGAR_DIR / FACTS["models"][name]["files"][level]["path"])
        (skin,) = gltf["skins"]
        joints = [gltf["nodes"][j]["name"] for j in skin["joints"]]
        assert sorted(joints) == sorted(bones), (name, level, joints)
        assert sorted(a["name"] for a in gltf["animations"]) == ["brake", "cruise", "hover"], (name, level)
        worlds = _node_worlds(gltf)
        (mesh_node,) = [i for i, n in enumerate(gltf["nodes"]) if "skin" in n]
        to_mesh = np.linalg.inv(worlds[mesh_node])
        inverse_bind = _accessor(gltf, blob, skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
        for k, j in enumerate(skin["joints"]):
            assert np.allclose(to_mesh @ worlds[j] @ inverse_bind[k], np.eye(4), atol=1e-5), (name, level, joints[k])
        turned: dict[tuple[str, str], float] = {}
        for anim in gltf["animations"]:
            for channel in anim["channels"]:
                node = gltf["nodes"][channel["target"]["node"]]
                values = _accessor(gltf, blob, anim["samplers"][channel["sampler"]]["output"])
                # a pose only turns bones: any other channel holds the joint's rest
                if channel["target"]["path"] == "translation":
                    assert np.allclose(values, node.get("translation", (0, 0, 0)), atol=1e-5), (name, anim["name"])
                elif channel["target"]["path"] == "scale":
                    assert np.allclose(values, 1.0, atol=1e-5), (name, anim["name"])
                else:
                    rest_turn = np.array(node.get("rotation", (0.0, 0.0, 0.0, 1.0)))
                    cosine = np.clip(np.abs(values @ rest_turn), 0.0, 1.0)
                    turned[(anim["name"], node["name"])] = float(np.degrees(2 * np.arccos(cosine)).max())
        for pose in ("hover", "cruise", "brake"):
            # the app leans and banks the whole figure (MOTION's design as the lead decided it, 25 Nov 03:22), so a pose
            # leaves the pelvis and the spine at rest and moves limbs: each upper arm turns more than 10 degrees from the
            # stance NASA modelled (measured 23 to 52)
            assert max(turned.get((pose, "pelvis"), 0.0), turned.get((pose, "spine"), 0.0)) < 0.5, (name, level, pose)
            named = rig["poses"][pose]
            if named:  # the EMU: the limbs it names turn, and nothing else
                assert max(turned[(pose, bone)] for bone in named) > 10.0, (name, level, pose)
            assert max(v for (a, bone), v in turned.items() if a == pose and bone not in named) < 0.5, (name, level, pose)
        # the import frame from the joints: gltf = s R import + t, fitted on the recipe's bone heads
        here = np.array([(to_mesh @ worlds[j])[:3, 3] for j in skin["joints"]])
        there = np.array([next(b["head"] for b in rig["bones"] if b["name"] == n) for n in joints], dtype=np.float64)
        u, sv, vt = np.linalg.svd((here - here.mean(0)).T @ (there - there.mean(0)))
        turn = u @ np.diag([1.0, 1.0, np.sign(np.linalg.det(u @ vt))]) @ vt
        scale = float(np.sum(sv * np.array([1.0, 1.0, np.sign(np.linalg.det(u @ vt))])) / np.sum((there - there.mean(0)) ** 2))
        shift = here.mean(0) - scale * turn @ there.mean(0)
        assert np.allclose(here, scale * there @ turn.T + shift, atol=1e-4), (name, level)
        points, which, weight, triangles = [], [], [], []
        for mesh in gltf["meshes"]:
            for prim in mesh["primitives"]:
                assert "WEIGHTS_1" not in prim["attributes"]
                indices = _accessor(gltf, blob, prim["indices"]).reshape(-1, 3).astype(np.int64)
                triangles.append(indices + sum(len(p) for p in points))
                points.append(_accessor(gltf, blob, prim["attributes"]["POSITION"]))
                which.append(_accessor(gltf, blob, prim["attributes"]["JOINTS_0"]).astype(np.int64))
                weight.append(_accessor(gltf, blob, prim["attributes"]["WEIGHTS_0"]))
        points, which, weight = np.concatenate(points), np.concatenate(which), np.concatenate(weight)
        assert np.allclose(weight.sum(axis=1), 1.0, atol=2e-3), (name, level)
        carried = (weight > 1e-3).sum(axis=1)
        assert carried.max() <= 4 and carried.min() == 1, (name, level)
        # only the EMU's fabric bends, and across a band at each joint: its second bone carries 0.1 or more on 0.074,
        # 0.068 and 0.044 of the EMU's vertices at lod0, lod1 and lod2, each joint blended over 0.1 m (fabric weighted to
        # one bone only, the blends of a level's reduction alone reach 0.008 at most, `.lanetools/weight_band.py`)
        bend = float(np.mean(np.sort(weight, axis=1)[:, -2] >= 0.1))
        if "skin_groups" in rig:
            assert bend >= 0.02, (name, level, round(bend, 4))
        else:
            assert carried.max() == 1, (name, level, "a suit of separate parts bends nowhere")
        single = np.where(carried == 1, which[np.arange(len(which)), weight.argmax(axis=1)], -1)
        part = _whole_parts(points, np.concatenate(triangles))
        for label in np.unique(part):
            on = single[part == label]
            if (on < 0).any():
                # a weighted part is the EMU's fabric from boots to chest or an arm, never a lamp or a glove: in trial
                # rig4 four helmet lamps, as dense as an arm, were weighted between spine and pelvis
                assert np.ptp(points[part == label], axis=0).max() >= 0.4, (name, level, "a small part weighted")
                continue
            assert len(set(on.tolist())) == 1, (name, level, "a whole part split across bones")
            # the pack and the helmet by the joints alone, whatever the recipe says (the suit faces +Z with +Y up): at
            # lod0, where every part is whole, a part 0.1 m behind the spine's joint or 0.08 m above the shoulders' rides
            # the spine (measured: the EMU's pack rails 0.15 m behind, its arms' bearings 0.07; helmets 0.15 m above the
            # shoulders and more, arm parts 0.01 at most)
            middle = points[part == label].mean(0)
            spine = here[joints.index("spine")]
            shoulders = max(here[joints.index("upperarm.L")][1], here[joints.index("upperarm.R")][1])
            if level == "lod0" and (spine[2] - middle[2] > 0.1 or middle[1] - shoulders > 0.08):
                assert joints[int(on[0])] == "spine", (name, level, np.round(middle, 3), joints[int(on[0])])
            centre = np.linalg.inv(turn) @ ((middle - shift) / scale)
            for hold in rig.get("hold", []):
                value = centre["xyz".index(hold["axis"])]
                past = value > hold.get("above", np.inf) or value < hold.get("below", -np.inf)
                if past and "within_m" in hold:
                    of = np.array(next(b["head"] for b in rig["bones"] if b["name"] == hold["of"]))
                    across = np.delete(centre - of, "xyz".index(hold["axis"]))
                    past = float(np.hypot(*across)) < hold["within_m"]
                if past:
                    assert joints[int(on[0])] == hold["bone"], (name, level, np.round(centre, 3), joints[int(on[0])])
                    break


@pytest.mark.parametrize("name", RIGGED)
def test_no_two_touching_parts_of_a_rigged_suit_come_apart_in_any_pose(name: str) -> None:
    # suit-rig-one version two (AUDIT-B's verdict of 25 Nov: in the cruise the Z-2's upper arms left their shoulder rings
    # by 15 cm, and the EMU's right arm carried a chest fitting and its hose 24 cm away from the chest): every two whole
    # parts touching at rest (within 5 mm) stay within 2 cm of each other in every pose, at every level, the app's own
    # skinning done here from the file alone. No joint gap is excused: the EMU's fabric bends between bones, its hard
    # parts at the torso ride the spine, and the Z-2, all hard segments meeting at rings, keeps NASA's stance.
    for level in ("lod0", "lod1", "lod2"):
        rigged = _rigged_level(HANGAR_DIR / FACTS["models"][name]["files"][level]["path"])
        part, points = rigged["part"], rigged["points"]
        for (a, b), gaps in _openings(rigged).items():
            worst = max(gaps, key=gaps.get)
            where = np.round(points[part == a].mean(0), 3).tolist()
            assert gaps[worst] <= 0.02, (name, level, worst, round(gaps[worst], 3), rigged["bone_of"][a], where, rigged["bone_of"][b])


SHADED = sorted(name for name, recipe in CRAFTS.items() if recipe.get("vertex_ao"))
AO_GROUP_ONE = {"clementine", "deepimpact", "ds1", "ibex", "kepler", "maven", "mgs", "near", "parker", "stardust"}
AO_GROUP_TWO = {"dawn", "juno", "messenger", "mro", "odyssey", "pioneer10", "spitzer", "tdrs", "vanallen", "viking"}
AO_GROUP_THREE = {
    "aces", "apollo_lm", "apollosoyuz", "gemini", "gemini_suit", "ingenuity", "insight", "mark3", "mercury_suit", "perseverance"
}


@pytest.mark.parametrize("name", SHADED)
def test_a_craft_given_fill_occlusion_carries_it_at_every_vertex_of_every_level(name: str) -> None:
    # craft-ao-one (the lead's ruling of 25 Nov on LOOK's (c)): the sky fill's shadowing of the model itself, per vertex,
    # as the attribute _AO the app applies to the fill alone: on every primitive of every level, one float a vertex
    # between 0 and 1, varying over the model as real shadowing does (lit at the outside, shut in the creases), never a
    # colour (three would darken the key with a COLOR_0), and the record's sentence says so. Measured means: EMU 0.42
    # to 0.45, New Horizons 0.64 to 0.68.
    # craft-ao-two adds the other models in groups, each its reach 15 percent of its longest side
    assert set(SHADED) >= {"emu", "newhorizons", "z2"} | AO_GROUP_ONE | AO_GROUP_TWO | AO_GROUP_THREE
    record = {f["path"]: f for f in json.loads(write_hangar.RECORD_PATH.read_text(encoding="utf-8"))["files"]}
    for level in ("lod0", "lod1", "lod2"):
        path = HANGAR_DIR / FACTS["models"][name]["files"][level]["path"]
        assert record[f"hangar/{name}/{path.name}"]["producedBy"]["changes"].count("_AO") == 1, (name, level)
        gltf, blob = _glb(path)
        shade = []
        for mesh in gltf["meshes"]:
            for prim in mesh["primitives"]:
                assert "COLOR_0" not in prim["attributes"], (name, level)
                acc = gltf["accessors"][prim["attributes"]["_AO"]]
                assert (acc["type"], acc["componentType"]) == ("SCALAR", 5126), (name, level)
                assert acc["count"] == gltf["accessors"][prim["attributes"]["POSITION"]]["count"], (name, level)
                shade.append(_accessor(gltf, blob, prim["attributes"]["_AO"]).ravel())
        shade = np.concatenate(shade)
        assert shade.min() >= 0.0 and shade.max() <= 1.0, (name, level)
        assert 0.3 < shade.mean() < 0.9 and shade.std() > 0.1, (name, level, round(shade.mean(), 3), round(shade.std(), 3))
        # the right way round: the outermost vertices (the convex hull's) see more sky than the innermost tenth (nearest
        # the bounds' centre). Measured over the 33 shaded models' 99 levels: the hull 0.876 to 1.000 (the lowest
        # Spitzer's phone level, its decimated panel rims), 0.205 to 0.801 above the innermost tenth (the least Gemini's
        # chase level). A mean over all vertices or over parts alike fails some shapes: the Van Allen Probes' solar faces
        # hold 21,027 of their chase level's 28,383 vertices at 0.99; Gemini's many small outer parts lift its parts' mean.
        from scipy.spatial import ConvexHull

        points = np.concatenate(
            [_accessor(gltf, blob, p["attributes"]["POSITION"]) for m in gltf["meshes"] for p in m["primitives"]]
        )
        outer = shade[np.unique(ConvexHull(points).vertices)].mean()
        dist = np.linalg.norm(points - (points.max(0) + points.min(0)) / 2, axis=1)
        inner = shade[dist <= np.quantile(dist, 0.1)].mean()
        assert outer > 0.85 and outer > inner + 0.15, (name, level, round(outer, 3), round(inner, 3))


@pytest.mark.parametrize("name", sorted(CRAFTS))
def test_every_source_material_has_a_role_and_rules_end_in_a_catch_all(name: str) -> None:
    recipe = CRAFTS[name]
    rules = recipe.get("material_rules", [])
    assert recipe.get("materials") or rules
    if rules:
        assert rules[-1]["match"] == [""] and rules[-1]["role"] in {"nasa_flat", "maps_mean"}
        for rule in rules:
            if rule["role"] == "pbr":
                assert all(0 <= c <= 1 for c in rule["base"]) and 0 <= rule["metallic"] <= 1 and 0 < rule["roughness"] <= 1


def test_a_craft_that_never_travels_between_stars_has_no_speed() -> None:
    near = [e for e in FACTS["entries"] if e["travel"] is None]
    near_ids = {e["id"] for e in near}
    assert near_ids <= {"jwst", "hubble", "astronaut-emu", "z2", "apollo-lm", "gemini", "mercury-suit", "mark-iii", "aces", "gemini-suit", "iss", "shuttle", "parker-solar-probe", "juno", "dawn", "messenger", "spitzer", "osiris-rex", "mro", "stardust", "deep-space-1", "mars-global-surveyor", "near-shoemaker", "maven", "cassini", "ibex", "van-allen-probes", "deep-impact", "viking-lander", "apollo-soyuz", "kepler", "ingenuity", "perseverance", "clementine", "tdrs", "mars-odyssey", "insight"}
    assert "clementine" in near_ids and FACTS["models"]["clementine"]["scaleFrom"] == {
        "part": None,
        "metres": 2,
        "trueTo": "part",
        "page": "https://nssdc.gsfc.nasa.gov/planetary/cleminfo.html",
        "quote": "The spacecraft consists of an octagonal prism about 2 meters high.",
    }
    assert "tdrs" in near_ids and FACTS["models"]["tdrs"]["scaleFrom"] == {
        "part": "tex_02",
        "metres": 5,
        "trueTo": "part",
        "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2002-011A",
        "quote": "With a mass of 1,781 kg, power of 2.3 kW, a pair of steerable, transponding antennas of diameter 5 m, and pair of solar arrays of length 21 m,",
    }
    assert "mars-odyssey" in near_ids and FACTS["models"]["odyssey"]["scaleFrom"] == {
        "part": "foil_gold",
        "partBand": {"axis": "y", "min": -0.75, "max": 0.95},
        "metres": 2.2,
        "trueTo": "part",
        "page": "https://nssdc.gsfc.nasa.gov/nmc/spacecraft/display.action?id=2001-013A",
        "quote": "The main body of the 2001 Mars Odyssey is box-shaped, 2.2 meters x 1.7 meters x 2.6 meters.",
    }
    assert "insight" in near_ids and FACTS["models"]["insight"]["scaleFrom"] == {
        "part": None,
        "metres": 6,
        "trueTo": "part",
        "page": "https://science.nasa.gov/mission/insight/spacecraft/",
        "quote": '19 feet 8 inches (6 meters) with solar panels deployed ("wingspan")',
    }
    for e in FACTS["entries"]:
        if e["travel"] is not None:
            assert e["travel"]["speedKmS"] > 0


# The Blender the committed models were built with; another version may write other bytes from the same recipe.
BUILT_WITH = "Blender 5.2.3"


def _blender_version() -> str | None:
    blender = shutil.which("blender")
    if blender is None:
        return None
    out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=120).stdout
    return out.splitlines()[0] if out else None


def _rebuild_inputs(craft: str) -> list[str]:
    """The NASA files the build reads for one craft, as the shared cache holds them and data/downloads.json lists them
    (a craft whose picker is rendered from the build reads no NASA picture)."""
    src = CRAFTS[craft]["source"]
    return [f"{CACHE}/{src['local']}/{src[key]}" for key in ("file", "picture") if src.get(key) is not None]


# Every craft built again since the hangar moved to Blender 5.2.3: each that carries the sky fill's occlusion, the
# station, the Shuttle, Cassini and OSIRIS-REx (their phone and chase levels decimated part by part) and Hubble (its
# phone textures capped). A recipe change to any of them that the committed models do not follow fails here.
LEVELS_REBUILT = {"cassini", "hubble", "iss", "osirisrex", "shuttle"}
REBUILT = sorted(set(SHADED) | LEVELS_REBUILT)


def test_the_rebuild_covers_every_craft_rebuilt_in_blender_5_2_3() -> None:
    assert set(REBUILT) >= {"emu", "newhorizons", "z2"} | AO_GROUP_ONE | AO_GROUP_TWO | AO_GROUP_THREE | LEVELS_REBUILT
    assert set(REBUILT) <= set(CRAFTS)


@pytest.mark.parametrize("craft", [pytest.param(c, marks=pytest.mark.local_dataset(*_rebuild_inputs(c))) for c in REBUILT])
def test_an_unchanged_craft_rebuilds_byte_for_byte(craft: str, tmp_path: Path) -> None:
    # Hubble's one mesh sits under a transformed parent in NASA's file: a build that drops that transform when it
    # unparents the meshes comes out at another scale and shape (AUDIT-B's read of hangar-20); New Horizons' picker is
    # rendered from the build because its recipe names a reason, so its picture checks that path. Runs only on a machine
    # with NASA's source in the shared cache (the local_dataset marker skips it where the cache is absent, as on CI) and
    # the Blender the hangar was built with: under --no-unexpected-skips a machine that has the cache but another
    # Blender fails here rather than passing without the check.
    version = _blender_version()
    if version is None or not version.startswith(BUILT_WITH):
        pytest.skip(f"needs {BUILT_WITH} (found {version})")
    script = ROOT / "scripts" / "lookdev" / "hangar" / "build_craft.py"
    cmd = [shutil.which("blender"), "-b", "--factory-startup", "-P", str(script), "--", "--craft", craft, "--out", str(tmp_path)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert done.returncode == 0, done.stderr[-2000:]
    for level in ("lod0", "lod1", "lod2", "picker"):
        name = f"{craft}_{level}.webp" if level == "picker" else f"{craft}_{level}.glb"
        assert (tmp_path / name).read_bytes() == (HANGAR_DIR / craft / name).read_bytes(), name
