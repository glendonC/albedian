"""An upload's solve job adds the sky predicted for the file's own recorded time and place (spec
predicted-sky-for-every-upload, REQ-SKY-1, REQ-SKY-2).

Covers, through the real upload path with synthetic files at the synthetic moment and open-ocean place the plate
solver fixtures use:
- A photo with a recorded time and place whose stars do not solve ends predicted_only: its detections stay
  pixel-only observed findings, the solver's quality, warnings and identity stay, the predicted objects are added,
  the moment is the capture time, one plain sentence says where the Sun was, and the stage list is unchanged
- A calibrated photo keeps its status, WCS, quality and every finding of the solver's own solution and gains only
  the predicted objects it did not match
- A search stopped at its time limit gets the predicted sky too, with the limit's warning kept
- No recorded time (none, an EXIF time without its UTC offset, or a clip's own written time), no place, or a moment
  outside the ephemeris range: the solver's solution and one plain reason, no predicted finding
- A failure inside the predicted sky keeps the solver's solution and adds one generic warning
- No warning or job record holds the capture time, the place or the file name
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from services.api import app
from services.contracts.ingest import SolveJob
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science.catalog import get_catalog_stars
from services.science.predicted_sky import NO_PLACE, NO_TIME, OUT_OF_RANGE, sky_phase
from services.science.solver import render_synthetic_starfield, triangle_solver_identity

FFMPEG = shutil.which("ffmpeg")
# The synthetic moment and open-ocean place of the plate-solver fixtures, as EXIF writes them; they point at nobody
EXIF_TIME, EXIF_OFFSET, MOMENT = "2030:01:01 02:00:00", "+00:00", "2030-01-01T02:00:00Z"
PLACE = {"latDeg": 30.0, "lonDeg": -40.0}
FILE_NAME = "synthetic-sky-upload"
# The forms real_case.py treats as a leaked capture value, and the file name
LEAKS = (MOMENT, MOMENT[:10], MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00", FILE_NAME)


@pytest.fixture
def store(tmp_path: Path) -> IngestStore:
    return IngestStore(tmp_path / "store", max_bytes=20_000_000)


@pytest.fixture
def client(store: IngestStore) -> Iterator[TestClient]:
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _few_stars(h: int = 120, w: int = 160, n: int = 5) -> np.ndarray:
    """Five point sources on a dark field: too few for the plate solver [simulated]."""
    yy, xx = np.mgrid[0:h, 0:w]
    luma = np.full((h, w), 6.0)
    for idx in range(n):
        cx, cy = 14.0 + (idx * 37) % (w - 28), 12.0 + (idx * 23) % (h - 24)
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return luma


def _png(luma: np.ndarray, *, time: str | None = EXIF_TIME, offset: str | None = EXIF_OFFSET, place: bool = True) -> bytes:
    exif = Image.Exif()
    if time is not None:
        sub = exif.get_ifd(0x8769)
        sub[0x9003] = time
        if offset is not None:
            sub[0x9011] = offset
    if place:
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (30.0, 0.0, 0.0), "W", (40.0, 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG", exif=exif.tobytes(), compress_level=1
    )
    return buf.getvalue()


def _upload(client: TestClient, body: bytes, content_type: str = "image/png") -> tuple[str, dict[str, Any]]:
    resp = client.post("/api/ingest", content=body, headers={"content-type": content_type, "x-albedian-filename": f"{FILE_NAME}.png"})
    assert resp.status_code == 201, resp.text
    return hashlib.sha256(body).hexdigest(), resp.json()["sourceAsset"]


def _solve(client: TestClient, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    job = client.post(f"/api/ingest/{sha}/solve").json()
    SolveJob.model_validate(job)
    assert job["state"] == "succeeded", job
    assert "error" not in job
    return job, client.get(f"/api/ingest/{sha}/solution").json()


def _by_class(solution: dict[str, Any], evidence_class: str) -> list[dict[str, Any]]:
    return [f for f in solution["findings"] if f["evidenceClass"] == evidence_class]


def _assert_no_leak(solution: dict[str, Any], job: dict[str, Any]) -> None:
    for warning in solution["warnings"]:
        for value in LEAKS:
            assert value not in warning, (value, warning)
    assert FILE_NAME not in json.dumps(job)


def test_a_photo_whose_stars_do_not_solve_shows_the_sky_for_its_time_and_place(client: TestClient) -> None:
    sha, asset = _upload(client, _png(_few_stars()))
    assert asset["capturedAtUTC"] == MOMENT and asset["observerLocation"] == PLACE
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve"]
    assert solution["status"] == "predicted_only"
    assert solution["observedAtUTC"] == MOMENT and solution["frameTimeSec"] == 0.0
    assert solution["observerLocation"] == PLACE
    assert "wcs" not in solution
    # The solver's own identity, quality and warning stay
    assert solution["solver"]["name"] == triangle_solver_identity(get_catalog_stars()).name
    assert solution["quality"]["passedIndependentVerification"] is False
    observed = _by_class(solution, "observed")
    assert len(observed) == solution["quality"]["detectedCandidateCount"] == 5
    assert all("sourceDetection" in f and "skyPosition" not in f for f in observed)
    assert not _by_class(solution, "catalog_matched")
    predicted = _by_class(solution, "predicted")
    assert len(predicted) >= 20
    assert len(observed) + len(predicted) == len(solution["findings"])
    for finding in predicted:
        assert "sourceDetection" not in finding
        assert finding["skyPosition"]["altDeg"] > 0.0 and finding["skyPosition"]["uncertaintyArcsec"] > 0.0
        assert any("not measured in the recording" in text for text in finding["evidence"]["limitations"])
        assert finding["evidence"]["verifiedByIndependentHoldout"] is False
    # 02:00 UTC at 40 degrees west in midwinter: the Sun is down, and the last warning says where it was
    assert not any(f["id"] == "finding-predicted-sun" for f in predicted)
    assert len(solution["warnings"]) == 2
    assert "night" in solution["warnings"][-1] and "USNO" in solution["warnings"][-1]
    _assert_no_leak(solution, job)


def test_a_calibrated_photo_keeps_its_solution_and_gains_only_what_it_did_not_match(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    body = _png(frame)
    sha, _ = _upload(client, body)
    job, solution = _solve(client, sha)
    assert [stage["stage"] for stage in job["stages"]] == ["store", "decode", "detect", "solve", "verify"]

    # The solver's own solution for the same upload, from a second store with the predicted sky turned off
    solver_store = IngestStore(tmp_path / "solver-only", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: solver_store
    monkeypatch.setattr(ingest_jobs, "complete_sky", lambda solved, asset: solved)
    solver_sha, _ = _upload(client, body)
    _, solver_only = _solve(client, solver_sha)
    assert solver_only["status"] == "calibrated"

    assert solution["status"] == "calibrated"
    for key in ("wcs", "quality", "solver", "observedAtUTC", "observerLocation", "frameTimeSec"):
        assert solution[key] == solver_only[key], key
    kept = len(solver_only["findings"])
    assert solution["findings"][:kept] == solver_only["findings"]
    assert solution["warnings"][:-1] == solver_only["warnings"]
    added = solution["findings"][kept:]
    assert added and all(f["evidenceClass"] == "predicted" for f in added)
    matched = {f["catalogRef"]["id"] for f in _by_class(solver_only, "catalog_matched")}
    assert len(matched) >= 6
    assert not matched & {f["catalogRef"]["id"] for f in added if "catalogRef" in f}
    _assert_no_leak(solution, job)


def test_a_search_stopped_at_its_time_limit_still_shows_the_predicted_sky(client: TestClient, store: IngestStore) -> None:
    sha, _ = _upload(client, _png(_few_stars(240, 320, 40)))
    # A fresh child cannot start within 0.05 s, so the search stops at its bound before any candidate
    ingest_jobs._SOLVE_WORKER.close()
    job = ingest_jobs.run_solve_job(store, sha, solve_time_limit_s=0.05)
    assert job["state"] == "succeeded"
    assert {entry["stage"]: entry for entry in job["stages"]}["solve"] == {"stage": "solve", "unit": "steps", "done": 0, "total": 1}
    solution = ingest_jobs.read_solution(store, sha)
    assert solution["status"] == "predicted_only"
    assert "0.05 s limit" in solution["warnings"][0]
    assert solution["solver"]["name"] == triangle_solver_identity(get_catalog_stars()).name
    assert _by_class(solution, "predicted") and solution["observedAtUTC"] == MOMENT
    assert all("skyPosition" not in f for f in _by_class(solution, "observed"))
    _assert_no_leak(solution, job)


@pytest.mark.parametrize(
    ("body_args", "reason", "has_moment"),
    [
        ({"place": False}, NO_PLACE, True),
        ({"time": None}, NO_TIME, False),
        # An EXIF time without its UTC offset is not a capture time (REQ-SKY-2.2)
        ({"offset": None}, NO_TIME, False),
        ({"time": "2051:01:01 00:00:00"}, OUT_OF_RANGE, True),
    ],
)
def test_without_a_time_a_place_or_a_moment_in_range_the_solution_says_why(
    client: TestClient, body_args: dict[str, Any], reason: str, has_moment: bool
) -> None:
    sha, asset = _upload(client, _png(_few_stars(), **body_args))
    assert ("capturedAtUTC" in asset) == has_moment
    job, solution = _solve(client, sha)
    assert solution["status"] == "unresolved"
    assert not _by_class(solution, "predicted")
    assert ("observedAtUTC" in solution) == has_moment
    assert solution["warnings"][-1] == reason and len(solution["warnings"]) == 2
    _assert_no_leak(solution, job)


def _clip(folder: Path, *metadata: str) -> bytes:
    path = folder / "synthetic.mov"
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x48:d=1:r=25", "-c:v", "mpeg4",
               "-movflags", "use_metadata_tags"]
    for item in metadata:
        command += ["-metadata", item]
    subprocess.run(command + [str(path)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return path.read_bytes()


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize(
    ("time_tag", "status"),
    [
        ("com.apple.quicktime.creationdate=2030-01-01T02:00:00+0000", "predicted_only"),
        # QuickTime's creation_time may be when the file was written, so it is no capture time (REQ-SKY-2.2)
        ("creation_time=2030-01-01T02:00:00Z", "unresolved"),
    ],
)
def test_a_clip_gets_the_sky_only_for_its_recorded_time(client: TestClient, tmp_path: Path, time_tag: str, status: str) -> None:
    body = _clip(tmp_path, time_tag, "com.apple.quicktime.location.ISO6709=+30.0000-040.0000/")
    sha, asset = _upload(client, body, "video/quicktime")
    assert asset["observerLocation"] == PLACE
    job, solution = _solve(client, sha)
    assert solution["status"] == status
    if status == "predicted_only":
        assert solution["observedAtUTC"] == MOMENT and _by_class(solution, "predicted")
    else:
        assert "capturedAtUTC" not in asset and "observedAtUTC" not in solution
        assert not _by_class(solution, "predicted") and solution["warnings"][-1] == NO_TIME
    _assert_no_leak(solution, job)


def test_a_failure_inside_the_predicted_sky_keeps_the_solvers_solution(
    client: TestClient, store: IngestStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, _ = _upload(client, _png(_few_stars()))

    def fails(solution: Any, asset: dict[str, Any]) -> Any:
        raise RuntimeError(f"could not place the sky for {asset['capturedAtUTC']} at {asset['observerLocation']}")

    monkeypatch.setattr(ingest_jobs, "complete_sky", fails)
    job, solution = _solve(client, sha)
    assert solution["status"] == "unresolved"
    assert not _by_class(solution, "predicted")
    assert solution["warnings"][-1] == ingest_jobs.PREDICTED_SKY_FAILED == "The predicted sky could not be computed."
    assert len(solution["warnings"]) == 2
    assert "could not place" not in json.dumps(solution) and "could not place" not in json.dumps(job)
    _assert_no_leak(solution, job)


def test_the_sky_phase_sentence_is_the_one_for_the_suns_altitude(client: TestClient) -> None:
    sha, _ = _upload(client, _png(_few_stars()))
    _, solution = _solve(client, sha)
    from services.science.ephemeris import compute_solar_system_ephemerides

    (sun,) = compute_solar_system_ephemerides(MOMENT, 30.0, -40.0, bodies=("Sun",))
    assert solution["warnings"][-1] == sky_phase(sun.alt_deg).sentence
