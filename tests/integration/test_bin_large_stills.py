"""A still above the line is searched binned and served in its stored pixels (bin-detect), through the routes.

The benchmark suite's synthetic phone photo [simulated] is stored twice: as it is, and with every pixel repeated
2 x 2. With the line drawn between the two (BIN_ABOVE_PX and BIN_MAX_PX lowered for the test), the large copy is
binned by 2, which gives back the photo itself exactly, so its solve is the photo's own, and what is served for it
must be that solve restated in the stored pixels: the same sky, every pixel figure twice the length and at the
centre of its block."""

from __future__ import annotations

import hashlib
import io
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from research.benchmarks.phone_photos import render as phone_render, stars as phone_stars, suite as phone_suite
from services.api import app
from services.ingest import router as ingest_router
from services.ingest.store import IngestStore
from services.science import binning
from services.science.binning import restate_pixels, stored_position

_FRAME: Any = None


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def _png(rgb: np.ndarray, *, camera: bool = True) -> bytes:
    """The frame with a camera's EXIF: its time, a 35 mm focal length and the place, as a phone writes them; with
    `camera` false, the time and the place only."""
    spec = _frame().spec
    exif = Image.Exif()
    if camera:
        exif[0x010F], exif[0x0110] = "Apple", "iPhone 18 Pro, 24 mm f/1.78"
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = spec.utc[:19].replace("-", ":").replace("T", " "), "+00:00"
    if camera:
        sub[0xA405] = 24
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = ("N" if spec.lat_deg >= 0 else "S"), (abs(float(spec.lat_deg)), 0.0, 0.0)
    gps[3], gps[4] = ("E" if spec.lon_deg >= 0 else "W"), (abs(float(spec.lon_deg)), 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG", exif=exif.tobytes(), compress_level=1)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=60_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _solve(client: TestClient, body: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    resp = client.post("/api/ingest", content=body, headers={"content-type": "image/png"})
    assert resp.status_code in (200, 201), resp.text
    asset = resp.json()["sourceAsset"]
    assert asset.get("capturedAtUTC") and asset.get("observerLocation"), "the test photo must carry its time and place"
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job
    return asset, client.get(f"/api/ingest/{sha}/solution").json()


def _measured(solution: dict[str, Any]) -> list[dict[str, Any]]:
    """The findings the chain measured (each with its detection), in order."""
    return [f for f in solution["findings"] if f.get("sourceDetection") is not None]


def test_a_still_above_the_line_is_solved_binned_and_its_calibration_handed_over_by_refine_2(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bin-detect-v2: the binned phone solve's calibration is handed to the stored pixels by refine-2's refit
    (`refine_binned_phone_solution`) right where that search ends, given the stored frame, the binned frame and the
    phone solver's own detections on it, and what is served is that refit: calibrated in the stored pixels under the
    same 3 px gate, the same sky as the photo's own. The chain runs here so the refit can be watched."""
    from services.ingest import jobs
    from services.science import detector

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    large = np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)
    handed: list[Any] = []
    real = jobs.refine_binned_phone_solution

    def handover(stored: Any, solution: Any, k: int, **kwargs: Any) -> Any:
        out = real(stored, solution, k, **kwargs)
        handed.append((stored, k, kwargs["binned_image"], kwargs["binned_detections"], out.to_contract_dict()))
        return out

    monkeypatch.setattr(jobs, "refine_binned_phone_solution", handover)
    _, small = _solve(client, _png(rgb))
    large_asset, served = _solve(client, _png(large))
    assert small["status"] == "calibrated", small["status"]
    assert (large_asset["width"], large_asset["height"]) == (2 * width, 2 * height)
    assert len(handed) == 1
    stored, k, binned_image, binned_detections, out = handed[0]
    # The stored frame as decoded, the binned frame (here the photo itself, exactly), and the phone solver's own
    # detections on it with the upload's own id
    assert np.array_equal(stored, large) and k == 2
    assert np.array_equal(binned_image, rgb)
    assert binned_detections == detector.detect_point_sources(
        detector._to_luma_2d(rgb) / 255.0, asset_id=large_asset["id"], frame_time_sec=0.0
    )
    assert binned_detections and all(d.asset_id == large_asset["id"] for d in binned_detections)
    # What is served is the refit, calibrated in the stored pixels within the gate, on the photo's own sky
    assert served["status"] == out["status"] == "calibrated"
    assert served["wcs"] == out["wcs"] and served["quality"] == out["quality"]
    assert 0.0 <= served["wcs"]["crpixX"] < 2 * width and 0.0 <= served["wcs"]["crpixY"] < 2 * height
    for key in ("fovHorizontalDeg", "fovVerticalDeg"):
        assert served["wcs"][key] == pytest.approx(small["wcs"][key], rel=0.01), key
    assert served["quality"]["passedIndependentVerification"] is True
    assert served["quality"]["medianResidualPx"] <= 3.0
    assert served["wcs"]["crvalRaDeg"] == pytest.approx(small["wcs"]["crvalRaDeg"], abs=0.05)
    assert served["wcs"]["crvalDecDeg"] == pytest.approx(small["wcs"]["crvalDecDeg"], abs=0.05)
    assert served["wcs"]["scaleDegPerPx"] == pytest.approx(small["wcs"]["scaleDegPerPx"] / 2, rel=0.01)
    assert all(0.0 <= f["sourceDetection"]["xPx"] < 2 * width and 0.0 <= f["sourceDetection"]["yPx"] < 2 * height
               for f in _measured(served))


def test_the_child_serves_what_the_chain_here_serves(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stored frame reaches the group's child with the binned one, and the solution served from there is the
    one the chain gives in this process."""
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    body = _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1))
    _, in_child = _solve(client, body)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    _, here = _solve(client, body)
    assert in_child["status"] == "calibrated"
    assert in_child == here


def test_a_handover_that_does_not_calibrate_lets_the_chain_go_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SOLVER's handover note: on `unresolved` the chain goes on as for any search that did not calibrate. With every
    refit refused, the next search runs after the first refusal, and nothing calibrated is served from the refused
    one."""
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    log: list[str] = []
    real_phone, real_plate = jobs.solve_phone_photo, jobs.solve_and_verify_field

    def refused(stored: Any, solution: Any, k: int, **kwargs: Any) -> Any:
        # The refit's own refusal, as its gate gives it: unresolved, no WCS, every finding observed
        from dataclasses import replace

        log.append("handover")
        loose = replace(solution, quality=replace(solution.quality, median_residual_px=2.0))
        out = binning.in_stored_pixels(loose, k, stored.shape[1], stored.shape[0])
        assert out.status == "unresolved" and out.wcs is None
        return out

    def phone(*args: Any, **kwargs: Any) -> Any:
        log.append("phone")
        return real_phone(*args, **kwargs)

    def plate(*args: Any, **kwargs: Any) -> Any:
        log.append("plate")
        return real_plate(*args, **kwargs)

    monkeypatch.setattr(jobs, "refine_binned_phone_solution", refused)
    monkeypatch.setattr(jobs, "solve_phone_photo", phone)
    monkeypatch.setattr(jobs, "solve_and_verify_field", plate)
    _, served = _solve(client, _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)))
    assert log[:2] == ["phone", "handover"], log
    assert len(log) > 2 and log[2] in ("phone", "plate"), log
    if served["status"] == "calibrated":
        # Only a later search's own calibration, never a refused refit's
        assert log[-1] != "handover", log
    assert all(0.0 <= f["sourceDetection"]["xPx"] < 2 * width and 0.0 <= f["sourceDetection"]["yPx"] < 2 * height
               for f in _measured(served))


