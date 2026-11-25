"""A still too large to search within its bound is searched binned, and its solution restated in the stored pixels.

The two public photos above 48 MP of the sixty (rp-b-10, 6144 x 8160, and rp-b-14, 6944 x 9280) stop at the 180 s
bound as stored; binned 3 x 3 both solve within about a minute, their centres within 0.03 deg of astrometry.net's
[tested, .lead/kiro/reports/SCIENCE-INGEST/bin-research.md], and no other of the sixty is above the line. So a still
above BIN_ABOVE_PX is binned by the smallest whole factor k that leaves at most BIN_MAX_PX, detected and searched so,
and its solution is restated in the stored pixels before anything reads it: the WCS, every detection, width and
residual, and every pixel figure in its words. The held-out gate is never loosened by binning: a binned calibration
stands only when its restated held-out quality passes the 3 px gate in stored pixels, and else degrades to unresolved
with pixel-only observed detections so the still serves predicted_only (or approximate when posed) [tested]. A still at
or below the line is searched exactly as before.

A binned pixel b covers stored pixels b k to b k + k - 1, so its centre is the stored (b + 0.5) k - 0.5; a length
(a width, a residual, an uncertainty) is k times as long, an area k squared times as large, and a star's summed flux
k squared times the binned frame's, which holds the blocks' means.
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np

from services.contracts.astronomy import held_out_gate
from services.science.phone_photo_solver import PHONE_SOLVER_IDENTITY
from services.science.solver import GATE_REFUSED_WARNING
from services.science.wcs_models import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SourceDetection,
    StarProfileRecord,
    WCSModel,
)

# Above this a stored still is binned: the two public photos past it stop at the search's bound; the largest still
# of the sixty that solves as stored is 45.4 MP (rp-d-01)
BIN_ABOVE_PX = 48_000_000
# The most a binned still holds: both photos solve binned to 5.6 and 7.2 MP; rp-b-14 binned to 16.1 MP does not
BIN_MAX_PX = 8_000_000


def bin_factor(width: int, height: int) -> int:
    """The whole factor a stored still of `width` x `height` is binned by: 1 at or below BIN_ABOVE_PX, else the
    smallest k from 2 whose whole blocks hold at most BIN_MAX_PX."""
    if width <= 0 or height <= 0:
        raise ValueError("A still's width and height are positive.")
    if width * height <= BIN_ABOVE_PX:
        return 1
    k = 2
    while (width // k) * (height // k) > BIN_MAX_PX:
        k += 1
    return k


def bin_frame(frame: np.ndarray, k: int) -> np.ndarray:
    """The frame's k x k blocks averaged and rounded to whole codes, as a decoded frame holds, in the frame's own dtype
    and channels; the last rows and columns that fill no whole block (fewer than k of each, at the bottom and the
    right) are left out. k = 1 gives the frame itself."""
    if k < 1:
        raise ValueError("A binning factor is a whole number from 1.")
    if k == 1:
        return frame
    a = np.asarray(frame)
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    blocks = a[:h, :w].reshape(h // k, k, w // k, k, *a.shape[2:])
    return np.round(blocks.mean(axis=(1, 3), dtype=np.float64)).astype(a.dtype)


def stored_position(value: float, k: int) -> float:
    """A binned pixel coordinate as the stored frame's: the centre of the block it stands for."""
    return (float(value) + 0.5) * k - 0.5


# A pixel figure in words: a position "(x, y) px", else a length "n px" (never part of a longer number or word)
_PIXEL_FIGURE = re.compile(
    r"\((-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)\) px\b|(?<![\w.])(-?\d+(?:\.\d+)?) px\b"
)


def _as_written(value: float, like: str) -> str:
    decimals = len(like.split(".", 1)[1]) if "." in like else 0
    return f"{value:.{decimals}f}"


