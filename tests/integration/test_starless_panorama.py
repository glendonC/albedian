"""The starless sky panorama of the WebGPU landing.

The landing draws every point star from the catalog tiles, sharp at any zoom, so the Milky Way
behind them is ESO's panorama with its stars removed, leaving its diffuse light
(scripts/bake_starless_panorama.py). Checked on the real photograph, its registration and the
catalog tiles: the stars the photograph resolves are gone where the registration puts them, the
diffuse light around them is kept, the two planets stay exactly as photographed, and the record
says what was changed. The bake's own steps are checked on images with known answers.

Nothing here compares float output byte for byte: every check is a measured value within a stated
tolerance. `uv run python scripts/bake_starless_panorama.py --verify` rebakes the committed file
byte for byte on the machine that made it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
TEXTURES = ROOT / "apps" / "web" / "public" / "textures"


def _load_bake():
    spec = importlib.util.spec_from_file_location("bake_starless_panorama", ROOT / "scripts" / "bake_starless_panorama.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bake():
    return _load_bake()


@pytest.fixture(scope="module")
def sky(bake):
    """The photograph and the starless map in linear light, and the catalog stars on them."""
    photo = bake.srgb_to_linear(np.asarray(Image.open(bake.PHOTO_PATH).convert("RGB"), dtype=np.float32) / 255.0)
    starless = bake.srgb_to_linear(np.asarray(Image.open(TEXTURES / bake.OUTPUT_NAME).convert("RGB"), dtype=np.float32) / 255.0)
    solution, table = bake.load_registration()
    geom = bake.geometry(photo)
    icrs, mag = bake.load_catalog(bake.MASK_LAYERS)
    x, y = bake.catalog_pixels(icrs, solution, table, geom)
    return {
        "photo": photo,
        "starless": starless,
        "photo_lum": photo @ bake.LUMA,
        "starless_lum": starless @ bake.LUMA,
        "icrs": icrs,
        "mag": mag,
        "x": x,
        "y": y,
        "solution": solution,
        "table": table,
        "geom": geom,
    }


def _dog_peaks(lum: np.ndarray, x: np.ndarray, y: np.ndarray, radius: int = 2) -> np.ndarray:
    """The brightest difference-of-Gaussians value (sigma 1 and 8 px) within `radius` px of each point."""
    dog = ndimage.gaussian_filter(lum, 1.0, mode=("nearest", "wrap")) - ndimage.gaussian_filter(lum, 8.0, mode=("nearest", "wrap"))
    peak = ndimage.maximum_filter(dog, size=2 * radius + 1, mode=("nearest", "wrap"))
    h, w = lum.shape
    xi = np.mod(np.round(x).astype(np.int64), w)
    yi = np.clip(np.round(y).astype(np.int64), 0, h - 1)
    return peak[yi, xi]


def test_the_stars_the_photograph_resolves_are_gone(bake, sky):
    """Where the registration puts each isolated catalog star to the masking limit, the photograph
    shows a point source and the starless map shows almost none: its difference-of-Gaussians peak
    falls under a tenth of the photograph's for 97 percent of the stars, and to a twentieth at
    the median."""
    mag = sky["mag"]
    chosen = bake.isolated(sky["icrs"], mag, np.nonzero(mag <= bake.MASK_MAG)[0])
    before = _dog_peaks(sky["photo_lum"], sky["x"][chosen], sky["y"][chosen])
    after = _dog_peaks(sky["starless_lum"], sky["x"][chosen], sky["y"][chosen])
    resolved = before > 0.02
    assert resolved.sum() > 3000, f"only {resolved.sum()} resolved stars to magnitude {bake.MASK_MAG}"
    ratio = np.maximum(after[resolved], 0.0) / before[resolved]
    assert np.mean(ratio < 0.1) >= 0.97, f"{np.mean(ratio < 0.1):.3f} of the resolved stars fell under a tenth"
    assert np.median(ratio) < 0.05


def test_the_fainter_stellar_grain_is_smoothed_away(bake, sky):
    """Isolated stars just past the masking limit (to one magnitude fainter) are not masked one by
    one; the grain filter takes them: their peaks fall under a quarter of the photograph's for 90
    percent of those the photograph resolves."""
    mag = sky["mag"]
    chosen = bake.isolated(sky["icrs"], mag, np.nonzero((mag > bake.MASK_MAG) & (mag <= bake.MASK_MAG + 1.0))[0])
    before = _dog_peaks(sky["photo_lum"], sky["x"][chosen], sky["y"][chosen])
    after = _dog_peaks(sky["starless_lum"], sky["x"][chosen], sky["y"][chosen])
    resolved = before > 0.01
    assert resolved.sum() > 1000
    ratio = np.maximum(after[resolved], 0.0) / before[resolved]
    assert np.mean(ratio < 0.25) >= 0.9, f"{np.mean(ratio < 0.25):.3f} fell under a quarter"


def test_the_diffuse_light_is_kept(bake, sky):
    """Each 1 degree patch keeps its diffuse light: the starless map's median luminance is the
    photograph's within 10 percent (plus 0.002 in linear light, the dark sky's noise), on a grid
    of patches every 5 degrees across the sky between latitudes 70 S and 70 N of the image."""
    lum0, lum1 = sky["photo_lum"], sky["starless_lum"]
    h, w = lum0.shape
    px_per_deg = w / 360.0
    half = int(round(px_per_deg / 2))
    failures = []
    count = 0
    for lat in range(-70, 71, 5):
        cy = int(round((90 - lat) * px_per_deg))
        for lon in range(0, 360, 5):
            cx = int(round(lon * px_per_deg))
            cols = np.mod(np.arange(cx - half, cx + half), w)
            a = float(np.median(lum0[cy - half : cy + half][:, cols]))
            b = float(np.median(lum1[cy - half : cy + half][:, cols]))
            count += 1
            if abs(b - a) > 0.10 * a + 0.002:
                failures.append((lat, lon, round(a, 4), round(b, 4)))
    assert count == 29 * 72
    assert not failures, f"{len(failures)} of {count} patches lost or gained light: {failures[:10]}"


def test_the_planets_stay_as_photographed(bake, sky):
    """Venus and Jupiter are not stars and stay unretouched (the sky layer names them): every
    pixel of their windows is the photograph's own, to the last bit."""
    photo8 = np.asarray(Image.open(bake.PHOTO_PATH).convert("RGB"))
    starless8 = np.asarray(Image.open(TEXTURES / bake.OUTPUT_NAME).convert("RGB"))
    windows = bake.planet_windows(sky["photo"], sky["solution"], sky["table"], sky["geom"])
    assert [name for name, *_ in windows] == ["Venus", "Jupiter"]
    for name, cx, cy, radius in windows:
        assert radius >= 6, name
        yy, xx = np.mgrid[0 : photo8.shape[0], 0 : photo8.shape[1]]
        inside = np.hypot(bake.wrap_dx(xx - cx, photo8.shape[1]), yy - cy) <= radius
        assert np.array_equal(photo8[inside], starless8[inside]), name


