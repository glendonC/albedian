"""Constellation lines and boundaries contract (data-7b0): Pydantic rules, a property, and the fixture shared with Zod.

`tests/fixtures/constellations_contract.json` is written from `build_constellation_samples()` and parsed by
packages/contracts/src/__tests__/constellations.test.ts, so both sides accept and refuse the same documents.
Regenerate it after a deliberate contract change with:

    ALBEDIAN_UPDATE_CONTRACT_FIXTURE=1 uv run pytest tests/integration/test_constellations_contract.py

The valid samples hold seven real entries of each file of d3-celestial at commit d2e20e104b86 (data/constellations.
lines.json and constellations.bounds.json at github.com/ofrohn/d3-celestial, BSD-3-Clause, Copyright (c) 2015, Olaf
Frohn), turned into the contract's form by .lanetools/d3c_rows.py (right ascension wrapped into 0 to 360 degrees,
the rank as an integer, Serpens named Se1 or Se2 by right ascension) and kept here as literals: Crux, Centaurus and
Carina, the two parts of Serpens, and Octans and Ursa Minor, whose boundaries ring the south and north celestial
poles. Every invalid sample breaks one rule.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from services.contracts import ConstellationBoundaries, ConstellationLines
from services.contracts import constellations as cn

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "constellations_contract.json"
UPDATE_FIXTURE_ENV = "ALBEDIAN_UPDATE_CONTRACT_FIXTURE"
RELEASE = "d3-celestial d2e20e104b86429d90ac8227a5b021262b45d75a"
ORDER = ("Cru", "Oct", "Cen", "Car", "Se1", "Se2", "UMi")

# fmt: off
REAL_LINES = {
    "Car": {"code": "Car", "rank": 1, "polylines": [[[99.4403, -43.1959], [95.988, -52.6957], [138.2999, -69.7172], [153.4342, -70.0379], [160.7392, -64.3945], [158.0061, -61.6853], [154.2707, -61.3323], [139.2725, -59.2752], [125.6285, -59.5095], [119.1946, -52.9824], [122.3831, -47.3366], [131.1759, -54.7088], [139.2725, -59.2752]], [[160.7392, -64.3945], [166.6351, -62.4241], [167.1417, -61.9472], [168.1501, -60.3176], [167.1475, -58.975], [163.3736, -58.8532], [158.0061, -61.6853]]]},
    "Cen": {"code": "Cen", "rank": 1, "polylines": [[[170.2517, -54.491], [182.0896, -50.7224], [187.0099, -50.2306], [190.3793, -48.9599], [204.9719, -53.4664], [208.8849, -47.2884], [207.4041, -42.4737], [207.3762, -41.6877], [211.6706, -36.37], [218.8768, -42.1578], [224.7904, -42.1042]], [[207.3762, -41.6877], [200.1492, -36.7123]], [[219.8962, -60.8372], [204.9719, -53.4664], [210.9559, -60.373]], [[187.0099, -50.2306], [182.913, -52.3685], [172.942, -59.4421]]]},
    "Cru": {"code": "Cru", "rank": 2, "polylines": [[[191.9303, -59.6888], [183.7863, -58.7489]], [[186.6496, -63.0991], [187.7915, -57.1132]]]},
    "Oct": {"code": "Oct", "rank": 3, "polylines": [[[216.7301, -83.6679], [341.5146, -81.3816], [325.3694, -77.39], [216.7301, -83.6679]]]},
    "Se1": {"code": "Se1", "rank": 3, "polylines": [[[236.5469, 15.4218], [235.3877, 19.6704], [237.1849, 18.1416], [239.1133, 15.6616], [236.5469, 15.4218], [233.7006, 10.5389], [236.067, 6.4256], [237.704, 4.4777], [243.5864, -3.6943]]]},
    "Se2": {"code": "Se2", "rank": 3, "polylines": [[[257.5945, -15.7249], [264.3967, -15.3986], [269.7566, -9.7736], [270.7705, -8.1803], [275.3275, -2.8988], [284.0549, 4.2036]]]},
    "UMi": {"code": "UMi", "rank": 2, "polylines": [[[236.0147, 77.7945], [244.3762, 75.7553], [230.1821, 71.834], [222.6764, 74.1555], [236.0147, 77.7945], [251.4927, 82.0373], [263.0542, 86.5865], [37.9545, 89.2641]]]},
}
REAL_BOUNDS = {
    "Car": {"code": "Car", "rank": 1, "ring": [[170.1559, -57.1843], [166.3373, -57.1744], [133.3237, -56.974], [133.3802, -54.9742], [127.5671, -54.9205], [127.6093, -53.4207], [123.3201, -53.3782], [123.3811, -51.1285], [120.8617, -51.1026], [90.7489, -50.7545], [90.6937, -52.5042], [93.1944, -52.5346], [93.1074, -55.034], [98.1143, -55.0946], [97.9951, -58.0938], [103.0111, -58.1537], [102.7033, -64.1519], [136.0947, -64.499], [135.2437, -75.4955], [169.857, -75.684], [170.0848, -64.6843], [170.1559, -57.1843]]},
    "Cen": {"code": "Cen", "rank": 1, "ring": [[166.4794, -35.6747], [166.4565, -40.4246], [166.3373, -57.1744], [170.1559, -57.1843], [170.0848, -64.6843], [179.0574, -64.6958], [179.0708, -55.6958], [194.3345, -55.6771], [194.4384, -64.677], [204.6803, -64.6379], [220.515, -64.539], [220.2345, -55.5401], [214.6568, -55.58], [214.4503, -42.5806], [225.7963, -42.4942], [225.6308, -29.9949], [190.4174, -30.1864], [190.4272, -33.6864], [185.3874, -33.6939], [185.3903, -35.6939], [166.4794, -35.6747]]},
    "Cru": {"code": "Cru", "rank": 2, "ring": [[179.0708, -55.6958], [179.0574, -64.6958], [194.4384, -64.677], [194.3345, -55.6771], [179.0708, -55.6958]]},
    "Oct": {"code": "Oct", "rank": 3, "ring": [[1.5334, -81.804], [50.0917, -82.0645], [48.2329, -84.5554], [109.0197, -85.2614], [111.6521, -82.7759], [209.1111, -83.1201], [276.866, -82.4583], [274.1951, -74.9745], [323.1848, -74.4545], [351.9978, -74.3125], [1.5663, -74.304], [1.5334, -81.804]]},
    "Se1": {"code": "Se1", "rank": 3, "ring": [[227.853, -0.4743], [227.7815, 7.5254], [227.6055, 25.5246], [229.0995, 25.5381], [241.8057, 25.6641], [241.8566, 21.6644], [240.111, 21.646], [240.1811, 15.6463], [242.6766, 15.6728], [242.8097, 3.6735], [245.5586, 3.7034], [245.6026, -0.2964], [245.6384, -3.5462], [240.387, -3.6026], [227.882, -3.7242], [227.853, -0.4743]]},
    "Se2": {"code": "Se2", "rank": 3, "ring": [[275.3506, 0.0552], [275.3142, 3.055], [277.9392, 3.0867], [277.9211, 4.5866], [275.296, 4.5549], [275.2746, 6.3048], [281.4586, 6.3792], [284.526, 6.4156], [284.5764, 2.1659], [280.3262, 2.1153], [280.3502, 0.1155], [280.3982, -3.8842], [275.3991, -3.9445], [275.5495, -15.9436], [265.8002, -16.0619], [259.2974, -16.14], [259.2221, -10.1404], [265.4735, -10.0654], [265.4944, -11.7319], [266.7447, -11.7168], [266.7238, -10.0502], [271.2237, -9.9956], [271.1497, -3.9961], [269.1497, -4.0204], [269.101, -0.0206], [275.3506, 0.0552]]},
    "UMi": {"code": "UMi", "rank": 2, "ring": [[195.8206, 76.3289], [196.0975, 69.3294], [210.6508, 69.3991], [210.8206, 65.3997], [235.3296, 65.6023], [235.0506, 69.6009], [247.8411, 69.7383], [247.2207, 74.7348], [261.5366, 74.9033], [260.2179, 79.8953], [267.656, 79.9857], [261.7222, 85.9496], [308.721, 86.4656], [308.3314, 86.6306], [343.5107, 86.8369], [339.261, 88.6639], [0.9483, 88.6092], [24.7077, 88.3564], [57.6647, 88.0063], [135.8325, 87.5689], [130.4028, 86.0975], [213.023, 85.9308], [216.7829, 79.445], [203.8092, 79.3629], [204.157, 76.3638], [195.8206, 76.3289]]},
}
# fmt: on

MODELS: dict[str, type[BaseModel]] = {"lines": ConstellationLines, "bounds": ConstellationBoundaries}
# Made up: no figure in the file has two consecutive points with the same RA [tested], but the contract allows it
MERIDIAN_SEGMENT = {"code": "Cru", "rank": 2, "polylines": [[[187.0, -57.0], [187.0, -63.0]]]}


def document(kind: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    head: dict[str, Any] = {
        "format": cn.CONSTELLATION_LINES_FORMAT if kind == "lines" else cn.CONSTELLATION_BOUNDARIES_FORMAT,
        "formatVersion": cn.CONSTELLATION_FORMAT_VERSION,
        "evidenceKind": cn.CONSTELLATION_EVIDENCE_KIND,
        "release": RELEASE,
    }
    if kind == "lines":
        head["equinox"] = cn.CONSTELLATION_LINES_EQUINOX
    else:
        head["vertexEquinox"] = cn.CONSTELLATION_BOUNDARY_VERTEX_EQUINOX
        head["edgeEquinox"] = cn.CONSTELLATION_BOUNDARY_EDGE_EQUINOX
    return {**head, "constellations": copy.deepcopy(entries)}


def real(kind: str) -> list[dict[str, Any]]:
    table = REAL_LINES if kind == "lines" else REAL_BOUNDS
    return [table[code] for code in ORDER]


def first(t: dict[str, Any]) -> dict[str, Any]:
    return t["constellations"][0]


def set_line_point(t: dict[str, Any], point: list[Any]) -> None:
    first(t)["polylines"][0][0] = point


def set_ring_point(t: dict[str, Any], point: list[Any]) -> None:
    """Move the ring's first point, and its closing copy with it, so only the point itself breaks a rule."""
    ring = first(t)["ring"]
    ring[0] = point
    ring[-1] = copy.deepcopy(point)


