"""The predicted stars of any sky on Earth come from the catalog tiles (catalog-1; spec predicted-sky-for-every-upload,
REQ-SKY-1.1 and P-SKY-1).

The predicted sky once took its stars from 71 pinned records, none south of declination -29.6 degrees, so the far
southern sky had 14 to 22 stars. These tests read the real Gaia DR3 and Hipparcos 2007 L0 and L1 rows shipped in
apps/web/public/catalog/stars/gaia-dr3 and check:
- P-SKY-1 over the tiles: every row to an approximate V of 4.5, and every row the shipped IAU name file names, that is
  above the horizon is present exactly once, and nothing else; each is above the horizon at its own place
- every catalog value of a predicted star equals its row (solver-10e's guard), its names are the IAU name file's alone,
  a row without a distance gives a finding without one, and a star a solve matched is left out by its catalog id
- the batch transform and the batch propagation equal their single-star twins
- each position lies within its stated uncertainty of ERFA's eraAtco13, and of a second route through other ERFA
  functions, with the truth drawn anywhere inside each stated part (UT1 - UTC, polar motion, parallax, the stored
  proper motion and the motion a row lacks); and eraAtco13 reproduces ERFA's own published test values
- the southern case: at latitude -60 Canopus, Rigil Kentaurus, Acrux and Achernar are present by HIP number and named,
  with at least 400 stars up
- the stated uncertainty is the sum of its listed parts, and the tile maxima it uses hold for every row
Places are made-up open ocean at longitude -40 or drawn at random; moments are synthetic.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
import warnings

import erfa
from hypothesis import example, given, settings, strategies as st
import numpy as np
import pytest

from services.science.catalog import (
    KM_S_TO_PC_YR,
    catalog_cartesian_pc,
    _iso_to_julian_date,
    icrs_to_altaz,
    icrs_to_altaz_batch,
    propagate_directions_batch,
    propagate_star_epoch,
    time_scales,
    ALL_SKY_CATALOG_STARS,
)
from services.contracts.designation import catalog_title, flamsteed_long_designation
from services.science.catalog_model import hipparcos_2007_tile_citation
from services.science.ephemeris import EARTH_ROTATION_ARCSEC_PER_S, POLAR_MOTION_BOUND_ARCSEC
from services.science.phone_photo_solver import TYPICAL_BP_RP_FOR_RANKING, _get_tile_index
from services.science.designations import baked_tile_row_designations as _tile_row_designations
from services.science.designations import designation_of_row
from services.science.predicted_sky import (
    FASTEST_PROPER_MOTION_MAS_YR,
    FASTEST_RADIAL_VELOCITY_KM_S,
    HALF_FLOAT_RELATIVE_ERROR,
    PREDICTED_STAR_DEPTH_V,
    STAR_PARALLAX_BOUND_ARCSEC,
    STAR_POSITION_LIMITATION,
    STAR_TRANSFORM_ARCSEC,
    SPEED_OF_LIGHT_KM_S,
    _star_without_distance,
    frame_moment,
    predicted_sky_findings,
    predicted_star_uncertainty_arcsec,
)
from tests.integration.test_no_invented_catalog_values import (  # noqa: F401 (fixtures)
    CATALOG_EPOCH,
    IAU_CODES,
    PINNED,
    Row,
    _assert_row_values,
    names,
    rows,
)

# A real SHA-256 of a fixed text: the contract refuses 64 zeros as a placeholder that points at no recording
SHA = hashlib.sha256(b"a synthetic recording for the predicted sky tests").hexdigest()
MAS_RAD = math.pi / (180.0 * 3600.0 * 1000.0)
ARCSEC_RAD = math.pi / (180.0 * 3600.0)
# ERFA's epochs are TDB Julian dates; the tiles' J2016.0 is JD 2457389.0
J2016_JD = 2457389.0
# The second route leaves out diurnal aberration: the Earth's rotation at the equator, 0.4651 km/s, over the speed of
# light, is 0.32 arcsec
DIURNAL_ABERRATION_ARCSEC = 0.33
# A star this close to the horizon may fall either side between two placements that agree to a few mas
HORIZON_BAND_DEG = 1e-3
NIGHTS = ("2031-08-30T02:40:00Z", "2032-02-29T02:40:00Z")


def _iso(moment: datetime) -> str:
    return moment.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _approximate_v(row: Row) -> float:
    """The ranking V the depth is cut at, written out here from its source: Riello et al. (2021) G - V in BP-RP for a
    Gaia row (the solver's stated colour for a row without one), Hp as it is for a Hipparcos row."""
    if row.is_hip:
        return row.mag
    c = row.color if (math.isfinite(row.color) and not row.color_bv) else TYPICAL_BP_RP_FOR_RANKING
    c = min(max(c, -0.5), 2.75)
    return row.mag - (-0.02704 + 0.01424 * c - 0.2156 * c * c + 0.01426 * c**3)


@pytest.fixture(scope="module")
def candidates(rows: dict[str, Row], names: dict[str, Any]) -> list[Row]:
    """Every row the predicted sky draws from: to the decided depth, approximate V 4.5 (catalog-1's decision 1, written
    here rather than read from the module), or named."""
    return [r for r in rows.values() if _approximate_v(r) <= 4.5 or r.source_id in names]


def _pm_rad(row: Row, pm_ra: float, pm_dec: float) -> tuple[float, float]:
    """ERFA's proper motion inputs: dRA/dt (not times cos dec) and dDec/dt, radians per year."""
    return pm_ra * MAS_RAD / math.cos(math.radians(row.dec0)), pm_dec * MAS_RAD


def _carried(row: Row, year: float) -> tuple[float, float]:
    """The row carried to `year` by ERFA's eraPmsafe (its own space motion, with light time), independent of the
    module's propagation; a row without a proper motion stays."""
    if not (math.isfinite(row.pm_ra) and math.isfinite(row.pm_dec)):
        return row.ra0, row.dec0
    pmr, pmd = _pm_rad(row, row.pm_ra, row.pm_dec)
    px = 1.0 / row.median if row.has_distance else 1e-3
    rv = row.rv if math.isfinite(row.rv) else 0.0
    jd = 2451545.0 + (year - 2000.0) * 365.25
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        ra, dec, *_ = erfa.pmsafe(
            math.radians(row.ra0), math.radians(row.dec0), pmr, pmd, px, rv, J2016_JD, 0.0, jd, 0.0
        )
    return math.degrees(float(ra)) % 360.0, math.degrees(float(dec))


def _year(moment: str) -> float:
    jd = sum(time_scales(moment).tt)
    return 2000.0 + (jd - 2451545.0) / 365.25


def _stars(findings) -> dict[str, Any]:
    return {f.catalog_star.catalog_source_id: f for f in findings if f.catalog_star is not None}


UTC = st.just(timezone.utc)
in_range = st.datetimes(min_value=datetime(1800, 1, 1), max_value=datetime(2050, 12, 31, 23, 59), timezones=UTC)
from_1960 = st.datetimes(min_value=datetime(1960, 1, 1), max_value=datetime(2050, 12, 31, 23, 59), timezones=UTC)
lat_st = st.floats(min_value=-89.9, max_value=89.9, allow_nan=False)
lon_st = st.floats(min_value=-180.0, max_value=180.0, allow_nan=False)


# =============================================================================================
# P-SKY-1 over the tiles
# =============================================================================================


@settings(max_examples=25, deadline=None)
@given(when=in_range, lat=lat_st, lon=lon_st)
@example(when=datetime(2031, 8, 30, 2, 40, tzinfo=timezone.utc), lat=-60.0, lon=-40.0)
@example(when=datetime(1800, 1, 1, tzinfo=timezone.utc), lat=-89.9, lon=179.9)
@example(when=datetime(2050, 12, 31, 23, 59, tzinfo=timezone.utc), lat=89.9, lon=-179.9)
def test_pbt_every_tile_star_to_the_depth_or_named_that_is_up_is_there_once(
    candidates: list[Row], names: dict[str, Any], when: datetime, lat: float, lon: float
) -> None:
    moment = _iso(when)
    year = _year(moment)
    by_source = {r.source_id: r for r in candidates}
    findings = predicted_sky_findings(moment, lat, lon, solution_id="sol_synthetic", source_sha256=SHA)
    ids = [f.id for f in findings]
    assert len(ids) == len(set(ids))
    found = _stars(findings)
    assert len(found) == sum(1 for f in findings if f.catalog_star is not None)
    for finding in found.values():
        alt, az = icrs_to_altaz(finding.ra_deg, finding.dec_deg, lat, lon, moment)
        assert alt > 0.0 and finding.alt_deg == alt and finding.az_deg == az
        assert 0.0 <= finding.ra_deg < 360.0 and finding.catalog_star.ra_deg == finding.ra_deg
    expected, edge = set(), set()
    for row in candidates:
        alt, _ = icrs_to_altaz(*_carried(row, year), lat, lon, moment)
        if abs(alt) < HORIZON_BAND_DEG:
            edge.add(row.source_id)
        elif alt > 0.0:
            expected.add(row.source_id)
    missing, extra = sorted(expected - set(found))[:5], sorted(set(found) - expected)[:5]
    assert expected - edge <= set(found) <= expected | edge, (missing, extra)
    # Brightest first, by the approximate V the depth is cut at
    order = [(round(_approximate_v(by_source[s]), 9), f.catalog_star.id) for s, f in found.items()]
    assert order == sorted(order) and len(found) >= 300  # equal V (8 such pairs in the pool) ordered by id
    for finding in found.values():
        assert finding.subtitle.startswith(f"Predicted Star ({finding.catalog_star.constellation}) · ")
        assert finding.subtitle.endswith(" · Above Horizon")
    # The named-star rule: every named row that is up is there, the faint ones too
    named_up = {s for s in expected if s in names}
    assert named_up <= set(found)


def test_named_stars_fainter_than_the_depth_are_there_when_up(candidates: list[Row], names: dict[str, Any]) -> None:
    faint = {r.source_id for r in candidates if _approximate_v(r) > PREDICTED_STAR_DEPTH_V}
    assert faint and faint <= set(names)
    seen = set()
    for moment in NIGHTS:
        for lat in (60.0, 30.0, 0.0, -30.0, -60.0):
            seen |= faint & set(_stars(predicted_sky_findings(moment, lat, -40.0, solution_id="s", source_sha256=SHA)))
    # Made-up places on two nights see most of the 102 faint named stars at least once
    assert len(seen) >= 0.8 * len(faint), (len(seen), len(faint))


# The depth, read here from the tiles by this file's own reader (`_approximate_v` on the guard's rows): the unnamed
# row nearest inside approximate V 4.5 and the one nearest outside it. Each edge is one row: moving the depth to
# either neighbour row changes what a sky holds.
DEPTH_EDGES = (
    # (catalog id, approximate V, inside the depth, a made-up place where it never sets)
    ("Gaia DR3 1337345927125084416", 4.49987, True, 60.0),
    ("Gaia DR3 5517171678276669696", 4.50076, False, -60.0),
)
# Stars up at the made-up places of longitude -40 on the two synthetic nights, at the decided depth (catalog-1)
SKY_COUNTS = {
    60.0: (467, 462), 30.0: (472, 497), 0.0: (490, 526), -30.0: (516, 541), -60.0: (549, 544),
}


def test_the_depth_is_approximate_v_4_5(rows: dict[str, Row], names: dict[str, Any]) -> None:
    assert PREDICTED_STAR_DEPTH_V == 4.5
    for source, v, inside, lat in DEPTH_EDGES:
        row = rows[source]
        assert source not in names and _approximate_v(row) == pytest.approx(v, abs=5e-6)
        # Circumpolar there (declination beyond 90 - |lat|), so up at every moment
        assert abs(row.dec0) > 90.0 - abs(lat) and math.copysign(1.0, row.dec0) == math.copysign(1.0, lat)
        for moment in NIGHTS:
            found = _stars(predicted_sky_findings(moment, lat, -40.0, solution_id="s", source_sha256=SHA))
            assert (source in found) == inside, (source, moment)


def test_the_pool_and_the_stars_up_at_fixed_places_follow_the_depth(
    candidates: list[Row], names: dict[str, Any]
) -> None:
    unnamed = [r for r in candidates if r.source_id not in names]
    assert (len(candidates), len(unnamed), len(candidates) - len(unnamed)) == (1012, 632, 380)
    for lat, counts in SKY_COUNTS.items():
        for moment, count in zip(NIGHTS, counts, strict=True):
            found = _stars(predicted_sky_findings(moment, lat, -40.0, solution_id="s", source_sha256=SHA))
            assert len(found) == count, (lat, moment, len(found))


def test_the_far_southern_sky_holds_its_bright_stars() -> None:
    for moment in NIGHTS:
        found = _stars(predicted_sky_findings(moment, -60.0, -40.0, solution_id="s", source_sha256=SHA))
        assert len(found) >= 400, len(found)
        for hip, name in (("HIP 30438", "Canopus"), ("HIP 71683", "Rigil Kentaurus"), ("HIP 60718", "Acrux"),
                          ("HIP 7588", "Achernar")):
            assert hip in found, (moment, hip)
            assert found[hip].title == name and found[hip].catalog_star.common_name == name


# =============================================================================================
# Every value from its row
# =============================================================================================


def test_every_predicted_star_carries_only_its_rows_values_and_the_name_files_names(
    rows: dict[str, Row], names: dict[str, Any]
) -> None:
    pinned_ids = {s.catalog_source_id: s.id for s in ALL_SKY_CATALOG_STARS}
    designated = _tile_row_designations()
    checked = without_distance = 0
    for moment in NIGHTS:
        year = _year(moment)
        for lat in (60.0, 0.0, -60.0):
            for finding in predicted_sky_findings(moment, lat, -40.0, solution_id="s", source_sha256=SHA):
                star = finding.catalog_star
                if star is None:
                    continue
                row = rows[star.catalog_source_id]
                name = names.get(row.source_id)
                ref = star.to_catalog_reference_dict()
                # The names file's designation is an HR or other catalog number, not a Bayer designation: it is in
                # the subtitle only. The Bayer designation and the Flamsteed number are the baked designations'
                # (catalog-2b), the number only where its constellation is the star's
                _assert_row_values(ref, row, {**name, "designation": None} if name else None, year)
                pinned = PINNED.get(row.source_id)
                want = designation_of_row(
                    row.is_hip, int(row.source_id.split()[-1]), star.constellation, pinned.bayer_designation if pinned else None
                )
                assert (ref.get("bayerDesignation"), ref.get("flamsteedNumber")) == want
                assert designated.get((row.is_hip, int(row.source_id.split()[-1]))) is not None or want[1] is None
                title, parts = catalog_title(ref)
                # The title is the name file's name, else the Bayer designation, else the Flamsteed designation with
                # the genitive in full, else the catalog id (catalog-2c)
                expected_flamsteed_long = (
                    None
                    if ref.get("flamsteedNumber") is None
                    else flamsteed_long_designation(ref["flamsteedNumber"], ref["constellation"])
                )
                expected_title = name["name"] if name else (ref.get("bayerDesignation") or expected_flamsteed_long or row.source_id)
                assert finding.title == title == expected_title
                assert all(f" · {part}" in finding.subtitle for part in parts)
                # The subtitle never repeats the title
                assert finding.title not in finding.subtitle.split(" · ")
                if name and name.get("designation"):
                    assert f" · {name['designation']} · " in finding.subtitle
                tile_id = f"hip-{row.source_id[4:]}" if row.is_hip else f"gaia-dr3-{row.source_id[9:]}"
                assert star.id == pinned_ids.get(row.source_id, tile_id)
                payload = finding.to_contract_dict()
                assert ("inferredDistancePc" in payload) == ("cartesianParsecICRS" in payload) == row.has_distance
                assert ("distanceUncertaintyNote" in payload["evidence"]) == row.has_distance
                if row.has_distance:
                    assert payload["inferredDistancePc"]["median"] == pytest.approx(row.median, rel=1e-9)
                    assert f"{row.median:.1f} pc" in finding.subtitle
                else:
                    without_distance += 1
                    assert " pc" not in finding.subtitle
                    assert any("gives no distance" in text for text in finding.limitations)
                assert "None" not in json.dumps(payload)
                checked += 1
    assert checked > 2000 and without_distance > 0


def test_a_pinned_star_takes_the_name_files_names_not_its_typed_ones(names: dict[str, Any]) -> None:
    found = _stars(predicted_sky_findings(NIGHTS[1], 30.0, -40.0, solution_id="s", source_sha256=SHA))
    betelgeuse = found["HIP 27989"]
    assert betelgeuse.catalog_star.id == PINNED["HIP 27989"].id
    entry = names["HIP 27989"]
    assert betelgeuse.catalog_star.constellation == entry["constellation"] == "Ori"
    # "HR 2061" is a Bright Star Catalogue number, not a Bayer designation: the subtitle's text only. Its Bayer
    # designation and Flamsteed number are the baked designations', which give the pinned record's text exactly
    assert entry["designation"] == "HR 2061"
    assert betelgeuse.catalog_star.bayer_designation == "Alpha Orionis" == PINNED["HIP 27989"].bayer_designation
    assert betelgeuse.catalog_star.flamsteed_number == 58
    assert betelgeuse.title == "Betelgeuse"
    assert " · Alpha Orionis · 58 Ori · HR 2061 · HIP 27989 · " in betelgeuse.subtitle
    # Gamma Cassiopeiae has no IAU name: its typed name is not used; its Bayer designation, from the baked file, is its
    # title, as when it is matched (catalog-2c), and its Flamsteed number and catalog id are in the subtitle
    gamma_cas = _stars(predicted_sky_findings(NIGHTS[0], 60.0, -40.0, solution_id="s", source_sha256=SHA))["HIP 4427"]
    assert gamma_cas.title == "Gamma Cassiopeiae" and gamma_cas.catalog_star.flamsteed_number == 27
    assert " · 27 Cas · HIP 4427 · " in gamma_cas.subtitle and "Gamma Cassiopeiae" not in gamma_cas.subtitle
    assert gamma_cas.catalog_star.constellation in IAU_CODES


def test_a_star_the_solve_matched_is_left_out_by_its_catalog_id() -> None:
    every = predicted_sky_findings(NIGHTS[0], -30.0, -40.0, solution_id="s", source_sha256=SHA)
    stars = [f.catalog_star for f in every if f.catalog_star is not None]
    pinned = next(s.id for s in stars if s.catalog_source_id in PINNED)
    tile = next(s.id for s in stars if s.catalog_source_id not in PINNED)
    fewer = predicted_sky_findings(
        NIGHTS[0], -30.0, -40.0, solution_id="s", source_sha256=SHA, exclude_ids=frozenset({pinned, tile})
    )
    assert {f.id for f in every} - {f.id for f in fewer} == {f"finding-predicted-{pinned}", f"finding-predicted-{tile}"}


def test_each_star_names_its_release_epoch_band_transform_and_that_it_was_not_measured() -> None:
    for finding in predicted_sky_findings(NIGHTS[0], -30.0, -40.0, solution_id="s", source_sha256=SHA):
        star = finding.catalog_star
        if star is None:
            continue
        first = finding.limitations[0]
        assert f"{star.release_version} catalog row (epoch J{CATALOG_EPOCH:.1f})" in first
        assert first.endswith("not measured in the recording.")
        moved = star.pm_ra_mas_yr is not None
        assert ("moved by its proper motion to the date" in first) == moved
        assert ("not moved, since the row has no proper motion" in first) == (not moved)
        assert f"Apparent magnitude is {star.magnitude_band}, not Johnson V." in finding.limitations
        assert finding.limitations[-1] == STAR_POSITION_LIMITATION and "ERFA" in STAR_POSITION_LIMITATION


# =============================================================================================
# The batch twins
# =============================================================================================


def test_an_instant_before_the_year_1000_keeps_four_digits_on_every_machine() -> None:
    # glibc's %Y writes year 999 as "999" under Python 3.12.3 on Linux, and the C library on macOS writes "0999"
    for given_at, offset, written in (
        ("0999-01-01T00:00:00Z", 0.0, "0999-01-01T00:00:00Z"),
        ("0001-01-01T00:00:00Z", 1.0, "0001-01-01T00:00:01Z"),
        ("0999-01-01T00:00:00Z", 0.5, "0999-01-01T00:00:00.500Z"),
    ):
        assert frame_moment(given_at, offset) == written
    assert _iso(datetime(999, 1, 1, tzinfo=timezone.utc)) == "0999-01-01T00:00:00Z"


def test_the_predicted_sky_and_its_tests_write_no_year_through_the_platform() -> None:
    # The macOS C library pads %Y, so the test above can pass there with strftime; this one holds on every machine
    root = Path(__file__).resolve().parents[2]
    platform_year = re.compile(r"\.strf" + r"time\(\s*[\"'][^\"']*%[YGy]")
    files = ("services/science/predicted_sky.py", "tests/integration/test_predicted_sky_from_tiles.py",
             "tests/science/test_predicted_sky.py")
    assert [f for f in files if platform_year.search((root / f).read_text(encoding="utf-8"))] == []


@settings(max_examples=40, deadline=None)
@given(when=st.datetimes(min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31), timezones=UTC),
       lat=lat_st, lon=lon_st, seed=st.integers(0, 2**31 - 1))
