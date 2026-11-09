"""Constellation lines and boundaries bake (data-7b): parsing, the committed files, their record, and real-data checks.

`scripts/bake_constellations_d3celestial.py` writes `apps/web/public/catalog/constellations/lines.json`, `bounds.json`
and their `PROVENANCE.json` from d3-celestial at commit d2e20e104b86 (github.com/ofrohn/d3-celestial, BSD-3-Clause,
Copyright (c) 2015, Olaf Frohn), cached in `.cache/d3celestial/` and registered in `data/downloads.json`. The
southern-sky audit (data-7) found no constellation lines or boundaries anywhere in the repo.

The real-data checks hold the two files against catalogues made independently of them, all committed:
- every line vertex sits on a Gaia DR3 or Hipparcos star of the G 12 tiles, once each star is moved back from the
  tiles' epoch (J2016.0) to J2000 by its own proper motion;
- under the boundaries, Acrux, Rigil Kentaurus and Canopus fall in Crux, Centaurus and Carina; every star the IAU
  names (names.json, IAU WGSN) falls in exactly the constellation the IAU lists for it; and every positioned OpenNGC
  object (openngc.json) falls in exactly the constellation OpenNGC gives it.

A point is placed by the B1875 boundary edges, as Roman (1987) does: it and the vertices are precessed from J2000 to
B1875 (IAU 1976, services/science/catalog.py), and a ray north along the point's B1875 right ascension counts the
edges it crosses; a ring around the north celestial pole (Ursa Minor) holds the ray's end, so there an even count
means inside. Every comparison is within a stated tolerance; no float output is hashed or byte-compared outside the
rebake, which only adds, rounds and copies values.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from services.contracts.constellations import CONSTELLATION_CODES, ConstellationBoundaries, ConstellationLines
from services.contracts.deep_sky import DEEP_SKY_COLUMNS
from services.contracts.provenance import AssetProvenanceRecord
from services.science.catalog import precession_matrix_iau1976
from services.science.catalog_tiles import decode_core_directions, decode_core_tile, decode_detail_tile, decode_f16, decode_mag
from tests.integration.test_constellations_contract import REAL_BOUNDS, REAL_LINES

ROOT = Path(__file__).resolve().parents[2]
LINES_REL = ".cache/d3celestial/constellations.lines.json"
BOUNDS_REL = ".cache/d3celestial/constellations.bounds.json"
OUT_DIR = ROOT / "apps" / "web" / "public" / "catalog" / "constellations"
DOC_PATH = ROOT / "docs" / "science" / "CONSTELLATIONS.md"
STARS = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
OPENNGC = ROOT / "apps" / "web" / "public" / "catalog" / "deepsky" / "openngc.json"
JD_B1875 = 2415020.31352 + (1875 - 1900) * 365.242198781  # Besselian epoch 1875.0
MAS_DEG = 1 / 3.6e6
TILE_EPOCH = 2016.0


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_constellations_d3celestial", ROOT / "scripts" / "bake_constellations_d3celestial.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/bake_constellations_d3celestial.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bake = _load_bake()


# =============================================================================================
# Parsing
# =============================================================================================


def test_longitude_wraps_into_right_ascension_at_four_decimals() -> None:
    assert bake.ra_deg(0.0) == 0.0
    assert bake.ra_deg(179.9066) == 179.9066
    assert bake.ra_deg(-168.0697) == pytest.approx(191.9303, abs=1e-12)  # Mimosa, in Crux
    assert repr(bake.ra_deg(-168.0697)) == "191.9303"
    for bad in (180.0, -180.5, 360.0):
        with pytest.raises(ValueError):
            bake.ra_deg(bad)


def test_serpens_parts_are_told_apart_by_right_ascension() -> None:
    assert bake.constellation_code("Cru", [[[191.9303, -59.6888]]]) == "Cru"
    # The first two points of each Serpens figure in the pinned file, after the wrap
    caput, cauda = [[236.5469, 15.4218], [235.3877, 19.6704]], [[257.5945, -15.7249], [264.3967, -15.3986]]
    assert bake.constellation_code("Ser", [caput]) == "Se1"
    assert bake.constellation_code("Ser", [cauda]) == "Se2"
    with pytest.raises(ValueError, match="straddles"):
        bake.constellation_code("Ser", [caput[:1] + cauda[:1]])


def test_parse_refuses_other_shapes() -> None:
    feature = {"type": "Feature", "id": "Cru", "properties": {"rank": "2"}, "geometry": {"type": "MultiLineString", "coordinates": [[[-168.0697, -59.6888], [-176.2137, -58.7489]]]}}
    collection = {"type": "FeatureCollection", "features": [feature]}
    parsed = bake.parse_features(json.dumps(collection), "MultiLineString")
    assert parsed == [{"code": "Cru", "rank": 2, "parts": [[[191.9303, -59.6888], [183.7863, -58.7489]]]}]
    with pytest.raises(ValueError, match="expected Polygon"):
        bake.parse_features(json.dumps(collection), "Polygon")
    with pytest.raises(ValueError, match="properties"):
        bake.parse_features(json.dumps({**collection, "features": [{**feature, "properties": {"rank": "2", "name": "Crux"}}]}), "MultiLineString")
    with pytest.raises(ValueError, match="FeatureCollection"):
        bake.parse_features(json.dumps({**collection, "bbox": [0, 0, 1, 1]}), "MultiLineString")


def test_check_inputs_refuses_a_missing_or_altered_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="missing"):
        bake.check_inputs(tmp_path)
    for item in bake.INPUTS:
        (tmp_path / item.filename).write_bytes(b"x" * item.bytes)
    with pytest.raises(SystemExit, match="sha256"):
        bake.check_inputs(tmp_path)
    (tmp_path / bake.INPUTS[0].filename).write_bytes(b"x")
    with pytest.raises(SystemExit, match="bytes"):
        bake.check_inputs(tmp_path)


# =============================================================================================
# The committed files
# =============================================================================================


@pytest.fixture(scope="module")
def lines() -> ConstellationLines:
    return ConstellationLines.model_validate_json((OUT_DIR / "lines.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bounds() -> ConstellationBoundaries:
    return ConstellationBoundaries.model_validate_json((OUT_DIR / "bounds.json").read_text(encoding="utf-8"))


def test_committed_files_validate_are_one_line_and_hold_all_89(lines: ConstellationLines, bounds: ConstellationBoundaries) -> None:
    for name, doc in (("lines.json", lines), ("bounds.json", bounds)):
        text = (OUT_DIR / name).read_text(encoding="utf-8")
        assert text.count("\n") == 1
        assert json.loads(text) == doc.to_contract_dict()
        assert doc.release == "d3-celestial d2e20e104b86429d90ac8227a5b021262b45d75a"
        assert sorted(e.code for e in doc.constellations) == sorted(CONSTELLATION_CODES)
    assert {e.code: e.rank for e in lines.constellations} == {e.code: e.rank for e in bounds.constellations}
    assert Counter(e.rank for e in lines.constellations) == {1: 22, 2: 24, 3: 43}
    assert sum(len(p) for e in lines.constellations for p in e.polylines) == 893
    assert sum(len(e.ring) for e in bounds.constellations) == 1659


def test_contract_fixture_entries_are_the_committed_entries(lines: ConstellationLines, bounds: ConstellationBoundaries) -> None:
    """The seven real entries of each file that the contract tests hold."""
    committed_lines = {e["code"]: e for e in lines.to_contract_dict()["constellations"]}
    committed_bounds = {e["code"]: e for e in bounds.to_contract_dict()["constellations"]}
    for code, entry in REAL_LINES.items():
        assert committed_lines[code] == entry
    for code, entry in REAL_BOUNDS.items():
        assert committed_bounds[code] == entry


# =============================================================================================
# Real data: the G 12 star tiles, names.json and openngc.json
# =============================================================================================


def _unit(ra_deg: np.ndarray, dec_deg: np.ndarray) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def _back_to_j2000(v: np.ndarray, pm_ra: np.ndarray, pm_dec: np.ndarray) -> np.ndarray:
    """Move unit vectors at the tiles' epoch back to J2000 along their proper motion (mas/yr, pmRa with cos dec)."""
    east = np.cross(np.array([0.0, 0.0, 1.0]), v)
    east /= np.linalg.norm(east, axis=-1, keepdims=True)
    north = np.cross(v, east)
    dt = 2000.0 - TILE_EPOCH
    moved = v + east * np.radians(pm_ra * MAS_DEG * dt)[..., None] + north * np.radians(pm_dec * MAS_DEG * dt)[..., None]
    return moved / np.linalg.norm(moved, axis=-1, keepdims=True)


