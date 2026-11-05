"""scripts/check_landing_moon_features.py on synthetic captures: a grey Moon with dark spots drawn
where a mesh, turned by a known matrix, puts named features. The check must find them as built and
reject the same frame read with a mirrored map, and must fail when the spots sit where a mirrored
map would put them."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 640, 360
FOV_DEG = 20.0
MOON_RADIUS = 1.7374
MOON_CENTRE = np.array([0.0, 0.0, -10.0])


def _load_script():
    spec = importlib.util.spec_from_file_location("check_landing_moon_features", ROOT / "scripts" / "check_landing_moon_features.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check = _load_script()


def _rotation_taking(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    v, c = np.cross(a, b), float(a @ b)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def _projection() -> np.ndarray:
    f = 1 / math.tan(math.radians(FOV_DEG) / 2)
    near, far = 0.1, 100.0
    return np.array([
        [f / (WIDTH / HEIGHT), 0, 0, 0],
        [0, f, 0, 0],
        [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
        [0, 0, -1, 0],
    ])


def _pixel(point: np.ndarray) -> tuple[float, float]:
    clip = _projection() @ np.append(point, 1.0)
    return (clip[0] / clip[3] * 0.5 + 0.5) * WIDTH, (0.5 - clip[1] / clip[3] * 0.5) * HEIGHT


def _capture(tmp_path: Path, spot_convention: str) -> Path:
    """A capture of a Moon turned so Mare Moscoviense faces the camera, with dark spots drawn where
    the given map convention puts the two dark maria."""
    turn = _rotation_taking(check.mesh_direction(*check.FEATURES["Mare Moscoviense"][:2]), np.array([0.0, 0.0, 1.0]))
    world = np.eye(4)
    world[:3, :3] = turn
    world[:3, 3] = MOON_CENTRE
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    image = np.zeros((HEIGHT, WIDTH))
    cx, cy = _pixel(MOON_CENTRE)
    radius_px = MOON_RADIUS / 10.0 / math.tan(math.radians(FOV_DEG) / 2) * HEIGHT / 2
    image[np.hypot(cols - cx, rows - cy) <= radius_px] = 150.0
    remap = check.CONVENTIONS[spot_convention]
    for name in ("Mare Moscoviense", "Mare Ingenii"):
        normal = turn @ check.mesh_direction(*remap(*check.FEATURES[name][:2]))
        if normal[2] <= 0:
            continue  # faces away from the camera
        x, y = _pixel(MOON_CENTRE + MOON_RADIUS * normal)
        image[np.hypot(cols - x, rows - y) <= 6.0] = 60.0
    Image.fromarray(image.astype(np.uint8)).save(tmp_path / "webgpu-full.png")
    geometry = {
        "projectionMatrix": _projection().T.flatten().tolist(),
        "matrixWorld": np.eye(4).T.flatten().tolist(),
        "bodies": [{"name": "cislunarMoon", "center": MOON_CENTRE.tolist(), "radius": MOON_RADIUS, "matrixWorld": world.T.flatten().tolist()}],
    }
    (tmp_path / "webgpu-camera.json").write_text(json.dumps(geometry))
    return tmp_path


def test_finds_the_dark_maria_where_the_mesh_puts_them_and_rejects_a_mirrored_map(tmp_path):
    results = check.measure(_capture(tmp_path, "as built"))
    assert check.as_expected("Mare Moscoviense", results["as built"]["Mare Moscoviense"]) is True
    assert check.as_expected("Mare Ingenii", results["as built"]["Mare Ingenii"]) is True
    assert results["as built"]["Mare Moscoviense"] < -50
    mirrored = [check.as_expected(n, c) for n, c in results["mirrored east-west"].items() if c is not None]
    assert False in mirrored


def test_fails_when_the_spots_sit_where_a_mirrored_map_puts_them(tmp_path):
    results = check.measure(_capture(tmp_path, "mirrored east-west"))
    built = [check.as_expected(n, c) for n, c in results["as built"].items() if c is not None]
    assert False in built