def test_an_untagged_stills_phone_search_is_handed_over_after_its_plate_search(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still with no camera tag goes to the plate search first and to the phone search after it; the phone search's
    calibration is handed over by refine-2's refit like a tagged one's."""
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    log: list[str] = []
    real_plate, real_refit = jobs.solve_and_verify_field, jobs.refine_binned_phone_solution
    outs: list[dict[str, Any]] = []

    def plate(*args: Any, **kwargs: Any) -> Any:
        log.append("plate")
        return real_plate(*args, **kwargs)

    def refit(*args: Any, **kwargs: Any) -> Any:
        log.append("handover")
        out = real_refit(*args, **kwargs)
        outs.append(out.to_contract_dict())
        return out

    monkeypatch.setattr(jobs, "solve_and_verify_field", plate)
    monkeypatch.setattr(jobs, "refine_binned_phone_solution", refit)
    _, small = _solve(client, _png(rgb))
    log.clear()
    outs.clear()
    _, served = _solve(client, _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1), camera=False))
    assert log[0] == "plate" and log.count("handover") == 1, log
    assert served["status"] == outs[0]["status"] == "calibrated"
    assert served["wcs"] == outs[0]["wcs"]
    assert served["wcs"]["scaleDegPerPx"] == pytest.approx(small["wcs"]["scaleDegPerPx"] / 2, rel=0.01)


def _binned_positions(rgb: np.ndarray, asset_id: str) -> set[tuple[float, float]]:
    """Every detection's stored position, of the job's detection and of the phone solver's, on a frame that bins to
    `rgb` by 2."""
    from services.science import detector

    luma = detector._to_luma_2d(rgb.astype(np.float64))
    found = [*detector.detect_point_sources(luma, asset_id=asset_id),
             *detector.detect_point_sources(luma / 255.0, asset_id=asset_id, frame_time_sec=0.0)]
    return {(round(stored_position(d.x_px, 2), 6), round(stored_position(d.y_px, 2), 6)) for d in found}


def test_a_search_stopped_at_its_bound_serves_the_binned_detections_at_their_stored_positions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound's unresolved solution is made in the server from the job's detections on the binned still, so it is
    restated there: every finding at the centre of its block."""
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "SOLVE_TIME_LIMIT_SEC", 0.1)
    asset, served = _solve(client, _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)))
    assert served["status"] in ("unresolved", "predicted_only")
    assert "0.1 s limit" in " ".join(served["warnings"]), served["warnings"]
    measured = _measured(served)
    assert measured
    expected = _binned_positions(rgb, asset["id"])
    assert all((round(f["sourceDetection"]["xPx"], 6), round(f["sourceDetection"]["yPx"], 6)) in expected
               for f in measured)


