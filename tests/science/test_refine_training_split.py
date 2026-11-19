"""How many of a candidate's tight pairs the phone solver trains its refinement on (FIELD refine-1).

The refinement fits six numbers (three of attitude, the field of view, k1, k2) and the held-out gate judges every pair
it did not see. A cap of 8 training pairs left a wide phone frame's camera model short of stars: on public photos
whose sky the search had found, with astrometry.net's field of view, the held-out medians were 3.45 to 4.52 px against
the gate's 3.0, and training on half the pairs brought rp-b-02, rp-b-22 and rp-b-12 to 1.01 to 2.82 px through the
same gate [tested, .lead/kiro/reports/FIELD/refine-1-diagnosis.md]. The split is now half the pairs, never fewer
than 3 and never leaving fewer than 6 held out, as before; the gate itself is unchanged.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st
import pytest

from services.science import phone_photo_solver as pps


@pytest.mark.parametrize(
    ("pairs", "train"),
    [(6, 3), (9, 3), (12, 6), (14, 7), (16, 8), (17, 8), (20, 10), (40, 20), (61, 30), (100, 50)],
)
def test_half_the_pairs_train_and_at_least_six_are_held_out(pairs: int, train: int) -> None:
    assert pps._training_count(pairs) == train


@settings(max_examples=200, deadline=None)
@given(pairs=st.integers(min_value=6, max_value=400))
def test_pbt_refine_split_holds_out_six_or_more_and_trains_on_no_more_than_half(pairs: int) -> None:
    train = pps._training_count(pairs)
    assert 3 <= train <= pairs // 2 or (train == 3 and pairs < 9)
    assert pairs - train >= 6 or train == 3


def test_a_solve_trains_its_refinement_on_the_split(monkeypatch: pytest.MonkeyPatch) -> None:
    # The benchmark's rendered Orion field ci-00 calibrates; its first refinement is trained on what the split says
    import hashlib

    from research.benchmarks.phone_photos import render, stars, suite

    asked: list[int] = []
    refined: list[int] = []
    real_split, real_refine = pps._training_count, pps._refine_phone_camera_wcs

    def split(pairs: int) -> int:
        asked.append(pairs)
        return real_split(pairs)

    def refine(initial, pairs, *, fit_distortion=None):
        refined.append(len(pairs))
        return real_refine(initial, pairs, fit_distortion=fit_distortion)

    monkeypatch.setattr(pps, "_training_count", split)
    monkeypatch.setattr(pps, "_refine_phone_camera_wcs", refine)
    frame = render.render_frame(suite.build_suite("ci")[0], stars.TileCatalog())
    meta = frame.truth.metadata
    solution = pps.solve_phone_photo(
        frame.rgb, obs_utc_iso=meta["utc"], observer_lat=meta["latDeg"], observer_lon=meta["lonDeg"],
        camera_label=meta["camera"], source_asset_id="src_ci00", source_sha256=hashlib.sha256(frame.jpeg_bytes).hexdigest(),
    )
    assert solution.status == "calibrated"
    assert asked and refined[0] == real_split(asked[0]) and refined[0] > 8
