"""POST /api/science/solve says a frame's measured limits in plain words when it measures the frame itself.

The detector flags a frame on which its stated false rate does not hold ("near_black_clip";
detector.NOISE_LIMIT_WORDS). The upload job and the phone solver put those words in the solution's warnings; the
science route's image_luma path, which detects on the frame it is given, did not (AUDIT M, realsky-pair finding 5).
Given detections instead of a frame, the route measures no frame, so it has no flag to say.
All frames here are simulated.
"""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
import numpy as np

from services.api import app
from services.science.detector import NOISE_LIMIT_WORDS, detect_point_sources

ROUTE_MOMENT = {"observedAtUTC": "2030-01-01T02:00:00Z", "latDeg": 30.0, "lonDeg": -40.0}


def _dark_grey_frame() -> np.ndarray:
    """AUDIT M's frame for this finding (probe_api_flags.py) brought down to its black clip: a dark 8-bit grey sky at
    0.8 codes, read noise 0.8 codes (its median within 1.25 noises of its lowest code), with 20 stars of 1.3 px, rounded."""
    rng = np.random.default_rng(7101)
    h, w = 200, 240
    frame = 0.8 + rng.normal(0.0, 0.8, (h, w))
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(20):
        x, y = rng.uniform(15, w - 15), rng.uniform(15, h - 15)
        frame += rng.uniform(20, 60) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3 * 1.3))
    return np.clip(np.rint(frame), 0, 255)


def _body(frame: np.ndarray) -> dict:
    digest = hashlib.sha256(np.ascontiguousarray(frame, dtype=np.float64).tobytes()).hexdigest()
    return {**ROUTE_MOMENT, "image_luma": frame.tolist(), "sourceAssetId": "src_abababababababab", "sourceSha256": digest}


# C25 (.lead/uiux/UIUX-nearblackwords-READY.md, decided by the lead at 12:38 on 21 Nov), in C14's shape: the first
# sentence is the one shown on screen, the second stays in the solution's record
C25_SCREEN = (
    "This photo's sky is so close to black that its faintest noise is cut off, so some of the points counted may be "
    "that noise, not stars."
)
C25_RECORD = (
    "On such a sky the detector finds more points on pure noise than its stated rate, so the count's false rate is not "
    "controlled."
)


def test_the_near_black_words_are_the_decided_ones_screen_sentence_first() -> None:
    assert NOISE_LIMIT_WORDS["near_black_clip"] == f"{C25_SCREEN} {C25_RECORD}"


def test_a_near_black_frames_warning_is_the_decided_words_once() -> None:
    frame = _dark_grey_frame()
    response = TestClient(app).post("/api/science/solve", json=_body(frame))
    assert response.status_code == 200, response.text
    assert response.json()["warnings"].count(f"{C25_SCREEN} {C25_RECORD}") == 1


def test_the_image_luma_path_says_the_frames_flags_in_its_warnings() -> None:
    frame = _dark_grey_frame()
    flags = detect_point_sources(frame)[0].noise_limits
    assert "near_black_clip" in flags
    response = TestClient(app).post("/api/science/solve", json=_body(frame))
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    # Each raised flag's words once, and no other flag's words
    for flag, words in NOISE_LIMIT_WORDS.items():
        assert warnings.count(words) == (1 if flag in flags else 0), flag


def test_a_frame_with_no_flag_gets_no_flag_words() -> None:
    # A float sky at 100 with noise 2 sits on no grid and far from the black clip: no flag, no words
    rng = np.random.default_rng(7102)
    frame = 100.0 + rng.normal(0.0, 2.0, (200, 240))
    assert detect_point_sources(frame) == [] or not detect_point_sources(frame)[0].noise_limits
    response = TestClient(app).post("/api/science/solve", json=_body(frame))
    assert response.status_code == 200, response.text
    assert not any(words in response.json()["warnings"] for words in NOISE_LIMIT_WORDS.values())


def test_given_detections_the_route_measures_no_frame_and_says_no_flag() -> None:
    # The client's detections come without the frame they were measured on, so the route has no flag to say
    frame = _dark_grey_frame()
    dets = [d for d in detect_point_sources(frame) if not d.is_hot_pixel_impulse]
    detections = [
        {"xPx": d.x_px, "yPx": d.y_px, "flux": d.flux, "snr": d.snr, "fwhmPx": d.fwhm_px, "centroidUncertaintyPx": d.centroid_uncertainty_px}
        for d in dets
    ]
    body = {k: v for k, v in _body(frame).items() if k != "image_luma"}
    response = TestClient(app).post("/api/science/solve", json={**body, "detections": detections, "width": 240, "height": 200})
    assert response.status_code == 200, response.text
    assert not any(words in response.json()["warnings"] for words in NOISE_LIMIT_WORDS.values())


def test_the_upload_solve_chain_says_a_flagged_phone_solutions_words_once() -> None:
    # When camera metadata sends the upload through solve_phone_photo first, the phone solver puts the frame's flag
    # words in the solution's warnings and _solve_chain does not add a duplicate copy
    from services.ingest import jobs

    frame = _dark_grey_frame().astype(np.uint8)
    dets = detect_point_sources(frame, asset_id="src_abababababababab")
    assert "near_black_clip" in dets[0].noise_limits
    sol = jobs._solve_chain(
        frame, dets, frame.shape[1], frame.shape[0], camera_meta=True, camera_label="iPhone 16 Pro, 24 mm f/1.78",
        fov_hint=None, obs_utc_iso="2030-01-01T02:00:00Z", lat_deg=30.0, lon_deg=-40.0,
        source_asset_id="src_abababababababab", sha256="a" * 64, solution_id="sol_aaaaaaaaaaaaaaaa",
        on_event=lambda *_: None,
    )
    assert sol.warnings.count(NOISE_LIMIT_WORDS["near_black_clip"]) == 1