@pytest.fixture(scope="module")
def stars_j2000() -> tuple[np.ndarray, np.ndarray]:
    """Every star of the G 12 tiles brighter than magnitude 7, as J2000 unit vectors, with its magnitude."""
    vectors, mags = [], []
    for core in sorted(STARS.glob("L*/*.core.bin")):
        header, cols = decode_core_tile(core.read_bytes())
        if not header.count:
            continue
        mag = decode_mag(cols["mag"])
        keep = mag < 7.0
        if not keep.any():
            continue
        _, detail = decode_detail_tile(core.with_name(core.name.replace(".core.", ".detail.")).read_bytes())
        pm_ra = np.nan_to_num(decode_f16(detail["pmRa"]).astype(np.float64))[keep]
        pm_dec = np.nan_to_num(decode_f16(detail["pmDec"]).astype(np.float64))[keep]
        vectors.append(_back_to_j2000(decode_core_directions(header, cols).astype(np.float64)[keep], pm_ra, pm_dec))
        mags.append(mag[keep])
    return np.concatenate(vectors), np.concatenate(mags)


def test_line_vertices_sit_on_bright_stars_at_j2000(lines: ConstellationLines, stars_j2000: tuple[np.ndarray, np.ndarray]) -> None:
    """Measured on the committed tiles: median 0.14 arcsec, 99.5% within 5 arcsec, all within 20 arcsec.

    The few beyond 5 arcsec are bright stars where the figure's position and the catalogue's differ most (the worst,
    17.7 arcsec, in Ursa Major); without the proper motion the worst would be 52 arcsec (Alpha Centauri).
    """
    stars, _ = stars_j2000
    points = np.unique(np.array([pt for e in lines.constellations for p in e.polylines for pt in p]), axis=0)
    v = _unit(points[:, 0], points[:, 1])
    nearest = np.concatenate([np.max(v[i : i + 64] @ stars.T, axis=1) for i in range(0, len(v), 64)])
    sep_arcsec = np.degrees(np.arccos(np.clip(nearest, -1.0, 1.0))) * 3600
    assert len(points) == 757
    assert sep_arcsec.max() < 20.0
    assert np.mean(sep_arcsec <= 5.0) >= 0.99


