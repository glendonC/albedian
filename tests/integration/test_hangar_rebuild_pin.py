"""The hangar's rebuild pin (scripts/lookdev/hangar/rebuild_pin.py and rebuild_pin.json, ASSETS lane).

- a craft whose recipe and shipped files are what its pin holds is judged in seconds, with no Blender run;
- any input of its recipe that moves (build_craft.py's bytes, a module it imports, a crafts.py name it imports, its
  own CRAFTS entry with the NASA source's SHA-256 in it, the Blender it is built with) moves the pin and names that
  input; the craft is then rebuilt, and it fails until it is rebuilt and repinned;
- a shipped file unlike its record, or unlike the file its unmoved recipe built at the pin, fails without a rebuild;
- ALBEDIAN_HANGAR_REBUILD_ALL=1 rebuilds every craft; a rebuild that cannot run here fails, it never skips;
- the pin writer pins a craft only after its rebuild came out byte for byte as shipped.
No case here runs Blender: the rebuild is replaced by a stand-in that writes chosen bytes, and the real rebuild runs in
test_hangar_assets.py when a pin moves or on the switch.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.lookdev.hangar import crafts, rebuild_pin
from scripts.lookdev.hangar.crafts import CRAFTS

ROOT = Path(__file__).resolve().parents[2]
CRAFT = "clementine"
NEVER = "the judge rebuilt a craft whose recipe and files are as pinned"


def _no_rebuild(craft: str, out: Path) -> dict[str, str]:
    raise AssertionError(NEVER)


def _rebuild_as_shipped(hangar: Path):
    def rebuild(craft: str, out: Path) -> dict[str, str]:
        return rebuild_pin.shipped_digests(craft, hangar)

    return rebuild


def _rebuild_unlike_shipped(craft: str, out: Path) -> dict[str, str]:
    return {name: "0" * 64 for name in rebuild_pin.shipped_names(craft)}


@pytest.fixture
def setup(tmp_path: Path):
    """A copy of the build's scripts and of one craft's shipped files, with its record and its pin as they stand now."""
    root = tmp_path / "root"
    for rel in ("scripts/lookdev/hangar/build_craft.py", "scripts/lookdev/hangar/geometry.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, root / rel)
    hangar = tmp_path / "hangar"
    (hangar / CRAFT).mkdir(parents=True)
    for name in rebuild_pin.shipped_names(CRAFT):
        shutil.copyfile(rebuild_pin.HANGAR_DIR / CRAFT / name, hangar / CRAFT / name)
    shipped = rebuild_pin.shipped_digests(CRAFT, hangar)
    record = {"files": [{"path": f"hangar/{CRAFT}/{name}", "sha256": digest} for name, digest in shipped.items()]}
    pin = {"crafts": {CRAFT: {"recipe": rebuild_pin.recipe_parts(CRAFT, root), "files": dict(shipped)}}}
    return {"root": root, "hangar": hangar, "record": record, "pin": pin, "out": tmp_path / "out"}


def _judge(s: dict, rebuild_all: bool = False, rebuild_fn=_no_rebuild, craft: str = CRAFT) -> rebuild_pin.Verdict:
    return rebuild_pin.judge(
        craft,
        rebuild_all=rebuild_all,
        out=s["out"],
        pin=s["pin"],
        root=s["root"],
        hangar_dir=s["hangar"],
        record=s["record"],
        rebuild_fn=rebuild_fn,
    )


def test_an_unmoved_recipe_with_its_pinned_files_passes_without_a_rebuild(setup) -> None:
    v = _judge(setup)
    assert v.problems == [] and not v.rebuilt and v.path.startswith("pinned")


def test_every_committed_craft_is_judged_as_pinned_with_no_rebuild(tmp_path: Path) -> None:
    for craft in sorted(CRAFTS):
        v = rebuild_pin.judge(craft, rebuild_all=False, out=tmp_path / craft, rebuild_fn=_no_rebuild)
        assert (v.path.startswith("pinned"), v.problems) == (True, []), (craft, v)


def test_the_recipe_covers_the_build_script_its_imports_its_own_entry_and_the_blender() -> None:
    parts = rebuild_pin.recipe_parts(CRAFT)
    assert sorted(parts) == sorted(
        [
            "scripts/lookdev/hangar/build_craft.py",
            "scripts/lookdev/hangar/geometry.py",
            "crafts.CACHE",
            f"crafts.CRAFTS[{CRAFT!r}]",
            "crafts.LOD_RATIO",
            "crafts.PICKER_EDGE",
            "crafts.TEXTURE_EDGE",
            "tool",
        ]
    )
    assert parts["tool"] == rebuild_pin.BUILT_WITH == "Blender 5.2.3"


def _moved_by(s: dict, monkeypatch: pytest.MonkeyPatch, change: str) -> str:
    """Make one change to the recipe's inputs and return the input it should be named for."""
    entry = CRAFTS[CRAFT]
    if change == "build script":
        p = s["root"] / "scripts/lookdev/hangar/build_craft.py"
        p.write_bytes(p.read_bytes() + b"\n")
        return "scripts/lookdev/hangar/build_craft.py"
    if change == "imported module":
        p = s["root"] / "scripts/lookdev/hangar/geometry.py"
        p.write_bytes(p.read_bytes().replace(b"from math import dist", b"from math import dist  # moved"))
        return "scripts/lookdev/hangar/geometry.py"
    if change == "level ratio":
        monkeypatch.setitem(crafts.LOD_RATIO, "lod2", 0.17)
        return "crafts.LOD_RATIO"
    if change == "texture edge":
        monkeypatch.setitem(crafts.TEXTURE_EDGE, "lod1", 1023)
        return "crafts.TEXTURE_EDGE"
    if change == "picker edge":
        monkeypatch.setattr(crafts, "PICKER_EDGE", crafts.PICKER_EDGE + 1)
        return "crafts.PICKER_EDGE"
    if change == "cache folder":
        monkeypatch.setattr(crafts, "CACHE", crafts.CACHE + "x")
        return "crafts.CACHE"
    if change == "entry key":
        monkeypatch.setitem(entry, "probe_key", 1)
        return f"crafts.CRAFTS[{CRAFT!r}]"
    if change == "nasa source digest":
        monkeypatch.setitem(entry["source"], "sha256", "f" * 64)
        return f"crafts.CRAFTS[{CRAFT!r}]"
    if change == "a tuple made a list":
        key = next(k for k, v in entry.items() if isinstance(v, tuple))
        monkeypatch.setitem(entry, key, list(entry[key]))
        return f"crafts.CRAFTS[{CRAFT!r}]"
    if change == "an int made a float":
        src = entry["source"]
        monkeypatch.setitem(src, "bytes", float(src["bytes"]))
        return f"crafts.CRAFTS[{CRAFT!r}]"
    if change == "blender":
        monkeypatch.setattr(rebuild_pin, "BUILT_WITH", "Blender 5.3.0")
        return "tool"
    raise AssertionError(change)


