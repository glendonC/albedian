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

import pytest

from scripts.lookdev.hangar import write_hangar
from scripts.lookdev.hangar.crafts import BUDGET_BYTES, CACHE, CRAFTS, HANGAR

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
            budget = {**BUDGET_BYTES, **CRAFTS[name].get("budget_bytes", {})}[level]
            assert size == f["bytes"] <= f["budgetBytes"] == budget, (name, level)
            # a craft's own budget is only ever for its close view, and never past twice the shared one
            assert level == "lod0" or budget == BUDGET_BYTES[level]
            assert budget <= 2 * BUDGET_BYTES[level]


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
            for node in gltf["nodes"]:
                assert not {"matrix", "rotation", "scale", "translation"} & set(node), (name, level)
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
        if CRAFTS[name].get("decimate_by_material"):
            assert set(p1) == set(p0) and all(v > 0 for v in p1.values()), (name, p1)
            assert set(p2) == set(p0) and all(v > 0 for v in p2.values()), (name, p2)
            for axis, (s0, s2) in enumerate(zip(_glb_spans(g0), _glb_spans(g2))):
                assert s2 >= 0.95 * s0, (name, axis, s0, s2)
    assert CRAFTS["odyssey"].get("decimate_by_material") is True


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
    assert "newhorizons" in rendered and "perseverance" in rendered
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
    assert near_ids <= {"jwst", "hubble", "astronaut-emu", "z2", "apollo-lm", "gemini", "mercury-suit", "mark-iii", "aces", "gemini-suit", "iss", "shuttle", "parker-solar-probe", "juno", "dawn", "messenger", "spitzer", "osiris-rex", "mro", "stardust", "deep-space-1", "mars-global-surveyor", "near-shoemaker", "maven", "cassini", "ibex", "van-allen-probes", "deep-impact", "viking-lander", "apollo-soyuz", "kepler", "ingenuity", "perseverance", "clementine", "tdrs", "mars-odyssey"}
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
    """The NASA files the build reads for one craft, as the shared cache holds them and data/downloads.json lists them."""
    src = CRAFTS[craft]["source"]
    return [f"{CACHE}/{src['local']}/{src[key]}" for key in ("file", "picture")]


@pytest.mark.parametrize(
    "craft", [pytest.param(c, marks=pytest.mark.local_dataset(*_rebuild_inputs(c))) for c in ("hubble", "newhorizons")]
)
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
