"""Camera models for the benchmark, each phone camera with the field of view its maker publishes.

Published figures carry their source: the page, the day we read it, and the words it uses.
Everything a maker does not publish is a range, labelled [inference] where it is derived from
published figures or general sensor practice and [open] where nothing grounds it; the suite
samples inside each range, so no single guess decides a result.

Light gathered per star scales with the entrance pupil's area. The pupil diameter is the true
focal length over the f-number, and the true focal length is the 35 mm equivalent scaled by the
sensor's diagonal over 43.27 mm. Where a maker gives the sensor only as an optical format
("1/1.3-inch"), the diagonal is taken as 16 mm over the format's denominator, the usual reading
of that convention [inference].
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from research.benchmarks.phone_photos.geometry import FULL_FRAME_DIAGONAL_MM

READ_ON = "2026-11-04"


@dataclass(frozen=True)
class CameraSpec:
    """One camera: published figures with their source, and ranges for what is not published."""

    key: str
    label: str
    role: str  # "main", "ultra-wide" or "head-worn"
    diagonal_fov_deg: float | None  # published, or derived from a published 35 mm equivalent
    fov_open_range_deg: tuple[float, float] | None  # swept when no field of view is published
    f_number: float | None
    aperture_mm_range: tuple[float, float]  # entrance pupil diameter [inference]
    aspect: tuple[int, int]  # landscape still aspect, width to height
    video_size: tuple[int, int] | None
    max_exposure_s: float  # longest single frame the suite gives this camera [inference]
    lens_correction: str  # "corrected", "either" (the user can turn it off) or "unknown"
    source_url: str | None
    source_read: str | None
    source_quote: str | None
    notes: str = ""


def equivalent_focal_mm(diagonal_fov_deg: float) -> float:
    """35 mm equivalent focal length of a rectilinear lens with this diagonal field."""
    return FULL_FRAME_DIAGONAL_MM / 2.0 / math.tan(math.radians(diagonal_fov_deg) / 2.0)


def pupil_mm(diagonal_fov_deg: float, sensor_diagonal_mm: float, f_number: float) -> float:
    """Entrance pupil diameter from the field, the sensor diagonal and the f-number."""
    focal = equivalent_focal_mm(diagonal_fov_deg) * sensor_diagonal_mm / FULL_FRAME_DIAGONAL_MM
    return focal / f_number


def _around(value: float, fraction: float = 0.08) -> tuple[float, float]:
    return (round(value * (1.0 - fraction), 3), round(value * (1.0 + fraction), 3))


_IPHONE = "https://www.apple.com/iphone-18-pro/specs/"
_PIXEL = "https://store.google.com/product/pixel_11_pro_specs"
_GALAXY = "https://www.samsung.com/lb/support/mobile-devices/s25-series-comparison-with-s24-series/"

CAMERAS: dict[str, CameraSpec] = {
    spec.key: spec
    for spec in (
        CameraSpec(
            key="iphone-18-pro-main",
            label="iPhone 18 Pro, 48MP Fusion Main",
            role="main",
            diagonal_fov_deg=round(2.0 * math.degrees(math.atan(FULL_FRAME_DIAGONAL_MM / 2.0 / 24.0)), 2),
            fov_open_range_deg=None,
            f_number=1.48,
            # Apple publishes no sensor size; a 1/1.28-inch class sensor (12.5 mm) is assumed [inference]
            aperture_mm_range=_around(pupil_mm(84.06, 12.5, 1.48)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="corrected",
            source_url=_IPHONE,
            source_read=READ_ON,
            source_quote="48MP Fusion Main with variable aperture (ƒ/1.48, ƒ/1.8, ƒ/2.8, ƒ/4.0): 24 mm",
            notes="Field derived from the 24 mm equivalent; widest aperture used.",
        ),
        CameraSpec(
            key="iphone-18-pro-ultra-wide",
            label="iPhone 18 Pro, 48MP Fusion Ultra Wide",
            role="ultra-wide",
            diagonal_fov_deg=120.0,
            fov_open_range_deg=None,
            f_number=2.2,
            # No published sensor size; a 1/2.55-inch class sensor (6.3 mm) is assumed [inference]
            aperture_mm_range=_around(pupil_mm(120.0, 6.3, 2.2)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="either",
            source_url=_IPHONE,
            source_read=READ_ON,
            source_quote="48MP Fusion Ultra Wide: 13 mm, ƒ/2.2 aperture and 120° field of view",
            notes="Apple's Camera settings offer Lens Correction for the Ultra Wide; off leaves fisheye distortion.",
        ),
        CameraSpec(
            key="pixel-11-pro-wide",
            label="Pixel 11 Pro, 50 MP wide",
            role="main",
            diagonal_fov_deg=82.0,
            fov_open_range_deg=None,
            f_number=1.68,
            aperture_mm_range=_around(pupil_mm(82.0, 16.0 / 1.3, 1.68)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="corrected",
            source_url=_PIXEL,
            source_read=READ_ON,
            # The page lists these as separate items; they are joined here with semicolons
            source_quote="50 MP Octa PD wide camera; ƒ/1.68 aperture; 82° field of view; 1/1.3\" image sensor size",
        ),
        CameraSpec(
            key="pixel-11-pro-ultrawide",
            label="Pixel 11 Pro, 48 MP ultrawide",
            role="ultra-wide",
            diagonal_fov_deg=123.0,
            fov_open_range_deg=None,
            f_number=1.7,
            aperture_mm_range=_around(pupil_mm(123.0, 16.0 / 2.51, 1.7)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="corrected",
            source_url=_PIXEL,
            source_read=READ_ON,
            # The page lists these as separate items; they are joined here with semicolons
            source_quote="48 MP Quad PD ultrawide camera with autofocus; ƒ/1.7 aperture; 123° field of view; 1/2.51\" image sensor size",
        ),
        CameraSpec(
            key="galaxy-s25-ultra-wide",
            label="Galaxy S25 Ultra, 200 MP wide",
            role="main",
            diagonal_fov_deg=85.0,
            fov_open_range_deg=None,
            f_number=1.7,
            # 200 MP of 0.6 um pixels at 4:3 is 16320 x 12240, a 12.24 mm diagonal [inference]
            aperture_mm_range=_around(pupil_mm(85.0, 0.0006 * math.hypot(16320, 12240), 1.7)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="corrected",
            source_url=_GALAXY,
            source_read=READ_ON,
            source_quote="200 MP F1.7 FOV 85°, 0.6 µm",
            notes="Samsung Lebanon comparison page, last updated 2025-01-28.",
        ),
        CameraSpec(
            key="galaxy-s25-ultra-ultra-wide",
            label="Galaxy S25 Ultra, 50 MP ultra-wide",
            role="ultra-wide",
            diagonal_fov_deg=120.0,
            fov_open_range_deg=None,
            f_number=1.9,
            # 50 MP of 0.7 um pixels at 4:3 is 8160 x 6120, a 7.14 mm diagonal [inference]
            aperture_mm_range=_around(pupil_mm(120.0, 0.0007 * math.hypot(8160, 6120), 1.9)),
            aspect=(4, 3),
            video_size=None,
            max_exposure_s=30.0,
            lens_correction="corrected",
            source_url=_GALAXY,
            source_read=READ_ON,
            source_quote="50 MP F1.9 FOV 120°, 0.7µm",
            notes="Samsung Lebanon comparison page, last updated 2025-01-28.",
        ),
        CameraSpec(
            key="head-worn-video",
            label="Head-worn video camera (modelled class)",
            role="head-worn",
            diagonal_fov_deg=None,
            fov_open_range_deg=(80.0, 110.0),  # [open]: swept, no product figure behind it
            f_number=None,
            aperture_mm_range=(0.5, 0.9),  # [open]: a small sensor behind a fast lens
            aspect=(3, 4),
            video_size=(1440, 1920),
            max_exposure_s=1.0 / 30.0,
            lens_correction="unknown",
            source_url=None,
            source_read=None,
            source_quote=None,
            notes="Portrait video at 30 frames a second; every figure is a benchmark parameter, not a product's specification.",
        ),
    )
}


@dataclass(frozen=True)
class SensorRanges:
    """Per native pixel, ranges the suite samples [inference: typical of current small CMOS sensors;
    phone makers publish none of these]."""

    read_noise_e: tuple[float, float] = (1.2, 3.0)
    full_well_e: tuple[float, float] = (4000.0, 9000.0)
    quantum_efficiency: tuple[float, float] = (0.25, 0.45)  # with colour filters and optics
    dark_current_e_per_s: tuple[float, float] = (0.2, 2.0)
    hot_pixel_fraction: tuple[float, float] = (1e-5, 2e-4)
    hot_pixel_e_per_s: tuple[float, float] = (20.0, 400.0)
    psf_fwhm_native_px: tuple[float, float] = (1.6, 3.2)  # after demosaicing, at the centre
    psf_corner_growth: tuple[float, float] = (0.2, 1.0)  # fractional FWHM growth at the corner


SENSOR = SensorRanges()