CHANGES = [
    "build script",
    "imported module",
    "level ratio",
    "texture edge",
    "picker edge",
    "cache folder",
    "entry key",
    "nasa source digest",
    "a tuple made a list",
    "an int made a float",
    "blender",
]


@pytest.mark.parametrize("change", CHANGES)
def test_each_input_of_the_recipe_moves_the_pin_and_names_itself(setup, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    named = _moved_by(setup, monkeypatch, change)
    assert rebuild_pin.moved_from_pin(CRAFT, setup["pin"], setup["root"]) == [named]


@pytest.mark.parametrize("change", CHANGES)
def test_a_moved_recipe_with_the_files_unrebuilt_rebuilds_and_fails(setup, monkeypatch, change: str) -> None:
    named = _moved_by(setup, monkeypatch, change)
    v = _judge(setup, rebuild_fn=_rebuild_unlike_shipped)
    assert v.rebuilt and v.path.startswith("rebuilt") and named in v.path
    differs = [p for p in v.problems if "rebuilt from its recipe, it differs from the shipped file" in p]
    assert len(differs) == 4
    assert any("repin" in p for p in v.problems)


def test_a_moved_recipe_whose_rebuild_matches_still_fails_until_it_is_repinned(setup, monkeypatch) -> None:
    _moved_by(setup, monkeypatch, "entry key")
    v = _judge(setup, rebuild_fn=_rebuild_as_shipped(setup["hangar"]))
    assert v.rebuilt and len(v.problems) == 1
    assert "the rebuild matches the shipped files" in v.problems[0]
    assert f"scripts/lookdev/hangar/rebuild_pin.py --craft {CRAFT}" in v.problems[0]


def test_a_craft_with_no_pin_is_rebuilt_and_fails(setup) -> None:
    setup["pin"]["crafts"].pop(CRAFT)
    v = _judge(setup, rebuild_fn=_rebuild_as_shipped(setup["hangar"]))
    assert v.rebuilt and "no pin" in v.path and len(v.problems) == 1


def _tamper(s: dict, name: str) -> str:
    p = s["hangar"] / CRAFT / name
    data = bytearray(p.read_bytes())
    data[-1] ^= 0xFF
    p.write_bytes(bytes(data))
    return rebuild_pin.shipped_digests(CRAFT, s["hangar"])[name]


def test_a_shipped_file_unlike_its_record_fails_without_a_rebuild(setup) -> None:
    _tamper(setup, f"{CRAFT}_lod2.glb")
    v = _judge(setup)
    assert not v.rebuilt
    assert any(f"{CRAFT}_lod2.glb" in p and "PROVENANCE.json" in p for p in v.problems)


def test_a_shipped_file_changed_with_its_record_fails_against_the_pin_without_a_rebuild(setup) -> None:
    name = f"{CRAFT}_picker.webp"
    digest = _tamper(setup, name)
    for f in setup["record"]["files"]:
        if f["path"].endswith(name):
            f["sha256"] = digest
    v = _judge(setup)
    assert not v.rebuilt and len(v.problems) == 1
    assert name in v.problems[0] and "pin" in v.problems[0]


def test_a_missing_shipped_file_fails(setup) -> None:
    (setup["hangar"] / CRAFT / f"{CRAFT}_lod1.glb").unlink()
    v = _judge(setup)
    assert any(f"{CRAFT}_lod1.glb" in p and "missing" in p for p in v.problems)


def test_the_switch_rebuilds_an_unmoved_craft(setup) -> None:
    v = _judge(setup, rebuild_all=True, rebuild_fn=_rebuild_as_shipped(setup["hangar"]))
    assert v.rebuilt and rebuild_pin.SWITCH in v.path and v.problems == []
    v = _judge(setup, rebuild_all=True, rebuild_fn=_rebuild_unlike_shipped)
    assert v.rebuilt and len(v.problems) == 4


def test_a_rebuild_that_cannot_run_here_fails_and_never_skips(setup, monkeypatch) -> None:
    def cannot(craft: str, out: Path) -> dict[str, str]:
        raise rebuild_pin.CannotRebuild("needs Blender 5.2.3 (found None)")

    v = _judge(setup, rebuild_all=True, rebuild_fn=cannot)
    assert not v.rebuilt and v.path.startswith("not rebuilt") and len(v.problems) == 1
    _moved_by(setup, monkeypatch, "entry key")
    v = _judge(setup, rebuild_fn=cannot)
    assert not v.rebuilt and v.path.startswith("not rebuilt") and len(v.problems) == 2


@pytest.mark.parametrize(
    ("environ", "expected"),
    [({}, False), ({rebuild_pin.SWITCH: ""}, False), ({rebuild_pin.SWITCH: "0"}, False), ({rebuild_pin.SWITCH: "1"}, True)],
)
def test_the_switch_is_one_for_every_craft(environ: dict, expected: bool) -> None:
    assert rebuild_pin.rebuild_all_switch(environ) is expected


@pytest.mark.parametrize("value", ["yes", "true", "2", " 1", "all"])
def test_any_other_value_of_the_switch_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match=rebuild_pin.SWITCH):
        rebuild_pin.rebuild_all_switch({rebuild_pin.SWITCH: value})


