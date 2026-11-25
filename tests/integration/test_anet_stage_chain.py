"""The astrometry.net stage in the solve chain (anet-stage-one; the lead's decision of 2026-11-24 21:32).

It runs only when the stage is on and the app's own searches calibrated nothing; its WCS is only a candidate, which
the phone solver's own association, refinement and held-out check judge, unchanged; a calibration from it names its
source (`candidateSource`) and says so in the result's details. Off, nothing runs and every record is main's.

The program is a stand-in (tests/science/test_anet_stage.py's) that writes a TAN header for a synthetic frame [simulated]:
the frame's own pose as the app solved it, or that pose turned 40 deg, which the held-out check must refuse.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import math
from pathlib import Path
import time
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
from services.science import anet_stage
from services.science import phone_photo_solver as pps
from services.science.detector import detect_point_sources
from tests.science.test_anet_stage import _gnomonic, stand_in, tan_header  # noqa: F401 (the stand-in fixture)

_FRAME: Any = None
_TRUTH: Any = None


def _frame() -> Any:
    global _FRAME
    if _FRAME is None:
        _FRAME = phone_render.render_frame(phone_suite.build_suite("ci")[0], phone_stars.TileCatalog())
    return _FRAME


def _truth() -> Any:
    """The frame's WCS as the app's own solve finds it, once."""
    global _TRUTH
    if _TRUTH is None:
        frame = _frame()
        spec = frame.spec
        _TRUTH = pps.solve_phone_photo(
            frame.rgb, obs_utc_iso=spec.utc, observer_lat=spec.lat_deg, observer_lon=spec.lon_deg,
            fov_diagonal_deg=spec.fov_deg, source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1",
        )
        assert _TRUTH.status == "calibrated"
    return _TRUTH


def _header_for(turn_deg: float = 0.0) -> bytes:
    """A TAN header of the frame's solved pose, read off it at the centre (turned about the axis by `turn_deg`)."""
    wcs = _truth().wcs
    spec = _frame().spec
    cx, cy = (spec.width - 1) / 2.0, (spec.height - 1) / 2.0
    ra0, dec0 = wcs.pixel_to_world(cx, cy)
    right = _gnomonic(ra0, dec0, *wcs.pixel_to_world(cx + 1.0, cy))
    down = _gnomonic(ra0, dec0, *wcs.pixel_to_world(cx, cy + 1.0))
    c, s = math.cos(math.radians(turn_deg)), math.sin(math.radians(turn_deg))
    turn = lambda v: (c * v[0] - s * v[1], s * v[0] + c * v[1])  # noqa: E731
    right, down = turn(right), turn(down)
    return tan_header((ra0, dec0), (cx + 1.0, cy + 1.0), (right[0], down[0], right[1], down[1]))


def _unresolved_like_the_search() -> Any:
    """What the app's own searches gave when they calibrated nothing: a real refused solution of the frame."""
    frame = _frame()
    spec = frame.spec
    return pps.solve_phone_photo(
        frame.rgb, obs_utc_iso=None, observer_lat=None, observer_lon=None, detections=[],
        source_asset_id="src_" + "c" * 16, source_sha256="c" * 63 + "1",
    )


def _chain(monkeypatch: pytest.MonkeyPatch, first: Any, anet: dict | None, calls: list | None = None) -> Any:
    frame = _frame()
    spec = frame.spec

    def searches(*args: Any, **kwargs: Any) -> Any:
        if calls is not None:
            calls.append(kwargs.get("whole_sky", False))
        return first

    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", searches)
    luma = np.asarray(frame.rgb, dtype=np.float64).mean(axis=2) if frame.rgb.ndim == 3 else frame.rgb
    detections = detect_point_sources(luma, asset_id="src_" + "c" * 16)
    events: list[tuple] = []
    result = ingest_jobs._solve_chain(
        frame.rgb, detections, spec.width, spec.height, on_event=lambda *e: events.append(e), camera_meta=False,
        camera_label=None, fov_hint=None, obs_utc_iso=None, lat_deg=None, lon_deg=None,
        source_asset_id="src_" + "c" * 16, sha256="c" * 63 + "1", solution_id="sol_anet", anet=anet,
    )
    return result, events


