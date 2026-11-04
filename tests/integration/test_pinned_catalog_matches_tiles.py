"""The pinned bright stars carry the catalog ids and distances of their rows in the committed G 12 tiles.

Each of the 71 pinned stars is found in the tiles by position: its J2000 position moved to the tiles'
J2016 epoch with its own proper motion lands within 3 arcsec of exactly one layer 0 row. That row is a
Hipparcos 2007 entry, whose distance is 1000/parallax with bounds from the parallax error, or a Gaia DR3
source with a Bailer-Jones et al. (2021) photogeometric distance. The pinned star must name the same
catalog and id and the same distance method, and its distance and 16th to 84th percentile bounds must
encode to the row's own codes. These are real catalog rows; the test reads only committed files.

Covers:
- Every pinned star resolves to one tile row, and its provider, catalog id and distance method are that row's
- Its distance median and bounds re-encode to the row's codes with `catalog_tiles`' codec
- A Hipparcos star's distance is 1000/parallax; a Gaia DR3 star carries no parallax, since the tiles hold none
- No pinned Gaia DR3 id encodes a HEALPix pixel that misses its own star
- A solved field's catalog matches describe each distance by its own source, in the finding and its evidence
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from services.science import catalog_tiles as ct
from services.science.catalog import ALL_SKY_CATALOG_STARS
from services.science.catalog_model import CatalogStar
from services.science.solver import detect_point_sources, render_synthetic_starfield, solve_and_verify_field

TILES = Path(__file__).resolve().parents[2] / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
MATCH_RADIUS_ARCSEC = 3.0
YEARS_J2000_TO_J2016 = 16.0


def _layer0_rows() -> dict[str, np.ndarray]:
    parts: dict[str, list[np.ndarray]] = {key: [] for key in ("vec", "id", "hip", "kind", "dist", "lo", "hi")}
    for core_path in sorted((TILES / "L0").glob("*.core.bin")):
        header, core = ct.decode_core_tile(core_path.read_bytes())
        _, detail = ct.decode_detail_tile(core_path.with_name(core_path.name.replace(".core.", ".detail.")).read_bytes())
        parts["vec"].append(ct.decode_core_directions(header, core))
        parts["id"].append((detail["idHi"].astype(np.int64) << 32) | detail["idLo"].astype(np.int64))
        parts["hip"].append((core["flags"] & ct.FLAG_CATALOG_HIPPARCOS) > 0)
        parts["kind"].append(core["flags"] & ct.FLAG_DIST_KIND_MASK)
        parts["dist"].append(core["distance"])
        parts["lo"].append(detail["distanceLower"])
        parts["hi"].append(detail["distanceUpper"])
    return {key: np.concatenate(values) for key, values in parts.items()}


ROWS = _layer0_rows()


def _j2016(star: CatalogStar) -> tuple[float, float]:
    ra, dec = ct.propagate_linear(star.ra_deg, star.dec_deg, star.pm_ra_mas_yr, star.pm_dec_mas_yr, YEARS_J2000_TO_J2016)
    return float(ra), float(dec)


def _row_of(star: CatalogStar) -> int:
    sep = ct.angular_separation_arcsec(ROWS["vec"], ct.radec_to_vec(*_j2016(star))[None, :])
    near = np.flatnonzero(sep <= MATCH_RADIUS_ARCSEC)
    assert len(near) == 1, f"{star.common_name}: {len(near)} tile rows within {MATCH_RADIUS_ARCSEC} arcsec"
    return int(near[0])


@pytest.mark.parametrize("star", ALL_SKY_CATALOG_STARS, ids=lambda star: star.id)
def test_pinned_star_carries_its_tile_rows_catalog_id_and_distance(star: CatalogStar) -> None:
    i = _row_of(star)
    source_id = int(ROWS["id"][i])
    if ROWS["hip"][i]:
        assert (star.provider, star.catalog_source_id) == ("Hipparcos", f"HIP {source_id}")
        assert int(ROWS["kind"][i]) == ct.DIST_KIND_PARALLAX_INVERSE
        assert star.distance_method == "geometric_parallax"
        assert star.parallax_mas is not None and star.parallax_error_mas is not None
        assert star.median_pc == pytest.approx(1000.0 / star.parallax_mas, rel=1e-12)
        assert star.lower_pc == pytest.approx(1000.0 / (star.parallax_mas + star.parallax_error_mas), rel=1e-12)
        assert star.upper_pc == pytest.approx(1000.0 / (star.parallax_mas - star.parallax_error_mas), rel=1e-12)
    else:
        assert (star.provider, star.catalog_source_id) == ("Gaia DR3", f"Gaia DR3 {source_id}")
        assert int(ROWS["kind"][i]) == ct.DIST_KIND_BJ_PHOTOGEO
        assert star.distance_method == "photogeometric_bailer_jones_edr3"
        assert star.parallax_mas is None and star.parallax_error_mas is None
    # The pinned distance and bounds encode to the tile row's own codes
    assert int(ct.encode_distance(np.array([star.median_pc]))[0]) == int(ROWS["dist"][i])
    lower_code, upper_code = ct.encode_distance_bounds(
        np.array([star.median_pc]), np.array([star.lower_pc]), np.array([star.upper_pc])
    )
    assert (int(lower_code[0]), int(upper_code[0])) == (int(ROWS["lo"][i]), int(ROWS["hi"][i]))


def test_no_pinned_gaia_id_points_away_from_its_star() -> None:
    # A Gaia DR3 source_id encodes the level 12 HEALPix pixel (about 52 arcsec across) the source sits in, so a
    # real id's pixel centre lies within a pixel's half-diagonal of the star; a star near a pixel edge may sit
    # in the neighbouring pixel, which the 60 arcsec allowance covers
    gaia_stars = [star for star in ALL_SKY_CATALOG_STARS if star.catalog_source_id.startswith("Gaia DR3 ")]
    assert gaia_stars
    nside = float(1 << 12)
    for star in gaia_stars:
        pixel = ct.gaia_source_id_healpix(int(star.catalog_source_id.removeprefix("Gaia DR3 ")), 12)
        face, ix, iy = ct.nest_to_face_ixiy(12, np.atleast_1d(pixel))
        centre = ct.face_xy_to_vec(face, (ix + 0.5) / nside, (iy + 0.5) / nside)
        sep = float(ct.angular_separation_arcsec(centre, ct.radec_to_vec(*_j2016(star))[None, :])[0])
        assert sep <= 60.0, f"{star.common_name}: its Gaia DR3 id points {sep:.0f} arcsec away"


def test_catalog_matches_describe_each_distance_by_its_own_source() -> None:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    sol = solve_and_verify_field(
        detect_point_sources(frame, snr_threshold=4.0), 960, 540,
        obs_utc_iso="2001-01-01T00:00:00Z", observer_lat=12.5, observer_lon=45.25,
    )
    matched = [f for f in sol.findings if f.evidence_class == "catalog_matched"]
    methods = {f.catalog_star.distance_method for f in matched if f.catalog_star is not None}
    assert methods == {"geometric_parallax", "photogeometric_bailer_jones_edr3"}
    for finding in matched:
        star = finding.catalog_star
        assert star is not None
        payload = finding.to_contract_dict()
        texts = " ".join([*finding.limitations, payload["evidence"]["distanceUncertaintyNote"]])
        hipparcos = star.distance_method == "geometric_parallax"
        assert ("Hipparcos 2007" in texts and "1000/parallax" in texts) is hipparcos, star.common_name
        assert ("Bailer-Jones" in texts) is not hipparcos, star.common_name
        assert ("parallaxMas" in payload["catalogRef"]) is hipparcos, star.common_name