def _to_b1875(ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = _unit(np.asarray(ra_deg, float), np.asarray(dec_deg, float)) @ precession_matrix_iau1976(JD_B1875).T
    return np.degrees(np.arctan2(v[..., 1], v[..., 0])) % 360, np.degrees(np.arcsin(np.clip(v[..., 2], -1.0, 1.0)))


def _wrap(x: np.ndarray) -> np.ndarray:
    return (x + 180.0) % 360.0 - 180.0


def containing(bounds: ConstellationBoundaries, ra_deg: np.ndarray, dec_deg: np.ndarray) -> list[list[str]]:
    """The codes whose boundary holds each J2000 point, by ray crossings in B1875 coordinates."""
    pa, pd = _to_b1875(ra_deg, dec_deg)
    found: list[list[str]] = [[] for _ in range(len(pa))]
    for entry in bounds.constellations:
        ring = np.array(entry.ring)
        a, d = _to_b1875(ring[:, 0], ring[:, 1])
        crossings = np.zeros(len(pa), dtype=int)
        for i in range(len(a) - 1):
            span = _wrap(a[i + 1] - a[i])
            if span == 0:
                continue  # an edge along a meridian never crosses the ray beside it
            t = _wrap(pa - a[i]) / span
            crossings += (t >= 0) & (t < 1) & (d[i] + t * (d[i + 1] - d[i]) > pd)
        inside = crossings % 2 == 1
        if abs(_wrap(np.diff(a)).sum()) > 180 and d.mean() > 0:
            inside = ~inside  # the ring goes round the north celestial pole, where the ray ends
        for j in np.nonzero(inside)[0]:
            found[j].append(entry.code)
    return found


def test_only_octans_and_ursa_minor_ring_a_pole(bounds: ConstellationBoundaries) -> None:
    rings = {}
    for entry in bounds.constellations:
        ring = np.array(entry.ring)
        a, _ = _to_b1875(ring[:, 0], ring[:, 1])
        rings[entry.code] = round(float(_wrap(np.diff(a)).sum()))
    assert {code for code, winding in rings.items() if winding != 0} == {"Oct", "UMi"}
    assert {abs(rings["Oct"]), abs(rings["UMi"])} == {360}


@pytest.fixture(scope="module")
def named_stars_j2000() -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray]:
    names = json.loads((STARS / "names.json").read_text(encoding="utf-8"))["names"]
    ra, dec = np.array([n["raDeg"] for n in names]), np.array([n["decDeg"] for n in names])
    pm_ra, pm_dec = np.zeros(len(names)), np.zeros(len(names))
    for i, n in enumerate(names):
        _, detail = decode_detail_tile((STARS / f"L{n['layer']}" / f"{n['pix']}.detail.bin").read_bytes())
        pm_ra[i] = np.nan_to_num(float(decode_f16(detail["pmRa"][n["index"] : n["index"] + 1])[0]))
        pm_dec[i] = np.nan_to_num(float(decode_f16(detail["pmDec"][n["index"] : n["index"] + 1])[0]))
    v = _back_to_j2000(_unit(ra, dec), pm_ra, pm_dec)
    return names, np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360, np.degrees(np.arcsin(v[:, 2]))