@example(when=datetime(999, 1, 1, tzinfo=timezone.utc), lat=0.0, lon=0.0, seed=0)
def test_pbt_the_batch_transform_equals_the_single_one(when: datetime, lat: float, lon: float, seed: int) -> None:
    rng = np.random.default_rng(seed)
    ra = rng.uniform(0.0, 360.0, 25)
    dec = np.degrees(np.arcsin(rng.uniform(-1.0, 1.0, 25)))
    moment = _iso(when)
    alt, az = icrs_to_altaz_batch(ra, dec, lat, lon, moment)
    assert np.all((az >= 0.0) & (az < 360.0)) and np.all(np.abs(alt) <= 90.0)
    for k in range(25):
        assert (float(alt[k]), float(az[k])) == icrs_to_altaz(float(ra[k]), float(dec[k]), lat, lon, moment)


@settings(max_examples=40, deadline=None)
@given(years=st.floats(min_value=-216.0, max_value=35.0, allow_nan=False), seed=st.integers(0, 2**31 - 1))
@example(years=-216.0, seed=0)
def test_pbt_the_batch_propagation_equals_propagate_star_epoch(years: float, seed: int) -> None:
    index = _get_tile_index()
    data = index.data
    full = np.flatnonzero(np.isfinite(data["pm_ra"]) & np.isfinite(data["rv"]) & data["valid_dist"])
    no_rv = np.flatnonzero(np.isfinite(data["pm_ra"]) & ~np.isfinite(data["rv"]) & data["valid_dist"])
    rng = np.random.default_rng(seed)
    pick = np.concatenate([rng.choice(full, 30, replace=False), rng.choice(no_rv, 10, replace=False)])
    # The fastest star and the nearest are always among them
    pick = np.concatenate([pick, [int(np.nanargmax(np.hypot(data["pm_ra"], data["pm_dec"]))),
                                  int(np.nanargmin(np.where(data["valid_dist"], data["lo_pc"], np.nan)))]])
    ra, dec = propagate_directions_batch(
        data["ra0"][pick], data["dec0"][pick], data["pm_ra"][pick], data["pm_dec"][pick], data["rv"][pick],
        data["med_pc"][pick], years,
    )
    assert np.all((ra >= 0.0) & (ra < 360.0)) and np.all(np.abs(dec) <= 90.0)
    for k, row in enumerate(pick.tolist()):
        star = index.to_catalog_star(row, float(data["ra0"][row]), float(data["dec0"][row]), index.epoch_jyear)
        moved = propagate_star_epoch(star, index.epoch_jyear + years)
        sep = _sep_arcsec(_unit(moved.ra_deg, moved.dec_deg), _unit(float(ra[k]), float(dec[k])))
        assert sep < 1e-6, (star.catalog_source_id, sep)


