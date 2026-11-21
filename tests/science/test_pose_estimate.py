"""The head-worn Moon pose: lined up from the Moon, a camera profile and a level head (spec
predicted-sky-for-every-upload, REQ-SKY-3; design component 4).

Covers, with synthetic frames at the synthetic Moon moment and open-ocean place the fixtures use (the Moon 33 degrees
up), at the stored size of the glasses' clips (1344 x 1792):
- The Moon test, the Moon found by the contour at half its peak height above the sky: the brightest point after a
  2.5 px Gaussian (one hot pixel is not it), its peak at least 200 of 255 and at least 10 times the noise above the
  sky (the frame's median and MAD), its region (the pixels above the half-peak level) of an equivalent diameter
  inside the limits (0.8 times the Moon's predicted diameter at the widest field of view, 8 times at the narrowest),
  whole inside the frame (its region grown by two pixels never on the frame's edge), and no other spot that passes
  the same test on its own; each at its edge; the centroid, flux, FWHM and radius over the region grown by two pixels
- A soft-peaked Moon of the predicted size at 244 of 255 with only a few pixels near its top (as the first real
  head-worn clip records its Moon) is found within a pixel and lined up; a point light at 244, a lit cloud, a band of
  twilight, two spots that each pass, a lamp twice the Moon's width beside a Moon-sized spot, a Moon-sized rival cut
  by the frame's edge, a spot cut by the frame's edge and a peak under 200 are refused, each for its own reason; a
  Moon's own halo on a bright sky is no rival
- By day (the Sun above the USNO sunrise and sunset altitude at the frame's moment) no Moon pose is tried
- The topocentric Moon: the ephemeris record as it is (topocentric, its parallax applied once at its source), never
  lowered again; from the Earth's centre it stands one parallax higher
- The level-head poses in closed form: none, one or two for one Moon pixel, field of view and roll, the same as an
  independent search finds; a count that is not one at a narrower field of view is not one at any wider one (so one
  pose at the widest swept field of view means one at every narrower field of view at that roll)
- One fit: the WCS a fixed field of view and roll give puts the topocentric Moon on the blob and agrees with a WCS
  built independently for the same pointing, roll and orientation (the level constraint's sign included); a fitted
  centre past a pole is brought back inside the contract's range
- The sweep: nine WCS at the profile's two limits and middle and at rolls -10, 0 and +10 degrees, the nominal one at
  the middle field of view and roll 0, the largest centre offset across them, and the beacon term: the Moon's
  predicted radius plus the ephemeris record's stated uncertainty, read at run time, plus the frame term (what the
  WCS's one rotation leaves out across the frame, from the field of view and where the Moon sits); the stated centre
  is where the camera's centre is seen, the closed form's pointing; a second level pose, or none,
  at any swept value refuses the pose with a true sentence, decided by the closed form before any fit (two poses
  first); a fit that leaves the closed form's pose, or shows the sky upside down, is refused
- P-SKY-5, over the whole stated range (the Moon 5 to 90 degrees up, anywhere in the frame, corners included, the
  camera pointing anywhere a level head allows, both hemispheres, azimuth near 0 and 360): whenever the answer is
  approximate, the topocentric Moon lands on the blob within 0.5 px under every swept WCS, every in-frame predicted
  object's true pixel lies inside its ring (the largest distance to its swept projections plus the beacon term) and
  the true centre lies within maxCenterOffsetDeg; whenever two level poses exist, the answer is never approximate
- P-SKY-6: two soft-peaked Moons that each pass the Moon test, or only point lights smaller than the Moon test
  allows, give no pose
- The approximate solution: its fields, the observed bright spot taken to be the Moon, the detector's findings minus
  those on the spot in the same frame, the predicted sky with in-frame subtitles, the warnings, and no capture value
  in any of them; no text says the spot is the Moon, and nothing was matched to a catalog (matchedInlierCount 0)
- No pose: sky-2's predicted_only result with one more plain reason (no head-worn profile, the Moon not up, by day,
  not confirmed as the Moon, two level poses, the stated ranges not all fitted, the fit refused, a mirrored frame, no
  frame read), never a stand-in pose; a calibrated solution never takes the pose path

Places other than the fixtures' are computed at run time from the sub-lunar point at the fixtures' moment and are
never written: P-SKY-5 needs the Moon at every altitude from 5 to 90 degrees, in both hemispheres.
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass, replace
import hashlib
import math
import re
from typing import Any

from hypothesis import HealthCheck, assume, example, given, settings, strategies as st
import numpy as np
import pytest
from scipy import ndimage, optimize

from services.science import ephemeris
from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL, camera_profile
from services.science import catalog
from services.science.catalog import angular_separation_deg, get_catalog_stars
from services.science.ephemeris import compute_solar_system_ephemerides, moon_parallax_shift_arcsec
from services.science import pose_estimate as pe
from services.science.pose_estimate import (
    MoonBlob,
    PoseResult,
    Refusal,
    find_moon_blob,
    fit_level_pose,
    head_worn_moon_pose,
    moon_diameter_limits_px,
    scale_deg_per_px,
    topocentric_moon,
)
from services.science.predicted_sky import (
    OTHER_FRAMES_KEPT,
    complete_sky,
    frame_moment,
    predicted_sky_findings,
    sky_phase,
)
from services.science.solver import _build_unresolved_solution, triangle_solver_identity
from services.science.wcs_models import CelestialFindingRecord, ObservationSolution, SourceDetection, WCSModel


# The test's world is the apparent sky (the lead's ruling of 2026-11-11, for every pose property): each direction is
# seen at its apparent place (catalog.py `icrs_to_altaz`), and the truth camera is level in the true horizon. The
# estimator may assume what it likes; the truth may not
icrs_to_altaz = catalog.icrs_to_altaz
altaz_to_icrs = catalog.altaz_to_icrs


WIDTH, HEIGHT = 1344, 1792
# The synthetic Moon moment and open-ocean place of the fixtures (tests/integration/test_unregistered_detections.py):
# the Moon 33 degrees up. The second moment has the Moon below the horizon. They point at nobody
MOON_MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0
MOON_DOWN_MOMENT = "2030-01-01T02:00:00Z"
# The Moon's position uncertainty the ephemeris record states at the fixtures' moment and place, in arcsec, and the
# frame term of the pose of the spot near (500, 700) px there (catalog.py `anchored_rotation_omission_arcsec`: the
# observer's aberration over its chord to the frame's farthest point from the Moon, plus twice the deflection)
MOON_STATED_ARCSEC = 44.06372110817171
# A 5 x 5 grid of the stored frame's pixels, its corners included
FRAME_GRID = tuple((x, y) for x in (0.0, WIDTH / 4, WIDTH / 2, 3 * WIDTH / 4, WIDTH)
                   for y in (0.0, HEIGHT / 4, HEIGHT / 2, 3 * HEIGHT / 4, HEIGHT))
FRAME_TERM_ARCSEC = 24.56402658720924
# The same for the soft-peaked Moon at (500.3, 700.6) px
SOFT_FRAME_TERM_ARCSEC = 24.552454352076293
PROFILE = camera_profile(META_RAY_BAN_DISPLAY_MODEL)
ASSET_ID = "src_synthetic_head_worn"
SHA = hashlib.sha256(b"synthetic head-worn Moon pose").hexdigest()
LEAKS = (MOON_MOMENT, MOON_MOMENT[:10], MOON_MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00")
YY, XX = np.ogrid[0:HEIGHT, 0:WIDTH]


def _asset(moment: str = MOON_MOMENT, *, model: str | None = META_RAY_BAN_DISPLAY_MODEL, turns: int = 0,
           mirrored: bool = False, kind: str = "video", lat: float = LAT, lon: float = LON) -> dict[str, Any]:
    asset: dict[str, Any] = {
        "id": ASSET_ID, "sha256": SHA, "mediaKind": kind, "width": WIDTH, "height": HEIGHT,
        "capturedAtUTC": moment, "observerLocation": {"latDeg": lat, "lonDeg": lon},
    }
    if model is not None:
        asset["cameraMetadata"] = {"make": "Meta", "model": model}
    if turns or mirrored:
        asset["displayOrientation"] = {"quarterTurnsClockwise": turns, "mirrored": mirrored}
    return asset


def _solver_solution(
    detections: list[SourceDetection] | None = None, warning: str = "Too few sources.", *, moment: str = MOON_MOMENT,
    lat: float = LAT, lon: float = LON,
) -> ObservationSolution:
    return _build_unresolved_solution(
        detections or [], "sol-synthetic-head-worn", ASSET_ID, SHA, 0.0, moment, lat, lon, warning,
        "Pixel position only: no verified WCS places it on the sky.", triangle_solver_identity(get_catalog_stars()),
    )


def _frame(x: float, y: float, radius: float, *, value: float = 255.0, background: float = 2.0, seed: int = 0) -> np.ndarray:
    """A dark frame with read noise and one flat disk at `value`: a synthetic Moon whose core is clipped [simulated]."""
    rng = np.random.default_rng(seed)
    luma = np.clip(background + rng.normal(0.0, 1.0, (HEIGHT, WIDTH)), 0.0, None)
    luma[(XX - x) ** 2 + (YY - y) ** 2 <= radius * radius] = value
    return luma


def _rects(*rects: tuple[int, int, int, int], value: float = 255.0) -> np.ndarray:
    """A dark frame with flat bright rectangles (x0, y0, w, h), so each spot's area is exact. The sky is code 2 with
    every third pixel at 0, a noise of whole codes that weighs nothing in a centroid (the median is 2)."""
    luma = np.where((XX + YY) % 3 == 0, 0.0, 2.0)
    for x0, y0, w, h in rects:
        luma[y0 : y0 + h, x0 : x0 + w] = value
    return luma


def _capture_free(text: str) -> None:
    for value in LEAKS:
        assert value not in text, (value, text)


# --- An independent model of the truth -----------------------------------------------------------------------------


def _upright(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """A stored-pixel vector as the upright frame shows it (packages/contracts/src/media.ts: turned clockwise)."""
    for _ in range(turns):
        dx, dy = -dy, dx
    return dx, dy


def _stored(dx: float, dy: float, turns: int) -> tuple[float, float]:
    """An upright-frame vector in stored pixels: the turns undone."""
    for _ in range(turns):
        dx, dy = dy, -dx
    return dx, dy


def _scale(fov_deg: float) -> float:
    return math.degrees(2.0 * math.tan(math.radians(fov_deg) / 2.0) / math.hypot(WIDTH, HEIGHT))


def _camera_coords(alt_deg: float, az_deg: float, lat: float, lon: float, moment: str) -> tuple[float, float]:
    """A direction of the true horizon in the truth camera's own coordinates: the horizon frame turned by one fixed
    rotation (catalog.py `rigid_altaz_to_icrs`), so a TAN WCS over them is a gnomonic camera in the horizon."""
    return catalog.rigid_altaz_to_icrs(alt_deg, az_deg, lat, lon, moment)


def _seen(ra: float, dec: float, lat: float, lon: float, moment: str) -> tuple[float, float]:
    """Where the camera sees an ICRS direction, in the truth camera's coordinates: at its apparent place."""
    return _camera_coords(*icrs_to_altaz(ra, dec, lat, lon, moment), lat, lon, moment)


def truth_pixel(wcs: WCSModel, ra: float, dec: float, lat: float = LAT, lon: float = LON,
                moment: str = MOON_MOMENT) -> tuple[float, float]:
    """The pixel at which the truth camera `wcs` sees the ICRS direction (ra, dec)."""
    return wcs.world_to_pixel(*_seen(ra, dec, lat, lon, moment))


def _truth_wcs(centre_alt: float, centre_az: float, fov_deg: float, roll_deg: float, turns: int, moment: str,
               lat: float = LAT, lon: float = LON, coords: Any = None) -> WCSModel:
    """The truth camera: its centre at this altitude and azimuth of the true horizon, straight up in the upright
    frame pointing to the zenith, then the picture turned clockwise by the roll (the horizon's right side lower for a
    positive roll), over `_camera_coords`. With `coords` (a horizontal direction to sky coordinates) the same camera
    over those instead: `_drawn_wcs` gives it the pose's own frame rotation."""
    to_sky = coords or (lambda alt, az: _camera_coords(alt, az, lat, lon, moment))
    ra, dec = to_sky(centre_alt, centre_az)
    unrotated = WCSModel(crval_ra_deg=ra, crval_dec_deg=dec, crpix_x=WIDTH / 2.0, crpix_y=HEIGHT / 2.0,
                         scale_deg_per_px=_scale(fov_deg), width=WIDTH, height=HEIGHT)
    up_ra, up_dec = to_sky(centre_alt + min(0.5, (90.0 - centre_alt) / 2.0), centre_az)
    zx, zy = unrotated.world_to_pixel(up_ra, up_dec)
    tx, ty = _stored(0.0, -1.0, turns)
    level = math.degrees(math.atan2(zy - HEIGHT / 2.0, zx - WIDTH / 2.0) - math.atan2(ty, tx))
    return replace(unrotated, rotation_deg=level - roll_deg)


def _topocentric(moment: str, lat: float = LAT, lon: float = LON) -> tuple[float, float, float]:
    """The Moon as seen from the place: the ephemeris record as it is (topocentric, its parallax already applied);
    (ra, dec, radius)."""
    (moon,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",))
    assert moon.frame == "topocentric"
    return moon.ra_deg, moon.dec_deg, math.degrees(math.asin(1737.4 / (moon.distance_au * 149597870.7)))


def _drawn_wcs(centre_alt: float, centre_az: float, fov_deg: float, roll_deg: float, turns: int, moment: str,
               lat: float = LAT, lon: float = LON) -> WCSModel:
    """The camera the pose should draw for this pointing: the truth camera seen through the pose's one frame
    rotation (`pe.pose_frame`, the rigid frame rotation anchored on the Moon), as ICRS."""
    frame = pe.pose_frame(topocentric_moon(moment, lat, lon), lat, lon, moment)
    return _truth_wcs(centre_alt, centre_az, fov_deg, roll_deg, turns, moment, lat, lon,
                      coords=lambda alt, az: catalog.frame_altaz_to_icrs(frame, alt, az))


def _roll_of(wcs: WCSModel, turns: int, moment: str, lat: float = LAT, lon: float = LON, *, truth: bool = False) -> float:
    """The horizon's tilt in the upright frame: two points at the centre's altitude, 10 degrees either side in
    azimuth; positive when the right one is lower. For a WCS the pose drew (ICRS), the horizon as seen: the points
    at their apparent places. For a truth camera (`truth`), in its own coordinates."""
    if truth:
        alt, az = catalog.icrs_to_rigid_altaz(wcs.crval_ra_deg, wcs.crval_dec_deg, lat, lon, moment)
        lx, ly = wcs.world_to_pixel(*_camera_coords(alt, az - 10.0, lat, lon, moment))
        rx, ry = wcs.world_to_pixel(*_camera_coords(alt, az + 10.0, lat, lon, moment))
    else:
        alt, az = icrs_to_altaz(wcs.crval_ra_deg, wcs.crval_dec_deg, lat, lon, moment)
        lx, ly = wcs.world_to_pixel(*altaz_to_icrs(alt, az - 10.0, lat, lon, moment))
        rx, ry = wcs.world_to_pixel(*altaz_to_icrs(alt, az + 10.0, lat, lon, moment))
    dx, dy = _upright(rx - lx, ry - ly, turns)
    assert dx > 0.0
    return math.degrees(math.atan2(dy, dx))


def _sun_alt(moment: str, lat: float = LAT, lon: float = LON) -> float:
    return compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))[0].alt_deg


# --- Synthetic places, computed and never written --------------------------------------------------------------------


