"""Frames for the photo path's tests: the sRGB transfer (design section 1, Rec. ITU-T H.273 Table 3 value 13),
synthetic star fields with known truth, the corpus photos the design measured, and the path's steps composed in the
order the job will run them (design overview, steps 2, 3, 5 and 7), which restore-4 builds as product code.

The tests may draw random numbers to make a scene; the restoration path itself draws none (REQ-RST-4.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from services.media import noise, spatial, stars, tone

REPO_ROOT = Path(__file__).resolve().parents[2]
# The design's five photos (design.md, "Measured facts"), matched to the corpus by their SHA-256
DESIGN_PHOTOS = ("rp-b-01", "rp-b-02", "rp-b-03", "rp-b-04", "rp-b-05")
PHOTO_PATHS = tuple(f".cache/real-photos/{photo}/{photo}.jpg" for photo in DESIGN_PHOTOS)
# The design measured 768 px crops of the upper middle of each photo
CROP_PX = 768
EXIF_IFD = 0x8769
EXIF_COLOR_SPACE = 0xA001
LUMA = np.array(stars.LUMA_WEIGHTS)


def srgb_decode(code: np.ndarray) -> np.ndarray:
    v = np.asarray(code, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def srgb_encode(light: np.ndarray) -> np.ndarray:
    lin = np.clip(np.asarray(light, dtype=np.float64), 0.0, None)
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)


def srgb_encode_slope(light: np.ndarray) -> np.ndarray:
    lin = np.maximum(np.asarray(light, dtype=np.float64), 1e-12)
    return np.where(lin <= 0.0031308, 12.92, 1.055 / 2.4 * lin ** (1 / 2.4 - 1))


@dataclass(frozen=True)
class Scene:
    codes: np.ndarray  # H x W x 3 integers, as stored
    linear: np.ndarray  # H x W x 3, linear light
    stars: np.ndarray  # (x, y, peak in linear light) of every star drawn
    code_steps: int


def star_field(seed: int, size: int = 256, count: int = 30, *, sky: float = 0.004, noise_sigma: float = 0.0015,
               shared: float = 0.0, bits: int = 16, peaks: tuple[float, float] = (0.01, 0.2)) -> Scene:
    """A dark sky with stars of known place and peak (Gaussian, sigma 1.3 px), white or neighbour-shared noise in
    linear light, stored as sRGB codes."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    image = np.full((size, size), sky)
    placed = []
    margin = 16
    while len(placed) < count:
        x, y = rng.uniform(margin, size - margin, 2)
        if all((x - px) ** 2 + (y - py) ** 2 > 18 ** 2 for px, py, _ in placed):
            peak = float(np.exp(rng.uniform(np.log(peaks[0]), np.log(peaks[1]))))
            placed.append((x, y, peak))
            image += peak * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.3 ** 2))
    channels = []
    for gain in (1.0, 0.8, 1.2):
        field = rng.standard_normal((size, size))
        if shared:
            from scipy import ndimage

            field = ndimage.gaussian_filter(field, shared, mode="wrap")
            field /= field.std()
        channels.append(image + gain * noise_sigma * field)
    linear = np.stack(channels, axis=-1)
    code_steps = 2**bits - 1
    codes = np.clip(np.round(srgb_encode(linear) * code_steps), 0, code_steps).astype(np.uint16 if bits > 8 else np.uint8)
    return Scene(codes, srgb_decode(codes / code_steps), np.array(placed), code_steps)


def photo_crop(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """The upper middle 768 px of a corpus photo: its 8 bit codes and their linear light. The photo must say EXIF
    ColorSpace 1 (sRGB), as the design's five do (M1)."""
    with Image.open(path) as image:
        color_space = image.getexif().get_ifd(EXIF_IFD).get(EXIF_COLOR_SPACE)
        assert color_space == 1, "the photo does not say it is sRGB"
        codes = np.asarray(image.convert("RGB"))
    height, width = codes.shape[:2]
    y0 = max(0, height // 4 - CROP_PX // 2)
    x0 = max(0, (width - CROP_PX) // 2)
    crop = codes[y0:y0 + CROP_PX, x0:x0 + CROP_PX]
    return crop, srgb_decode(crop / 255.0)


@dataclass(frozen=True)
class PathResult:
    field: stars.StarField
    measurement: noise.NoiseMeasurement
    working: np.ndarray
    lift: tone.LiftSolution
    lifted: np.ndarray
    fine_noise_left: float | None


def run_photo_path(codes: np.ndarray, linear: np.ndarray, code_steps: int, *, footprints: bool = True,
                   exposure_ev: float | None = None) -> PathResult:
    """Stars, noise, the spatial step and the lift, in the job's order, with sRGB as the file's transfer."""
    field = stars.find_stars(codes, peak=code_steps)
    footprint = field.footprint if footprints else np.zeros_like(field.footprint)
    measurement = noise.measure_noise(linear, codes, field.footprint, field.clipped, encode_slope=srgb_encode_slope,
                                      code_steps=code_steps)
    luminance = linear @ LUMA
    working = linear
    left = None
    original_noise = working_noise = None
    if measurement.reason is None:
        guide = noise.stabilised(luminance, measurement)
        working = spatial.edge_avoiding(linear, guide, footprint).frame
        blocks = noise.darkest_quarter_blocks(luminance, field.footprint, field.clipped)
        before = noise.fine_noise(luminance, blocks)
        after = noise.fine_noise(working @ LUMA, blocks)
        left = after / before
        dark = tone.dark_level(luminance)
        original_noise = measurement.luma_fine_sigma_at(dark)
        working_noise = after
    lift = tone.solve_lift(luminance, encode_slope=srgb_encode_slope, decode=srgb_decode,
                           original_noise=original_noise, working_noise=working_noise, exposure_ev=exposure_ev)
    return PathResult(field, measurement, working, lift, tone.apply_lift(working, lift.beta), left)
