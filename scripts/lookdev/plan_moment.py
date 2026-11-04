#!/usr/bin/env python3
"""Plan a real Earth-at-night look-dev moment for Blender.

For one UTC instant this computes the Sun and Moon positions in ICRS axes (from the app's own
ephemeris in `services/science/ephemeris.py`), Earth's rotation (GMST), and a camera position
above the night-side region with the most NASA Black Marble city light in view. The result is
written as JSON for `scripts/lookdev/earth_at_night.py` to render.

Run from the repo root:
    uv run python scripts/lookdev/plan_moment.py --utc 2026-11-03T20:00:00Z
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from services.science.catalog import _iso_to_julian_date  # noqa: E402
from services.science.ephemeris import (  # noqa: E402
    PLANET_ELEMENTS,
    _compute_moon_geocentric_icrs,
    _ecliptic_to_equatorial_icrs,
    _heliocentric_ecliptic_xyz_au,
)

EARTH_RADIUS_KM = 6371.0
AU_KM = 149_597_870.7
SOURCES = REPO_ROOT / "data" / "lookdev" / "sources"
BLACK_MARBLE = SOURCES / "earth_blackmarble_2016_3km.jpg"
BLUE_MARBLE = SOURCES / "earth_bluemarble_2004-11_21600.jpg"
# Blender's GPU textures top out at 16384 px, so the 21600 px Blue Marble is resampled once
BLUE_MARBLE_16K = SOURCES / "earth_bluemarble_2004-11_16384.jpg"
# Sun more than about 6 degrees below the horizon: past civil twilight, so city lights dominate
NIGHT_SUN_DOT = -math.sin(math.radians(6.0))


def gmst_deg(jd: float) -> float:
    """Greenwich mean sidereal time, the same expression `services/science/catalog.py` uses."""
    t = (jd - 2451545.0) / 36525.0
    return (
        280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t**3 / 38710000.0
    ) % 360.0


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def geographic_to_icrs(lat_deg: np.ndarray, lon_deg: np.ndarray, gmst: float) -> np.ndarray:
    """Unit vectors of geographic points in ICRS axes, taking Earth's spin axis as ICRS +Z."""
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg + gmst)
    return np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)


def radec_to_unit(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return np.array([math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)])


