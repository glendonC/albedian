#!/usr/bin/env python3
"""Measure how far the sky dome's star images fall from the catalog stars on a captured frame.

The renderers draw ESO's Milky Way panorama (eso0932a) as a sky dome registered to the Gaia DR3 and
Hipparcos 2007 catalog stars (docs/science/SKY_PANORAMA_REGISTRATION.md). This script checks the
registration where it matters, on screen: every IAU-named star brighter than a magnitude limit is
projected through the captured camera at its catalog direction, and the panorama's own image of
that star is centroided near the projected position in a frame captured with the catalog sprites
hidden. Stars behind a body (the Moon, the Earth and its atmosphere shell) or too close to the
frame's edge are skipped, and so are stars with no dome image above the detection threshold.

A capture is three files in one directory, as the landing capture tool writes them:

    <name>-dome.png     the canvas with the catalog star sprites hidden
    <name>-camera.json  three.js camera matrices, the star mount's world matrix and body spheres
    <name>-full.png     the canvas as shown (only for --detail)

Usage:
    uv run python scripts/check_sky_dome_on_screen.py DIR NAME [--mag 4.5] [--json OUT]
    uv run python scripts/check_sky_dome_on_screen.py DIR NAME --detail OUT.jpg --before DIR2

Residuals are reported in pixels and in texels of the panorama (360/6000 degrees, 216 arcsec).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.science import sky_registration as sr  # noqa: E402

NAMES_PATH = ROOT_DIR / "apps" / "web" / "public" / "catalog" / "stars" / "gaia-dr3" / "names.json"
TEXEL_ARCSEC = 360.0 * 3600.0 / 6000.0
MAG_LIMIT = 4.5
SEARCH_RADIUS_PX = 6
# Difference-of-Gaussians peak on display luminance in [0, 1]; below it there is no dome image
MIN_PEAK = 0.004
DOG_INNER_PX, DOG_OUTER_PX = 1.0, 8.0


@dataclass(frozen=True)
class Camera:
    """A captured three.js camera: projection, world matrix and drawing-buffer size."""

    projection: np.ndarray  # 4x4
    world: np.ndarray  # 4x4, camera to world
    width: int
    height: int

    @property
    def position(self) -> np.ndarray:
        return self.world[:3, 3]

    def project(self, world_dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixel positions (x right, y down; pixel k spans [k, k + 1)) of directions at infinity."""
        view = np.linalg.inv(self.world)
        dirs = np.atleast_2d(world_dirs)
        homogeneous = np.concatenate([dirs, np.zeros((len(dirs), 1))], axis=1)
        clip = (self.projection @ view @ homogeneous.T).T
        w = clip[:, 3]
        with np.errstate(divide="ignore", invalid="ignore"):
            x = (clip[:, 0] / w + 1.0) * 0.5 * self.width
            y = (1.0 - clip[:, 1] / w) * 0.5 * self.height
        return np.stack([x, y], axis=1), w > 0

    def unproject(self, xy: np.ndarray) -> np.ndarray:
        """World-space unit directions through pixel positions (inverse of `project`)."""
        pts = np.atleast_2d(xy)
        ndc = np.stack([pts[:, 0] / self.width * 2.0 - 1.0, 1.0 - pts[:, 1] / self.height * 2.0], axis=1)
        inv_projection = np.linalg.inv(self.projection)
        view_pts = (inv_projection @ np.concatenate([ndc, np.full((len(ndc), 1), 0.5), np.ones((len(ndc), 1))], axis=1).T).T
        view_dirs = view_pts[:, :3] / view_pts[:, 3:4]
        world_dirs = (self.world[:3, :3] @ view_dirs.T).T
        return world_dirs / np.linalg.norm(world_dirs, axis=1, keepdims=True)


def _matrix(elements: list[float]) -> np.ndarray:
    """three.js Matrix4.elements are column-major."""
    return np.asarray(elements, dtype=np.float64).reshape(4, 4).T


def load_capture(directory: Path, name: str) -> tuple[Camera, np.ndarray, list[dict], np.ndarray]:
    meta = json.loads((directory / f"{name}-camera.json").read_text())
    dome = np.asarray(Image.open(directory / f"{name}-dome.png").convert("RGB"), dtype=np.float64) / 255.0
    height, width = dome.shape[:2]
    camera = Camera(_matrix(meta["projectionMatrix"]), _matrix(meta["matrixWorld"]), width, height)
    mount = _matrix(meta["starMountMatrixWorld"])[:3, :3]
    icrs_to_world = mount / np.linalg.norm(mount, axis=0, keepdims=True)
    return camera, dome, meta.get("bodies", []), icrs_to_world


def screen_dog(luminance: np.ndarray) -> np.ndarray:
    """Difference of Gaussians on a screen frame (no wrap at the edges, unlike the panorama's)."""
    return gaussian_filter(luminance, DOG_INNER_PX, mode="nearest") - gaussian_filter(luminance, DOG_OUTER_PX, mode="nearest")


def occluded(camera: Camera, world_dir: np.ndarray, bodies: list[dict], margin_rad: float) -> bool:
    """True when the ray from the camera along `world_dir` passes within a body sphere plus a margin."""
    origin = camera.position
    for body in bodies:
        to_center = np.asarray(body["center"], dtype=np.float64) - origin
        distance = float(np.linalg.norm(to_center))
        if distance <= body["radius"]:
            continue  # a helper around the camera, not a body in front of the sky
        along = float(to_center @ world_dir)
        if along <= 0.0:
            continue
        angle = math.acos(max(-1.0, min(1.0, along / distance)))
        if angle < math.asin(min(1.0, body["radius"] / distance)) + margin_rad:
            return True
    return False


