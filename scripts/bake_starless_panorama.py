#!/usr/bin/env python3
"""Bake the starless Milky Way for the WebGPU landing: ESO's panorama with its stars removed.

The landing draws every point star from the catalog tiles (Gaia DR3 to G 12, the bright end from
Hipparcos 2007) as a sharp sprite at any zoom. The photograph's own stars, 216 arcseconds to a
pixel, turn into soft blocks at the landing's magnification and duplicate the catalog's, so this
bake keeps only the photograph's diffuse light: the glow of unresolved stars, the dust lanes and
the nebulae.

Inputs, all repository files, each pinned by SHA-256 below:
- apps/web/public/textures/eso_milkyway_panorama.jpg: ESO/S. Brunier, The Milky Way panorama
  (eso0932a), 6000x3000, CC BY 4.0.
- apps/web/public/textures/eso0932a_registration.json: where each catalog direction falls on it
  (docs/science/SKY_PANORAMA_REGISTRATION.md).
- The catalog's core tiles of layers 0 and 1 (to magnitude 8), pinned by the same digest the
  registration pins.

Steps, in linear light (the photograph's sRGB decoded):
1. Measure the photograph's own star profiles: for isolated catalog stars in each magnitude bin,
   the mean radial excess over a local background, and the radius where the excess above the outer
   crowding plateau (radii 12 to 24 pixels) falls under 3 percent of its peak above that plateau.
2. Mask each catalog star to MASK_MAG at its registered position, with its bin's radius plus two
   pixels for the registration's residual and local crowding.
3. Smooth away the fainter stellar grain: a 5x5 median, which removes points a pixel or two wide
   and keeps the level and the edges of extended light, then a Gaussian of 1 pixel. Both wrap in
   longitude, where the panorama's edges meet.
4. Fill the masks from their smoothed surroundings: a normalized Gaussian average at growing scales,
   Laplace smoothing inside the masks, and a 1 pixel Gaussian across the seams, followed by two
   passes of 1 degree (16x16 pixel) regional median luminance preservation so every sky patch keeps
   its diffuse brightness.
5. Put back Venus and Jupiter exactly as photographed: they are planets captured during the
   panorama shoot rather than background stars, and the sky layer discloses them directly. Each is
   found as the saturated blob nearest the repository's ephemeris position on its date of agreement.

Output: apps/web/public/textures/eso_milkyway_diffuse_6k.webp, 6000x3000 sRGB, lossless WebP, the
photograph's projection, so its registration applies unchanged. The bake rewrites its file entry
in that folder's PROVENANCE.json and leaves every other entry.

Usage:
  uv run python scripts/bake_starless_panorama.py            bake and write
  uv run python scripts/bake_starless_panorama.py --verify   rebake in memory, compare bytes
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import sys
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.contracts.provenance import AssetProvenanceRecord, dumps_record  # noqa: E402
from services.science import catalog_tiles as ct  # noqa: E402
from services.science import sky_registration as sr  # noqa: E402
from services.science.ephemeris import compute_solar_system_ephemerides  # noqa: E402

TEXTURES = REPO_ROOT / "apps" / "web" / "public" / "textures"
CATALOG_DIR = REPO_ROOT / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
PHOTO_PATH = TEXTURES / "eso_milkyway_panorama.jpg"
PHOTO_SHA256 = "60400c92c54b7c1bd12299c69e83b16e5b6256e7dabacc478c021758ecd28179"
REGISTRATION_PATH = TEXTURES / "eso0932a_registration.json"
REGISTRATION_SHA256 = "11c98ed4452e265129b4a5f39c3198f545998c5bfb635fb8c84fc43c4f0611af"
MASK_LAYERS = (0, 1)
CORE_TILES_SHA256 = "88758681f53a384e6466cbf0cf8c0b5326c05614afb45d46715e64f4af2e7681"
OUTPUT_NAME = "eso_milkyway_diffuse_6k.webp"
RECORD = TEXTURES / "PROVENANCE.json"

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
MASK_MAG = 7.0
MASK_MARGIN_PX = 2
PROFILE_BINS = tuple(range(-1, 7))  # floor of the magnitude; stars to MASK_MAG fall in these
PROFILE_RMAX_PX = 24
PROFILE_PLATEAU_START_PX = 12
PROFILE_FRACTION = 0.03
ISOLATION_DEG = 0.3
ISOLATION_DMAG = 2.0
FILL_SIGMAS_PX = (2.0, 4.0, 8.0, 16.0, 32.0)
FILL_ITERATIONS = 200
GRAIN_MEDIAN_PX = 5
GRAIN_SIGMA_PX = 1.0
REGIONAL_HALF_PX = 8
REGIONAL_STEP_PX = 5
REGIONAL_SIGMA_GRID = 0.7
REGIONAL_PASSES = 2
# The photograph's planets and the dates on which the repository's ephemeris puts each on it
# (tests/integration/test_sky_registration.py); ESO gives no capture dates
PLANETS = (("Venus", "2009-01-22"), ("Jupiter", "2008-06-09"))
SATURATED = 0.9  # mean of R, G and B in sRGB [0, 1]
PLANET_SEARCH_PX = 20
PLANET_MARGIN_PX = 6


def srgb_to_linear(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(v: np.ndarray) -> np.ndarray:
    v = np.clip(np.asarray(v, dtype=np.float32), 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1.0 / 2.4) - 0.055).astype(np.float32)


def wrap_dx(dx, width: int):
    return sr.wrap_dx(np.asarray(dx, dtype=np.float64), width)


def geometry(image: np.ndarray) -> sr.PanoramaGeometry:
    return sr.PanoramaGeometry(width=image.shape[1], height=image.shape[0])


def core_tiles(layers: tuple[int, ...]) -> list[Path]:
    return [
        path
        for layer in layers
        for path in sorted((CATALOG_DIR / f"L{layer}").glob("*.core.bin"), key=lambda p: int(p.name.split(".")[0]))
    ]


def core_tiles_digest(layers: tuple[int, ...]) -> str:
    """SHA-256 over "<sha256>  L<n>/<i>.core.bin" lines, as scripts/register_sky_panorama.py pins them."""
    lines = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.parent.name}/{path.name}\n" for path in core_tiles(layers)
    )
    return hashlib.sha256(lines.encode()).hexdigest()


def load_catalog(layers: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    """ICRS unit vectors and magnitudes of every star in the core tiles of `layers`."""
    dirs, mags = [], []
    for path in core_tiles(layers):
        header, cols = ct.decode_core_tile(path.read_bytes())
        dirs.append(ct.decode_core_directions(header, cols))
        mags.append(ct.decode_mag(cols["mag"]))
    return np.concatenate(dirs), np.concatenate(mags)


def load_registration() -> tuple[sr.RigidSolution, np.ndarray]:
    reg = json.loads(REGISTRATION_PATH.read_text())
    table = reg["offsetTable"]
    offsets = sr.decode_offset_table(base64.b64decode(table["data"]), table["width"], table["height"], table["scale"])
    return sr.RigidSolution(np.array(reg["galacticToPanorama"]), float(reg["yOffsetPx"])), offsets


def catalog_pixels(icrs: np.ndarray, solution: sr.RigidSolution, table: np.ndarray, geom: sr.PanoramaGeometry):
    """Where the registration puts each ICRS direction on the panorama, in pixels."""
    return sr.apply_offsets(icrs @ sr.ICRS_TO_GALACTIC.T, solution, table, geom)


def isolated(icrs: np.ndarray, mag: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Candidates with no catalog star within ISOLATION_DEG brighter than their magnitude + ISOLATION_DMAG."""
    tree = cKDTree(icrs)
    chord = 2.0 * math.sin(math.radians(ISOLATION_DEG) / 2.0)
    keep = []
    for i, neighbours in zip(candidates, tree.query_ball_point(icrs[candidates], chord)):
        if all(j == i or mag[j] >= mag[i] + ISOLATION_DMAG for j in neighbours):
            keep.append(int(i))
    return np.array(keep, dtype=np.int64)