def city_light_map(width: int = 720) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Black Marble 2016 luminance on a coarse lat/lon grid, roughly linearized so lights dominate."""
    Image.MAX_IMAGE_PIXELS = None
    img = Image.open(BLACK_MARBLE).convert("L").resize((width, width // 2), Image.Resampling.BOX)
    lum = (np.asarray(img, dtype=np.float64) / 255.0) ** 2.2
    lat = 90.0 - (np.arange(width // 2) + 0.5) * (180.0 / (width // 2))
    lon = -180.0 + (np.arange(width) + 0.5) * (360.0 / width)
    return lum, lat, lon


def choose_nadir(sun_dir: np.ndarray, gmst: float, cap_deg: float) -> tuple[float, float, float]:
    """Night-side point whose surrounding cap holds the most city light (Gaussian-smoothed proxy)."""
    lum, lat, lon = city_light_map()
    lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
    dirs = geographic_to_icrs(lat_grid, lon_grid, gmst)
    night = (dirs @ sun_dir) < NIGHT_SUN_DOT
    weighted = lum * night * np.cos(np.radians(lat_grid))
    sigma_px = cap_deg / (180.0 / lum.shape[0]) / 2.0
    smooth = ndimage.gaussian_filter(weighted, sigma=sigma_px, mode=("nearest", "wrap"))
    smooth[~night] = 0.0
    i, j = np.unravel_index(int(np.argmax(smooth)), smooth.shape)
    return float(lat[i]), float(lon[j]), float(weighted.sum())


def prepare_textures() -> None:
    """Resample source maps that exceed Blender's texture limit; a no-op once done."""
    if BLUE_MARBLE_16K.exists():
        return
    Image.MAX_IMAGE_PIXELS = None
    Image.open(BLUE_MARBLE).resize((16384, 8192), Image.Resampling.LANCZOS).save(BLUE_MARBLE_16K, quality=95)
    print(f"Resampled {BLUE_MARBLE.name} to {BLUE_MARBLE_16K.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--utc", default=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z"))
    parser.add_argument("--altitude-km", type=float, default=12_742.0, help="camera height above the surface")
    parser.add_argument("--cap-deg", type=float, default=30.0, help="radius of the city-light search cap")
    parser.add_argument(
        "--midnight-offset-deg",
        type=float,
        default=30.0,
        help="how far the camera sits from the anti-solar point, toward the brightest city lights",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    prepare_textures()

    jd = _iso_to_julian_date(args.utc)
    t = (jd - 2451545.0) / 36525.0
    gmst = gmst_deg(jd)

    earth_helio = np.array(_heliocentric_ecliptic_xyz_au(PLANET_ELEMENTS["Earth"], t))
    sun_dir = unit(np.array(_ecliptic_to_equatorial_icrs(*(-earth_helio))))
    moon_ra, moon_dec, moon_au = _compute_moon_geocentric_icrs(t)
    moon_pos = radec_to_unit(moon_ra, moon_dec) * (moon_au * AU_KM / EARTH_RADIUS_KM)
    moon_phase_angle = math.degrees(math.acos(float(np.clip(unit(-moon_pos) @ unit(sun_dir * 1e6 - moon_pos), -1, 1))))

    lights_lat, lights_lon, _ = choose_nadir(sun_dir, gmst, args.cap_deg)
    lights_dir = geographic_to_icrs(np.array(lights_lat), np.array(lights_lon), gmst)
    # Start at the anti-solar point (local midnight) and swing toward the city lights; close to
    # midnight the camera sits in Earth's shadow, so the sunlit atmosphere rings the limb
    anti_sun = -sun_dir
    swing = math.acos(float(np.clip(anti_sun @ lights_dir, -1.0, 1.0)))
    frac = 1.0 if swing == 0.0 else min(1.0, math.radians(args.midnight_offset_deg) / swing)
    camera_dir = unit(
        (math.sin((1 - frac) * swing) * anti_sun + math.sin(frac * swing) * lights_dir) / math.sin(swing)
        if swing > 1e-9
        else anti_sun
    )
    camera_pos = camera_dir * (1.0 + args.altitude_km / EARTH_RADIUS_KM)
    ra = math.degrees(math.atan2(camera_dir[1], camera_dir[0]))
    nadir_lat = math.degrees(math.asin(float(camera_dir[2])))
    nadir_lon = ((ra - gmst + 180.0) % 360.0) - 180.0

    plan = {
        "utc": args.utc,
        "julianDate": jd,
        "gmstDeg": gmst,
        "units": "ICRS axes (+X toward RA 0h, +Z toward the north celestial pole); 1 unit = 6371 km",
        "sunDirection": sun_dir.tolist(),
        "moonPosition": moon_pos.tolist(),
        "moonDistanceKm": moon_au * AU_KM,
        "moonPhaseAngleDeg": moon_phase_angle,
        "cameraNadir": {"latDeg": nadir_lat, "lonDeg": nadir_lon},
        "brightestNightLights": {"latDeg": lights_lat, "lonDeg": lights_lon},
        "midnightOffsetDeg": args.midnight_offset_deg,
        "cameraPosition": camera_pos.tolist(),
        "cameraAltitudeKm": args.altitude_km,
        "assumptions": [
            "Earth's spin axis is ICRS +Z; precession and nutation (under half a degree) are ignored.",
            "Sun and Moon come from the app's analytical ephemeris (Meeus low-precision Moon).",
            "Camera height and the night-side framing rule are creative choices; the lighting is not.",
        ],
        "sources": {
            "earth_day": "NASA Blue Marble Next Generation, November 2004, topography and bathymetry, 21600x10800 (rendered from a 16384x8192 Lanczos resample)",
            "earth_night": "NASA Black Marble 2016 color composite, 3 km, 13500x6750",
            "moon_color": "NASA SVS CGI Moon Kit, LROC color 4k",
            "moon_elevation": "NASA SVS CGI Moon Kit, LOLA ldem_16",
            "stars": "NASA/Goddard SVS Deep Star Maps 2020, starmap_2020_8k.exr (Hipparcos, Tycho-2, Gaia DR2; ESA/Gaia/DPAC)",
        },
    }
    out = args.out or REPO_ROOT / "data" / "lookdev" / "plans" / f"earth_at_night_{args.utc.replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2))
    print(f"Wrote {out}")
    print(f"GMST {gmst:.2f} deg; camera over {nadir_lat:+.1f} lat, {nadir_lon:+.1f} lon at {args.altitude_km:.0f} km")
    print(f"Moon at {moon_au * AU_KM:,.0f} km, phase angle {moon_phase_angle:.0f} deg")


if __name__ == "__main__":
    main()
