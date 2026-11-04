"""Closed-loop astronomy-guided lunar scattering veil subtraction and WCS-coherent
multi-frame trajectory photometry (`services/science/coherent_stack.py`).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from services.science.catalog import CatalogStar, get_all_sky_catalog_stars
from services.science.detector import _to_luma_2d
from services.science.wcs_models import WCSModel


def subtract_lunar_scattering_veil(
    luma: np.ndarray,
    moon_x_px: float,
    moon_y_px: float,
    sky_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, float]]:
    """Fit and subtract the smooth radial Rayleigh/Mie lunar atmospheric glare profile
    inside `sky_mask > 0.5` outside the lunar core (`r > 18 px`).

    Uses the 35th percentile within concentric radial annuli around `(moon_x_px, moon_y_px)`
    so compact stellar point sources are never absorbed into the background veil model.
    """
    work = _to_luma_2d(luma).copy()
    mask_2d = np.asarray(sky_mask, dtype=np.float64)
    if mask_2d.shape != work.shape:
        raise ValueError(f"sky_mask shape {mask_2d.shape} does not match luma shape {work.shape}")

    h, w = work.shape
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - float(moon_x_px), yy - float(moon_y_px))

    sky_bool = (mask_2d > 0.5) & (rr > 18.0)
    if not np.any(sky_bool):
        return work, {"veilPeakSubtracted": 0.0, "contrastGainRatio": 1.0}

    # Estimate far-field dark sky baseline from the furthest 20% of sky pixels
    r_far = float(np.percentile(rr[sky_bool], 80.0))
    far_pixels = work[sky_bool & (rr >= r_far)]
    sky_floor = float(np.percentile(far_pixels, 25.0)) if far_pixels.size > 0 else float(np.median(work[sky_bool]))

    max_r = float(math.hypot(w, h))
    radii_edges = [18.0, 28.0, 44.0, 68.0, 104.0, 160.0, 240.0, 360.0, max(480.0, max_r)]
    r_centers: list[float] = []
    veil_vals: list[float] = []

    for r_in, r_out in zip(radii_edges[:-1], radii_edges[1:]):
        ann_mask = sky_bool & (rr >= r_in) & (rr < r_out)
        r_mid = 0.5 * (r_in + r_out)
        r_centers.append(r_mid)
        if np.count_nonzero(ann_mask) >= 8:
            excess = max(0.0, float(np.percentile(work[ann_mask], 35.0)) - sky_floor)
            veil_vals.append(excess)
        else:
            veil_vals.append(veil_vals[-1] * 0.65 if veil_vals else 0.0)

    # Enforce monotonically non-increasing radial scattering veil away from the Moon
    for i in range(1, len(veil_vals)):
        veil_vals[i] = min(veil_vals[i], veil_vals[i - 1])

    veil_2d = np.interp(rr, r_centers, veil_vals, left=veil_vals[0], right=0.0)
    # Smoothly taper veil subtraction to 0 inside r <= 18 px so the lunar disk itself is preserved
    core_taper = np.clip((rr - 14.0) / 8.0, 0.0, 1.0)
    sky_taper = np.clip((mask_2d - 0.5) * 2.0, 0.0, 1.0)
    subtracted_veil = veil_2d * core_taper * sky_taper

    cleaned = np.clip(work - subtracted_veil, 0.0, None)
    veil_peak = float(np.max(subtracted_veil))

    # Measure local contrast gain ratio in the circumlunar zone (22 < r < 140 px)
    near_zone = sky_bool & (rr >= 22.0) & (rr <= 140.0)
    if np.count_nonzero(near_zone) >= 16:
        bg_before = max(1e-5, float(np.mean(work[near_zone])))
        bg_after = max(1e-5, float(np.mean(cleaned[near_zone])))
        contrast_gain = max(1.0, bg_before / bg_after)
    else:
        contrast_gain = 1.0 + veil_peak / max(sky_floor, 1e-3)

    return cleaned, {
        "veilPeakSubtracted": round(veil_peak, 5),
        "contrastGainRatio": round(float(contrast_gain), 4),
    }


def _measure_aperture_minus_annulus(
    luma: np.ndarray,
    x_px: float,
    y_px: float,
) -> float | None:
    """Measure net 3x3 PSF core mean minus [5, 9] px background annulus median at `(x_px, y_px)`."""
    h, w = luma.shape
    ix = int(round(x_px))
    iy = int(round(y_px))
    if not (10 <= ix < w - 10 and 10 <= iy < h - 10):
        return None

    core = luma[iy - 1 : iy + 2, ix - 1 : ix + 2]
    win = luma[iy - 9 : iy + 10, ix - 9 : ix + 10]
    wy, wx = np.mgrid[-9:10, -9:10]
    rr = np.hypot(wx, wy)
    annulus = win[(rr >= 5.0) & (rr <= 9.0)]
    if annulus.size == 0:
        return None
    bg_med = float(np.median(annulus))
    return float(np.mean(core)) - bg_med


def integrate_wcs_coherent_star_trajectories(
    frames: Sequence[np.ndarray],
    wcs: WCSModel,
    pose_trajectory: Sequence[dict[str, Any]],
    *,
    wcs_verified: bool,
    original_pixels: bool,
    max_stars: int = 8,
    catalog_stars: Sequence[CatalogStar] | None = None,
) -> list[dict[str, Any]]:
    """Aperture photometry where `wcs` and the per-frame pose offsets put bright catalog stars.

    Projects bright in-FOV catalog stars through `wcs` and per-frame `(duPx, dvPx)` across `frames`, sums a
    3x3 aperture minus a [5, 9] px sky annulus over the frames, and compares the sum with 8 control apertures
    offset by about 14 to 16 px, giving `coherentSnr` and `controlNoiseSigma`.

    The measurement is of whatever lies where the WCS predicts the star. A record says the star was `observed`
    only when the WCS passed held-out verification (`wcs_verified`) and `frames` are the recording's own pixels
    (`original_pixels`); otherwise the star's position is only predicted, and a lamp, an aircraft or a hot pixel
    at that spot would read the same, so the record stays `predicted` and says so.
    """
    if len(frames) == 0:
        return []

    luma_frames = [_to_luma_2d(f) for f in frames]
    h, w = luma_frames[0].shape
    stars = list(catalog_stars) if catalog_stars is not None else get_all_sky_catalog_stars()

    # Find bright catalog stars whose frame-0 WCS projection falls inside the usable sensor interior
    in_fov_candidates: list[tuple[CatalogStar, float, float]] = []
    margin = 18.0
    for st in stars:
        px0, py0 = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
        if math.isnan(px0) or math.isnan(py0):
            continue
        if margin <= px0 < (w - margin) and margin <= py0 < (h - margin):
            in_fov_candidates.append((st, float(px0), float(py0)))

    in_fov_candidates.sort(key=lambda item: item[0].mag)
    selected = in_fov_candidates[: max(1, int(max_stars))]

    control_offsets = [
        (-14.0, -14.0),
        (14.0, -14.0),
        (-14.0, 14.0),
        (14.0, 14.0),
        (-16.0, 0.0),
        (16.0, 0.0),
        (0.0, -16.0),
        (0.0, 16.0),
    ]

    results: list[dict[str, Any]] = []
    for st, px0, py0 in selected:
        target_samples: list[float] = []
        control_trajectories: list[list[float]] = [[] for _ in control_offsets]

        for f_idx, luma in enumerate(luma_frames):
            if f_idx < len(pose_trajectory):
                du = float(pose_trajectory[f_idx].get("duPx", 0.0))
                dv = float(pose_trajectory[f_idx].get("dvPx", 0.0))
            else:
                du, dv = 0.0, 0.0

            cur_x = px0 + du
            cur_y = py0 + dv
            val = _measure_aperture_minus_annulus(luma, cur_x, cur_y)
            if val is not None:
                target_samples.append(val)

            for c_idx, (ox, oy) in enumerate(control_offsets):
                c_val = _measure_aperture_minus_annulus(luma, cur_x + ox, cur_y + oy)
                if c_val is not None:
                    control_trajectories[c_idx].append(c_val)

        if not target_samples:
            continue

        # Coherent trajectory sum / mean across N frames
        n_used = len(target_samples)
        coherent_signal = float(np.mean(target_samples)) * math.sqrt(float(n_used))

        control_means = [
            float(np.mean(c_vals)) * math.sqrt(float(len(c_vals)))
            for c_vals in control_trajectories
            if len(c_vals) > 0
        ]
        if len(control_means) >= 2:
            ctrl_med = float(np.median(control_means))
            ctrl_mad = float(np.median(np.abs(np.asarray(control_means) - ctrl_med)))
            ctrl_sigma = max(1e-5, max(1.4826 * ctrl_mad, float(np.std(control_means))))
        else:
            ctrl_sigma = 1e-3

        coherent_snr = (coherent_signal - (float(np.median(control_means)) if control_means else 0.0)) / ctrl_sigma
        signal = coherent_snr >= 3.0
        confirmed = wcs_verified and original_pixels
        if confirmed:
            status = "coherent_signal_detected" if signal else "sub_threshold_upper_limit"
        else:
            status = "signal_at_predicted_position" if signal else "no_signal_at_predicted_position"

        results.append(
            {
                "starId": st.id,
                "commonName": st.common_name,
                "constellation": st.constellation,
                "mag": float(st.mag),
                "frame0PixelXY": [round(px0, 2), round(py0, 2)],
                "framesIntegrated": int(n_used),
                "coherentSnr": round(float(coherent_snr), 2),
                "controlNoiseSigma": round(float(ctrl_sigma), 6),
                "status": status,
                "wcsVerified": bool(wcs_verified),
                "originalPixels": bool(original_pixels),
                "evidenceClass": "observed" if confirmed and signal else "predicted",
                **(
                    {}
                    if confirmed
                    else {
                        "limitation": (
                            "Measured where an unverified pose or processed pixels put the star; anything bright "
                            "there (a lamp, an aircraft, a hot pixel) reads the same, so the star is not observed."
                        )
                    }
                ),
            }
        )

    return results
