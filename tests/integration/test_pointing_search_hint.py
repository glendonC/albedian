"""The pointing record as a search hint (pointing-4; ios-capture-companion design component 5; REQ-IOS-2.1 to 2.3).

The phone solver's horizon search takes a centre hint and keeps only rotations whose axis lies within the hint's
radius (its catalog is the unhinted one); the plate solver cuts its catalog to the hint's radius plus the frame's half
diagonal. The held-out gate is unchanged, so a hint never makes a solve (P2); when the hinted searches do not
calibrate, the chain runs once more without the hint, so a hint far from the truth never loses one either
(REQ-IOS-2.2, pointing-4 v2).

Frames are the benchmark suite's synthetic phone photos, with their known pointing [simulated].
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.science import phone_photo_solver as pps
from services.science.catalog import altaz_to_icrs
from services.science.solver import angular_separation_deg

_FRAME: Any = None


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def _hint(alt: float, az: float, radius: float) -> dict[str, float]:
    spec = _frame().spec
    ra, dec = altaz_to_icrs(alt, az, spec.lat_deg, spec.lon_deg, spec.utc, refracted=True)
    return {"hint_ra_deg": ra, "hint_dec_deg": dec, "hint_radius_deg": radius}


def _solve(**hint: float) -> Any:
    frame = _frame()
    spec = frame.spec
    return pps.solve_phone_photo(
        frame.rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1",
        solution_id="sol_hint", **hint,
    )


def _centre(solution: Any) -> tuple[float, float]:
    spec = _frame().spec
    return solution.wcs.pixel_to_world(spec.width / 2.0, spec.height / 2.0)


def test_the_true_pointing_as_a_hint_keeps_the_solve_inside_its_radius() -> None:
    spec = _frame().spec
    hint = _hint(spec.alt_deg, spec.az_deg, 10.0)
    solution = _solve(**hint)
    assert solution.status == "calibrated", solution.warnings
    ra, dec = _centre(solution)
    assert angular_separation_deg(ra, dec, hint["hint_ra_deg"], hint["hint_dec_deg"]) <= 10.0


@pytest.mark.parametrize("turn_deg", [60.0, 120.0, 180.0])
def test_a_hint_far_from_the_truth_loses_the_solve_and_never_makes_one(turn_deg: float) -> None:
    spec = _frame().spec
    solution = _solve(**_hint(spec.alt_deg, (spec.az_deg + turn_deg) % 360.0, 10.0))
    assert solution.status != "calibrated"
    assert not [f for f in solution.findings if f.evidence_class == "catalog_matched"]


@pytest.mark.parametrize(
    "hint",
    [
        {"hint_ra_deg": 10.0, "hint_dec_deg": 20.0},
        {"hint_ra_deg": 10.0, "hint_radius_deg": 5.0},
        {"hint_ra_deg": 10.0, "hint_dec_deg": 20.0, "hint_radius_deg": 0.0},
        {"hint_ra_deg": 10.0, "hint_dec_deg": 20.0, "hint_radius_deg": 180.5},
    ],
)
def test_a_hint_gives_its_centre_and_a_radius_in_range_together(hint: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        _solve(**hint)


def test_the_hint_keeps_the_rotations_whose_axis_lies_within_its_radius() -> None:
    # Rows: image right, image down, optical axis, in east, north, up; the hint straight north on the horizon
    def rotation(axis_az: float) -> np.ndarray:
        a = math.radians(axis_az)
        axis = np.array([math.sin(a), math.cos(a), 0.0])
        down = np.array([0.0, 0.0, -1.0])
        return np.stack([np.cross(down, axis), down, axis])

    rotations = np.stack([rotation(az) for az in (0.0, 9.9, 10.1, 90.0, 350.5, 180.0)])
    takes = pps._hint_takes(rotations, np.array([0.0, 1.0, 0.0]), 10.0)
    assert takes.tolist() == [True, True, False, False, True, False]


# ---- The record's heading through the routes (services/ingest/jobs.py) ----

import copy  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
import dataclasses  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from typing import Iterator  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from scipy.spatial.transform import Rotation  # noqa: E402

from services.api import app  # noqa: E402
from services.contracts.pointing import PointingRecord  # noqa: E402
from services.ingest import jobs as ingest_jobs  # noqa: E402
from services.ingest import router as ingest_router  # noqa: E402
from services.ingest.store import IngestStore  # noqa: E402
from services.science.record_hint import record_hint  # noqa: E402

POINTING_SAMPLE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "pointing_contract.json").read_text(encoding="utf-8")
)["valid"][0]["record"]


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _companion_png(*, focal_35mm: bool = True) -> bytes:
    """The frame as the companion app sends it: the camera's EXIF and its own time, no GPS; with `focal_35mm`, the
    35 mm equivalent focal length too."""
    frame = _frame()
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Apple", "iPhone 18 Pro, 24 mm f/1.78"
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = frame.spec.utc[:19].replace("-", ":").replace("T", " "), "+00:00"
    if focal_35mm:
        sub[0xA405] = 24
    buf = io.BytesIO()
    Image.fromarray(frame.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


def _sky_vector(alt: float, az: float) -> np.ndarray:
    a, z = math.radians(alt), math.radians(az)
    return np.array([math.cos(a) * math.cos(z), -math.cos(a) * math.sin(z), math.sin(a)])  # north, west, up


def _record(sha: str, alt: float, az: float, *, accuracy: float = 12.0, heading: bool = True) -> dict[str, Any]:
    """The contract's first sample at this frame's place and time, aimed with the back camera at (alt, az)."""
    spec = _frame().spec
    record = copy.deepcopy(POINTING_SAMPLE)
    record["sourceAssetSha256"] = sha
    record["capturedAtDeviceClockUTC"] = spec.utc.replace("+00:00", "Z")
    record["location"].update(latDeg=spec.lat_deg, lonDeg=spec.lon_deg)
    if not heading:
        record["heading"] = {"state": "none", "reason": "no_heading_event"}
        return record
    camera = _sky_vector(alt, az)
    up = np.array([0.0, 0.0, 1.0]) - camera[2] * camera
    top = up / np.linalg.norm(up)
    rotation = np.column_stack([np.cross(top, -camera), top, -camera])
    x, y, z, w = Rotation.from_matrix(rotation).as_quat()
    record["heading"]["attitude"]["quaternion"] = {"x": x, "y": y, "z": z, "w": w}
    record["heading"]["headingAccuracyDeg"] = accuracy
    g = rotation.T @ np.array([0.0, 0.0, -1.0])
    record["motion"]["gravityDeviceG"] = {"x": g[0], "y": g[1], "z": g[2]}
    return record


def _solve_with(client: TestClient, record: dict[str, Any] | None, *, focal_35mm: bool = True, job_out: list | None = None) -> dict[str, Any]:
    body = _companion_png(focal_35mm=focal_35mm)
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert resp.status_code == 201, resp.text
    sha = hashlib.sha256(body).hexdigest()
    if record is not None:
        record = {**record, "sourceAssetSha256": sha}
        put = client.put(f"/api/ingest/{sha}/pointing", content=json.dumps(record), headers={"content-type": "application/json"})
        assert put.status_code == 201, put.text
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job
    if job_out is not None:
        job_out.append(job)
    return client.get(f"/api/ingest/{sha}/solution").json()


def test_a_record_aimed_at_the_sky_in_the_photo_narrows_the_search_and_states_its_offset(client: TestClient) -> None:
    spec = _frame().spec
    record = _record("0" * 63 + "1", spec.alt_deg, spec.az_deg)
    solution = _solve_with(client, record)
    assert solution["status"] == "calibrated", solution["warnings"]
    hint = solution["pointingHint"]
    expected = record_hint(PointingRecord.model_validate(record))
    assert expected is not None
    assert hint["usedAs"] == ["search_hint", "moment"] and (hint["timeFrom"], hint["placeFrom"]) == ("file", "record")
    assert hint["centerAltDeg"] == pytest.approx(expected.center_alt_deg, abs=1e-9)
    assert hint["centerAzDeg"] == pytest.approx(expected.center_az_deg, abs=1e-9)
    assert hint["uncertaintyDeg"] == pytest.approx(expected.uncertainty_deg, abs=1e-9)
    assert 0.0 <= hint["verifiedCenterOffsetDeg"] <= hint["uncertaintyDeg"]
    assert ingest_jobs.HINT_OUTSIDE not in solution["warnings"]


def test_a_record_aimed_far_from_the_photo_keeps_the_solve_the_stars_give_with_a_warning(client: TestClient, tmp_path: Path) -> None:
    # REQ-IOS-2.2: the hinted searches find nothing, the chain runs once more without the hint inside the same bound,
    # and the verified WCS wins over the hint, its disagreement recorded (pointing-4 v2, AUDIT's read of 12:58)
    spec = _frame().spec
    solution = _solve_with(client, _record("0" * 63 + "1", spec.alt_deg, (spec.az_deg + 120.0) % 360.0, accuracy=3.0))
    # The same photo beside a record with no heading, in a store of its own: the solve no hint narrowed
    other = IngestStore(tmp_path / "unhinted", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: other
    unhinted = _solve_with(TestClient(app), _record("0" * 63 + "1", 0.0, 0.0, heading=False))
    assert unhinted["status"] == "calibrated", unhinted["warnings"]
    assert solution["status"] == "calibrated", solution["warnings"]
    assert solution["wcs"]["crvalRaDeg"] == pytest.approx(unhinted["wcs"]["crvalRaDeg"], abs=1e-6)
    assert solution["wcs"]["crvalDecDeg"] == pytest.approx(unhinted["wcs"]["crvalDecDeg"], abs=1e-6)
    matched = lambda s: sorted(f["id"] for f in s["findings"] if f["evidenceClass"] == "catalog_matched")  # noqa: E731
    assert matched(solution) == matched(unhinted) and matched(solution)
    hint = solution["pointingHint"]
    assert hint["usedAs"] == ["search_hint", "moment"]
    assert hint["verifiedCenterOffsetDeg"] > hint["uncertaintyDeg"]
    assert ingest_jobs.HINT_OUTSIDE in solution["warnings"]


def test_the_chain_runs_unhinted_once_more_only_when_the_hinted_searches_do_not_calibrate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float | None] = []

    def once(statuses: list[str]):
        def chain(*args: Any, **kwargs: Any) -> Any:
            calls.append(kwargs.get("hint_ra_deg"))
            return type("S", (), {"status": statuses[len(calls) - 1]})()
        return chain

    hinted = dict(hint_ra_deg=10.0, hint_dec_deg=20.0, hint_radius_deg=5.0, triangle_radius_deg=40.0)
    for statuses, expected_calls, expected_status in (
        (["calibrated"], [10.0], "calibrated"),
        (["unresolved", "calibrated"], [10.0, None], "calibrated"),
        (["ambiguous", "unresolved"], [10.0, None], "ambiguous"),
    ):
        calls.clear()
        monkeypatch.setattr(ingest_jobs, "_solve_chain_once", once(statuses))
        result = ingest_jobs._solve_chain(None, [], 10, 10, on_event=lambda *a: None, **hinted)
        assert calls == expected_calls and result.status == expected_status
    calls.clear()
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", once(["unresolved"]))
    assert ingest_jobs._solve_chain(None, [], 10, 10, on_event=lambda *a: None).status == "unresolved"
    assert calls == [None]


def test_a_record_with_no_heading_lends_only_its_moment(client: TestClient) -> None:
    solution = _solve_with(client, _record("0" * 63 + "1", 0.0, 0.0, heading=False))
    assert solution["pointingHint"]["usedAs"] == ["moment"]
    assert not {"centerAltDeg", "centerAzDeg", "uncertaintyDeg", "verifiedCenterOffsetDeg"} & set(solution["pointingHint"])


def test_the_verified_calibration_wins_over_a_hint_outside_its_uncertainty_with_a_warning() -> None:
    spec = _frame().spec
    solution = _solve()
    assert solution.status == "calibrated"
    ra, dec = _centre(solution)
    for off_deg, outside in ((0.0, False), (30.0, True)):
        # The shift is in declination, toward the equator, so it is that far on the sky
        shifted = dec - off_deg if dec > 0.0 else dec + off_deg
        search = ingest_jobs.SearchHint(spec.alt_deg, spec.az_deg, ra, shifted, 10.0, None)
        hint, warning = ingest_jobs.pointing_hint_for(solution, None, search, "c" * 63 + "1", spec.width, spec.height)
        assert hint is not None and hint.used_as == ("search_hint",)
        assert (warning == ingest_jobs.HINT_OUTSIDE) == outside
        assert (hint.verified_center_offset_deg > 10.0) == outside


# ---- P1 and P2 (design: REQ-IOS-2.1, 2.3) over hints near and far ----

from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

_UNHINTED: Any = None


def _unhinted() -> Any:
    global _UNHINTED
    if _UNHINTED is None:
        _UNHINTED = _solve()
    return _UNHINTED


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(off_deg=st.floats(0.0, 120.0), bearing_deg=st.floats(0.0, 360.0), radius_deg=st.floats(1.0, 40.0))
def test_pbt_p1_p2_a_hint_makes_no_match_the_gate_did_not_verify_and_no_false_solve(
    off_deg: float, bearing_deg: float, radius_deg: float
) -> None:
    spec = _frame().spec
    # A hinted centre `off_deg` from the true pointing, toward `bearing_deg` on the sky
    centre = _sky_vector(spec.alt_deg, spec.az_deg)
    side = np.cross(centre, [0.0, 0.0, 1.0])
    side /= np.linalg.norm(side)
    up = np.cross(side, centre)
    b, o = math.radians(bearing_deg), math.radians(off_deg)
    aim = math.cos(o) * centre + math.sin(o) * (math.cos(b) * side + math.sin(b) * up)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, aim[2]))))
    az = math.degrees(math.atan2(-aim[1], aim[0])) % 360.0
    hinted = _solve(**_hint(alt, az, radius_deg))
    # P1 and P2: a hint makes no finding catalog_matched that the unchanged held-out gate did not verify
    if hinted.status == "calibrated":
        assert hinted.quality is not None and hinted.quality.passed_independent_verification
        # P1 as amended (the lead's answer of 09:58): a wrong hint never yields a false solve, so a hinted calibrated
        # solve's verified centre is the one the frame verifies with no hint, within 0.1 deg. P1's first wording
        # (every finding's class equal to the unhinted solve's) failed when a hint dropped the unhinted best rotation
        # and kept a neighbour, which named 38 stars of the unhinted 42 (off 1 deg, radius 1 deg) [tested]
        unhinted = _unhinted()
        assert unhinted.status == "calibrated"
        assert angular_separation_deg(*_centre(hinted), *_centre(unhinted)) <= 0.1
    else:
        assert not [f for f in hinted.findings if f.evidence_class == "catalog_matched"]


@pytest.mark.parametrize("with_record", [True, False])
def test_with_no_35mm_tag_the_records_field_of_view_is_the_first_search(client: TestClient, with_record: bool) -> None:
    # The record's 69.5 deg along a 4032 x 3024 format gives an 81.9 deg diagonal (the frame's is 84.1 deg)
    spec = _frame().spec
    jobs: list = []
    record = _record("0" * 63 + "1", spec.alt_deg, spec.az_deg) if with_record else None
    solution = _solve_with(client, record, focal_35mm=False, job_out=jobs)
    solve = {stage["stage"]: stage for stage in jobs[0]["stages"]}["solve"]
    assert solve["pass"]["label"] == ("phone_photo_hinted" if with_record else "phone_photo_generic")
    assert solution["status"] == "calibrated", solution["warnings"]


def test_the_plate_solvers_cut_is_the_hints_uncertainty_and_the_frames_half_diagonal() -> None:
    hint = ingest_jobs.SearchHint(40.0, 100.0, 10.0, 20.0, 12.5, 80.0)
    assert ingest_jobs.triangle_hint_radius_deg(hint) == pytest.approx(52.5)
    assert ingest_jobs.triangle_hint_radius_deg(ingest_jobs.SearchHint(40.0, 100.0, 10.0, 20.0, 12.5, None)) is None
    assert ingest_jobs.triangle_hint_radius_deg(ingest_jobs.SearchHint(40.0, 100.0, 10.0, 20.0, 170.0, 80.0)) == 180.0



@pytest.mark.parametrize("status", ["approximate", "ambiguous", "unresolved", "predicted_only"])
def test_only_a_calibrated_solution_states_the_offset_from_the_hint(status: str) -> None:
    # A solution of another status that still carries a WCS (an approximate pose's nominal one): no offset, no warning
    from types import SimpleNamespace

    spec = _frame().spec
    calibrated = _solve()
    ra, dec = _centre(calibrated)
    search = ingest_jobs.SearchHint(spec.alt_deg, spec.az_deg, ra, dec - 30.0 if dec > 0.0 else dec + 30.0, 10.0, None)
    other = SimpleNamespace(status=status, wcs=calibrated.wcs)
    hint, warning = ingest_jobs.pointing_hint_for(other, None, search, "c" * 63 + "1", spec.width, spec.height)  # type: ignore[arg-type]
    assert hint is not None and hint.verified_center_offset_deg is None and warning is None



def test_a_lent_moment_never_costs_the_blind_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lead's answer of 13:20: when the searches with a lent moment do not calibrate, the searches once more with no
    # time and no place (the whole-sky blind search), kept only when they calibrate
    calls: list[tuple[Any, Any]] = []
    Outcome = dataclasses.make_dataclass(
        "Outcome", ["status", ("observed_at_utc", Any, None), ("observer_lat", Any, None), ("observer_lon", Any, None)]
    )

    def once(statuses: list[str]):
        def chain(*args: Any, **kwargs: Any) -> Any:
            calls.append((kwargs.get("hint_ra_deg"), kwargs.get("obs_utc_iso")))
            return Outcome(statuses[len(calls) - 1])
        return chain

    moment = dict(obs_utc_iso="2030-01-01T00:00:00Z", lat_deg=10.0, lon_deg=20.0)
    hinted = dict(hint_ra_deg=10.0, hint_dec_deg=20.0, hint_radius_deg=5.0, triangle_radius_deg=40.0)
    for kwargs, statuses, expected_calls, expected_status in (
        ({**moment, **hinted, "moment_lent": True}, ["unresolved", "unresolved", "calibrated"],
         [(10.0, moment["obs_utc_iso"]), (None, moment["obs_utc_iso"]), (None, None)], "calibrated"),
        ({**moment, "moment_lent": True}, ["unresolved", "calibrated"], [(None, moment["obs_utc_iso"]), (None, None)], "calibrated"),
        ({**moment, "moment_lent": True}, ["ambiguous", "unresolved"], [(None, moment["obs_utc_iso"]), (None, None)], "ambiguous"),
        ({**moment, "moment_lent": True}, ["calibrated"], [(None, moment["obs_utc_iso"])], "calibrated"),
        ({**moment, "moment_lent": False}, ["unresolved"], [(None, moment["obs_utc_iso"])], "unresolved"),
    ):
        calls.clear()
        monkeypatch.setattr(ingest_jobs, "_solve_chain_once", once(statuses))
        result = ingest_jobs._solve_chain(None, [], 10, 10, on_event=lambda *a: None, **kwargs)
        assert calls == expected_calls and result.status == expected_status
        if expected_calls[-1] == (None, None) and expected_status == "calibrated":
            # The blind solution states the solve's own (lent) moment
            assert (result.observed_at_utc, result.observer_lat, result.observer_lon) == (
                moment["obs_utc_iso"], moment["lat_deg"], moment["lon_deg"]
            )


def _timeless_png() -> bytes:
    """The frame as the companion app sends it, with no time in the file: the camera's EXIF and 35 mm focal length."""
    frame = _frame()
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Apple", "iPhone 18 Pro, 24 mm f/1.78"
    exif.get_ifd(0x8769)[0xA405] = 24
    buf = io.BytesIO()
    Image.fromarray(frame.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


def test_a_photo_whose_lent_moment_is_wrong_still_gets_the_solve_its_stars_give(client: TestClient) -> None:
    # The record lends a time seven hours off and no heading: the horizon search at that moment finds nothing, and the
    # blind search over the whole sky, which the photo alone would get, calibrates it
    spec = _frame().spec
    body = _timeless_png()
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert resp.status_code == 201, resp.text
    sha = hashlib.sha256(body).hexdigest()
    record = _record(sha, 0.0, 0.0, heading=False)
    wrong = datetime.fromisoformat(spec.utc.replace("Z", "+00:00")) + timedelta(hours=7)
    record["capturedAtDeviceClockUTC"] = wrong.strftime("%Y-%m-%dT%H:%M:%SZ")
    put = client.put(f"/api/ingest/{sha}/pointing", content=json.dumps(record), headers={"content-type": "application/json"})
    assert put.status_code == 201, put.text
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", (job.get("error"), job.get("stages"))
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["status"] == "calibrated", solution["warnings"]
    assert [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    hint = solution["pointingHint"]
    assert hint["usedAs"] == ["moment"] and (hint["timeFrom"], hint["placeFrom"]) == ("record", "record")