def repeat_line_point(t: dict[str, Any]) -> None:
    line = first(t)["polylines"][0]
    line.insert(1, copy.deepcopy(line[0]))


def repeat_ring_point(t: dict[str, Any]) -> None:
    ring = first(t)["ring"]
    ring.insert(1, copy.deepcopy(ring[0]))


# Each fault breaks a rule of any valid document of at least two entries
COMMON_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "formatVersion": lambda t: t.update(formatVersion=2),
    "evidence kind": lambda t: t.update(evidenceKind="observed"),
    "snake-case top-level key": lambda t: t.update(format_version=t.pop("formatVersion")),
    "extra field": lambda t: t.update(extra=1),
    "blank-padded release": lambda t: t.update(release=" d3-celestial"),
    "no constellations": lambda t: t.update(constellations=[]),
    "repeated code": lambda t: t["constellations"][1].update(code=first(t)["code"]),
    "unknown code": lambda t: first(t).update(code="Ser"),
    "rank 0": lambda t: first(t).update(rank=0),
    "rank 4": lambda t: first(t).update(rank=4),
    "fractional rank": lambda t: first(t).update(rank=1.5),
    "rank as text": lambda t: first(t).update(rank="1"),
    "boolean rank": lambda t: first(t).update(rank=True),
    "extra entry field": lambda t: first(t).update(name="Crux"),
}
LINE_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "lines format": lambda t: t.update(format=cn.CONSTELLATION_BOUNDARIES_FORMAT),
    "lines equinox B1875": lambda t: t.update(equinox="B1875"),
    "no polylines": lambda t: first(t).update(polylines=[]),
    "one-point polyline": lambda t: first(t)["polylines"].__setitem__(0, [first(t)["polylines"][0][0]]),
    "point repeated at once in a polyline": repeat_line_point,
    "line right ascension of 360": lambda t: set_line_point(t, [360.0, 0.0]),
    "line negative right ascension": lambda t: set_line_point(t, [-0.5, 0.0]),
    "line declination past the pole": lambda t: set_line_point(t, [10.0, 90.5]),
    "line declination below the south pole": lambda t: set_line_point(t, [10.0, -90.5]),
    "line point of three numbers": lambda t: set_line_point(t, [10.0, 0.0, 1.0]),
    "line point as text": lambda t: set_line_point(t, ["10.0", 0.0]),
    "a ring where polylines belong": lambda t: first(t).update(ring=first(t).pop("polylines")[0]),
}
BOUND_FAULTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "bounds format": lambda t: t.update(format=cn.CONSTELLATION_LINES_FORMAT),
    "vertices in B1875": lambda t: t.update(vertexEquinox="B1875"),
    "edges in J2000": lambda t: t.update(edgeEquinox="J2000"),
    "open ring": lambda t: first(t)["ring"].pop(),
    "ring end on the start's RA but not its Dec": lambda t: first(t)["ring"].__setitem__(
        -1, [first(t)["ring"][0][0], first(t)["ring"][0][1] + (0.5 if first(t)["ring"][0][1] < 0 else -0.5)]
    ),
    "ring of three points": lambda t: first(t).update(ring=[*first(t)["ring"][:2], copy.deepcopy(first(t)["ring"][0])]),
    "point repeated at once in a ring": repeat_ring_point,
    "ring right ascension of 360": lambda t: set_ring_point(t, [360.0, 0.0]),
    "ring negative right ascension": lambda t: set_ring_point(t, [-0.5, 0.0]),
    "ring declination past the pole": lambda t: set_ring_point(t, [10.0, -90.5]),
    "ring declination above the north pole": lambda t: set_ring_point(t, [10.0, 90.5]),
    "ring point as text": lambda t: set_ring_point(t, ["10.0", 0.0]),
    "polylines where a ring belongs": lambda t: first(t).update(polylines=[first(t).pop("ring")]),
}
FAULTS = {"lines": {**COMMON_FAULTS, **LINE_FAULTS}, "bounds": {**COMMON_FAULTS, **BOUND_FAULTS}}