def test_a_star_with_a_radial_velocity_and_a_distance_is_turned_by_them(rows: dict[str, Row]) -> None:
    # Gaia DR3 4847957293278177024 (6.04 pc, 87.75 km/s) is the pool's star its radial motion turns most: about 2.2
    # arcsec by 1800. Its finding there sits where propagate_star_epoch puts it, not where the sky motion alone would
    index = _get_tile_index()
    data = index.data
    row = next(i for i in range(len(data["sid"])) if int(data["sid"][i]) == 4847957293278177024)
    star = index.to_catalog_star(row, float(data["ra0"][row]), float(data["dec0"][row]), index.epoch_jyear)
    moment = "1800-06-01T00:00:00Z"
    lat = math.copysign(89.9, star.dec_deg)  # circumpolar there
    found = _stars(predicted_sky_findings(moment, lat, -40.0, solution_id="s", source_sha256=SHA))
    finding = found[star.catalog_source_id]
    year = 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25
    moved = propagate_star_epoch(star, year)
    flat = propagate_star_epoch(replace(star, rv_km_s=None), year)
    here = _unit(finding.ra_deg, finding.dec_deg)
    assert _sep_arcsec(here, _unit(moved.ra_deg, moved.dec_deg)) < 1e-6
    assert _sep_arcsec(here, _unit(flat.ra_deg, flat.dec_deg)) > 2.0