def test_the_real_rebuild_refuses_another_blender_and_a_missing_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rebuild_pin, "blender_version", lambda: None)
    with pytest.raises(rebuild_pin.CannotRebuild, match="needs Blender 5.2.3"):
        rebuild_pin.rebuild(CRAFT, tmp_path)
    monkeypatch.setattr(rebuild_pin, "blender_version", lambda: "Blender 5.1.0")
    with pytest.raises(rebuild_pin.CannotRebuild, match="found Blender 5.1.0"):
        rebuild_pin.rebuild(CRAFT, tmp_path)
    monkeypatch.setattr(rebuild_pin, "blender_version", lambda: "Blender 5.2.3 (hash 0)")
    with pytest.raises(rebuild_pin.CannotRebuild, match="NASA source"):
        rebuild_pin.rebuild(CRAFT, tmp_path, root=tmp_path)


def test_the_canonical_form_keeps_order_types_and_refuses_what_it_cannot_write() -> None:
    c = rebuild_pin.canonical
    assert c((1, 2)) != c([1, 2])
    assert c(1) != c(1.0) and c(True) != c(1) and c(None) != c("None") and c("1") != c(1)
    assert c({"a": 1, "b": 2}) != c({"b": 2, "a": 1})  # a reorder rebuilds, never the other way round
    assert c(0.1 + 0.2) != c(0.3)
    with pytest.raises(TypeError):
        c({1, 2})
    with pytest.raises(TypeError):
        c(lambda: None)


