#!/usr/bin/env python3
"""Register ESO's Milky Way panorama (eso0932a) to the Gaia DR3 / Hipparcos 2007 catalog stars.

The panorama is a plate carree photograph whose axes sit close to, but not on, galactic
coordinates. This script measures the star images in it, fits where its axes really point
(a rotation from galactic plus a vertical offset), fits a smooth table of the residual
stitching offsets, checks both on held-out stars, and writes the solution the web renderers
use to sample the photograph by direction:

    apps/web/public/textures/eso0932a_registration.json

Inputs are files in this repository only: the panorama (pinned by SHA-256 below) and the baked
catalog star tiles (layers 0 and 1, which reach magnitude 8). The photograph's pixels are not
changed; the solution says where to sample them. Method and results:
docs/science/SKY_PANORAMA_REGISTRATION.md.

Usage:
    uv run python scripts/register_sky_panorama.py           # fit and write the solution
    uv run python scripts/register_sky_panorama.py --verify  # refit and compare with the committed file
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.science import catalog_tiles as ct  # noqa: E402
from services.science import sky_registration as sr  # noqa: E402

IMAGE_PATH = ROOT_DIR / "apps" / "web" / "public" / "textures" / "eso_milkyway_panorama.jpg"
# ESO "Large JPEG" of eso0932a as committed in 34e524a; the solution is only valid for these pixels
IMAGE_SHA256 = "60400c92c54b7c1bd12299c69e83b16e5b6256e7dabacc478c021758ecd28179"
CATALOG_DIR = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3"
OUT_PATH = ROOT_DIR / "apps" / "web" / "public" / "textures" / "eso0932a_registration.json"
FORMAT = "albedian.sky-panorama-registration"

MAG_LIMIT = 6.0  # stars used for the fit
ISOLATION_DEG = 0.3  # no catalog star within this radius brighter than the star + ISOLATION_DMAG
ISOLATION_DMAG = 2.0
# Coarse-to-fine matching from galactic axes: (search radius in pixels, magnitude limit)
COARSE_STAGES = ((80, 1.6), (40, 2.5), (14, 3.5), (6, MAG_LIMIT))
FINAL_RADIUS_PX = 3
MIN_PEAK = 0.03  # difference-of-Gaussians peak, luminance in [0, 1]
OUTLIER_TEXELS = 3.5
SIGMA_CANDIDATES_DEG = (1.5, 2.0, 3.0, 4.0, 6.0)
PRIOR_WEIGHT = 0.5  # pseudo-stars of zero offset at every table texel
TABLE_WIDTH, TABLE_HEIGHT = 180, 90  # 2 degree texels; within 0.1 texel of a 1 degree table
TABLE_SCALE = 32.0  # codes per texel, so +-3.97 texels fit a byte
CHECK_STAR_MAG = 2.5  # IAU-named stars listed with their measured positions


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def core_tiles(layers: tuple[int, ...]) -> list[Path]:
    """The core tile files of `layers`, in tile order: the only catalog files the fit reads."""
    return [
        path
        for layer in layers
        for path in sorted((CATALOG_DIR / f"L{layer}").glob("*.core.bin"), key=lambda p: int(p.name.split(".")[0]))
    ]


def core_tiles_digest(layers: tuple[int, ...]) -> str:
    """SHA-256 over the SHA256SUMS-style lines ("<sha256>  L<n>/<i>.core.bin") of those files.

    It pins exactly the fit's inputs, so new tile kinds elsewhere in the catalog leave it alone.
    """
    lines = "".join(f"{sha256_file(path)}  {path.parent.name}/{path.name}\n" for path in core_tiles(layers))
    return hashlib.sha256(lines.encode()).hexdigest()


def load_catalog(layers: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    dirs, mags = [], []
    for path in core_tiles(layers):
        header, cols = ct.decode_core_tile(path.read_bytes())
        dirs.append(ct.decode_core_directions(header, cols))
        mags.append(ct.decode_mag(cols["mag"]))
    return np.concatenate(dirs), np.concatenate(mags)


def isolated(icrs: np.ndarray, mag: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Candidates with no catalog star within ISOLATION_DEG brighter than their mag + ISOLATION_DMAG."""
    tree = cKDTree(icrs)
    chord = 2.0 * math.sin(math.radians(ISOLATION_DEG) / 2.0)
    keep = []
    for i, neighbours in zip(candidates, tree.query_ball_point(icrs[candidates], chord)):
        if all(j == i or mag[j] >= mag[i] + ISOLATION_DMAG for j in neighbours):
            keep.append(i)
    return np.array(keep, dtype=np.int64)