class _Handover:
    """A stand-in for `_StoredPixels` that records which of its two ways each search's solution took, and hands back
    a solution of its own, with a status the test chose, for the chain to read."""

    def __init__(self, stored_status: dict[str, str]) -> None:
        self.seen: list[tuple[str, str]] = []
        self._stored_status = stored_status

    def _out(self, way: str, solution: Any) -> Any:
        from types import SimpleNamespace

        self.seen.append((way, solution.name))
        name = f"stored {solution.name}"
        return SimpleNamespace(name=name, status=self._stored_status.get(name, solution.status), warnings=[])

    def phone(self, solution: Any) -> Any:
        return self._out("phone", solution)

    def restated(self, solution: Any) -> Any:
        return self._out("restated", solution)


def _scripted(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, list[str]]) -> None:
    """Stand-in searches: each call returns the next of its kind's scripted statuses, named by kind and turn."""
    from types import SimpleNamespace

    from services.ingest import jobs

    turns = {"phone": 0, "plate": 0}

    def answer(kind: str) -> Any:
        turns[kind] += 1
        return SimpleNamespace(name=f"{kind} {turns[kind]}", status=statuses[kind][turns[kind] - 1], warnings=[])

    monkeypatch.setattr(jobs, "solve_phone_photo", lambda *a, **k: answer("phone"))
    monkeypatch.setattr(jobs, "solve_and_verify_field", lambda *a, **k: answer("plate"))


_PASS = dict(camera_label=None, obs_utc_iso=None, lat_deg=None, lon_deg=None, source_asset_id="src_x", sha256="0" * 64,
             solution_id="sol_x", on_event=lambda *a: None, phone_detections=lambda: [])


@pytest.mark.parametrize(
    ("camera", "fov_hint", "statuses", "stored_status", "seen", "kept"),
    [
        # A tagged still: the 35 mm search, the field search, then the plate search, each handed over as it ends
        (True, 50.0, {"phone": ["unresolved", "unresolved"], "plate": ["unresolved"]}, {},
         [("phone", "phone 1"), ("phone", "phone 2"), ("restated", "plate 1")], "stored phone 2"),
        # A binned calibration the handover refuses lets the pass go on, as from a search that did not calibrate
        (True, 50.0, {"phone": ["calibrated", "calibrated"], "plate": ["calibrated"]},
         {"stored phone 1": "unresolved", "stored phone 2": "unresolved"},
         [("phone", "phone 1"), ("phone", "phone 2"), ("restated", "plate 1")], "stored plate 1"),
        # An untagged still: the plate search, then the phone search
        (False, None, {"phone": ["unresolved"], "plate": ["unresolved"]}, {},
         [("restated", "plate 1"), ("phone", "phone 1")], "stored plate 1"),
        (False, None, {"phone": ["calibrated"], "plate": ["unresolved"]}, {},
         [("restated", "plate 1"), ("phone", "phone 1")], "stored phone 1"),
    ],
)
def test_every_search_of_a_binned_pass_is_handed_to_the_stored_pixels_where_it_ends(
    monkeypatch: pytest.MonkeyPatch, camera: bool, fov_hint: float | None, statuses: dict[str, list[str]],
    stored_status: dict[str, str], seen: list[tuple[str, str]], kept: str,
) -> None:
    from services.ingest import jobs

    _scripted(monkeypatch, statuses)
    handover = _Handover(stored_status)
    out = jobs._solve_chain_once(np.zeros((4, 4)), [object()] * 10, 4, 4, camera_meta=camera, fov_hint=fov_hint,
                                 to_stored=handover, **_PASS)
    assert handover.seen == seen
    assert out.name == kept


