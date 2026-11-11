"""A matched tile star carries only the values its own catalog row gives (solver-10e).

The phone photo solver once filled what a tile row lacks: a colour of 0.82, a spectral type "G", a radial velocity
of 0, the constellation "ICRS", a Hipparcos parallax rebuilt from the distance, a Hipparcos B-V shown as Gaia BP-RP,
a Bailer-Jones geometric distance labelled photogeometric, an unnamed star's id repeated as its designation, and the
epoch 2026.0 for a photo without a time. These tests read the real Gaia DR3 and Hipparcos 2007 rows shipped in
apps/web/public/catalog/stars/gaia-dr3 and check, field by field:
- the catalog reference of every unpinned L0 and L1 row that can become a finding
- every catalog_matched finding of the phone photo benchmark's solved CI frames (simulated frames, labelled so)
- an unnamed star's constellation: the IAU boundary (Davenhall and Leggett 1989, shipped in
  apps/web/public/catalog/constellations/bounds.json) that holds it, which reproduces the IAU name table's
  constellation for every named star
- a photo without a time: its stars at the catalog's own epoch, and the finding says so
- a typical colour used to rank stars never reaches a reference, and a missing proper motion is left out
- a pinned star's finding takes every catalog value from its tile row; the pinned record adds only its names
- the magnitude's band (Hipparcos Hp or Gaia G) is named, a designation is the name table's in full, and a
  Hipparcos row with no parallax is not cited for one
- a timed photo's stars are at the photo's time as a Julian year, moved there by their rows' own proper motions,
  checked against an independent propagation, with or without a place
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any

import numpy as np
import pytest

from research.benchmarks.phone_photos import render, stars, suite
from services.contracts.catalog_reference import CatalogReference
from services.science import catalog_tiles as ct
from services.science.catalog import ALL_SKY_CATALOG_STARS
from services.science.phone_photo_solver import (
    _get_tile_index,
    _TileStarIndex,
    catalog_matched_subtitle,
    iau_constellation_codes,
    solve_phone_photo,
)

ROOT = Path(__file__).resolve().parents[2]
TILES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
BOUNDS = ROOT / "apps" / "web" / "public" / "catalog" / "constellations" / "bounds.json"
CATALOG_EPOCH = float(json.loads((TILES / "manifest.json").read_text())["epochJulianYear"])
PINNED = {star.catalog_source_id: star for star in ALL_SKY_CATALOG_STARS}
# The 88 IAU constellations: the boundary file's codes, Serpens drawn as its two parts Se1 and Se2
IAU_CODES = {"Ser" if c["code"] in ("Se1", "Se2") else c["code"] for c in json.loads(BOUNDS.read_text())["constellations"]}
# Every key a catalog reference may carry (CatalogReferenceSchema in packages/contracts/src/astronomy.ts)
CONTRACT_KEYS = {
    "id", "provider", "releaseVersion", "catalogSourceId", "commonName", "bayerDesignation", "constellation",
    "objectType", "coordinateEpoch", "raDeg", "decDeg", "apparentMagnitude", "colorIndexBpRp", "spectralType",
    "properMotionRaMasYr", "properMotionDecMasYr", "radialVelocityKmS", "parallaxMas", "parallaxErrorMas",
    "bailerJonesDistancePc", "citation",
}
# The tile's distance kind, as the contract names its method; a Bailer-Jones geometric distance has no name there
METHOD_BY_KIND = {
    ct.DIST_KIND_BJ_PHOTOGEO: "photogeometric_bailer_jones_edr3",
    ct.DIST_KIND_BJ_GEO: None,
    ct.DIST_KIND_PARALLAX_INVERSE: "geometric_parallax",
}


@dataclass(frozen=True)
class Row:
    """One L0 or L1 tile row, decoded straight from the tile bytes."""

    source_id: str
    is_hip: bool
    ra0: float
    dec0: float
    mag: float
    color: float
    color_bv: bool
    pm_ra: float
    pm_dec: float
    rv: float
    median: float
    lower: float
    upper: float
    dist_kind: int
    has_distance: bool
    mag_hp: bool


@pytest.fixture(scope="module")
def rows() -> dict[str, Row]:
    out: dict[str, Row] = {}
    for layer in (0, 1):
        for core_path in sorted((TILES / f"L{layer}").glob("*.core.bin")):
            pix = int(core_path.name.split(".")[0])
            header, cols = ct.decode_core_tile(core_path.read_bytes())
            _, detail = ct.decode_detail_tile((core_path.parent / f"{pix}.detail.bin").read_bytes())
            flags = cols["flags"].astype(np.int64)
            vec = ct.decode_core_directions(header, cols)
            ra0 = np.degrees(np.arctan2(vec[:, 1], vec[:, 0])) % 360.0
            dec0 = np.degrees(np.arcsin(np.clip(vec[:, 2], -1.0, 1.0)))
            med = ct.decode_distance(cols["distance"])
            lo, hi = ct.decode_distance_bounds(med, detail["distanceLower"], detail["distanceUpper"])
            ok = np.isfinite(med) & np.isfinite(lo) & np.isfinite(hi) & (lo > 0.0) & (lo <= med) & (med <= hi)
            mag, color = ct.decode_mag(cols["mag"]), ct.decode_color(cols["color"])
            pm_ra, pm_dec = ct.decode_f16(detail["pmRa"]), ct.decode_f16(detail["pmDec"])
            rv = ct.decode_f16(detail["radialVelocity"])
            sid = (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64)
            for i in range(len(sid)):
                is_hip = bool(flags[i] & ct.FLAG_CATALOG_HIPPARCOS)
                source = f"HIP {sid[i]}" if is_hip else f"Gaia DR3 {sid[i]}"
                out[source] = Row(
                    source, is_hip, float(ra0[i]), float(dec0[i]), float(mag[i]), float(color[i]),
                    bool(flags[i] & ct.FLAG_COLOR_B_V), float(pm_ra[i]), float(pm_dec[i]), float(rv[i]),
                    float(med[i]), float(lo[i]), float(hi[i]), int(flags[i] & ct.FLAG_DIST_KIND_MASK), bool(ok[i]),
                    bool(flags[i] & ct.FLAG_MAG_BAND_HP),
                )
    return out


@pytest.fixture(scope="module")
def names() -> dict[str, dict[str, Any]]:
    doc = json.loads((TILES / "names.json").read_text())
    return {
        (f"HIP {e['catalogId']}" if e.get("catalog") == "hipparcos-2007" else f"Gaia DR3 {e['catalogId']}"): e
        for e in doc["names"]
    }


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _expected(row: Row, name: dict[str, Any] | None, epoch: float, pinned: Any = None) -> dict[str, Any]:
    """What the row itself gives for each field; None means the field must be absent. A pinned star's record gives
    only its names."""
    method = METHOD_BY_KIND[row.dist_kind]
    if pinned is not None:
        common, designation = pinned.common_name, pinned.bayer_designation
    else:
        common = name["name"] if name else row.source_id
        designation = (name.get("designation") or None) if name else None  # the name table's full designation
    return {
        "provider": "Hipparcos" if row.is_hip else "Gaia DR3",
        "catalogSourceId": row.source_id,
        "commonName": common,
        "bayerDesignation": designation,
        "objectType": "star",
        "coordinateEpoch": f"J{epoch:.1f}",
        "apparentMagnitude": row.mag,
        # A Hipparcos row's colour is Johnson B-V, which the contract has no field for
        "colorIndexBpRp": None if row.color_bv else _finite(row.color),
        "spectralType": None,  # neither release carries one
        "properMotionRaMasYr": _finite(row.pm_ra),
        "properMotionDecMasYr": _finite(row.pm_dec),
        "radialVelocityKmS": _finite(row.rv),
        "parallaxMas": None,  # the tiles carry no parallax; one rebuilt from the distance is not the catalog's
        "parallaxErrorMas": None,
        "bailerJonesDistancePc": (
            {"lower": row.lower, "median": row.median, "upper": row.upper, "method": method} if method else None
        ),
    }


def _assert_row_values(
    ref: dict[str, Any], row: Row, name: dict[str, Any] | None, epoch: float, pinned: Any = None
) -> None:
    source = row.source_id
    assert set(ref) <= CONTRACT_KEYS, (source, set(ref) - CONTRACT_KEYS)
    for key, want in _expected(row, name, epoch, pinned).items():
        got = ref.get(key)
        if want is None:
            assert key not in ref, (source, key, got)
        elif isinstance(want, float):
            assert got == pytest.approx(want, rel=1e-9, abs=1e-9), (source, key)
        elif isinstance(want, dict):
            assert got is not None and got["method"] == want["method"], (source, key, got)
            for bound in ("lower", "median", "upper"):
                assert got[bound] == pytest.approx(want[bound], rel=1e-9), (source, key, bound)
        else:
            assert got == want, (source, key, got)
    # The constellation: the name table's where the star has an entry, else a real IAU constellation
    if pinned is not None:
        assert ref["constellation"] == pinned.constellation and ref["id"] == pinned.id, source
    elif name is not None:
        assert ref["constellation"] == name["constellation"], source
    else:
        assert ref["constellation"] in IAU_CODES, (source, ref["constellation"])
    # A designation as the catalog writes it in full, never a raw abbreviation such as "alf" or "tet01"
    if "bayerDesignation" in ref:
        assert not re.fullmatch(r"[a-z]{3}\d*", ref["bayerDesignation"]), (source, ref["bayerDesignation"])
    # The citation names only what the row carries: the magnitude's band, and no parallax the row lacks
    citation = ref["citation"]
    assert ("Hipparcos Hp magnitude" if row.mag_hp else "Gaia G magnitude") in citation, (source, citation)
    assert "parallax" not in citation.lower(), (source, citation)
    # The citation names only what the row carries, and the distance as the row's own kind
    if not row.is_hip:
        citation = ref["citation"]
        assert ("color index (BP-RP)" in citation) == ("colorIndexBpRp" in ref), source
        assert ("radial velocity" in citation) == ("radialVelocityKmS" in ref), source
        assert citation.endswith("photogeometric." if row.dist_kind == ct.DIST_KIND_BJ_PHOTOGEO else " geometric."), source
    assert "None" not in json.dumps(ref), source
    CatalogReference.model_validate(ref)


@pytest.fixture(scope="module")
def unpinned(rows: dict[str, Row]) -> list[tuple[Any, Row]]:
    """(CatalogStar at the catalog epoch, its row) for every unpinned row with a distance."""
    index = _get_tile_index()
    out = []
    for i in range(len(index.data["sid"])):
        sid = int(index.data["sid"][i])
        source = f"HIP {sid}" if bool(index.data["hip"][i]) else f"Gaia DR3 {sid}"
        if source in PINNED:
            continue
        star = index.to_catalog_star(i, float(index.data["ra0"][i]), float(index.data["dec0"][i]), index.epoch_jyear)
        if star is None:
            assert not rows[source].has_distance, source
            continue
        out.append((star, rows[source]))
    return out


# =============================================================================================
# Every unpinned row, field by field
# =============================================================================================


def test_the_shipped_tiles_hold_rows_these_checks_reach(rows: dict[str, Row], names: dict[str, Any]) -> None:
    usable = [r for r in rows.values() if r.has_distance and r.source_id not in PINNED]
    assert sum(math.isnan(r.color) for r in usable) > 0
    assert sum(r.color_bv for r in usable) > 100
    assert sum(math.isnan(r.rv) for r in usable) > 1000
    assert sum(r.dist_kind == ct.DIST_KIND_BJ_GEO for r in usable) > 100
    assert sum(r.is_hip for r in usable) > 100
    assert sum(r.source_id not in names for r in usable) > 10_000


def test_every_unpinned_tile_star_carries_only_its_rows_values(unpinned, names: dict[str, Any]) -> None:
    assert len(unpinned) > 60_000
    for star, row in unpinned:
        _assert_row_values(star.to_catalog_reference_dict(), row, names.get(row.source_id), CATALOG_EPOCH)


def test_every_pinned_star_takes_its_rows_values_and_only_its_names(rows: dict[str, Row]) -> None:
    index = _get_tile_index()
    checked = 0
    for i in range(len(index.data["sid"])):
        sid = int(index.data["sid"][i])
        source = f"HIP {sid}" if bool(index.data["hip"][i]) else f"Gaia DR3 {sid}"
        if source not in PINNED:
            continue
        star = index.to_catalog_star(i, float(index.data["ra0"][i]), float(index.data["dec0"][i]), index.epoch_jyear)
        _assert_row_values(star.to_catalog_reference_dict(), rows[source], None, CATALOG_EPOCH, PINNED[source])
        checked += 1
    assert checked == len(PINNED)


def test_a_title_never_repeats_in_its_subtitle(unpinned) -> None:
    for star, row in unpinned:
        subtitle = catalog_matched_subtitle(star)
        assert star.common_name not in subtitle and "None" not in subtitle, row.source_id
    index = _get_tile_index()
    tabit = next(s for s in index._pinned_by_source.values() if s.common_name == "Tabit")
    assert tabit.spectral_type is None  # its pinned Gaia DR3 row carries none
    assert catalog_matched_subtitle(tabit).startswith(f"{tabit.bayer_designation} · ")
    assert "None" not in catalog_matched_subtitle(tabit)


# =============================================================================================
# Constellations from the IAU boundaries
# =============================================================================================


def test_every_tile_row_lies_in_exactly_one_iau_constellation(rows: dict[str, Row]) -> None:
    ra = np.array([r.ra0 for r in rows.values()])
    dec = np.array([r.dec0 for r in rows.values()])
    codes = iau_constellation_codes(ra, dec)
    assert len(codes) == len(rows)
    assert all(code in IAU_CODES for code in codes)
    assert len(set(codes)) == 88


def test_the_boundaries_give_every_named_star_its_iau_constellation(rows: dict[str, Row], names) -> None:
    named = [(rows[source], entry) for source, entry in names.items() if source in rows]
    assert len(named) >= 380  # the IAU names that fall in L0 and L1
    codes = iau_constellation_codes(np.array([r.ra0 for r, _ in named]), np.array([r.dec0 for r, _ in named]))
    for (row, entry), code in zip(named, codes, strict=True):
        assert code == entry["constellation"], (entry["name"], code)


def test_the_lookup_holds_at_both_poles_and_across_zero_right_ascension() -> None:
    # Directions on the sky, not catalog values: the J2000 celestial poles (in Ursa Minor and Octans) and both sides
    # of the J2000 vernal equinox, which lies in Pisces
    codes = iau_constellation_codes(np.array([0.0, 0.0, 359.95, 0.05]), np.array([90.0, -90.0, 0.0, 0.0]))
    assert codes == ["UMi", "Oct", "Psc", "Psc"]


# =============================================================================================
# Findings of solved benchmark frames (simulated) against their tile rows
# =============================================================================================


def _solve(frame: render.RenderedFrame, *, with_time: bool, with_place: bool = True):
    meta = frame.truth.metadata
    sha = hashlib.sha256(np.ascontiguousarray(frame.rgb).tobytes()).hexdigest()
    return solve_phone_photo(
        frame.rgb,
        obs_utc_iso=meta.get("utc") if with_time else None,
        observer_lat=meta.get("latDeg") if with_place else None,
        observer_lon=meta.get("lonDeg") if with_place else None,
        camera_label=meta.get("camera"),
        source_asset_id=f"frame-{sha[:12]}",
        source_sha256=sha,
    )


@pytest.fixture(scope="module")
def ci_frames() -> list[render.RenderedFrame]:
    catalog = stars.TileCatalog()
    return [render.render_frame(spec, catalog) for spec in suite.build_suite("ci") if spec.kind == "positive"]


def _matched(solution) -> list[dict[str, Any]]:
    return [
        f.to_contract_dict(source_asset_id=solution.source_asset_id)
        for f in solution.findings
        if f.evidence_class == "catalog_matched"
    ]


def test_every_finding_of_a_solved_ci_frame_equals_its_tile_row(ci_frames, rows, names) -> None:
    checked = 0
    for frame in ci_frames:
        solution = _solve(frame, with_time=True)
        if solution.status != "calibrated":
            continue
        epoch = _julian_year(frame.truth.metadata["utc"])
        for finding in _matched(solution):
            ref = finding["catalogRef"]
            source = ref["catalogSourceId"]
            text = json.dumps(finding)
            assert "None" not in text and "ICRS" not in json.dumps(ref), source
            assert ref["commonName"] not in finding["subtitle"], source
            row = rows[source]
            # Pinned or not, every catalog value is the row's; a pinned record adds only the star's names
            _assert_row_values(ref, row, names.get(source), epoch, PINNED.get(source))
            band = "Hipparcos Hp" if row.mag_hp else "Gaia G"
            assert any(f"magnitude is {band}" in note for note in finding["evidence"]["limitations"]), source
            # The finding's own distance block names the row's method, a Bailer-Jones geometric one included
            assert finding["inferredDistancePc"]["median"] == pytest.approx(row.median, rel=1e-9)
            checked += 1
    assert checked >= 50


def test_a_photo_without_a_time_puts_its_stars_at_the_catalog_epoch(ci_frames) -> None:
    solved = 0
    for frame in ci_frames:
        solution = _solve(frame, with_time=False)
        if solution.status != "calibrated":
            continue
        for finding in _matched(solution):
            assert finding["catalogRef"]["coordinateEpoch"] == f"J{CATALOG_EPOCH:.1f}"
            assert any("catalog epoch" in note and "no capture time" in note for note in finding["evidence"]["limitations"])
        solved += 1
    assert solved >= 1


def _julian_year(utc: str) -> float:
    """The Julian year of an ISO time, computed here on its own: J2000.0 is 2000-01-01T12:00 and a year 365.25 days."""
    moment = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return 2000.0 + (moment - datetime(2000, 1, 1, 12, tzinfo=timezone.utc)).total_seconds() / 86400.0 / 365.25


def _carried(row: Row, years: float) -> tuple[float, float]:
    """The row's direction moved by its own proper motion, to first order on the sphere (an independent check:
    the second-order term is under 0.001 arcsec for these motions and spans)."""
    pm_ra = row.pm_ra if math.isfinite(row.pm_ra) else 0.0
    pm_dec = row.pm_dec if math.isfinite(row.pm_dec) else 0.0
    dec = row.dec0 + pm_dec * years / 3.6e6
    ra = row.ra0 + pm_ra * years / 3.6e6 / math.cos(math.radians(row.dec0))
    return ra % 360.0, dec


@pytest.mark.parametrize("with_place", [True, False], ids=["time-and-place", "time-only"])
def test_a_timed_photo_moves_its_stars_by_their_rows_proper_motion_to_its_time(ci_frames, rows, with_place) -> None:
    solved = moved = 0
    for frame in ci_frames:
        solution = _solve(frame, with_time=True, with_place=with_place)
        if solution.status != "calibrated":
            continue
        epoch = _julian_year(frame.truth.metadata["utc"])
        assert abs(epoch - CATALOG_EPOCH) > 1.0  # the moves below are then large enough to see
        for finding in _matched(solution):
            ref = finding["catalogRef"]
            row = rows[ref["catalogSourceId"]]
            assert ref["coordinateEpoch"] == f"J{epoch:.1f}", ref["catalogSourceId"]
            ra, dec = _carried(row, epoch - CATALOG_EPOCH)
            cos_dec = math.cos(math.radians(dec))
            off_arcsec = 3600.0 * math.hypot(((ref["raDeg"] - ra + 180.0) % 360.0 - 180.0) * cos_dec, ref["decDeg"] - dec)
            assert off_arcsec < 0.005, (ref["catalogSourceId"], off_arcsec)
            if math.hypot(row.pm_ra, row.pm_dec) * abs(epoch - CATALOG_EPOCH) / 1000.0 > 0.5:
                moved += 1  # moved by more than 0.5 arcsec, so a wrong epoch or no move fails above
            notes = finding["evidence"]["limitations"]
            assert not any("catalog epoch" in note for note in notes)
            assert any("no place" in note and "capture time" in note for note in notes) == (not with_place)
        solved += 1
    assert solved >= 1 and moved >= 3


# =============================================================================================
# Values the solver needs for its own arithmetic
# =============================================================================================


def test_the_typical_colour_used_for_ranking_never_reaches_a_reference(unpinned) -> None:
    index = _get_tile_index()
    no_colour = [(s, r) for s, r in unpinned if math.isnan(r.color) and not r.is_hip]
    assert no_colour
    by_source = {
        (f"HIP {int(index.data['sid'][i])}" if index.data["hip"][i] else f"Gaia DR3 {int(index.data['sid'][i])}"): i
        for i in range(len(index.data["sid"]))
    }
    for star, row in no_colour:
        assert math.isfinite(float(index.data["v_mag"][by_source[row.source_id]]))  # ranked with the typical colour
        assert star.bp_rp is None and "colorIndexBpRp" not in star.to_catalog_reference_dict()


def test_a_missing_proper_motion_is_left_out_and_still_propagates_as_none(tmp_path: Path) -> None:
    # No shipped row with a distance lacks a proper motion, so a copy of the L0 and L1 tiles has one real Gaia row's
    # motion blanked in its bytes (NaN half floats, the tiles' own missing value) before the loader reads it
    release = tmp_path / "gaia-dr3"
    release.mkdir()
    for name in ("manifest.json", "names.json"):
        (release / name).write_bytes((TILES / name).read_bytes())
    for layer in ("L0", "L1"):
        shutil.copytree(TILES / layer, release / layer)
    target = None
    for core_path in sorted((release / "L0").glob("*.core.bin")):
        _, cols = ct.decode_core_tile(core_path.read_bytes())
        detail_path = core_path.parent / core_path.name.replace(".core.", ".detail.")
        blob = bytearray(detail_path.read_bytes())
        _, detail = ct.decode_detail_tile(blob)
        med = ct.decode_distance(cols["distance"])
        lo, hi = ct.decode_distance_bounds(med, detail["distanceLower"], detail["distanceUpper"])
        has_distance = np.isfinite(med) & np.isfinite(lo) & np.isfinite(hi) & (lo > 0.0) & (lo <= med) & (med <= hi)
        sid = (detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64)
        unpinned = np.array([f"Gaia DR3 {s}" not in PINNED for s in sid])
        gaia = np.flatnonzero(((cols["flags"].astype(np.int64) & ct.FLAG_CATALOG_HIPPARCOS) == 0) & has_distance & unpinned)
        if gaia.size:
            i = int(gaia[0])
            detail["pmRa"][i] = detail["pmDec"][i] = np.float16("nan").view(np.uint16)
            detail_path.write_bytes(bytes(blob))
            target = (int(detail["idHi"][i]) << 32) | int(detail["idLo"][i])
            break
    assert target is not None
    index = _TileStarIndex(release)
    row = int(np.flatnonzero(~index.data["hip"] & (index.data["sid"] == target))[0])
    assert index.has_distance(row)
    star = index.to_catalog_star(row, float(index.data["ra0"][row]), float(index.data["dec0"][row]), index.epoch_jyear)
    assert star is not None and star.pm_ra_mas_yr is None and star.pm_dec_mas_yr is None
    ref = star.to_catalog_reference_dict()
    assert "properMotionRaMasYr" not in ref and "properMotionDecMasYr" not in ref
    # Placement treats the missing motion as none, for the arithmetic only
    _, ra, dec, _ = index.query_bright(v_limit=8.0, max_stars=100_000, epoch_jyear=index.epoch_jyear + 50.0)
    assert np.all(np.isfinite(ra)) and np.all(np.isfinite(dec))


def test_a_designation_equal_to_the_name_is_not_repeated(unpinned) -> None:
    # No shipped star reaches this now (an unnamed star has no designation), so a real star is given its own name
    star, _ = next((s, r) for s, r in unpinned if s.bayer_designation is not None)
    same = replace(star, bayer_designation=star.common_name)
    assert star.common_name not in catalog_matched_subtitle(same)
    assert catalog_matched_subtitle(star).startswith(f"{star.bayer_designation} · ")