def test_a_module_or_crafts_name_the_build_comes_to_import_joins_the_recipe(setup) -> None:
    root = setup["root"]
    extra = root / "scripts/lookdev/hangar/extra_helper.py"
    extra.write_text("X = 1\n", encoding="utf-8")
    build = root / "scripts/lookdev/hangar/build_craft.py"
    text = build.read_text(encoding="utf-8")
    anchor = "from scripts.lookdev.hangar.geometry import by_part_at  # noqa: E402\n"
    assert anchor in text
    build.write_text(
        text.replace(
            anchor,
            anchor
            + "from scripts.lookdev.hangar import extra_helper  # noqa: E402\n"
            + "from scripts.lookdev.hangar.crafts import SUIT_POSES as POSES  # noqa: E402\n",
        ),
        encoding="utf-8",
    )
    parts = rebuild_pin.recipe_parts(CRAFT, root)
    assert "scripts/lookdev/hangar/extra_helper.py" in parts and "crafts.SUIT_POSES" in parts
    before = parts["scripts/lookdev/hangar/extra_helper.py"]
    extra.write_text("X = 2\n", encoding="utf-8")
    assert rebuild_pin.recipe_parts(CRAFT, root)["scripts/lookdev/hangar/extra_helper.py"] != before


def test_another_crafts_entry_leaves_this_crafts_pin_where_it_was(setup, monkeypatch) -> None:
    other = next(name for name in sorted(CRAFTS) if name != CRAFT)
    monkeypatch.setitem(CRAFTS[other], "probe_key", 1)
    assert rebuild_pin.moved_from_pin(CRAFT, setup["pin"], setup["root"]) == []


def test_a_module_the_build_stops_importing_is_named_with_the_build_script(setup) -> None:
    root = setup["root"]
    (root / "scripts/lookdev/hangar/extra_helper.py").write_text("X = 1\n", encoding="utf-8")
    build = root / "scripts/lookdev/hangar/build_craft.py"
    text = build.read_text(encoding="utf-8")
    build.write_text(text + "\nfrom scripts.lookdev.hangar.extra_helper import X\n", encoding="utf-8")
    pin = {"crafts": {CRAFT: {"recipe": rebuild_pin.recipe_parts(CRAFT, root), "files": {}}}}
    assert "scripts/lookdev/hangar/extra_helper.py" in pin["crafts"][CRAFT]["recipe"]
    build.write_text(text, encoding="utf-8")
    assert rebuild_pin.moved_from_pin(CRAFT, pin, root) == [
        "scripts/lookdev/hangar/build_craft.py",
        "scripts/lookdev/hangar/extra_helper.py",
    ]


