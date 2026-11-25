"""detect-once-1: the phone solver's own detection is made once per solve chain and handed to every pass after the
first (lever two of solve-wall-profile), every result as it was.

`solve_phone_photo` detects its frame itself when it is given no detections (the luma, over 255 for 8-bit codes), and
the chain called it so at every pass: the 35 mm retry, the unhinted chain after a pointing hint, the blind chain after
a lent moment. The same frame the same way gives the same list, so one detection per chain serves them all. These
tests count the detections a chain makes, compare its solution with the one each pass detecting for itself gives, in
one process (so on any platform), and hold the memo to what `solve_phone_photo` makes on its own.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from scipy import ndimage

from services.ingest import jobs as ingest_jobs
from services.science import phone_photo_solver as pps
from services.science.detector import _to_luma_2d, detect_point_sources

ASSET = "src_0123456789abcdef"
# Every pass runs: a 35 mm tag (then the generic retry), a pointing hint (then the unhinted chain), a lent moment (then
# the blind chain); a synthetic moment and an open-ocean place no capture was made at
CHAIN: dict[str, Any] = dict(
    camera_meta=True, camera_label="Synthetic Phone", fov_hint=60.0, obs_utc_iso="2031-03-20T12:00:00Z", lat_deg=20.0,
    lon_deg=-140.0, source_asset_id=ASSET, sha256="ab" * 32, solution_id="sol_0123456789abcdef", hint_ra_deg=10.0,
    hint_dec_deg=20.0, hint_radius_deg=5.0, triangle_radius_deg=40.0, moment_lent="both",
)


def _frame(seed: int = 7, h: int = 240, w: int = 320, stars: int = 14) -> np.ndarray:
    """An 8-bit RGB night frame of a few random stars on correlated noise: no sky to match, so every pass runs."""
    rng = np.random.default_rng(seed)
    sky = 18.0 + ndimage.gaussian_filter(rng.normal(0.0, 4.0, (h, w)), 1.0)
    yy, xx = np.mgrid[0:h, 0:w]
    for _ in range(stars):
        cx, cy, amp = rng.uniform(8, w - 8), rng.uniform(8, h - 8), rng.uniform(30.0, 140.0)
        sky += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * 1.3**2))
    luma = np.clip(np.round(sky), 0.0, 255.0).astype(np.uint8)
    return np.stack([luma, luma, luma], axis=-1)


def _job_detections(image: np.ndarray) -> list:
    return detect_point_sources(_to_luma_2d(image.astype(np.float64)), asset_id=ASSET)


def _counting(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts = {"phone detections": 0, "phone passes": 0}
    real_detect, real_solve = pps.detect_point_sources, ingest_jobs.solve_phone_photo

    def detect(*args: Any, **kwargs: Any) -> Any:
        counts["phone detections"] += 1
        return real_detect(*args, **kwargs)

    def solve(*args: Any, **kwargs: Any) -> Any:
        counts["phone passes"] += 1
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(pps, "detect_point_sources", detect)
    monkeypatch.setattr(ingest_jobs, "detect_point_sources", detect)
    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", solve)
    return counts


def _chain(image: np.ndarray, detections: list) -> Any:
    h, w = image.shape[:2]
    return ingest_jobs._solve_chain(image, detections, w, h, on_event=lambda *a: None, **CHAIN)


def test_a_chain_of_many_passes_detects_its_frame_once(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    detections = _job_detections(image)
    counts = _counting(monkeypatch)
    solution = _chain(image, detections)
    assert solution.status != "calibrated"
    assert counts["phone passes"] >= 4, counts
    assert counts["phone detections"] == 1, counts


def test_the_solution_is_the_one_each_pass_detecting_for_itself_gives(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    detections = _job_detections(image)
    once = _chain(image, detections).to_contract_dict()

    class EachPassDetects:
        """The chain as it was: no list handed over, so `solve_phone_photo` detects at every pass."""

        def __init__(self, *args: Any) -> None:
            pass

        def __call__(self) -> None:
            return None

    monkeypatch.setattr(ingest_jobs, "_PhoneDetections", EachPassDetects)
    assert _chain(image, detections).to_contract_dict() == once


def test_the_memo_holds_what_solve_phone_photo_detects_and_detects_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame(seed=11)
    made: list[list] = []
    real_detect = pps.detect_point_sources

    def detect(*args: Any, **kwargs: Any) -> Any:
        made.append(real_detect(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(pps, "detect_point_sources", detect)
    pps.solve_phone_photo(image, obs_utc_iso=None, observer_lat=None, observer_lon=None, camera_label=None,
                          source_asset_id=ASSET, source_sha256="ab" * 32, solution_id="sol_0123456789abcdef")
    assert len(made) == 1
    monkeypatch.setattr(ingest_jobs, "detect_point_sources", detect)
    memo = ingest_jobs._PhoneDetections(image, ASSET)
    assert len(made) == 1
    assert memo() == made[0] and memo() is memo()
    assert len(made) == 2


def test_the_list_a_chain_hands_over_is_the_one_solve_phone_photo_would_make(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame(seed=5)
    handed: list = []
    real_solve = ingest_jobs.solve_phone_photo

    def solve(*args: Any, **kwargs: Any) -> Any:
        handed.append(kwargs.get("detections"))
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(ingest_jobs, "solve_phone_photo", solve)
    _chain(image, _job_detections(image))
    made: list[list] = []
    real_detect = pps.detect_point_sources

    def detect(*args: Any, **kwargs: Any) -> Any:
        made.append(real_detect(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(pps, "detect_point_sources", detect)
    real_solve(image, obs_utc_iso=None, observer_lat=None, observer_lon=None, camera_label=None,
               source_asset_id=ASSET, source_sha256="ab" * 32, solution_id="sol_0123456789abcdef")
    assert handed and all(h is handed[0] for h in handed)
    assert handed[0] == made[0] and all(d.asset_id == ASSET for d in handed[0])


def test_the_whole_sky_pass_after_an_own_moment_shares_the_one_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    # A file's own time and place (nothing lent): the hinted chain, the unhinted one and the whole-sky pass
    # (own-moment-fallback-1) all take the one list
    image = _frame()
    detections = _job_detections(image)
    counts = _counting(monkeypatch)
    own = {key: value for key, value in CHAIN.items() if key != "moment_lent"}
    h, w = image.shape[:2]
    ingest_jobs._solve_chain(image, detections, w, h, on_event=lambda *a: None, **own)
    assert counts["phone passes"] >= 3, counts
    assert counts["phone detections"] == 1, counts