def _anet(stand_in, bound_s: float = 300.0) -> dict:
    return {**dataclasses.asdict(stand_in.settings), "bound_s": bound_s}


def test_the_stage_turns_its_candidate_into_a_calibration_the_apps_own_check_passed(monkeypatch, stand_in) -> None:
    stand_in.plan(header=_header_for())
    result, events = _chain(monkeypatch, _unresolved_like_the_search(), _anet(stand_in))
    assert result.status == "calibrated" and result.quality.passed_independent_verification
    assert result.candidate_source is not None
    assert (result.candidate_source.program, result.candidate_source.version, result.candidate_source.index_file) == (
        "astrometry.net", "0.97-stand-in", "index-4117.fits"
    )
    assert ingest_jobs.ANET_WORDS in result.warnings
    # The stars it names are catalog matched by the app's own check, as on any calibration
    assert [f for f in result.findings if f.evidence_class == "catalog_matched"]
    assert result.to_contract_dict()["candidateSource"]["program"] == "astrometry.net"
    # The stage's work is a pass of its own on the working screen
    assert ("solver", "phone_photo") in events


def test_a_wrong_candidate_is_refused_by_the_check_and_the_searches_answer_stands(monkeypatch, stand_in) -> None:
    stand_in.plan(header=_header_for(turn_deg=40.0))
    first = _unresolved_like_the_search()
    result, _ = _chain(monkeypatch, first, _anet(stand_in))
    assert result is first and result.candidate_source is None
    assert ingest_jobs.ANET_WORDS not in result.warnings
    assert len(stand_in.calls()) >= 1


def test_with_the_stage_off_nothing_runs_and_the_searches_answer_stands(monkeypatch, stand_in) -> None:
    stand_in.plan(header=_header_for())
    first = _unresolved_like_the_search()
    result, _ = _chain(monkeypatch, first, None)
    assert result is first and stand_in.calls() == []


def test_the_stage_runs_only_after_the_searches_failed(monkeypatch, stand_in) -> None:
    stand_in.plan(header=_header_for())
    calibrated = _truth()
    result, _ = _chain(monkeypatch, calibrated, _anet(stand_in))
    assert result is calibrated and stand_in.calls() == []


def test_the_stage_takes_no_more_than_what_is_left_of_its_bound(monkeypatch, stand_in) -> None:
    # Nothing is left for the program after the check's own share: the stage gives up at once
    stand_in.plan(header=_header_for(), sleep=30.0)
    first = _unresolved_like_the_search()
    began = time.monotonic()
    result, _ = _chain(monkeypatch, first, _anet(stand_in, bound_s=20.0))
    assert result is first and time.monotonic() - began < 15.0


def test_the_words_are_uiuxs() -> None:
    # UIUX-anet-words-READY.md item 1, decided by the lead at 21:44
    assert ingest_jobs.ANET_WORDS == (
        "The first possible match came from astrometry.net; the stars held back to check it landed where it put them, "
        "so it was kept."
    )


def test_the_search_bound_grows_by_the_stage_s_own_only_when_it_is_on(monkeypatch, stand_in, tmp_path) -> None:
    monkeypatch.delenv(anet_stage.ENV_ON, raising=False)
    assert ingest_jobs._anet_for_job(180.0) == (180.0, None)
    monkeypatch.setenv(anet_stage.ENV_ON, "1")
    monkeypatch.setenv(anet_stage.ENV_BIN, str(Path(stand_in.settings.solve_field).parent))
    monkeypatch.setenv(anet_stage.ENV_INDEX, stand_in.settings.index_dir)
    bound, anet = ingest_jobs._anet_for_job(180.0)
    assert bound == 180.0 + anet_stage.STAGE_LIMIT_S and anet["bound_s"] == bound
    assert anet["solve_field"] == stand_in.settings.solve_field


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)


