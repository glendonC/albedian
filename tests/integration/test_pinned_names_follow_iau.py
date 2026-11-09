"""Pinned star names follow the IAU star-name table the app ships, and a name is never printed twice.

- Every pinned star that the IAU table (`apps/web/public/catalog/stars/gaia-dr3/names.json`, the WGSN names) lists
  by its HIP or Gaia DR3 id carries that row's name
- No pinned star carries a name the IAU gives another star (epsilon Cygni was pinned as Gienah, gamma Corvi's name)
- A pinned star the table does not list shows its Bayer designation, and the list of such stars is explicit, so a
  new one fails until someone reviews it
- The horizon solver's title and the voice description leave out a parenthesis that repeats the name
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from services.science.catalog import ALL_SKY_CATALOG_STARS
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.voice import intent

ROOT = Path(__file__).resolve().parents[2]
NAMES = ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3" / "names.json"
# Pinned stars the IAU table does not name, reviewed: the WGSN has named none of them, so each shows its Bayer
# designation
NOT_IN_THE_IAU_TABLE = {"star-gamma-cas"}
# A synthetic moment and place that point at nobody; gamma Cas (Dec +60.7) and Polaris never set at latitude 30
MOMENT = {"obs_utc_iso": "2031-08-30T03:01:00Z", "observer_lat": 30.0, "observer_lon": -40.0}


def _rows_by_source() -> dict[str, dict]:
    """The table's rows by the catalog source id a pinned star carries ("HIP 102488", "Gaia DR3 ...")."""
    rows = json.loads(NAMES.read_text(encoding="utf-8"))["names"]
    prefix = {"hipparcos-2007": "HIP ", "gaia-dr3": "Gaia DR3 "}
    return {prefix[row["catalog"]] + row["catalogId"]: row for row in rows}


def test_every_pinned_star_the_iau_table_lists_carries_its_iau_name() -> None:
    rows = _rows_by_source()
    wrong = {
        star.id: (star.common_name, rows[star.catalog_source_id]["name"])
        for star in ALL_SKY_CATALOG_STARS
        if star.catalog_source_id in rows and star.common_name != rows[star.catalog_source_id]["name"]
    }
    assert wrong == {}


def test_no_pinned_star_carries_a_name_the_iau_gives_another_star() -> None:
    source_by_name = {row["name"]: source for source, row in _rows_by_source().items()}
    taken = {
        star.id: star.common_name
        for star in ALL_SKY_CATALOG_STARS
        if star.common_name in source_by_name and source_by_name[star.common_name] != star.catalog_source_id
    }
    assert taken == {}


def test_a_pinned_star_the_iau_table_does_not_list_shows_its_bayer_designation_and_is_reviewed() -> None:
    rows = _rows_by_source()
    unlisted = {star.id: star for star in ALL_SKY_CATALOG_STARS if star.catalog_source_id not in rows}
    assert set(unlisted) == NOT_IN_THE_IAU_TABLE
    assert {star.id: star.common_name for star in unlisted.values()} == {
        star_id: star.bayer_designation for star_id, star in unlisted.items()
    }


def test_the_horizon_title_does_not_repeat_a_name_that_is_its_designation() -> None:
    frame = np.zeros((1792, 1344), dtype=np.float64)
    frame[int(1792 * 0.55) :, :] = 40.0 / 255.0
    frame[118:126, 930:938] = 240.0 / 255.0
    pose = solve_horizon_and_beacon_pose(
        frame, **MOMENT, source_asset_id="synthetic-night", source_sha256=hashlib.sha256(frame.tobytes()).hexdigest()
    )
    titles = {finding.id: finding.title for finding in pose.findings}
    assert titles["finding-predicted-star-gamma-cas"] == "Gamma Cassiopeiae"
    assert titles["finding-predicted-star-polaris"] == "Polaris (Alpha Ursae Minoris)"


def test_the_voice_description_does_not_repeat_a_name_that_is_its_designation() -> None:
    by_id = {star.id: star for star in ALL_SKY_CATALOG_STARS}
    assert intent._describe_star(by_id["star-gamma-cas"]).startswith("Gamma Cassiopeiae (in Cassiopeia, ")
    assert intent._describe_star(by_id["star-vega"]).startswith("Vega (Alpha Lyrae in Lyra, ")