def test_every_star_just_above_the_horizon_is_there(candidates: list[Row]) -> None:
    # Hourly skies through one synthetic year at a made-up place: every candidate the module's own propagation and the
    # batch transform put between 1e-6 and 0.01 degree above the horizon is listed, and none just below
    index = _get_tile_index()
    data = index.data
    sources = [f"HIP {int(s)}" if h else f"Gaia DR3 {int(s)}" for h, s in zip(data["hip"], data["sid"], strict=True)]
    pool = {r.source_id for r in candidates}
    rows_ = np.array([i for i, s in enumerate(sources) if s in pool])
    distance = np.where(data["valid_dist"][rows_], data["med_pc"][rows_], np.nan)
    low = high = 0
    start = datetime(2031, 1, 1, tzinfo=timezone.utc)
    for hour in range(0, 24 * 365, 37):
        moment = _iso(start + timedelta(hours=hour))
        years = 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25 - index.epoch_jyear
        ra, dec = propagate_directions_batch(data["ra0"][rows_], data["dec0"][rows_], data["pm_ra"][rows_],
                                             data["pm_dec"][rows_], data["rv"][rows_], distance, years)
        alt, _ = icrs_to_altaz_batch(ra, dec, -30.0, -40.0, moment)
        just_up = {sources[rows_[k]] for k in np.flatnonzero((alt > 1e-6) & (alt <= 0.01))}
        just_down = {sources[rows_[k]] for k in np.flatnonzero((alt < -1e-6) & (alt >= -0.01))}
        if just_up or just_down:
            found = set(_stars(predicted_sky_findings(moment, -30.0, -40.0, solution_id="s", source_sha256=SHA)))
            assert just_up <= found and not just_down & found, (moment, just_up - found, just_down & found)
            low, high = low + len(just_down), high + len(just_up)
    assert low >= 3 and high >= 3, (low, high)