def radial_profile(lum: np.ndarray, x: np.ndarray, y: np.ndarray, rmax: int = PROFILE_RMAX_PX) -> tuple[np.ndarray, int]:
    """Mean excess over the local background (the median of the ring rmax to rmax + 6 px) by radius."""
    size = rmax + 6
    yy, xx = np.mgrid[-size : size + 1, -size : size + 1]
    rr = np.hypot(xx, yy)
    rings = [(rr >= r - 0.5) & (rr < r + 0.5) for r in range(rmax + 1)]
    background_ring = (rr >= rmax) & (rr < rmax + 6)
    h, w = lum.shape
    total = np.zeros(rmax + 1)
    n = 0
    for px, py in zip(x, y):
        xi, yi = int(round(px)), int(round(py))
        if yi - size < 0 or yi + size + 1 > h:
            continue
        cols = np.mod(np.arange(xi - size, xi + size + 1), w)
        window = lum[yi - size : yi + size + 1][:, cols]
        background = np.median(window[background_ring])
        total += np.array([window[ring].mean() - background for ring in rings])
        n += 1
    return total / max(n, 1), n


def profile_radii(lum: np.ndarray, icrs: np.ndarray, mag: np.ndarray, x: np.ndarray, y: np.ndarray) -> dict[int, dict]:
    """For each magnitude bin, the measured profile and the radius where it falls under the threshold."""
    out = {}
    for lo in PROFILE_BINS:
        if lo == PROFILE_BINS[0]:
            selected = mag < lo + 1  # every star brighter than magnitude 0
        elif lo == PROFILE_BINS[-1]:
            selected = (mag >= lo) & (mag <= MASK_MAG)
        else:
            selected = (mag >= lo) & (mag < lo + 1)
        chosen = isolated(icrs, mag, np.nonzero(selected)[0])
        profile, n = radial_profile(lum, x[chosen], y[chosen])
        plateau = float(np.median(profile[PROFILE_PLATEAU_START_PX : PROFILE_RMAX_PX + 1]))
        peak = float(profile[0])
        threshold = PROFILE_FRACTION * (peak - plateau)
        radius = next((r for r in range(len(profile)) if profile[r] - plateau < threshold), PROFILE_RMAX_PX)
        out[lo] = {"stars": n, "peak": peak, "plateau": plateau, "radiusPx": int(radius)}
    return out