@pytest.mark.parametrize(("star", "code"), [("Acrux", "Cru"), ("Rigil Kentaurus", "Cen"), ("Canopus", "Car")])
def test_southern_stars_fall_in_their_constellations(bounds: ConstellationBoundaries, named_stars_j2000: Any, star: str, code: str) -> None:
    names, ra, dec = named_stars_j2000
    i = next(i for i, n in enumerate(names) if n["name"] == star)
    assert containing(bounds, ra[i : i + 1], dec[i : i + 1]) == [[code]]


def test_every_named_star_falls_in_exactly_its_iau_constellation(bounds: ConstellationBoundaries, named_stars_j2000: Any) -> None:
    """Measured: all 410 IAU-named stars of names.json, each in exactly one boundary, the one the IAU lists."""
    names, ra, dec = named_stars_j2000
    found = containing(bounds, ra, dec)
    assert len(names) == 410
    for n, codes in zip(names, found, strict=True):
        allowed = {"Se1", "Se2"} if n["constellation"] == "Ser" else {n["constellation"]}
        assert len(codes) == 1 and codes[0] in allowed, (n["name"], n["constellation"], codes)


def test_every_positioned_openngc_object_falls_in_exactly_its_constellation(bounds: ConstellationBoundaries) -> None:
    """Measured: all 14,026 positioned rows of OpenNGC v20260501 agree with the boundaries, so the threshold is all."""
    rows = [r for r in json.loads(OPENNGC.read_text(encoding="utf-8"))["rows"] if r[DEEP_SKY_COLUMNS.index("raDeg")] is not None]
    ra = np.array([r[DEEP_SKY_COLUMNS.index("raDeg")] for r in rows])
    dec = np.array([r[DEEP_SKY_COLUMNS.index("decDeg")] for r in rows])
    found = containing(bounds, ra, dec)
    assert len(rows) == 14_026
    wrong = [(r[0], r[DEEP_SKY_COLUMNS.index("constellation")], codes) for r, codes in zip(rows, found, strict=True) if codes != [r[DEEP_SKY_COLUMNS.index("constellation")]]]
    assert wrong == []