def test_a_row_moving_without_a_distance_moves_along_the_sky_only() -> None:
    # No shipped row has a proper motion and a radial velocity but no distance; the rule is checked on one made here
    ra, dec, pm = np.array([80.0, 80.0]), np.array([-20.0, -20.0]), np.array([500.0, 500.0])
    with_rv = propagate_directions_batch(ra, dec, pm, pm, np.array([300.0, np.nan]), np.array([np.nan, np.nan]), -200.0)
    assert with_rv[0][0] == with_rv[0][1] and with_rv[1][0] == with_rv[1][1]
    near = propagate_directions_batch(ra[:1], dec[:1], pm[:1], pm[:1], np.array([300.0]), np.array([2.0]), -200.0)
    assert abs(near[1][0] - with_rv[1][0]) > 1e-4  # the radial term turns a star with a distance


def test_a_row_without_a_proper_motion_or_distance_keeps_its_direction() -> None:
    index = _get_tile_index()
    data = index.data
    rows_ = np.flatnonzero(~np.isfinite(data["pm_ra"]))[:20]
    ra, dec = propagate_directions_batch(
        data["ra0"][rows_], data["dec0"][rows_], data["pm_ra"][rows_], data["pm_dec"][rows_], data["rv"][rows_],
        np.where(data["valid_dist"][rows_], data["med_pc"][rows_], np.nan), -150.0,
    )
    assert np.array_equal(ra, data["ra0"][rows_] % 360.0) and np.array_equal(dec, data["dec0"][rows_])


# =============================================================================================
# Positions within the stated uncertainty of ERFA
# =============================================================================================


def _unit(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])


def _horizon_unit(alt_deg: float, az_deg: float) -> np.ndarray:
    alt, az = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(alt) * math.cos(az), math.cos(alt) * math.sin(az), math.sin(alt)])


