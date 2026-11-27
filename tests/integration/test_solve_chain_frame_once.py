"""frame-once-1: the phone solver's frame setup (the luma, the background and the ranked pool every pass reads) made
once per frame and handed to every pass after the first, every result as it was.

A solve runs the phone solver's pass four to six times on one frame (the upright prior, then every roll; the 35 mm
retry; the unhinted, blind and whole-sky chains), and each pass made the three again from the same input: one
distinct input and one distinct output each over all its calls on eight public photos, 3.6 to 15.4 s a solve
(SCIENCE-INGEST pass-repeats-1). `PhoneFrameSetup` keeps them under a key of what they read, the frame's type, shape
and bytes and the detections' measured values in order, so a frame changed in place or a list with another star is
made again. These tests count the three calls a solve and a chain make, compare the solution with the one each pass
making its own gives, in one process (so on any platform), and hold the setup to what a pass makes itself.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from scipy import ndimage

from services.ingest import jobs as ingest_jobs
from services.science import phone_photo_solver as pps
from services.science.detector import _to_luma_2d, detect_point_sources

ASSET = "src_0123456789abcdef"
SHA = "ab" * 32
SOLUTION = "sol_0123456789abcdef"
# Every pass runs: a 35 mm tag (then the generic retry), a pointing hint (then the unhinted chain), a lent moment (then
# the blind chain); a synthetic moment and an open-ocean place no capture was made at
CHAIN: dict[str, Any] = dict(
    camera_meta=True, camera_label="Synthetic Phone", fov_hint=60.0, obs_utc_iso="2031-03-20T12:00:00Z", lat_deg=20.0,
    lon_deg=-140.0, source_asset_id=ASSET, sha256=SHA, solution_id=SOLUTION, hint_ra_deg=10.0,
    hint_dec_deg=20.0, hint_radius_deg=5.0, triangle_radius_deg=40.0, moment_lent="both",
)
SETUP = ("luma", "background", "ranking")


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


def _phone_detections(image: np.ndarray) -> list:
    raw = _to_luma_2d(image)
    return detect_point_sources(raw / 255.0 if image.dtype == np.uint8 else raw, asset_id=ASSET)


def _counting(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Counts of the three the setup makes, and of the phone solver's passes, as the phone solver calls them."""
    counts = {name: 0 for name in (*SETUP, "passes")}
    real = {
        "luma": pps._to_luma_2d, "background": pps._estimate_background_and_noise,
        "ranking": pps._rank_stellar_candidates, "passes": pps._solve_phone_photo_with,
    }
    names = {"luma": "_to_luma_2d", "background": "_estimate_background_and_noise",
             "ranking": "_rank_stellar_candidates", "passes": "_solve_phone_photo_with"}

    def counted(name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            counts[name] += 1
            return real[name](*args, **kwargs)

        return call

    for name, attribute in names.items():
        monkeypatch.setattr(pps, attribute, counted(name))
    return counts


def _chain(image: np.ndarray, detections: list, **chain: Any) -> Any:
    h, w = image.shape[:2]
    return ingest_jobs._solve_chain(image, detections, w, h, on_event=lambda *a: None, **{**CHAIN, **chain})


def _each_pass_makes_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """The solver as it was: no setup handed to any pass, so each makes the three itself."""
    monkeypatch.setattr(pps, "PhoneFrameSetup", lambda: None)
    monkeypatch.setattr(ingest_jobs, "PhoneFrameSetup", lambda: None)


def test_a_chain_of_many_passes_makes_its_frame_setup_once(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    detections = _job_detections(image)
    counts = _counting(monkeypatch)
    solution = _chain(image, detections)
    assert solution.status != "calibrated"
    assert counts["passes"] >= 4, counts
    assert all(counts[name] == 1 for name in SETUP), counts


def test_a_solve_of_two_passes_makes_its_frame_setup_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # A time and a place: the upright prior, and when it does not calibrate every roll on the same detections
    image = _frame(seed=3)
    detections = _phone_detections(image)
    counts = _counting(monkeypatch)
    solution = pps.solve_phone_photo(
        image, detections=detections, obs_utc_iso=CHAIN["obs_utc_iso"], observer_lat=20.0, observer_lon=-140.0,
        source_asset_id=ASSET, source_sha256=SHA, solution_id=SOLUTION,
    )
    assert solution.status != "calibrated"
    assert counts["passes"] == 2, counts
    assert all(counts[name] == 1 for name in SETUP), counts


def test_the_chains_solution_is_the_one_each_pass_making_its_own_gives(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    detections = _job_detections(image)
    once = _chain(image, detections).to_contract_dict()
    own = {key: value for key, value in CHAIN.items() if key != "moment_lent"}
    once_own = ingest_jobs._solve_chain(image, detections, 320, 240, on_event=lambda *a: None, **own).to_contract_dict()
    _each_pass_makes_its_own(monkeypatch)
    counts = _counting(monkeypatch)
    assert _chain(image, detections).to_contract_dict() == once
    assert counts["background"] == counts["passes"] >= 4, counts
    assert ingest_jobs._solve_chain(image, detections, 320, 240, on_event=lambda *a: None, **own).to_contract_dict() == once_own


@pytest.mark.parametrize("kind", ["rgb8", "luma float", "luma codes"])
def test_the_setup_gives_what_a_pass_makes_itself(kind: str) -> None:
    rgb = _frame(seed=5)
    image = {
        "rgb8": rgb,
        "luma float": rgb[:, :, 0].astype(np.float64) / 255.0,
        "luma codes": rgb[:, :, 0].astype(np.float64),
    }[kind]
    raw = _to_luma_2d(image)
    luma = raw / 255.0 if (image.dtype == np.uint8 or float(np.max(raw)) > 1.5) else raw
    bg, _ = pps._estimate_background_and_noise(luma)
    detections = _phone_detections(rgb)
    ranked, sky = pps._rank_stellar_candidates(image, luma, bg, detections, max_pool=85)
    prepared = pps.PhoneFrameSetup().frame(image)
    assert np.array_equal(prepared.luma, luma) and prepared.luma.dtype == luma.dtype
    assert np.array_equal(prepared.bg, bg) and prepared.bg.dtype == bg.dtype
    pool, pool_sky = prepared.ranked_pool(image, detections)
    assert len(pool) == len(ranked) >= 6 and all(a is b for a, b in zip(pool, ranked)) and pool_sky == sky


def test_a_frame_changed_in_place_is_made_again(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame(seed=9)
    setup = pps.PhoneFrameSetup()
    counts = _counting(monkeypatch)
    first = setup.frame(image)
    assert setup.frame(image) is first and setup.frame(image.copy()) is first
    assert counts["background"] == 1 and counts["luma"] == 1
    image[120, 160, :] += 3
    again = setup.frame(image)
    assert again is not first and counts["background"] == 2 and counts["luma"] == 2
    fresh = pps.PhoneFrameSetup().frame(image)
    assert np.array_equal(again.luma, fresh.luma) and np.array_equal(again.bg, fresh.bg)
    assert not np.array_equal(again.luma, first.luma)
    # The same values in another type or shape are another frame, and so are the same bytes read as another type or
    # laid out in another shape
    assert setup.frame(image.astype(np.float64)) is not again
    assert setup.frame(image[:, :, 0].copy()).luma.shape == image.shape[:2]
    setup.frame(image)
    signed = setup.frame(image.view(np.int8))
    assert np.array_equal(signed.luma, pps.PhoneFrameSetup().frame(image.view(np.int8)).luma)
    assert not np.array_equal(signed.luma, again.luma)
    setup.frame(image)
    turned = setup.frame(image.reshape(image.shape[1], image.shape[0], 3))
    assert turned.luma.shape == (image.shape[1], image.shape[0])


def test_a_list_with_another_star_is_ranked_again_and_an_equal_list_gets_its_own_detections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = _frame(seed=11)
    detections = _phone_detections(image)
    prepared = pps.PhoneFrameSetup().frame(image)
    counts = _counting(monkeypatch)
    pool, _ = prepared.ranked_pool(image, detections)
    assert prepared.ranked_pool(image, detections)[0] == pool and counts["ranking"] == 1
    # Another list of the same measures: no ranking again, and the pool is made of that list's own detections
    copied = [replace(d) for d in detections]
    copied_pool, _ = prepared.ranked_pool(image, copied)
    assert counts["ranking"] == 1
    assert all(any(c is d for d in copied) for c in copied_pool) and copied_pool == pool
    # A list in which one measure differs (each the pass might read), or with one star fewer, is ranked again
    for field in ("x_px", "y_px", "flux", "snr", "fwhm_px", "centroid_uncertainty_px", "peak_to_total_ratio",
                  "saturated_area_px"):
        changed = list(detections)
        value = getattr(changed[0], field)
        changed[0] = replace(changed[0], **{field: type(value)(value + 1)})
        before = counts["ranking"]
        prepared.ranked_pool(image, changed)
        assert counts["ranking"] == before + 1, field
    flipped = list(detections)
    flipped[0] = replace(flipped[0], saturated=not flipped[0].saturated)
    before = counts["ranking"]
    prepared.ranked_pool(image, flipped)
    prepared.ranked_pool(image, detections[:-1])
    prepared.ranked_pool(image, detections[1:] + detections[:1])
    assert counts["ranking"] == before + 3


def test_the_pool_handed_out_is_a_new_list_each_time() -> None:
    image = _frame(seed=13)
    detections = _phone_detections(image)
    prepared = pps.PhoneFrameSetup().frame(image)
    pool, _ = prepared.ranked_pool(image, detections)
    kept = list(pool)
    pool.clear()
    assert prepared.ranked_pool(image, detections)[0] == kept


def test_the_aligned_chain_shares_the_one_setup_across_its_searches(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    detections = _job_detections(image)
    counts = _counting(monkeypatch)
    solution = _chain(image, detections, person_axes=[(10.0, 20.0), (10.5, 20.0), (10.0, 19.5)], person_fov_deg=60.0)
    assert solution.status != "calibrated"
    assert counts["passes"] >= 3, counts
    assert all(counts[name] == 1 for name in SETUP), counts


def test_the_candidate_check_takes_the_chains_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame()
    phone = ingest_jobs._PhoneDetections(image, ASSET)
    handed: list[Any] = []

    def check(solve_image: Any, detections: Any, candidate: Any, **solve: Any) -> Any:
        handed.append(solve.get("_frame_setup"))
        return SimpleNamespace(status="unresolved")

    monkeypatch.setattr(ingest_jobs.anet_stage, "find_candidate", lambda *a, **k: SimpleNamespace(wcs=object()))
    monkeypatch.setattr(ingest_jobs, "_solve_phone_photo_with_candidate", check)
    anet = {"solve_field": "solve-field", "index_dir": "index", "limit_s": 1.0, "program_limit_s": 1.0, "bound_s": 200.0}
    staged = ingest_jobs._solve_from_anet(
        image, [], 320, 240, on_event=lambda *a: None, anet=anet, moment_lent=None, spent_s=0.0, phone_detections=phone,
        camera_meta=False, camera_label=None, fov_hint=None, obs_utc_iso=None, lat_deg=None, lon_deg=None,
        source_asset_id=ASSET, sha256=SHA, solution_id=SOLUTION,
    )
    assert staged is None
    assert handed == [phone.setup] and isinstance(phone.setup, pps.PhoneFrameSetup)


def test_a_candidate_check_on_a_frame_already_set_up_makes_nothing_again(monkeypatch: pytest.MonkeyPatch) -> None:
    image = _frame(seed=3)
    detections = _phone_detections(image)
    setup = pps.PhoneFrameSetup()
    setup.frame(image).ranked_pool(image, detections)
    counts = _counting(monkeypatch)
    wcs = pps.PhoneCameraWCS.from_rotation_and_intrinsics(rot_matrix=np.eye(3), fov_diagonal_deg=60.0, width=320, height=240)
    ingest_jobs._solve_phone_photo_with_candidate(
        image, detections, wcs, obs_utc_iso=None, observer_lat=None, observer_lon=None, camera_label=None,
        source_asset_id=ASSET, source_sha256=SHA, solution_id=SOLUTION, whole_sky=False, _frame_setup=setup,
    )
    assert counts["passes"] == 1 and all(counts[name] == 0 for name in SETUP), counts