def build_constellation_samples() -> dict[str, Any]:
    # Each fault is shown on Crux and Octans, which keeps the fixture small; the property test applies every fault to
    # generated documents too
    invalid = []
    for kind in ("lines", "bounds"):
        for rule, fault in FAULTS[kind].items():
            broken = document(kind, real(kind)[:2])
            fault(broken)
            invalid.append({"rule": rule, "kind": kind, "table": broken})
    return {
        "credit": "d3-celestial, Olaf Frohn, BSD-3-Clause",
        "formats": {"lines": cn.CONSTELLATION_LINES_FORMAT, "bounds": cn.CONSTELLATION_BOUNDARIES_FORMAT},
        "formatVersion": cn.CONSTELLATION_FORMAT_VERSION,
        "evidenceKind": cn.CONSTELLATION_EVIDENCE_KIND,
        "codes": list(cn.CONSTELLATION_CODES),
        "rankRange": [cn.CONSTELLATION_RANK_MIN, cn.CONSTELLATION_RANK_MAX],
        "equinoxes": {
            "lines": cn.CONSTELLATION_LINES_EQUINOX,
            "boundaryVertices": cn.CONSTELLATION_BOUNDARY_VERTEX_EQUINOX,
            "boundaryEdges": cn.CONSTELLATION_BOUNDARY_EDGE_EQUINOX,
        },
        "ringMinPoints": cn.CONSTELLATION_RING_MIN_POINTS,
        "valid": [
            *({"kind": kind, "table": document(kind, real(kind))} for kind in ("lines", "bounds")),
            # Made up, not from the file: a segment along one meridian, two points with the same RA, is valid
            {"kind": "lines", "table": document("lines", [MERIDIAN_SEGMENT])},
        ],
        "invalid": invalid,
    }