def _sep_arcsec(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(a @ b))) * 3600.0


def _truth(row: Row, rng: np.random.Generator, tt: tuple[float, float]) -> tuple[float, float, float]:
    """The row at the moment as ERFA's eraPmsafe moves it, with every value the module states a bound for drawn inside
    that bound: the stored proper motion off by up to its half-float error, a proper motion the row lacks up to the
    fastest in the tiles, a radial velocity it lacks up to the fastest, its parallax from its distance or, without
    one, up to the bound. Returns (ra rad, dec rad, parallax arcsec) at the moment."""
    if math.isfinite(row.pm_ra) and math.isfinite(row.pm_dec):
        pm_ra = row.pm_ra * (1.0 + rng.uniform(-1.0, 1.0) * HALF_FLOAT_RELATIVE_ERROR)
        pm_dec = row.pm_dec * (1.0 + rng.uniform(-1.0, 1.0) * HALF_FLOAT_RELATIVE_ERROR)
    else:
        speed, angle = rng.uniform(0.0, FASTEST_PROPER_MOTION_MAS_YR), rng.uniform(0.0, 2.0 * math.pi)
        pm_ra, pm_dec = speed * math.cos(angle), speed * math.sin(angle)
    rv = row.rv if math.isfinite(row.rv) else rng.uniform(-FASTEST_RADIAL_VELOCITY_KM_S, FASTEST_RADIAL_VELOCITY_KM_S)
    px = 1.0 / row.median if row.has_distance else rng.uniform(1e-3, STAR_PARALLAX_BOUND_ARCSEC)
    pmr, pmd = _pm_rad(row, pm_ra, pm_dec)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", erfa.ErfaWarning)
        ra, dec, _, _, px_at, _ = erfa.pmsafe(math.radians(row.ra0), math.radians(row.dec0), pmr, pmd, px, rv,
                                              J2016_JD, 0.0, tt[0], tt[1])
    return float(ra), float(dec), float(px_at)


@settings(max_examples=15, deadline=None)
@given(when=from_1960, lat=lat_st, lon=lon_st, seed=st.integers(0, 2**31 - 1))
@example(when=datetime(2050, 12, 31, 23, 59, tzinfo=timezone.utc), lat=-60.0, lon=-40.0, seed=1)
def test_pbt_each_position_is_within_its_uncertainty_of_era_atco13(
    rows: dict[str, Row], when: datetime, lat: float, lon: float, seed: int
) -> None:
    rng = np.random.default_rng(seed)
    moment = _iso(when)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", erfa.ErfaWarning)  # a year past the leap-second table is "dubious"
        utc = erfa.dtf2d("UTC", when.year, when.month, when.day, when.hour, when.minute, float(when.second))
    dut1 = rng.uniform(-0.9, 0.9)
    xp, yp = rng.uniform(-0.5, 0.5, 2) * ARCSEC_RAD  # within the 1 arcsec polar motion bound in any direction
    tt = time_scales(moment).tt
    worst = 0.0
    for finding in _stars(predicted_sky_findings(moment, lat, lon, solution_id="s", source_sha256=SHA)).values():
        ra, dec, px = _truth(rows[finding.catalog_star.catalog_source_id], rng, tt)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", erfa.ErfaWarning)
            aob, zob, *_ = erfa.atco13(ra, dec, 0.0, 0.0, px, 0.0, *utc, dut1, math.radians(lon), math.radians(lat),
                                       0.0, xp, yp, 0.0, 0.0, 0.0, 0.0)
        truth = _horizon_unit(90.0 - math.degrees(float(zob)), math.degrees(float(aob)))
        sep = _sep_arcsec(truth, _horizon_unit(finding.alt_deg, finding.az_deg))
        assert sep <= finding.uncertainty_arcsec, (finding.title, sep, finding.uncertainty_arcsec)
        worst = max(worst, sep / finding.uncertainty_arcsec)
    assert worst > 0.0


def _second_route(ra: float, dec: float, px_arcsec: float, moment: str, dut1: float, lat: float, lon: float):
    """The apparent place by other ERFA functions, equinox based: the Earth from eraEpv00, the star's parallax, light
    deflection (eraLdsun), annual aberration (eraAb), IAU 2006/2000A precession and nutation (eraPnm06a), Greenwich
    apparent sidereal time (eraGst06a) and eraHd2ae. It leaves out diurnal aberration and polar motion."""
    scales = time_scales(moment)
    tt = scales.tt
    ut1 = (scales.ut1[0], scales.ut1[1] + dut1 / 86400.0)
    pvh, pvb = erfa.epv00(*tt)
    eh = np.asarray(pvh["p"], dtype=np.float64)
    em = float(np.linalg.norm(eh))
    v = np.asarray(pvb["v"], dtype=np.float64) * (erfa.DAU / erfa.CMPS / 86400.0)
    bm1 = math.sqrt(1.0 - float(v @ v))
    p = _unit(math.degrees(ra), math.degrees(dec)) - px_arcsec * ARCSEC_RAD * np.asarray(pvb["p"], dtype=np.float64)
    p /= np.linalg.norm(p)
    q = erfa.ab(erfa.ldsun(p, eh / em, em), v, em, bm1)
    ra_t, dec_t = erfa.c2s(erfa.pnm06a(*tt) @ q)
    ha = erfa.gst06a(*ut1, *tt) + math.radians(lon) - ra_t
    az, el = erfa.hd2ae(ha, dec_t, math.radians(lat))
    return _horizon_unit(math.degrees(float(el)), math.degrees(float(az)))


