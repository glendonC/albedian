"""scripts/lookdev/plan_moment.py starts on the ERFA ephemeris and gives the answer it gave before, within the
ephemeris' stated uncertainty.

The script plans an Earth-at-night look-dev moment for Blender from the Sun's direction and the Moon's position seen
from the Earth's centre. Its full run reads NASA images under data/lookdev/, which git does not hold, so the test runs
its start (`--help`, which parses its arguments after every import) and its Sun and Moon at the moment its usage line
documents.

Covers:
- `uv run python scripts/lookdev/plan_moment.py --help` exits 0
- At 2026-11-03T20:00:00Z, `sun_and_moon` gives the Sun's direction and the Moon's position from the Earth's centre
  (in Earth radii of 6371 km, as the scene's units), the ephemeris' own geocentric records, and they lie within those
  records' stated uncertainty of what the script computed before this change (main's code at that moment: the values
  below)
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from services.science import ephemeris

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lookdev" / "plan_moment.py"
MOMENT = "2026-11-03T20:00:00Z"
# What main's plan_moment.py computed at MOMENT (its Standish and Williams Earth reversed, its six-term Moon), printed by
# a run of main's functions on 2026-11-11: the Sun's unit direction, the Moon's unit direction and its distance in km
BEFORE_SUN = np.array([-0.754254823412, -0.602417933081, -0.261136545244])
BEFORE_MOON = np.array([-0.914271757544, 0.377122096216, 0.147939439983])
BEFORE_MOON_KM = 379228.603


def _sep_arcsec(u: np.ndarray, v: np.ndarray) -> float:
    u, v = u / np.linalg.norm(u), v / np.linalg.norm(v)
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(u, v))), float(u @ v))) * 3600.0


def _script():
    spec = importlib.util.spec_from_file_location("plan_moment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_starts() -> None:
    run = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:]
    assert "--utc" in run.stdout


def test_its_sun_and_moon_are_the_ephemeris_and_within_its_uncertainty_of_its_answer_before() -> None:
    sun_dir, moon_pos, moon_km = _script().sun_and_moon(MOMENT)
    # The scene's unit is 6371 km: the Moon's position is its direction times its distance in those units
    assert float(np.linalg.norm(moon_pos)) == pytest.approx(moon_km / 6371.0, rel=1e-12)
    moon_dir = moon_pos / float(np.linalg.norm(moon_pos))
    sun, moon = ephemeris.compute_solar_system_ephemerides(MOMENT, 0.0, 0.0, bodies=("Sun", "Moon"), topocentric=False)
    assert sun.frame == moon.frame == "geocentric"
    # The script's numbers are the records', in ICRS axes
    assert _sep_arcsec(sun_dir, np.array(ephemeris.ra_dec_to_unit_vector(sun.ra_deg, sun.dec_deg))) < 1e-6
    assert _sep_arcsec(moon_dir, np.array(ephemeris.ra_dec_to_unit_vector(moon.ra_deg, moon.dec_deg))) < 1e-6
    assert moon_km == moon.distance_au * ephemeris.AU_KM
    # And the answer before lies within their stated uncertainty
    assert _sep_arcsec(sun_dir, BEFORE_SUN) <= sun.uncertainty_arcsec
    assert _sep_arcsec(moon_dir, BEFORE_MOON) <= moon.uncertainty_arcsec
    assert moon.distance_lower_au * ephemeris.AU_KM <= BEFORE_MOON_KM <= moon.distance_upper_au * ephemeris.AU_KM