def star_mask(shape: tuple[int, int], x: np.ndarray, y: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """True inside a disk of the given radius around each point; wraps in longitude."""
    h, w = shape
    mask = np.zeros(shape, dtype=bool)
    for px, py, r in zip(x, y, radii):
        r = int(r)
        xi, yi = int(round(px)), int(round(py))
        y0, y1 = max(0, yi - r), min(h, yi + r + 1)
        if y0 >= y1:
            continue
        yy, xx = np.mgrid[y0 - yi : y1 - yi, -r : r + 1]
        disk = np.hypot(xx + (xi - px), yy + (yi - py)) <= r + 0.5
        cols = np.mod(np.arange(xi - r, xi + r + 1), w)
        block = mask[y0:y1][:, cols]
        mask[y0:y1, cols] = block | disk
    return mask


def _wrap_filter(channel: np.ndarray, pad: int, op) -> np.ndarray:
    """Applies `op` with the image wrapped in longitude (columns) and held at its edges in latitude."""
    padded = np.pad(channel, ((0, 0), (pad, pad)), mode="wrap")
    return op(padded)[:, pad:-pad]


def fill_masked(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Masked pixels filled from their surroundings; unmasked pixels returned unchanged."""
    out = image.astype(np.float32).copy()
    if not mask.any():
        return out
    valid = (~mask).astype(np.float32)
    have = np.zeros(mask.shape, dtype=bool)
    estimate = np.zeros_like(out)
    for sigma in FILL_SIGMAS_PX:
        pad = int(4 * sigma) + 1

        def blur(a, s=sigma):
            return ndimage.gaussian_filter(a, s, mode="nearest")

        weight = _wrap_filter(valid, pad, blur)
        # The widest scale takes whatever is left, however few unmasked pixels it reaches
        ready = ~have & (weight > (0.2 if sigma < FILL_SIGMAS_PX[-1] else 0.0))
        for c in range(out.shape[2]):
            num = _wrap_filter(out[..., c] * valid, pad, blur)
            estimate[..., c] = np.where(ready, num / np.maximum(weight, 1e-6), estimate[..., c])
        have |= ready
    ys, xs = np.nonzero(mask)
    out[ys, xs] = estimate[ys, xs]
    # Laplace smoothing inside the masks, with the unmasked pixels as fixed boundary values
    h, w = mask.shape
    up = (np.clip(ys - 1, 0, h - 1), xs)
    down = (np.clip(ys + 1, 0, h - 1), xs)
    left = (ys, np.mod(xs - 1, w))
    right = (ys, np.mod(xs + 1, w))
    for _ in range(FILL_ITERATIONS):
        out[ys, xs] = 0.25 * (out[up] + out[down] + out[left] + out[right])
    return out


def smooth_grain(image: np.ndarray) -> np.ndarray:
    """A 5x5 median then a 1 px Gaussian, per channel, wrapping in longitude."""
    out = np.empty_like(image, dtype=np.float32)
    pad = max(GRAIN_MEDIAN_PX, int(4 * GRAIN_SIGMA_PX) + 1)
    for c in range(image.shape[2]):

        def op(a):
            a = ndimage.median_filter(a, size=GRAIN_MEDIAN_PX, mode="nearest")
            return ndimage.gaussian_filter(a, GRAIN_SIGMA_PX, mode="nearest")

        out[..., c] = _wrap_filter(image[..., c].astype(np.float32), pad, op)
    return out


def _regional_median_grid(lum: np.ndarray, half: int = REGIONAL_HALF_PX, step: int = REGIONAL_STEP_PX) -> np.ndarray:
    """Median luminance in (2*half)x(2*half) 1 degree windows sampled every `step` pixels."""
    padded = np.pad(np.pad(lum, ((0, 0), (half, half)), mode="wrap"), ((half, half), (0, 0)), mode="edge")
    windows = sliding_window_view(padded, (2 * half, 2 * half))[::step, ::step]
    return np.median(windows, axis=(-2, -1)).astype(np.float32)


def preserve_regional_light(reference: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Scale `target` smoothly so its 1 degree regional median luminance matches `reference`."""
    h, w = reference.shape[:2]
    grid0 = _regional_median_grid(reference @ LUMA)
    yy = np.arange(h, dtype=np.float32) / REGIONAL_STEP_PX
    xx = np.arange(w, dtype=np.float32) / REGIONAL_STEP_PX
    coords = np.array(np.meshgrid(yy, xx, indexing="ij"))
    out = target.astype(np.float32).copy()
    for _ in range(REGIONAL_PASSES):
        grid1 = _regional_median_grid(out @ LUMA)
        ratio = ndimage.gaussian_filter((grid0 + 1e-4) / (grid1 + 1e-4), REGIONAL_SIGMA_GRID, mode=("nearest", "wrap"))
        scale = ndimage.map_coordinates(ratio, coords, order=1, mode="nearest")
        out = np.clip(out * scale[..., None], 0.0, 1.0)
    return out


def _saturated_core(srgb_mean: np.ndarray, x: float, y: float, radius: int = PLANET_SEARCH_PX):
    """Centroid and pixel count of the saturated blob nearest (x, y) within +-radius pixels."""
    x0, y0 = int(round(x)), int(round(y))
    w = srgb_mean.shape[1]
    cols = np.mod(np.arange(x0 - radius, x0 + radius + 1), w)
    window = srgb_mean[y0 - radius : y0 + radius + 1][:, cols]
    labels, count = ndimage.label(window > SATURATED)
    best = None
    for k in range(1, count + 1):
        ys, xs = np.nonzero(labels == k)
        cx, cy = xs.mean() + x0 - radius, ys.mean() + y0 - radius
        if best is None or math.hypot(cx - x, cy - y) < math.hypot(best[0] - x, best[1] - y):
            best = (float(cx), float(cy), int(len(xs)))
    return best


def planet_windows(photo_linear: np.ndarray, solution, table, geom) -> list[tuple[str, float, float, int]]:
    """(name, x, y, radius) of each planet's window: its saturated core and PLANET_MARGIN_PX around it."""
    srgb_mean = linear_to_srgb(photo_linear).mean(axis=2)
    windows = []
    for body, day in PLANETS:
        best = None
        for hour in range(24):
            iso = f"{day}T{hour:02d}:00:00Z"
            planet = compute_solar_system_ephemerides(iso, 0.0, 0.0, bodies=(body,))[0]
            g = sr.radec_to_unit(planet.ra_deg, planet.dec_deg)[None, :] @ sr.ICRS_TO_GALACTIC.T
            x, y = sr.apply_offsets(g, solution, table, geom)
            core = _saturated_core(srgb_mean, float(x[0]), float(y[0]))
            if core is None:
                continue
            offset = math.hypot(float(wrap_dx(core[0] - float(x[0]), geom.width)), core[1] - float(y[0]))
            if best is None or offset < best[0]:
                best = (offset, core)
        if best is None:
            raise RuntimeError(f"{body}: no saturated blob near the ephemeris position on {day}")
        cx, cy, pixels = best[1]
        windows.append((body, cx, cy, int(math.ceil(math.sqrt(pixels / math.pi))) + PLANET_MARGIN_PX))
    return windows


def _check_inputs() -> None:
    for path, expected in ((PHOTO_PATH, PHOTO_SHA256), (REGISTRATION_PATH, REGISTRATION_SHA256)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise SystemExit(f"{path.name}: sha256 {digest}, expected {expected}")
    digest = core_tiles_digest(MASK_LAYERS)
    if digest != CORE_TILES_SHA256:
        raise SystemExit(f"core tiles of layers {MASK_LAYERS}: digest {digest}, expected {CORE_TILES_SHA256}")


def bake() -> tuple[bytes, dict]:
    """The starless panorama as lossless WebP bytes, and what was measured on the way."""
    _check_inputs()
    photo8 = np.asarray(Image.open(PHOTO_PATH).convert("RGB"))
    photo = srgb_to_linear(photo8.astype(np.float32) / 255.0)
    lum = photo @ LUMA
    geom = geometry(photo)
    solution, table = load_registration()
    icrs, mag = load_catalog(MASK_LAYERS)
    x, y = catalog_pixels(icrs, solution, table, geom)

    radii = profile_radii(lum, icrs, mag, x, y)
    masked = np.nonzero(mag <= MASK_MAG)[0]
    bins = np.clip(np.floor(mag[masked]), PROFILE_BINS[0], PROFILE_BINS[-1]).astype(np.int64)
    radius = np.array([radii[int(b)]["radiusPx"] for b in bins]) + MASK_MARGIN_PX
    mask = star_mask(lum.shape, x[masked], y[masked], radius)

    grained = smooth_grain(photo)
    filled = fill_masked(grained, mask)
    pad = int(4 * GRAIN_SIGMA_PX) + 1
    seamed = np.empty_like(filled, dtype=np.float32)
    for c in range(filled.shape[2]):
        seamed[..., c] = _wrap_filter(filled[..., c], pad, lambda a: ndimage.gaussian_filter(a, GRAIN_SIGMA_PX, mode="nearest"))
    out = preserve_regional_light(photo, seamed)
    out8 = np.round(linear_to_srgb(out) * 255.0).astype(np.uint8)

    windows = planet_windows(photo, solution, table, geom)
    yy, xx = np.mgrid[0 : geom.height, 0 : geom.width]
    for _, cx, cy, r in windows:
        inside = np.hypot(wrap_dx(xx - cx, geom.width), yy - cy) <= r
        out8[inside] = photo8[inside]

    buffer = io.BytesIO()
    Image.fromarray(out8, "RGB").save(buffer, "WEBP", lossless=True, quality=100, method=6)
    measured = {
        "profiles": radii,
        "maskedStars": int(len(masked)),
        "maskedFraction": float(mask.mean()),
        "planets": [{"name": n, "x": round(cx, 1), "y": round(cy, 1), "radiusPx": r} for n, cx, cy, r in windows],
    }
    return buffer.getvalue(), measured


def record_file(data: bytes, measured: dict) -> dict:
    radii = ", ".join(f"{b} {v['radiusPx']}" for b, v in sorted(measured["profiles"].items()))
    return {
        "path": OUTPUT_NAME,
        "sha256": hashlib.sha256(data).hexdigest(),
        "sources": ["eso0932a", "gaia-dr3", "hipparcos-2007"],
        "producedBy": {
            "method": "script",
            "script": "scripts/bake_starless_panorama.py",
            "command": "uv run python scripts/bake_starless_panorama.py",
        },
        "note": (
            "ESO/S. Brunier, eso0932a, modified: stars removed. The landing draws its point stars from the catalog "
            f"tiles, so the photograph's {measured['maskedStars']:,} catalog stars to magnitude {MASK_MAG:g} are masked "
            "where its registration (eso0932a_registration.json) puts them, with radii measured from the photograph's "
            f"own star profiles by magnitude (bin and pixels: {radii}) plus {MASK_MARGIN_PX} for the registration's "
            "error, and filled from their surroundings; a 5x5 median and a 1 px Gaussian, wrapping in longitude, "
            "take the fainter stellar grain. What remains is the photograph's diffuse light: unresolved stars, dust "
            "lanes and nebulae. Venus and Jupiter are put back exactly as photographed. Same projection and size as "
            "the photograph, so its registration applies unchanged."
        ),
    }


def updated_record(record_text: str, entry: dict) -> str:
    """The folder's record with this bake's file entry replaced in place and every other entry kept."""
    record = json.loads(record_text)
    replaced = False
    files: list[dict] = []
    for existing in record["files"]:
        if existing["path"] == entry["path"]:
            if not replaced:
                files.append(entry)
                replaced = True
        else:
            files.append(existing)
    if not replaced:
        files.append(entry)
    record["files"] = files
    return dumps_record(AssetProvenanceRecord.model_validate(record))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="rebake in memory and compare with the committed files")
    args = parser.parse_args()

    data, measured = bake()
    for lo, row in sorted(measured["profiles"].items()):
        print(
            f"profile bin {lo:+d}: {row['stars']} isolated stars, peak {row['peak']:.4f}, "
            f"plateau {row['plateau']:.4f}, radius {row['radiusPx']} px"
        )
    print(f"masked {measured['maskedStars']:,} stars, {measured['maskedFraction'] * 100:.2f}% of the pixels")
    for planet in measured["planets"]:
        print(f"{planet['name']}: kept as photographed within {planet['radiusPx']} px of ({planet['x']}, {planet['y']})")

    target = TEXTURES / OUTPUT_NAME
    digest = hashlib.sha256(data).hexdigest()
    failed = False
    if args.verify:
        same = target.exists() and target.read_bytes() == data
        failed |= not same
        print(f"{OUTPUT_NAME}: {'matches' if same else 'DIFFERS from'} the rebake ({len(data):,} bytes, sha256 {digest})")
    else:
        target.write_bytes(data)
        print(f"wrote {target.relative_to(REPO_ROOT)}: {len(data):,} bytes, sha256 {digest}")
    record_text = RECORD.read_text(encoding="utf-8")
    new_record = updated_record(record_text, record_file(data, measured))
    if args.verify:
        same = new_record == record_text
        failed |= not same
        print(f"{RECORD.name}: {'matches' if same else 'DIFFERS from'} the entry this bake writes")
    else:
        RECORD.write_text(new_record, encoding="utf-8")
        print(f"updated {RECORD.relative_to(REPO_ROOT)}: 1 file")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