@settings(max_examples=15, deadline=None)
@given(when=in_range, lat=lat_st, lon=lon_st, seed=st.integers(0, 2**31 - 1))
@example(when=datetime(1800, 1, 1, 3, 0, tzinfo=timezone.utc), lat=-60.0, lon=-40.0, seed=2)
def test_pbt_each_position_is_within_its_uncertainty_of_a_second_era_route(
    rows: dict[str, Row], when: datetime, lat: float, lon: float, seed: int
) -> None:
    rng = np.random.default_rng(seed)
    moment = _iso(when)
    scales = time_scales(moment)
    dut1 = rng.uniform(-scales.ut1_bound_s, scales.ut1_bound_s)
    for finding in _stars(predicted_sky_findings(moment, lat, lon, solution_id="s", source_sha256=SHA)).values():
        ra, dec, px = _truth(rows[finding.catalog_star.catalog_source_id], rng, scales.tt)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", erfa.ErfaWarning)
            truth = _second_route(ra, dec, px, moment, dut1, lat, lon)
        sep = _sep_arcsec(truth, _horizon_unit(finding.alt_deg, finding.az_deg))
        assert sep <= finding.uncertainty_arcsec + DIURNAL_ABERRATION_ARCSEC, (finding.title, sep)


def test_era_atco13_reproduces_erfas_own_published_values() -> None:
    # t_atco13 in ERFA's t_erfa_c.c (tag v2.0.1, read at source 2026-11-11): its inputs and expected outputs
    got = erfa.atco13(2.71, 0.174, 1e-5, 5e-6, 0.1, 55.0, 2456384.5, 0.969254051, 0.1550675, -0.527800806,
                      -1.2345856, 2738.0, 2.47230737e-7, 1.82640464e-6, 731.0, 12.8, 0.59, 0.55)
    want = (0.9251774485485515207e-1, 1.407661405256499357, -0.9265154431529724692e-1, 0.1716626560072526200,
            2.710260453504961012, -0.003020548354802412839)
    assert max(abs(float(g) - w) for g, w in zip(got[:6], want, strict=True)) < 1e-12


# =============================================================================================
# The stated uncertainty
# =============================================================================================


def test_the_uncertainty_is_the_sum_of_its_stated_parts(rows: dict[str, Row]) -> None:
    moment = NIGHTS[0]
    # The module's Julian year of the moment: from its UTC Julian date
    years = abs(2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25 - CATALOG_EPOCH)
    found = _stars(predicted_sky_findings(moment, -60.0, -40.0, solution_id="s", source_sha256=SHA))
    base = (
        0.9 * EARTH_ROTATION_ARCSEC_PER_S
        + POLAR_MOTION_BOUND_ARCSEC
        + STAR_PARALLAX_BOUND_ARCSEC
        + STAR_TRANSFORM_ARCSEC
    )
    assert base == pytest.approx(15.362, abs=0.001)
    nearest_pc = 1.0 / STAR_PARALLAX_BOUND_ARCSEC
    kinds = set()
    for source, finding in found.items():
        row = rows[source]
        if math.isfinite(row.pm_ra):
            pm = math.hypot(row.pm_ra, row.pm_dec) / 1000.0
            stored, unmoved = pm * 2.0**-11 * years, 0.0
        else:
            pm = FASTEST_PROPER_MOTION_MAS_YR / 1000.0
            stored, unmoved = 0.0, pm * years
        speed = abs(row.rv) if math.isfinite(row.rv) else FASTEST_RADIAL_VELOCITY_KM_S
        turn = speed * KM_S_TO_PC_YR * years / nearest_pc
        perspective = pm * years * turn / (1.0 - turn)
        light_time = pm * years * speed / 299792.458
        want = base + stored + unmoved + perspective + light_time
        assert finding.uncertainty_arcsec == pytest.approx(want, rel=1e-12), source
        kinds.add((math.isfinite(row.pm_ra), math.isfinite(row.rv)))
    assert {(True, True), (True, False), (False, False)} <= kinds
    # Before 1960 the moment is read as UT1 (catalog.time_scales): no rotation part
    still = predicted_star_uncertainty_arcsec(np.array([0.0]), np.array([0.0]), np.array([0.0]), 0.0, 0.0)[0]
    assert still == pytest.approx(POLAR_MOTION_BOUND_ARCSEC + STAR_PARALLAX_BOUND_ARCSEC + STAR_TRANSFORM_ARCSEC)


def test_a_sky_before_1960_states_no_rotation_part(rows: dict[str, Row]) -> None:
    # Before 1960 the moment is read as UT1 itself (catalog.time_scales), so UT1 - UTC adds nothing
    moment = "1901-03-01T22:00:00Z"
    assert time_scales(moment).ut1_bound_s == 0.0
    years = 2000.0 + (_iso_to_julian_date(moment) - 2451545.0) / 365.25 - CATALOG_EPOCH
    found = _stars(predicted_sky_findings(moment, 30.0, -40.0, solution_id="s", source_sha256=SHA))
    for source, finding in found.items():
        row = rows[source]
        want = predicted_star_uncertainty_arcsec(
            np.array([row.pm_ra]), np.array([row.pm_dec]), np.array([row.rv]), years, 0.0
        )[0]
        assert finding.uncertainty_arcsec == pytest.approx(want, rel=1e-12), source