def named_stars(mag_limit: float) -> list[dict]:
    return [s for s in json.loads(NAMES_PATH.read_text())["names"] if s["mag"] < mag_limit]


def measure(directory: Path, name: str, mag_limit: float = MAG_LIMIT) -> dict:
    camera, dome, bodies, icrs_to_world = load_capture(directory, name)
    dog = screen_dog(dome.mean(axis=2))
    # The search radius as an angle at the frame centre, to keep searches off the bodies' limbs
    centre = camera.unproject(np.array([[camera.width / 2, camera.height / 2]]))[0]
    beside = camera.unproject(np.array([[camera.width / 2 + SEARCH_RADIUS_PX, camera.height / 2]]))[0]
    margin = math.acos(float(np.clip(centre @ beside, -1.0, 1.0)))
    edge = SEARCH_RADIUS_PX + sr.CENTROID_BOX_HALF + 2

    rows, skipped = [], {"behind_body": 0, "off_frame": 0, "no_dome_image": 0}
    for star in named_stars(mag_limit):
        world_dir = icrs_to_world @ sr.radec_to_unit(star["raDeg"], star["decDeg"])
        (xy,), (in_front,) = camera.project(world_dir[None, :])
        x, y = float(xy[0]), float(xy[1])
        if not in_front or not (edge <= x <= camera.width - edge and edge <= y <= camera.height - edge):
            skipped["off_frame"] += 1
            continue
        if occluded(camera, world_dir, bodies, margin):
            skipped["behind_body"] += 1
            continue
        found = sr.star_centroid(dog, x, y, SEARCH_RADIUS_PX, MIN_PEAK)
        if found is None:
            skipped["no_dome_image"] += 1
            continue
        measured_dir = camera.unproject(np.array([[found[0], found[1]]]))[0]
        angle_arcsec = math.degrees(math.acos(float(np.clip(measured_dir @ world_dir, -1.0, 1.0)))) * 3600.0
        rows.append(
            {
                "name": star["name"],
                "mag": star["mag"],
                "x": round(x, 3),
                "y": round(y, 3),
                "measuredX": round(found[0], 3),
                "measuredY": round(found[1], 3),
                "peak": round(found[2], 4),
                "pixels": round(math.hypot(found[0] - x, found[1] - y), 3),
                "texels": round(angle_arcsec / TEXEL_ARCSEC, 3),
            }
        )
    texels = np.array([r["texels"] for r in rows])
    summary = {
        "capture": f"{directory / name}",
        "magnitudeLimit": mag_limit,
        "searchRadiusPx": SEARCH_RADIUS_PX,
        "minPeak": MIN_PEAK,
        "stars": len(rows),
        "skipped": skipped,
        "medianTexels": round(float(np.median(texels)), 3) if len(rows) else None,
        "maxTexels": round(float(texels.max()), 3) if len(rows) else None,
        "pixelsPerTexelAtCentre": round(SEARCH_RADIUS_PX / (math.degrees(margin) * 3600.0 / TEXEL_ARCSEC), 3),
    }
    return {"summary": summary, "stars": rows}


def detail_figure(out: Path, panels: list[tuple[str, Path, str]], stars: list[str], directory: Path, name: str) -> None:
    """Crops around named stars, x5, one row per star, one column per (title, dir, file) panel."""
    camera, _, _, icrs_to_world = load_capture(directory, name)
    by_name = {s["name"]: s for s in named_stars(99.0)}
    half, zoom = 12, 5
    tile = 2 * half * zoom
    canvas = Image.new("RGB", (tile * len(panels), tile * len(stars)), (0, 0, 0))
    for row, star_name in enumerate(stars):
        star = by_name[star_name]
        (xy,), _ = camera.project((icrs_to_world @ sr.radec_to_unit(star["raDeg"], star["decDeg"]))[None, :])
        for col, (_, panel_dir, panel_file) in enumerate(panels):
            img = Image.open(panel_dir / panel_file).convert("RGB")
            box = (int(round(xy[0])) - half, int(round(xy[1])) - half, int(round(xy[0])) + half, int(round(xy[1])) + half)
            crop = img.crop(box).resize((tile, tile), Image.NEAREST)
            px = np.asarray(crop).copy()
            cx = int((xy[0] - box[0]) * zoom)
            cy = int((xy[1] - box[1]) * zoom)
            for d in range(-14, 15):
                if abs(d) > 5:
                    for xx, yy in ((cx + d, cy), (cx, cy + d)):
                        if 0 <= xx < tile and 0 <= yy < tile:
                            px[yy, xx] = (0, 220, 255)
            canvas.paste(Image.fromarray(px), (col * tile, row * tile))
    canvas.save(out, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path)
    parser.add_argument("name")
    parser.add_argument("--mag", type=float, default=MAG_LIMIT)
    parser.add_argument("--json", type=Path, help="write the per-star table and summary here")
    parser.add_argument("--detail", type=Path, help="write a x5 detail figure of four named stars here")
    parser.add_argument("--before", type=Path, help="capture directory of the before frame, for --detail")
    args = parser.parse_args()

    result = measure(args.directory, args.name, args.mag)
    print(json.dumps(result["summary"], indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n")
    if args.detail:
        panels = []
        if args.before:
            panels.append(("before, dome only", args.before, f"{args.name}-dome.png"))
        panels += [("dome only", args.directory, f"{args.name}-dome.png"), ("as shown", args.directory, f"{args.name}-full.png")]
        detail_figure(args.detail, panels, ["Hamal", "Enif", "Algenib", "Menkar"], args.directory, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