def _destination(lat: float, lon: float, bearing_deg: float, distance_deg: float) -> tuple[float, float]:
    """The point `distance_deg` along a great circle from (lat, lon), leaving at `bearing_deg` east of north."""
    p1, l1, b, d = (math.radians(v) for v in (lat, lon, bearing_deg, distance_deg))
    p2 = math.asin(max(-1.0, min(1.0, math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 180.0) % 360.0 - 180.0


def _initial_bearing(lat: float, lon: float, to_lat: float, to_lon: float) -> float:
    """The great circle's bearing from (lat, lon) toward (to_lat, to_lon), east of north."""
    p1, p2, dl = math.radians(lat), math.radians(to_lat), math.radians(to_lon - lon)
    y = math.sin(dl) * math.cos(p2)
    return math.degrees(math.atan2(y, math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360.0


def _horizontal(alt_deg: float, az_deg: float) -> np.ndarray:
    a, z = math.radians(alt_deg), math.radians(az_deg)
    return np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z)])


class NoRealPlace(AssertionError):
    """No real place has the Moon at the asked altitude and bearing (the point asked for lies past a pole): such a
    draw is refused, never answered with a place that is not one."""


def _fold(lat: float, lon: float) -> tuple[float, float]:
    """The real place a (latitude, longitude) pair names: a latitude past a pole is the point on the far meridian
    (latitude reflected at the pole, longitude turned half way round), and a longitude past +-180 the same meridian
    wrapped into range. A pair already in range is returned as it is."""
    if not -90.0 <= lat <= 90.0:
        # Through the unit vector, so any value folds, however far a step went
        a, o = math.radians(lat), math.radians(lon)
        z = max(-1.0, min(1.0, math.sin(a)))
        lat, lon = math.degrees(math.asin(z)), math.degrees(math.atan2(math.cos(a) * math.sin(o), math.cos(a) * math.cos(o)))
    if not -180.0 <= lon <= 180.0:
        lon = (lon + 180.0) % 360.0 - 180.0
    return lat, lon


@functools.lru_cache(maxsize=None)
def _place_with_moon(alt_deg: float, bearing_deg: float, moment: str = MOON_MOMENT) -> tuple[float, float]:
    """A synthetic place from which the predicted Moon (the record, topocentric) stands `alt_deg` up at `moment` (by
    default the fixtures'), in the direction of the sub-lunar point: first the point its zenith distance from the
    sub-lunar point along `bearing_deg` on a sphere, the sub-lunar point being the fixtures' place moved along the
    geocentric Moon's azimuth by its zenith distance; then moved by Newton's method until the record's altitude is
    `alt_deg` and its azimuth the great circle's bearing from that first point toward the sub-lunar point (both
    within 1e-9 degree), since the record is seen from the place on the WGS84 ellipsoid with aberration, not from a
    sphere's centre. The two horizontal components of the Moon's direction are solved, so a Moon at the zenith
    converges too. A low Moon near the east or west point leaves the search ill conditioned (a step east turns the
    meridian, so the azimuth moves with the altitude), so a step that does not shrink the miss is halved until it does;
    the place may then lie well away from the first point, with the Moon where it was asked. Every place it evaluates
    and returns is a real one (`_fold`): a step past a pole lands on the far meridian, which the ephemeris now
    requires (it refuses a latitude past a pole). A bearing of 0 puts the place north of the sub-lunar point, so
    the Moon stands due south of it; 180 puts it south, so the Moon stands due north (azimuth near 0 and 360)."""
    (geo,) = compute_solar_system_ephemerides(moment, LAT, LON, bodies=("Moon",), topocentric=False)
    sub_lunar = _destination(LAT, LON, geo.az_deg, 90.0 - geo.alt_deg)
    lat, lon = _destination(*sub_lunar, bearing_deg, 90.0 - alt_deg)
    target = _horizontal(alt_deg, _initial_bearing(lat, lon, *sub_lunar))

    def seen(p: np.ndarray) -> np.ndarray:
        (moon,) = compute_solar_system_ephemerides(moment, *_fold(float(p[0]), float(p[1])), bodies=("Moon",))
        return _horizontal(moon.alt_deg, moon.az_deg)

    place = np.array([lat, lon])
    here = seen(place)
    for _ in range(30):
        miss = here - target
        if float(np.max(np.abs(miss))) < 1e-12:
            break
        step = 1e-5
        jacobian = np.column_stack([(seen(place + step * np.eye(2)[i]) - here) / step for i in range(2)])
        move, scale = np.linalg.solve(jacobian, miss), 1.0
        while True:
            trial = place - scale * move
            there = seen(trial)
            if float(np.max(np.abs(there - target))) < float(np.max(np.abs(miss))) or scale < 1e-6:
                break
            scale /= 2.0
        place, here = trial, there
    else:
        raise NoRealPlace(("no synthetic place found", alt_deg, bearing_deg))
    lat, lon = _fold(float(place[0]), float(place[1]))
    return lat, (lon + 180.0) % 360.0 - 180.0


# --- Every level-head pose for one Moon pixel, by a search independent of the code's closed form ---------------------


def _enu(alt_deg: Any, az_deg: Any) -> tuple[Any, Any, Any]:
    a, z = np.radians(alt_deg), np.radians(az_deg)
    return np.cos(a) * np.sin(z), np.cos(a) * np.cos(z), np.sin(a)


def _level_view(centre_alt: Any, centre_az: Any, moon_alt: float, moon_az: float) -> tuple[Any, Any, Any]:
    """Where a level camera centred on (centre_alt, centre_az) sees the Moon: its tangent-plane coordinates (X to the
    right, Y up) and its component along the optical axis (positive in front)."""
    a, z = np.radians(centre_alt), np.radians(centre_az)
    m = _enu(moon_alt, moon_az)
    forward = (np.cos(a) * np.sin(z), np.cos(a) * np.cos(z), np.sin(a))
    right = (np.cos(z), -np.sin(z), 0.0)
    up = (-np.sin(a) * np.sin(z), -np.sin(a) * np.cos(z), np.cos(a))
    along = sum(mi * fi for mi, fi in zip(m, forward))
    with np.errstate(divide="ignore", invalid="ignore"):
        return sum(mi * ri for mi, ri in zip(m, right)) / along, sum(mi * ui for mi, ui in zip(m, up)) / along, along


def _tangent_of_pixel(x_px: float, y_px: float, fov: float, roll: float, turns: int) -> tuple[float, float]:
    """The stored pixel's tangent-plane coordinates in a level camera: the upright offset from the frame centre, the
    roll undone (the truth turns the picture clockwise by the roll), times the scale in radians, Y up."""
    ux, uy = _upright(x_px - WIDTH / 2.0, y_px - HEIGHT / 2.0, turns)
    rho = math.radians(roll)
    dx, dy = ux * math.cos(rho) + uy * math.sin(rho), -ux * math.sin(rho) + uy * math.cos(rho)
    s = math.radians(_scale(fov))
    return dx * s, -dy * s


def _level_poses_by_search(moon_alt: float, moon_az: float, x_px: float, y_px: float, fov: float, roll: float,
                           turns: int) -> list[tuple[float, float]]:
    """Every level-head pointing (centre altitude, azimuth) at this field of view and roll that sees the Moon at the
    stored pixel, lowest centre first: a grid of centre altitudes and azimuth offsets every half degree, each local
    minimum of the miss polished by least squares and kept when it meets the pixel within 1e-10 in tangent units, in
    front of the camera and with the camera's top toward the zenith (|altitude| < 90)."""
    xt, yt = _tangent_of_pixel(x_px, y_px, fov, roll, turns)
    alts = np.arange(-89.75, 90.0, 0.5)
    offsets = np.arange(-180.0, 180.0, 0.5)
    grid_alt, grid_off = np.meshgrid(alts, offsets, indexing="ij")
    gx, gy, along = _level_view(grid_alt, moon_az - grid_off, moon_alt, moon_az)
    miss = np.where(along > 0.0, np.hypot(gx - xt, gy - yt), np.inf)
    padded = np.pad(miss, ((1, 1), (0, 0)), constant_values=np.inf)
    minimum = np.ones_like(miss, dtype=bool)
    for da in (-1, 0, 1):
        for do in (-1, 0, 1):
            if da or do:
                minimum &= miss <= np.roll(padded, (-da, -do), axis=(0, 1))[1:-1]
    found: list[tuple[float, float]] = []
    for i, j in np.argwhere(minimum & (miss < 0.3)):

        def residual(p: np.ndarray) -> np.ndarray:
            x, y, _ = _level_view(p[0], moon_az - p[1], moon_alt, moon_az)
            return np.array([x - xt, y - yt])

        fit = optimize.least_squares(residual, [alts[i], offsets[j]], bounds=([-90.0, -540.0], [90.0, 540.0]),
                                     xtol=1e-15, ftol=1e-15, gtol=1e-15)
        alt0, az0 = float(fit.x[0]), float((moon_az - fit.x[1]) % 360.0)
        if not (np.max(np.abs(fit.fun)) < 1e-10 and abs(alt0) < 90.0 - 1e-7 and _level_view(alt0, az0, moon_alt, moon_az)[2] > 0.0):
            continue
        if not any(angular_separation_deg(az0, alt0, oz, oa) < 1e-5 for oa, oz in found):
            found.append((alt0, az0))
    return sorted(found)


def _fov_of(wcs: WCSModel) -> float:
    return math.degrees(2.0 * math.atan(math.radians(wcs.scale_deg_per_px) * math.hypot(WIDTH, HEIGHT) / 2.0))


# --- The Moon test ---------------------------------------------------------------------------------------------------

EIGHT = np.ones((3, 3), dtype=bool)


def _limits() -> dict[str, float]:
    """The Moon test's diameter limits at the synthetic moment for the profile, as the pose uses them."""
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    low, high = moon_diameter_limits_px(moon, PROFILE.fov_diagonal_deg_range, WIDTH, HEIGHT)
    return {"min_diameter_px": low, "max_diameter_px": high}


def _predicted_radius_px(fov_deg: float = 95.0) -> float:
    """The Moon's predicted radius in pixels at this diagonal field of view (by default the profile's middle)."""
    return _topocentric(MOON_MOMENT)[2] / _scale(fov_deg)


def _noise(seed: int = 1, *, background: float = 2.0, sigma: float = 1.0) -> np.ndarray:
    """A sky of read noise only, in whole codes [simulated]."""
    rng = np.random.default_rng(seed)
    return np.round(np.clip(background + rng.normal(0.0, sigma, (HEIGHT, WIDTH)), 0.0, 255.0))


def _soft_disk(x: float, y: float, radius: float, blur: float, supersample: int = 5) -> tuple[slice, slice, np.ndarray]:
    """A disk of `radius` px about (x, y), each pixel's share of it counted on a 5 x 5 grid inside the pixel (pixel
    centres at whole coordinates), convolved with a Gaussian of `blur` px and scaled to 1 at its brightest pixel; with
    the rows and columns of the window it fills."""
    half = int(math.ceil(radius + 5.0 * blur + 4.0))
    x0, y0 = int(math.floor(x)) - half, int(math.floor(y)) - half
    n = 2 * half + 2
    sub = (np.arange(n * supersample) + 0.5) / supersample - 0.5
    yy, xx = np.meshgrid(sub + y0 - y, sub + x0 - x, indexing="ij")
    cover = (xx * xx + yy * yy <= radius * radius).reshape(n, supersample, n, supersample).mean(axis=(1, 3))
    soft = ndimage.gaussian_filter(cover, blur, mode="constant")
    return slice(y0, y0 + n), slice(x0, x0 + n), soft / soft.max()


def _soft_moon(
    x: float, y: float, radius: float | None = None, *, peak: float = 244.0, blur: float = 2.0,
    background: float = 2.0, sigma: float = 1.0, seed: int = 0, luma: np.ndarray | None = None,
) -> np.ndarray:
    """A sky of read noise (or `luma`) with one soft-peaked Moon over it: a disk of `radius` px (by default the Moon's
    predicted radius at the profile's middle field of view) convolved with a blur of `blur` px, rising from
    `background` to `peak` at its brightest pixel, in whole codes. Nothing is clipped: its top is a peak, not a
    plateau [simulated]."""
    luma = _noise(seed, background=background, sigma=sigma) if luma is None else luma.copy()
    rows, cols, soft = _soft_disk(x, y, _predicted_radius_px() if radius is None else radius, blur)
    # A Moon near the frame's edge is cut by it: only the part of its window inside the frame is drawn
    r0, c0 = max(rows.start, 0), max(cols.start, 0)
    r1, c1 = min(rows.stop, HEIGHT), min(cols.stop, WIDTH)
    part = soft[r0 - rows.start : r1 - rows.start, c0 - cols.start : c1 - cols.start]
    luma[r0:r1, c0:c1] = np.maximum(luma[r0:r1, c0:c1], np.round(background + (peak - background) * part))
    return luma


def _point_light(luma: np.ndarray, x: int, y: int, *, peak: float = 244.0, blur: float = 2.0) -> np.ndarray:
    """An unresolved light at the whole pixel (x, y) over a dark sky (code 2): a Gaussian of `blur` px rising to
    `peak` [simulated]."""
    luma = luma.copy()
    half = int(math.ceil(6.0 * blur)) + 1
    rows, cols = slice(y - half, y + half + 1), slice(x - half, x + half + 1)
    yy, xx = np.mgrid[rows, cols]
    spot = np.round(2.0 + (peak - 2.0) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * blur * blur)))
    luma[rows, cols] = np.maximum(luma[rows, cols], spot)
    return luma


def _rule(luma: np.ndarray) -> tuple[int, int, float, float, np.ndarray, np.ndarray]:
    """The Moon test's measurements written out independently: the brightest point after a Gaussian of 2.5 px, its
    code in the frame's own pixels, the frame's median, the 8-connected labels above the half-peak level, and the
    region holding the peak."""
    smoothed = ndimage.gaussian_filter(luma, sigma=2.5)
    py, px = np.unravel_index(int(np.argmax(smoothed)), luma.shape)
    peak, background = float(luma[py, px]), float(np.median(luma))
    labels, _ = ndimage.label(luma > background + (peak - background) / 2.0, structure=EIGHT)
    return int(px), int(py), peak, background, labels, labels == labels[py, px]


def test_a_soft_peaked_moon_of_the_predicted_size_is_found_at_half_its_peak_within_a_pixel() -> None:
    """As the first real head-worn clip records its Moon: a peak at 244 of 255 with only a few pixels near its top,
    so no flat clipped core; the contour at half its peak still follows the disk's edge."""
    luma = _soft_moon(500.3, 700.6)
    top, _ = ndimage.label(luma >= luma.max() - 3.0, structure=EIGHT)
    assert luma.max() == 244.0 and int(np.bincount(top.ravel())[1:].max()) <= 4
    blob = find_moon_blob(luma, **_limits())
    assert isinstance(blob, MoonBlob), blob
    assert math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0
    # The region at half the peak is the disk: its equivalent diameter within a pixel and a half of the predicted one
    assert abs(blob.equivalent_diameter_px - 2.0 * _predicted_radius_px()) < 1.5
    # A clipped Moon, a flat disk at 255, is found by the same contour, its edge
    flat = find_moon_blob(_frame(700.0, 500.0, 6.0), **_limits())
    assert isinstance(flat, MoonBlob) and flat.area_px == int(((XX - 700.0) ** 2 + (YY - 500.0) ** 2 <= 36.0).sum())


def test_one_hot_pixel_brighter_than_the_moon_is_not_taken_for_it() -> None:
    luma = _soft_moon(500.3, 700.6)
    luma[100, 1200] = 255.0
    blob = find_moon_blob(luma, **_limits())
    assert isinstance(blob, MoonBlob), blob
    assert math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0
    # Its peak is the Moon's own top, 244, not the hot pixel's 255: the region and SNR are the Moon's
    px, py, peak, background, _, region = _rule(luma)
    assert (px, py, peak) == (500, 701, 244.0) and blob.area_px == int(region.sum())
    assert blob.snr == pytest.approx((peak - background) / (1.4826 * float(np.median(np.abs(luma - background)))))
    # and the floor is judged on that peak: a Moon at 199 beside a hot pixel at 255 is refused
    dim = _soft_moon(500.3, 700.6, peak=199.0)
    dim[100, 1200] = 255.0
    refusal = find_moon_blob(dim, **_limits())
    assert isinstance(refusal, Refusal) and "peaks at 199 of 255, under 200" in refusal.reason


def test_a_point_light_at_244_is_refused_on_its_size() -> None:
    """An unresolved light's region at half its peak is only its blur: here 21 px, 5.2 px across."""
    limits = _limits()
    luma = _point_light(_noise(), 600, 900, peak=244.0, blur=2.0)
    _, _, peak, _, _, region = _rule(luma)
    assert peak == 244.0 and int(region.sum()) == 21
    refusal = find_moon_blob(luma, **limits)
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon"
    assert refusal.reason == (
        "The frame's bright spots were not confirmed as the Moon: the brightest spot is 5.2 px across at half its "
        f"peak, under the {limits['min_diameter_px']:.1f} px the Moon needs."
    )


def test_a_lit_cloud_or_a_band_of_twilight_wider_than_eight_moons_is_refused_on_its_size() -> None:
    limits = _limits()
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    cloud = np.maximum(_noise(), np.round(2.0 + 228.0 * np.exp(-((xx - 700.0) ** 2 + (yy - 600.0) ** 2) / (2.0 * 60.0**2))))
    twilight = np.maximum(_noise(), np.round(2.0 + 228.0 * np.exp(-((HEIGHT - 1.0 - yy) ** 2) / (2.0 * 250.0**2))))
    for luma in (cloud, twilight):
        refusal = find_moon_blob(luma, **limits)
        assert isinstance(refusal, Refusal) and refusal.kind == "not_moon", refusal
        assert refusal.reason.endswith(f"px across at half its peak, over the {limits['max_diameter_px']:.1f} px the Moon allows.")
    # A Moon beside a lit cloud brighter than it is refused with it: the cloud is the brightest peak
    assert isinstance(find_moon_blob(_soft_moon(1100.0, 1500.0, peak=220.0, luma=cloud), **limits), Refusal)


MORE_THAN_ONE = "The frame's bright spots were not confirmed as the Moon: more than one spot passes the Moon test on its own."


def test_two_comparable_moons_are_refused() -> None:
    luma = _soft_moon(900.0, 400.0, peak=240.0, luma=_soft_moon(400.0, 1200.0, peak=244.0))
    refusal = find_moon_blob(luma, **_limits())
    assert isinstance(refusal, Refusal) and refusal.reason == MORE_THAN_ONE


def _own_test(luma: np.ndarray, x: int, y: int) -> bool:
    """The Moon test written out for one spot on its own: its own code at (x, y), its own half-peak region about it,
    against the frame's sky and noise and the limits at the synthetic moment."""
    background = float(np.median(luma))
    noise = max(1.4826 * float(np.median(np.abs(luma - background))), 1e-4)
    peak = float(luma[y, x])
    labels, _ = ndimage.label(luma > background + (peak - background) / 2.0, structure=EIGHT)
    diameter = 2.0 * math.sqrt(float((labels == labels[y, x]).sum()) / math.pi)
    limits = _limits()
    return (peak >= 200.0 and (peak - background) / noise >= 10.0
            and limits["min_diameter_px"] <= diameter <= limits["max_diameter_px"])


def test_the_candidate_is_the_brightest_point_after_a_gaussian_of_2_5_px() -> None:
    """REQ-SKY-3.3 (row 3.3-2 of sky-3b-v4's statement table, which no test pinned): a 5 x 5 block at 255, too small
    for the Moon test, beside a Moon of the predicted size peaking at 220. After a Gaussian of 1.5 px the block is
    the brightest point, after 2 px or more the Moon is; the Moon test takes the Moon, and its width is 2.5 px."""
    luma = _soft_moon(500.3, 700.6, peak=220.0)
    luma[300:305, 1100:1105] = 255.0

    def brightest(sigma: float) -> tuple[int, int]:
        y, x = np.unravel_index(int(np.argmax(ndimage.gaussian_filter(luma, sigma=sigma))), luma.shape)
        return int(x), int(y)

    assert abs(brightest(1.5)[0] - 1102) <= 2 and abs(brightest(2.0)[0] - 500) <= 10
    blob = find_moon_blob(luma, **_limits())
    assert isinstance(blob, MoonBlob) and math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0, blob
    assert pe.PEAK_SMOOTHING_SIGMA_PX == 2.5


