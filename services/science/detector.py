"""Astronomical point-source extraction, motion-compensated video frame stacking,
and synthetic starfield rendering.
"""

from __future__ import annotations

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


def _estimate_background_and_noise(
    luma: np.ndarray, sky_mask: np.ndarray | None = None
) -> tuple[np.ndarray, float]:
    """Estimate smooth local background via fast strided median filtering and MAD noise floor."""
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
    mad = float(np.median(np.abs(valid - med)))
    sigma_bg = 1.4826 * mad
    if sigma_bg < 1e-7:
        q75, q25 = np.percentile(valid, [75.0, 25.0])
        sigma_bg = float((q75 - q25) / 1.349)
    return bg, float(sigma_bg)


def _build_gaussian_psf_kernel(sigma_px: float = 1.15, radius: int = 3) -> np.ndarray:
    """Create a zero-mean-free unit-L2-norm 2D Gaussian matched filter kernel."""
    grid = np.arange(-radius, radius + 1, dtype=np.float64)
    xx, yy = np.meshgrid(grid, grid)
    kernel = np.exp(-(xx * xx + yy * yy) / (2.0 * sigma_px * sigma_px))
    kernel[xx * xx + yy * yy > (radius + 0.5) ** 2] = 0.0
    norm = float(np.sqrt(np.sum(kernel * kernel)))
    return kernel / max(norm, 1e-12)


def detect_point_sources(
    image_luma: np.ndarray,
    sky_mask: np.ndarray | None = None,
    snr_threshold: float = 3.5,
    asset_id: str | None = None,
    frame_time_sec: float = 0.0,
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

    sigma_eff = max(sigma_bg, 1e-3)
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
        is_bright_impulse = peak_raw_snr >= 14.0
        if not (is_real_psf or is_bright_impulse):
            continue

        weights = np.maximum(0.0, patch5 - 0.75 * sigma_eff)
        total_w = float(np.sum(weights))
        if total_w <= 1e-9:
            weights = np.maximum(0.0, patch5)
            total_w = float(np.sum(weights))
        if total_w <= 1e-9:
            continue

        pos_patch5 = np.maximum(0.0, patch5)
        flux = float(np.sum(weights))
        peak_val = float(np.max(pos_patch5))
        peak_ratio = peak_val / max(float(np.sum(pos_patch5)), 1e-9)

        yy, xx = np.mgrid[y0 - 2 : y0 + 3, x0 - 2 : x0 + 3]
        cx = float(np.sum(xx * weights) / total_w)
        cy = float(np.sum(yy * weights) / total_w)

        r2_moment = float(np.sum(weights * ((xx - cx) ** 2 + (yy - cy) ** 2)) / total_w)
        fwhm_px = max(0.55, 1.6651092223153954 * math.sqrt(max(0.0, r2_moment)))
        if peak_ratio > 0.68:
            fwhm_px = min(fwhm_px, 0.85)

        snr_final = matched_snr
        centroid_unc = max(0.03, min(1.5, fwhm_px / max(snr_final, 1.0)))

        detections.append(
            SourceDetection(
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
        )

    detections.sort(key=lambda d: d.snr, reverse=True)
    return detections


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

    luma_frames = [_to_luma_2d(f) for f in frames]
    if len(luma_frames) == 1:
        _, sigma0 = _estimate_background_and_noise(luma_frames[0], sky_mask)
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
        bg, sig = _estimate_background_and_noise(frame, mask_bool)
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

    _, stacked_sigma = _estimate_background_and_noise(composite_luma, mask_bool)
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
    """Synthesize a realistic consumer night-sky frame with known WCS + radial distortion + noise."""
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