def test_the_record_says_the_stars_were_removed(bake):
    record = json.loads((TEXTURES / "PROVENANCE.json").read_text())
    entry = next(f for f in record["files"] if f["path"] == bake.OUTPUT_NAME)
    data = (TEXTURES / bake.OUTPUT_NAME).read_bytes()
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["producedBy"]["method"] == "script"
    assert entry["producedBy"]["script"] == "scripts/bake_starless_panorama.py"
    assert set(entry["sources"]) == {"eso0932a", "gaia-dr3", "hipparcos-2007"}
    assert "modified: stars removed" in entry["note"]
    # The photograph itself is unchanged and keeps its own entry
    photo = next(f for f in record["files"] if f["path"] == "eso_milkyway_panorama.jpg")
    assert photo["sha256"] == hashlib.sha256((TEXTURES / "eso_milkyway_panorama.jpg").read_bytes()).hexdigest()


def test_the_bake_reads_only_pinned_inputs(bake):
    """The photograph, its registration and the core tiles the masks come from, each pinned."""
    registration = json.loads(bake.REGISTRATION_PATH.read_text())
    assert hashlib.sha256(bake.PHOTO_PATH.read_bytes()).hexdigest() == bake.PHOTO_SHA256
    assert registration["image"]["sha256"] == bake.PHOTO_SHA256
    assert hashlib.sha256(bake.REGISTRATION_PATH.read_bytes()).hexdigest() == bake.REGISTRATION_SHA256
    assert bake.core_tiles_digest(bake.MASK_LAYERS) == bake.CORE_TILES_SHA256


# The bake's steps on images with known answers


def test_filling_a_mask_recovers_a_smooth_background(bake):
    """Stars added to a smooth gradient and masked come back as the gradient within 1 percent."""
    h, w = 120, 240
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    background = 0.05 + 0.10 * xx / w + 0.05 * yy / h
    image = np.repeat(background[..., None], 3, axis=2)
    stars = [(40.3, 30.7), (120.0, 60.0), (200.6, 90.2)]
    mask = np.zeros((h, w), dtype=bool)
    for sx, sy in stars:
        r2 = (xx - sx) ** 2 + (yy - sy) ** 2
        image += (0.8 * np.exp(-r2 / 2.0))[..., None]
        mask |= r2 <= 16
    filled = bake.fill_masked(image, mask)
    assert np.allclose(filled[mask], np.repeat(background[..., None], 3, axis=2)[mask], rtol=0.01, atol=1e-4)
    assert np.array_equal(filled[~mask], image[~mask])


def test_the_grain_filter_removes_points_and_keeps_a_smooth_field(bake):
    """Single-pixel points vanish; a smooth field and a broad band keep their level."""
    h, w = 100, 200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    field = 0.05 + 0.02 * np.sin(xx / 30.0) + 0.3 * np.exp(-((yy - 50) ** 2) / (2 * 12.0**2))
    image = np.repeat(field[..., None], 3, axis=2).astype(np.float32)
    rng = np.random.default_rng(7)
    pts = rng.integers(10, [h - 10, w - 10], size=(40, 2))
    noisy = image.copy()
    noisy[pts[:, 0], pts[:, 1]] += 0.5
    smoothed = bake.smooth_grain(noisy)
    assert np.max(np.abs(smoothed[pts[:, 0], pts[:, 1]] - image[pts[:, 0], pts[:, 1]])) < 0.02
    inner = (slice(10, h - 10), slice(10, w - 10))
    assert np.max(np.abs(smoothed[inner] - image[inner])) < 0.02


def test_the_grain_filter_wraps_in_longitude(bake):
    """The panorama's left and right edges meet: a cluster at the seam wraps to columns 78..79 rather than clamping column 0."""
    image = np.full((40, 80, 3), 0.05, dtype=np.float32)
    image[18:23, 0] += 0.6
    image[18:22, 1] += 0.6
    smoothed = bake.smooth_grain(image)
    assert abs(float(smoothed[20, 0, 0]) - 0.05) < 0.005
    assert abs(float(smoothed[20, 79, 0]) - 0.05) < 0.005
