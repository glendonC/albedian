"""WCS frame projection, major-axis pixel span geometry, and predicted deep-sky finding assembly."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from services.science.deep_sky_catalog import (
    LIMITATIONS,
    OPENNGC_LICENCE_NOTE,
    POSITION_PRECISION_ARCSEC,
    VERIFICATION_METHOD,
    _candidate_rows,
    catalog_reference,
    finding_id,
    table_and_columns,
)
from services.science.deep_sky_selection import (
    STAR_TYPES,
    faintest_matched_magnitude,
    keep_reason,
    row_sort_key,
)
from services.science.wcs_models import CelestialFindingRecord, ObservationSolution

_ARCMIN_TO_HALF_AXIS_DEG = 120.0
_POLE_DEC_LIMIT_DEG = 90.0


def _project(solution: ObservationSolution, ra: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wcs = solution.wcs
    batch = getattr(wcs, "world_to_pixel_batch", None)
    if batch is not None:
        x, y = batch(ra, dec)
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    points = [wcs.world_to_pixel(float(r), float(d)) for r, d in zip(ra, dec, strict=True)]
    return np.array([p[0] for p in points], dtype=np.float64), np.array([p[1] for p in points], dtype=np.float64)


@dataclass(frozen=True)
class DeepSkyPrediction:
    """The kept findings, brightest first, and how many objects inside the frame were not included."""

    findings: tuple[CelestialFindingRecord, ...]
    not_included: int

    @property
    def warning(self) -> str | None:
        """The solution warning for the objects not included, worded for what the server knows."""
        if self.not_included == 0:
            return None
        if self.not_included == 1:
            return "1 more catalog object in this frame is not included"
        return f"{self.not_included} more catalog objects in this frame are not included"


def predict_deep_sky_findings(solution: ObservationSolution, width: int, height: int) -> DeepSkyPrediction:
    """The OpenNGC objects whose catalog centre the solution's verified WCS puts inside the `width` x `height`
    frame and that a keep rule holds for, as predicted findings, brightest first. Nothing when the solution is not
    calibrated or its WCS did not pass held-out verification. A row OpenNGC types as a single star, a double star or
    a nova is left to the star catalog, as the predicted sky leaves it, and not counted as an object not included:
    OpenNGC's "gam Cyg" is Sadr itself (star-names-one).
    """
    if solution.status != "calibrated" or not solution.has_verified_wcs:
        return DeepSkyPrediction(findings=(), not_included=0)
    table, at = table_and_columns()
    rows = [row for row in _candidate_rows() if row[at["type"]] not in STAR_TYPES]
    ra = np.array([row[at["raDeg"]] for row in rows], dtype=np.float64)
    dec = np.array([row[at["decDeg"]] for row in rows], dtype=np.float64)
    x, y = _project(solution, ra, dec)
    inside = np.flatnonzero(np.isfinite(x) & np.isfinite(y) & (x >= 0.0) & (x < width) & (y >= 0.0) & (y < height))
    # The major axis in this frame: the pixel distance between the points half an axis north and south of the centre
    half = np.array(
        [(rows[int(i)][at["majorAxisArcmin"]] or math.nan) / _ARCMIN_TO_HALF_AXIS_DEG for i in inside],
        dtype=np.float64,
    )
    xn, yn = _project(solution, ra[inside], np.minimum(_POLE_DEC_LIMIT_DEG, dec[inside] + half))
    xs, ys = _project(solution, ra[inside], np.maximum(-_POLE_DEC_LIMIT_DEG, dec[inside] - half))
    spans = np.hypot(xn - xs, yn - ys)
    faintest = faintest_matched_magnitude(solution)
    kept = [
        rows[int(i)]
        for i, span in zip(inside, spans, strict=True)
        if keep_reason(rows[int(i)], faintest, float(span)) is not None
    ]
    name_idx = at["name"]
    kept.sort(key=lambda row: row_sort_key(row, name_idx))
    findings = []
    for row in kept:
        reference = catalog_reference(row)
        findings.append(
            CelestialFindingRecord(
                id=finding_id(row[name_idx]),
                observation_solution_id=solution.id,
                evidence_class="predicted",
                title=reference["commonName"],
                subtitle=f"{table['types'][row[at['type']]]} · {row[at['constellation']]} · {OPENNGC_LICENCE_NOTE}",
                ra_deg=reference["raDeg"],
                dec_deg=reference["decDeg"],
                uncertainty_arcsec=POSITION_PRECISION_ARCSEC,
                deep_sky_reference=reference,
                verification_method=VERIFICATION_METHOD,
                limitations=LIMITATIONS,
                source_sha256=solution.source_sha256,
            )
        )
    return DeepSkyPrediction(findings=tuple(findings), not_included=len(inside) - len(kept))


__all__ = [
    "DeepSkyPrediction",
    "predict_deep_sky_findings",
]
