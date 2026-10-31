"""2D horizon/foreground silhouette contour extraction and ultra-dark sky H.265 macroblock
de-banding (`services/media/silhouette.py`).

Implements:
1. `compute_2d_sky_silhouette_mask`: Traces the column-wise 2D horizon / palm-tree / tent
   silhouette contour (`horizon_contour_y`) and produces a feathered `[0, 1]` 2D sky mask.
2. `deband_shadow_macroblocks`: Suppresses 8-bit H.265 quantization staircase banding in
   ultra-dark sky regions (`luma < 0.12` inside `sky_mask > 0.5`) via multi-scale guided
   smoothing + zero-mean triangular PDF (TPDF) sub-LSB dithering while strictly preserving
   point sources and high-gradient edges (`|luma - local_mean| > 2.5 * local_sigma`).
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from services.science.detector import _to_luma_2d


def compute_2d_sky_silhouette_mask(
    luma: np.ndarray,
    feather_px: float = 12.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Trace the column-wise 2D horizon/silhouette contour and build a smooth `[0, 1]` sky mask.

    Parameters
    ----------
    luma:
        2D grayscale or 3D RGB image array (in `[0, 1]` or `[0, 255]` scale).
    feather_px:
        Vertical feathering width (in pixels) across the traced silhouette boundary.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        `(sky_mask_2d, horizon_contour_y)` where:
        - `sky_mask_2d` is a 2D `float64` array of shape `(H, W)` in `[0.0, 1.0]`
          (`1.0` in sky above the local contour, `0.0` in foreground terrain/tents/people).
        - `horizon_contour_y` is a 1D `float64` array of shape `(W,)` giving the local
          silhouette row coordinate for each image column.
    """
    arr = _to_luma_2d(luma)
    h, w = arr.shape
    if h < 16 or w < 16:
        contour = np.full(w, float(h * 0.55), dtype=np.float64)
        yy = np.arange(h, dtype=np.float64)[:, None]
        mask = (yy < contour[None, :]).astype(np.float64)
        return mask, contour

    l_min = float(np.min(arr))
    l_p99 = float(np.percentile(arr, 99.0))
    span = max(l_p99 - l_min, 1e-6)
    norm = np.clip((arr - l_min) / span, 0.0, 1.5)

    # Pre-smooth horizontally & vertically to suppress sensor read noise and isolated stars
    smooth = ndimage.gaussian_filter(norm, sigma=(2.0, 3.0), mode="nearest")
    grad_y = np.gradient(smooth, axis=0)

    # Estimate global baseline horizon row from row-percentile vertical gradient in [0.28*H, 0.78*H]
    row_p80 = np.percentile(smooth, 80.0, axis=1)
    row_grad = np.gradient(ndimage.gaussian_filter1d(row_p80, sigma=6.0))
    y_lo = max(4, int(round(h * 0.22)))
    y_hi = min(h - 4, int(round(h * 0.80)))

    if y_hi > y_lo and float(np.max(np.abs(row_grad[y_lo:y_hi]))) > 1e-4:
        base_horizon_y = float(y_lo + int(np.argmax(np.abs(row_grad[y_lo:y_hi]))))
    else:
        base_horizon_y = float(h * 0.55)

    # Estimate upper-sky reference luminance per column from the top 18% of the frame
    top_h = max(4, int(round(h * 0.18)))
    sky_ref_col = np.median(smooth[:top_h, :], axis=0)
    sky_mad = float(np.median(np.abs(smooth[:top_h, :] - sky_ref_col[None, :]))) * 1.4826
    contrast_thresh = max(0.06, 3.5 * sky_mad)

    # Column-wise silhouette contour tracing: detect both bright foreground terrain/tents
    # and dark vertical silhouettes (e.g. palm trees / ridgelines) protruding into the sky.
    raw_contour = np.full(w, base_horizon_y, dtype=np.float64)
    band_slice = slice(y_lo, y_hi)
    band_contrast = np.abs(smooth[band_slice, :] - sky_ref_col[None, :])
    band_grad = np.abs(grad_y[band_slice, :])

    # Distance prior keeps columns without strong local edges anchored near base_horizon_y
    band_ys = np.arange(y_lo, y_hi, dtype=np.float64)[:, None]
    prior_weight = np.exp(-0.5 * ((band_ys - base_horizon_y) / max(16.0, 0.22 * h)) ** 2)
    score_map = band_grad * (0.45 + 0.55 * prior_weight)

    for x in range(w):
        col_contrast = band_contrast[:, x]
        col_grad = band_grad[:, x]
        # Check if a protruding structure (e.g., palm tree or tent peak) crosses contrast threshold early
        crossings = np.nonzero((col_contrast >= contrast_thresh) & (col_grad >= 0.008))[0]
        if crossings.size > 0:
            first_y = float(y_lo + int(crossings[0]))
            peak_y = float(y_lo + int(np.argmax(score_map[:, x])))
            raw_contour[x] = 0.85 * first_y + 0.15 * peak_y
        else:
            if float(np.max(score_map[:, x])) > 1e-4:
                raw_contour[x] = float(y_lo + int(np.argmax(score_map[:, x])))

    # Reject narrow star/impulse spikes along the contour via 1D median + Gaussian smoothing
    med_win = max(3, min(15, (w // 24) | 1))
    contour_med = ndimage.median_filter(raw_contour, size=med_win, mode="nearest")
    horizon_contour_y = ndimage.gaussian_filter1d(
        contour_med, sigma=max(1.5, w / 96.0), mode="nearest"
    )
    horizon_contour_y = np.clip(horizon_contour_y, 0.0, float(h - 1)).astype(np.float64)

    # Build smooth cosine-feathered 2D sky mask in [0, 1]
    yy = np.arange(h, dtype=np.float64)[:, None]
    half_f = max(1.0, 0.5 * float(feather_px))
    rel = (yy - (horizon_contour_y[None, :] - half_f)) / (2.0 * half_f)
    clamped = np.clip(rel, 0.0, 1.0)
    sky_mask_2d = 0.5 * (1.0 + np.cos(np.pi * clamped))
    return np.clip(sky_mask_2d, 0.0, 1.0).astype(np.float64), horizon_contour_y


def deband_shadow_macroblocks(
    luma: np.ndarray,
    sky_mask: np.ndarray,
    seed: int = 42,
) -> np.ndarray:
    """Suppress 8-bit H.265 quantization staircase banding in ultra-dark sky regions.

    Applies multi-scale guided box/Gaussian smoothing + zero-mean triangular PDF (TPDF)
    sub-LSB dithering (`amplitude <= 0.5 / 255.0` in normalized units) where `luma < 0.12`
    (or `< 30.6` on `[0, 255]` scale) inside `sky_mask > 0.5`, while strictly preserving
    high-gradient edges and point sources (`|luma - local_mean| > 2.5 * local_sigma`).
    """
    work = _to_luma_2d(luma).copy()
    mask_2d = np.asarray(sky_mask, dtype=np.float64)
    if mask_2d.shape != work.shape:
        raise ValueError(f"sky_mask shape {mask_2d.shape} does not match luma shape {work.shape}")

    # Detect whether input luminance is normalized [0, 1] or 8-bit [0, 255]
    scale_factor = 255.0 if float(np.max(work)) > 1.5 else 1.0
    dark_limit = 0.12 * scale_factor
    lsb_unit = scale_factor / 255.0

    # Multi-scale local mean and robust local standard deviation
    local_mean = ndimage.uniform_filter(work, size=7, mode="nearest")
    broad_mean = ndimage.gaussian_filter(work, sigma=4.5, mode="nearest")
    guided_smooth = 0.45 * local_mean + 0.55 * broad_mean

    diff = work - local_mean
    local_var = ndimage.uniform_filter(diff * diff, size=7, mode="nearest")
    local_sigma = np.sqrt(np.maximum(local_var, 0.0)) + 1e-6 * scale_factor

    # Strictly preserve point sources and high-gradient edges: |luma - local_mean| > 2.5 * local_sigma
    is_protected_detail = np.abs(diff) > (2.5 * local_sigma)
    soft_detail_ratio = np.clip(np.abs(diff) / (2.5 * local_sigma), 0.0, 1.0)
    detail_preserve_weight = np.where(is_protected_detail, 1.0, soft_detail_ratio ** 4)

    # Target ultra-dark sky pixels (luma < 0.12 in [0, 1] scale) inside sky_mask > 0.5
    in_sky = (mask_2d > 0.5).astype(np.float64) * np.clip((mask_2d - 0.5) * 2.0, 0.0, 1.0)
    is_ultra_dark = (work < dark_limit).astype(np.float64)
    dark_taper = np.clip((dark_limit - work) / max(0.035 * scale_factor, 1e-6), 0.0, 1.0)
    deband_weight = in_sky * is_ultra_dark * (0.35 + 0.65 * dark_taper) * (1.0 - detail_preserve_weight)

    # Zero-mean triangular PDF (TPDF) sub-LSB dithering with amplitude <= 0.5 / 255.0
    rng = np.random.default_rng(seed)
    u1 = rng.uniform(-0.24, 0.24, size=work.shape)
    u2 = rng.uniform(-0.24, 0.24, size=work.shape)
    tpdf_dither = (u1 + u2) * lsb_unit

    candidate = guided_smooth + tpdf_dither
    out = work * (1.0 - deband_weight) + candidate * deband_weight
    # Guarantee exact bit-level identity on protected point sources & high-gradient edges
    out = np.where(is_protected_detail | (mask_2d <= 0.5), work, out)
    return np.clip(out, 0.0, 255.0 if scale_factor > 1.5 else 1.0)