# =============================================================================================
# Record, doc and registry
# =============================================================================================


def test_provenance_record_keeps_the_bsd_notice_and_says_what_the_lines_are() -> None:
    record = AssetProvenanceRecord.model_validate_json((OUT_DIR / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert [f.path for f in record.files] == ["lines.json", "bounds.json"]
    for entry, item in zip(record.files, bake.INPUTS, strict=True):
        assert entry.sha256 == hashlib.sha256((OUT_DIR / entry.path).read_bytes()).hexdigest()
        assert entry.sources == (item.source_id,)
        assert entry.produced_by.script == "scripts/bake_constellations_d3celestial.py"
        assert "catalog-derived" in entry.note
    assert "not an IAU product" in record.files[0].note
    assert "densify" in record.files[1].note and "B1875" in record.files[1].note
    for src, item in zip(record.sources, bake.INPUTS, strict=True):
        assert src.sha256 == item.sha256
        assert src.license.spdx == "BSD-3-Clause"
        assert bake.BSD_NOTICE in (src.license.conditions or "")
        assert "Olaf Frohn" in src.credit
        assert str(src.url) == f"https://raw.githubusercontent.com/ofrohn/d3-celestial/{bake.COMMIT}/data/{item.filename}"
    assert "Davenhall" in (record.sources[1].citation or "")


def test_science_doc_states_licence_equinoxes_and_limits() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "\u2014" not in doc  # no em dash characters in project prose
    for required in ("d2e20e104b86", "Olaf Frohn", "BSD-3-Clause", "not an IAU product", "B1875", "J2000",
                     "densify", "Se1", "Se2", "`catalog-derived`", "Davenhall"):  # fmt: skip
        assert required in doc
    assert bake.BSD_NOTICE.split("\n")[0] in doc and "ADVISED OF THE POSSIBILITY OF SUCH DAMAGE" in doc


def test_registry_agrees_with_the_inputs_the_bake_pins() -> None:
    entries = {e["path"]: e for e in json.loads((ROOT / "data" / "downloads.json").read_text(encoding="utf-8"))["downloads"]}
    for item in bake.INPUTS:
        entry = entries[f".cache/d3celestial/{item.filename}"]
        assert (entry["url"], entry["bytes"], entry["sha256"], entry["licence"]) == (
            f"https://raw.githubusercontent.com/ofrohn/d3-celestial/{bake.COMMIT}/data/{item.filename}",
            item.bytes,
            item.sha256,
            "BSD-3-Clause",
        )


# =============================================================================================
# Real data: the cached files
# =============================================================================================


@pytest.mark.local_dataset(LINES_REL, BOUNDS_REL)
def test_cached_files_match_pins_and_rebake_is_byte_identical() -> None:
    """Byte comparison is platform-stable here: the bake only adds 360, rounds and copies values (each correctly
    rounded under IEEE 754 on every platform), with no trigonometry, exp or noise."""
    cache = ROOT / ".cache" / "d3celestial"
    bake.check_inputs(cache)
    for name, text in bake.built_texts(cache).items():
        assert text == (OUT_DIR / name).read_text(encoding="utf-8")
    assert bake.verify_outputs(cache, OUT_DIR) == []