def _served(client: TestClient) -> dict:
    out = io.BytesIO()
    Image.fromarray(_frame().rgb, mode="RGB").save(out, format="PNG")
    body = out.getvalue()
    sha = hashlib.sha256(body).hexdigest()
    assert client.post("/api/ingest", content=body, headers={"content-type": "image/png"}).status_code == 201
    job = client.post(f"/api/ingest/{sha}/solve").json()
    deadline = time.monotonic() + 300.0
    while job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    assert job["state"] == "succeeded", job.get("error")
    return client.get(f"/api/ingest/{sha}/solution").json()


@pytest.mark.parametrize("on", [True, False])
def test_through_the_routes_a_served_calibration_names_its_candidate_s_source_only_with_the_stage_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stand_in, on: bool
) -> None:
    # The app's own searches stand in as finding nothing, in the caller's process so the stand-in reaches them
    stand_in.plan(header=_header_for())
    def refused(solve_image: Any, *args: Any, **kwargs: Any) -> Any:
        # A real refused solution of this job's own upload: its asset id, hash and solution id
        return pps.solve_phone_photo(
            solve_image, detections=[], source_asset_id=kwargs["source_asset_id"], source_sha256=kwargs["sha256"],
            solution_id=kwargs["solution_id"],
        )

    monkeypatch.setattr(ingest_jobs, "_SOLVE_IN_PROCESS", True)
    monkeypatch.setattr(ingest_jobs, "_solve_chain_once", refused)
    if on:
        monkeypatch.setenv(anet_stage.ENV_ON, "1")
        monkeypatch.setenv(anet_stage.ENV_BIN, str(Path(stand_in.settings.solve_field).parent))
        monkeypatch.setenv(anet_stage.ENV_INDEX, stand_in.settings.index_dir)
    else:
        monkeypatch.delenv(anet_stage.ENV_ON, raising=False)
    solution = _served(client)
    if on:
        assert solution["status"] == "calibrated"
        assert solution["candidateSource"] == {"program": "astrometry.net", "version": "0.97-stand-in", "indexFile": "index-4117.fits"}
        assert ingest_jobs.ANET_WORDS in solution["warnings"]
    else:
        assert solution["status"] != "calibrated" and "candidateSource" not in solution
        assert stand_in.calls() == []


@pytest.mark.parametrize("handed", ["kept", "refused"])
def test_a_binned_stills_calibration_from_the_stage_is_handed_to_its_stored_pixels(monkeypatch, stand_in, handed: str) -> None:
    # bin-detect-v2: every phone search's calibration of a still searched binned goes through the stored-pixel handover
    # (refine-2's refit under the same gate); the stage's does too, and is kept only when the handover keeps it
    stand_in.plan(header=_header_for())
    seen: list[Any] = []
    refused = _unresolved_like_the_search()

    class Handover:
        def phone(self, solution: Any) -> Any:
            seen.append(solution.status)
            return solution if handed == "kept" else refused

    frame = _frame()
    spec = frame.spec
    luma = np.asarray(frame.rgb, dtype=np.float64).mean(axis=2)
    detections = detect_point_sources(luma, asset_id="src_" + "c" * 16)
    phone = ingest_jobs._PhoneDetections(frame.rgb, "src_" + "c" * 16)
    result = ingest_jobs._solve_from_anet(
        frame.rgb, detections, spec.width, spec.height, on_event=lambda *e: None, anet=_anet(stand_in), moment_lent=None,
        spent_s=0.0, phone_detections=phone, to_stored=Handover(), camera_meta=False, camera_label=None, fov_hint=None,
        obs_utc_iso=None, lat_deg=None, lon_deg=None, source_asset_id="src_" + "c" * 16, sha256="c" * 63 + "1",
        solution_id="sol_anet",
    )
    assert seen == ["calibrated"]
    if handed == "kept":
        assert result is not None and result.status == "calibrated" and result.candidate_source is not None
    else:
        assert result is None
