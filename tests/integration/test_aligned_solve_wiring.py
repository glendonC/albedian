"""A solve from a person's alignment, wired through the job (align-2w; D24's Solve from here; the order measured in
.lead/kiro/reports/SOLVER/align-2-measure.md: the person's pose first, then the hint at the person's field of view,
then a fine sweep of the field around it).

Covers, with the benchmark suite's first synthetic phone photo and its known camera [simulated]:
- The person's pose, read in the drawn sky's convention (airless altitude, a pose estimate's roll), is the camera the
  renderer used, built independently (its observed altitude and its own roll convention), within 0.3 degree
- The phone solver tried from a pose (`seed_axes_icrs`) calibrates where the camera was; from a pose far off the
  gate refuses it and nothing is matched; a field-of-view sweep (`fov_candidates_deg`) is searched as given; bad
  inputs are refused
- The job's chain for an aligned solve: the pose, then the hint at the person's field, then the sweep, stopping at the
  first calibrated, each search holding the person's hint and never one without it
- Through the route: an aligned solve's solution states the person's pointing hint (source person, a search hint
  only, the roll and field of view, the verified offset when calibrated); a wrong alignment matches nothing
- The solve kind keys an aligned solve by its alignment
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import time
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import ExifTags, Image
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.api import app
from services.contracts.alignment import PERSON_ALIGNMENT_UNCERTAINTY_DEG, PersonAlignment
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest import runner as job_runner
from services.ingest.store import IngestStore
from services.science import phone_photo_solver as pps
from services.science.catalog import airless_altitude_deg
from services.science.person_hint import camera_rotation, person_axes_icrs
from services.science.solver import angular_separation_deg
from tests.integration.test_capture_time_from_gps import position

_FRAME: Any = None


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def _alignment(*, az_off: float = 0.0, alt_off: float = 0.0, roll_off: float = 0.0, fovx: float = 1.0) -> PersonAlignment:
    spec = _frame().spec
    return PersonAlignment.model_validate({
        "format": "albedian.person-alignment", "formatVersion": 1, "source": "person",
        "centerAltDeg": airless_altitude_deg(spec.alt_deg) + alt_off, "centerAzDeg": (spec.az_deg + az_off) % 360.0,
        "rollDeg": spec.roll_deg + roll_off, "fovDiagonalDeg": spec.fov_deg * fovx,
    })


def _truth_axes(step_px: float = 20.0) -> list[tuple[float, float]]:
    spec = _frame().spec
    truth = phone_render.camera_truth_for(spec)
    cx, cy = (spec.width - 1) / 2.0, (spec.height - 1) / 2.0
    ra, dec = truth.pixel_to_icrs(np.array([cx, cx + step_px, cx]), np.array([cy, cy, cy + step_px]))
    return [(float(r), float(d)) for r, d in zip(ra, dec)]


def _sky_frame() -> Any:
    spec = _frame().spec
    return pps._ApparentSkyFrame.from_iso(spec.utc, spec.lat_deg, spec.lon_deg)


def _turned_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(a @ b.T)) - 1.0) / 2.0))))


def _solve(**extra: Any) -> Any:
    frame = _frame()
    spec = frame.spec
    return pps.solve_phone_photo(
        frame.rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1", solution_id="sol_align", **extra,
    )


def _truth_centre() -> tuple[float, float]:
    return _truth_axes()[0]


def test_the_persons_pose_in_the_drawn_skys_convention_is_the_renderers_camera() -> None:
    frame = _sky_frame()
    spec = _frame().spec
    person = camera_rotation(person_axes_icrs(_alignment(), spec.utc, spec.lat_deg, spec.lon_deg), frame)
    truth = camera_rotation(_truth_axes(), frame)
    assert person is not None and truth is not None
    assert _turned_deg(person, truth) < 0.3


def test_the_solver_tried_from_the_persons_pose_calibrates_where_the_camera_was() -> None:
    spec = _frame().spec
    solution = _solve(fov_diagonal_deg=spec.fov_deg, seed_axes_icrs=_truth_axes())
    assert solution.status == "calibrated", solution.warnings
    ra, dec = solution.wcs.pixel_to_world(spec.width / 2.0, spec.height / 2.0)
    assert angular_separation_deg(ra, dec, *_truth_centre()) < 0.2


def test_a_pose_far_off_is_refused_by_the_gate_and_nothing_is_matched() -> None:
    spec = _frame().spec
    far = person_axes_icrs(_alignment(az_off=40.0), spec.utc, spec.lat_deg, spec.lon_deg)
    solution = _solve(fov_diagonal_deg=spec.fov_deg, seed_axes_icrs=far)
    assert solution.status != "calibrated"


def test_a_field_of_view_sweep_is_searched_as_given(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _frame().spec
    seen: list[list[tuple[float, str]]] = []
    real = pps._search_horizon_attitude

    def spy(pool, width, height, sky_frame, enu, ra, dec, fov_models, *rest, **kw):
        seen.append(list(fov_models))
        return real(pool, width, height, sky_frame, enu, ra, dec, fov_models, *rest, **kw)

    monkeypatch.setattr(pps, "_search_horizon_attitude", spy)
    rungs = [spec.fov_deg * k for k in (1.06, 1.03, 1.0)]
    solution = _solve(fov_candidates_deg=rungs)
    assert seen and all(models == [(f, "brown-conrady") for f in rungs] for models in seen)
    assert solution.status == "calibrated", solution.warnings


@pytest.mark.parametrize("extra", [
    {"seed_axes_icrs": [(10.0, 20.0), (10.1, 20.0)]},
    {"fov_candidates_deg": []},
    {"fov_candidates_deg": [0.0]},
    {"fov_candidates_deg": [190.0]},
])
def test_a_pose_or_a_sweep_the_solver_cannot_take_is_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _solve(fov_diagonal_deg=60.0, **extra)


def test_the_chain_of_an_aligned_solve_runs_pose_hint_then_sweep_and_stops_at_the_first_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    outcomes = iter(["unresolved", "unresolved", "calibrated"])

    class Fake:
        def __init__(self, status: str) -> None:
            self.status = status
            self.warnings: list[str] = []

    def fake_phone(image: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return Fake(next(outcomes))

    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", fake_phone)
    monkeypatch.setattr(ingest_jobs, "noise_limit_warnings", lambda measures: [])
    axes = [(10.0, 20.0), (10.5, 20.0), (10.0, 19.5)]
    events: list[tuple[str, ...]] = []
    solution = ingest_jobs._solve_chain(
        np.zeros((4, 4)), [], 4, 4, on_event=lambda *e: events.append(e), camera_meta=True, camera_label=None,
        fov_hint=62.0, obs_utc_iso="2031-03-20T09:30:00Z", lat_deg=20.0, lon_deg=-140.0, source_asset_id="src_" + "c" * 16,
        sha256="c" * 64, solution_id="sol_x", hint_ra_deg=10.0, hint_dec_deg=20.0, hint_radius_deg=10.0,
        triangle_radius_deg=41.0, person_axes=axes, person_fov_deg=60.0,
    )
    assert solution.status == "calibrated" and len(calls) == 3
    pose, hint, sweep = calls
    assert pose["seed_axes_icrs"] == axes and pose["fov_diagonal_deg"] == 60.0
    assert hint.get("seed_axes_icrs") is None and hint["fov_diagonal_deg"] == 60.0
    assert sweep["fov_candidates_deg"] == [60.0 * k for k in ingest_jobs.PERSON_FOV_SWEEP]
    for call in calls:
        assert (call["hint_ra_deg"], call["hint_dec_deg"], call["hint_radius_deg"]) == (10.0, 20.0, 10.0)
    assert [e for e in events if e[0] == "solver"] == [("solver", "phone_photo")] * 3


def test_the_solve_kind_keys_an_aligned_solve_by_its_alignment() -> None:
    assert ingest_jobs.SOLVE_KIND.subject is job_runner.solve_subject


# ---- Through the route and the job ----


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _png_with_time_and_place() -> bytes:
    frame = _frame()
    spec = frame.spec
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = spec.utc[:19].replace("-", ":").replace("T", " "), "+00:00"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    for name, value in position((spec.lat_deg, spec.lon_deg)).items():
        gps[getattr(ExifTags.GPS, name)] = value
    buf = io.BytesIO()
    Image.fromarray(frame.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


def _aligned_solution(client: TestClient, alignment: PersonAlignment) -> tuple[dict[str, Any], dict[str, Any]]:
    body = _png_with_time_and_place()
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve", content=json.dumps(alignment.to_wire_dict()),
                      headers={"content-type": "application/json"})
    assert job.status_code == 202, job.text
    record = job.json()
    deadline = time.monotonic() + 300.0
    while record.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        record = client.get(f"/api/ingest/jobs/{record['id']}").json()
    assert record["state"] == "succeeded", record
    return record, client.get(f"/api/ingest/{sha}/solution").json()


def test_an_aligned_solve_states_the_persons_hint_and_its_verified_offset(client: TestClient) -> None:
    alignment = _alignment(az_off=1.0, roll_off=1.0, fovx=1.02)
    record, solution = _aligned_solution(client, alignment)
    assert record["request"] == alignment.to_wire_dict()
    assert solution["status"] == "calibrated", solution.get("warnings")
    hint = solution["pointingHint"]
    assert hint["source"] == "person" and hint["usedAs"] == ["search_hint"]
    assert (hint["timeFrom"], hint["placeFrom"]) == ("file", "file")
    assert hint["rollDeg"] == pytest.approx(alignment.roll_deg) and hint["fovDiagonalDeg"] == pytest.approx(alignment.fov_diagonal_deg)
    assert hint["uncertaintyDeg"] == PERSON_ALIGNMENT_UNCERTAINTY_DEG
    assert 0.0 <= hint["verifiedCenterOffsetDeg"] < 3.0


def test_a_wrong_alignment_matches_nothing_and_keeps_the_hint_as_the_persons(client: TestClient) -> None:
    _, solution = _aligned_solution(client, _alignment(az_off=60.0))
    assert solution["status"] != "calibrated"
    hint = solution["pointingHint"]
    assert hint["source"] == "person" and "verifiedCenterOffsetDeg" not in hint


def test_an_aligned_solve_publishes_no_early_sky_and_a_plain_one_tries_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lock-on's early sky (platform-10c) would replace on screen the sky the person has just aligned with a pose's
    # guess, so an aligned solve never tries it (PLATFORM's read of the wiring); a plain solve still does
    tries: list[str] = []
    real = ingest_jobs._publish_early_sky

    def spy(publish: Any, asset: Any, sha256: str, *rest: Any) -> None:
        tries.append(sha256)
        return real(publish, asset, sha256, *rest)

    monkeypatch.setattr(ingest_jobs, "_publish_early_sky", spy)
    record, _ = _aligned_solution(client, _alignment())
    assert tries == []
    plain = client.post(f"/api/ingest/{record['sourceAssetSha256']}/solve")
    assert plain.status_code == 202, plain.text
    job = plain.json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded" and tries == [record["sourceAssetSha256"]]


def test_a_match_from_the_persons_pose_ends_the_chain_there(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class Fake:
        status = "calibrated"
        warnings: list[str] = []

    def fake_phone(image: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return Fake()

    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", fake_phone)
    ingest_jobs._solve_chain(
        np.zeros((4, 4)), [], 4, 4, on_event=lambda *e: None, camera_meta=True, camera_label=None, fov_hint=60.0,
        obs_utc_iso="2031-03-20T09:30:00Z", lat_deg=20.0, lon_deg=-140.0, source_asset_id="src_" + "c" * 16,
        sha256="c" * 64, solution_id="sol_x", hint_ra_deg=10.0, hint_dec_deg=20.0, hint_radius_deg=10.0,
        person_axes=[(10.0, 20.0), (10.5, 20.0), (10.0, 19.5)], person_fov_deg=60.0,
    )
    assert len(calls) == 1 and calls[0]["seed_axes_icrs"] is not None


@pytest.mark.parametrize("status", ["approximate", "ambiguous", "predicted_only"])
def test_only_a_calibrated_solution_states_its_offset_from_the_persons_centre(status: str) -> None:
    class Wcs:
        def pixel_to_world(self, x: float, y: float) -> tuple[float, float]:
            return 11.0, 21.0

    class Solution:
        def __init__(self, s: str) -> None:
            self.status, self.wcs = s, Wcs()

    search = ingest_jobs.SearchHint(40.0, 120.0, 10.0, 20.0, PERSON_ALIGNMENT_UNCERTAINTY_DEG, 60.0)
    alignment = _alignment()
    refused = ingest_jobs.person_pointing_hint(Solution(status), search, alignment, "c" * 64, 100, 80)
    assert refused.verified_center_offset_deg is None
    matched = ingest_jobs.person_pointing_hint(Solution("calibrated"), search, alignment, "c" * 64, 100, 80)
    assert matched.verified_center_offset_deg == pytest.approx(angular_separation_deg(11.0, 21.0, 10.0, 20.0))


def test_a_mirrored_pose_is_tried_as_nothing_and_never_searched_instead() -> None:
    spec = _frame().spec
    centre, right, down = _truth_axes()
    # The down step on the other side of the centre: a mirrored camera, which no camera of the solver's takes
    mirrored_down = (2 * centre[0] - down[0], 2 * centre[1] - down[1])
    solution = _solve(fov_diagonal_deg=spec.fov_deg, seed_axes_icrs=[centre, right, mirrored_down])
    assert solution.status != "calibrated"


def test_the_job_gives_the_chain_the_persons_field_and_pose(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    real = ingest_jobs._solve_chain

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain", spy)
    alignment = _alignment(fovx=1.02)
    _aligned_solution(client, alignment)
    (kwargs,) = seen
    assert kwargs["person_fov_deg"] == pytest.approx(alignment.fov_diagonal_deg)
    assert kwargs["fov_hint"] == pytest.approx(alignment.fov_diagonal_deg)
    assert kwargs["person_axes"] is not None and len(kwargs["person_axes"]) == 3
    assert kwargs["hint_radius_deg"] == PERSON_ALIGNMENT_UNCERTAINTY_DEG