def test_a_rival_is_judged_at_its_brightest_point_after_the_same_smoothing() -> None:
    """REQ-SKY-3.3 (verdict sky-3b-v3 note (a), its mutant M1): a small rival whose one pixel at 255 lies beside its
    core's centre, beside a Moon of the predicted size. At its brightest point after the 2.5 px Gaussian, the core's
    centre, it passes the Moon test on its own; at its raw maximum, the 255 pixel, its half-peak region is under the
    minimum. The frame is refused, so the rival was judged at the smoothed point."""
    luma = _soft_moon(1200.0, 300.0, 3.5, peak=205.0, blur=1.0, luma=_soft_moon(500.3, 700.6))
    luma[300, 1201] = 255.0
    group, _ = ndimage.label(luma >= 200.0, structure=EIGHT)
    assert group[300, 1201] == group[300, 1200] != 0
    smoothed = ndimage.gaussian_filter(luma, sigma=2.5)
    assert tuple(int(v) for v in ndimage.maximum_position(smoothed, group, index=int(group[300, 1200]))) == (300, 1200)
    assert _own_test(luma, 1200, 300) and not _own_test(luma, 1201, 300)
    refusal = find_moon_blob(luma, **_limits())
    assert isinstance(refusal, Refusal) and "more than one spot passes the Moon test on its own" in refusal.reason


def test_a_rival_is_judged_by_the_moon_test_on_its_own_not_by_its_area_beside_the_first() -> None:
    """Each other spot is judged by the Moon test itself: its own peak, its own half-peak region and the same limits.
    One that passes refuses the frame, whatever its area beside the brightest; one that fails on its own does not."""
    moon = _soft_moon(400.0, 1200.0)
    # A spot of the Moon's size at 210: under the brightest's peak, but it passes on its own, so the frame is refused
    rival = _soft_moon(900.0, 400.0, peak=210.0, luma=moon)
    assert _own_test(rival, 900, 400)
    assert find_moon_blob(rival, **_limits()).reason == MORE_THAN_ONE
    # A wider spot at 199, dimmer than the Moon after smoothing, fails the floor on its own: no rival, though it rises
    # above the Moon's half-peak level over more than the Moon's own area there
    dim = _soft_moon(900.0, 400.0, 6.0, peak=199.0, luma=moon)
    px, py, _, _, labels, region = _rule(dim)
    assert (px, py) == (400, 1200) and int((labels == labels[400, 900]).sum()) > int(region.sum())
    blob = find_moon_blob(dim, **_limits())
    assert isinstance(blob, MoonBlob) and math.hypot(blob.x_px - 400.0, blob.y_px - 1200.0) < 1.0
    # A point light at 255, smaller than the Moon test allows on its own, is no rival either
    lit = _point_light(moon, 900, 400, peak=255.0, blur=1.0)
    assert not _own_test(lit, 900, 400)
    assert isinstance(find_moon_blob(lit, **_limits()), MoonBlob)
    # A lit cloud over 8 Moons across at 230 fails on its own: a clipped Moon beside it, brighter after smoothing
    # (a flat disk at 255), is still the one spot that passes
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    glow = np.round(2.0 + 228.0 * np.exp(-((xx - 900.0) ** 2 + (yy - 500.0) ** 2) / (2.0 * 60.0**2)))
    cloud = np.maximum(_frame(400.0, 1200.0, 6.0), glow)
    assert _rule(cloud)[:2] == (400, 1200) and not _own_test(cloud, 900, 500)
    blob = find_moon_blob(cloud, **_limits())
    assert isinstance(blob, MoonBlob) and math.hypot(blob.x_px - 400.0, blob.y_px - 1200.0) < 1.0


def test_a_rival_cut_by_the_frames_edge_still_refuses_and_the_moons_own_halo_is_no_rival() -> None:
    """The edge rule is the candidate's alone: a Moon-sized spot cut by the frame's left edge may itself be a cut
    Moon, so it still refuses the frame. And on a bright sky (150) a Moon at 255 has a ring of pixels at 200 to 202
    just outside its half-peak region (level 202.5); at their own half-peak level that ring's region holds the Moon's
    own peak, so it is the Moon seen again, not a rival."""
    radius = _predicted_radius_px()
    moon = _soft_moon(500.0, 700.0)
    # Dim enough at 205 that, though the smoothing reflects it whole at the edge, the Moon is still the brightest
    cut = _soft_moon(2.0, 1200.0, radius, peak=205.0, luma=moon)
    assert _rule(cut)[:2] == (500, 700) and _own_test(cut, 2, 1200)
    assert find_moon_blob(cut, **_limits()).reason == MORE_THAN_ONE
    bright = _soft_moon(500.3, 700.6, peak=255.0, background=150.0, sigma=1.0)
    _, _, peak, background, _, region = _rule(bright)
    assert (peak, background) == (255.0, 150.0) and bool(((bright >= 200.0) & ~region).any())
    blob = find_moon_blob(bright, **_limits())
    assert isinstance(blob, MoonBlob) and math.hypot(blob.x_px - 500.3, blob.y_px - 700.6) < 1.0


def test_a_lamp_twice_the_moons_width_beside_a_moon_sized_spot_is_refused() -> None:
    """The auditor's case: a lamp twice the Moon's width at 255 beside a Moon-sized spot at 235. The lamp is the
    brightest, and the Moon-sized spot passes the Moon test on its own, so neither is taken."""
    radius = _predicted_radius_px()
    luma = _soft_moon(900.0, 400.0, 2.0 * radius, peak=255.0, luma=_soft_moon(400.0, 1200.0, radius, peak=235.0))
    assert _own_test(luma, 900, 400) and _own_test(luma, 400, 1200)
    assert find_moon_blob(luma, **_limits()).reason == MORE_THAN_ONE
    # Two lamps whose areas at half their peaks are 0.39 of each other, both inside the limits: refused
    two = _soft_moon(900.0, 400.0, 1.6 * radius, peak=250.0, luma=_soft_moon(400.0, 1200.0, 2.56 * radius, peak=250.0))
    assert _own_test(two, 900, 400) and _own_test(two, 400, 1200)
    assert find_moon_blob(two, **_limits()).reason == MORE_THAN_ONE


def test_a_spot_cut_by_the_edge_of_the_frame_is_refused() -> None:
    """The spot's region grown by the two pixels its centroid is measured over must not reach the frame's edge: a
    spot cut by an edge or a corner moves its centroid off its centre."""
    radius = _predicted_radius_px()
    cut = "The frame's bright spots were not confirmed as the Moon: the brightest spot is cut by the edge of the frame."
    for x, y in ((2.0, 700.0), (700.0, HEIGHT - 3.0), (WIDTH - 1.0, 900.0), (3.0, 3.0), (WIDTH - 4.0, HEIGHT - 2.0)):
        result = find_moon_blob(_soft_moon(x, y), **_limits())
        assert isinstance(result, Refusal) and result.reason == cut, (x, y, result)
    # At the edge: a flat disk whose region ends 3 px from the left edge is whole (grown by 2 it ends 1 px inside);
    # 2 px from it, grown by 2, it reaches the edge
    for gap, whole in ((3, True), (2, False)):
        luma = _rects((gap, 800, 10, 10))
        result = find_moon_blob(luma, min_diameter_px=5.0, max_diameter_px=50.0)
        assert isinstance(result, MoonBlob) is whole, (gap, result)
        if not whole:
            assert result.reason == cut
    # Whole, the same soft Moon near a corner is found
    near = find_moon_blob(_soft_moon(radius + 12.0, radius + 12.0), **_limits())
    assert isinstance(near, MoonBlob)


def test_the_two_pixel_edge_margin_holds_on_every_edge() -> None:
    """The margin on each of the four edges (the sky-3 audit's surviving mutants C7, E1 and E2 weakened it on the top,
    bottom and right): a flat 10 x 10 disk whose region ends 3 px from the edge is whole, 2 px from it is cut."""
    cut = "The frame's bright spots were not confirmed as the Moon: the brightest spot is cut by the edge of the frame."
    for gap, whole in ((3, True), (2, False)):
        for edge, rect in (
            ("top", (800, gap, 10, 10)),
            ("bottom", (800, HEIGHT - gap - 10, 10, 10)),
            ("right", (WIDTH - gap - 10, 800, 10, 10)),
            ("left", (gap, 800, 10, 10)),
        ):
            result = find_moon_blob(_rects(rect), min_diameter_px=5.0, max_diameter_px=50.0)
            assert isinstance(result, MoonBlob) is whole, (edge, gap, result)
            if not whole:
                assert result.reason == cut, (edge, result)


def test_a_peak_under_200_is_refused_and_200_is_enough() -> None:
    refusal = find_moon_blob(_soft_moon(500.0, 700.0, peak=199.0), **_limits())
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon"
    assert refusal.reason == (
        "The frame's bright spots were not confirmed as the Moon: the brightest spot peaks at 199 of 255, under 200."
    )
    assert isinstance(find_moon_blob(_soft_moon(500.0, 700.0, peak=200.0), **_limits()), MoonBlob)
    # A dark frame's brightest point is noise
    assert "peaks at" in find_moon_blob(_noise(), **_limits()).reason
    assert pe.MIN_PEAK_LUMA == 200.0


def test_the_peak_must_be_at_least_10_times_the_noise_above_the_sky(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Moon at 244 over a bright, noisy sky at 150: read noise of 10 codes puts it under 10 times the noise (the
    frame's median and 1.4826 times its median absolute deviation), 8 codes over it."""
    for sigma, enough in ((10.0, False), (8.0, True)):
        luma = _soft_moon(500.0, 700.0, background=150.0, sigma=sigma, seed=5)
        _, _, peak, background, _, _ = _rule(luma)
        snr = (peak - background) / (1.4826 * float(np.median(np.abs(luma - background))))
        assert (snr >= 10.0) is enough, snr
        result = find_moon_blob(luma, **_limits())
        if enough:
            assert isinstance(result, MoonBlob) and result.snr == pytest.approx(snr, rel=1e-9)
        else:
            assert isinstance(result, Refusal) and result.reason == (
                f"The frame's bright spots were not confirmed as the Moon: the brightest spot peaks {snr:.1f} times "
                "the noise above the sky, under 10."
            )
    # At the edge: the measured SNR itself is enough, a hair over it is not
    luma = _soft_moon(500.0, 700.0)
    _, _, peak, background, _, _ = _rule(luma)
    snr = (peak - background) / (1.4826 * float(np.median(np.abs(luma - background))))
    assert isinstance(find_moon_blob(luma, **_limits()), MoonBlob)
    assert pe.MIN_PEAK_SNR == 10.0
    monkeypatch.setattr(pe, "MIN_PEAK_SNR", snr)
    assert isinstance(find_moon_blob(luma, **_limits()), MoonBlob)
    monkeypatch.setattr(pe, "MIN_PEAK_SNR", snr * (1.0 + 1e-9))
    assert isinstance(find_moon_blob(luma, **_limits()), Refusal)
    monkeypatch.undo()
    # A sky with no noise at all (its MAD 0) is floored at 1e-4, as horizon_solver.py floors it, so a flat clipped
    # Moon on it passes; the spot's reported SNR keeps the detector's rounding noise of the frame's code step (for
    # two codes, 4 and 255, that step is 251, so the SNR is sqrt(12))
    flat = np.full((HEIGHT, WIDTH), 4.0)
    flat[(XX - 500.0) ** 2 + (YY - 700.0) ** 2 <= 36.0] = 255.0
    clipped = find_moon_blob(flat, **_limits())
    assert isinstance(clipped, MoonBlob) and clipped.snr == pytest.approx(math.sqrt(12.0), rel=1e-9)


def test_the_region_is_above_the_half_peak_level_and_the_centroid_is_measured_over_it_grown_by_two() -> None:
    luma = _soft_moon(700.3, 500.6, 7.0, seed=3)
    blob = find_moon_blob(luma, min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(blob, MoonBlob), blob
    px, py, peak, background, _, region = _rule(luma)
    assert peak == 244.0
    grown = ndimage.binary_dilation(region, structure=EIGHT, iterations=2)
    weights = np.where(grown, np.clip(luma - background, 0.0, None), 0.0)
    yy, xx = np.indices(luma.shape)
    assert blob.x_px == pytest.approx(float((xx * weights).sum() / weights.sum()), abs=1e-9)
    assert blob.y_px == pytest.approx(float((yy * weights).sum() / weights.sum()), abs=1e-9)
    assert abs(blob.x_px - 700.3) < 0.1 and abs(blob.y_px - 500.6) < 0.1
    assert blob.area_px == int(region.sum())
    assert blob.equivalent_diameter_px == pytest.approx(2.0 * math.sqrt(region.sum() / math.pi))
    assert blob.radius_px == pytest.approx(math.sqrt(grown.sum() / math.pi))
    assert blob.flux == pytest.approx(float(weights.sum()))
    sigma_sq = float((((xx - blob.x_px) ** 2 + (yy - blob.y_px) ** 2) * weights).sum() / (2.0 * weights.sum()))
    assert blob.fwhm_px == pytest.approx(2.355 * math.sqrt(sigma_sq))
    noise = 1.4826 * float(np.median(np.abs(luma - background)))
    assert blob.snr == pytest.approx((peak - background) / noise, rel=1e-9) and blob.snr > 100.0
    assert 0.0 < blob.peak_to_total_ratio < 1.0
    assert blob.peak_to_total_ratio == pytest.approx((peak - background) / blob.flux)
    assert blob.centroid_uncertainty_px == pytest.approx(blob.fwhm_px / (2.355 * blob.snr))
    # Strictly above the half-peak level: a ring exactly at it, 123 for a peak of 244 over a sky at 2, is left out
    ringed = _rects((598, 798, 14, 14), value=123.0)
    ringed[800:810, 600:610] = 244.0
    ring = find_moon_blob(ringed, min_diameter_px=5.0, max_diameter_px=50.0)
    assert isinstance(ring, MoonBlob) and ring.area_px == 100


def test_the_blob_must_be_at_least_its_minimum_and_at_most_its_maximum_equivalent_diameter() -> None:
    frame = _rects((600, 800, 10, 10))
    diameter = 2.0 * math.sqrt(100.0 / math.pi)
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter - 1e-6, max_diameter_px=50.0), MoonBlob)
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter + 1e-6, max_diameter_px=50.0), Refusal)
    assert isinstance(find_moon_blob(frame, min_diameter_px=5.0, max_diameter_px=diameter + 1e-6), MoonBlob)
    assert isinstance(find_moon_blob(frame, min_diameter_px=5.0, max_diameter_px=diameter - 1e-6), Refusal)
    # Both limits hold their own value: "at least" the minimum, "at most" the maximum
    assert isinstance(find_moon_blob(frame, min_diameter_px=diameter, max_diameter_px=diameter), MoonBlob)


def test_every_other_spot_is_judged_by_the_same_limits_on_its_own() -> None:
    limits = {"min_diameter_px": 5.0, "max_diameter_px": 50.0}
    # 100 px beside 16 px (4.5 px across, under the 5 px the test needs): accepted; beside 25 px (5.6 px across,
    # inside the limits): refused, though it is a quarter of the first one's area
    blob = find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 4, 4)), **limits)
    assert isinstance(blob, MoonBlob) and (round(blob.x_px, 6), round(blob.y_px, 6)) == (604.5, 804.5)
    assert find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 5, 5)), **limits).reason == MORE_THAN_ONE
    # A lit square over the 50 px the test allows, at the same 255: it is the brightest after smoothing (its middle
    # stays at 255, the small square's does not), so it is the candidate, and it fails on its size
    big = find_moon_blob(_rects((600, 800, 10, 10), (100, 100, 60, 60)), **limits)
    assert isinstance(big, Refusal) and "over the 50.0 px the Moon allows" in big.reason
    # The same square dimmer than the Moon is no rival: the Moon is the brightest and the only spot that passes
    square = _rects((600, 800, 10, 10))
    square[100:160, 100:160] = 230.0
    assert isinstance(find_moon_blob(square, **limits), MoonBlob)
    # Diagonal neighbours are one spot (8-connected), so a spot touching at a corner is not a second one
    corner = find_moon_blob(_rects((600, 800, 10, 10), (610, 810, 8, 8)), **limits)
    assert isinstance(corner, MoonBlob) and corner.area_px == 164


def test_no_bright_spot_or_none_of_the_moons_size_is_refused() -> None:
    assert isinstance(find_moon_blob(_rects(), min_diameter_px=5.0, max_diameter_px=50.0), Refusal)
    small = _rects((100, 100, 3, 3), (400, 400, 4, 4), (900, 900, 2, 2))
    assert isinstance(find_moon_blob(small, min_diameter_px=5.0, max_diameter_px=50.0), Refusal)


def test_the_size_limits_are_the_moons_predicted_diameter_at_the_profiles_two_limits() -> None:
    ra, dec, radius = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    low, high = moon_diameter_limits_px(moon, PROFILE.fov_diagonal_deg_range, WIDTH, HEIGHT)
    assert low == pytest.approx(0.8 * 2.0 * radius / _scale(110.0), rel=1e-9)
    assert high == pytest.approx(8.0 * 2.0 * radius / _scale(80.0), rel=1e-9)
    assert scale_deg_per_px(95.0, WIDTH, HEIGHT) == pytest.approx(_scale(95.0), rel=1e-12)