def measure(dog: np.ndarray, galactic: np.ndarray, solution: sr.RigidSolution, geom: sr.PanoramaGeometry, radius: int):
    """Centroids near each star's modelled position: (indices found, measured xy)."""
    x, y = sr.model_pixels(galactic, solution, geom)
    found, xy = [], []
    for k in range(len(galactic)):
        c = sr.star_centroid(dog, x[k], y[k], radius, MIN_PEAK)
        if c is not None:
            found.append(k)
            xy.append(c[:2])
    return np.array(found, dtype=np.int64), np.array(xy).reshape(-1, 2)


def angular_texels(galactic: np.ndarray, residual_px: np.ndarray, solution: sr.RigidSolution) -> np.ndarray:
    """Residual size on the sky in texels at the equator (x residuals shrink by cos(lat))."""
    _, lat = sr.lon_lat(galactic @ solution.rotation.T)
    return np.hypot(residual_px[:, 0] * np.cos(lat), residual_px[:, 1])


def coarse_to_fine(dog, galactic, mag, geom) -> sr.RigidSolution:
    solution = sr.RigidSolution(np.eye(3), 0.0)
    for radius, mag_limit in COARSE_STAGES:
        use = np.where(mag <= mag_limit)[0]
        found, xy = measure(dog, galactic[use], solution, geom, radius)
        g = galactic[use][found]
        keep = np.ones(len(found), dtype=bool)
        for _ in range(3):
            target = sr.pixel_to_frame(xy[keep, 0], xy[keep, 1], geom, solution.y_offset_px)
            solution = sr.RigidSolution(sr.kabsch(g[keep], target), solution.y_offset_px)
            err = angular_texels(g, sr.pixel_residuals(g, xy, solution, geom), solution)
            mad = np.median(np.abs(err - np.median(err))) * 1.4826
            keep = err < max(3.0, float(np.median(err) + 4.0 * mad))
        solution = sr.fit_rigid(g[keep], xy[keep], solution, geom)
    return solution


def summarize(texels: np.ndarray) -> dict:
    return {
        "stars": int(len(texels)),
        "medianTexels": round(float(np.median(texels)), 3),
        "p90Texels": round(float(np.percentile(texels, 90)), 3),
        "rmsTexels": round(float(np.sqrt(np.mean(texels**2))), 3),
        "maxTexels": round(float(texels.max()), 3),
    }


def tangent_texels(offsets: np.ndarray) -> np.ndarray:
    return np.hypot(offsets[:, 0], offsets[:, 1])


def cross_validate(galactic: np.ndarray, xy: np.ndarray, start: sr.RigidSolution, geom: sr.PanoramaGeometry) -> dict:
    """Two folds (alternate stars): fit on one, report residuals on the other, for each sigma."""
    folds = np.arange(len(galactic)) % 2
    rigid_held, field_held = [], {s: [] for s in SIGMA_CANDIDATES_DEG}
    for k in (0, 1):
        train, test = folds != k, folds == k
        solution = sr.fit_rigid(galactic[train], xy[train], start, geom)
        held = sr.tangent_offsets(galactic[test], xy[test], solution, geom)
        rigid_held.append(held)
        train_frame = galactic[train] @ solution.rotation.T
        train_offsets = sr.tangent_offsets(galactic[train], xy[train], solution, geom)
        test_frame = galactic[test] @ solution.rotation.T
        for sigma in SIGMA_CANDIDATES_DEG:
            predicted = sr.smooth_offsets(train_frame, train_offsets, test_frame, math.radians(sigma), PRIOR_WEIGHT)
            field_held[sigma].append(held - predicted)
    report = {"folds": 2, "rigidOnly": summarize(tangent_texels(np.concatenate(rigid_held)))}
    report["withOffsetTable"] = [
        {"sigmaDeg": sigma, **summarize(tangent_texels(np.concatenate(field_held[sigma])))} for sigma in SIGMA_CANDIDATES_DEG
    ]
    return report


