"""A solution's free text holds no place and no time: the observer's coordinates and the moment travel only in its
structured fields (observerLocation, observedAtUTC), never in a warning, a finding's title or subtitle, or a
limitation.

The horizon solver used to write the observer's coordinates (to 4 decimals, about 11 m) and the moment into the
warning of every pose it fitted or gave up on, so each discovered clip's solution carried the clip's place and
capture time in free text that the web app keeps in its session.

Covers:
- A horizon solve that fits a pose (approximate) and one that finds no beacon (predicted_only), for the trip guard's
  own synthetic place and moment: no free text holds either, read by the trip guard's own scanner
- The structured fields still carry the place and the moment
- owner_media: no discovered clip's solution holds the clip's own place or capture time in its free text (checked as
  a flag, so a failure prints nothing of the clips)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from services.media.discovery import discover_uploaded_media_report
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.wcs_models import ObservationSolution
from tests.integration.test_no_owner_trip_data import OWNER_MEDIA, SYNTHETIC, read_captures, scan_text

ROOT = Path(__file__).resolve().parents[2]
# The trip guard's own synthetic capture, a place and a moment that point at nobody; the Moon is up there and then
(CAPTURE,) = SYNTHETIC
MOMENT = {
    "obs_utc_iso": CAPTURE.time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "observer_lat": CAPTURE.place[0],
    "observer_lon": CAPTURE.place[1],
}
# A place or a moment written from the observation sits within metres and seconds of it; these bounds leave out a
# chance pairing of unrelated numbers in the same text (an altitude and an azimuth, say)
WRITTEN_PLACE_M = 1_000.0
WRITTEN_TIME_H = 1.0


def _frame(height: int, width: int, *, beacon: bool) -> np.ndarray:
    """A dark sky over a lit foreground, with a compact bright source high in the sky when `beacon`."""
    frame = np.zeros((height, width), dtype=np.float64)
    frame[int(height * 0.55) :, :] = 40.0
    if beacon:
        frame[118:126, 930:938] = 240.0
    return frame


def _solve(frame: np.ndarray) -> ObservationSolution:
    return solve_horizon_and_beacon_pose(
        frame, **MOMENT, source_asset_id="synthetic-night", source_sha256=hashlib.sha256(frame.tobytes()).hexdigest()
    )


def _free_text(solution: ObservationSolution) -> list[str]:
    return [*solution.warnings, *(text for f in solution.findings for text in (f.title, f.subtitle, *f.limitations))]


def _holds_a_written_place_or_time(text: str, captures) -> bool:
    return any(
        (kind == "place" and closeness < WRITTEN_PLACE_M) or (kind == "time" and closeness < WRITTEN_TIME_H)
        for _, kind, closeness in scan_text(text, captures)
    )


@pytest.mark.parametrize(("beacon", "status"), [(True, "approximate"), (False, "predicted_only")])
def test_a_horizon_solution_names_its_place_and_moment_only_in_its_fields(beacon: bool, status: str) -> None:
    solution = _solve(_frame(1792, 1344, beacon=beacon))
    assert solution.status == status
    texts = _free_text(solution)
    assert solution.warnings and texts
    # Each text on its own, so numbers from two texts never pair; any hit at all fails, near or not
    holding = [text for text in texts if scan_text(text, SYNTHETIC)]
    assert holding == []
    payload = solution.to_contract_dict()
    assert payload["observedAtUTC"] == MOMENT["obs_utc_iso"]
    assert payload["observerLocation"] == {"latDeg": MOMENT["observer_lat"], "lonDeg": MOMENT["observer_lon"]}


@pytest.mark.owner_media("data/uploads")
def test_no_discovered_solution_text_holds_its_clips_place_or_capture_time() -> None:
    captures = read_captures(OWNER_MEDIA)
    items, _ = discover_uploaded_media_report(
        ROOT / "data" / "uploads", ROOT / "apps" / "web" / "public", ROOT / "data" / "artifacts"
    )
    solutions = [item.observation_solution for item in items if item.observation_solution is not None]
    solved = len(solutions)
    assert solved > 0, "no discovered clip has a solution to check"
    # Flags with fixed messages: a failure must not print a clip's text, place or time
    leaked = any(_holds_a_written_place_or_time(text, captures) for sol in solutions for text in _free_text(sol))
    assert not leaked, "a discovered solution's free text holds a clip's own place or capture time"