def test_the_moon_is_lined_up_where_it_is_seen_from_the_place_not_from_the_earths_centre() -> None:
    """The pose takes the ephemeris record as it is: topocentric, its parallax applied once by ERFA's chain, and
    nothing lowers it again (tests/integration/test_moon_pose_paths.py checks it against ERFA's eraAtco13)."""
    (topo,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    (geo,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",), topocentric=False)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    shift = moon_parallax_shift_arcsec(geo.distance_au, geo.alt_deg)[1]
    assert topo.frame == "topocentric" and shift > 2000.0
    ra, dec, radius = _topocentric(MOON_MOMENT)
    assert angular_separation_deg(moon.ra_deg, moon.dec_deg, ra, dec) < 1e-9
    assert moon.alt_deg == pytest.approx(topo.alt_deg, abs=1e-9)
    assert moon.az_deg == pytest.approx(topo.az_deg, abs=1e-9)
    assert moon.radius_deg == pytest.approx(radius, rel=1e-9)
    # From the Earth's centre it stands higher by the angle between ERFA's geocentric and topocentric Moon here,
    # 3,071.5 arcsec; the first-order shift `moon_parallax_shift_arcsec` gives, 3,049.0, is 22.5 short of it
    assert angular_separation_deg(moon.ra_deg, moon.dec_deg, geo.ra_deg, geo.dec_deg) == pytest.approx(3071.4815 / 3600.0, rel=1e-3)
    assert (geo.alt_deg - moon.alt_deg) * 3600.0 == pytest.approx(3071.6375, rel=1e-3)


# --- One fit and the sweep --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("fov", "roll", "turns"), [(95.0, 0.0, 0), (80.0, 10.0, 0), (110.0, -10.0, 1), (88.0, 7.0, 3), (101.0, -4.0, 2)])
def test_one_fit_is_the_pointing_that_roll_and_field_of_view_give(fov: float, roll: float, turns: int) -> None:
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    centre_alt, centre_az = moon.alt_deg + 12.0, moon.az_deg - 9.0
    truth = _truth_wcs(centre_alt, centre_az, fov, roll, turns, MOON_MOMENT)
    mx, my = truth_pixel(truth, ra, dec)
    fitted = fit_level_pose(moon, mx, my, width=WIDTH, height=HEIGHT, fov_diagonal_deg=fov, roll_deg=roll,
                            orientation={"quarterTurnsClockwise": turns, "mirrored": False},
                            moment=MOON_MOMENT, lat=LAT, lon=LON)
    assert isinstance(fitted, WCSModel)
    assert (fitted.parity, fitted.k1, fitted.k2) == (-1, 0.0, 0.0)
    assert (fitted.crpix_x, fitted.crpix_y) == (WIDTH / 2.0, HEIGHT / 2.0)
    assert fitted.scale_deg_per_px == pytest.approx(_scale(fov), rel=1e-12)
    fx, fy = fitted.world_to_pixel(ra, dec)
    assert math.hypot(fx - mx, fy - my) <= 0.5
    assert _roll_of(fitted, turns, MOON_MOMENT) == pytest.approx(roll, abs=0.05)
    # The pose's pointing is the truth's, exactly: its crval turned by the pose's frame rotation is where the truth
    # camera's centre is seen
    frame = pe.pose_frame(moon, LAT, LON, MOON_MOMENT)
    fitted_alt, fitted_az = catalog.icrs_to_frame_altaz(frame, fitted.crval_ra_deg, fitted.crval_dec_deg)
    assert angular_separation_deg(fitted_az, fitted_alt, centre_az, centre_alt) < 1e-6
    drawn = _drawn_wcs(centre_alt, centre_az, fov, roll, turns, MOON_MOMENT)
    # Every bright star in the frame lands where the independently built truth sees it, within half a pixel, and
    # exactly where the truth camera seen through the pose's one frame rotation draws it
    for star in get_catalog_stars()[:200]:
        tx, ty = truth_pixel(truth, star.ra_deg, star.dec_deg)
        if not (0.0 <= tx < WIDTH and 0.0 <= ty < HEIGHT):
            continue
        sx, sy = fitted.world_to_pixel(star.ra_deg, star.dec_deg)
        assert math.hypot(sx - tx, sy - ty) < 0.5, star.id
        dx, dy = drawn.world_to_pixel(star.ra_deg, star.dec_deg)
        assert math.hypot(sx - dx, sy - dy) < 1e-6, star.id
    # The pinned bright stars may all lie outside this frame: the same over a 5 x 5 grid of the frame's own directions,
    # its corners included
    for gx, gy in FRAME_GRID:
        ra_g, dec_g = fitted.pixel_to_world(gx, gy)
        tx, ty = truth_pixel(truth, ra_g, dec_g)
        assert math.hypot(gx - tx, gy - ty) < 0.5, (gx, gy)
        dx, dy = drawn.world_to_pixel(ra_g, dec_g)
        assert math.hypot(gx - dx, gy - dy) < 1e-6, (gx, gy)


def test_every_closed_form_takes_the_moons_apparent_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """The camera is level in the true horizon, so every closed form the pose runs (the nine counts that decide two
    poses or none, and the nine fits) takes the Moon where it is seen: the record's apparent altitude and azimuth."""
    seen: list[tuple[float, float]] = []
    real = pe.level_centres

    def spy(moon_alt: float, moon_az: float, *args: Any, **kwargs: Any) -> tuple[tuple[float, float], ...]:
        seen.append((moon_alt, moon_az))
        return real(moon_alt, moon_az, *args, **kwargs)

    monkeypatch.setattr(pe, "level_centres", spy)
    assert isinstance(_pose(_soft_moon(500.3, 700.6)), PoseResult)
    (record,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    assert len(seen) == 18 and set(seen) == {(record.alt_deg, record.az_deg)}, seen


def test_the_frame_term_needs_a_field_of_view_for_every_frame() -> None:
    """Each swept frame's field is its crval and half its own field of view: a list of fields of view one short is
    refused, never paired up quietly."""
    pose = _pose(_soft_moon(500.3, 700.6))
    assert isinstance(pose, PoseResult)
    swept = pose.pose_estimate.swept_wcs
    fovs = [_fov_of(w) for w in swept]
    assert pe.frame_term_arcsec(pose.moon, swept, fovs, LAT, LON, pose.moment) == pytest.approx(SOFT_FRAME_TERM_ARCSEC,
                                                                                                rel=1e-9)
    with pytest.raises(ValueError):
        pe.frame_term_arcsec(pose.moon, swept, fovs[:-1], LAT, LON, pose.moment)


@pytest.mark.parametrize(("fov", "roll", "turns"), [(95.0, 0.0, 0), (80.0, 10.0, 1), (110.0, -10.0, 3)])
def test_the_closed_forms_start_is_already_the_pose(monkeypatch: pytest.MonkeyPatch, fov: float, roll: float,
                                                    turns: int) -> None:
    """The fit's start, the closed form's pointing turned to ICRS through the pose's frame rotation with the rotation
    the level line gives there, is already the camera seen through that rotation: with the polish left out it draws
    every star as the polished fit does, within 1e-6 px, so the start is in the pose's own frame."""
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    mx, my = truth_pixel(_truth_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, fov, roll, turns, MOON_MOMENT), ra, dec)
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=fov, roll_deg=roll,
                  orientation={"quarterTurnsClockwise": turns, "mirrored": False}, moment=MOON_MOMENT, lat=LAT, lon=LON)
    polished = fit_level_pose(moon, mx, my, **kwargs)
    assert isinstance(polished, WCSModel)

    class Unpolished:
        def __init__(self, start: np.ndarray) -> None:
            self.x = np.asarray(start, dtype=np.float64)

    monkeypatch.setattr(pe.optimize, "least_squares", lambda fun, start, **kw: Unpolished(start))
    started = fit_level_pose(moon, mx, my, **kwargs)
    assert isinstance(started, WCSModel)
    for star in get_catalog_stars()[:200]:
        px, py = polished.world_to_pixel(star.ra_deg, star.dec_deg)
        if not (0.0 <= px < WIDTH and 0.0 <= py < HEIGHT):
            continue
        sx, sy = started.world_to_pixel(star.ra_deg, star.dec_deg)
        assert math.hypot(sx - px, sy - py) < 1e-6, star.id
    for gx, gy in FRAME_GRID:
        sx, sy = started.world_to_pixel(*polished.pixel_to_world(gx, gy))
        assert math.hypot(sx - gx, sy - gy) < 1e-6, (gx, gy)


def test_a_fit_that_shows_the_sky_upside_down_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The level constraint holds for a frame turned by half a turn too; the fit must refuse that one. Built by hand:
    the upright truth turned by 180 degrees puts the Moon at the pixel mirrored through the frame centre, so the fit
    is handed that pixel and that WCS (the truth as the pose draws it, through its own frame rotation), which meets
    all three residuals."""
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    truth = _drawn_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, 95.0, 0.0, 0, MOON_MOMENT)
    mx, my = truth.world_to_pixel(ra, dec)

    class Fit:
        x = np.array([truth.crval_ra_deg, truth.crval_dec_deg, truth.rotation_deg + 180.0])

    monkeypatch.setattr(pe.optimize, "least_squares", lambda *args, **kwargs: Fit())
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None,
                  moment=MOON_MOMENT, lat=LAT, lon=LON)
    turned = fit_level_pose(moon, WIDTH - mx, HEIGHT - my, **kwargs)
    assert isinstance(turned, Refusal) and turned.kind == "fit"
    # The same hand-built fit the right way up is accepted, so only the turn refused it
    Fit.x = np.array([truth.crval_ra_deg, truth.crval_dec_deg, truth.rotation_deg])
    assert isinstance(fit_level_pose(moon, mx, my, **kwargs), WCSModel)
    # The spot at the frame centre itself: the half-turned frame keeps the Moon there, the horizon's slope and the
    # closed form's centre, so only its upside-down sky refuses it
    monkeypatch.undo()
    centred = _truth_wcs(moon.alt_deg, moon.az_deg, 95.0, 0.0, 0, MOON_MOMENT)
    cx, cy = truth_pixel(centred, ra, dec)
    upright = fit_level_pose(moon, cx, cy, **kwargs)
    assert isinstance(upright, WCSModel)
    Fit.x = np.array([upright.crval_ra_deg, upright.crval_dec_deg, upright.rotation_deg + 180.0])
    monkeypatch.setattr(pe.optimize, "least_squares", lambda *args, **kwargs: Fit())
    flipped = fit_level_pose(moon, cx, cy, **kwargs)
    assert isinstance(flipped, Refusal) and flipped.kind == "fit"


def test_the_fit_is_accepted_only_up_to_half_a_pixel() -> None:
    assert pe.MAX_FIT_RESIDUAL_PX == 0.5
    assert pe._fit_accepted(np.array([0.5, -0.5, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, 0.5000001, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, -0.5000001, 0.0]))
    assert not pe._fit_accepted(np.array([0.0, float("nan"), 0.0]))


def _pose(luma: np.ndarray, turns: int = 0, frame_time: float = 0.0) -> PoseResult | Refusal:
    return head_worn_moon_pose(
        luma, frame_time_sec=frame_time, moment=frame_moment(MOON_MOMENT, frame_time), lat=LAT, lon=LON,
        profile=PROFILE, orientation={"quarterTurnsClockwise": turns, "mirrored": False},
    )


def test_each_fit_starts_at_the_closed_forms_pose(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-SKY-3.9 (row 3.9-4 of sky-3b-v4's statement table, which no test pinned): each of the nine fits is started
    at the one level pose the closed form gives for its swept field of view and roll."""
    starts: list[np.ndarray] = []
    real = pe.optimize.least_squares

    def recording(fun: Any, x0: Any, **kwargs: Any) -> Any:
        starts.append(np.array(x0, dtype=np.float64))
        return real(fun, x0, **kwargs)

    monkeypatch.setattr(pe.optimize, "least_squares", recording)
    pose = _pose(_frame(500.0, 700.0, 6.0))
    assert isinstance(pose, PoseResult) and len(starts) == 9
    swept = [(fov, roll) for fov in (80.0, 95.0, 110.0) for roll in (-10.0, 0.0, 10.0)]
    frame = pe.pose_frame(pose.moon, LAT, LON, MOON_MOMENT)
    for (fov, roll), start in zip(swept, starts):
        ((alt, az),) = pe.level_centres(
            pose.moon.alt_deg, pose.moon.az_deg, pose.blob.x_px, pose.blob.y_px, width=WIDTH, height=HEIGHT,
            fov_diagonal_deg=fov, roll_deg=roll, turns=0,
        )
        start_alt, start_az = catalog.icrs_to_frame_altaz(frame, start[0], start[1])
        assert angular_separation_deg(start_az, start_alt, az, alt) < 1e-9, (fov, roll)


def test_the_head_worn_pose_states_its_assumptions_with_the_numbers_it_used() -> None:
    """REQ-SKY-3.5 (rows 3.5-7 and 3.5-9 of sky-3b-v4's statement table, which no test pinned): the level head with the
    swept rolls, and the beacon term with the Moon's radius from its distance and the uncertainty its ephemeris record
    states, word for word."""
    pose = _pose(_frame(500.0, 700.0, 6.0))
    assert isinstance(pose, PoseResult)
    assumptions = pose.pose_estimate.assumptions
    assert len(assumptions) == 7
    assert assumptions[2] == (
        "The head was level: the horizon's roll in the upright frame is within 10 degrees either way; the sweep takes "
        "-10, 0 and +10 degrees, and a second level pose or none at any of them refuses the pose."
    )
    (record,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    swept = pose.pose_estimate.swept_wcs
    term = pe.frame_term_arcsec(pose.moon, swept, [_fov_of(w) for w in swept], LAT, LON, MOON_MOMENT)
    assert assumptions[5] == (
        "The spot's light centroid is taken to lie on the Moon's disk, and the Moon where its ephemeris puts it: the "
        f"beacon term, the Moon's predicted radius of {_topocentric(MOON_MOMENT)[2]:.2f} degrees plus the ephemeris's "
        f"stated uncertainty of {record.uncertainty_arcsec:,.0f} arcsec plus {term:,.0f} arcsec for drawing every "
        "position through one rotation, is taken to cover overexposure, which spreads the light, a partial phase, "
        "which moves it toward the lit edge, the ephemeris's own error, and the aberration and light deflection that "
        "differ across the frame from the Moon's, which one rotation cannot follow."
    )


def test_the_sweep_takes_the_profiles_limits_and_middle_and_rolls_of_ten_degrees_either_way() -> None:
    assert pe.SWEPT_ROLLS_DEG == (-10.0, 0.0, 10.0)
    pose = _pose(_frame(500.0, 700.0, 6.0))
    assert isinstance(pose, PoseResult)
    estimate = pose.pose_estimate
    swept = estimate.swept_wcs
    assert len(swept) == 9
    pairs = sorted((round(_fov_of(w), 6), round(_roll_of(w, 0, MOON_MOMENT), 1)) for w in swept)
    assert pairs == sorted((fov, roll) for fov in (80.0, 95.0, 110.0) for roll in (-10.0, 0.0, 10.0))
    # The nominal WCS is the middle field of view at roll 0, and it is one of the swept ones
    assert pose.wcs in swept
    assert round(_fov_of(pose.wcs), 6) == 95.0 and abs(_roll_of(pose.wcs, 0, MOON_MOMENT)) < 0.05
    assert (estimate.fov_diagonal_deg, estimate.roll_deg) == (95.0, 0.0)
    # The stated centre is where the camera's centre is seen: the crval turned by the pose's frame rotation, which is
    # the closed form's pointing in the apparent horizon for the Moon's apparent place; the crval's own apparent place
    # (the direction drawn at the centre) lies within the frame term of it, and not on it
    frame = pe.pose_frame(pose.moon, LAT, LON, MOON_MOMENT)
    centre_alt, centre_az = catalog.icrs_to_frame_altaz(frame, pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg)
    assert estimate.center_alt_deg == pytest.approx(centre_alt, abs=1e-9)
    assert estimate.center_az_deg == pytest.approx(centre_az, abs=1e-9)
    ((pointing_alt, pointing_az),) = pe.level_centres(
        pose.moon.alt_deg, pose.moon.az_deg, pose.blob.x_px, pose.blob.y_px, width=WIDTH, height=HEIGHT,
        fov_diagonal_deg=95.0, roll_deg=0.0, turns=0,
    )
    assert angular_separation_deg(estimate.center_az_deg, estimate.center_alt_deg, pointing_az, pointing_alt) < 1e-6
    seen_alt, seen_az = icrs_to_altaz(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, LAT, LON, MOON_MOMENT)
    drawn_miss = angular_separation_deg(estimate.center_az_deg, estimate.center_alt_deg, seen_az, seen_alt) * 3600.0
    assert 1.0 < drawn_miss <= FRAME_TERM_ARCSEC, drawn_miss
    offsets = [angular_separation_deg(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg) for w in swept]
    assert estimate.max_center_offset_deg == pytest.approx(max(offsets), abs=1e-9) and max(offsets) > 1.0
    _, _, radius = _topocentric(MOON_MOMENT)
    # The beacon term: the Moon's radius, the record's stated uncertainty (44.06 arcsec) and the frame term
    assert estimate.beacon_centroid_uncertainty_deg == pytest.approx(radius + (MOON_STATED_ARCSEC + FRAME_TERM_ARCSEC) / 3600.0, rel=1e-9)
    assert (estimate.method, estimate.beacon) == ("moon_camera_profile_level_head", "moon")
    assert META_RAY_BAN_DISPLAY_MODEL in estimate.fov_source and "[open]" in estimate.fov_source
    words = " ".join(estimate.assumptions)
    for needed in ("80 to 110 degrees", "10 degrees either way", "rectilinear", "topocentric", "refraction", "radius",
                   "The bright spot is taken to be the Moon", "a lamp or another light of similar size",
                   f"stated uncertainty of 44 arcsec plus {FRAME_TERM_ARCSEC:,.0f} arcsec for drawing every position "
                   "through one rotation"):
        assert needed in words, needed
    assert "The spot is the Moon" not in words
    # The profile's range is for the camera with no zoom, and nothing in the file is read to show a zoom was not used
    assert "The camera was not zoomed" in words
    for text in (*estimate.assumptions, estimate.fov_source):
        _capture_free(text)


def test_frames_are_tried_in_time_order_and_the_first_that_passes_is_kept() -> None:
    """Each frame is judged on its own: a brighter clipped Moon in a later frame does not stop the soft one in an
    earlier frame, and a point light earlier still is refused."""
    dark = np.full((HEIGHT, WIDTH), 2.0)
    frames = [
        (5.0, frame_moment(MOON_MOMENT, 5.0), _frame(900.0, 400.0, 6.0)),
        (1.0, frame_moment(MOON_MOMENT, 1.0), dark),
        (4.0, frame_moment(MOON_MOMENT, 4.0), _soft_moon(800.0, 300.0)),
        (2.0, frame_moment(MOON_MOMENT, 2.0), _point_light(_noise(), 600, 900)),
        (3.0, frame_moment(MOON_MOMENT, 3.0), _soft_moon(500.3, 700.6)),
    ]
    pose = pe.pose_from_frames(frames, lat=LAT, lon=LON, profile=PROFILE, orientation=None)
    assert isinstance(pose, PoseResult)
    assert pose.frame_time_sec == 3.0 and pose.moment == frame_moment(MOON_MOMENT, 3.0)
    assert math.hypot(pose.blob.x_px - 500.3, pose.blob.y_px - 700.6) < 1.0


def test_any_refused_swept_fit_refuses_the_pose(monkeypatch: pytest.MonkeyPatch) -> None:
    """One of the nine fits refused, here the widest field of view at roll +10 alone: the pose is refused, since its
    uncertainty could not be bounded, never left with eight."""
    real = pe.fit_level_pose

    def refuse_one(*args: Any, fov_diagonal_deg: float, roll_deg: float, **kwargs: Any) -> WCSModel | Refusal:
        if (fov_diagonal_deg, roll_deg) == (110.0, 10.0):
            return Refusal("fit", "refused for the test")
        return real(*args, fov_diagonal_deg=fov_diagonal_deg, roll_deg=roll_deg, **kwargs)

    monkeypatch.setattr(pe, "fit_level_pose", refuse_one)
    result = _pose(_soft_moon(500.3, 700.6))
    assert isinstance(result, Refusal) and result.kind == "fit" and result.blob is not None


def test_the_beacon_term_is_the_moons_radius_plus_its_stated_uncertainty_read_at_run_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The beacon term covers where the spot's light sits on the Moon's disk (its predicted radius) and where the
    Moon itself is (the ephemeris record's own stated uncertainty, 44.06 arcsec here) plus the frame term (what the
    WCS's one rotation leaves out across the frame), and the warning's bound counts it. Read from the record at run
    time: a record stating 10 degrees more widens both by 10 degrees."""
    luma = _soft_moon(500.3, 700.6)
    _, _, radius = _topocentric(MOON_MOMENT)
    (moon,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    assert moon.uncertainty_arcsec == pytest.approx(MOON_STATED_ARCSEC, rel=1e-9)
    pose = _pose(luma).pose_estimate
    assert pose.beacon_centroid_uncertainty_deg == pytest.approx(radius + (MOON_STATED_ARCSEC + SOFT_FRAME_TERM_ARCSEC) / 3600.0, rel=1e-9)
    out = complete_sky(_solver_solution(), _asset(), frames=lambda: [(0.0, luma)])
    within = math.ceil(out.pose_estimate.max_center_offset_deg + radius + (MOON_STATED_ARCSEC + SOFT_FRAME_TERM_ARCSEC) / 3600.0)
    assert f"within about {within} degrees near the bright spot" in out.warnings[-2]
    real = pe.compute_solar_system_ephemerides

    def stating_ten_degrees_more(*args: Any, **kwargs: Any) -> list[Any]:
        return [replace(r, uncertainty_arcsec=r.uncertainty_arcsec + 36000.0) if r.name == "Moon" else r
                for r in real(*args, **kwargs)]

    monkeypatch.setattr(pe, "compute_solar_system_ephemerides", stating_ten_degrees_more)
    wide = complete_sky(_solver_solution(), _asset(), frames=lambda: [(0.0, luma)])
    assert wide.pose_estimate.beacon_centroid_uncertainty_deg == pytest.approx(radius + (MOON_STATED_ARCSEC + SOFT_FRAME_TERM_ARCSEC) / 3600.0 + 10.0, rel=1e-9)
    assert f"within about {within + 10} degrees near the bright spot" in wide.warnings[-2]
    assert "radius" in " ".join(wide.pose_estimate.assumptions)


# --- The level poses in closed form, and when they refuse the pose ---------------------------------------------------


def _near_a_branch_edge(moon_alt: float, x_px: float, y_px: float, fov: float, roll: float, turns: int) -> bool:
    """Whether the pixel lies within a hair of where a level pose appears or vanishes (the two poses meet, or one
    points within 0.01 degree of the zenith or the nadir), where a numerical search and a closed form may count
    differently. Written from the level camera's height equation, sin(a0) + Y cos(a0) = sin(Moon) sqrt(1 + X^2 + Y^2)
    (the verdict's finding 1)."""
    xt, yt = _tangent_of_pixel(x_px, y_px, fov, roll, turns)
    q = math.sin(math.radians(moon_alt)) * math.sqrt(1.0 + xt * xt + yt * yt) / math.sqrt(1.0 + yt * yt)
    if abs(q - 1.0) < 1e-6:
        return True
    if q > 1.0:
        return False
    t, phi = math.asin(q), math.atan(yt)
    return any(abs(abs(math.degrees(a)) - 90.0) < 0.01 for a in (t - phi, math.pi - t - phi))


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    moon_alt=st.floats(5.0, 89.0), moon_az=st.floats(0.0, 360.0, exclude_max=True), u=st.floats(0.0, 1.0),
    v=st.floats(0.0, 1.0), fov=st.floats(80.0, 110.0), roll=st.floats(-10.0, 10.0), turns=st.sampled_from([0, 1, 2, 3]),
)
# Two poses (the Moon high, near the top of the frame), none, one, and a low Moon in a corner by north
@example(moon_alt=59.6, moon_az=180.0, u=0.5, v=0.093, fov=95.0, roll=0.0, turns=0)
@example(moon_alt=57.9, moon_az=180.0, u=0.732, v=0.783, fov=110.0, roll=10.0, turns=0)
@example(moon_alt=33.0, moon_az=224.3, u=0.5, v=0.5, fov=95.0, roll=0.0, turns=0)
@example(moon_alt=10.0, moon_az=359.9, u=0.0, v=1.0, fov=80.0, roll=-10.0, turns=3)
def test_the_closed_form_finds_every_level_pose_the_independent_search_finds(
    moon_alt: float, moon_az: float, u: float, v: float, fov: float, roll: float, turns: int,
) -> None:
    x, y = u * (WIDTH - 1.0), v * (HEIGHT - 1.0)
    assume(not _near_a_branch_edge(moon_alt, x, y, fov, roll, turns))
    searched = _level_poses_by_search(moon_alt, moon_az, x, y, fov, roll, turns)
    closed = pe.level_centres(moon_alt, moon_az, x, y, width=WIDTH, height=HEIGHT, fov_diagonal_deg=fov,
                              roll_deg=roll, turns=turns)
    assert len(closed) == len(searched), (closed, searched)
    for (alt, az), (s_alt, s_az) in zip(closed, searched):
        assert angular_separation_deg(az, alt, s_az, s_alt) < 1e-6


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    moon_alt=st.floats(5.0, 89.0), u=st.floats(0.0, 1.0), v=st.floats(0.0, 1.0), roll=st.floats(-10.0, 10.0),
    turns=st.sampled_from([0, 1, 2, 3]), narrow=st.floats(80.0, 110.0), wide=st.floats(80.0, 110.0),
)
@example(moon_alt=59.6, u=0.5, v=0.093, roll=0.0, turns=0, narrow=95.0, wide=110.0)
@example(moon_alt=57.9, u=0.732, v=0.783, roll=10.0, turns=0, narrow=108.0, wide=110.0)
# Two poses at the narrower field of view that meet and vanish at the wider one: not one at either
@example(moon_alt=63.0, u=0.0, v=0.0, roll=0.0, turns=0, narrow=80.0, wide=99.0)
def test_a_level_pose_count_that_is_not_one_stays_not_one_at_any_wider_field_of_view(
    moon_alt: float, u: float, v: float, roll: float, turns: int, narrow: float, wide: float,
) -> None:
    """For a fixed pixel a wider field of view puts the Moon farther from the frame centre. The count of level poses
    can then leave one (a second pose appears, or the one is lost, and two can meet and vanish) but never come back
    to one: so one pose at the widest swept field of view means one at every narrower field of view in the stated
    range, at that roll."""
    x, y = u * (WIDTH - 1.0), v * (HEIGHT - 1.0)
    assume(narrow < wide)
    assume(not any(_near_a_branch_edge(moon_alt, x, y, fov, roll, turns) for fov in (narrow, wide)))
    at_narrow = len(_level_poses_by_search(moon_alt, 180.0, x, y, narrow, roll, turns))
    at_wide = len(_level_poses_by_search(moon_alt, 180.0, x, y, wide, roll, turns))
    if at_narrow != 1:
        assert at_wide != 1, (at_narrow, at_wide)


def _two_pose_scene() -> tuple[float, float, np.ndarray, list[tuple[float, float]]]:
    """The auditor's scene (verdict finding 1): a place where the Moon stands about 60 degrees up, a level head at
    the nominal field of view and roll, the camera 85 degrees up with its back to the Moon, so the Moon sits near the
    top of the frame. Returns the place, the frame and the level poses for the Moon's pixel there."""
    lat, lon = _place_with_moon(60.0, 0.0)
    ra, dec, radius = _topocentric(MOON_MOMENT, lat, lon)
    alt, az = icrs_to_altaz(ra, dec, lat, lon, MOON_MOMENT)
    truth = _truth_wcs(85.0, (az + 180.0) % 360.0, 95.0, 0.0, 0, MOON_MOMENT, lat, lon)
    mx, my = truth_pixel(truth, ra, dec, lat, lon, MOON_MOMENT)
    return lat, lon, _soft_moon(mx, my, radius / _scale(95.0)), _level_poses_by_search(alt, az, mx, my, 95.0, 0.0, 0)


def _blob_at(luma: np.ndarray, moment: str, lat: float, lon: float) -> MoonBlob:
    moon = topocentric_moon(moment, lat, lon)
    low, high = moon_diameter_limits_px(moon, PROFILE.fov_diagonal_deg_range, WIDTH, HEIGHT)
    blob = find_moon_blob(luma, min_diameter_px=low, max_diameter_px=high)
    assert isinstance(blob, MoonBlob), blob
    return blob


def test_two_level_poses_refuse_the_pose_with_a_plain_sentence() -> None:
    lat, lon, luma, poses = _two_pose_scene()
    # The search finds both: the camera facing the Moon about 24 degrees up, and the truth, 85 degrees up
    assert len(poses) == 2 and abs(poses[1][0] - 85.0) < 1e-6 and 20.0 < poses[0][0] < 30.0
    assert _sun_alt(MOON_MOMENT, lat, lon) < -18.0
    solver, asset = _solver_solution(lat=lat, lon=lon), _asset(lat=lat, lon=lon)
    out = complete_sky(solver, asset, frames=lambda: [(2.0, luma)])
    assert out.status == "predicted_only", out.warnings
    blob = _blob_at(luma, frame_moment(MOON_MOMENT, 2.0), lat, lon)
    reason = (
        "The sky is not lined up over the picture: the bright spot taken to be the Moon fits two different directions "
        f"of view, so which way the camera faced is not known. The spot is at ({blob.x_px:.0f}, {blob.y_px:.0f}) px in "
        "the frame 2.00 s into the clip; both directions keep a level head inside the stated field of view and roll."
    )
    _assert_sky_2_plus(out, solver, asset, reason)


def _counting_fits(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts the least-squares fits run: the closed form decides a refusal for the whole sweep before any fit."""
    calls: list[int] = []
    real = pe.optimize.least_squares

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(pe.optimize, "least_squares", counted)
    return calls


def test_two_poses_at_one_swept_value_and_none_at_another_refuse_for_the_two_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Moon about 55 degrees up and the spot near the top left (found by the lane's search): one level pose at
    80 degrees, two at 95, and at 110 two then none. Two directions of view is the sentence, decided by the closed
    form before any fit is run."""
    lat, lon = _place_with_moon(55.0, 0.0)
    ra, dec, radius = _topocentric(MOON_MOMENT, lat, lon)
    alt, az = icrs_to_altaz(ra, dec, lat, lon, MOON_MOMENT)
    x, y = 0.05 * WIDTH, 0.1 * HEIGHT
    luma = _soft_moon(x, y, radius / _scale(95.0))
    blob = _blob_at(luma, MOON_MOMENT, lat, lon)
    counts = [len(_level_poses_by_search(alt, az, blob.x_px, blob.y_px, fov, roll, 0))
              for fov in (80.0, 95.0, 110.0) for roll in (-10.0, 0.0, 10.0)]
    assert 2 in counts and 0 in counts, counts
    fits = _counting_fits(monkeypatch)
    out = complete_sky(_solver_solution(lat=lat, lon=lon), _asset(lat=lat, lon=lon), frames=lambda: [(1.0, luma)])
    assert out.status == "predicted_only" and fits == []
    assert out.warnings[-2] == pe.two_poses_reason(blob.x_px, blob.y_px, "in the frame 1.00 s into the clip")


def test_one_fit_refuses_a_pixel_with_two_level_poses_or_none() -> None:
    lat, lon, luma, poses = _two_pose_scene()
    moon = topocentric_moon(MOON_MOMENT, lat, lon)
    blob = _blob_at(luma, MOON_MOMENT, lat, lon)
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None,
                  moment=MOON_MOMENT, lat=lat, lon=lon)
    assert fit_level_pose(moon, blob.x_px, blob.y_px, **kwargs).kind == "two_poses"
    # The Moon about 58 degrees up and the spot low and right: none at the widest field of view
    lat, lon = _place_with_moon(58.4, 0.0)
    moon = topocentric_moon(MOON_MOMENT, lat, lon)
    none = fit_level_pose(moon, 0.732 * WIDTH, 0.783 * HEIGHT, **{**kwargs, "fov_diagonal_deg": 110.0, "lat": lat,
                                                                  "lon": lon})
    assert none.kind == "ranges"
    assert none.reason == pe.level_ranges_reason(0.732 * WIDTH, 0.783 * HEIGHT, "in the frame", 110.0, 0.0)


def test_a_fit_that_leaves_the_closed_forms_pose_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handed a closed-form centre one degree too high, least squares still reaches the one true pose: its centre is
    then a degree from the closed form's, more than 0.001 degree, and the fit is refused rather than kept."""
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    truth = _truth_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, 95.0, 0.0, 0, MOON_MOMENT)
    mx, my = truth_pixel(truth, ra, dec)
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None,
                  moment=MOON_MOMENT, lat=LAT, lon=LON)
    assert isinstance(fit_level_pose(moon, mx, my, **kwargs), WCSModel)
    real = pe.level_centres
    monkeypatch.setattr(pe, "level_centres", lambda *a, **k: tuple((alt + 1.0, az) for alt, az in real(*a, **k)))
    refused = fit_level_pose(moon, mx, my, **kwargs)
    assert isinstance(refused, Refusal) and refused.kind == "fit"


