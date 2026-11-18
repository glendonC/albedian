"""The designation tables and the functions beside the catalog reference (catalog-2b).

scripts/bake_designation_tables.py writes the tables as twin modules (packages/contracts/src/designation-tables.ts,
services/contracts/designation_tables.py) from the baked HYG designations, Unicode's letter names (one stated exception:
Lambda) and d3-celestial's constellations.json (registered in data/downloads.json). services/contracts/designation.py and
packages/contracts/src/designation.ts make the wire form, the compact form and the title order from them;
tests/fixtures/designation_cases.json holds the cases both sides answer alike.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from collections import Counter
from typing import Any

import pytest

from services.contracts import designation as dz
from services.contracts.designation_tables import (
    DESIGNATION_CONSTELLATIONS,
    DESIGNATION_INDICES,
    DESIGNATION_LETTERS,
    FLAMSTEED_NUMBER_MAX,
)
from services.science.catalog import ALL_SKY_CATALOG_STARS

ROOT = Path(__file__).resolve().parents[2]
D3_REL = ".cache/d3celestial/constellations.json"
STARS = ROOT / "apps" / "web" / "public" / "catalog" / "stars"
CASES = ROOT / "tests" / "fixtures" / "designation_cases.json"
UPDATE_ENV = "ALBEDIAN_UPDATE_DESIGNATION_CASES"
# IAU-named rows of the solver's index with a distance: how many to_catalog_star gives a Bayer designation, how many none
NAMED_GIVEN, NAMED_NONE = 244, 119
# Named rows whose matched subtitle held a names file or pinned designation yesterday: each holds the same text today
KEPT_PARTS = 363


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_designation_tables", ROOT / "scripts" / "bake_designation_tables.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("bake_designation_tables", module)
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


def _designations() -> dict[str, Any]:
    return json.loads((STARS / "designations" / "designations.json").read_text(encoding="utf-8"))


# =============================================================================================
# The tables
# =============================================================================================


@pytest.mark.local_dataset(D3_REL)
def test_both_table_modules_are_what_a_fresh_bake_writes() -> None:
    fresh = bake.outputs()
    for path, text in fresh.items():
        assert path.read_text(encoding="utf-8") == text, path


def test_the_letters_are_the_abbreviations_hyg_uses_each_with_the_letter_its_unicode_name_begins() -> None:
    cols = _designations()["columns"]
    used = {r[cols.index("bayer")].partition("-")[0] for r in _designations()["rows"] if r[cols.index("bayer")]}
    assert {abbr for abbr, _, _ in DESIGNATION_LETTERS} == used
    for abbr, glyph, name in DESIGNATION_LETTERS:
        unicode_name = unicodedata.name(glyph)
        assert unicode_name.startswith("GREEK SMALL LETTER ")
        word = unicode_name.removeprefix("GREEK SMALL LETTER ").title()
        assert word.startswith(abbr)
        # The one stated exception: Unicode spells U+03BB LAMDA, the long form writes Lambda
        assert name == ("Lambda" if word == "Lamda" else word), abbr
    assert [g for _, g, _ in DESIGNATION_LETTERS] == sorted(g for _, g, _ in DESIGNATION_LETTERS)


def test_the_letters_are_the_24_greek_small_letters_final_sigma_left_out() -> None:
    letters = bake.greek_letters()
    assert len(letters) == 24 and all(unicodedata.name(g) != bake.FINAL_SIGMA for g, _ in letters)


def test_lambda_is_the_one_exception_to_the_unicode_names() -> None:
    assert bake.NAME_EXCEPTIONS == {"Lamda": "Lambda"}
    assert unicodedata.name("λ") == "GREEK SMALL LETTER LAMDA"
    assert ("Lam", "λ", "Lambda") in DESIGNATION_LETTERS


def test_the_indices_run_from_one_to_the_largest_the_data_holds_with_their_superscripts() -> None:
    cols = _designations()["columns"]
    largest = max(int(r[cols.index("bayer")].partition("-")[2]) for r in _designations()["rows"] if r[cols.index("bayer")] and "-" in r[cols.index("bayer")])
    assert [d for d, _ in DESIGNATION_INDICES] == [str(i) for i in range(1, largest + 1)]
    for digit, glyph in DESIGNATION_INDICES:
        assert unicodedata.name(glyph) == f"SUPERSCRIPT {bake.DIGIT_NAMES[int(digit)]}"


def test_the_flamsteed_bound_is_the_largest_number_the_data_holds() -> None:
    # A future bake with a larger number regenerates the tables, and the contract's bound moves with them
    cols = _designations()["columns"]
    assert FLAMSTEED_NUMBER_MAX == max(r[cols.index("flamsteed")] for r in _designations()["rows"] if r[cols.index("flamsteed")] is not None)
    assert dz.FLAMSTEED_NUMBER_MIN == 1


def test_the_constellations_are_the_88_each_once() -> None:
    abbrs = [a for a, _, _ in DESIGNATION_CONSTELLATIONS]
    assert len(abbrs) == len(set(abbrs)) == 88
    assert ("Ser", "Serpens", "Serpentis") in DESIGNATION_CONSTELLATIONS
    assert all(re.fullmatch(r"[A-Z][A-Za-z]{2}", a) for a in abbrs)


@pytest.mark.local_dataset(D3_REL)
def test_the_constellations_are_d3_celestials_own_rows() -> None:
    rows = {f["properties"]["desig"]: f["properties"] for f in json.loads((ROOT / D3_REL).read_text(encoding="utf-8"))["features"]}
    for abbr, name, genitive in DESIGNATION_CONSTELLATIONS:
        assert rows[abbr]["gen"] == genitive
        assert rows[abbr]["name"] == name or rows[abbr]["name"].startswith(name + " ")


def test_a_constellation_listed_in_parts_takes_the_name_its_parts_share_and_refuses_a_disagreement() -> None:
    def feature(desig: str, name: str, gen: str) -> dict[str, Any]:
        return {"properties": {"desig": desig, "name": name, "gen": gen}}

    others = [feature(f"X{i:02d}", f"Name {i}", f"Gen {i}") for i in range(87)]
    two_parts = [feature("Ser", "Serpens Caput", "Serpentis"), feature("Ser", "Serpens Cauda", "Serpentis")]
    rows = bake.constellation_rows(json.dumps({"features": others + two_parts}).encode())
    assert rows[-1] == ["Ser", "Serpens", "Serpentis"]
    with pytest.raises(ValueError, match="genitives"):
        bake.constellation_rows(json.dumps({"features": others + [two_parts[0], feature("Ser", "Serpens Cauda", "Other")]}).encode())
    with pytest.raises(ValueError, match="share no word"):
        bake.constellation_rows(json.dumps({"features": others + [two_parts[0], feature("Ser", "Cauda", "Serpentis")]}).encode())
    with pytest.raises(ValueError, match="not 88"):
        bake.constellation_rows(json.dumps({"features": others}).encode())


def test_an_abbreviation_no_letter_name_begins_or_that_two_begin_stops_the_bake() -> None:
    with pytest.raises(ValueError, match="begins"):
        bake.letter_rows({"Alp", "Foo"})
    with pytest.raises(ValueError, match="all begin"):
        bake.letter_rows({"Ps", "Psi"})
    # The other way: one abbreviation that begins two letters' names (Omicron and Omega; Pi, Phi and Psi)
    with pytest.raises(ValueError, match="'Om' begins the names Omicron, Omega"):
        bake.letter_rows({"Om"})
    with pytest.raises(ValueError, match="'P' begins the names Pi, Phi, Psi"):
        bake.letter_rows({"Alp", "P"})
    with pytest.raises(ValueError, match="one digit"):
        bake.index_rows(10)


def test_the_input_check_refuses_a_missing_or_changed_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        bake.check_input(tmp_path / "constellations.json")
    (tmp_path / "constellations.json").write_bytes(b"{}")
    with pytest.raises(SystemExit, match="registered"):
        bake.check_input(tmp_path / "constellations.json")


def test_the_tables_source_is_registered() -> None:
    registry = {e["path"]: e for e in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}
    entry = registry[D3_REL]
    assert (entry["url"], entry["bytes"], entry["sha256"]) == (bake.CONSTELLATIONS_URL, bake.CONSTELLATIONS_BYTES, bake.CONSTELLATIONS_SHA256)
    assert entry["licence"] == "BSD-3-Clause" and "Olaf Frohn" in entry["source"]


# =============================================================================================
# The forms and the title order, and the cases both sides share
# =============================================================================================


def _title_cases() -> list[dict[str, Any]]:
    base = {"catalogSourceId": "HIP 4427", "commonName": "HIP 4427", "constellation": "Cas"}
    refs = [
        {**base, "commonName": "Navi", "bayerDesignation": "Gamma Cassiopeiae", "flamsteedNumber": 27},
        {**base, "bayerDesignation": "Gamma Cassiopeiae", "flamsteedNumber": 27},
        {**base, "flamsteedNumber": 27},
        {**base, "constellation": "Cassiopeia", "flamsteedNumber": 27},
        {**base, "constellation": "Nowhere", "flamsteedNumber": 27},
        {**base, "flamsteedNumber": FLAMSTEED_NUMBER_MAX + 1},
        dict(base),
        {**base, "commonName": "Navi"},
        {"catalogSourceId": "Gaia DR3 3337991583943473280", "commonName": "Meissa", "constellation": "Orion", "bayerDesignation": "Lambda Orionis"},
    ]
    out = []
    for ref in refs:
        title, parts = dz.catalog_title(ref)
        out.append({"reference": ref, "title": title, "subtitleParts": list(parts)})
    return out


def _stored_old_shape() -> dict[str, Any]:
    from services.science.designations import upgrade_stored_solution

    before = _readers_old_record()
    return {"before": before, "after": upgrade_stored_solution(before)}


_PROBE_TOKENS: dict[str, Any] = {
    "__NAN__": float("nan"),
    "__INF__": float("inf"),
    "__NEG_INF__": float("-inf"),
    "__NEG_ZERO__": -0.0,
    "__NEL_U0085__": "\u0085",
    "__US_U001F__": "\u001f",
}


def _decode_probe(token: Any) -> Any:
    return _PROBE_TOKENS[token] if isinstance(token, str) and token in _PROBE_TOKENS else token


def _build_probe_ref(parts: list[Any]) -> dict[str, Any]:
    keys = ("catalogSourceId", "commonName", "constellation", "bayerDesignation", "flamsteedNumber")
    return {k: _decode_probe(v) for k, v in zip(keys, parts, strict=True) if v != "__UNDEFINED__"}


FLAMSTEED_PROBES: list[list[Any]] = [
    [1, "And", "1 And", "1 Andromedae"],
    [1.0, "Cas", "1 Cas", "1 Cassiopeiae"],
    [27, "Cas", "27 Cas", "27 Cassiopeiae"],
    [27.0, "Cas", "27 Cas", "27 Cassiopeiae"],
    [119, "Tau", "119 Tau", "119 Tauri"],
    [119.0, "Tau", "119 Tau", "119 Tauri"],
    [62, "Sagittarius", "62 Sgr", "62 Sagittarii"],
    [62.0, "Sagittarius", "62 Sgr", "62 Sagittarii"],
    [FLAMSTEED_NUMBER_MAX, "Vul", "140 Vul", "140 Vulpeculae"],
    [float(FLAMSTEED_NUMBER_MAX), "Vulpecula", "140 Vul", "140 Vulpeculae"],
    [0, "Cas", None, None],
    [0.0, "Cas", None, None],
    ["__NEG_ZERO__", "Cas", None, None],
    [-1, "Cas", None, None],
    [-1.0, "Cas", None, None],
    [FLAMSTEED_NUMBER_MAX + 1, "Cas", None, None],
    [float(FLAMSTEED_NUMBER_MAX + 1), "Cas", None, None],
    [0.5, "Cas", None, None],
    [1.5, "Cas", None, None],
    [27.5, "Cas", None, None],
    [140.5, "Cas", None, None],
    ["__NAN__", "Cas", None, None],
    ["__INF__", "Cas", None, None],
    ["__NEG_INF__", "Cas", None, None],
    [True, "Cas", None, None],
    [False, "Cas", None, None],
    ["27", "Cas", None, None],
    ["", "Cas", None, None],
    [None, "Cas", None, None],
    ["__UNDEFINED__", "Cas", None, None],
    [[27], "Cas", None, None],
    [27, "Nowhere", None, None],
    [27, "cas", None, None],
    [27, "", None, None],
    [27, "   ", None, None],
    [27, "Serpens Caput", None, None],
    [27, None, None, None],
    [27, "__UNDEFINED__", None, None],
    [27, 27, None, None],
    [27, ["Cas"], None, None],
]

COMPACT_PROBES: list[list[Any]] = [
    ["Alpha2 Capricorni", "α² Cap"],
    ["Lambda Orionis", "λ Ori"],
    ["Alpha Canis Majoris", "α CMa"],
    ["", None],
    ["   ", None],
    ["Alpha Ori", None],
    ["alpha Orionis", None],
    ["Lamda Orionis", None],
    ["Alpha Orionis\n", None],
    [None, None],
    ["__UNDEFINED__", None],
    [123, None],
    [True, None],
    [["Alpha Orionis"], None],
]

TITLE_PROBES: list[list[Any]] = [
    ["wire_whole_float_flamsteed_title", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", 27.0], "27 Cassiopeiae", ["HIP 4427"]],
    ["wire_whole_float_119_tau", ["Gaia DR3 3400796031719045632", "Gaia DR3 3400796031719045632", "Tau", "__UNDEFINED__", 119.0], "119 Tauri", ["Gaia DR3 3400796031719045632"]],
    ["wire_whole_float_flamsteed_subtitle", ["HIP 4427", "HIP 4427", "Cas", "Gamma Cassiopeiae", 27.0], "Gamma Cassiopeiae", ["27 Cas", "HIP 4427"]],
    ["named_with_whole_float_flamsteed", ["HIP 4427", "Navi", "Cas", "Gamma Cassiopeiae", 27.0], "Navi", ["Gamma Cassiopeiae", "27 Cas", "HIP 4427"]],
    ["fractional_float_1_5", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", 1.5], "HIP 4427", []],
    ["str_flamsteed_27", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", "27"], "HIP 4427", []],
    ["bool_true_flamsteed", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", True], "HIP 4427", []],
    ["nan_flamsteed", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", "__NAN__"], "HIP 4427", []],
    ["inf_flamsteed", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", "__INF__"], "HIP 4427", []],
    ["null_flamsteed", ["HIP 4427", "HIP 4427", "Cas", "__UNDEFINED__", None], "HIP 4427", []],
    ["empty_common_with_bayer", ["HIP 4427", "", "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "Gamma Cassiopeiae", ["HIP 4427"]],
    ["spaces_common_with_bayer", ["HIP 4427", "   ", "Cas", "Gamma Cassiopeiae", 27], "Gamma Cassiopeiae", ["27 Cas", "HIP 4427"]],
    ["tabs_common_with_flamsteed", ["HIP 4427", " \t\n\r\f\v\u00a0", "Cas", "__UNDEFINED__", 27], "27 Cassiopeiae", ["HIP 4427"]],
    ["nbsp_common_only_id", ["HIP 4427", "\u00a0", "Cas", "__UNDEFINED__", "__UNDEFINED__"], "HIP 4427", []],
    ["empty_bayer_with_flamsteed", ["HIP 4427", "HIP 4427", "Cas", "", 27], "27 Cassiopeiae", ["HIP 4427"]],
    ["spaces_bayer_only_id", ["HIP 4427", "HIP 4427", "Cas", "   ", "__UNDEFINED__"], "HIP 4427", []],
    ["empty_bayer_and_common", ["HIP 4427", "", "Cas", "", "__UNDEFINED__"], "HIP 4427", []],
    ["empty_source_id_with_bayer", ["", "", "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "Gamma Cassiopeiae", []],
    ["spaces_source_id_with_bayer", ["   ", "   ", "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "Gamma Cassiopeiae", []],
    ["all_empty_strings", ["", "", "Cas", "", "__UNDEFINED__"], "", []],
    ["all_spaces_strings", ["   ", "   ", "Cas", "   ", "__UNDEFINED__"], "   ", []],
    ["non_string_source_id_missing", [None, None, "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "Gamma Cassiopeiae", []],
    ["non_string_int_source_id", [123, 123, "Cas", "__UNDEFINED__", "__UNDEFINED__"], "", []],
    ["non_string_all_missing", ["__UNDEFINED__", "__UNDEFINED__", "__UNDEFINED__", "__UNDEFINED__", "__UNDEFINED__"], "", []],
    ["nel_u0085_not_blank_parity", ["HIP 4427", "__NEL_U0085__", "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "\u0085", ["Gamma Cassiopeiae", "HIP 4427"]],
    ["unit_sep_u001f_not_blank_parity", ["HIP 4427", "__US_U001F__", "Cas", "Gamma Cassiopeiae", "__UNDEFINED__"], "\u001f", ["Gamma Cassiopeiae", "HIP 4427"]],
    ["named_equals_bayer", ["HIP 4427", "Gamma Cassiopeiae", "Cas", "Gamma Cassiopeiae", 27], "Gamma Cassiopeiae", ["27 Cas", "HIP 4427"]],
    ["named_equals_flamsteed_long", ["HIP 4427", "27 Cassiopeiae", "Cas", "Gamma Cassiopeiae", 27], "27 Cassiopeiae", ["Gamma Cassiopeiae", "HIP 4427"]],
    ["named_equals_flamsteed_short", ["HIP 4427", "27 Cas", "Cas", "Gamma Cassiopeiae", 27], "27 Cas", ["Gamma Cassiopeiae", "HIP 4427"]],
]


def build_cases() -> dict[str, Any]:
    longs = sorted({s.bayer_designation for s in ALL_SKY_CATALOG_STARS if s.bayer_designation})
    longs += ["Alpha2 Capricorni", "Omega9 Vulpeculae", "Alpha Ori", "alpha Orionis", "Lamda Orionis", "", "α Ori"]
    return {
        "pattern": dz.BAYER_DESIGNATION_PATTERN,
        "compact": [[text, dz.compact_designation(text)] for text in longs],
        "flamsteed": [
            [n, c, dz.flamsteed_designation(n, c)]
            for n, c in ((27, "Cas"), (1, "Andromeda"), (FLAMSTEED_NUMBER_MAX, "Vul"), (0, "Cas"), (FLAMSTEED_NUMBER_MAX + 1, "Cas"), (27, "Nowhere"), (27, "cas"))
        ],
        "titles": _title_cases(),
        "flamsteedProbes": FLAMSTEED_PROBES,
        "compactProbes": COMPACT_PROBES,
        "titleProbes": TITLE_PROBES,
        # AUDIT H's made-up stored solution of the old shape, and how it is read now: the web's schema refuses the one
        # and takes the other (packages/contracts/src/__tests__/designation.test.ts)
        "storedOldShape": _stored_old_shape(),
        # What to_catalog_star writes for every IAU-named star that gets one: the Zod pattern must take each
        "namedStarDesignations": _named_star_designations()[0],
    }


def test_the_shared_cases_are_current() -> None:
    cases = build_cases()
    rendered = json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get(UPDATE_ENV) == "1":
        CASES.write_text(rendered, encoding="utf-8")
    assert CASES.read_text(encoding="utf-8") == rendered, (
        f"regenerate with {UPDATE_ENV}=1 uv run pytest {Path(__file__).relative_to(ROOT)}"
    )


def test_the_designation_contract_exports_each_symbol_by_name() -> None:
    expected = [
        "BAYER_DESIGNATION_PATTERN",
        "FLAMSTEED_NUMBER_MAX",
        "FLAMSTEED_NUMBER_MIN",
        "catalog_title",
        "compact_designation",
        "flamsteed_designation",
        "flamsteed_long_designation",
        "long_designation",
    ]
    assert dz.__all__ == expected
    assert all(hasattr(dz, name) for name in dz.__all__)


def _safe_call(fn: Any, *args: Any) -> tuple[str, Any]:
    try:
        return ("ok", fn(*args))
    except Exception as exc:  # noqa: BLE001
        return ("err", type(exc).__name__)


def test_python_and_typescript_designation_parity_probes() -> None:
    loaded = json.loads(CASES.read_text(encoding="utf-8"))
    for num_tok, con_tok, exp_short, exp_long in loaded.get("flamsteedProbes", FLAMSTEED_PROBES):
        num = None if num_tok == "__UNDEFINED__" else _decode_probe(num_tok)
        con = None if con_tok == "__UNDEFINED__" else _decode_probe(con_tok)
        assert _safe_call(dz.flamsteed_designation, num, con) == ("ok", exp_short), (num_tok, con_tok)
        assert _safe_call(dz.flamsteed_long_designation, num, con) == ("ok", exp_long), (num_tok, con_tok)
    for abbr, name, genitive in DESIGNATION_CONSTELLATIONS:
        assert _safe_call(dz.flamsteed_designation, 1.0, abbr) == ("ok", f"1 {abbr}")
        assert _safe_call(dz.flamsteed_long_designation, 140.0, name) == ("ok", f"140 {genitive}")
    for text_tok, exp_compact in loaded.get("compactProbes", COMPACT_PROBES):
        val = None if text_tok == "__UNDEFINED__" else _decode_probe(text_tok)
        assert _safe_call(dz.compact_designation, val) == ("ok", exp_compact), text_tok
    for tag, raw_parts, exp_title, exp_sub in loaded.get("titleProbes", TITLE_PROBES):
        ref = _build_probe_ref(raw_parts)
        kind, res = _safe_call(dz.catalog_title, ref)
        assert (kind, (res[0], list(res[1])) if kind == "ok" else res) == ("ok", (exp_title, exp_sub)), tag


def test_the_long_form_from_abbreviations() -> None:
    assert dz.long_designation("Gam", None, "Cas") == "Gamma Cassiopeiae"
    assert dz.long_designation("Alp", 2, "Cap") == "Alpha2 Capricorni"
    assert dz.long_designation("Lam", None, "Ori") == "Lambda Orionis"
    assert dz.long_designation("Alp", None, "CMa") == "Alpha Canis Majoris"
    with pytest.raises(KeyError):
        dz.long_designation("Foo", None, "Cas")
    with pytest.raises(KeyError):
        dz.long_designation("Alp", None, "Foo")
    with pytest.raises(ValueError):
        dz.long_designation("Alp", 10, "Cas")
    with pytest.raises(ValueError):
        dz.long_designation("Alp", 0, "Cas")


def test_every_long_form_made_from_the_tables_is_what_the_pattern_admits_and_compacts_back() -> None:
    pattern = re.compile(dz.BAYER_DESIGNATION_PATTERN)
    for abbr, glyph, _ in DESIGNATION_LETTERS:
        for con, _, _ in DESIGNATION_CONSTELLATIONS:
            for index in (None, *[int(d) for d, _ in DESIGNATION_INDICES]):
                text = dz.long_designation(abbr, index, con)
                assert pattern.fullmatch(text), text
                sup = "" if index is None else dict(DESIGNATION_INDICES)[str(index)]
                assert dz.compact_designation(text) == f"{glyph}{sup} {con}"


def test_the_compact_form_and_the_title_order() -> None:
    assert dz.compact_designation("Alpha2 Capricorni") == "α² Cap"
    assert dz.compact_designation("Lambda Orionis") == "λ Ori"
    assert dz.compact_designation("Alpha Canis Majoris") == "α CMa"
    for text in ("Alpha Ori", "alpha Orionis", "Lamda Orionis", "", "α Ori", "Alpha Orionis\n"):
        assert dz.compact_designation(text) is None, text
    titles = {json.dumps(c["reference"], sort_keys=True): (c["title"], c["subtitleParts"]) for c in _title_cases()}
    assert list(titles.values()) == [
        # The title is the IAU name, else the Bayer designation, else the Flamsteed designation with the genitive in
        # full, else the catalog id (catalog-2c, the owner's yes); the others go in the subtitle, the Flamsteed one
        # never again in its short form when it is the title
        ("Navi", ["Gamma Cassiopeiae", "27 Cas", "HIP 4427"]),
        ("Gamma Cassiopeiae", ["27 Cas", "HIP 4427"]),
        ("27 Cassiopeiae", ["HIP 4427"]),
        ("27 Cassiopeiae", ["HIP 4427"]),
        ("HIP 4427", []),
        ("HIP 4427", []),
        ("HIP 4427", []),
        ("Navi", ["HIP 4427"]),
        ("Meissa", ["Lambda Orionis", "Gaia DR3 3337991583943473280"]),
    ]
    assert dz.flamsteed_designation(True, "Cas") is None


# =============================================================================================
# Every designation already shipped is reproduced exactly
# =============================================================================================


def test_every_pinned_stars_designation_is_reproduced_exactly_from_its_hyg_abbreviations() -> None:
    des = {r[0]: r for r in _designations()["rows"]}
    links = json.loads((STARS / "designation-links" / "links.json").read_text(encoding="utf-8"))
    hip_of_row = {(r[0], r[1]): r[5] for r in links["rows"]}
    names = json.loads((STARS / "gaia-dr3" / "names.json").read_text(encoding="utf-8"))["names"]
    hip_of_name = {n["catalogId"]: n["hip"] for n in names if n.get("hip") is not None}
    checked, differ = 0, []
    for star in ALL_SKY_CATALOG_STARS:
        if not star.bayer_designation:
            continue
        ident = star.catalog_source_id.split()[-1]
        hip = int(ident) if star.provider == "Hipparcos" else hip_of_row.get(("gaia-dr3", ident), hip_of_name.get(ident))
        row = des[hip]
        letter, _, index = row[2].partition("-")
        made = dz.long_designation(letter, int(index) if index else None, row[3])
        checked += 1
        if made != star.bayer_designation:
            differ.append((star.id, star.bayer_designation, made))
    # Every one of them: none of the shipped texts differs from what the tables give (the report lists any that did)
    assert checked == sum(1 for s in ALL_SKY_CATALOG_STARS if s.bayer_designation) == 71
    assert differ == []


def test_every_designation_in_a_shipped_fixture_is_one_the_pattern_admits() -> None:
    pattern = re.compile(dz.BAYER_DESIGNATION_PATTERN)
    found = []
    for path in sorted((ROOT / "tests" / "fixtures").glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if path == CASES:
            # The one record kept in the old shape on purpose, to show it is refused and read: its three values only
            cases = json.loads(text)
            old = re.findall(r'"bayerDesignation": "([^"]*)"', json.dumps(cases["storedOldShape"].pop("before")))
            assert old == ["HR 1713", "bet Ori", "bet Ori"]
            text = json.dumps(cases, indent=2, ensure_ascii=False)
        found += [value for value in re.findall(r'"bayerDesignation": "([^"]*)"', text) if value != "bayer_designation"]
    assert found and all(pattern.fullmatch(text) for text in found), [t for t in found if not pattern.fullmatch(t)]
    # The one other kind of text under that key: a table of the contract's field names (wire name to Python name)
    names = json.loads((ROOT / "tests" / "fixtures" / "observation_solution_contract.json").read_text(encoding="utf-8"))
    assert names["pythonNames"]["CatalogReference"]["bayerDesignation"] == "bayer_designation"


# =============================================================================================
# Which tile row a designation goes on (services/science/predicted_sky.py)
# =============================================================================================

DES_COLUMNS = ["hip", "flamsteed", "bayer", "constellation", "hd", "hr", "hygId", "vMag"]
LINK_COLUMNS = ["catalog", "catalogId", "layer", "pix", "index", "hip", "link"]


def _mapping(des_rows: list[list[Any]], link_rows: list[list[Any]]) -> dict[tuple[bool, int], Any]:
    from services.science.designations import tile_row_designations

    return tile_row_designations({"columns": DES_COLUMNS, "rows": des_rows}, {"columns": LINK_COLUMNS, "rows": link_rows})


def _link(hip: int, gaia: int | None = None) -> list[Any]:
    if gaia is None:
        return ["hipparcos-2007", str(hip), 0, 0, 0, hip, "hipparcos-row"]
    return ["gaia-dr3", str(gaia), 0, 0, 0, hip, "gaia-best-neighbour"]


def test_a_shared_designation_goes_only_on_the_brightest_of_its_rows() -> None:
    a = [10, None, "Iot", "Pic", None, None, 1, 5.58]
    b = [11, None, "Iot", "Pic", None, None, 2, 6.42]
    # Both linked: the brighter takes it, the fainter is left with its catalog id
    both = _mapping([a, b], [_link(10, 100), _link(11, 101)])
    assert set(both) == {(False, 100)} and both[(False, 100)].bayer == "Iota Pictoris"
    # Only the fainter linked (iota Pic in the real files): nobody takes it, since it is not the star
    assert _mapping([a, b], [_link(11, 101)]) == {}
    # Only the brighter linked: it takes it
    assert set(_mapping([a, b], [_link(10, 100)])) == {(False, 100)}
    # A tie for the brightest gives it to neither
    assert _mapping([a, [*b[:7], 5.58]], [_link(10, 100), _link(11, 101)]) == {}


def test_separate_designations_each_go_on_their_own_row_with_their_forms() -> None:
    rows = [[20, 27, "Gam", "Cas", None, None, 3, 2.39], [21, 58, None, "Ori", None, None, 4, 0.45], [22, None, "Alp-2", "Cap", None, None, 5, 3.57]]
    out = _mapping(rows, [_link(20), _link(21, 210), _link(22, 220)])
    assert out[(True, 20)] == ("Gamma Cassiopeiae", 27, "Cas")
    assert out[(False, 210)] == (None, 58, "Ori")
    assert out[(False, 220)] == ("Alpha2 Capricorni", None, "Cap")


def test_in_the_real_files_every_shared_designation_is_on_at_most_its_brightest_row() -> None:
    from services.science.designations import baked_tile_row_designations as _tile_row_designations

    des = _designations()
    cols = des["columns"]
    groups: dict[tuple[Any, ...], list[tuple[float, int]]] = {}
    for r in des["rows"]:
        groups.setdefault((r[cols.index("flamsteed")], r[cols.index("bayer")], r[cols.index("constellation")]), []).append((r[cols.index("vMag")], r[0]))
    shared = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
    assert len(shared) == 20
    links = json.loads((STARS / "designation-links" / "links.json").read_text(encoding="utf-8"))
    row_of_hip = {r[5]: (r[0] == "hipparcos-2007", int(r[1])) for r in links["rows"]}
    mapping = _tile_row_designations()
    for key, members in shared.items():
        given = [hip for _, hip in members if row_of_hip.get(hip) in mapping]
        brightest = members[0][1] if members[0][0] < members[1][0] else None
        assert given == ([brightest] if brightest is not None and brightest in row_of_hip else []), (key, members, given)
    # The verdict's case: iota Pic's fainter HIP 22534 has a tile row, its brighter HIP 22531 has none; neither is given it
    assert 22534 in row_of_hip and 22531 not in row_of_hip and row_of_hip[22534] not in mapping


# =============================================================================================
# The solver's catalog star and the predicted sky's take a designation from one source (lead's third answer)
# =============================================================================================


def _named_star_designations() -> tuple[list[str], int, int]:
    """What to_catalog_star writes into the Bayer designation for every IAU-named row of the solver's index, and how
    many named rows get one and how many none (a row without a distance gives no catalog star)."""
    from services.science.phone_photo_solver import _get_tile_index

    ix = _get_tile_index()
    d = ix.data
    written, given, none = [], 0, 0
    for i in range(len(d["sid"])):
        if (bool(d["hip"][i]), int(d["sid"][i])) not in ix._names_by_source:
            continue
        star = ix.to_catalog_star(i, float(d["ra0"][i]), float(d["dec0"][i]), ix.epoch_jyear)
        if star is None:
            continue
        if star.bayer_designation is None:
            none += 1
        else:
            given += 1
            written.append(star.bayer_designation)
    return sorted(set(written)), given, none


def test_for_every_predicted_row_the_solver_gives_the_same_designation() -> None:
    from services.science.phone_photo_solver import _get_tile_index
    from services.science.predicted_sky import _sky_rows

    ix = _get_tile_index()
    sky = _sky_rows()
    compared = designated = 0
    for k, row in enumerate(sky.rows.tolist()):
        solver = ix.to_catalog_star(row, float(ix.data["ra0"][row]), float(ix.data["dec0"][row]), ix.epoch_jyear)
        if solver is None:
            continue
        predicted = sky.stars[k]
        assert (solver.bayer_designation, solver.flamsteed_number) == (predicted.bayer_designation, predicted.flamsteed_number), predicted.id
        compared += 1
        designated += predicted.bayer_designation is not None or predicted.flamsteed_number is not None
    assert (compared, designated) == (952, 660)


def test_every_named_star_the_solver_writes_has_no_designation_or_one_the_pattern_admits() -> None:
    written, given, none = _named_star_designations()
    pattern = re.compile(dz.BAYER_DESIGNATION_PATTERN)
    assert all(pattern.fullmatch(text) for text in written), [t for t in written if not pattern.fullmatch(t)]
    # The counts the report gives
    assert (given, none) == (NAMED_GIVEN, NAMED_NONE)
    # No names file number reaches the field (the old line copied it in for every named star)
    assert not any(re.match(r"(HR|HD|HIP|GJ|BD)[ +-]", text) for text in written)


def test_a_reference_of_the_old_shape_is_refused_in_plain_words() -> None:
    from pydantic import ValidationError

    from services.contracts.catalog_reference import CatalogReference

    old = {
        "id": "gaia-dr3-5044368071870093312", "provider": "Gaia DR3", "releaseVersion": "Gaia DR3",
        "catalogSourceId": "Gaia DR3 5044368071870093312", "commonName": "Acamar", "bayerDesignation": "HR 897",
        "constellation": "Eri", "objectType": "star", "coordinateEpoch": "J2016.0", "raDeg": 44.565, "decDeg": -40.305,
        "apparentMagnitude": 3.228, "citation": "Gaia DR3",
    }
    with pytest.raises(ValidationError) as caught:
        CatalogReference.model_validate(old)
    (error,) = caught.value.errors()
    assert error["loc"] == ("bayerDesignation",) and error["type"] == "string_pattern_mismatch"
    assert CatalogReference.model_validate({**old, "bayerDesignation": "Theta1 Eridani"}).bayer_designation == "Theta1 Eridani"


def _named_rows():
    """(row index, names file entry, pinned record or None, solver star) for every IAU-named row with a distance."""
    from services.science.phone_photo_solver import _get_tile_index

    ix = _get_tile_index()
    d = ix.data
    for i in range(len(d["sid"])):
        key = (bool(d["hip"][i]), int(d["sid"][i]))
        entry = ix._names_by_source.get(key)
        if entry is None:
            continue
        star = ix.to_catalog_star(i, float(d["ra0"][i]), float(d["dec0"][i]), ix.epoch_jyear)
        if star is not None:
            yield i, entry, ix._pinned_by_source.get(star.catalog_source_id), star


def test_the_names_files_designation_stays_in_the_matched_subtitle_character_for_character() -> None:
    from services.science.phone_photo_solver import catalog_matched_subtitle

    kept = 0
    for _, entry, pinned, star in _named_rows():
        # Yesterday's part: the pinned record's designation, else the names file's own, unless it repeated the name
        before = pinned.bayer_designation if pinned is not None else (entry.get("designation") or None)
        if before is None or before == star.common_name:
            continue
        assert before in catalog_matched_subtitle(star).split(" · "), (star.id, before)
        kept += 1
    assert kept == KEPT_PARTS


def test_both_subtitles_take_one_designation_part_for_the_same_row() -> None:
    from dataclasses import replace

    from services.science.designations import designation_parts
    from services.science.phone_photo_solver import _get_tile_index
    from services.science.predicted_sky import _sky_rows

    ix = _get_tile_index()
    sky = _sky_rows()
    compared = 0
    for k, row in enumerate(sky.rows.tolist()):
        solver = ix.to_catalog_star(row, float(ix.data["ra0"][row]), float(ix.data["dec0"][row]), ix.epoch_jyear)
        if solver is None:
            continue
        # The same function on the same row's designations gives the same text; the two paths differ only in the
        # name the repeat rule compares with (a pinned star's own name, "Gamma Cassiopeiae", for a matched star)
        predicted = sky.stars[k]
        assert (solver.bayer_designation, solver.flamsteed_number, solver.names_file_designation) == (
            predicted.bayer_designation, predicted.flamsteed_number, predicted.names_file_designation,
        ), predicted.id
        assert designation_parts(solver) == designation_parts(replace(predicted, common_name=solver.common_name)), predicted.id
        compared += 1
    assert compared == 952


def test_a_designation_or_a_number_that_only_repeats_the_name_is_left_out() -> None:
    from dataclasses import replace

    from services.science.designations import designation_parts
    from services.science.phone_photo_solver import catalog_matched_subtitle

    (_, _, _, star) = next(r for r in _named_rows() if r[3].bayer_designation and r[3].names_file_designation)
    assert designation_parts(star)[0] == star.bayer_designation
    same_bayer = replace(star, bayer_designation=star.common_name)
    assert star.common_name not in designation_parts(same_bayer)
    same_number = replace(star, names_file_designation=star.common_name, bayer_designation=None)
    assert star.common_name not in designation_parts(same_number)
    assert star.common_name not in catalog_matched_subtitle(same_number).split(" · ")
    twice = replace(star, names_file_designation=star.bayer_designation)
    assert designation_parts(twice).count(star.bayer_designation) == 1
    # Unnamed designated star (catalog-2c): title is its designation ("Gamma Cassiopeiae") while common_name is its
    # catalog id ("HIP 4427"); repeating common_name in names_file_designation or bayer_designation is still omitted
    from services.science.predicted_sky import _sky_rows, _star_subtitle

    gamma_cas = next(s for s in _sky_rows().stars if s.catalog_source_id == "HIP 4427")
    assert gamma_cas.common_name == "HIP 4427" and dz.catalog_title(gamma_cas.to_catalog_reference_dict())[0] == "Gamma Cassiopeiae"
    unnamed_same_id = replace(gamma_cas, names_file_designation=gamma_cas.common_name)
    assert designation_parts(unnamed_same_id) == ["27 Cas"]
    assert "HIP 4427" not in catalog_matched_subtitle(unnamed_same_id).split(" · ")
    assert _star_subtitle(unnamed_same_id, gamma_cas.common_name, 45.0, 180.0).split(" · ").count("HIP 4427") == 1


def test_the_flamsteed_number_goes_only_with_its_own_constellation_by_abbreviation_or_name() -> None:
    from services.science.designations import baked_tile_row_designations, designation_of_row

    key = next(k for k, v in baked_tile_row_designations().items() if v.bayer == "Gamma Cassiopeiae")
    assert designation_of_row(*key, "Cas") == ("Gamma Cassiopeiae", 27)
    assert designation_of_row(*key, "Cassiopeia") == ("Gamma Cassiopeiae", 27)
    assert designation_of_row(*key, "And") == ("Gamma Cassiopeiae", None)
    assert designation_of_row(*key, "Andromeda") == ("Gamma Cassiopeiae", None)
    # A row the rule gives nothing: the pinned text if there is one, else nothing
    assert designation_of_row(True, 0, "Cas") == (None, None)
    assert designation_of_row(True, 0, "Cas", "Alpha Orionis") == ("Alpha Orionis", None)


# =============================================================================================
# One star, one title: the common name, else the catalog id, on the predicted and the matched path alike (catalog-2b v2)
# =============================================================================================


def test_a_star_has_one_title_on_both_paths_its_name_else_its_designation_else_its_catalog_id() -> None:
    from services.science.phone_photo_solver import _get_tile_index
    from services.science.predicted_sky import _sky_rows

    ix = _get_tile_index()
    sky = _sky_rows()
    compared = 0
    kinds: Counter[str] = Counter()
    for k, row in enumerate(sky.rows.tolist()):
        matched = ix.to_catalog_star(row, float(ix.data["ra0"][row]), float(ix.data["dec0"][row]), ix.epoch_jyear)
        if matched is None:
            continue
        predicted = sky.stars[k]
        titles = (dz.catalog_title(predicted.to_catalog_reference_dict())[0], dz.catalog_title(matched.to_catalog_reference_dict())[0])
        named = matched.common_name != matched.catalog_source_id
        flamsteed = None if matched.flamsteed_number is None else f"{matched.flamsteed_number} {_GENITIVE[_abbr(matched.constellation)]}"
        rule = matched.common_name if named else (matched.bayer_designation or flamsteed or matched.catalog_source_id)
        assert titles == (rule, rule), (predicted.id, titles)
        kinds[("name" if named else "bayer" if matched.bayer_designation else "flamsteed" if flamsteed else "id")] += 1
        compared += 1
    assert compared == 952
    # The counts the report proposed to the owner: 348 by a Bayer designation and 50 by a Flamsteed number
    assert (kinds["bayer"], kinds["flamsteed"]) == (348, 50), kinds


def test_both_paths_take_a_stars_title_from_the_one_function() -> None:
    from services.science.designations import star_title

    solver = (ROOT / "services" / "science" / "phone_photo_solver.py").read_text(encoding="utf-8")
    predicted = (ROOT / "services" / "science" / "predicted_sky.py").read_text(encoding="utf-8")
    assert solver.count("title=star_title(st),") == 1 and "title=st.common_name" not in solver
    assert predicted.count("title=star_title(star),") == 1
    (_, _, _, star) = next(r for r in _named_rows() if r[3].bayer_designation)
    assert star_title(star) == star.common_name == dz.catalog_title(star.to_catalog_reference_dict())[0]


# =============================================================================================
# An earlier solve stays readable: a stored solution of the old shape is read as what it was (catalog-2b v2)
# =============================================================================================

# Named rows whose reference the solver wrote before catalog-2b with a names file designation in bayerDesignation (an
# HR, HD, HIP, GJ or BD id or a host's name), which the contract now refuses
OLD_REFUSED = 293


def _old_reference(new: dict[str, Any], old: str | None) -> dict[str, Any]:
    """The reference as the solver wrote it before catalog-2b: no flamsteedNumber, and `old` in bayerDesignation."""
    out: dict[str, Any] = {}
    for key, value in new.items():
        if key == "flamsteedNumber":
            continue
        if key == "bayerDesignation":
            if old is not None:
                out[key] = old
            continue
        out[key] = value
        if key == "commonName" and old is not None and "bayerDesignation" not in new:
            out["bayerDesignation"] = old
    return out


def test_a_stored_reference_of_the_old_shape_reads_as_a_solve_of_its_row_writes_it_now() -> None:
    import copy

    from services.contracts.catalog_reference import CatalogReference
    from services.science.designations import upgrade_stored_solution

    pattern = re.compile(dz.BAYER_DESIGNATION_PATTERN)
    refused = 0
    for _, entry, pinned, star in _named_rows():
        old = pinned.bayer_designation if pinned is not None else (entry.get("designation") or None)
        new = star.to_catalog_reference_dict()
        stored = {"id": "sol-old", "findings": [{"id": f"finding-{star.id}", "catalogRef": _old_reference(new, old)}]}
        kept = copy.deepcopy(stored)
        read = upgrade_stored_solution(stored)
        assert stored == kept, star.id
        if old is None or pattern.fullmatch(old):
            assert read == stored, star.id
            continue
        refused += 1
        ref = read["findings"][0]["catalogRef"]
        assert list(ref.items()) == list(new.items()), star.id
        CatalogReference.model_validate(ref)
        assert upgrade_stored_solution(read) == read
    assert refused == OLD_REFUSED


def _readers_old_record() -> dict[str, Any]:
    """AUDIT H's made-up stored solution: the shared fixture's calibrated plate with each star's bayerDesignation as the
    solver wrote it before catalog-2b ("HR 1713", a names file number) or in the short form ("bet Ori")."""
    fixture = json.loads((ROOT / "tests" / "fixtures" / "python_contract_solutions.json").read_text(encoding="utf-8"))
    solution = fixture["plate_calibrated"]
    solution["findings"].sort(key=lambda f: f["id"] != "finding-star-rigel")
    refs = [f["catalogRef"] for f in solution["findings"] if f.get("catalogRef")]
    refs[0]["bayerDesignation"] = "HR 1713"
    for ref in refs[1:]:
        ref["bayerDesignation"] = "bet Ori"
    return solution


def test_the_readers_old_record_keeps_everything_but_the_refused_designations() -> None:
    from services.contracts.catalog_reference import CatalogReference
    from services.science.designations import upgrade_stored_solution

    before = _readers_old_record()
    after = upgrade_stored_solution(before)
    for old, new in zip(before["findings"], after["findings"], strict=True):
        if "catalogRef" not in old:
            assert new == old
            continue
        with pytest.raises(ValueError):
            CatalogReference.model_validate(old["catalogRef"])
        CatalogReference.model_validate(new["catalogRef"])
        assert {k: v for k, v in new.items() if k != "catalogRef"} == {k: v for k, v in old.items() if k != "catalogRef"}
        changed = {k for k in old["catalogRef"].keys() | new["catalogRef"].keys() if old["catalogRef"].get(k) != new["catalogRef"].get(k)}
        assert changed <= {"bayerDesignation", "flamsteedNumber"}
    assert {k: v for k, v in after.items() if k != "findings"} == {k: v for k, v in before.items() if k != "findings"}
    assert [(f["catalogRef"]["bayerDesignation"], f["catalogRef"]["flamsteedNumber"]) for f in after["findings"] if "catalogRef" in f] == [
        ("Beta Orionis", 19), ("Alpha Orionis", 58), ("Alpha Tauri", 87),
    ]
    # Read twice, the same
    assert upgrade_stored_solution(after) == after


def test_the_solution_route_answers_an_old_shape_solution_in_the_new_shape_and_leaves_the_file_as_it_is(tmp_path: Path) -> None:
    import io

    import numpy as np
    from fastapi.testclient import TestClient
    from PIL import Image

    from services.api import app
    from services.contracts.catalog_reference import CatalogReference
    from services.ingest import router as ingest_router
    from services.ingest.jobs import job_id_for_sha256
    from services.ingest.store import IngestStore
    from services.science.designations import upgrade_stored_solution

    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app)
        buffer = io.BytesIO()
        Image.fromarray((np.random.default_rng(7).random((60, 80)) * 40).astype(np.uint8)).save(buffer, format="PNG")
        body = buffer.getvalue()
        headers = {"content-type": "image/png", "x-albedian-filename": "synthetic-old-solve.png"}
        receipt = client.post("/api/ingest", content=body, headers=headers).json()
        sha = receipt["sourceAsset"]["sha256"]
        before = _readers_old_record()
        before.update(sourceSha256=sha, sourceAssetId=receipt["sourceAsset"]["id"])
        job = {"id": job_id_for_sha256(sha), "sourceAssetSha256": sha, "state": "succeeded", "createdAtUTC": "2030-01-01T04:00:00Z",
               "stages": [{"stage": "store", "unit": "bytes", "done": len(body), "total": len(body)}],
               "startedAtUTC": "2030-01-01T04:01:00Z", "finishedAtUTC": "2030-01-01T04:02:00Z", "solutionId": before["id"]}
        folder = store.folder(sha)
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        (folder / "solution.json").write_text(json.dumps(before), encoding="utf-8")
        on_disk = (folder / "solution.json").read_bytes()
        # The reader's path: solving again answers the stored job, and the solution the web then reads is the new shape
        again = client.post(f"/api/ingest/{sha}/solve")
        assert again.status_code == 200 and again.json() == job
        read = client.get(f"/api/ingest/{sha}/solution")
        assert read.status_code == 200 and read.json() == upgrade_stored_solution(before) != before
        for finding in read.json()["findings"]:
            if "catalogRef" in finding:
                CatalogReference.model_validate(finding["catalogRef"])
        assert (folder / "solution.json").read_bytes() == on_disk
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def test_an_old_shape_reference_keeps_a_flamsteed_number_it_holds_and_one_with_no_tile_star_loses_only_the_value() -> None:
    from services.science.designations import upgrade_stored_solution

    record = _readers_old_record()
    refs = [f["catalogRef"] for f in record["findings"] if "catalogRef" in f]
    refs[0]["flamsteedNumber"] = 3
    refs[1]["catalogSourceId"] = "NGC 1976"
    read = [f["catalogRef"] for f in upgrade_stored_solution(record)["findings"] if "catalogRef" in f]
    assert (read[0]["bayerDesignation"], read[0]["flamsteedNumber"]) == ("Beta Orionis", 3)
    assert "bayerDesignation" not in read[1] and "flamsteedNumber" not in read[1]
    assert {k: v for k, v in read[1].items()} == {k: v for k, v in refs[1].items() if k != "bayerDesignation"}
    # A Gaia id is read as a Gaia row, not as a HIP number
    gaia = {"findings": [{"catalogRef": {**refs[2], "catalogSourceId": "Gaia DR3 21421", "bayerDesignation": "HR 1"}}]}
    assert "bayerDesignation" not in upgrade_stored_solution(gaia)["findings"][0]["catalogRef"]


def test_the_upgrade_reads_each_stored_text_whole() -> None:
    # Four edges of the reading, each a stored reference of the reader's record (Rigel, HIP 24436, in Orion)
    from services.science.designations import upgrade_stored_solution

    def read(**ref: Any) -> dict[str, Any]:
        rigel = next(f["catalogRef"] for f in _readers_old_record()["findings"] if "catalogRef" in f)
        rigel = {**rigel, **ref}
        rigel = {k: v for k, v in rigel.items() if v is not None}
        return upgrade_stored_solution({"findings": [{"catalogRef": rigel}]})["findings"][0]["catalogRef"]

    # A wire form with a trailing newline is refused by both contracts, so it is read as an old value, not kept
    assert read(bayerDesignation="Beta Orionis\n")["bayerDesignation"] == "Beta Orionis"
    # Only a whole tile id names a tile star: "HIP 24436 B" is not HIP 24436
    assert "bayerDesignation" not in read(bayerDesignation="HR 1713", catalogSourceId="HIP 24436 B")
    assert "bayerDesignation" not in read(bayerDesignation="HR 1713", catalogSourceId="xHIP 24436")
    # A Flamsteed number the record holds is kept wherever it stands among the keys
    first = {"flamsteedNumber": 3, **next(f["catalogRef"] for f in _readers_old_record()["findings"] if "catalogRef" in f)}
    kept = upgrade_stored_solution({"findings": [{"catalogRef": first}]})["findings"][0]["catalogRef"]
    assert (kept["bayerDesignation"], kept["flamsteedNumber"]) == ("Beta Orionis", 3)
    # Without a constellation nothing is looked up or guessed, and nothing is raised
    assert {"bayerDesignation", "flamsteedNumber"}.isdisjoint(read(bayerDesignation="HR 1713", constellation=None))
    assert {"bayerDesignation", "flamsteedNumber"}.isdisjoint(read(bayerDesignation="HR 1713", constellation=["Ori"]))



_GENITIVE = {abbr: genitive for abbr, _, genitive in DESIGNATION_CONSTELLATIONS}
_ABBR = {name: abbr for abbr, name, _ in DESIGNATION_CONSTELLATIONS}


def _abbr(constellation: str) -> str:
    return constellation if constellation in _GENITIVE else _ABBR[constellation]


# The report's ten real examples (catalogue id, the title shown): the owner sees these on a screen
TEN = [
    ("HIP 4427", "Gamma Cassiopeiae"), ("HIP 61932", "Gamma Centauri"), ("HIP 66657", "Epsilon Centauri"),
    ("HIP 71860", "Alpha Lupi"), ("HIP 39953", "Gamma2 Velorum"), ("Gaia DR3 2921635736292220800", "Omicron2 Canis Majoris"),
    ("HIP 87073", "Iota1 Scorpii"), ("Gaia DR3 5964991494281655808", "Zeta2 Scorpii"),
    ("Gaia DR3 3400796031719045632", "119 Tauri"), ("Gaia DR3 6750856228950043264", "62 Sagittarii"),
]


def test_the_ten_examples_are_titled_as_the_report_shows_and_no_subtitle_repeats_its_title() -> None:
    from services.science.designations import designation_parts, star_title
    from services.science.phone_photo_solver import _get_tile_index, catalog_matched_subtitle
    from services.science.predicted_sky import _sky_rows, _star_subtitle

    sky = _sky_rows()
    by_id = {s.catalog_source_id: s for s in sky.stars}
    assert [star_title(by_id[source]) for source, _ in TEN] == [title for _, title in TEN]
    ix = _get_tile_index()
    for k, row in enumerate(sky.rows.tolist()):
        star = sky.stars[k]
        title = star_title(star)
        parts = _star_subtitle(star, sky.designations[k], 45.0, 180.0).split(" · ")
        assert title not in parts and title not in designation_parts(star), star.id
        if star.flamsteed_number is not None and title.split(" ")[0] == str(star.flamsteed_number):
            assert f"{star.flamsteed_number} {_abbr(star.constellation)}" not in parts, star.id
        matched = ix.to_catalog_star(row, float(ix.data["ra0"][row]), float(ix.data["dec0"][row]), ix.epoch_jyear)
        if matched is not None:
            assert star_title(matched) not in catalog_matched_subtitle(matched).split(" · "), star.id


def test_the_long_flamsteed_form_takes_the_genitive_in_full() -> None:
    assert dz.flamsteed_long_designation(119, "Tau") == "119 Tauri"
    assert dz.flamsteed_long_designation(62, "Sagittarius") == "62 Sagittarii"
    assert dz.flamsteed_long_designation(27, "Cas") == "27 Cassiopeiae"
    assert dz.flamsteed_long_designation(27, "Nowhere") is None
    assert dz.flamsteed_long_designation(FLAMSTEED_NUMBER_MAX + 1, "Cas") is None
    assert dz.flamsteed_long_designation(0, "Cas") is None
    assert dz.flamsteed_long_designation(True, "Cas") is None