def restate_pixels(text: str, k: int) -> str:
    """`text` with every pixel figure in it restated in the stored pixels, each to its own decimals: a position by
    `stored_position`, a length k times. A text with no pixel figure is returned as it is."""
    if k == 1:
        return text

    def stored(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            x, y = match.group(1), match.group(2)
            return f"({_as_written(stored_position(float(x), k), x)}, {_as_written(stored_position(float(y), k), y)}) px"
        n = match.group(3)
        return f"{_as_written(float(n) * k, n)} px"

    return _PIXEL_FIGURE.sub(stored, text)


def _length(value: float | None, k: int) -> float | None:
    return None if value is None else float(value) * k


def _detection(d: SourceDetection, k: int) -> SourceDetection:
    frame = d.frame
    if frame is not None:
        frame = replace(
            frame,
            profile_sigma_major_px=_length(frame.profile_sigma_major_px, k),
            profile_sigma_minor_px=_length(frame.profile_sigma_minor_px, k),
        )
    return replace(
        d,
        x_px=stored_position(d.x_px, k),
        y_px=stored_position(d.y_px, k),
        flux=float(d.flux) * k * k,
        fwhm_px=float(d.fwhm_px) * k,
        centroid_uncertainty_px=float(d.centroid_uncertainty_px) * k,
        saturated_area_px=int(d.saturated_area_px) * k * k,
        frame=frame,
    )


def wcs_in_stored_pixels(w: WCSModel, k: int, width: int, height: int) -> WCSModel:
    """A WCS fitted on a frame binned by `k`, restated for the stored `width` x `height` frame: the reference pixel at
    the centre of its block, the scale k times finer, and for the phone solver's camera WCS (which projects through
    its own focal length in pixels, its distortion on the normalised plane) the focal length k times longer; its
    kind kept."""
    focal = getattr(w, "focal_px", None)
    return replace(
        w,
        crpix_x=stored_position(w.crpix_x, k),
        crpix_y=stored_position(w.crpix_y, k),
        scale_deg_per_px=float(w.scale_deg_per_px) / k,
        width=width,
        height=height,
        **({} if focal is None else {"focal_px": float(focal) * k}),
    )


def _profile(p: StarProfileRecord | None, k: int) -> StarProfileRecord | None:
    return None if p is None else replace(p, sigma_major_px=p.sigma_major_px * k, sigma_minor_px=p.sigma_minor_px * k)


def _finding(f: CelestialFindingRecord, k: int) -> CelestialFindingRecord:
    return replace(
        f,
        title=restate_pixels(f.title, k),
        subtitle=restate_pixels(f.subtitle, k),
        source_detection=None if f.source_detection is None else _detection(f.source_detection, k),
        pixel_residual_px=_length(f.pixel_residual_px, k),
        limitations=tuple(restate_pixels(text, k) for text in f.limitations),
    )


def _is_calibrated_only_warning(text: str) -> bool:
    return " unmatched sensor detection(s): " in text or text.startswith("The stars alone lined this photo up;")


def _refused_finding(
    idx: int,
    det: SourceDetection,
    solution_id: str,
    source_sha256: str,
    limitation: str,
) -> CelestialFindingRecord:
    return CelestialFindingRecord(
        id=f"finding-obs-{idx}",
        observation_solution_id=solution_id,
        evidence_class="observed",
        title=f"Observed Point Source #{idx}",
        subtitle=f"Measured at ({det.x_px:.1f}, {det.y_px:.1f}) px · SNR {det.snr:.1f}",
        source_detection=det,
        verified_by_holdout=False,
        limitations=(limitation,),
        source_sha256=source_sha256,
    )


def in_stored_pixels(solution: ObservationSolution, k: int, width: int, height: int) -> ObservationSolution:
    """The solution of a still searched binned by `k`, restated in its stored `width` x `height` pixels: the WCS and a
    pose's swept WCSs, every finding's detection, residual and words, the quality's pixel residuals and measured star,
    and the warnings' pixel figures. When a binned calibration's restated held-out quality falls short of the 3 px
    held-out gate in stored pixels, the solution degrades cleanly to unresolved with pixel-only observed detections and
    `GATE_REFUSED_WARNING` rather than raising or claiming calibration. k = 1 gives the solution itself."""
    if k == 1:
        return solution
    quality = solution.quality
    detection = None
    median_px: float | None = None
    p95_px: float | None = None
    if quality is not None:
        detection = quality.detection
        if detection is not None:
            detection = replace(detection, star_profile=_profile(detection.star_profile, k))
        median_px = _length(quality.median_residual_px, k)
        p95_px = _length(quality.p95_residual_px, k)
    if solution.status == "calibrated" or (quality is not None and quality.passed_independent_verification):
        least, most = held_out_gate()
        if quality is None or quality.held_out_inliers < least or median_px is None or median_px > most:
            limitation = (
                "Pixel position only: the WCS fit failed coincidence-weighed held-out verification."
                if solution.solver is not None and solution.solver.name == PHONE_SOLVER_IDENTITY.name
                else "Pixel position only: the WCS fit failed held-out verification, so it places nothing on the sky."
            )
            refused_quality = None if quality is None else SolutionQuality(
                detected_candidate_count=quality.detected_candidate_count,
                matched_inlier_count=0,
                held_out_pair_count=quality.held_out_pair_count,
                held_out_inliers=quality.held_out_inliers,
                median_residual_px=median_px,
                p95_residual_px=p95_px,
                median_residual_arcsec=quality.median_residual_arcsec,
                passed_independent_verification=False,
                detection=detection,
            )
            dets = [_detection(f.source_detection, k) for f in solution.findings if f.source_detection is not None]
            kept_warnings = [
                restate_pixels(text, k)
                for text in solution.warnings
                if not _is_calibrated_only_warning(text) and text != GATE_REFUSED_WARNING
            ]
            hint = solution.pointing_hint
            if hint is not None and hint.verified_center_offset_deg is not None:
                hint = replace(hint, verified_center_offset_deg=None)
            return replace(
                solution,
                status="unresolved",
                wcs=None,
                pose_estimate=None,
                pointing_hint=hint,
                quality=refused_quality,
                findings=[
                    _refused_finding(idx, det, solution.id, solution.source_sha256, limitation)
                    for idx, det in enumerate(dets, start=1)
                ],
                warnings=[GATE_REFUSED_WARNING, *kept_warnings],
            )
    if quality is not None:
        quality = replace(
            quality,
            median_residual_px=median_px,
            p95_residual_px=p95_px,
            detection=detection,
        )
    pose = solution.pose_estimate
    if pose is not None:
        pose = replace(
            pose,
            swept_wcs=tuple(wcs_in_stored_pixels(w, k, width, height) for w in pose.swept_wcs),
            assumptions=tuple(restate_pixels(text, k) for text in pose.assumptions),
            fov_source=restate_pixels(pose.fov_source, k),
        )
    return replace(
        solution,
        wcs=None if solution.wcs is None else wcs_in_stored_pixels(solution.wcs, k, width, height),
        quality=quality,
        findings=[_finding(f, k) for f in solution.findings],
        warnings=[restate_pixels(text, k) for text in solution.warnings],
        pose_estimate=pose,
    )


__all__ = [
    "BIN_ABOVE_PX",
    "BIN_MAX_PX",
    "bin_factor",
    "bin_frame",
    "in_stored_pixels",
    "restate_pixels",
    "stored_position",
    "wcs_in_stored_pixels",
]
