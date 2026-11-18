"""Render a real Earth-at-night look-dev frame in Blender Cycles from NASA data.

Plan the moment first with `scripts/lookdev/plan_moment.py`, then from the repo root:
    blender -b --factory-startup -P scripts/lookdev/earth_at_night.py -- \
        --plan data/lookdev/plans/<plan>.json --out data/lookdev/renders/earth_at_night.png

World axes are ICRS (+X toward RA 0h, +Z toward the north celestial pole) and one unit is one
mean Earth radius (6371 km). Sunlight, the Moon's position and Earth's rotation come from the
plan. The atmosphere is a physically based volume (Rayleigh, Mie and ozone with exponential
density at real scale heights, coefficients after Bruneton 2017). City lights are NASA Black
Marble radiance mapped to emission, so their relative brightness is real; the exposure is a
camera choice, set for the night side the way orbital night photography is.
"""

import json as _json
from pathlib import Path as _Path
import sys as _sys

import bpy as _bpy
from mathutils import Vector as _Vector

REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(REPO_ROOT))

from scripts.lookdev.earth_night import (  # noqa: E402
    AIRGLOW_ALTITUDE_M,
    AIRGLOW_COLOR,
    AIRGLOW_SIGMA_M,
    ATMOSPHERE_TOP_M,
    EARTH_RADIUS_M,
    MIE_ABSORB_PER_M,
    MIE_SCALE_HEIGHT_M,
    MIE_SCATTER_PER_M,
    MOON_RADIUS,
    OZONE_ABSORB_PER_M,
    OZONE_CENTER_M,
    OZONE_HALF_WIDTH_M,
    RAYLEIGH_PER_M,
    RAYLEIGH_SCALE_HEIGHT_M,
    SRC,
    atmosphere,
    camera,
    earth,
    equirect_uv,
    exp_density,
    grade,
    image_node,
    link,
    math_node,
    moon,
    new_material,
    node,
    parse_args,
    render_settings,
    stars,
    sun,
)


def main() -> None:
    args = parse_args()
    plan = _json.loads(args.plan.read_text())
    _bpy.ops.wm.read_factory_settings(use_empty=True)

    earth(plan["gmstDeg"], args.city_radiance)
    atmosphere(args.airglow_radiance)
    moon(_Vector(plan["moonPosition"]))
    sun(_Vector(plan["sunDirection"]), args.sun_irradiance)
    stars(args.star_strength)
    camera(_Vector(plan["cameraPosition"]), args.lens_mm)
    render_settings(args)
    grade(args.bloom)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    _bpy.ops.render.render(write_still=True)
    print(f"LOOKDEV_RENDERED {args.out}")


__all__ = [
    "AIRGLOW_ALTITUDE_M",
    "AIRGLOW_COLOR",
    "AIRGLOW_SIGMA_M",
    "ATMOSPHERE_TOP_M",
    "EARTH_RADIUS_M",
    "MIE_ABSORB_PER_M",
    "MIE_SCALE_HEIGHT_M",
    "MIE_SCATTER_PER_M",
    "MOON_RADIUS",
    "OZONE_ABSORB_PER_M",
    "OZONE_CENTER_M",
    "OZONE_HALF_WIDTH_M",
    "RAYLEIGH_PER_M",
    "RAYLEIGH_SCALE_HEIGHT_M",
    "REPO_ROOT",
    "SRC",
    "atmosphere",
    "camera",
    "earth",
    "equirect_uv",
    "exp_density",
    "grade",
    "image_node",
    "link",
    "main",
    "math_node",
    "moon",
    "new_material",
    "node",
    "parse_args",
    "render_settings",
    "stars",
    "sun",
]


if __name__ == "__main__":
    main()