def test_a_row_without_a_distance_is_built_from_its_row_alone(rows: dict[str, Row]) -> None:
    index = _get_tile_index()
    data = index.data
    no_distance = np.flatnonzero(~data["valid_dist"])
    hip = [int(i) for i in no_distance if data["hip"][i]]
    assert len(hip) == 3  # HIP 26224, 26220 and 71502, outside the predicted sky's rows
    gaia = [int(i) for i in no_distance if not data["hip"][i]][:5]
    for i in [*hip, *gaia]:
        star = _star_without_distance(index, i)
        row = rows[star.catalog_source_id]
        # The builder gives no designation; _sky_rows adds the baked one, as the row check expects
        bayer, flamsteed = designation_of_row(row.is_hip, int(row.source_id.split()[-1]), star.constellation)
        designated = replace(star, bayer_designation=bayer, flamsteed_number=flamsteed)
        _assert_row_values(designated.to_catalog_reference_dict(), row, None, CATALOG_EPOCH)
        assert star.id == (f"hip-{row.source_id[4:]}" if row.is_hip else f"gaia-dr3-{row.source_id[9:]}")
        assert star.constellation in IAU_CODES and star.epoch_year == CATALOG_EPOCH
        assert star.magnitude_band == ("Hipparcos Hp" if row.mag_hp else "Gaia G")
        assert (star.ra_deg, star.dec_deg) == pytest.approx((row.ra0 % 360.0, row.dec0), abs=1e-12)
        assert star.distance_source is None and not star.has_distance
        with pytest.raises(ValueError, match="no distance"):
            catalog_cartesian_pc(star)
    pinned_row = next(i for i in range(len(data["sid"])) if f"HIP {int(data['sid'][i])}" in PINNED and data["hip"][i])
    with pytest.raises(ValueError, match="pinned star's row gives a distance"):
        _star_without_distance(index, pinned_row)


def test_a_distance_comes_whole_or_not_at_all() -> None:
    star = ALL_SKY_CATALOG_STARS[0]
    for missing in ("lower_pc", "median_pc", "upper_pc", "distance_method"):
        with pytest.raises(ValueError, match="bounds and method, or not at all"):
            replace(star, **{missing: None})


def test_a_hipparcos_row_without_a_distance_is_not_cited_for_one() -> None:
    assert hipparcos_2007_tile_citation(magnitude_band="Hipparcos Hp", distance=False) == (
        "Catalog id and Hipparcos Hp magnitude: Hipparcos 2007, van Leeuwen (2007) A&A 474, 653."
    )


# The bound by date, as docs/science/PREDICTED_SKY.md states it (mid-year moments): a row with no proper motion or
# radial velocity, and a row moving 50 mas/yr with a radial velocity of 20 km/s
BOUND_BY_DATE = {
    1800: (1640.6, 1.87), 1900: (850.3, 1.84), 1960: (414.9, 15.37), 2016: (18.9, 15.36), 2026: (89.8, 15.36),
    2031: (125.5, 15.36), 2050: (262.0, 15.36),
}


def test_the_bound_grows_with_the_years_from_the_catalog_epoch_as_the_doc_says() -> None:
    for year, (still, moving) in BOUND_BY_DATE.items():
        moment = f"{year}-07-01T00:00:00Z"
        got = predicted_star_uncertainty_arcsec(
            np.array([np.nan, 50.0]), np.array([np.nan, 0.0]), np.array([np.nan, 20.0]), year + 0.5 - CATALOG_EPOCH,
            time_scales(moment).ut1_bound_s,
        )
        assert got.tolist() == pytest.approx([still, moving], abs=0.05), year


def test_each_constant_of_the_bound_is_its_sources_value(rows: dict[str, Row]) -> None:
    every = list(rows.values())
    nearest = min((r for r in every if r.has_distance), key=lambda r: r.lower)
    # The parallax bound: the nearest row's 1 au over its lower distance, rounded up to the mas
    assert STAR_PARALLAX_BOUND_ARCSEC == math.ceil(1000.0 / nearest.lower) / 1000.0 == 0.824
    # The fastest proper motion rounded up to 10 mas/yr, the fastest radial velocity up to 1 km/s
    fastest_pm = max(math.hypot(r.pm_ra, r.pm_dec) for r in every if math.isfinite(r.pm_ra))
    assert FASTEST_PROPER_MOTION_MAS_YR == math.ceil(fastest_pm / 10.0) * 10.0 == 7060.0
    fastest_rv = max(abs(r.rv) for r in every if math.isfinite(r.rv))
    assert FASTEST_RADIAL_VELOCITY_KM_S == math.ceil(fastest_rv) == 388.0
    # A half float's rounding: half its machine epsilon; the speed of light: ERFA's ERFA_CMPS
    assert HALF_FLOAT_RELATIVE_ERROR == float(np.finfo(np.float16).eps) / 2.0 == 2.0**-11
    assert SPEED_OF_LIGHT_KM_S == erfa.CMPS / 1000.0
    assert KM_S_TO_PC_YR == pytest.approx(365.25 * 86400.0 / (erfa.DAU / 1000.0 * 206264.80624709636), rel=1e-12)
    # The transform's part is a stated 0.001 arcsec over its measured 2.1e-10 [proposed]
    assert STAR_TRANSFORM_ARCSEC == 0.001


def test_the_tile_maxima_the_uncertainty_uses_hold_for_every_row(rows: dict[str, Row]) -> None:
    every = list(rows.values())
    nearest = min((r for r in every if r.has_distance), key=lambda r: r.lower)
    assert nearest.source_id == "HIP 71681" and 1.0 / nearest.lower <= STAR_PARALLAX_BOUND_ARCSEC
    # Proxima Centauri, the nearest star, 768.07 mas in Gaia DR3 (not in L0 or L1), sits inside the bound too
    assert 0.76807 <= STAR_PARALLAX_BOUND_ARCSEC
    assert max(math.hypot(r.pm_ra, r.pm_dec) for r in every if math.isfinite(r.pm_ra)) <= FASTEST_PROPER_MOTION_MAS_YR
    assert max(abs(r.rv) for r in every if math.isfinite(r.rv)) <= FASTEST_RADIAL_VELOCITY_KM_S


@settings(max_examples=200, deadline=None)
@given(value=st.floats(min_value=-65000.0, max_value=65000.0, allow_nan=False).filter(lambda v: abs(v) >= 6.2e-5))
def test_pbt_a_half_float_holds_its_value_to_two_to_the_minus_eleven(value: float) -> None:
    # The tiles store proper motions as IEEE half floats (catalog_tiles.py); above the smallest normal, 6.1e-5,
    # rounding to nearest is off by at most half a step, 2^-11 of the value
    assert abs(float(np.float16(value)) - value) <= abs(value) * HALF_FLOAT_RELATIVE_ERROR
