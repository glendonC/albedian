"""Catalog metadata parity on `AlbedianTriangleSolver` (`services/science/solver.py`) matched findings.

Verifies that `solve_and_verify_field`:
- keeps all 18 default J2000 `PINNED_CATALOG_STARS` titles, subtitles, and limitations byte-identical;
- formats non-Bayer, Flamsteed, IAU names-file, and unnamed `CatalogStar` rows through `star_title` and
  `designation_parts` without ever repeating the title or writing literal `"None"`;
- emits `magnitude_band_words(st.magnitude_band)` in `limitations` whenever `st.magnitude_band` is set;
- handles matched `CatalogStar` rows with `has_distance=False` cleanly under `parse_observation_solution`.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import numpy as np

from services.contracts.astronomy import parse_observation_solution
from services.science import phone_photo_solver as pps
from services.science.catalog import PINNED_CATALOG_STARS, get_catalog_stars
from services.science.catalog_model import CatalogStar
from services.science.designations import designation_parts, star_title
from services.science.phone_photo_solver import NAMING_FIT_STAR_NOTE, magnitude_band_words
from services.science.solver import detect_point_sources, render_synthetic_starfield, solve_and_verify_field

MOMENT = {"obs_utc_iso": "2001-01-01T00:00:00Z", "observer_lat": 12.5, "observer_lon": 45.25}


def _synthetic_detections() -> tuple[list, str]:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    sha = hashlib.sha256(np.ascontiguousarray(frame).tobytes()).hexdigest()
    return detect_point_sources(frame), sha


def test_solver_default_pinned_catalog_matches_stay_byte_identical() -> None:
    dets, sha = _synthetic_detections()
    sol = solve_and_verify_field(dets, 960, 540, source_asset_id="syn-pinned-42", source_sha256=sha, **MOMENT)
    assert sol.status == "calibrated"
    payload = sol.to_contract_dict()
    parse_observation_solution(payload)
    assert "None" not in json.dumps(payload)

    by_id = {st.id: st for st in get_catalog_stars()}
    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 8
    for finding in matched:
        st = by_id[finding.catalog_star.id]
        assert finding.title == st.common_name
        assert finding.subtitle == (
            f"{st.bayer_designation} · {st.median_pc:.1f} pc [{st.lower_pc:.1f} to {st.upper_pc:.1f} pc]"
        )
        expected_lims = (
            f"Distance is {st.distance_source}, not measured from this image.",
            *((NAMING_FIT_STAR_NOTE,) if not finding.verified_by_holdout else ()),
        )
        assert finding.limitations == expected_lims


def test_solver_tile_and_flamsteed_catalog_stars_omit_none_use_star_title_and_name_magnitude_band() -> None:
    dets, sha = _synthetic_detections()
    base = get_catalog_stars()
    customized: list[CatalogStar] = []
    for idx, st in enumerate(base):
        if idx % 3 == 0:
            # Flamsteed + IAU names-file designation with no Bayer designation
            customized.append(
                replace(
                    st,
                    bayer_designation=None,
                    flamsteed_number=10 + idx,
                    names_file_designation=f"HR {2000 + idx}",
                    constellation="Ori",
                    magnitude_band="Hipparcos Hp",
                )
            )
        elif idx % 3 == 1:
            # Unnamed in names.json (common_name == catalog_source_id) with Bayer + Flamsteed -> star_title picks Bayer
            customized.append(
                replace(
                    st,
                    common_name=st.catalog_source_id,
                    flamsteed_number=20 + idx,
                    constellation="Ori",
                    magnitude_band="Gaia G",
                )
            )
        else:
            # Completely unnamed star with no Bayer, Flamsteed, or names-file designation
            customized.append(
                replace(
                    st,
                    common_name=st.catalog_source_id,
                    bayer_designation=None,
                    flamsteed_number=None,
                    names_file_designation=None,
                    constellation="Ori",
                    magnitude_band="Gaia G",
                )
            )

    sol = solve_and_verify_field(
        dets, 960, 540, catalog_stars=customized, source_asset_id="syn-custom-42", source_sha256=sha, **MOMENT
    )
    assert sol.status == "calibrated"
    payload = sol.to_contract_dict()
    parse_observation_solution(payload)
    assert "None" not in json.dumps(payload)

    by_id = {st.id: st for st in customized}
    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 8
    seen_kinds: set[int] = set()
    index_by_id = {st.id: idx % 3 for idx, st in enumerate(customized)}
    for finding in matched:
        st = by_id[finding.catalog_star.id]
        kind = index_by_id[st.id]
        seen_kinds.add(kind)
        assert finding.title == star_title(st)
        dist = f"{st.median_pc:.1f} pc [{st.lower_pc:.1f} to {st.upper_pc:.1f} pc]"
        want_parts = [*designation_parts(st), dist]
        assert finding.subtitle == " · ".join(want_parts)
        assert finding.title not in finding.subtitle.split(" · ")
        assert magnitude_band_words(st.magnitude_band) in finding.limitations
        if kind == 1:
            assert finding.title == st.bayer_designation
        if kind == 2:
            assert finding.title == st.catalog_source_id
            assert finding.subtitle == dist
    assert seen_kinds == {0, 1, 2}


def test_solver_matched_star_without_distance_never_crashes_or_writes_none() -> None:
    dets, sha = _synthetic_detections()
    no_dist: list[CatalogStar] = []
    for idx, st in enumerate(get_catalog_stars()):
        if idx % 2 == 0:
            no_dist.append(
                replace(st, lower_pc=None, median_pc=None, upper_pc=None, distance_method=None, magnitude_band="Gaia G")
            )
        else:
            no_dist.append(
                replace(
                    st,
                    common_name=st.catalog_source_id,
                    bayer_designation=None,
                    flamsteed_number=None,
                    names_file_designation=None,
                    lower_pc=None,
                    median_pc=None,
                    upper_pc=None,
                    distance_method=None,
                )
            )

    sol = solve_and_verify_field(
        dets, 960, 540, catalog_stars=no_dist, source_asset_id="syn-nodist-42", source_sha256=sha, **MOMENT
    )
    assert sol.status == "calibrated"
    payload = sol.to_contract_dict()
    parse_observation_solution(payload)
    assert "None" not in json.dumps(payload)

    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 8
    seen_designated = seen_unnamed = False
    for finding in matched:
        st = finding.catalog_star
        assert st is not None and not st.has_distance
        assert "Its catalog row gives no distance, so none is shown." in finding.limitations
        parts = designation_parts(st)
        if parts:
            seen_designated = True
            assert finding.subtitle == " · ".join(parts)
        else:
            seen_unnamed = True
            assert finding.subtitle == st.catalog_source_id
    assert seen_designated and seen_unnamed


def test_solver_with_real_tile_star_index_rows_matches_cleanly() -> None:
    dets, sha = _synthetic_detections()
    tile_index = pps._get_tile_index()
    data = tile_index.data
    row_by_source = {
        (f"HIP {int(data['sid'][i])}" if bool(data["hip"][i]) else f"Gaia DR3 {int(data['sid'][i])}"): i
        for i in range(len(data["sid"]))
    }
    tile_backed: list[CatalogStar] = []
    for pinned in PINNED_CATALOG_STARS:
        row_i = row_by_source[pinned.catalog_source_id]
        # Keep the synthetic frame's J2000 coordinates while taking all metadata from the real shipped tile row
        tile_star = tile_index.to_catalog_star(row_i, pinned.ra_deg, pinned.dec_deg, 2000.0)
        assert tile_star is not None
        tile_backed.append(tile_star)

    sol = solve_and_verify_field(
        dets, 960, 540, catalog_stars=tile_backed, source_asset_id="syn-tiles-42", source_sha256=sha, **MOMENT
    )
    assert sol.status == "calibrated"
    payload = sol.to_contract_dict()
    parse_observation_solution(payload)
    assert "None" not in json.dumps(payload)

    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    assert len(matched) >= 8
    for finding in matched:
        st = finding.catalog_star
        assert st is not None and st.magnitude_band is not None
        assert finding.title == star_title(st)
        assert finding.subtitle == " · ".join(
            [*designation_parts(st), f"{st.median_pc:.1f} pc [{st.lower_pc:.1f} to {st.upper_pc:.1f} pc]"]
        )
        assert magnitude_band_words(st.magnitude_band) in finding.limitations
