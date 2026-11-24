"""A photo's own time and place never cost it the solve its stars give alone (own-moment-fallback-1; the lead's answer
of 2026-11-24 13:25, after the lent-moment ruling of 2026-11-19 13:20).

With a time and a place the phone solver searches from the horizon at that moment only. When nothing calibrates there,
the chain runs the phone solver once more over the whole sky (`whole_sky`): the blind triangle search the photo gets
with no moment, with no horizon cut on the stars it is verified against, at the photo's own epoch; the held-out gate is
the same. Its solution is kept only when it calibrates, and says that the stars do not confirm the moment.

Frames are the benchmark suite's synthetic phone photos, with their known pointing [simulated].
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
import hashlib
import io
import time
from pathlib import Path
from typing import Any, Iterator

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.api import app
from services.ingest import jobs as ingest_jobs
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science import phone_photo_solver as pps
from services.science.solver import angular_separation_deg

_FRAME: Any = None
MOMENT = dict(obs_utc_iso="2030-01-01T00:00:00Z", lat_deg=10.0, lon_deg=20.0)


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def _seven_hours_off() -> str:
    spec = _frame().spec
    return (datetime.fromisoformat(spec.utc.replace("Z", "+00:00")) + timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chain_recorder(statuses: list[str], calls: list[tuple[Any, ...]]):
    outcome = dataclasses.make_dataclass(
        "Outcome", ["status", ("observed_at_utc", Any, None), ("observer_lat", Any, None), ("observer_lon", Any, None),
                    ("warnings", Any, ())]
    )

    def once(*args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("hint_ra_deg"), kwargs.get("obs_utc_iso"), kwargs.get("lat_deg"),
                      bool(kwargs.get("whole_sky", False))))
        return outcome(statuses[len(calls) - 1], kwargs.get("obs_utc_iso"), kwargs.get("lat_deg"), kwargs.get("lon_deg"))

    return once


def test_a_photo_s_own_moment_never_costs_the_whole_sky_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    obs, lat = MOMENT["obs_utc_iso"], MOMENT["lat_deg"]
    hinted = dict(hint_ra_deg=10.0, hint_dec_deg=20.0, hint_radius_deg=5.0, triangle_radius_deg=40.0)
    no_place = {**MOMENT, "lat_deg": None, "lon_deg": None}
    no_time = {**MOMENT, "obs_utc_iso": None}
    for kwargs, statuses, expected_calls, expected_status, own_words in (
        # The horizon search at the photo's own moment finds nothing: the whole sky, at the same moment
        (MOMENT, ["unresolved", "calibrated"], [(None, obs, lat, False), (None, obs, lat, True)], "calibrated", True),
        # With a record's heading: hinted, then unhinted, then the whole sky
        ({**MOMENT, **hinted}, ["unresolved", "unresolved", "calibrated"],
         [(10.0, obs, lat, False), (None, obs, lat, False), (None, obs, lat, True)], "calibrated", True),
        # The whole sky is kept only when it calibrates; the first answer stands otherwise
        (MOMENT, ["ambiguous", "unresolved"], [(None, obs, lat, False), (None, obs, lat, True)], "ambiguous", False),
        (MOMENT, ["calibrated"], [(None, obs, lat, False)], "calibrated", False),
        # With a time and no place, or a place and no time, the phone solver already searched the whole sky
        (no_place, ["unresolved"], [(None, obs, None, False)], "unresolved", False),
        (no_time, ["unresolved"], [(None, None, lat, False)], "unresolved", False),
        # A lent moment keeps its own rerun with no time and no place (the ruling of 2026-11-19 13:20)
        ({**MOMENT, "moment_lent": "both"}, ["unresolved", "calibrated"],
         [(None, obs, lat, False), (None, None, None, False)], "calibrated", False),
        # and a lent moment never gets the own moment's rerun after its own finds nothing
        ({**MOMENT, "moment_lent": "both"}, ["unresolved", "unresolved"],
         [(None, obs, lat, False), (None, None, None, False)], "unresolved", False),
        # No camera tag and under ten detections: only the plate solver ran, so no phone search has a rerun
        ({**MOMENT, "camera_meta": False}, ["unresolved"], [(None, obs, lat, False)], "unresolved", False),
    ):
        calls: list[tuple[Any, ...]] = []
        monkeypatch.setattr(ingest_jobs, "_solve_chain_once", _chain_recorder(statuses, calls))
        result = ingest_jobs._solve_chain(None, [], 10, 10, on_event=lambda *a: None, **{"camera_meta": True, **kwargs})
        assert calls == expected_calls and result.status == expected_status, (kwargs, calls)
        assert (ingest_jobs.BLIND_OWN_WORDS in result.warnings) is own_words
        if own_words:
            # The whole-sky solve ran at the photo's own moment, which its solution states
            assert (result.observed_at_utc, result.observer_lat, result.observer_lon) == (
                MOMENT["obs_utc_iso"], MOMENT["lat_deg"], MOMENT["lon_deg"]
            )
            assert list(result.warnings) == [ingest_jobs.BLIND_OWN_WORDS]


@pytest.mark.parametrize(("camera_meta", "fov_hint", "phone_calls"), [(True, 60.0, 2), (True, None, 1), (False, None, 1)])
def test_the_whole_sky_rerun_runs_only_the_phone_solver_over_the_whole_sky(
    monkeypatch: pytest.MonkeyPatch, camera_meta: bool, fov_hint: float | None, phone_calls: int
) -> None:
    # The plate solver searches the whole sky in the first run already, so the rerun asks only the phone solver
    import types

    told: list[dict[str, Any]] = []

    def plate(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the whole-sky rerun runs no plate solve")

    def phone(*args: Any, **kwargs: Any) -> Any:
        told.append(kwargs)
        return types.SimpleNamespace(status="unresolved", warnings=[])

    monkeypatch.setattr(ingest_jobs, "solve_and_verify_field", plate)
    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", phone)
    ingest_jobs._solve_chain_once(
        np.zeros((10, 10)), [object()] * 12, 10, 10, camera_meta=camera_meta, camera_label=None, fov_hint=fov_hint,
        obs_utc_iso=MOMENT["obs_utc_iso"], lat_deg=MOMENT["lat_deg"], lon_deg=MOMENT["lon_deg"],
        source_asset_id="src_" + "c" * 16, sha256="c" * 63 + "1", solution_id="sol_whole", on_event=lambda *e: None,
        whole_sky=True,
    )
    assert len(told) == phone_calls
    assert all(t["whole_sky"] is True and t["obs_utc_iso"] == MOMENT["obs_utc_iso"] for t in told)
    assert all(t.get("hint_ra_deg") is None for t in told)


def test_the_whole_sky_search_lines_up_a_photo_whose_own_moment_is_wrong() -> None:
    frame = _frame()
    spec = frame.spec
    common = dict(
        obs_utc_iso=_seven_hours_off(), observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, camera_label=None,
        fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1",
        solution_id="sol_whole",
    )
    from_horizon = pps.solve_phone_photo(frame.rgb, **common)
    assert from_horizon.status != "calibrated"
    counts: list[tuple[int, int]] = []
    whole = pps.solve_phone_photo(frame.rgb, whole_sky=True, on_candidate=lambda d, t: counts.append((d, t)), **common)
    # The job's count is the whole-sky search's own (it stops at the candidate that verifies)
    assert counts and all(0 <= done <= total and total > 0 for done, total in counts)
    alone = pps.solve_phone_photo(
        frame.rgb, **{**common, "obs_utc_iso": None, "observer_lat": None, "observer_lon": None}
    )
    assert whole.status == "calibrated" and alone.status == "calibrated"
    centre = whole.wcs.pixel_to_world(spec.width / 2.0, spec.height / 2.0)
    centre_alone = alone.wcs.pixel_to_world(spec.width / 2.0, spec.height / 2.0)
    assert angular_separation_deg(*centre, *centre_alone) < 0.05
    # The solution keeps the photo's own moment, and no matched star says the photo had none
    assert (whole.observed_at_utc, whole.observer_lat, whole.observer_lon) == (
        common["obs_utc_iso"], spec.lat_deg, spec.lon_deg
    )
    matched = [f for f in whole.findings if f.evidence_class == "catalog_matched"]
    assert matched
    assert not any("no capture time" in line or "has no place" in line for f in matched for line in f.limitations)


def test_the_whole_sky_search_is_one_solve(monkeypatch: pytest.MonkeyPatch) -> None:
    # The second prior keeps rotations of the horizon search only: a whole-sky solve that finds nothing is not run
    # again (it would repeat the same blind search), while the horizon search tries its second prior
    frame = _frame()
    spec = frame.spec
    calls: list[bool] = []
    real = pps._solve_phone_photo_with

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(bool(kwargs.get("whole_sky")))
        return real(*args, **kwargs)

    monkeypatch.setattr(pps, "_solve_phone_photo_with", counted)
    common = dict(
        obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg, detections=[],
        source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1",
    )
    assert pps.solve_phone_photo(frame.rgb, whole_sky=True, **common).status != "calibrated"
    assert calls == [True]
    calls.clear()
    assert pps.solve_phone_photo(frame.rgb, **common).status != "calibrated"
    assert calls == [False, False]


@pytest.mark.parametrize("narrowing", [
    {"seed_axes_icrs": [(0.0, 0.0), (1.0, 0.0), (0.0, -1.0)]},
    {"hint_ra_deg": 10.0, "hint_dec_deg": 20.0, "hint_radius_deg": 5.0},
])
def test_whole_sky_with_a_centre_hint_or_a_pose_to_try_is_refused(narrowing: dict[str, Any]) -> None:
    frame = _frame()
    spec = frame.spec
    with pytest.raises(ValueError, match="whole sky"):
        pps.solve_phone_photo(
            frame.rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg,
            source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1", whole_sky=True, detections=[],
            **narrowing,
        )


def _dms(value: float) -> tuple[float, float, float]:
    whole = abs(value)
    degrees = int(whole)
    minutes = int((whole - degrees) * 60.0)
    seconds = round((whole - degrees - minutes / 60.0) * 3600.0, 2)
    return float(degrees), float(minutes), min(seconds, 59.99)


def _png_with_its_own_moment(utc: str) -> bytes:
    """The frame as a camera writes it: its make, model and 35 mm focal length, its own time and its own GPS place."""
    frame = _frame()
    spec = frame.spec
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = "Apple", "iPhone 18 Pro, 24 mm f/1.78"
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = utc[:19].replace("-", ":").replace("T", " "), "+00:00"
    sub[0xA405] = 24
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = ("N" if spec.lat_deg >= 0 else "S"), _dms(spec.lat_deg)
    gps[3], gps[4] = ("E" if spec.lon_deg >= 0 else "W"), _dms(spec.lon_deg)
    buf = io.BytesIO()
    Image.fromarray(frame.rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def test_a_photo_whose_own_moment_is_wrong_still_gets_the_solve_its_stars_give(client: TestClient) -> None:
    # The file's own clock is seven hours off: the horizon search at that moment finds nothing, and the whole-sky
    # search, which the photo with no moment would get, calibrates it
    body = _png_with_its_own_moment(_seven_hours_off())
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert resp.status_code == 201, resp.text
    asset = resp.json()["sourceAsset"]
    assert asset.get("capturedAtUTC") and asset.get("observerLocation")
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", (job.get("error"), job.get("stages"))
    solution = client.get(f"/api/ingest/{sha}/solution").json()
    assert solution["status"] == "calibrated", solution["warnings"]
    assert [f for f in solution["findings"] if f["evidenceClass"] == "catalog_matched"]
    assert ingest_jobs.BLIND_OWN_WORDS in solution["warnings"]
    assert not any(w in ingest_jobs.BLIND_LENT_WORDS.values() for w in solution["warnings"])


SECOND = "A search at that moment matched nothing, so it may be off; the stars were matched without it."


def test_the_whole_sky_solve_s_words() -> None:
    # Decided by the lead on UIUX's read (2026-11-24 18:10): C22's shape, the first sentence shown and the second kept
    # in the record, no value of the time or the place said; a clip's take the clip's noun
    assert ingest_jobs.BLIND_OWN_WORDS == (
        "The stars alone lined this photo up; what is predicted on it is drawn for the photo's recorded capture time "
        f"and place, which the stars do not confirm. {SECOND}"
    )
    assert ingest_jobs.BLIND_OWN_CLIP_WORDS == (
        "The stars alone lined this clip up; what is predicted on it is drawn for the clip's recorded capture time "
        f"and place, which the stars do not confirm. {SECOND}"
    )


def _field() -> np.ndarray:
    yy, xx = np.mgrid[0:120, 0:160]
    luma = np.full((120, 160), 6.0)
    for idx in range(12):
        cx, cy = 14.0 + (idx * 37) % 132, 12.0 + (idx * 23) % 96
        luma += 200.0 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.4**2))
    return np.clip(luma, 0, 255).astype(np.uint8)


def _clip(tmp_path: Path) -> bytes:
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is on every CI runner and on the laptop"
    clip = tmp_path / "own.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "160x120", "-r", "3", "-i", "-",
         "-frames:v", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=_field().tobytes() * 3, check=True, capture_output=True, timeout=60,
    )
    return clip.read_bytes()


def _photo() -> bytes:
    out = io.BytesIO()
    Image.fromarray(_field(), mode="L").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(("kind", "content_type", "words", "other"), [
    ("clip", "video/mp4", "BLIND_OWN_CLIP_WORDS", "BLIND_OWN_WORDS"),
    ("photo", "image/png", "BLIND_OWN_WORDS", "BLIND_OWN_CLIP_WORDS"),
])
def test_the_whole_sky_words_take_the_clip_s_noun_on_a_clip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, content_type: str, words: str,
    other: str,
) -> None:
    # The chain's solution as the whole-sky rerun ends it (its words appended), run in the caller's process so the
    # stand-in reaches it; the job names a clip as a clip
    real = ingest_jobs._solve_chain

    def whole_sky_said(*args: Any, **kwargs: Any) -> Any:
        solution = real(*args, **kwargs)
        return dataclasses.replace(solution, warnings=[*solution.warnings, ingest_jobs.BLIND_OWN_WORDS])

    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain", whole_sky_said)
    body = _clip(tmp_path) if kind == "clip" else _photo()
    sha = hashlib.sha256(body).hexdigest()
    assert client.post("/api/ingest", content=body, headers={"content-type": content_type}).status_code == 201
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job.get("error")
    warnings = client.get(f"/api/ingest/{sha}/solution").json()["warnings"]
    assert warnings.count(getattr(ingest_jobs, words)) == 1
    assert getattr(ingest_jobs, other) not in warnings
