"""Camera pose from a bright spot taken to be the Moon and a skyline taken to be level ground, for a given time and
place (spec predicted-sky-for-every-upload, REQ-SKY-4.1, 4.2, 4.4; design component 5).

The pose itself is `services/science/pose_estimate.py` `horizon_moon_pose`: sky-3's Moon test on the sky above the
skyline, the skyline swept over its true altitude (0 to 3 degrees) and its measured row, the field of view fitted
from the two, and the same beacon term as the head-worn pose. The solution is built as `complete_sky` builds an
upload's (`services/science/predicted_sky.py` `horizon_pose_solution`): approximate with its pose estimate, the spot
as an observed finding with no sky position, and the predicted sky; or predicted_only with the predicted sky and the
reason. Clips found on the laptop (`services/media/discovery.py`) and the science router reach it here.
"""

from __future__ import annotations

import math

import numpy as np

from services.science.catalog import require_place
from services.science.detector import _to_luma_2d
from services.science.pose_estimate import MAX_FIT_RESIDUAL_PX
from services.science.predicted_sky import HORIZON_POSE_SOLVER, PREDICTED_STAR_UNCERTAINTY_ARCSEC, horizon_pose_solution
from services.science.wcs_models import ObservationSolution, SolutionQuality, require_source_sha256

__all__ = ["MAX_POSE_FIT_RESIDUAL_PX", "PREDICTED_STAR_UNCERTAINTY_ARCSEC", "solve_horizon_and_beacon_pose"]

# The horizon fit meets four constraints with four pose parameters, so a pose that cannot reproduce them within this
# many pixels did not converge and is refused: the head-worn pose's own limit, which both poses share
MAX_POSE_FIT_RESIDUAL_PX = MAX_FIT_RESIDUAL_PX


def solve_horizon_and_beacon_pose(
    image: np.ndarray,
    obs_utc_iso: str,
    observer_lat: float,
    observer_lon: float,
    *,
    source_asset_id: str,
    source_sha256: str,
    frame_time_sec: float = 0.0,
    solution_id: str = "sol-horizon-beacon",
    media_kind: str = "image",
    full_scale: float = 255.0,
) -> ObservationSolution:
    """Line the predicted sky up over one upright frame from a bright spot taken to be the Moon and a skyline taken to
    be level ground, for the moment `obs_utc_iso` (the frame's own) and the place.

    The pose needs both: the spot fixes where the camera pointed and the skyline fixes its tilt and scale. Without
    either, or when the fit, its field of view or the spot's size is refused, camera pointing is unknown and the result
    is `predicted_only`: the sky predicted for the given time and place, with no WCS and no pixel positions, and the
    reason. `media_kind` "video" names the frame by its time into the clip. `full_scale` is the value of white in
    `image`: 255 for 8-bit codes, 1.0 for a decode normalized to [0, 1] (discovery's); the Moon test counts 8-bit
    codes, so the frame is read on that scale, never guessed from its own values.

    `source_asset_id` and `source_sha256` name the recording `image` comes from; there is no default.
    """
    observer_lat, observer_lon = require_place(observer_lat, observer_lon)
    require_source_sha256(source_sha256)
    if isinstance(full_scale, bool) or not (
        isinstance(full_scale, (int, float)) and math.isfinite(full_scale) and full_scale > 0.0
    ):
        raise ValueError(f"full_scale is the value of white in the image, a positive number (got {full_scale!r}).")
    luma = _to_luma_2d(image) * (255.0 / float(full_scale))
    base = ObservationSolution(
        id=solution_id,
        source_asset_id=source_asset_id,
        source_sha256=source_sha256,
        frame_time_sec=frame_time_sec,
        revision=1,
        status="unresolved",
        wcs=None,
        # The pose looks for one bright spot and a skyline, and for no other point of light
        quality=SolutionQuality(
            detected_candidate_count=1,
            matched_inlier_count=0,
            held_out_pair_count=0,
            held_out_inliers=0,
            median_residual_px=None,
            p95_residual_px=None,
            median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        solver=HORIZON_POSE_SOLVER,
        findings=[],
        warnings=[],
    )
    return horizon_pose_solution(
        base, luma, moment=obs_utc_iso, lat=observer_lat, lon=observer_lon, media_kind=media_kind
    )