def test_the_fit_stays_within_0_001_degree_of_the_closed_forms_pose(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-SKY-3.9's bound itself (verdict sky-3b-v4 finding 1): handed a closed-form centre 0.0009 degree too high,
    the fit still reaches the one true pose, 0.0009 degree from the closed form's, and is kept; 0.0011 degree too
    high, it is refused."""
    ra, dec, _ = _topocentric(MOON_MOMENT)
    moon = topocentric_moon(MOON_MOMENT, LAT, LON)
    truth = _truth_wcs(moon.alt_deg + 12.0, moon.az_deg - 9.0, 95.0, 0.0, 0, MOON_MOMENT)
    mx, my = truth.world_to_pixel(ra, dec)
    kwargs = dict(width=WIDTH, height=HEIGHT, fov_diagonal_deg=95.0, roll_deg=0.0, orientation=None,
                  moment=MOON_MOMENT, lat=LAT, lon=LON)
    real = pe.level_centres
    for shift, kept in ((0.0009, True), (0.0011, False)):
        monkeypatch.setattr(
            pe, "level_centres", lambda *a, shift=shift, **k: tuple((alt + shift, az) for alt, az in real(*a, **k))
        )
        result = fit_level_pose(moon, mx, my, **kwargs)
        assert isinstance(result, WCSModel) is kept, (shift, result)
    assert pe.SAME_POSE_DEG == 1e-3


def test_two_frame_times_are_the_same_frame_within_a_microsecond() -> None:
    """REQ-SKY-3.6's same frame (rows 3.6-8a and 3.6-8b of sky-3b-v5's table): a detection on the spot 0.75
    microseconds from the pose's frame time is the spot itself, left out; one 1.5 microseconds away is another
    frame's, kept, with the sentence that says why."""
    frame_time = 3.0
    luma = _frame(500.0, 700.0, 8.0)
    blob = _pose(luma, frame_time=frame_time).blob
    for offset, kept in ((0.75e-6, False), (1.5e-6, True)):
        detection = SourceDetection(blob.x_px, blob.y_px, 900.0, 40.0, 3.0, 0.1, asset_id=ASSET_ID,
                                    frame_time_sec=frame_time + offset)
        out = complete_sky(_solver_solution([detection]), _asset(), frames=lambda: [(frame_time, luma)])
        assert out.status == "approximate", out.warnings
        others = [f for f in out.findings if f.evidence_class == "observed" and f.id != "finding-observed-moon"]
        (spot,) = [f for f in out.findings if f.id == "finding-observed-moon"]
        assert (len(others), spot.limitations[-1] == OTHER_FRAMES_KEPT) == ((1, True) if kept else (0, False)), offset


