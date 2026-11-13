"""Every function of the ephemeris unit that takes a place refuses one that is not a place, at its door.

Covers, from one edge table (each end inside, one step outside, not finite), for every public function of the unit
whose parameters name a latitude or longitude (catalog.py, ephemeris.py, pose_estimate.py, horizon_solver.py,
predicted_sky.py) and the two API requests that had no range (POST /api/science/solve, /api/science/ephemeris):
- a latitude in [-90, 90] and a longitude in [-180, 180] pass the door (the function may still refuse for its own
  reasons, never with PlaceOutOfRange)
- one step outside either range, or either not finite, raises PlaceOutOfRange (the API answers 422)
- the rule itself: floats back, the value never in the message
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable

from fastapi.testclient import TestClient
import numpy as np
import pytest

from services.api import app
from services.science import catalog, ephemeris, horizon_solver, pose_estimate, predicted_sky
from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL, camera_profile
from services.science.catalog import PlaceOutOfRange, require_place

MOMENT = "2031-08-30T03:01:00Z"
# A SHA-256 of a synthetic name: the code refuses the all-zero placeholder
SHA = hashlib.sha256(b"synthetic-door").hexdigest()
STEP = 1e-9
PLACE_EDGES = [
    (-90.0, 0.0, False), (90.0, 0.0, False), (0.0, -180.0, False), (0.0, 180.0, False),
    (-90.0 - STEP, 0.0, True), (90.0 + STEP, 0.0, True), (0.0, -180.0 - STEP, True), (0.0, 180.0 + STEP, True),
    (math.nan, 0.0, True), (math.inf, 0.0, True), (-math.inf, 0.0, True), (0.0, math.nan, True), (0.0, math.inf, True),
]
_MOON = pose_estimate.topocentric_moon(MOMENT, 30.0, -40.0)
_PROFILE = camera_profile(META_RAY_BAN_DISPLAY_MODEL)
_LUMA = np.zeros((64, 48))
DOORS: dict[str, Callable[[float, float], Any]] = {
    "catalog.observer_astrom": lambda lat, lon: catalog.observer_astrom(MOMENT, lat, lon),
    "catalog.solar_deflection_arcsec": lambda lat, lon: catalog.solar_deflection_arcsec(10.0, 5.0, lat, lon, MOMENT),
    "catalog.icrs_to_altaz": lambda lat, lon: catalog.icrs_to_altaz(10.0, 5.0, lat, lon, MOMENT),
    "catalog.altaz_to_icrs": lambda lat, lon: catalog.altaz_to_icrs(30.0, 100.0, lat, lon, MOMENT),
    "catalog.rigid_horizon_rotation": lambda lat, lon: catalog.rigid_horizon_rotation(lat, lon, MOMENT),
    "catalog.icrs_to_rigid_altaz": lambda lat, lon: catalog.icrs_to_rigid_altaz(10.0, 5.0, lat, lon, MOMENT),
    "catalog.rigid_altaz_to_icrs": lambda lat, lon: catalog.rigid_altaz_to_icrs(30.0, 100.0, lat, lon, MOMENT),
    "catalog.anchored_horizon_rotation": lambda lat, lon: catalog.anchored_horizon_rotation(10.0, 5.0, lat, lon, MOMENT),
    # 0.05 degree from the Sun is the peak band, the function's early answer: the door comes before it
    "catalog.solar_deflection_at_separation_arcsec":
        lambda lat, lon: catalog.solar_deflection_at_separation_arcsec(0.05, lat, lon, MOMENT),
    "catalog.anchored_rotation_omission_arcsec":
        lambda lat, lon: catalog.anchored_rotation_omission_arcsec(10.0, 5.0, [(10.0, 5.0, 1.0)], lat, lon, MOMENT),
    "ephemeris.compute_solar_system_ephemerides":
        lambda lat, lon: ephemeris.compute_solar_system_ephemerides(MOMENT, lat, lon, bodies=("Moon",)),
    "pose_estimate.topocentric_moon": lambda lat, lon: pose_estimate.topocentric_moon(MOMENT, lat, lon),
    "pose_estimate.moon_is_up": lambda lat, lon: pose_estimate.moon_is_up(MOMENT, lat, lon),
    "pose_estimate.sun_is_up": lambda lat, lon: pose_estimate.sun_is_up(MOMENT, lat, lon),
    "pose_estimate.fit_level_pose": lambda lat, lon: pose_estimate.fit_level_pose(
        _MOON, 24.0, 32.0, width=48, height=64, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None, moment=MOMENT,
        lat=lat, lon=lon),
    "pose_estimate.pose_frame": lambda lat, lon: pose_estimate.pose_frame(_MOON, lat, lon, MOMENT),
    "pose_estimate.frame_term_arcsec": lambda lat, lon: pose_estimate.frame_term_arcsec(_MOON, [], [], lat, lon, MOMENT),
    "pose_estimate.head_worn_moon_pose": lambda lat, lon: pose_estimate.head_worn_moon_pose(
        _LUMA, frame_time_sec=0.0, moment=MOMENT, lat=lat, lon=lon, profile=_PROFILE, orientation=None),
    "pose_estimate.pose_from_frames": lambda lat, lon: pose_estimate.pose_from_frames(
        [], lat=lat, lon=lon, profile=_PROFILE, orientation=None),
    "horizon_solver.solve_horizon_and_beacon_pose": lambda lat, lon: horizon_solver.solve_horizon_and_beacon_pose(
        _LUMA, MOMENT, lat, lon, source_asset_id="synthetic-door", source_sha256=SHA),
    "predicted_sky.predicted_sky_findings": lambda lat, lon: predicted_sky.predicted_sky_findings(
        MOMENT, lat, lon, solution_id="sol-door", source_sha256=SHA),
}


@pytest.mark.parametrize("door", sorted(DOORS))
@pytest.mark.parametrize(("lat", "lon", "refused"), PLACE_EDGES)
def test_every_door_at_each_edge_of_a_place(door: str, lat: float, lon: float, refused: bool) -> None:
    call = DOORS[door]
    if refused:
        with pytest.raises(PlaceOutOfRange) as caught:
            call(lat, lon)
        # At its own door: the function that called the rule is the door itself, not a function it calls later
        names = [entry.name for entry in caught.traceback]
        assert names[names.index("require_place") - 1] == door.split(".")[1], names
        return
    try:
        call(lat, lon)
    except PlaceOutOfRange:  # pragma: no cover - the failure this test exists for
        pytest.fail(f"{door} refused a real place ({lat}, {lon})")
    except Exception:
        # Its own refusal (no frame, no field), not the door's
        pass


def test_the_rule_returns_floats_and_never_names_the_value() -> None:
    assert require_place(np.float64(-90.0), 180) == (-90.0, 180.0)
    assert all(type(v) is float for v in require_place(np.float64(12.5), np.int64(3)))
    for lat, lon in ((90.0 + STEP, 0.0), (0.0, 180.0 + STEP), (math.nan, 0.0)):
        with pytest.raises(PlaceOutOfRange) as caught:
            require_place(lat, lon)
        assert repr(lat) not in str(caught.value) and repr(lon) not in str(caught.value)
    assert issubclass(PlaceOutOfRange, ValueError)


@pytest.mark.parametrize(("route", "extra"), [
    ("/api/science/ephemeris", {"bodies": ["Moon"]}),
    ("/api/science/solve", {"sourceAssetId": f"src_{SHA[:16]}", "sourceSha256": SHA}),
])
@pytest.mark.parametrize(("lat", "lon", "refused"), [row for row in PLACE_EDGES if math.isfinite(row[0] + row[1])])
def test_the_api_refuses_a_place_that_is_not_one(route: str, extra: dict, lat: float, lon: float, refused: bool) -> None:
    response = TestClient(app).post(route, json={"observedAtUTC": MOMENT, "latDeg": lat, "lonDeg": lon, **extra})
    detail = response.json().get("detail", []) if response.status_code == 422 else []
    # Both routes hold the place to the contract's rule at the door and answer with its sentence, which names the
    # field (never the value); a validation error list names it in its location
    if isinstance(detail, str):
        place_errors = [name for name in ("latDeg", "lonDeg") if name in detail]
        assert not [v for v in (lat, lon) if abs(v) not in (0.0, 90.0, 180.0) and repr(v) in detail], detail
    else:
        place_errors = [e for e in detail if isinstance(e, dict) and e.get("loc", [None])[-1] in ("latDeg", "lonDeg")]
    assert bool(place_errors) == refused, (route, lat, lon, response.status_code, response.json())
