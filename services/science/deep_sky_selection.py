"""Selection, magnitude ranking, and keep-rule evaluation for OpenNGC frame candidates."""

from __future__ import annotations

import math
from typing import Any

from services.science.deep_sky_catalog import table_and_columns
from services.science.wcs_models import ObservationSolution

# An object whose major axis spans at least this many pixels in the frame is kept
MIN_MAJOR_AXIS_PX = 3.0
_DECISION_MAGNITUDE_COLUMNS = ("vMag", "bMag")


def decision_magnitude(row: list[Any]) -> float | None:
    """The magnitude that decides and orders: V when OpenNGC gives it, else B. Only V is ever shown."""
    _, at = table_and_columns()
    for column in _DECISION_MAGNITUDE_COLUMNS:
        value = row[at[column]]
        if value is not None:
            return float(value)
    return None


def keep_reason(row: list[Any], faintest_matched_magnitude: float | None, major_axis_px: float) -> str | None:
    """Why an object inside the frame is kept ('name', 'messier', 'magnitude' or 'size'), or None when no rule holds.

    `faintest_matched_magnitude` is the faintest star the solution matched (None when it matched none), and
    `major_axis_px` is the object's major axis in this frame's pixels (NaN when OpenNGC gives none).
    """
    _, at = table_and_columns()
    if row[at["commonNames"]]:
        return "name"
    if row[at["messier"]] is not None:
        return "messier"
    magnitude = decision_magnitude(row)
    if magnitude is not None and faintest_matched_magnitude is not None and magnitude <= faintest_matched_magnitude:
        return "magnitude"
    if math.isfinite(major_axis_px) and major_axis_px >= MIN_MAJOR_AXIS_PX:
        return "size"
    return None


def faintest_matched_magnitude(solution: ObservationSolution) -> float | None:
    """The faintest catalog star the solution matched, or None when it matched none."""
    magnitudes = [
        float(f.catalog_star.mag)
        for f in solution.findings
        if f.evidence_class == "catalog_matched" and f.catalog_star is not None
    ]
    return max(magnitudes) if magnitudes else None


def row_sort_key(row: list[Any], name_index: int) -> tuple[bool, float, str]:
    """Brightest known magnitudes first, missing magnitudes last, tied by OpenNGC designation."""
    mag = decision_magnitude(row)
    return (mag is None, 0.0 if mag is None else mag, row[name_index])


__all__ = [
    "MIN_MAJOR_AXIS_PX",
    "decision_magnitude",
    "faintest_matched_magnitude",
    "keep_reason",
    "row_sort_key",
]