def test_a_swept_value_with_no_level_pose_refuses_with_a_sentence_that_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """One level pose at the nominal field of view and roll, none at the widest field of view (the Moon about 58
    degrees up, the spot low and right, found by the lane's search): the nominal fit exists, so the sentence says at
    one or more of the stated fields of view and rolls no level head puts the Moon on the spot, never that none does. The
    closed form decides it before any fit is run."""
    lat, lon = _place_with_moon(58.4, 0.0)
    ra, dec, radius = _topocentric(MOON_MOMENT, lat, lon)
    alt, az = icrs_to_altaz(ra, dec, lat, lon, MOON_MOMENT)
    x, y = 0.732 * WIDTH, 0.783 * HEIGHT
    (nominal,) = _level_poses_by_search(alt, az, x, y, 95.0, 0.0, 0)
    assert _level_poses_by_search(alt, az, x, y, 110.0, 0.0, 0) == []
    luma = _soft_moon(x, y, radius / _scale(95.0))
    solver, asset = _solver_solution(lat=lat, lon=lon), _asset(lat=lat, lon=lon)
    # Counted from here: the test's own search above polishes its candidates with the same least squares
    fits = _counting_fits(monkeypatch)
    out = complete_sky(solver, asset, frames=lambda: [(1.0, luma)])
    assert out.status == "predicted_only", out.warnings
    blob = _blob_at(luma, frame_moment(MOON_MOMENT, 1.0), lat, lon)
    reason = (
        "The sky is not lined up over the picture: at one or more of the fields of view and head rolls the camera is "
        f"stated to have, no level head puts the Moon on the bright spot. The spot is at ({blob.x_px:.0f}, "
        f"{blob.y_px:.0f}) px in the frame 1.00 s into the clip; the field of view was 80 to 110 degrees across the diagonal and the roll within 10 "
        "degrees either way."
    )
    assert fits == []
    _assert_sky_2_plus(out, solver, asset, reason)
    assert nominal[0] > alt


@pytest.mark.parametrize("dec", [95.0, 120.0, 179.0, -95.0, -150.0])
def test_a_centre_past_a_pole_is_the_same_pointing_brought_back_inside_the_range(dec: float) -> None:
    past = WCSModel(crval_ra_deg=300.0, crval_dec_deg=dec, crpix_x=WIDTH / 2.0, crpix_y=HEIGHT / 2.0,
                    scale_deg_per_px=_scale(95.0), rotation_deg=170.0, parity=-1, width=WIDTH, height=HEIGHT)
    back = pe._normalized(past)
    assert -90.0 <= back.crval_dec_deg <= 90.0 and 0.0 <= back.crval_ra_deg < 360.0
    assert -180.0 <= back.rotation_deg < 180.0
    drawn = 0
    for star in get_catalog_stars()[:400]:
        a, b = past.world_to_pixel(star.ra_deg, star.dec_deg), back.world_to_pixel(star.ra_deg, star.dec_deg)
        assert math.isfinite(a[0]) == math.isfinite(b[0])
        if math.isfinite(a[0]):
            assert math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6, star.id
            drawn += 1
    assert drawn >= 1


def test_a_fit_that_ends_past_a_pole_is_brought_back_inside_the_contracts_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auditor found fits whose centre ended past a pole, the same pointing written with a declination beyond 90
    that the contract refuses (verdict finding 5; their 25 rows rerun in the lane's pole_cases.py). Here each of the
    nine fits is handed exactly that: every WCS of the solution is brought back inside the contract's range and draws
    the same sky."""
    luma = _soft_moon(500.3, 700.6)
    plain = complete_sky(_solver_solution(), _asset(), frames=lambda: [(0.0, luma)])
    real = pe.optimize.least_squares

    def past_the_pole(fun: Any, x0: Any, **kwargs: Any) -> Any:
        fit = real(fun, x0, **kwargs)
        ra, dec, rotation = fit.x
        fit.x = np.array([ra + 180.0, math.copysign(180.0, dec) - dec, rotation + 180.0])
        return fit

    monkeypatch.setattr(pe.optimize, "least_squares", past_the_pole)
    out = complete_sky(_solver_solution(), _asset(), frames=lambda: [(0.0, luma)])
    assert out.status == "approximate", out.warnings
    for wcs, same in zip((out.wcs, *out.pose_estimate.swept_wcs), (plain.wcs, *plain.pose_estimate.swept_wcs)):
        assert -90.0 <= wcs.crval_dec_deg <= 90.0 and 0.0 <= wcs.crval_ra_deg < 360.0
        assert -180.0 <= wcs.rotation_deg < 180.0
        for star in get_catalog_stars()[:100]:
            a, b = wcs.world_to_pixel(star.ra_deg, star.dec_deg), same.world_to_pixel(star.ra_deg, star.dec_deg)
            if math.isfinite(a[0]) or math.isfinite(b[0]):
                assert math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6
    ObservationSolution(**{**out.__dict__})


# --- By day --------------------------------------------------------------------------------------------------------


def _daytime_place() -> tuple[float, float]:
    """A synthetic place where the Moon stands 15 degrees up while the Sun is more than 5 degrees up at the fixtures'
    moment: the first bearing from the sub-lunar point, in steps of 10 degrees, that has it."""
    for bearing in range(0, 360, 10):
        lat, lon = _place_with_moon(15.0, float(bearing))
        if _sun_alt(MOON_MOMENT, lat, lon) > 5.0:
            return lat, lon
    raise AssertionError("no daytime place with the Moon up at the fixtures' moment")


def test_frames_that_mix_the_moon_not_up_and_by_day_answer_by_day() -> None:
    """REQ-SKY-3.12 as the code states it (the sky-3b audit's finding): with no spot searched, the Moon is said to be
    not up only when it is not up in every frame; frames that mix the Moon not up and by day answer by day, in
    either order. At the daytime place the fixtures' moment has the Moon up and the Sun up; a later hour has the Moon
    down."""
    lat, lon = _daytime_place()
    day = MOON_MOMENT
    down = next(
        moment
        for moment in (frame_moment(MOON_MOMENT, hours * 3600.0) for hours in range(1, 25))
        if compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",))[0].alt_deg <= pe.MIN_MOON_ALT_DEG
    )
    luma = _soft_moon(500.3, 700.6)

    def answer(*moments: str) -> Refusal:
        result = pe.pose_from_frames([(float(i), m, luma) for i, m in enumerate(moments)], lat=lat, lon=lon,
                                     profile=PROFILE, orientation=None)
        assert isinstance(result, Refusal)
        return result

    for moments in ((down, day), (day, down), (day, day)):
        refusal = answer(*moments)
        assert (refusal.kind, refusal.reason) == ("daytime", pe.DAYTIME), moments
    refusal = answer(down, down)
    assert (refusal.kind, refusal.reason) == ("moon_not_up", pe.MOON_NOT_UP)


def test_by_day_with_the_moon_down_the_reason_given_is_that_the_moon_was_not_up() -> None:
    """REQ-SKY-3.10 as the code states it (verdict sky-3b-v3 finding 1): the Moon is checked before the Sun, at the
    solution's moment as at each frame's own, so by day with the Moon down, the usual daytime case, the reason given
    is that the Moon was not up, and no frame is read. The same for a camera with no head-worn profile, whose frames
    the Moon plus horizon pose would search, and for that pose on one frame."""
    lat, lon = next(
        p for p in (_place_with_moon(-30.0, float(b)) for b in range(0, 360, 10)) if _sun_alt(MOON_MOMENT, *p) > 5.0
    )
    assert -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0, (lat, lon)
    assert compute_solar_system_ephemerides(MOON_MOMENT, lat, lon, bodies=("Moon",))[0].alt_deg < 0.0
    luma = _soft_moon(500.3, 700.6, peak=255.0)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, luma)]

    for model in (META_RAY_BAN_DISPLAY_MODEL, None):
        solver, asset = _solver_solution(lat=lat, lon=lon), _asset(lat=lat, lon=lon, model=model)
        out = complete_sky(solver, asset, frames=frames)
        assert out.status == "predicted_only" and out.pose_estimate is None, (model, out.warnings)
        assert out.warnings[-2] == pe.MOON_NOT_UP and pe.DAYTIME not in out.warnings, (model, out.warnings)
    assert calls == []
    for result in (
        pe.pose_from_frames([(0.0, MOON_MOMENT, luma)], lat=lat, lon=lon, profile=PROFILE, orientation=None),
        pe.horizon_moon_pose(luma, frame_time_sec=0.0, moment=MOON_MOMENT, lat=lat, lon=lon, orientation=None),
    ):
        assert isinstance(result, Refusal) and (result.kind, result.reason) == ("moon_not_up", pe.MOON_NOT_UP)


def test_a_spot_that_gave_no_pose_says_more_than_a_frame_not_confirmed_and_the_first_such_frame_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-SKY-3.12's order across kinds (verdict sky-3b-v2 note (a)): frames that mix a spot that passed the Moon test
    but gave no pose (every fit refused here) with frames not confirmed answer with the spot's reason, whatever the
    order the frames come in; with two such spots, the first in time order is named, by its own time and place."""
    monkeypatch.setattr(pe, "fit_level_pose", lambda *args, **kwargs: Refusal("fit", "refused for the test"))
    first, later = _soft_moon(500.3, 700.6), _soft_moon(800.3, 900.6)
    for frames in (
        [(0.0, _noise()), (1.5, first), (3.0, later)],
        [(3.0, later), (1.5, first), (0.0, _noise())],
        [(1.5, first), (3.0, _noise()), (4.5, later)],
    ):
        result = pe.pose_from_frames([(t, frame_moment(MOON_MOMENT, t), luma) for t, luma in frames], lat=LAT,
                                     lon=LON, profile=PROFILE, orientation=None)
        assert isinstance(result, Refusal) and result.kind == "fit", result
        assert result.frame_time_sec == 1.5 and abs(result.blob.x_px - 500.3) < 1.0, result
        assert result.reason == pe.fit_reason(result.blob.x_px, result.blob.y_px, "in the frame 1.50 s into the clip")


def test_by_day_no_moon_pose_is_tried_and_the_reason_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """By day the likeliest bright spot is the Sun. A disk of the Moon's size, as the Sun may record, gives no pose,
    and the frames are not read; each frame's own moment is checked again."""
    lat, lon = _daytime_place()
    luma = _soft_moon(500.3, 700.6, peak=255.0)
    solver, asset = _solver_solution(lat=lat, lon=lon), _asset(lat=lat, lon=lon)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, luma)]

    out = complete_sky(solver, asset, frames=frames)
    assert out.status == "predicted_only", out.warnings
    assert pe.DAYTIME == (
        "The sky is not lined up over the picture: the Sun was above the horizon at the frame's moment, and by day a "
        "bright spot may be the Sun."
    )
    _assert_sky_2_plus(out, solver, asset, pe.DAYTIME)
    assert calls == []
    assert pe.pose_from_frames([(0.0, MOON_MOMENT, luma)], lat=lat, lon=lon, profile=PROFILE,
                               orientation=None).reason == pe.DAYTIME
    # The day limit is USNO's, the one the sky phase sentence uses: up strictly above it
    sun = _sun_alt(MOON_MOMENT, lat, lon)
    from services.science import predicted_sky

    assert pe.SUNRISE_SUNSET_ALT_DEG == predicted_sky.SUNRISE_SUNSET_ALT_DEG == -50.0 / 60.0
    monkeypatch.setattr(pe, "SUNRISE_SUNSET_ALT_DEG", sun)
    assert isinstance(_pose_at(luma, lat, lon), PoseResult)
    monkeypatch.setattr(pe, "SUNRISE_SUNSET_ALT_DEG", sun - 1e-6)
    assert _pose_at(luma, lat, lon).reason == pe.DAYTIME


def _pose_at(luma: np.ndarray, lat: float, lon: float) -> PoseResult | Refusal:
    return head_worn_moon_pose(luma, frame_time_sec=0.0, moment=MOON_MOMENT, lat=lat, lon=lon, profile=PROFILE,
                               orientation=None)


def test_the_synthetic_places_put_the_moon_where_they_say() -> None:
    """The helper the properties use: the Moon at the asked altitude, due south of a place north of the sub-lunar
    point and due north of one south of it, in both hemispheres. A low Moon near the east point (11.125 up, bearing
    219.375, which a random seed drew on 2026-11-12) leaves the search ill conditioned, and it still lands."""
    # The last: one the sixth version's helper moved 39.9 degrees from the fifth's, past the south pole (its audit)
    for alt, bearing in ((5.0, 30.0), (33.0, 200.0), (60.0, 0.0), (89.5, 90.0), (40.0, 180.0), (11.125, 219.375),
                         (13.674, 161.216)):
        lat, lon = _place_with_moon(alt, bearing)
        assert -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0, (alt, bearing)
        (moon,) = compute_solar_system_ephemerides(MOON_MOMENT, lat, lon, bodies=("Moon",))
        assert moon.alt_deg == pytest.approx(alt, abs=1e-6)
    # Where both earlier helpers gave latitude -98.839 (its audit): refused (as on this Mac) or a real place, never one
    # past the pole
    try:
        lat, lon = _place_with_moon(14.033, 188.051)
    except NoRealPlace:
        pass
    else:
        assert -90.0 <= lat <= 90.0
    north, south = _place_with_moon(40.0, 0.0), _place_with_moon(40.0, 180.0)
    assert north[0] > 0.0 > south[0]
    assert compute_solar_system_ephemerides(MOON_MOMENT, *north, bodies=("Moon",))[0].az_deg == pytest.approx(180.0, abs=1e-6)
    for bearing, side in ((179.99, "below 360"), (180.01, "above 0")):
        az = compute_solar_system_ephemerides(MOON_MOMENT, *_place_with_moon(35.0, bearing), bodies=("Moon",))[0].az_deg
        assert (az > 359.9) if side == "below 360" else (az < 0.1), (bearing, az)



def test_every_synthetic_place_is_a_real_place_over_the_region_where_it_once_was_not() -> None:
    """A grid over the Moon 6 to 20 degrees up at bearings 150 to 210 (where the sixth version's helper made 9 of 315
    places past the south pole): every place found lies in range with the Moon at the asked altitude, and the draws no
    real place satisfies are refused."""
    refused = []
    for alt in range(6, 21, 2):
        for bearing in range(150, 211, 5):
            try:
                lat, lon = _place_with_moon(float(alt), float(bearing))
            except NoRealPlace:
                refused.append((alt, bearing))
                continue
            assert -90.0 <= lat <= 90.0 and -180.0 <= lon < 180.0, (alt, bearing, lat)
            (moon,) = compute_solar_system_ephemerides(MOON_MOMENT, lat, lon, bodies=("Moon",))
            assert moon.alt_deg == pytest.approx(alt, abs=1e-6), (alt, bearing)
    # Refusals stay rare, so the property still covers its range (measured on this Mac: 5 of 104, the Moon 14 degrees up
    # at bearings 170 to 190; which draws converge near a pole can differ in the last bits on another machine)
    assert len(refused) <= 10, refused


def test_fold_brings_a_place_past_a_pole_or_the_date_line_into_range_and_leaves_one_in_range_alone() -> None:
    assert _fold(-98.839, 10.0) == pytest.approx((-81.161, -170.0))
    assert _fold(95.0, -170.0) == pytest.approx((85.0, 10.0))
    assert _fold(10.0, 200.0) == pytest.approx((10.0, -160.0))
    for place in ((-90.0, -180.0), (90.0, 180.0), (12.345678901234, -40.98765432109)):
        assert _fold(*place) == place

# --- Through the job's frames: the soft-peaked Moon ------------------------------------------------------------------

NOT_CONFIRMED = (
    "The sky is not lined up over the picture: what was seen {where} was not confirmed as the Moon. The Moon test needs "
    "a spot whose peak is at 200 of 255 or more and at least 10 times the noise above the sky, that is whole inside the "
    "frame, that measures at half its peak between 0.8 times the Moon's predicted diameter at the widest field of view "
    "and 8 times it at the narrowest, and that is the only spot in the frame passing this test."
)


def _timed(*frames: tuple[float, np.ndarray]) -> list[tuple[float, str, np.ndarray]]:
    return [(t, frame_moment(MOON_MOMENT, t), luma) for t, luma in frames]


def test_a_soft_peaked_moon_at_244_is_lined_up_from_it() -> None:
    """The first real head-worn clip's Moon peaks at 241 to 244 of 255 with no flat clipped core; a synthetic one of
    the predicted size at 244 is lined up, in a clip and in a photo."""
    luma = _soft_moon(500.3, 700.6)
    region = _rule(luma)[5]
    for kind, frame_time in (("video", 3.0), ("image", 0.0)):
        out = complete_sky(_solver_solution(), _asset(kind=kind), frames=lambda: [(frame_time, luma)])
        assert out.status == "approximate", out.warnings
        assert out.frame_time_sec == frame_time and len(out.pose_estimate.swept_wcs) == 9
        (moon,) = [f for f in out.findings if f.id == "finding-observed-moon"]
        detection = moon.source_detection
        assert math.hypot(detection.x_px - 500.3, detection.y_px - 700.6) < 1.0
        assert moon.title == "Bright Spot (Taken To Be The Moon)"
        assert moon.subtitle.endswith(f"· {int(region.sum())} px above half its peak")
        assert not any(f.evidence_class == "catalog_matched" for f in out.findings)