def fixture_text(samples: dict[str, Any]) -> str:
    """Two-space JSON with each sample on one line, so the fixture stays small and a diff shows the samples that changed."""

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

    def block(value: Any, indent: int) -> str:
        pad = " " * indent
        return "[\n" + ",\n".join(f"{pad}  {compact(item)}" for item in value) + "\n" + pad + "]"

    lines = [f"  {json.dumps(key)}: {block(value, 2) if key in ('valid', 'invalid') else compact(value)}" for key, value in samples.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def test_constellation_fixture_is_current() -> None:
    samples = build_constellation_samples()
    if os.environ.get(UPDATE_FIXTURE_ENV) == "1":
        FIXTURE.write_text(fixture_text(samples), encoding="utf-8")
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert samples == stored, (
        f"{FIXTURE.name} no longer matches the Python constellation contract; regenerate it with "
        f"{UPDATE_FIXTURE_ENV}=1 uv run pytest tests/integration/test_constellations_contract.py"
    )


@pytest.mark.parametrize("kind", ["lines", "bounds"])
def test_real_entries_parse_and_round_trip_unchanged(kind: str) -> None:
    sample = document(kind, real(kind))
    parsed = MODELS[kind].model_validate(sample)
    assert parsed.to_contract_dict() == sample  # type: ignore[attr-defined]
    assert json.loads(json.dumps(parsed.to_contract_dict())) == sample  # type: ignore[attr-defined]


def test_a_meridian_segment_is_valid() -> None:
    """Two consecutive points may share an RA: only a point repeated at once is refused."""
    parsed = ConstellationLines.model_validate(document("lines", [MERIDIAN_SEGMENT]))
    assert parsed.constellations[0].polylines[0] == ((187.0, -57.0), (187.0, -63.0))


def test_real_entries_keep_the_southern_figures_and_both_serpens_parts() -> None:
    lines = ConstellationLines.model_validate(document("lines", real("lines")))
    bounds = ConstellationBoundaries.model_validate(document("bounds", real("bounds")))
    assert [e.code for e in lines.constellations] == list(ORDER) == [e.code for e in bounds.constellations]
    by_code = {e.code: e for e in lines.constellations}
    # The Southern Cross: two bars of two stars each, rank 2 in d3-celestial
    assert [len(p) for p in by_code["Cru"].polylines] == [2, 2] and by_code["Cru"].rank == 2
    # Serpens Caput lies west of Ophiuchus, Serpens Cauda east of it
    assert max(pt[0] for p in by_code["Se1"].polylines for pt in p) < 250 < min(pt[0] for p in by_code["Se2"].polylines for pt in p)
    rings = {e.code: e.ring for e in bounds.constellations}
    # Octans rings the south celestial pole, Ursa Minor the north: their rings span every right ascension
    assert max(pt[1] for pt in rings["Oct"]) < -74 and min(pt[1] for pt in rings["UMi"]) > 65


@pytest.mark.parametrize(
    "case", build_constellation_samples()["invalid"], ids=lambda case: f"{case['kind']}: {case['rule']}"
)
def test_every_invalid_sample_is_refused(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MODELS[case["kind"]].model_validate(case["table"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("axis", [0, 1])
def test_values_that_json_cannot_carry_are_refused(bad: float, axis: int) -> None:
    lines = document("lines", real("lines"))
    lines["constellations"][0]["polylines"][0][0][axis] = bad
    with pytest.raises(ValidationError):
        ConstellationLines.model_validate(lines)
    bounds = document("bounds", real("bounds"))
    first(bounds)["ring"][0][axis] = bad
    first(bounds)["ring"][-1][axis] = bad
    with pytest.raises(ValidationError):
        ConstellationBoundaries.model_validate(bounds)


# =============================================================================================
# PBT-CON-1: the models accept exactly the documents the rules allow
# =============================================================================================


_POINT = st.tuples(
    st.floats(0, 360, exclude_max=True, allow_nan=False, allow_infinity=False),
    st.floats(-90, 90, allow_nan=False, allow_infinity=False),
)


def _distinct_points(min_size: int, max_size: int) -> st.SearchStrategy[list[list[float]]]:
    return st.lists(_POINT, min_size=min_size, max_size=max_size, unique=True).map(lambda pts: [list(p) for p in pts])


@st.composite
def entry_strategy(draw: st.DrawFn, kind: str, code: str) -> dict[str, Any]:
    rank = draw(st.integers(cn.CONSTELLATION_RANK_MIN, cn.CONSTELLATION_RANK_MAX))
    if kind == "lines":
        return {"code": code, "rank": rank, "polylines": draw(st.lists(_distinct_points(2, 5), min_size=1, max_size=3))}
    corners = draw(_distinct_points(cn.CONSTELLATION_RING_MIN_POINTS - 1, 6))
    return {"code": code, "rank": rank, "ring": [*corners, list(corners[0])]}


@st.composite
def valid_document(draw: st.DrawFn, kind: str) -> dict[str, Any]:
    codes = draw(st.lists(st.sampled_from(cn.CONSTELLATION_CODES), min_size=2, max_size=4, unique=True))
    return document(kind, [draw(entry_strategy(kind, code)) for code in codes])


@st.composite
def document_with_fault(draw: st.DrawFn) -> tuple[str, dict[str, Any], str | None]:
    kind = draw(st.sampled_from(["lines", "bounds"]))
    t = draw(valid_document(kind))
    fault = draw(st.one_of(st.none(), st.none(), st.sampled_from(sorted(FAULTS[kind]))))
    if fault is not None:
        FAULTS[kind][fault](t)
    return kind, t, fault


@settings(max_examples=300, deadline=None)
@given(case=document_with_fault())
@example(case=("lines", document("lines", real("lines")), None))
@example(case=("bounds", document("bounds", real("bounds")), None))  # both poles, both Serpens parts
def test_pbt_con_1_models_accept_exactly_the_documents_the_rules_allow(case: tuple[str, dict[str, Any], str | None]) -> None:
    """PBT-CON-1 (Python half): validation succeeds exactly when no rule is broken, and the wire form round-trips."""
    kind, t, fault = case
    model = MODELS[kind]
    try:
        parsed = model.model_validate(t)
    except ValidationError:
        assert fault is not None, "a document that keeps every rule was refused"
        return
    assert fault is None, f"a {kind} document with the fault {fault!r} was accepted"
    assert model.model_validate(parsed.to_contract_dict()) == parsed  # type: ignore[attr-defined]
    assert parsed.to_contract_dict() == t  # type: ignore[attr-defined]
