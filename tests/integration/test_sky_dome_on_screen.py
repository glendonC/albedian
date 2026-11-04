"""On-screen sky dome check (scripts/check_sky_dome_on_screen.py) on synthetic captures.

The script measures, in a captured frame, how far the panorama's star images fall from the
catalog directions of the IAU-named stars. These tests render frames whose star images sit at
known directions through a known camera, with a body in front of some of them, and check that the
script finds them where they are, skips the ones behind the body, and reads a known rotation of
the dome back as that angle. Star positions come from the repository's named-star list.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from services.science import sky_registration as sr

ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT = 1280, 720
FOV_DEG = 55.4
TEXEL_DEG = 360.0 / 6000.0
# ICRS to scene axes, as ICRS_TO_SCENE_QUATERNION in apps/web/src/spatial/catalogStars/CatalogStarField.ts
ICRS_TO_SCENE = np.array([[0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]])


def _load_script():
    spec = importlib.util.spec_from_file_location("check_sky_dome_on_screen", ROOT / "scripts" / "check_sky_dome_on_screen.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _perspective(fov_deg: float, aspect: float, near: float = 0.05, far: float = 2800.0) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    return np.array(
        [
            [f / aspect, 0.0, 0.0, 0.0],
            [0.0, f, 0.0, 0.0],
            [0.0, 0.0, (far + near) / (near - far), 2.0 * far * near / (near - far)],
            [0.0, 0.0, -1.0, 0.0],
        ]
    )


def _look_at(forward: np.ndarray, up=np.array([0.0, 1.0, 0.0])) -> np.ndarray:
    """Camera to world rotation whose -z axis points along `forward`."""
    z = -forward / np.linalg.norm(forward)
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    world = np.eye(4)
    world[:3, 0], world[:3, 1], world[:3, 2] = x, y, z
    return world


def _rotation(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    a = axis / np.linalg.norm(axis)
    k = np.array([[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]])
    return np.eye(3) + math.sin(angle_rad) * k + (1.0 - math.cos(angle_rad)) * (k @ k)


def _three_elements(m: np.ndarray) -> list[float]:
    """three.js Matrix4.elements order (column-major)."""
    return [float(v) for v in m.T.reshape(-1)]


def _write_capture(directory: Path, name: str, *, dome_rotation=np.eye(3), body=None, drop=()) -> dict:
    """Render Gaussian star images for the named stars through a camera looking at Orion."""
    script = _load_script()
    forward = ICRS_TO_SCENE @ sr.radec_to_unit(84.0, -1.0)
    world = _look_at(forward)
    world[:3, 3] = [3.0, -2.0, 7.0]
    projection = _perspective(FOV_DEG, WIDTH / HEIGHT)
    mount = np.eye(4)
    mount[:3, :3] = ICRS_TO_SCENE * 85.0
    camera = script.Camera(projection, world, WIDTH, HEIGHT)

    image = np.full((HEIGHT, WIDTH), 0.02)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH] + 0.5
    drawn = []
    for star in script.named_stars(script.MAG_LIMIT):
        if star["name"] in drop:
            continue
        direction = dome_rotation @ ICRS_TO_SCENE @ sr.radec_to_unit(star["raDeg"], star["decDeg"])
        (xy,), (in_front,) = camera.project(direction[None, :])
        if not in_front or not (0 <= xy[0] < WIDTH and 0 <= xy[1] < HEIGHT):
            continue
        image += 0.6 * np.exp(-((xx - xy[0]) ** 2 + (yy - xy[1]) ** 2) / (2.0 * 1.1**2))
        drawn.append((star["name"], float(xy[0]), float(xy[1])))
    rgb = np.repeat(np.clip(np.rint(image * 255.0), 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    Image.fromarray(rgb).save(directory / f"{name}-dome.png")
    meta = {
        "projectionMatrix": _three_elements(projection),
        "matrixWorld": _three_elements(world),
        "starMountMatrixWorld": _three_elements(mount),
        "bodies": [body] if body else [],
    }
    (directory / f"{name}-camera.json").write_text(json.dumps(meta))
    return {"camera": camera, "drawn": drawn, "forward": forward}


def test_finds_each_star_image_where_it_was_drawn(tmp_path):
    script = _load_script()
    drawn = _write_capture(tmp_path, "synthetic")["drawn"]
    summary = script.measure(tmp_path, "synthetic")["summary"]
    # Searches keep clear of the frame's edge by the search radius plus the centroid box
    edge = script.SEARCH_RADIUS_PX + sr.CENTROID_BOX_HALF + 2
    inside = [n for n, x, y in drawn if edge <= x <= WIDTH - edge and edge <= y <= HEIGHT - edge]
    assert len(inside) >= 12
    assert summary["stars"] == len(inside)
    assert summary["medianTexels"] < 0.02 and summary["maxTexels"] < 0.05


def test_skips_stars_behind_a_body_and_stars_with_no_dome_image(tmp_path):
    script = _load_script()
    plain = _write_capture(tmp_path, "plain")
    baseline = script.measure(tmp_path, "plain")["summary"]

    # A sphere 20 units ahead along the view, 2 units across, covers about 6 degrees of sky
    body_center = plain["camera"].position + 20.0 * plain["forward"] / np.linalg.norm(plain["forward"])
    body = {"name": "sphere", "center": body_center.tolist(), "radius": 1.0}
    _write_capture(tmp_path, "covered", body=body, drop=("Rigel",))
    covered = script.measure(tmp_path, "covered")["summary"]

    behind = covered["skipped"]["behind_body"]
    assert behind >= 1
    assert covered["skipped"]["no_dome_image"] >= 1
    assert covered["stars"] == baseline["stars"] - behind - covered["skipped"]["no_dome_image"]


def test_reads_a_known_rotation_of_the_dome_back_as_that_angle(tmp_path):
    shift_texels = 2.0
    capture = _write_capture(
        tmp_path, "shifted", dome_rotation=_rotation(np.array([0.3, 1.0, 0.2]), math.radians(shift_texels * TEXEL_DEG))
    )
    rows = _load_script().measure(tmp_path, "shifted")["stars"]
    assert len(rows) >= 10
    texels = np.array([r["texels"] for r in rows])
    # Each star moves by the rotation angle times the sine of its angle from the axis
    axis = np.array([0.3, 1.0, 0.2]) / np.linalg.norm([0.3, 1.0, 0.2])
    expected = []
    for r in rows:
        star = next(s for s in _load_script().named_stars(99.0) if s["name"] == r["name"])
        d = ICRS_TO_SCENE @ sr.radec_to_unit(star["raDeg"], star["decDeg"])
        expected.append(shift_texels * math.sqrt(max(0.0, 1.0 - float(d @ axis) ** 2)))
    assert np.max(np.abs(texels - np.array(expected))) < 0.05
    assert capture["camera"].width == WIDTH


@settings(max_examples=60, deadline=None)
@given(x=st.floats(min_value=0.5, max_value=WIDTH - 0.5), y=st.floats(min_value=0.5, max_value=HEIGHT - 0.5))
def test_unproject_inverts_project(x, y):
    script = _load_script()
    camera = script.Camera(_perspective(FOV_DEG, WIDTH / HEIGHT), _look_at(np.array([0.2, -0.1, -1.0])), WIDTH, HEIGHT)
    direction = camera.unproject(np.array([[x, y]]))
    (xy,), (in_front,) = camera.project(direction)
    assert in_front
    assert abs(xy[0] - x) < 1e-6 and abs(xy[1] - y) < 1e-6