def test_a_peak_under_200_gives_no_pose_with_the_rule_in_plain_words() -> None:
    solver, clip, photo = _solver_solution(), _asset(), _asset(kind="image")
    dim = _soft_moon(500.0, 700.0, peak=199.0)
    out = complete_sky(solver, clip, frames=lambda: [(0.5, dim), (1.5, _noise())])
    reason = out.warnings[-2]
    assert reason == NOT_CONFIRMED.format(where="in the 2 frames searched")
    _assert_sky_2_plus(out, solver, clip, reason)
    one = complete_sky(solver, photo, frames=lambda: [(0.0, dim)])
    assert one.warnings[-2] == NOT_CONFIRMED.format(where="in the photo")
    _assert_sky_2_plus(one, solver, photo, one.warnings[-2])
    single = complete_sky(solver, clip, frames=lambda: [(0.5, dim)]).warnings[-2]
    assert single == NOT_CONFIRMED.format(where="in the frame searched")
    assert pe.pose_from_frames(_timed((0.5, dim)), lat=LAT, lon=LON, profile=PROFILE, orientation=None).kind == "not_moon"
    # The same Moon peaking at 200 is lined up
    bright_enough = _soft_moon(500.0, 700.0, peak=200.0)
    assert complete_sky(solver, clip, frames=lambda: [(0.5, bright_enough), (1.5, _noise())]).status == "approximate"


# --- P-SKY-5 -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Truth:
    """One synthetic truth: the place and the frame's moment, the topocentric Moon and its stored pixel, the field of
    view, roll and display turns, the drawn Moon's radius in px, every level pose that sees the Moon at that pixel
    (lowest centre first) and the one taken as the truth with its WCS (None when there is no level pose)."""

    lat: float
    lon: float
    moment: str
    ra: float
    dec: float
    moon_alt: float
    moon_az: float
    x: float
    y: float
    fov: float
    roll: float
    turns: int
    disk_px: float
    poses: tuple[tuple[float, float], ...]
    centre: tuple[float, float] | None
    wcs: WCSModel | None


def _draw_truth(moon_alt: float, bearing: float, u: float, v: float, fov: float, roll: float, turns: int, pick: int,
                bloom: float, blur: float, frame_time: float) -> _Truth:
    """The Moon `moon_alt` up (the topocentric record) from the place along `bearing` from the sub-lunar point, at the
    stored pixel a fraction (u, v) across the frame inside a margin that keeps the drawn Moon whole and off the
    frame's edge, and the truth the level pose number `pick` of those that see it there in the true horizon (the
    Moon at its apparent place), so the camera points anywhere a level head allows."""
    try:
        lat, lon = _place_with_moon(moon_alt, bearing)
    except NoRealPlace:
        # The draw asks for a point past a pole: no real place, so no case
        assume(False)
    moment = frame_moment(MOON_MOMENT, frame_time)
    ra, dec, radius = _topocentric(moment, lat, lon)
    alt, az = icrs_to_altaz(ra, dec, lat, lon, moment)
    disk = radius / _scale(fov) * bloom
    margin = disk + 5.0 * blur + 8.0
    x, y = margin + u * (WIDTH - 2.0 * margin), margin + v * (HEIGHT - 2.0 * margin)
    poses = tuple(_level_poses_by_search(alt, az, x, y, fov, roll, turns))
    centre = poses[min(pick, len(poses) - 1)] if poses else None
    wcs = None
    if centre is not None and abs(centre[0]) < 89.9:
        wcs = _truth_wcs(*centre, fov, roll, turns, moment, lat, lon)
        # The search and the truth's own model agree: the truth sees the Moon at the drawn pixel, with the roll
        mx, my = truth_pixel(wcs, ra, dec, lat, lon, moment)
        assert math.hypot(mx - x, my - y) < 1e-6, (mx, my, x, y)
        assert _roll_of(wcs, turns, moment, lat, lon, truth=True) == pytest.approx(roll, abs=1e-6)
    return _Truth(lat, lon, moment, ra, dec, alt, az, x, y, fov, roll, turns, disk, poses, centre, wcs)


def _lined_up_over(truth: _Truth, peak: int, blur: float, frame_time: float) -> ObservationSolution:
    luma = _soft_moon(truth.x, truth.y, truth.disk_px, peak=float(peak), blur=blur, seed=7)
    return complete_sky(
        _solver_solution(lat=truth.lat, lon=truth.lon), _asset(turns=truth.turns, lat=truth.lat, lon=truth.lon),
        frames=lambda: [(frame_time, luma)],
    )


def _at_night(truth: _Truth) -> bool:
    """The Sun below the USNO sunrise and sunset altitude at the frame's moment: by day no Moon pose is tried."""
    from services.science.predicted_sky import SUNRISE_SUNSET_ALT_DEG

    return _sun_alt(truth.moment, truth.lat, truth.lon) <= SUNRISE_SUNSET_ALT_DEG


def _assert_inside_the_bound(solution: ObservationSolution, truth: _Truth, frame_time: float) -> None:
    """The answer is approximate at the frame's own time and moment; the topocentric Moon lands on the spot within
    0.5 px under every swept WCS; every predicted object truly in the frame lies inside its ring (the largest
    distance from its nominal pixel to its nine swept pixels, plus the beacon term in px); and the true centre lies
    within maxCenterOffsetDeg of the nominal one, plus only what the centroid's own miss moves the pose by at the
    truth's field of view and roll (the nearest level pose for the measured centroid, by the independent search)."""
    assert solution.frame_time_sec == frame_time and solution.observed_at_utc == truth.moment
    pose = solution.pose_estimate
    assert pose is not None and len(pose.swept_wcs) == 9
    (spot,) = [f for f in solution.findings if f.id == "finding-observed-moon"]
    cx, cy = spot.source_detection.x_px, spot.source_detection.y_px
    # The centroid is near the drawn Moon's centre of light (each pixel's share of the disk was counted on a 5 x 5
    # grid). The smallest, faintest Moon under the widest blur keeps light outside its half-peak region grown by two
    # pixels, cut on the pixel grid: 0.106 px off at a corner (the example below). Every bound below is measured from
    # the centroid itself, and the true centre's slack is what this miss moves the pose by
    assert math.hypot(cx - truth.x, cy - truth.y) < 0.25
    for wcs in (solution.wcs, *pose.swept_wcs):
        px, py = wcs.world_to_pixel(truth.ra, truth.dec)
        assert math.hypot(px - cx, py - cy) <= 0.5
    term_px = pose.beacon_centroid_uncertainty_deg / solution.wcs.scale_deg_per_px
    for finding in solution.findings:
        if finding.evidence_class != "predicted":
            continue
        tx, ty = truth_pixel(truth.wcs, finding.ra_deg, finding.dec_deg, truth.lat, truth.lon, truth.moment)
        if not (0.0 <= tx < WIDTH and 0.0 <= ty < HEIGHT):
            continue
        nx, ny = solution.wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        swept = [w.world_to_pixel(finding.ra_deg, finding.dec_deg) for w in pose.swept_wcs]
        assert all(math.isfinite(value) for value in (nx, ny, *(c for p in swept for c in p))), finding.id
        ring = max(math.hypot(x - nx, y - ny) for x, y in swept)
        assert math.hypot(tx - nx, ty - ny) <= ring + term_px, finding.id
    # The frame term holds where it is largest, at every corner of every swept frame: the direction a corner draws is
    # seen within the term of where the pose's one frame rotation puts it
    moon = topocentric_moon(truth.moment, truth.lat, truth.lon)
    frame = pe.pose_frame(moon, truth.lat, truth.lon, truth.moment)
    term = pe.frame_term_arcsec(moon, pose.swept_wcs, [_fov_of(w) for w in pose.swept_wcs], truth.lat, truth.lon,
                                truth.moment)
    assert pose.beacon_centroid_uncertainty_deg == pytest.approx(moon.radius_deg + moon.uncertainty_deg + term / 3600.0,
                                                                 rel=1e-12)
    for wcs in pose.swept_wcs:
        for corner in ((0.0, 0.0), (WIDTH, 0.0), (0.0, HEIGHT), (WIDTH, HEIGHT)):
            ra, dec = wcs.pixel_to_world(*corner)
            drawn_alt, drawn_az = catalog.icrs_to_frame_altaz(frame, ra, dec)
            seen_alt, seen_az = icrs_to_altaz(ra, dec, truth.lat, truth.lon, truth.moment)
            assert angular_separation_deg(drawn_az, drawn_alt, seen_az, seen_alt) * 3600.0 <= term, (corner, term)
    # The true centre is where the truth camera's centre is seen, in the true horizon, and the pose states where its
    # own centre is seen there
    offset = angular_separation_deg(pose.center_az_deg, pose.center_alt_deg, truth.centre[1], truth.centre[0])
    moved = _level_poses_by_search(truth.moon_alt, truth.moon_az, cx, cy, truth.fov, truth.roll, truth.turns)
    slack = min(angular_separation_deg(truth.centre[1], truth.centre[0], az, alt) for alt, az in moved)
    assert offset <= pose.max_center_offset_deg + slack + 1e-9, (offset, pose.max_center_offset_deg, slack)
    assert solution.quality.passed_independent_verification is False
    assert not any(f.evidence_class == "catalog_matched" for f in solution.findings)


