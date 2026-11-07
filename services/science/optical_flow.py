"""Temporal camera pose tracking (`du_px`, `dv_px`, `droll_deg`) across handheld night-sky video frames.

Implements a hybrid optical-flow / registration tracker (`compute_video_pose_trajectory`):
1. Upper-sky celestial beacon centroid tracking (`trackingMode = "beacon_lock"`) when a
   bright celestial beacon (e.g. Moon or bright planet/star with `SNR >= 8.0`) is present.
2. Sub-pixel FFT phase correlation (`_phase_correlation_shift`) on the 2D horizon
   silhouette gradient band (`trackingMode = "silhouette_flow"`) when no bright beacon
   is in the frame, plus left/right horizon tilt estimation for camera roll (`droll_deg`).
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np
from scipy import ndimage

from services.science.detector import _to_luma_2d


def _estimate_horizon_row(luma: np.ndarray) -> float | None:
    """Estimate baseline horizon row from vertical gradient of row 80th-percentile luminance,
    or return `None` when no vertical gradient in `[0.22*h, 0.80*h]` exceeds `1e-5`.
    """
    h, _ = luma.shape
    row_p80 = np.percentile(luma, 80.0, axis=1)
    grad = np.gradient(ndimage.gaussian_filter1d(row_p80, sigma=6.0))
    y_lo = max(4, int(round(h * 0.22)))
    y_hi = min(h - 4, int(round(h * 0.80)))
    if y_hi > y_lo and float(np.max(np.abs(grad[y_lo:y_hi]))) > 1e-5:
        return float(y_lo + int(np.argmax(np.abs(grad[y_lo:y_hi]))))
    return None


def _detect_upper_sky_beacon(
    luma: np.ndarray,
    horizon_y: float | None,
    snr_threshold: float = 8.0,
) -> tuple[float, float, float] | None:
    """Detect a bright celestial beacon in the upper sky (`y < horizon_y - 16` or `y < 0.48*h`) with `SNR >= 8`."""
    h, w = luma.shape
    upper_bound = int(round(h * 0.48))
    if horizon_y is not None:
        upper_bound = min(int(round(horizon_y - 16)), upper_bound)
    sky_limit_y = max(16, upper_bound)
    if sky_limit_y < 12 or w < 12:
        return None

    sky_band = luma[:sky_limit_y, :]
    sky_med = float(np.median(sky_band))
    sky_mad = float(np.median(np.abs(sky_band - sky_med))) * 1.4826
    sky_sigma = max(sky_mad, 1e-4 * max(1.0, float(np.max(luma))))

    smoothed = ndimage.gaussian_filter(sky_band, sigma=1.6, mode="nearest")
    margin = 4
    if sky_limit_y <= 2 * margin or w <= 2 * margin:
        return None

    inner = smoothed[margin : sky_limit_y - margin, margin : w - margin]
    iy, ix = np.unravel_index(int(np.argmax(inner)), inner.shape)
    peak_y = int(iy) + margin
    peak_x = int(ix) + margin
    peak_val = float(smoothed[peak_y, peak_x])
    peak_snr = (peak_val - sky_med) / sky_sigma

    if peak_snr < snr_threshold or peak_val <= sky_med + 1e-4:
        return None

    # Sub-pixel flux-weighted centroid in a 13x13 window around the peak
    r_win = 6
    y0 = max(0, peak_y - r_win)
    y1 = min(sky_limit_y, peak_y + r_win + 1)
    x0 = max(0, peak_x - r_win)
    x1 = min(w, peak_x + r_win + 1)
    patch = np.maximum(sky_band[y0:y1, x0:x1] - (sky_med + 1.5 * sky_sigma), 0.0)
    tot = float(np.sum(patch))
    if tot <= 1e-9:
        patch = np.maximum(sky_band[y0:y1, x0:x1] - sky_med, 0.0)
        tot = float(np.sum(patch))
    if tot <= 1e-9:
        return (float(peak_x), float(peak_y), float(peak_snr))

    yy, xx = np.mgrid[y0:y1, x0:x1]
    bx = float(np.sum(xx * patch) / tot)
    by = float(np.sum(yy * patch) / tot)
    return (bx, by, float(peak_snr))


def _phase_correlation_shift(
    ref_patch: np.ndarray,
    cur_patch: np.ndarray,
    max_shift_px: int = 32,
) -> tuple[float, float]:
    """Compute sub-pixel `(du_px, dv_px)` shift of `cur_patch` relative to `ref_patch`."""
    h, w = ref_patch.shape
    if h < 6 or w < 6:
        return (0.0, 0.0)

    win_y = np.hanning(h)[:, None]
    win_x = np.hanning(w)[None, :]
    window2d = win_y * win_x

    r_zero = (ref_patch - float(np.mean(ref_patch))) * window2d
    c_zero = (cur_patch - float(np.mean(cur_patch))) * window2d

    f_ref = np.fft.rfft2(r_zero)
    f_cur = np.fft.rfft2(c_zero)
    cross = f_cur * np.conj(f_ref)
    cc = np.fft.fftshift(np.fft.irfft2(cross, s=(h, w)))

    cy, cx = h // 2, w // 2
    rad_y = min(max_shift_px, max(1, cy - 2))
    rad_x = min(max_shift_px, max(1, cx - 2))
    y_min, y_max = cy - rad_y, cy + rad_y + 1
    x_min, x_max = cx - rad_x, cx + rad_x + 1

    sub = cc[y_min:y_max, x_min:x_max]
    wy, wx = np.unravel_index(int(np.argmax(sub)), sub.shape)
    py = y_min + int(wy)
    px = x_min + int(wx)

    dy_sub = 0.0
    dx_sub = 0.0
    if 1 <= py < h - 1:
        vm1, v0, vp1 = float(cc[py - 1, px]), float(cc[py, px]), float(cc[py + 1, px])
        denom = 2.0 * (vm1 - 2.0 * v0 + vp1)
        if abs(denom) > 1e-12:
            dy_sub = max(-0.5, min(0.5, (vm1 - vp1) / denom))
    if 1 <= px < w - 1:
        um1, u0, up1 = float(cc[py, px - 1]), float(cc[py, px]), float(cc[py, px + 1])
        denom = 2.0 * (um1 - 2.0 * u0 + up1)
        if abs(denom) > 1e-12:
            dx_sub = max(-0.5, min(0.5, (um1 - up1) / denom))

    dv_px = (py + dy_sub) - cy
    du_px = (px + dx_sub) - cx
    return (float(du_px), float(dv_px))


def _extract_horizon_gradient_feature(luma: np.ndarray, horizon_y: float) -> np.ndarray:
    """Extract a 2D structural gradient band around `horizon_y` for silhouette phase correlation."""
    h, _ = luma.shape
    half_band = max(16, min(64, h // 4))
    y0 = max(0, int(round(horizon_y)) - half_band)
    y1 = min(h, int(round(horizon_y)) + half_band)
    band = ndimage.gaussian_filter(luma[y0:y1, :], sigma=1.2, mode="nearest")
    gy = np.gradient(band, axis=0)
    gx = np.gradient(band, axis=1)
    return gy + 0.5 * np.abs(gx)


def compute_video_pose_trajectory(
    frames: Sequence[np.ndarray],
    base_horizon_y: float | None = None,
    fps: float = 1.0,
) -> list[dict[str, Any]]:
    """Track per-frame `(duPx, dvPx, drollDeg)` camera motion relative to frame 0.

    Uses upper-sky beacon centroid tracking (`"beacon_lock"`) when a bright beacon with
    `SNR >= 8` is detected in both frame 0 and frame `i`, or sub-pixel FFT phase
    correlation on the 2D horizon silhouette gradient band (`"silhouette_flow"`) otherwise.
    """
    if len(frames) == 0:
        return []

    luma_frames = [_to_luma_2d(f) for f in frames]
    ref_luma = luma_frames[0]
    _, w = ref_luma.shape

    horizon_y = float(base_horizon_y) if base_horizon_y is not None else _estimate_horizon_row(ref_luma)
    ref_beacon = _detect_upper_sky_beacon(ref_luma, horizon_y=horizon_y, snr_threshold=8.0)
    if ref_beacon is None and horizon_y is None:
        return []
    ref_horizon_feat = (
        _extract_horizon_gradient_feature(ref_luma, horizon_y=horizon_y) if horizon_y is not None else None
    )

    mid_x = max(4, w // 2)
    ref_left_feat = ref_horizon_feat[:, :mid_x] if ref_horizon_feat is not None else None
    ref_right_feat = ref_horizon_feat[:, mid_x:] if ref_horizon_feat is not None else None

    trajectory: list[dict[str, Any]] = []
    for idx, cur_luma in enumerate(luma_frames):
        time_sec = float(idx) / max(float(fps), 1e-6)
        if idx == 0:
            mode0 = "beacon_lock" if ref_beacon is not None else "silhouette_flow"
            trajectory.append(
                {
                    "frameIndex": 0,
                    "timeSec": 0.0,
                    "duPx": 0.0,
                    "dvPx": 0.0,
                    "drollDeg": 0.0,
                    "trackingMode": mode0,
                }
            )
            continue

        cur_beacon = (
            _detect_upper_sky_beacon(cur_luma, horizon_y=horizon_y, snr_threshold=8.0)
            if ref_beacon is not None
            else None
        )
        cur_horizon_feat = (
            _extract_horizon_gradient_feature(cur_luma, horizon_y=horizon_y) if horizon_y is not None else None
        )

        # Estimate roll from differential vertical shift between left and right horizon halves when a horizon exists
        if (
            cur_horizon_feat is not None
            and ref_left_feat is not None
            and ref_right_feat is not None
        ):
            _, dv_left = _phase_correlation_shift(
                ref_left_feat, cur_horizon_feat[:, :mid_x], max_shift_px=24
            )
            _, dv_right = _phase_correlation_shift(
                ref_right_feat, cur_horizon_feat[:, mid_x:], max_shift_px=24
            )
            baseline_x = max(16.0, 0.5 * float(w))
            droll_deg = math.degrees(math.atan2(dv_right - dv_left, baseline_x))
        else:
            droll_deg = 0.0

        if ref_beacon is not None and cur_beacon is not None:
            du_px = cur_beacon[0] - ref_beacon[0]
            dv_px = cur_beacon[1] - ref_beacon[1]
            mode = "beacon_lock"
        elif ref_horizon_feat is not None and cur_horizon_feat is not None:
            du_px, dv_px = _phase_correlation_shift(
                ref_horizon_feat, cur_horizon_feat, max_shift_px=36
            )
            mode = "silhouette_flow"
        else:
            return []

        trajectory.append(
            {
                "frameIndex": int(idx),
                "timeSec": round(time_sec, 4),
                "duPx": float(du_px),
                "dvPx": float(dv_px),
                "drollDeg": float(droll_deg),
                "trackingMode": mode,
            }
        )

    return trajectory