def test_every_search_from_a_persons_alignment_is_handed_to_the_stored_pixels_where_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.ingest import jobs

    _scripted(monkeypatch, {"phone": ["calibrated", "calibrated", "unresolved"], "plate": []})
    handover = _Handover({"stored phone 1": "unresolved", "stored phone 2": "unresolved"})
    out = jobs._solve_person_chain(
        np.zeros((4, 4)), [], hint_ra_deg=None, hint_dec_deg=None, hint_radius_deg=None, person_axes=[(1.0, 2.0)],
        person_fov_deg=60.0, to_stored=handover, **_PASS,
    )
    assert handover.seen == [("phone", "phone 1"), ("phone", "phone 2"), ("phone", "phone 3")]
    assert out.name == "stored phone 3"


def test_a_still_at_the_line_is_searched_as_stored(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height // 4)
    factors: list[int] = []
    real = jobs.bin_frame
    monkeypatch.setattr(jobs, "bin_frame", lambda frame, k: factors.append(k) or real(frame, k))
    _, at_line = _solve(client, _png(rgb))
    assert factors == []
    assert at_line["status"] == "calibrated"


def test_the_search_is_told_the_size_of_the_binned_frame_it_is_given(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The triangle and plate searches place their WCS and cut their catalog by the frame size they are told, so a
    binned still's search is told the binned size, never the stored one."""
    from services.ingest import jobs

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    told: list[tuple[tuple[int, ...], int, int]] = []
    real = jobs._solve_chain

    def spy(solve_image: Any, detections: Any, w: int, h: int, **kwargs: Any) -> Any:
        told.append((tuple(solve_image.shape[:2]), w, h))
        return real(solve_image, detections, w, h, **kwargs)

    monkeypatch.setattr(jobs, "_solve_chain", spy)
    _solve(client, _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)))
    assert told == [((height, width), width, height)]


