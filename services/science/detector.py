"""Astronomical point-source extraction, motion-compensated video frame stacking,
and synthetic starfield rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
from scipy import ndimage

from services.science.catalog import CatalogStar, get_catalog_stars
from services.science.wcs_models import (
    SourceDetection,
    VideoStackCalibrationResult,
    WCSModel,
)


def _to_luma_2d(image: np.ndarray) -> np.ndarray:
    """Convert a 2D grayscale or 3D RGB array to a 2D float64 luminance array."""
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
    raise ValueError(f"Expected 2D or 3D image array, got shape {arr.shape}")


# Rec. 709 luma weights, as _to_luma_2d uses them
LUMA_WEIGHTS = (0.2126, 0.7152, 0.0722)
# Values sampled to measure the value step; np.unique on a million values takes a fraction of a second
STEP_SAMPLE = 1_000_000
# How far a gap between neighbouring values may sit from a whole number of steps: limited-range video codes widened
# to 16 bits land 299 or 300 apart (0.3 %), while continuous values give gaps of no common step at all
STEP_TOLERANCE = 0.05
# The smallest step, relative to the largest value, that is the data's and not float rounding: the smallest gap in a
# sub-pixel-shifted stack is float rounding, 1e-16 of its values or less, while 16-bit data averaged over a thousand
# frames still steps 1.5e-8 of its range
STEP_RELATIVE_MIN = 1e-9
# The old code's absolute floor on the noise, kept so the floor never falls below it on any scale
ABSOLUTE_NOISE_FLOOR = 1e-3


def _value_step(values: np.ndarray) -> float | None:
    """The grid the values sit on, measured, or None when they sit on no grid.

    The smallest positive gap between the sorted unique sampled values, accepted only when it is at least
    STEP_RELATIVE_MIN of the largest absolute value and every gap between neighbouring values is a whole number of
    steps within STEP_TOLERANCE: 1 for 8-bit integers, 257 or 256 for 8-bit data widened to 16 bits, 1/255 for 8-bit
    data scaled to 0..1, and about 299 for a video's limited-range luma widened to full-range 16 bits (its codes land
    299 or 300 apart). An average of N frames sits on a grid N times finer; an interpolated frame sits on none.
    """
    flat = np.asarray(values, dtype=np.float64).ravel()
    flat = flat[np.isfinite(flat)]
    if flat.size > STEP_SAMPLE:
        flat = flat[:: flat.size // STEP_SAMPLE]
    unique = np.unique(flat)
    if unique.size < 2:
        return None
    gaps = np.diff(unique)
    step = float(np.min(gaps))
    if not step > STEP_RELATIVE_MIN * max(abs(float(unique[0])), abs(float(unique[-1]))):
        return None
    multiples = gaps / step
    if float(np.max(np.abs(multiples - np.rint(multiples)))) > STEP_TOLERANCE:
        return None
    return step


def _quantization_sigma(image: np.ndarray) -> float:
    """The rounding noise of the luma the detector sees: its value step / sqrt(12), 0 when it sits on no grid.

    For RGB the step is measured on the channels; a grey RGB frame's luma is its channel, and a colour frame's luma
    mixes three rounded channels, whose rounding noise the luma weights scale.
    """
    arr = np.asarray(image)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        channels = arr[:, :, :3]
        step = _value_step(channels)
        if step is None:
            return 0.0
        grey = np.array_equal(channels[:, :, 0], channels[:, :, 1]) and np.array_equal(channels[:, :, 1], channels[:, :, 2])
        weight = 1.0 if grey else float(np.sqrt(sum(w * w for w in LUMA_WEIGHTS)))
        return step * weight / float(np.sqrt(12.0))
    step = _value_step(arr)
    return 0.0 if step is None else step / float(np.sqrt(12.0))


def _effective_sigma(sigma_bg: float, image: np.ndarray, floor: float = 0.0) -> float:
    """The noise to divide by: never below the rounding noise of the data's own value step, nor below the old code's
    absolute floor.

    A dark 8-bit frame's MAD is 0 (over half its pixels share one value), so its rounding noise carries it. A frame
    on no grid, such as a stack or the Rec. 709 luma of a colour photo on the 0..1 scale that solve_phone_photo hands
    the detector, keeps the old floor of ABSOLUTE_NOISE_FLOOR. Since `sigma_bg` from _estimate_background_and_noise is
    never below the old code's estimate, this is never below the old code's max(estimate, ABSOLUTE_NOISE_FLOOR).
    `floor` carries a rounding noise from elsewhere: injection recovery adds synthetic stars that move pixels off
    the frame's grid, and passes the frame's own rounding noise so the injected image is measured as the frame was.
    """
    return max(float(sigma_bg), _quantization_sigma(image), ABSOLUTE_NOISE_FLOOR, float(floor))


def _estimate_background_and_noise(
    luma: np.ndarray, sky_mask: np.ndarray | None = None, *, interquartile_only: bool = False
) -> tuple[np.ndarray, float]:
    """Estimate smooth local background via fast strided median filtering and MAD noise floor.

    When the MAD is 0 (over half the residuals share one value, as on a dark 8-bit frame), the noise is the larger of
    the interquartile range / 1.349, the old code's fallback, and the spread of the residuals without their top
    percent, so it is never below the old code's estimate. `interquartile_only` keeps the old fallback alone: frame
    registration in stack_video_frames_for_calibration uses it, so a stack and its shifts stay as they were.
    """
    h, w = luma.shape
    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    if mask_bool is not None and np.any(mask_bool):
        sky_med = float(np.median(luma[mask_bool]))
        work_luma = np.where(mask_bool, luma, sky_med)
    else:
        work_luma = luma

    stride = max(1, min(4, min(h, w) // 32))
    small = work_luma[::stride, ::stride]
    small_bg = ndimage.median_filter(small, size=7, mode="nearest")
    bg = ndimage.zoom(
        small_bg,
        (h / small_bg.shape[0], w / small_bg.shape[1]),
        order=1,
        mode="nearest",
    )[:h, :w]

    residual = work_luma - bg
    if mask_bool is not None:
        valid = residual[mask_bool]
    else:
        valid = residual.ravel()

    if valid.size == 0:
        return bg, 0.0

    med = float(np.median(valid))
    deviation = np.abs(valid - med)
    mad = float(np.median(deviation))
    sigma_bg = 1.4826 * mad
    if sigma_bg < 1e-7:
        q75, q25 = np.percentile(valid, [75.0, 25.0])
        sigma_bg = float((q75 - q25) / 1.349)
        if not interquartile_only:
            # On flat integer data the interquartile range is 0 when the middle half of the residuals share one value,
            # and a whole level when over a quarter sit a level above the rest. The spread of the residuals without
            # their top percent, where the stars are, carries the first case; taking the larger keeps the old estimate
            # wherever it was higher
            kept = valid[deviation <= np.percentile(deviation, 99.0)]
            spread = float(np.std(kept)) if kept.size > 1 else 0.0
            sigma_bg = max(sigma_bg, spread)
    return bg, float(sigma_bg)


def _build_gaussian_psf_kernel(sigma_px: float = 1.15, radius: int = 3) -> np.ndarray:
    """Create a zero-mean-free unit-L2-norm 2D Gaussian matched filter kernel."""
    grid = np.arange(-radius, radius + 1, dtype=np.float64)
    xx, yy = np.meshgrid(grid, grid)
    kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma_px * sigma_px))
    kernel[xx * xx + yy * yy > (radius + 0.5) ** 2] = 0.0
    norm = float(np.sqrt(np.sum(kernel * kernel)))
    return kernel / max(norm, 1e-12)


@dataclass(frozen=True)
class InjectionRecoveryBin:
    """Completeness measured in one injected-SNR bin."""

    snr_min: float
    snr_max: float
    injected_count: int
    recovered_count: int
    completeness: float


@dataclass(frozen=True)
class InjectionRecoveryReport:
    """Completeness, false-positive rate, and centroid accuracy from synthetic PSF injection-recovery."""

    injected_count: int
    recovered_count: int
    baseline_detection_count: int
    spurious_count: int
    completeness: float
    false_positive_rate: float
    median_centroid_error_px: float | None
    bins: tuple[InjectionRecoveryBin, ...]


def _measure_peak_photometry(
    residual: np.ndarray,
    snr_map: np.ndarray,
    sigma_eff: float,
    psf_core3: np.ndarray,
    psf_core3_norm: float,
    y0: int,
    x0: int,
    snr_threshold: float,
    asset_id: str | None,
    frame_time_sec: float,
    *,
    allow_bright_impulse: bool = True,
) -> SourceDetection | None:
    """Measure forced aperture and PSF-coherence photometry at `(y0, x0)` on `residual`."""
    patch3 = residual[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2]
    patch5 = residual[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]

    peak_raw_snr = float(residual[y0, x0] / sigma_eff)
    matched_snr = float(snr_map[y0, x0])
    signed_ap3_snr = float(np.sum(patch3) / (3.0 * sigma_eff))

    cardinal_vals = [
        residual[y0 - 1, x0],
        residual[y0 + 1, x0],
        residual[y0, x0 - 1],
        residual[y0, x0 + 1],
    ]
    cardinal_pos = sum(1 for v in cardinal_vals if v > 0.65 * sigma_eff)
    cardinal_strong = sum(1 for v in cardinal_vals if v > 1.25 * sigma_eff)

    patch3_pos = np.maximum(0.0, patch3)
    patch3_norm = float(np.linalg.norm(patch3_pos))
    psf_corr = (
        float(np.sum(patch3_pos * psf_core3)) / (patch3_norm * psf_core3_norm)
        if patch3_norm > 1e-9
        else 0.0
    )

    is_real_psf = (
        peak_raw_snr >= max(2.3, 0.58 * snr_threshold)
        and signed_ap3_snr >= max(3.6, 0.92 * snr_threshold)
        and cardinal_pos == 4
        and cardinal_strong >= 3
        and psf_corr >= 0.84
    )
    is_bright_impulse = allow_bright_impulse and (peak_raw_snr >= 14.0)
    if not (is_real_psf or is_bright_impulse):
        return None

    weights = np.maximum(0.0, patch5 - 0.75 * sigma_eff)
    total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        weights = np.maximum(0.0, patch5)
        total_w = float(np.sum(weights))
    if total_w <= 1e-9:
        return None

    pos_patch5 = np.maximum(0.0, patch5)
    flux = float(np.sum(weights))
    peak_val = float(np.max(pos_patch5))
    peak_ratio = peak_val / max(float(np.sum(pos_patch5)), 1e-9)

    yy, xx = np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
    cx = float(np.sum(xx * weights) / total_w)
    cy = float(np.sum(yy * weights) / total_w)

    r2_moment = float(np.sum(weights * ((xx - cx) ** 2 + (yy - cy) ** 2)) / total_w)
    fwhm_px = 1.6651092223153954 * math.sqrt(max(0.0, r2_moment))

    snr_final = matched_snr
    centroid_unc = max(0.03, min(1.5, fwhm_px / max(snr_final, 1.0)))

    return SourceDetection(
        x_px=cx,
        y_px=cy,
        flux=flux,
        snr=snr_final,
        fwhm_px=fwhm_px,
        centroid_uncertainty_px=centroid_unc,
        asset_id=asset_id,
        frame_time_sec=frame_time_sec,
        peak_to_total_ratio=peak_ratio,
    )


def detect_point_sources(
    image_luma: np.ndarray,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = 3.5,
    asset_id: str | None = None,
    frame_time_sec: float = 0.0,
    noise_floor: float = 0.0,
) -> list[SourceDetection]:
    """Extract astronomical point sources above local sky background noise.

    Performs local background subtraction, MAD noise estimation, matched Gaussian PSF
    filtering, local peak extraction, anti-white-noise spatial coherence gating, and
    sub-pixel centroid + FWHM measurement.

    Each detection carries `asset_id` when the caller names the recording it was measured in, and none
    otherwise: a solution serializes it under its own recording's id.
    """
    luma = _to_luma_2d(image_luma)
    h, w = luma.shape
    if h < 12 or w < 12:
        return []

    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask)
    residual = luma - bg
    max_res = float(np.max(residual))

    if max_res <= 1e-5 or (sigma_bg <= 1e-6 and max_res <= 1e-3):
        return []

    sigma_eff = _effective_sigma(sigma_bg, image_luma, noise_floor)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    matched = ndimage.convolve(residual, kernel, mode="nearest")
    snr_map = matched / sigma_eff

    effective_snr_gate = max(float(snr_threshold), 4.75)
    local_max = ndimage.maximum_filter(snr_map, size=7, mode="nearest")
    is_peak = (snr_map == local_max) & (snr_map >= effective_snr_gate) & (residual > 0.0)

    margin = 4
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False

    if sky_mask is not None:
        mask_bool = np.asarray(sky_mask, dtype=bool)
        is_peak &= mask_bool

    peak_ys, peak_xs = np.nonzero(is_peak)
    if peak_ys.size == 0:
        return []

    psf_core3 = kernel[2:5, 2:5]
    psf_core3_norm = float(np.linalg.norm(psf_core3))

    detections: list[SourceDetection] = []
    for y0, x0 in zip(peak_ys.tolist(), peak_xs.tolist()):
        det = _measure_peak_photometry(
            residual,
            snr_map,
            sigma_eff,
            psf_core3,
            psf_core3_norm,
            y0,
            x0,
            snr_threshold,
            asset_id,
            frame_time_sec,
            allow_bright_impulse=True,
        )
        if det is not None:
            detections.append(det)

    detections.sort(key=lambda d: d.snr, reverse=True)
    return detections


def verify_candidates_on_original_pixels(
    original_image: np.ndarray,
    proposed_candidates: Sequence[SourceDetection | tuple[float, float]],
    *,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = 3.5,
    asset_id: str | None = None,
    frame_time_sec: float = 0.0,
    noise_floor: float = 0.0,
) -> list[SourceDetection]:
    """Verify candidate positions proposed by a processed `MediaVariant` via forced photometry on `original_image`.

    A source seen only in a denoised or sharpened variant is rejected and never becomes `observed`.
    Every surviving `SourceDetection` takes its sub-pixel centroid, flux, FWHM, and SNR strictly from
    `original_image`.
    """
    luma = _to_luma_2d(original_image)
    h, w = luma.shape
    if h < 12 or w < 12 or not proposed_candidates:
        return []

    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask)
    residual = luma - bg
    max_res = float(np.max(residual))
    if max_res <= 1e-5 or (sigma_bg <= 1e-6 and max_res <= 1e-3):
        return []

    sigma_eff = _effective_sigma(sigma_bg, original_image, noise_floor)
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    matched = ndimage.convolve(residual, kernel, mode="nearest")
    snr_map = matched / sigma_eff
    effective_snr_gate = max(float(snr_threshold), 4.75)

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    psf_core3 = kernel[2:5, 2:5]
    psf_core3_norm = float(np.linalg.norm(psf_core3))
    margin = 4

    seen_peaks: set[tuple[int, int]] = set()
    verified: list[SourceDetection] = []

    for cand in proposed_candidates:
        if isinstance(cand, SourceDetection):
            cand_x, cand_y = float(cand.x_px), float(cand.y_px)
            resolved_asset_id = asset_id if asset_id is not None else cand.asset_id
        else:
            cand_x, cand_y = float(cand[0]), float(cand[1])
            resolved_asset_id = asset_id

        xc, yc = int(round(cand_x)), int(round(cand_y))
        if not (margin <= xc < w - margin and margin <= yc < h - margin):
            continue

        y_lo, y_hi = max(margin, yc - 2), min(h - margin, yc + 3)
        x_lo, x_hi = max(margin, xc - 2), min(w - margin, xc + 3)
        win = snr_map[y_lo:y_hi, x_lo:x_hi]
        if win.size == 0:
            continue
        wy, wx = np.unravel_index(int(np.argmax(win)), win.shape)
        y0, x0 = y_lo + int(wy), x_lo + int(wx)

        if (y0, x0) in seen_peaks:
            continue
        if mask_bool is not None and not bool(mask_bool[y0, x0]):
            continue
        if float(snr_map[y0, x0]) < effective_snr_gate or float(residual[y0, x0]) <= 0.0:
            continue
        # Require `(y0, x0)` to be a genuine 3x3 peak on the original SNR map
        if float(snr_map[y0, x0]) < float(np.max(snr_map[y0 - 1 : y0 + 2, x0 - 1 : x0 + 2])):
            continue

        det = _measure_peak_photometry(
            residual,
            snr_map,
            sigma_eff,
            psf_core3,
            psf_core3_norm,
            y0,
            x0,
            snr_threshold,
            resolved_asset_id,
            frame_time_sec,
            allow_bright_impulse=False,
        )
        if det is not None and not det.is_hot_pixel_impulse:
            seen_peaks.add((y0, x0))
            verified.append(det)

    verified.sort(key=lambda d: d.snr, reverse=True)
    return verified


def _extract_registration_signal(
    res: np.ndarray, kernel: np.ndarray, sig_eff: float
) -> np.ndarray:
    """Extract clean PSF-coherent stellar signal map for sub-pixel frame registration."""
    matched = ndimage.convolve(res, kernel, mode="constant", cval=0.0)
    snr_m = matched / sig_eff
    loc_max = ndimage.maximum_filter(snr_m, size=9, mode="constant", cval=0.0)
    c_up = np.roll(res, 1, axis=0) > 0.35 * sig_eff
    c_dn = np.roll(res, -1, axis=0) > 0.35 * sig_eff
    c_lf = np.roll(res, 1, axis=1) > 0.35 * sig_eff
    c_rt = np.roll(res, -1, axis=1) > 0.35 * sig_eff
    is_pk = (snr_m == loc_max) & (snr_m > 2.8) & c_up & c_dn & c_lf & c_rt
    is_pk[:8, :] = False
    is_pk[-8:, :] = False
    is_pk[:, :8] = False
    is_pk[:, -8:] = False

    py_arr, px_arr = np.nonzero(is_pk)
    clean_sig = np.zeros_like(matched)
    if py_arr.size > 0:
        vals = snr_m[py_arr, px_arr]
        top_idx = np.argsort(vals)[::-1][:10]
        for idx_p in top_idx:
            yy0, xx0 = int(py_arr[idx_p]), int(px_arr[idx_p])
            w_val = float(vals[idx_p]) ** 2
            clean_sig[yy0 - 3 : yy0 + 4, xx0 - 3 : xx0 + 4] += w_val * kernel
    return clean_sig


def _estimate_subpixel_shift_phase_corr(
    ref_signal: np.ndarray,
    target_signal: np.ndarray,
    prior_yx: tuple[float, float] = (0.0, 0.0),
    search_radius: int = 8,
) -> tuple[float, float]:
    """Estimate (dy, dx) shift bringing `target_signal` into alignment with `ref_signal`."""
    h, w = ref_signal.shape
    f_ref = np.fft.rfft2(ref_signal)
    f_tar = np.fft.rfft2(target_signal)
    cross_power = f_ref * np.conj(f_tar)
    cc = np.fft.irfft2(cross_power, s=(h, w))
    cc_shifted = np.fft.fftshift(cc)

    cy, cx = h // 2, w // 2
    center_y = int(round(cy + prior_yx[0]))
    center_x = int(round(cx + prior_yx[1]))
    y_min = max(1, center_y - search_radius)
    y_max = min(h - 1, center_y + search_radius + 1)
    x_min = max(1, center_x - search_radius)
    x_max = min(w - 1, center_x + search_radius + 1)
    window = cc_shifted[y_min:y_max, x_min:x_max]

    wy, wx = np.unravel_index(int(np.argmax(window)), window.shape)
    py = y_min + int(wy)
    px = x_min + int(wx)

    dy_sub = 0.0
    dx_sub = 0.0
    if 1 <= py < h - 1:
        v_m1, v_0, v_p1 = cc_shifted[py - 1, px], cc_shifted[py, px], cc_shifted[py + 1, px]
        denom = 2.0 * (v_m1 - 2.0 * v_0 + v_p1)
        if abs(denom) > 1e-12:
            dy_sub = max(-0.5, min(0.5, float((v_m1 - v_p1) / denom)))
    if 1 <= px < w - 1:
        u_m1, u_0, u_p1 = cc_shifted[py, px - 1], cc_shifted[py, px], cc_shifted[py, px + 1]
        denom = 2.0 * (u_m1 - 2.0 * u_0 + u_p1)
        if abs(denom) > 1e-12:
            dx_sub = max(-0.5, min(0.5, float((u_m1 - u_p1) / denom)))

    dy = (py + dy_sub) - cy
    dx = (px + dx_sub) - cx
    return (float(dy), float(dx))


def stack_video_frames_for_calibration(
    frames: Sequence[np.ndarray],
    sky_mask: np.ndarray | None = None,
) -> VideoStackCalibrationResult:
    """Align and stack low-SNR video frames via running-reference PSF cross-correlation (`EXP-02`)."""
    if len(frames) == 0:
        raise ValueError("frames must be a non-empty sequence of 2D/3D arrays.")

    # Every noise here is the old interquartile estimate: frame registration weights each frame's signal by it, and the
    # spread fallback moved a real clip's shifts by up to 1 px [measured on the owner's clips, outside the tests]
    luma_frames = [_to_luma_2d(f) for f in frames]
    if len(luma_frames) == 1:
        _, sigma0 = _estimate_background_and_noise(luma_frames[0], sky_mask, interquartile_only=True)
        return VideoStackCalibrationResult(
            composite_luma=luma_frames[0].copy(),
            shifts_yx=[(0.0, 0.0)],
            single_frame_noise_sigma=sigma0,
            stacked_noise_sigma=sigma0,
            snr_boost_factor=1.0,
        )

    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None
    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)

    signal_maps: list[np.ndarray] = []
    bg_frames: list[np.ndarray] = []
    residuals: list[np.ndarray] = []
    sigmas: list[float] = []

    for frame in luma_frames:
        bg, sig = _estimate_background_and_noise(frame, mask_bool, interquartile_only=True)
        sig_eff = max(sig, 1e-4)
        res = frame - bg
        res_masked = np.where(mask_bool, res, 0.0) if mask_bool is not None else res
        clean_sig = _extract_registration_signal(res_masked, kernel, sig_eff)
        signal_maps.append(clean_sig)
        bg_frames.append(bg)
        residuals.append(res_masked)
        sigmas.append(sig_eff)

    running_ref_sig = signal_maps[0].copy()
    aligned_residuals: list[np.ndarray] = [residuals[0]]
    shifts_yx: list[tuple[float, float]] = [(0.0, 0.0)]
    prev_shift = (0.0, 0.0)

    for idx in range(1, len(luma_frames)):
        dy, dx = _estimate_subpixel_shift_phase_corr(
            running_ref_sig, signal_maps[idx], prior_yx=prev_shift, search_radius=8
        )
        shifts_yx.append((dy, dx))
        prev_shift = (dy, dx)
        shifted_res = ndimage.shift(residuals[idx], shift=(dy, dx), order=1, mode="nearest")
        aligned_residuals.append(shifted_res)
        shifted_sig = ndimage.shift(signal_maps[idx], shift=(dy, dx), order=1, mode="constant", cval=0.0)
        running_ref_sig = running_ref_sig + shifted_sig

    stack_arr = np.stack(aligned_residuals, axis=0)
    composite_res = np.mean(stack_arr, axis=0)
    composite_luma = np.clip(bg_frames[0] + composite_res, 0.0, None)
    if mask_bool is not None:
        composite_luma = np.where(mask_bool, composite_luma, luma_frames[0])

    _, stacked_sigma = _estimate_background_and_noise(composite_luma, mask_bool, interquartile_only=True)
    single_sigma = float(np.median(sigmas))
    snr_boost = single_sigma / max(stacked_sigma, 1e-6)

    return VideoStackCalibrationResult(
        composite_luma=composite_luma,
        shifts_yx=shifts_yx,
        single_frame_noise_sigma=single_sigma,
        stacked_noise_sigma=stacked_sigma,
        snr_boost_factor=float(snr_boost),
    )


def render_synthetic_starfield(
    width: int = 960,
    height: int = 540,
    wcs: WCSModel | None = None,
    catalog_stars: Sequence[CatalogStar] | None = None,
    psf_sigma_px: float = 1.15,
    background_level: float = 24.0,
    read_noise_sigma: float = 2.0,
    flux_scale: float = 950.0,
    seed: int = 42,
    sky_mask: np.ndarray | None = None,
    shift_yx: tuple[float, float] = (0.0, 0.0),
) -> tuple[np.ndarray, WCSModel, list[tuple[CatalogStar, float, float]]]:
    """Synthesize a test starfield frame with known WCS, radial distortion, Gaussian read noise,
    and compressed empirical flux scaling `flux_scale * 10**(-0.14 * (mag - 1.5))` (not Pogson's `10**(-0.4 * mag)`).
    """
    if wcs is None:
        wcs = WCSModel(
            crval_ra_deg=84.0,
            crval_dec_deg=6.0,
            crpix_x=width / 2.0,
            crpix_y=height / 2.0,
            scale_deg_per_px=0.062 * (960.0 / float(width)),
            rotation_deg=4.5,
            k1=-0.035,
            k2=0.008,
            parity=-1,
            width=width,
            height=height,
        )

    stars = list(catalog_stars) if catalog_stars is not None else get_catalog_stars()
    rng = np.random.default_rng(seed)
    image = np.full((height, width), background_level, dtype=np.float64)

    projected_in_frame: list[tuple[CatalogStar, float, float]] = []
    dy_shift, dx_shift = shift_yx
    margin = 10

    for st in stars:
        x_px, y_px = wcs.world_to_pixel(st.ra_deg, st.dec_deg)
        if math.isnan(x_px) or math.isnan(y_px):
            continue
        x_s = x_px + dx_shift
        y_s = y_px + dy_shift
        if not (margin <= x_s < width - margin and margin <= y_s < height - margin):
            continue
        if sky_mask is not None and not bool(sky_mask[int(round(y_s)), int(round(x_s))]):
            continue

        projected_in_frame.append((st, x_s, y_s))
        star_flux = flux_scale * (10.0 ** (-0.14 * (st.mag - 1.5)))

        x0 = int(round(x_s))
        y0 = int(round(y_s))
        r_win = 6
        y_slice = slice(max(0, y0 - r_win), min(height, y0 + r_win + 1))
        x_slice = slice(max(0, x0 - r_win), min(width, x0 + r_win + 1))
        yy, xx = np.mgrid[y_slice, x_slice]

        psf = np.exp(-((xx - x_s) ** 2 + (yy - y_s) ** 2) / (2.0 * psf_sigma_px * psf_sigma_px))
        psf /= max(float(np.sum(psf)), 1e-12)
        image[y_slice, x_slice] += star_flux * psf

    if read_noise_sigma > 0.0:
        image += rng.normal(0.0, read_noise_sigma, size=image.shape)

    if sky_mask is not None:
        mask_bool = np.asarray(sky_mask, dtype=bool)
        image = np.where(mask_bool, image, background_level * 0.35)

    return np.clip(image, 0.0, None), wcs, projected_in_frame


def evaluate_injection_recovery(
    base_image: np.ndarray,
    *,
    variant_image: np.ndarray | None = None,
    injected_snrs: Sequence[float] = (3.0, 4.5, 6.0, 8.0, 12.0, 18.0),
    n_per_snr: int = 8,
    psf_sigma_px: float = 1.15,
    snr_threshold: float = 3.5,
    match_radius_px: float = 2.0,
    seed: int = 42,
    sky_mask: np.ndarray | None = None,
) -> InjectionRecoveryReport:
    """Measure point-source completeness and false-positive rate via synthetic PSF injection-recovery.

    When `variant_image` is provided, candidates are proposed on the variant with injected sources and must
    pass `verify_candidates_on_original_pixels` on the original image with the same injected sources, so any
    variant-only artifact present only in `variant_image` is tested against forced photometry on the original.
    """
    luma = _to_luma_2d(base_image)
    h, w = luma.shape
    bg, sigma_bg = _estimate_background_and_noise(luma, sky_mask)
    # The stars are scaled to the noise the detector divides by on this 2D luma (a colour frame's luma sits on no
    # grid, unlike its channels). Adding them moves pixels off the frame's grid, so the images they are added to
    # are measured with the frame's own rounding noise as a floor, as the frame was
    frame_rounding = _quantization_sigma(luma)
    sigma_eff = _effective_sigma(sigma_bg, luma)
    res_base = luma - bg

    baseline_dets = detect_point_sources(luma, sky_mask=sky_mask, snr_threshold=snr_threshold)
    mask_bool = np.asarray(sky_mask, dtype=bool) if sky_mask is not None else None

    rng = np.random.default_rng(seed)
    total_needed = len(injected_snrs) * n_per_snr
    margin = 12
    chosen_sites: list[tuple[float, float]] = []

    for _ in range(total_needed * 80):
        if len(chosen_sites) >= total_needed:
            break
        cx = float(rng.uniform(margin, w - margin - 1))
        cy = float(rng.uniform(margin, h - margin - 1))
        ix, iy = int(round(cx)), int(round(cy))
        if mask_bool is not None and not bool(mask_bool[iy, ix]):
            continue
        if any(math.hypot(cx - d.x_px, cy - d.y_px) < 10.0 for d in baseline_dets):
            continue
        if any(math.hypot(cx - sx, cy - sy) < 11.0 for sx, sy in chosen_sites):
            continue
        patch = np.abs(res_base[iy - 3 : iy + 4, ix - 3 : ix + 4])
        if patch.size > 0 and float(np.max(patch)) > 3.2 * sigma_eff:
            continue
        chosen_sites.append((cx, cy))

    # Fallback without quiet-patch restriction if frame is crowded
    for _ in range(total_needed * 80):
        if len(chosen_sites) >= total_needed:
            break
        cx = float(rng.uniform(margin, w - margin - 1))
        cy = float(rng.uniform(margin, h - margin - 1))
        ix, iy = int(round(cx)), int(round(cy))
        if mask_bool is not None and not bool(mask_bool[iy, ix]):
            continue
        if any(math.hypot(cx - d.x_px, cy - d.y_px) < 8.0 for d in baseline_dets):
            continue
        if any(math.hypot(cx - sx, cy - sy) < 9.0 for sx, sy in chosen_sites):
            continue
        chosen_sites.append((cx, cy))

    kernel = _build_gaussian_psf_kernel(sigma_px=1.15, radius=3)
    grid = np.arange(-3, 4, dtype=np.float64)
    gxx, gyy = np.meshgrid(grid, grid)
    unit_psf = np.exp(-(gxx * gxx + gyy * gyy) / (2.0 * psf_sigma_px * psf_sigma_px))
    unit_psf /= max(float(np.sum(unit_psf)), 1e-12)
    coupling = max(float(np.sum(unit_psf * kernel)), 1e-6)

    injected_orig = luma.copy()
    injected_var = _to_luma_2d(variant_image).copy() if variant_image is not None else None

    injected_records: list[tuple[int, float, float, float]] = []
    site_idx = 0
    for bin_idx, snr_val in enumerate(injected_snrs):
        star_flux = float(snr_val) * sigma_eff / coupling
        for _ in range(n_per_snr):
            if site_idx >= len(chosen_sites):
                break
            sx, sy = chosen_sites[site_idx]
            site_idx += 1
            x0, y0 = int(round(sx)), int(round(sy))
            r_win = 6
            y_slice = slice(max(0, y0 - r_win), min(h, y0 + r_win + 1))
            x_slice = slice(max(0, x0 - r_win), min(w, x0 + r_win + 1))
            yy, xx = np.mgrid[y_slice, x_slice]
            psf = np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2.0 * psf_sigma_px * psf_sigma_px))
            psf /= max(float(np.sum(psf)), 1e-12)
            injected_orig[y_slice, x_slice] += star_flux * psf
            if injected_var is not None:
                injected_var[y_slice, x_slice] += star_flux * psf
            injected_records.append((bin_idx, float(snr_val), sx, sy))

    if injected_var is not None:
        proposals = detect_point_sources(
            injected_var, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )
        post_dets = verify_candidates_on_original_pixels(
            injected_orig, proposals, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )
    else:
        post_dets = detect_point_sources(
            injected_orig, sky_mask=sky_mask, snr_threshold=snr_threshold, noise_floor=frame_rounding
        )

    used_det_indices: set[int] = set()
    # Mark pre-existing baseline detections first
    for b_det in baseline_dets:
        best_d: int | None = None
        best_dist = match_radius_px
        for d_idx, p_det in enumerate(post_dets):
            if d_idx in used_det_indices:
                continue
            dist = math.hypot(p_det.x_px - b_det.x_px, p_det.y_px - b_det.y_px)
            if dist <= best_dist:
                best_dist = dist
                best_d = d_idx
        if best_d is not None:
            used_det_indices.add(best_d)

    bin_injected = [0 for _ in injected_snrs]
    bin_recovered = [0 for _ in injected_snrs]
    centroid_errors: list[float] = []

    for bin_idx, _, sx, sy in injected_records:
        bin_injected[bin_idx] += 1
        best_d = None
        best_dist = match_radius_px
        for d_idx, p_det in enumerate(post_dets):
            if d_idx in used_det_indices:
                continue
            dist = math.hypot(p_det.x_px - sx, p_det.y_px - sy)
            if dist <= best_dist:
                best_dist = dist
                best_d = d_idx
        if best_d is not None:
            used_det_indices.add(best_d)
            bin_recovered[bin_idx] += 1
            centroid_errors.append(best_dist)

    total_injected = sum(bin_injected)
    total_recovered = sum(bin_recovered)
    spurious_count = sum(1 for d_idx in range(len(post_dets)) if d_idx not in used_det_indices)
    non_baseline_total = total_recovered + spurious_count
    false_positive_rate = float(spurious_count / non_baseline_total) if non_baseline_total > 0 else 0.0
    completeness = float(total_recovered / total_injected) if total_injected > 0 else 0.0
    median_err = float(np.median(centroid_errors)) if centroid_errors else None

    bins_out: list[InjectionRecoveryBin] = []
    for idx, snr_val in enumerate(injected_snrs):
        inj_c = bin_injected[idx]
        rec_c = bin_recovered[idx]
        bins_out.append(
            InjectionRecoveryBin(
                snr_min=float(snr_val),
                snr_max=float(snr_val),
                injected_count=inj_c,
                recovered_count=rec_c,
                completeness=float(rec_c / inj_c) if inj_c > 0 else 0.0,
            )
        )

    return InjectionRecoveryReport(
        injected_count=total_injected,
        recovered_count=total_recovered,
        baseline_detection_count=len(baseline_dets),
        spurious_count=spurious_count,
        completeness=completeness,
        false_positive_rate=false_positive_rate,
        median_centroid_error_px=median_err,
        bins=tuple(bins_out),
    )

