"""The solvers the benchmark runs, behind one interface: what an upload gives and what a solver claims.

A solver receives the decoded upload (8-bit RGB, as a JPEG decoder returns it) and the metadata a
phone writes (time, place, camera). It returns whether it claims a verified solution, the WCS it
claims, and the catalog matches behind the claim. The scorer judges the claim against the truth.

Adapters:

- `today-api`: today's solver exactly as `POST /api/science/solve` runs it on `image_luma`:
  Rec. 709 luma of the decoded pixels, `detect_point_sources` with its defaults, then
  `solve_and_verify_field` on the default 18-star Orion and Taurus catalog.
- `today-allsky`: the same on the 71-star all-sky list, to separate catalog coverage from the
  algorithm.
- `horizon-moon`: today's horizon and Moon pose solver, which never claims held-out verification.
- `phone-photo`: the G 12 tile-backed phone-photo solver with attitude triad voting, blind ICRS
  triangle matching, Brown-Conrady / Kannala-Brandt lens distortion refinement, and coincidence-weighed
  held-out verification.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from research.benchmarks.phone_photos.geometry import CameraTruth
from services.science.bounded_worker import BoundedWorker, WorkerFailed, WorkerTimeout


class WcsLike(Protocol):
    def world_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...


class TruthWcs:
    """The ground truth seen as a WCS: what a perfect solver would claim."""

    def __init__(self, camera_truth: CameraTruth) -> None:
        self.camera_truth = camera_truth

    def world_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.camera_truth.icrs_to_pixel(np.atleast_1d(ra_deg), np.atleast_1d(dec_deg))


class _ScalarWcs:
    """Vectorizes a WCS whose `world_to_pixel` takes one direction at a time."""

    def __init__(self, wcs) -> None:
        self.wcs = wcs

    def world_to_pixel(self, ra_deg: np.ndarray, dec_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if hasattr(self.wcs, "world_to_pixel_batch"):
            return self.wcs.world_to_pixel_batch(np.atleast_1d(ra_deg), np.atleast_1d(dec_deg))
        pairs = [self.wcs.world_to_pixel(float(r), float(d)) for r, d in zip(np.atleast_1d(ra_deg), np.atleast_1d(dec_deg))]
        out = np.array(pairs, dtype=np.float64).reshape(-1, 2)
        return out[:, 0], out[:, 1]


@dataclass
class SolverInput:
    rgb: np.ndarray
    metadata: dict


@dataclass
class SolverOutcome:
    status: str
    claimed: bool
    wcs: WcsLike | None
    matches: list[tuple[float, float, float, float]]  # (detection x, detection y, catalog RA, catalog Dec)
    detections: int
    held_out_inliers: int
    self_median_residual_px: float | None
    elapsed_s: float
    error: str | None = None
    extra: dict = field(default_factory=dict)


def _luma(rgb: np.ndarray) -> np.ndarray:
    pixels = np.asarray(rgb, dtype=np.float64) / 255.0
    return 0.2126 * pixels[..., 0] + 0.7152 * pixels[..., 1] + 0.0722 * pixels[..., 2]


def _today(catalog: str) -> Callable[[SolverInput], SolverOutcome]:
    def run(inp: SolverInput) -> SolverOutcome:
        from services.science.catalog import get_all_sky_catalog_stars
        from services.science.solver import detect_point_sources, solve_and_verify_field

        start = time.perf_counter()
        luma = _luma(inp.rgb)
        detections = detect_point_sources(luma)
        h, w = luma.shape
        sha = hashlib.sha256(np.ascontiguousarray(inp.rgb).tobytes()).hexdigest()
        solution = solve_and_verify_field(
            detections, w, h,
            obs_utc_iso=inp.metadata["utc"], observer_lat=inp.metadata["latDeg"], observer_lon=inp.metadata["lonDeg"],
            catalog_stars=get_all_sky_catalog_stars() if catalog == "all-sky" else None,
            source_asset_id=f"frame-{sha[:12]}", source_sha256=sha,
        )
        claimed = solution.has_verified_wcs
        matches = [
            (f.source_detection.x_px, f.source_detection.y_px, f.catalog_star.ra_deg, f.catalog_star.dec_deg)
            for f in solution.findings
            if f.evidence_class == "catalog_matched" and f.source_detection is not None and f.catalog_star is not None
        ]
        median = solution.quality.median_residual_px
        return SolverOutcome(
            status=solution.status,
            claimed=claimed,
            wcs=_ScalarWcs(solution.wcs) if solution.wcs is not None else None,
            matches=matches,
            detections=len(detections),
            held_out_inliers=solution.quality.held_out_inliers,
            self_median_residual_px=median,
            elapsed_s=time.perf_counter() - start,
        )

    return run


def _horizon_moon(inp: SolverInput) -> SolverOutcome:
    from services.science.solver import solve_horizon_and_beacon_pose

    start = time.perf_counter()
    sha = hashlib.sha256(np.ascontiguousarray(inp.rgb).tobytes()).hexdigest()
    solution = solve_horizon_and_beacon_pose(
        _luma(inp.rgb), obs_utc_iso=inp.metadata["utc"], observer_lat=inp.metadata["latDeg"], observer_lon=inp.metadata["lonDeg"],
        source_asset_id=f"frame-{sha[:12]}", source_sha256=sha,
    )
    median = solution.quality.median_residual_px
    return SolverOutcome(
        status=solution.status,
        claimed=solution.has_verified_wcs,
        wcs=_ScalarWcs(solution.wcs) if solution.wcs is not None else None,
        matches=[],
        detections=solution.quality.detected_candidate_count,
        held_out_inliers=solution.quality.held_out_inliers,
        self_median_residual_px=median,
        elapsed_s=time.perf_counter() - start,
    )


def _phone_photo(inp: SolverInput) -> SolverOutcome:
    from services.science.phone_photo_solver import solve_phone_photo

    start = time.perf_counter()
    sha = hashlib.sha256(np.ascontiguousarray(inp.rgb).tobytes()).hexdigest()
    solution = solve_phone_photo(
        inp.rgb,
        obs_utc_iso=inp.metadata.get("utc"),
        observer_lat=inp.metadata.get("latDeg"),
        observer_lon=inp.metadata.get("lonDeg"),
        camera_label=inp.metadata.get("camera"),
        source_asset_id=f"frame-{sha[:12]}",
        source_sha256=sha,
    )
    claimed = solution.has_verified_wcs
    matches = [
        (f.source_detection.x_px, f.source_detection.y_px, f.catalog_star.ra_deg, f.catalog_star.dec_deg)
        for f in solution.findings
        if f.evidence_class == "catalog_matched" and f.source_detection is not None and f.catalog_star is not None
    ]
    return SolverOutcome(
        status=solution.status,
        claimed=claimed,
        wcs=_ScalarWcs(solution.wcs) if solution.wcs is not None else None,
        matches=matches,
        detections=solution.quality.detected_candidate_count,
        held_out_inliers=solution.quality.held_out_inliers,
        self_median_residual_px=solution.quality.median_residual_px,
        elapsed_s=time.perf_counter() - start,
    )


SOLVERS: dict[str, Callable[[SolverInput], SolverOutcome]] = {
    "today-api": _today("pinned"),
    "today-allsky": _today("all-sky"),
    "horizon-moon": _horizon_moon,
    "phone-photo": _phone_photo,
}


def _run_named(name: str, inp: SolverInput) -> SolverOutcome:
    """Run one registered solver; called in the BoundedWorker child, so it is looked up by name there."""
    return SOLVERS[name](inp)


_WORKER: BoundedWorker | None = None


def run_solver(name: str, inp: SolverInput, timeout_s: float) -> SolverOutcome:
    """Run a solver in a child process stopped at a wall-clock limit; a timeout or a crash is an outcome.

    The child stays up across frames and is restarted after a stop. Unlike SIGALRM, the stop holds during a long
    numpy or scipy call and from any thread.
    """
    global _WORKER
    if _WORKER is None:
        _WORKER = BoundedWorker()
    start = time.perf_counter()
    try:
        return _WORKER.call(_run_named, (name, inp), timeout_s=timeout_s)
    except WorkerTimeout:
        return SolverOutcome("timeout", False, None, [], 0, 0, None, time.perf_counter() - start, error=f"over {timeout_s:g} s")
    except WorkerFailed as exc:  # the benchmark records crashes rather than stopping
        return SolverOutcome("error", False, None, [], 0, 0, None, time.perf_counter() - start, error=str(exc)[:300])


def field_errors_px(camera_truth: CameraTruth, wcs: WcsLike, sky_mask: np.ndarray | None, grid: int = 24) -> np.ndarray:
    """Pixel distance between where the truth and a claimed WCS put the sky seen at a grid of pixels."""
    k = camera_truth.camera.intrinsics
    xs = np.linspace(0.0, k.width - 1.0, grid)
    ys = np.linspace(0.0, k.height - 1.0, max(2, int(round(grid * k.height / k.width))))
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    if sky_mask is not None:
        on_sky = sky_mask[np.round(gy).astype(int), np.round(gx).astype(int)]
        if np.count_nonzero(on_sky) >= 8:
            gx, gy = gx[on_sky], gy[on_sky]
    ra, dec = camera_truth.pixel_to_icrs(gx, gy)
    good = np.isfinite(ra)
    if not np.any(good):
        return np.array([math.inf])
    with np.errstate(invalid="ignore"):
        px, py = wcs.world_to_pixel(ra[good], dec[good])
    distance = np.hypot(np.asarray(px) - gx[good], np.asarray(py) - gy[good])
    return np.where(np.isfinite(distance), distance, math.inf)