def test_an_import_of_a_repository_module_that_is_not_there_is_refused(setup) -> None:
    build = setup["root"] / "scripts/lookdev/hangar/build_craft.py"
    build.write_text(build.read_text(encoding="utf-8") + "\nfrom scripts.lookdev.hangar.absent import X\n", encoding="utf-8")
    with pytest.raises(ValueError, match="scripts.lookdev.hangar.absent"):
        rebuild_pin.recipe_parts(CRAFT, setup["root"])


def _write_setup(s: dict, tmp_path: Path) -> Path:
    pin_path = tmp_path / "rebuild_pin.json"
    s["pin"]["crafts"][CRAFT]["files"] = {"stale": "0" * 64}
    pin_path.write_text(json.dumps(s["pin"]), encoding="utf-8")
    return pin_path


def test_the_writer_pins_a_craft_only_after_its_rebuild_matches(setup, tmp_path: Path) -> None:
    pin_path = _write_setup(setup, tmp_path)
    pinned, refused = rebuild_pin.write_pins(
        [CRAFT], pin_path=pin_path, rebuild_fn=_rebuild_unlike_shipped, root=setup["root"], hangar_dir=setup["hangar"]
    )
    assert (pinned, [r[0] for r in refused]) == ([], [CRAFT])
    assert json.loads(pin_path.read_text(encoding="utf-8"))["crafts"][CRAFT]["files"] == {"stale": "0" * 64}
    pinned, refused = rebuild_pin.write_pins(
        [CRAFT], pin_path=pin_path, rebuild_fn=_rebuild_as_shipped(setup["hangar"]), root=setup["root"], hangar_dir=setup["hangar"]
    )
    assert (pinned, refused) == ([CRAFT], [])
    written = json.loads(pin_path.read_text(encoding="utf-8"))
    assert written["crafts"][CRAFT] == {
        "recipe": rebuild_pin.recipe_parts(CRAFT, setup["root"]),
        "files": rebuild_pin.shipped_digests(CRAFT, setup["hangar"]),
    }
    assert (written["format"], written["formatVersion"]) == (rebuild_pin.PIN_FORMAT, 1)


def test_the_writer_refuses_a_craft_whose_recipe_moved_while_it_was_rebuilt(setup, tmp_path: Path) -> None:
    pin_path = _write_setup(setup, tmp_path)
    as_shipped = _rebuild_as_shipped(setup["hangar"])

    def rebuild_then_edit(craft: str, out: Path) -> dict[str, str]:
        p = setup["root"] / "scripts/lookdev/hangar/build_craft.py"
        p.write_bytes(p.read_bytes() + b"\n")
        return as_shipped(craft, out)

    pinned, refused = rebuild_pin.write_pins(
        [CRAFT], pin_path=pin_path, rebuild_fn=rebuild_then_edit, root=setup["root"], hangar_dir=setup["hangar"]
    )
    assert pinned == [] and refused[0][0] == CRAFT and "moved while" in refused[0][1]


def test_the_committed_pin_is_what_the_writer_writes() -> None:
    pin = rebuild_pin.read_pin()
    assert sorted(pin) == ["about", "crafts", "format", "formatVersion"] and sorted(pin["crafts"]) == sorted(CRAFTS)
    assert (pin["about"], pin["format"], pin["formatVersion"]) == (rebuild_pin.PIN_ABOUT, rebuild_pin.PIN_FORMAT, 1)
    assert all(entry["recipe"]["tool"] == rebuild_pin.BUILT_WITH for entry in pin["crafts"].values())
    assert rebuild_pin.PIN_PATH.read_text(encoding="utf-8") == rebuild_pin.dumps_pin(pin)