P_SKY_5_DRAWS = dict(
    moon_alt=st.floats(5.0, 90.0),
    bearing=st.floats(0.0, 360.0, exclude_max=True),
    u=st.floats(0.0, 1.0),
    v=st.floats(0.0, 1.0),
    # The sweep's corners as often as anywhere between: there the truth's centre is a swept centre and the frame term
    # is largest (the third audit drew its failures there)
    fov=st.one_of(st.sampled_from([80.0, 95.0, 110.0]), st.floats(80.0, 110.0)),
    roll=st.one_of(st.sampled_from([-10.0, 0.0, 10.0]), st.floats(-10.0, 10.0)),
    turns=st.sampled_from([0, 1, 2, 3]),
    pick=st.sampled_from([0, 1]),
    bloom=st.floats(1.0, 3.0),
    blur=st.floats(0.5, 2.5),
    peak=st.integers(200, 255),
    frame_time=st.floats(0.0, 15.0),
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
@given(**P_SKY_5_DRAWS)
# The Moon in the middle of the frame, low, at the fixtures' height, and high; the profile's limits of field of view
# and roll; the widest blur on the smallest Moon at the floor
@example(moon_alt=5.5, bearing=30.0, u=0.5, v=0.5, fov=80.0, roll=-10.0, turns=0, pick=0, bloom=1.0, blur=0.5, peak=255, frame_time=0.0)
@example(moon_alt=33.0, bearing=200.0, u=0.5, v=0.5, fov=110.0, roll=10.0, turns=1, pick=0, bloom=1.0, blur=2.5, peak=200, frame_time=7.5)
@example(moon_alt=89.5, bearing=90.0, u=0.5, v=0.5, fov=95.0, roll=0.0, turns=2, pick=0, bloom=3.0, blur=1.0, peak=244, frame_time=14.0)
# The four corners, in both hemispheres (a bearing of 0 puts the place north of the sub-lunar point, 180 south)
@example(moon_alt=20.0, bearing=0.0, u=0.0, v=0.0, fov=110.0, roll=10.0, turns=0, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
@example(moon_alt=25.0, bearing=180.0, u=1.0, v=0.0, fov=80.0, roll=-10.0, turns=3, pick=0, bloom=2.0, blur=1.0, peak=241, frame_time=3.0)
@example(moon_alt=40.0, bearing=0.0, u=0.0, v=1.0, fov=95.0, roll=5.0, turns=1, pick=0, bloom=1.0, blur=2.0, peak=230, frame_time=0.0)
@example(moon_alt=15.0, bearing=180.0, u=1.0, v=1.0, fov=110.0, roll=-10.0, turns=2, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
# The Moon due north of the place (a bearing of 180), so the Moon's and the frame centre's azimuths lie just under
# 360 and just over 0
@example(moon_alt=35.0, bearing=179.99, u=0.5, v=0.5, fov=95.0, roll=0.0, turns=0, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
@example(moon_alt=35.0, bearing=180.01, u=0.5, v=0.5, fov=95.0, roll=0.0, turns=0, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
# A high Moon low in the frame with the camera looking up toward the zenith
@example(moon_alt=70.0, bearing=300.0, u=0.4, v=0.9, fov=95.0, roll=0.0, turns=0, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
# The smallest, faintest Moon under a wide blur at a corner: its centroid 0.106 px from the drawn centre
@example(moon_alt=5.0, bearing=2.0, u=0.0, v=0.0, fov=105.0, roll=0.0, turns=0, pick=0, bloom=1.0, blur=2.34375, peak=200, frame_time=0.0)
# The auditor's case (verdict finding 1): the truth the second of two level poses, the camera 85 degrees up with its
# back to a Moon about 60 degrees up; an answer that lines the sky up here cannot hold its bound
@example(moon_alt=60.0, bearing=0.0, u=0.5, v=0.083, fov=95.0, roll=0.0, turns=0, pick=1, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
# A low Moon near the east point, where the synthetic place's search is ill conditioned (a random seed, 2026-11-12)
@example(moon_alt=11.125, bearing=219.375, u=0.0, v=0.0, fov=80.0, roll=-10.0, turns=0, pick=0, bloom=1.0, blur=1.0, peak=200, frame_time=0.0)
def test_pbt_p_sky_5_whenever_the_answer_is_approximate_the_truth_lies_inside_its_bound(
    moon_alt: float, bearing: float, u: float, v: float, fov: float, roll: float, turns: int, pick: int,
    bloom: float, blur: float, peak: int, frame_time: float,
) -> None:
    truth = _draw_truth(moon_alt, bearing, u, v, fov, roll, turns, pick, bloom, blur, frame_time)
    assume(truth.wcs is not None and _at_night(truth))
    solution = _lined_up_over(truth, peak, blur, frame_time)
    if solution.status != "approximate":
        # Refused: layer 1 alone, with its reason
        assert solution.status == "predicted_only" and solution.wcs is None and solution.pose_estimate is None
        return
    _assert_inside_the_bound(solution, truth, frame_time)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
@given(**{**P_SKY_5_DRAWS, "moon_alt": st.floats(40.0, 90.0)})
# The auditor's case (verdict finding 1): the Moon about 60 degrees up near the top of the frame at the nominal field
# of view and roll, the truth the camera 85 degrees up with its back to the Moon; and the same frame with the truth
# the other pose, facing the Moon
@example(moon_alt=60.0, bearing=0.0, u=0.5, v=0.083, fov=95.0, roll=0.0, turns=0, pick=1, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
@example(moon_alt=60.0, bearing=0.0, u=0.5, v=0.083, fov=95.0, roll=0.0, turns=0, pick=0, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
@example(moon_alt=75.0, bearing=120.0, u=0.95, v=0.5, fov=80.0, roll=10.0, turns=1, pick=1, bloom=1.0, blur=2.0, peak=244, frame_time=0.0)
def test_pbt_p_sky_5_whenever_two_level_poses_exist_the_answer_is_never_approximate(
    moon_alt: float, bearing: float, u: float, v: float, fov: float, roll: float, turns: int, pick: int,
    bloom: float, blur: float, peak: int, frame_time: float,
) -> None:
    truth = _draw_truth(moon_alt, bearing, u, v, fov, roll, turns, pick, bloom, blur, frame_time)
    assume(len(truth.poses) == 2 and truth.wcs is not None and _at_night(truth))
    solution = _lined_up_over(truth, peak, blur, frame_time)
    assert solution.status == "predicted_only", solution.warnings
    assert solution.wcs is None and solution.pose_estimate is None
    # Refused because a swept value has two poses, or none (two can meet and vanish at a wider field of view)
    reason = solution.warnings[-2]
    assert ("fits two different directions of view" in reason
            or "at one or more of the fields of view and head rolls the camera is stated to have" in reason), reason


# --- P-SKY-6 -------------------------------------------------------------------------------------------------------


def _assert_not_confirmed(luma: np.ndarray, detail: str) -> None:
    """No pose, with the rule in plain words; the Moon test itself refuses for `detail`."""
    refusal = find_moon_blob(luma, **_limits())
    assert isinstance(refusal, Refusal) and detail in refusal.reason, refusal
    solver = _solver_solution()
    out = complete_sky(solver, _asset(), frames=lambda: [(0.0, luma)])
    assert out.status == "predicted_only" and out.wcs is None and out.pose_estimate is None
    assert not any(f.id == "finding-observed-moon" for f in out.findings)
    assert out.warnings[-2] == NOT_CONFIRMED.format(where="in the frame searched")
    assert out.warnings[-1] == sky_phase(compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Sun",))[0].alt_deg).sentence


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(radius=st.floats(4.0, 20.0), ratio=st.floats(0.3, 1.0), blur=st.floats(0.5, 2.5), peak=st.integers(200, 255),
       second=st.integers(200, 255))
@example(radius=4.0, ratio=0.75, blur=2.5, peak=200, second=200)
@example(radius=20.0, ratio=1.0, blur=0.5, peak=255, second=255)
# At the first real head-worn clip's peak, two Moons of about the predicted size still refuse
@example(radius=5.0, ratio=0.9, blur=2.0, peak=244, second=240)
# Areas far apart (the auditor's 0.39) and a dimmer second one: each still passes on its own
@example(radius=12.0, ratio=0.62, blur=1.5, peak=255, second=235)
def test_pbt_p_sky_6_two_moons_that_each_pass_the_moon_test_give_no_pose(
    radius: float, ratio: float, blur: float, peak: int, second: int,
) -> None:
    first = _soft_moon(400.0, 500.0, radius, peak=float(peak), blur=blur, seed=2)
    luma = _soft_moon(900.0, 1300.0, radius * ratio, peak=float(second), blur=blur, luma=first)
    # Written out independently: each spot passes the Moon test on its own, whatever their areas beside each other
    assume(_own_test(luma, 400, 500) and _own_test(luma, 900, 1300))
    _assert_not_confirmed(luma, "more than one spot passes the Moon test on its own")


@settings(max_examples=15, deadline=None)
@given(lights=st.lists(st.tuples(st.floats(0.5, 2.0), st.integers(200, 255)), min_size=1, max_size=6))
# The point light of the Moon test's own test, at the first real head-worn clip's peak, and at the floor
@example(lights=[(2.0, 244)])
@example(lights=[(2.0, 200)])
@example(lights=[(0.5, 255), (2.0, 255)])
def test_pbt_p_sky_6_only_point_lights_smaller_than_the_moon_test_allows_give_no_pose(lights: list[tuple[float, int]]) -> None:
    luma = _noise(4)
    for i, (blur, peak) in enumerate(lights):
        luma = _point_light(luma, 100 + 200 * i, 200 + 250 * i, peak=float(peak), blur=blur)
    region = _rule(luma)[5]
    assert 2.0 * math.sqrt(region.sum() / math.pi) < _limits()["min_diameter_px"]
    _assert_not_confirmed(luma, "px across at half its peak, under the")


# --- The approximate solution ----------------------------------------------------------------------------------------


def test_the_approximate_solution_carries_the_pose_the_moon_and_the_predicted_sky() -> None:
    frame_time = 3.0
    moment = frame_moment(MOON_MOMENT, frame_time)
    luma = _frame(500.0, 700.0, 8.0)
    blob = _pose(luma, frame_time=frame_time).blob
    bx, by, r = blob.x_px, blob.y_px, blob.radius_px
    detections = [
        # On the Moon in the same frame: left out, inside the blob's radius
        SourceDetection(bx + (r - 0.05), by, 900.0, 40.0, 3.0, 0.1, asset_id=ASSET_ID, frame_time_sec=frame_time),
        # Just outside it in the same frame, and on the Moon's pixel in another frame: both kept
        SourceDetection(bx, by + (r + 0.05), 50.0, 8.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=frame_time),
        SourceDetection(bx, by, 60.0, 9.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0),
        SourceDetection(1200.0, 100.0, 70.0, 10.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0),
    ]
    solver = _solver_solution(detections, "The plate solve found too few stars.")
    before = [f.to_contract_dict(source_asset_id=ASSET_ID) for f in solver.findings]
    out = complete_sky(solver, _asset(), frames=lambda: [(frame_time, luma)])
    assert [f.to_contract_dict(source_asset_id=ASSET_ID) for f in solver.findings] == before
    assert out.status == "approximate" and out.frame_time_sec == frame_time and out.observed_at_utc == moment
    assert (out.observer_lat, out.observer_lon) == (LAT, LON)
    assert out.wcs is not None and out.wcs in out.pose_estimate.swept_wcs
    q = out.quality
    # Nothing was matched to a catalog: the bright spot was only taken to be the Moon
    assert (q.detected_candidate_count, q.matched_inlier_count, q.held_out_pair_count, q.held_out_inliers) == (4, 0, 0, 0)
    assert (q.median_residual_px, q.p95_residual_px, q.median_residual_arcsec, q.passed_independent_verification) == (None, None, None, False)
    observed = [f for f in out.findings if f.evidence_class == "observed"]
    kept = [f.source_detection for f in observed if f.id != "finding-observed-moon"]
    assert kept == detections[1:]
    assert all(not f.has_sky_position for f in observed)
    (moon,) = [f for f in observed if f.id == "finding-observed-moon"]
    d = moon.source_detection
    assert (d.x_px, d.y_px, d.flux, d.snr, d.fwhm_px) == (blob.x_px, blob.y_px, blob.flux, blob.snr, blob.fwhm_px)
    assert d.frame_time_sec == frame_time and d.asset_id == ASSET_ID
    assert any("other frames" in text for text in moon.limitations)
    # Taken to be the Moon, never shown to be; no held-out check is claimed, and none is marked passed
    assert moon.title == "Bright Spot (Taken To Be The Moon)"
    assert moon.limitations[0] == (
        "Taken to be the Moon, not shown to be: nothing in one frame proves the spot is the Moon, and a lamp or another "
        "light of similar size, as bright and the only spot in its frame that passes the Moon test, would be taken the "
        "same way."
    )
    assert moon.verified_by_holdout is False
    assert moon.verification_method == (
        "Measured in the frame's pixels: the frame's brightest spot passed the Moon test and was taken to be the Moon; "
        "nothing was matched to a catalog and no held-out check was made"
    )
    evidence = moon.to_contract_dict(source_asset_id=ASSET_ID)["evidence"]
    assert evidence["verifiedByIndependentHoldout"] is False and evidence["verificationMethod"] == moon.verification_method
    assert not any(f.evidence_class == "catalog_matched" for f in out.findings)
    predicted = [f for f in out.findings if f.evidence_class == "predicted"]
    expected = predicted_sky_findings(moment, LAT, LON, solution_id=solver.id, source_sha256=SHA)
    assert [f.id for f in predicted] == [f.id for f in expected]
    inside = 0
    for finding, plain in zip(predicted, expected):
        assert (finding.ra_deg, finding.dec_deg, finding.alt_deg, finding.az_deg) == (plain.ra_deg, plain.dec_deg, plain.alt_deg, plain.az_deg)
        x, y = out.wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        in_frame = math.isfinite(x) and math.isfinite(y) and 0.0 <= x < WIDTH and 0.0 <= y < HEIGHT
        assert finding.subtitle == plain.subtitle + (" · Inside the frame (lined up, approximate)" if in_frame else " · Outside the frame")
        assert finding.source_detection is None
        if in_frame:
            inside += 1
            assert any("approximate pose" in text for text in finding.limitations)
    assert inside >= 1 and any(f.id == "finding-predicted-moon" for f in predicted)
    # The plate solver's warning, then how it was lined up, then where the Sun was
    assert out.warnings[0] == "The plate solve found too few stars."
    lined_up = out.warnings[1]
    pose = out.pose_estimate
    within = math.ceil(pose.max_center_offset_deg + pose.beacon_centroid_uncertainty_deg)
    assert lined_up == (
        f"Lined up by taking the bright spot at ({blob.x_px:.0f}, {blob.y_px:.0f}) px in the frame 3.00 s into the clip "
        "to be the Moon, with the camera profile's field of view of 80 to 110 degrees across the diagonal with no zoom "
        f"and a level head within 10 degrees: within about {within} degrees near the bright spot, more toward the "
        "edges; not checked against stars."
    )
    (sun,) = compute_solar_system_ephemerides(moment, LAT, LON, bodies=("Sun",))
    assert out.warnings[2] == sky_phase(sun.alt_deg).sentence and len(out.warnings) == 3
    for text in out.warnings:
        _capture_free(text)
    assert out.solver is not None and out.solver.name == "AlbedianHeadWornMoonPose"
    ObservationSolution(**{**out.__dict__})


def test_a_photo_is_lined_up_from_its_one_frame_and_says_so() -> None:
    out = complete_sky(_solver_solution(), _asset(kind="image"), frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
    assert out.status == "approximate" and out.frame_time_sec == 0.0 and out.observed_at_utc == MOON_MOMENT
    assert "in the photo" in out.warnings[1] and "into the clip" not in out.warnings[1]
    (moon,) = [f for f in out.findings if f.id == "finding-observed-moon"]
    assert not any("other frames" in text for text in moon.limitations)


# Sentences that state as fact what the pose only assumes (verdict finding 3)
SAYS_IT_IS_THE_MOON = (
    "was confirmed as the Moon", "Lined up from the Moon", "Moon (Observed", "The spot is the Moon",
    "matched to the Moon's predicted position", "about the Moon's size", "the Moon at (", "Moon pixel",
)


def test_no_text_says_the_bright_spot_is_the_moon() -> None:
    """Nothing in one frame proves the spot is the Moon: every title, warning, limitation, assumption and
    verification text that names the spot as the Moon says it was taken to be the Moon. The solver kept a detection
    from another frame of the clip, so the spot's limitations end with the sentence that says why it is kept
    (verdict sky-3b-v3 note (c): that sentence once called the spot "this frame's Moon pixel")."""
    other = SourceDetection(1200.0, 100.0, 70.0, 10.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0)
    out = complete_sky(_solver_solution([other]), _asset(), frames=lambda: [(2.0, _soft_moon(500.3, 700.6))])
    assert out.status == "approximate", out.warnings
    (spot,) = [f for f in out.findings if f.id == "finding-observed-moon"]
    assert spot.limitations[-1] == OTHER_FRAMES_KEPT
    assert "this frame's bright spot, taken to be the Moon," in OTHER_FRAMES_KEPT
    # The subtitle and the second limitation, which name the spot without taking it for the Moon, word for word (rows
    # 3.11-8 and 3.11-9 of sky-3b-v5's table: other words would pass the list of phrases below)
    assert re.fullmatch(r"Measured at \(\d+\.\d, \d+\.\d\) px in the frame 2\.00 s into the clip · \d+ px above "
                        r"half its peak", spot.subtitle), spot.subtitle
    assert spot.limitations[1] == (
        "Without a verified WCS the detection has no sky position; the predicted Moon carries the ephemeris position."
    )
    texts = [*out.warnings, *out.pose_estimate.assumptions, out.pose_estimate.fov_source]
    for finding in out.findings:
        texts += [finding.title, finding.subtitle, *finding.limitations, finding.verification_method or ""]
    for text in texts:
        for phrase in SAYS_IT_IS_THE_MOON:
            assert phrase not in text, (phrase, text)
    assert "taking the bright spot at" in out.warnings[-2] and "to be the Moon" in out.warnings[-2]
    (inside,) = {text for f in out.findings for text in f.limitations if "approximate pose" in text}
    assert "taking the frame's bright spot to be the Moon" in inside


def test_a_frame_whose_moment_is_past_2050_is_not_searched() -> None:
    """A clip recorded in the ephemeris' last seconds: a frame past the end of 2050 has no predicted sky, so it is
    not searched; the frame before it is."""
    start = "2050-12-31T23:59:50Z"
    lat, lon = next(
        p for p in (_place_with_moon(40.0, float(b), start) for b in range(0, 360, 10)) if _sun_alt(start, *p) < -18.0
    )
    solver = _solver_solution(moment=start, lat=lat, lon=lon)
    asset = _asset(start, lat=lat, lon=lon)
    out = complete_sky(solver, asset, frames=lambda: [(5.0, _noise()), (15.0, _soft_moon(500.3, 700.6))])
    assert out.status == "predicted_only"
    assert out.warnings[-2] == NOT_CONFIRMED.format(where="in the frame searched")


def test_under_a_pose_only_the_detectors_observed_findings_are_kept() -> None:
    """A plate solver's outcome holds pixel-only observed findings: the contract refuses a detection on any other
    class. A finding of another class carrying a detection, built here past those checks, is never kept."""
    detection = SourceDetection(1200.0, 100.0, 70.0, 10.0, 2.0, 0.2, asset_id=ASSET_ID, frame_time_sec=0.0)
    solver = _solver_solution([detection])
    stray = copy.copy(predicted_sky_findings(MOON_MOMENT, LAT, LON, solution_id=solver.id, source_sha256=SHA)[0])
    object.__setattr__(stray, "source_detection", detection)
    solver.findings.append(stray)
    out = complete_sky(solver, _asset(), frames=lambda: [(0.0, _soft_moon(500.3, 700.6))])
    assert out.status == "approximate", out.warnings
    assert not any(f is stray for f in out.findings)
    assert [f.source_detection for f in out.findings if f.evidence_class != "observed"] == [None] * sum(
        1 for f in out.findings if f.evidence_class != "observed"
    )


# --- No pose -------------------------------------------------------------------------------------------------------


def _assert_sky_2_plus(out: ObservationSolution, solver: ObservationSolution, asset: dict, reason: str) -> None:
    """Exactly sky-2's predicted_only result (complete_sky given no frames, layer 1 alone), with the reason before
    the sky phase sentence."""
    sky_2 = complete_sky(solver, asset)
    assert sky_2.status == "predicted_only" and len(sky_2.warnings) == len(solver.warnings) + 1
    assert out.status == "predicted_only" and out.wcs is None and out.pose_estimate is None
    assert out.findings == sky_2.findings and out.quality == sky_2.quality and out.solver == sky_2.solver
    assert out.observed_at_utc == sky_2.observed_at_utc and out.frame_time_sec == sky_2.frame_time_sec
    assert out.warnings == [*sky_2.warnings[:-1], reason, sky_2.warnings[-1]]
    _capture_free(reason)


def test_without_a_head_worn_profile_there_is_no_pose_and_the_reason_is_given() -> None:
    """No head-worn pose without a head-worn profile. Since sky-4 the Moon plus horizon pose is tried on the same
    frames instead (REQ-SKY-4.1), so the reason given is that pose's: here a frame with no skyline. The head-worn
    profile's own refusal is not said, since it is not why the sky was not lined up."""
    solver = _solver_solution()
    for model in (None, "iPhone 17 Pro"):
        asset = _asset(model=model)
        out = complete_sky(solver, asset, frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
        assert out.status == "predicted_only" and out.pose_estimate is None
        assert out.warnings == [*solver.warnings, pe.NO_SKYLINE, out.warnings[-1]]
        assert pe.NO_PROFILE not in out.warnings
        assert pe.NO_PROFILE == (
            "The sky is not lined up over the picture: the camera has no head-worn camera profile, so its field of "
            "view is not known."
        )


def test_with_the_moon_down_there_is_no_pose_and_no_frame_is_read() -> None:
    (moon,) = compute_solar_system_ephemerides(MOON_DOWN_MOMENT, LAT, LON, bodies=("Moon",))
    assert moon.alt_deg <= 5.0
    solver = replace(_solver_solution(), observed_at_utc=MOON_DOWN_MOMENT)
    asset = _asset(MOON_DOWN_MOMENT)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(solver, asset, frames=frames)
    _assert_sky_2_plus(out, solver, asset, pe.MOON_NOT_UP)
    assert "5 degrees" in pe.MOON_NOT_UP and calls == []
    # Checked again at each frame's own moment
    assert pe.pose_from_frames(
        [(0.0, MOON_DOWN_MOMENT, _frame(500.0, 700.0, 8.0))], lat=LAT, lon=LON, profile=PROFILE, orientation=None
    ).reason == pe.MOON_NOT_UP


def test_a_moon_only_5_degrees_up_is_not_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pe.MIN_MOON_ALT_DEG == 5.0
    (moon,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",))
    monkeypatch.setattr(pe, "MIN_MOON_ALT_DEG", moon.alt_deg)
    assert isinstance(_pose(_frame(500.0, 700.0, 8.0)), Refusal)
    monkeypatch.setattr(pe, "MIN_MOON_ALT_DEG", moon.alt_deg - 1e-6)
    assert isinstance(_pose(_frame(500.0, 700.0, 8.0)), PoseResult)


def test_a_spot_not_confirmed_as_the_moon_gives_no_pose_with_the_reason() -> None:
    solver = _solver_solution()
    asset = _asset()
    luma = _rects((300, 400, 12, 12), (800, 1200, 12, 12))
    out = complete_sky(solver, asset, frames=lambda: [(0.5, luma), (1.5, luma)])
    reason = out.warnings[-2]
    assert reason == NOT_CONFIRMED.format(where="in the 2 frames searched")
    _assert_sky_2_plus(out, solver, asset, reason)


def test_a_refused_fit_gives_no_pose_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "MAX_FIT_RESIDUAL_PX", -1.0)
    solver = _solver_solution()
    asset = _asset()
    out = complete_sky(solver, asset, frames=lambda: [(0.0, _frame(500.0, 700.0, 8.0))])
    reason = out.warnings[-2]
    assert reason == (
        "The sky is not lined up over the picture: the fit could not put the Moon on the bright spot with a level head. "
        "The spot is at (500, 700) px in the frame 0.00 s into the clip; the fit allows -1 px."
    )
    _assert_sky_2_plus(out, solver, asset, reason)


def test_a_mirrored_frame_or_no_frame_read_gives_no_pose() -> None:
    solver = _solver_solution()
    mirrored = _asset(mirrored=True)
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(solver, mirrored, frames=frames)
    _assert_sky_2_plus(out, solver, mirrored, pe.MIRRORED)
    # Refused before any frame is read, and refused again by the frame search itself
    assert calls == []
    assert pe.pose_from_frames(
        [(0.0, MOON_MOMENT, _frame(500.0, 700.0, 8.0))], lat=LAT, lon=LON, profile=PROFILE,
        orientation={"quarterTurnsClockwise": 0, "mirrored": True},
    ).reason == pe.MIRRORED
    asset = _asset()
    _assert_sky_2_plus(complete_sky(solver, asset, frames=lambda: []), solver, asset, pe.NO_FRAMES)


def test_given_no_frames_complete_sky_is_layer_1_alone() -> None:
    solver = _solver_solution()
    out = complete_sky(solver, _asset())
    assert out.status == "predicted_only" and out.pose_estimate is None
    assert out.warnings == [*solver.warnings, out.warnings[-1]] and "USNO" in out.warnings[-1]


def test_a_calibrated_solution_never_takes_the_pose_path() -> None:
    from services.science.solver import detect_point_sources, render_synthetic_starfield, solve_and_verify_field

    frame, _, _ = render_synthetic_starfield(width=960, height=540, read_noise_sigma=1.8, flux_scale=1100.0, seed=42)
    calibrated = solve_and_verify_field(
        detect_point_sources(frame), 960, 540, obs_utc_iso=MOON_MOMENT, observer_lat=LAT,
        observer_lon=LON, source_asset_id=ASSET_ID, source_sha256=SHA, solution_id="sol-calibrated",
    )
    assert calibrated.status == "calibrated"
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return [(0.0, _frame(500.0, 700.0, 8.0))]

    out = complete_sky(calibrated, _asset(), frames=frames)
    assert out.status == "calibrated" and out.wcs == calibrated.wcs and out.quality == calibrated.quality
    assert out.pose_estimate is None and calls == []
    assert out.findings[: len(calibrated.findings)] == calibrated.findings
    assert pe.NO_PROFILE not in out.warnings


def test_without_a_time_or_place_no_frame_is_read() -> None:
    calls: list[int] = []

    def frames() -> list:
        calls.append(1)
        return []

    solver = replace(_solver_solution(), observed_at_utc=None, status="unresolved")
    asset = _asset()
    del asset["capturedAtUTC"]
    out = complete_sky(solver, asset, frames=frames)
    assert out.status == "unresolved" and calls == []