def test_a_binned_solve_whose_stored_residual_exceeds_the_gate_serves_predicted_only_without_failing_the_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rp-b-10's route case under the 22:42 rule, for a calibration the handover does not take: when each binned
    search passes at 1.6 px but restated in 2 x 2 stored pixels is 3.2 px > 3.0 px, the job succeeds and serves
    predicted_only with GATE_REFUSED_WARNING."""
    from dataclasses import fields, replace

    from services.ingest import jobs
    from services.science.solver import GATE_REFUSED_WARNING
    from services.science.wcs_models import WCSModel

    rgb = _frame().rgb
    height, width = rgb.shape[:2]
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", 2 * width * height)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height)
    monkeypatch.setattr(jobs, "_SOLVE_IN_PROCESS", True)
    calibrated: list[str] = []

    def loose(real: Any) -> Any:
        # Every search's calibration as one the handover does not take (bin-detect-v2 hands over a phone solve's
        # PhoneCameraWCS only): its WCS as a plain TAN model, as a plate solve gives, at 1.6 px in the binned frame,
        # so the stored-pixel gate of the restatement holds it
        def search(*args: Any, **kwargs: Any) -> Any:
            sol = real(*args, **kwargs)
            if sol.status != "calibrated":
                return sol
            calibrated.append(real.__name__)
            tan = WCSModel(**{f.name: getattr(sol.wcs, f.name) for f in fields(WCSModel)})
            return replace(sol, wcs=tan, quality=replace(sol.quality, median_residual_px=1.6, p95_residual_px=2.8))

        return search

    monkeypatch.setattr(jobs, "solve_phone_photo", loose(jobs.solve_phone_photo))
    monkeypatch.setattr(jobs, "solve_and_verify_field", loose(jobs.solve_and_verify_field))
    _, served = _solve(client, _png(np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)))
    assert calibrated, "a binned search calibrated"
    assert served["status"] == "predicted_only"
    assert "wcs" not in served and "poseEstimate" not in served
    q = served["quality"]
    assert q["passedIndependentVerification"] is False
    assert q["matchedInlierCount"] == 0
    assert q["medianResidualPx"] == pytest.approx(3.2)
    assert q["p95ResidualPx"] == pytest.approx(5.6)
    assert {f["evidenceClass"] for f in served["findings"]} == {"observed", "predicted"}
    measured = _measured(served)
    assert len(measured) > 0
    assert all(f["evidenceClass"] == "observed" and "skyPosition" not in f for f in measured)
    assert all(0.0 <= f["sourceDetection"]["xPx"] < 2 * width for f in measured)
    assert all(0.0 <= f["sourceDetection"]["yPx"] < 2 * height for f in measured)
    assert GATE_REFUSED_WARNING in served["warnings"]



def test_a_one_frame_clip_above_the_line_is_searched_as_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Binning is for stills: a clip of one frame takes the still's branch of the job but is never binned."""
    import shutil
    import subprocess

    from services.ingest import jobs

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is on every CI runner and on the laptop"
    width, height = 160, 120
    monkeypatch.setattr(binning, "BIN_ABOVE_PX", width * height // 4)
    monkeypatch.setattr(binning, "BIN_MAX_PX", width * height // 16)
    factors: list[int] = []
    real = jobs.bin_frame
    monkeypatch.setattr(jobs, "bin_frame", lambda frame, k: factors.append(k) or real(frame, k))
    luma = np.full((height, width), 12, dtype=np.uint8)
    clip = tmp_path / "one.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", "1", "-i", "-",
         "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        input=luma.tobytes(), check=True, capture_output=True, timeout=60,
    )
    body = clip.read_bytes()
    resp = client.post("/api/ingest", content=body, headers={"content-type": "video/mp4"})
    assert resp.status_code == 201, resp.text
    assert binning.bin_factor(width, height) > 1, "the line is drawn below the clip's size"
    sha = hashlib.sha256(body).hexdigest()
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job
    assert factors == []


_CHAIN = dict(camera_meta=True, camera_label=None, fov_hint=None, obs_utc_iso="2026-01-01T00:00:00Z", lat_deg=0.0,
              lon_deg=0.0, source_asset_id="src_x", sha256="0" * 64, solution_id="sol_x")


@pytest.mark.parametrize("binned", [True, False])
@pytest.mark.parametrize("moment_lent", ["time", None])
def test_every_pass_of_the_chain_is_given_one_handover_when_binned_and_none_when_not(
    monkeypatch: pytest.MonkeyPatch, binned: bool, moment_lent: str | None
) -> None:
    """The hinted pass, the pass without the hint, and the blind pass (a lent moment) or the whole-sky pass (the
    photo's own): each is given the chain's one handover, built from the stored frame it was given, or none."""
    from types import SimpleNamespace

    from services.ingest import jobs

    given: list[tuple[bool, Any, Any]] = []

    def once(*args: Any, to_stored: Any = None, **kwargs: Any) -> Any:
        given.append((kwargs.get("whole_sky", False), kwargs.get("obs_utc_iso"), to_stored))
        return SimpleNamespace(status="unresolved", warnings=[])

    monkeypatch.setattr(jobs, "_solve_chain_once", once)
    stored = (np.zeros((8, 8)), 2, 8, 8) if binned else None
    jobs._solve_chain(np.zeros((4, 4)), [], 4, 4, on_event=lambda *a: None, hint_ra_deg=10.0, hint_dec_deg=20.0,
                      hint_radius_deg=5.0, moment_lent=moment_lent, stored=stored, **_CHAIN)
    # hinted, unhinted, then blind (no moment) or whole sky
    assert [(g[0], g[1] is None) for g in given] == [(False, False), (False, False), (moment_lent is None, moment_lent is not None)]
    if binned:
        assert isinstance(given[0][2], jobs._StoredPixels)
        assert all(g[2] is given[0][2] for g in given)
    else:
        assert all(g[2] is None for g in given)


@pytest.mark.parametrize("binned", [True, False])
def test_a_persons_chain_is_given_the_handover_when_binned_and_none_when_not(
    monkeypatch: pytest.MonkeyPatch, binned: bool
) -> None:
    from types import SimpleNamespace

    from services.ingest import jobs

    given: list[Any] = []

    def person(*args: Any, to_stored: Any = None, **kwargs: Any) -> Any:
        given.append(to_stored)
        return SimpleNamespace(status="unresolved", warnings=[])

    monkeypatch.setattr(jobs, "_solve_person_chain", person)
    stored = (np.zeros((8, 8)), 2, 8, 8) if binned else None
    jobs._solve_chain(np.zeros((4, 4)), [], 4, 4, on_event=lambda *a: None, person_axes=None, person_fov_deg=60.0,
                      stored=stored, **_CHAIN)
    assert len(given) == 1
    assert isinstance(given[0], jobs._StoredPixels) if binned else given[0] is None