def rows(matrix: np.ndarray, digits: int = 12) -> list[list[float]]:
    return [[round(float(v), digits) for v in row] for row in matrix]


def check_stars(dog, solution, table, geom) -> list[dict]:
    names = json.loads((CATALOG_DIR / "names.json").read_text())["names"]
    out = []
    for star in sorted(names, key=lambda s: (s["mag"], s["name"])):
        if star["mag"] > CHECK_STAR_MAG:
            continue
        g = sr.radec_to_unit(star["raDeg"], star["decDeg"])[None, :] @ sr.ICRS_TO_GALACTIC.T
        model_x, model_y = sr.apply_offsets(g, solution, table, geom)
        c = sr.star_centroid(dog, float(model_x[0]), float(model_y[0]), FINAL_RADIUS_PX, MIN_PEAK)
        if c is None:
            continue
        out.append(
            {
                "name": star["name"],
                "raDeg": star["raDeg"],
                "decDeg": star["decDeg"],
                "mag": star["mag"],
                "modelX": round(float(model_x[0]), 3),
                "modelY": round(float(model_y[0]), 3),
                "measuredX": round(c[0], 3),
                "measuredY": round(c[1], 3),
            }
        )
    return out


def solve() -> dict:
    sha = sha256_file(IMAGE_PATH)
    if sha != IMAGE_SHA256:
        raise SystemExit(f"{IMAGE_PATH.name} has SHA-256 {sha}, not the pinned {IMAGE_SHA256}; refit deliberately")
    rgb = np.asarray(Image.open(IMAGE_PATH).convert("RGB"), dtype=np.float64) / 255.0
    geom = sr.PanoramaGeometry(width=rgb.shape[1], height=rgb.shape[0])
    if geom.width != 2 * geom.height:
        raise SystemExit("a plate carree all-sky panorama is twice as wide as it is tall")
    dog = sr.difference_of_gaussians(rgb.mean(axis=2))

    icrs, mag = load_catalog((0, 1))
    candidates = isolated(icrs, mag, np.where(mag <= MAG_LIMIT)[0])
    galactic = icrs[candidates] @ sr.ICRS_TO_GALACTIC.T
    star_mag = mag[candidates]

    solution = coarse_to_fine(dog, galactic, star_mag, geom)
    found, xy = measure(dog, galactic, solution, geom, FINAL_RADIUS_PX)
    g = galactic[found]
    keep = np.ones(len(found), dtype=bool)
    for _ in range(3):
        solution = sr.fit_rigid(g[keep], xy[keep], solution, geom)
        keep = angular_texels(g, sr.pixel_residuals(g, xy, solution, geom), solution) < OUTLIER_TEXELS
    g, xy = g[keep], xy[keep]

    validation = cross_validate(g, xy, solution, geom)
    best = min(validation["withOffsetTable"], key=lambda r: r["medianTexels"])
    sigma_deg = best["sigmaDeg"]

    frame = g @ solution.rotation.T
    offsets = sr.tangent_offsets(g, xy, solution, geom)
    table_values = sr.smooth_offsets(
        frame, offsets, sr.offset_table_frame(TABLE_WIDTH, TABLE_HEIGHT), math.radians(sigma_deg), PRIOR_WEIGHT
    )
    blob = sr.encode_offset_table(table_values, TABLE_WIDTH, TABLE_HEIGHT, TABLE_SCALE)
    table = sr.decode_offset_table(blob, TABLE_WIDTH, TABLE_HEIGHT, TABLE_SCALE)

    final_x, final_y = sr.apply_offsets(g, solution, table, geom)
    final_px = np.stack([sr.wrap_dx(xy[:, 0] - final_x, geom.width), xy[:, 1] - final_y], axis=-1)
    rotation_deg = math.degrees(np.linalg.norm(Rotation.from_matrix(solution.rotation).as_rotvec()))
    pole_lon, pole_lat = sr.lon_lat(solution.rotation.T @ np.array([0.0, 0.0, 1.0]))
    centre_lon, centre_lat = sr.lon_lat(solution.rotation.T @ np.array([1.0, 0.0, 0.0]))
    texel_arcsec = 360.0 * 3600.0 / geom.width

    return {
        "format": FORMAT,
        "formatVersion": 1,
        "image": {
            "file": IMAGE_PATH.name,
            "sha256": IMAGE_SHA256,
            "width": geom.width,
            "height": geom.height,
            "texelArcsec": round(texel_arcsec, 3),
        },
        "model": (
            "p = galacticToPanorama * icrsToGalactic * icrs; lon = atan2(p.y, p.x), lat = asin(p.z); "
            "u = frac(0.5 - lon / 2 pi), v = 0.5 + lat / pi (row 0 of the image is v = 1); "
            "then (east, north) = offsetTable(u, v) in texels at the equator and "
            "u -= east / (width cos lat), v += (north - yOffsetPx) / height"
        ),
        "icrsToGalactic": rows(sr.ICRS_TO_GALACTIC, 16),
        "galacticToPanorama": rows(solution.rotation),
        "yOffsetPx": round(solution.y_offset_px, 4),
        "offsetTable": {
            "width": TABLE_WIDTH,
            "height": TABLE_HEIGHT,
            "encoding": "uint8 pairs (east, north) per texel, value = (code - bias) / scale texels, row 0 at v = 0 (latitude -90)",
            "scale": TABLE_SCALE,
            "bias": sr.OFFSET_TABLE_BIAS,
            "kernelSigmaDeg": sigma_deg,
            "priorWeight": PRIOR_WEIGHT,
            "maxAbsTexels": round(float(np.abs(table).max()), 3),
            "data": base64.b64encode(blob).decode("ascii"),
        },
        "fit": {
            "catalog": {
                "releaseId": "gaia-dr3",
                "layers": [0, 1],
                "coreTilesSha256": core_tiles_digest((0, 1)),
            },
            "selection": {
                "magnitudeLimit": MAG_LIMIT,
                "isolationDeg": ISOLATION_DEG,
                "isolationDeltaMag": ISOLATION_DMAG,
                "candidates": int(len(candidates)),
                "matched": int(len(found)),
                "used": int(len(g)),
                "outlierTexels": OUTLIER_TEXELS,
            },
            "measurement": "brightest difference-of-Gaussians peak (sigma 1 and 8 px) within 3 px, 5x5 centroid",
            "rotationFromGalacticDeg": round(rotation_deg, 4),
            "panoramaPoleGalactic": {"lDeg": round(math.degrees(pole_lon) % 360.0, 4), "bDeg": round(math.degrees(pole_lat), 4)},
            "panoramaCentreGalactic": {"lDeg": round(math.degrees(centre_lon) % 360.0, 4), "bDeg": round(math.degrees(centre_lat), 4)},
            "heldOut": validation,
            "inSample": {
                "rigidOnly": summarize(angular_texels(g, sr.pixel_residuals(g, xy, solution, geom), solution)),
                "withOffsetTable": summarize(angular_texels(g, final_px, solution)),
            },
        },
        "checkStars": check_stars(dog, solution, table, geom),
    }


def dumps(solution: dict) -> str:
    return json.dumps(solution, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--verify", action="store_true", help="refit and compare with the committed solution")
    args = parser.parse_args()
    text = dumps(solve())
    if args.verify:
        committed = args.out.read_text() if args.out.exists() else ""
        if committed != text:
            print(f"{args.out} differs from a fresh fit", file=sys.stderr)
            return 1
        print(f"{args.out.name} matches a fresh fit")
        return 0
    args.out.write_text(text)
    report = json.loads(text)["fit"]
    print(f"wrote {args.out.relative_to(ROOT_DIR)}")
    print(json.dumps({k: report[k] for k in ("selection", "rotationFromGalacticDeg", "heldOut", "inSample")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
