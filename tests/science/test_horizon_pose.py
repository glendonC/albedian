"""The Moon plus horizon pose for uploads and laptop clips (spec predicted-sky-for-every-upload, REQ-SKY-4.1, 4.2,
4.4; design component 5).

Covers, with synthetic frames at the synthetic Moon moment and open-ocean place the fixtures use, and places computed
at run time from the point under the Moon (never written):
- The skyline: the row in the frame's middle band where each row's 85th percentile, smoothed by 12 rows, rises most,
  found within a row of a drawn step, with its half width at half the rise's peak; refused when the step is under
  10 times the sky's noise or no row in the band rises
- The pose: the bright spot above the skyline taken to be the Moon by sky-3's Moon test on the sky above the skyline
  (so a light on the ground below it is no rival, and a spot cut by the skyline is refused), the skyline taken to be
  level ground; nine fits at the skyline's true altitude 0, 1.5 and 3 degrees and its row less, at and plus its half
  width; the nominal at 1.5 degrees and the row found; the beacon term the Moon's radius plus the record's stated
  uncertainty plus the frame term over the sweep's frames; refused by day, with the Moon not up, on a mirrored frame, with no
  skyline, when a fit misses, when the field of view the fit gives lies outside 20 to 130 degrees, and when the spot's
  size is not 0.8 to 8 times the Moon's at the fitted field of view
- A property over synthetic truths (the Moon from about 8 to 60 degrees up from places in both hemispheres, a level
  camera of 40 to 100 degrees across the diagonal, the skyline 0 to 3 degrees above the true horizon in the frame's
  middle band, the Moon anywhere above it, any display turn): whenever the answer is approximate, the Moon lands on
  the spot within half a pixel under every swept WCS, every predicted object truly in the frame lies inside its ring,
  and the true centre lies within maxCenterOffsetDeg plus the beacon term
- A frame stored turned is lined up as it is shown
- The words: the spot taken to be the Moon, the skyline taken to be level ground, nothing matched to a catalog, no
  held-out check claimed, no capture value in any text
- complete_sky tries the horizon pose when the head-worn pose found none, and the old entry point
  `solve_horizon_and_beacon_pose` (used by discovery and the science router) returns the same pose estimate fields,
  its predicted findings the predicted sky's own (a characterization test of ids and positions)
"""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError, dataclass, replace
import hashlib
import math
from typing import Any

from hypothesis import HealthCheck, assume, event, example, given, settings, strategies as st
import numpy as np
import pytest
from scipy import ndimage, optimize

from services.science import pose_estimate as pe
from services.science import catalog
from services.science.catalog import PlaceOutOfRange, angular_separation_deg
from services.science.ephemeris import compute_solar_system_ephemerides
from services.science.horizon_solver import solve_horizon_and_beacon_pose
from services.science.pose_estimate import MoonBlob, PoseResult, Refusal
from services.science.predicted_sky import complete_sky, frame_moment, horizon_pose_solution, predicted_sky_findings
from services.science.wcs_models import ObservationSolution, SolutionQuality, SolverIdentity, WCSModel

# The fixtures' synthetic Moon moment and open-ocean place (the Moon about 33 degrees up there)
MOON_MOMENT, LAT, LON = "2031-08-30T03:01:00Z", 30.0, -40.0
LEAKS = (MOON_MOMENT, MOON_MOMENT[:10], MOON_MOMENT[11:16], "30.0", "30.00", "-40.0", "40.0", "-40.00")
WIDTH, HEIGHT = 960, 720
SHA = hashlib.sha256(b"synthetic-horizon-frame").hexdigest()
TAKEN = "taken to be the Moon"
# Sentences that would state the spot is the Moon (sky-3's own list, test_pose_estimate.py), and the old horizon
# solver's: none may appear
SAYS_IT_IS_THE_MOON = (
    "was confirmed as the Moon", "Lined up from the Moon", "Moon (Observed", "The spot is the Moon",
    "matched to the Moon's predicted position", "about the Moon's size", "the Moon at (", "Pose from the Moon",
    "Ephemeris Lock",
)


# --- A level camera, written apart from the pose's own code -----------------------------------------------------------


@dataclass(frozen=True)
class _Camera:
    """A level rectilinear camera in the true horizon (east, north, up) that sees each direction at its apparent place
    (catalog.icrs_to_altaz at lat, lon and moment): its centre at (alt, az), `scale` radians per pixel of the tangent
    plane, an upright frame of up_w x up_h pixels whose centre is the index centre ((up_w - 1) / 2, (up_h - 1) / 2)."""

    alt: float
    az: float
    scale: float
    up_w: int
    up_h: int
    lat: float
    lon: float
    moment: str

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        a, z = math.radians(self.alt), math.radians(self.az)
        forward = np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])
        right = np.array([math.cos(z), -math.sin(z), 0.0])
        return forward, right, np.cross(right, forward)

    def upright_pixel(self, ra_deg: float, dec_deg: float) -> tuple[float, float] | None:
        forward, right, up = self.basis()
        alt, az = catalog.icrs_to_altaz(ra_deg, dec_deg, self.lat, self.lon, self.moment)
        a, z = math.radians(alt), math.radians(az)
        e = np.array([math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a)])
        depth = float(e @ forward)
        if depth <= 1e-6:
            return None
        cu, cv = (self.up_w - 1) / 2.0, (self.up_h - 1) / 2.0
        return cu + float(e @ right) / depth / self.scale, cv - float(e @ up) / depth / self.scale

    def altitudes(self) -> np.ndarray:
        """The altitude in degrees of every upright pixel's direction."""
        forward, right, up = self.basis()
        rows, cols = np.mgrid[0 : self.up_h, 0 : self.up_w].astype(np.float64)
        x = (cols - (self.up_w - 1) / 2.0) * self.scale
        y = -(rows - (self.up_h - 1) / 2.0) * self.scale
        d = forward[None, None, :] + x[..., None] * right + y[..., None] * up
        return np.degrees(np.arcsin(d[..., 2] / np.linalg.norm(d, axis=-1)))


def _scale_rad(fov_deg: float, width: int = WIDTH, height: int = HEIGHT) -> float:
    return 2.0 * math.tan(math.radians(fov_deg) / 2.0) / math.hypot(width, height)


def _to_stored(u: float, v: float, turns: int, up_w: int, up_h: int) -> tuple[float, float]:
    """An upright index position back in the stored frame that np.rot90(stored, -turns) turned upright."""
    for _ in range(turns % 4):
        # One clockwise turn took stored (x, y) of height h to upright ((h - 1) - y, x)
        u, v, up_w, up_h = v, (up_w - 1) - u, up_h, up_w
    return u, v


def _moon(moment: str = MOON_MOMENT, lat: float = LAT, lon: float = LON):
    (moon,) = compute_solar_system_ephemerides(moment, lat, lon, bodies=("Moon",))
    assert moon.frame == "topocentric"
    return moon


def _moon_level(moment: str, lat: float, lon: float) -> tuple[float, float, float]:
    """The Moon's apparent (alt, az), where the camera sees its record's direction, and its angular radius."""
    moon = _moon(moment, lat, lon)
    alt, az = catalog.icrs_to_altaz(moon.ra_deg, moon.dec_deg, lat, lon, moment)
    return alt, az, math.degrees(math.asin(1737.4 / (moon.distance_au * 149597870.7)))


def _camera_for(moment: str, lat: float, lon: float, *, fov: float, skyline_alt: float, row_fraction: float,
                moon_column: float, up_w: int, up_h: int) -> _Camera | None:
    """The level camera of this field of view whose centre column sees the skyline (altitude `skyline_alt`) at
    `row_fraction` of the upright height and the Moon at `moon_column` of the upright width; None when no azimuth
    puts it there."""
    s = _scale_rad(fov, up_w, up_h)
    cv = (up_h - 1) / 2.0
    y = -(row_fraction * (up_h - 1) - cv) * s
    alt = skyline_alt - math.degrees(math.atan(y))
    moon_alt, moon_az, _ = _moon_level(moment, lat, lon)
    moon = _moon(moment, lat, lon)
    target = moon_column * (up_w - 1)

    def miss(dz: float) -> float:
        pixel = _Camera(alt, (moon_az - dz) % 360.0, s, up_w, up_h, lat, lon, moment).upright_pixel(moon.ra_deg,
                                                                                                  moon.dec_deg)
        return (pixel[0] if pixel else -1e9) - target

    try:
        dz = optimize.brentq(miss, -85.0, 85.0, xtol=1e-12)
    except ValueError:
        return None
    return _Camera(alt, (moon_az - dz) % 360.0, s, up_w, up_h, lat, lon, moment)


def _draw(camera: _Camera, moment: str, lat: float, lon: float, *, skyline_alt: float, turns: int = 0,
          bloom: float = 1.0, blur: float = 1.5, peak: float = 244.0, ground: float = 60.0, seed: int = 3,
          spots: tuple[tuple[float, float, float, float], ...] = (), moon: bool = True) -> np.ndarray:
    """The stored frame: a dark sky (code 2, read noise of 1), the ground below the skyline at `ground`, the Moon a
    soft disk `bloom` times its size where the camera sees it, and extra soft `spots` (upright x, y, radius, peak)
    [simulated]. The upright picture is turned back to the stored frame by `turns` quarter turns."""
    rng = np.random.default_rng(seed)
    upright = np.clip(2.0 + rng.normal(0.0, 1.0, (camera.up_h, camera.up_w)), 0.0, None)
    below = camera.altitudes() < skyline_alt
    upright[below] = np.clip(ground + rng.normal(0.0, 1.0, int(below.sum())), 0.0, None)
    disks = list(spots)
    if moon:
        m = _moon(moment, lat, lon)
        x, y = camera.upright_pixel(m.ra_deg, m.dec_deg)
        radius = _moon_level(moment, lat, lon)[2] / math.degrees(camera.scale) * bloom
        disks.append((x, y, radius, peak))
    yy, xx = np.mgrid[0 : camera.up_h, 0 : camera.up_w]
    for x, y, radius, value in disks:
        disk = ((xx - x) ** 2 + (yy - y) ** 2 <= radius * radius).astype(np.float64)
        soft = ndimage.gaussian_filter(disk, blur)
        # The light added onto the sky, so the sky keeps its own noise everywhere
        upright = upright + (value - 2.0) * soft / max(float(soft.max()), 1e-12)
    return np.ascontiguousarray(np.rot90(np.clip(upright, 0.0, 255.0), k=turns))


def _orientation(turns: int, mirrored: bool = False) -> dict[str, Any]:
    return {"quarterTurnsClockwise": turns, "mirrored": mirrored}


def _pose(luma: np.ndarray, *, moment: str = MOON_MOMENT, lat: float = LAT, lon: float = LON, turns: int = 0,
          frame_time: float = 0.0) -> PoseResult | Refusal:
    return pe.horizon_moon_pose(luma, frame_time_sec=frame_time, moment=moment, lat=lat, lon=lon,
                                orientation=_orientation(turns))


EXAMPLE_FOV, EXAMPLE_ROW, EXAMPLE_COLUMN = 100.0, 0.68, 0.4


def _example(**overrides: Any) -> tuple[_Camera, np.ndarray]:
    """One scene at the fixtures' moment and place, the Moon about 33 degrees up: a camera 100 degrees across the
    diagonal, the skyline 1 degree up at 0.68 of the height, the Moon at 0.4 of the width (about row 200)."""
    skyline_alt = overrides.pop("skyline_alt", 1.0)
    turns = overrides.pop("turns", 0)
    up_w, up_h = WIDTH, HEIGHT
    camera = _camera_for(MOON_MOMENT, LAT, LON, fov=overrides.pop("fov", EXAMPLE_FOV), skyline_alt=skyline_alt,
                         row_fraction=overrides.pop("row_fraction", EXAMPLE_ROW),
                         moon_column=overrides.pop("moon_column", EXAMPLE_COLUMN), up_w=up_w, up_h=up_h)
    assert camera is not None
    return camera, _draw(camera, MOON_MOMENT, LAT, LON, skyline_alt=skyline_alt, turns=turns, **overrides)


# --- The skyline ------------------------------------------------------------------------------------------------------


def _step(row: int, *, sky: float = 2.0, ground: float = 60.0, height: int = HEIGHT, width: int = WIDTH,
          noise: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(5)
    luma = np.full((height, width), sky)
    luma[row:, :] = ground
    return np.clip(luma + rng.normal(0.0, noise, luma.shape), 0.0, None)


def test_the_skyline_is_the_row_where_the_frame_turns_brighter_below_with_its_half_width() -> None:
    skyline = pe.find_skyline(_step(400))
    assert isinstance(skyline, pe.Skyline)
    assert abs(skyline.row - 399.5) <= 1.0, skyline
    # The rise is smoothed by a Gaussian of 12 rows, so its half width at half its peak is about 1.18 times that
    assert skyline.half_width_px == pytest.approx(1.1774 * pe.SKYLINE_SMOOTHING_ROWS, abs=1.5)
    assert skyline.step_snr >= pe.SKYLINE_MIN_STEP_SNR
    assert (pe.SKYLINE_ROW_PERCENTILE, pe.SKYLINE_SMOOTHING_ROWS, pe.SKYLINE_BAND) == (85.0, 12.0, (0.38, 0.72))


def test_a_step_under_ten_times_the_noise_or_outside_the_band_is_no_skyline() -> None:
    assert pe.SKYLINE_MIN_STEP_SNR == 10.0
    # The same step at 12 and at 8 noise units (a noise of 1 code, the step's rise measured 24 rows either side)
    assert isinstance(pe.find_skyline(_step(400, ground=2.0 + 12.0)), pe.Skyline)
    assert pe.find_skyline(_step(400, ground=2.0 + 8.0)) is None
    # Above the band (0.38 of the height) or below it (0.72): no row inside it rises
    assert pe.find_skyline(_step(200)) is None
    assert pe.find_skyline(_step(600)) is None
    # A frame darker below (the sky brighter than the ground) is no skyline here (sky-6 takes either sign)
    assert pe.find_skyline(_step(400, sky=60.0, ground=2.0)) is None
    # A noiseless sky on one code reads a median absolute deviation of 0: the noise is still taken as one code, so a
    # step of 8 codes is no skyline and one of 12 is
    assert pe.SKYLINE_NOISE_FLOOR == 1.0
    assert pe.find_skyline(_step(400, ground=2.0 + 8.0, noise=0.0)) is None
    assert isinstance(pe.find_skyline(_step(400, ground=2.0 + 12.0, noise=0.0)), pe.Skyline)


def _documented_rise(luma: np.ndarray) -> tuple[int, float]:
    """The skyline's whole row and its rise in noise units as find_skyline's docstring defines them, written apart
    from it: each row's 85th percentile smoothed by a Gaussian of 12 rows, its steepest rise from 0.38 of the height
    to before 0.72, the smoothed profile's rise from 24 rows above that row to 24 below (held inside the frame), over
    1.4826 times the median absolute deviation of the rows above (at least the first row), and at least one code."""
    height = luma.shape[0]
    smooth = ndimage.gaussian_filter1d(np.percentile(luma, 85.0, axis=1), 12.0)
    rise = np.gradient(smooth)
    lo, hi = int(height * 0.38), int(height * 0.72)
    row = lo + int(np.argmax(rise[lo:hi]))
    above, below = max(0, row - 24), min(height - 1, row + 24)
    sky = luma[: max(above, 1)]
    noise = max(1.4826 * float(np.median(np.abs(sky - np.median(sky)))), 1.0)
    return row, float(smooth[below] - smooth[above]) / noise


def test_the_rise_is_measured_24_rows_either_side_of_the_row_against_the_sky_above_it() -> None:
    """The step's rise in noise units, as documented: on a frame whose sky is noisier than the one-code floor, on a
    small frame whose rows run out 24 rows above the skyline (the sky measured is then its first row), and on one
    whose rows run out 24 rows below it (the rise ends on its last row)."""
    assert pe.SKYLINE_STEP_REACH == 2.0
    for height, width, row in ((HEIGHT, WIDTH, 400), (50, 40, 21), (60, 40, 42)):
        luma = _step(row, ground=200.0, height=height, width=width, noise=3.0)
        expected_row, snr = _documented_rise(luma)
        found = pe.find_skyline(luma)
        assert found is not None and snr >= pe.SKYLINE_MIN_STEP_SNR, (height, found, snr)
        assert found.step_snr == pytest.approx(snr, rel=1e-9), (height, found.step_snr, snr)
        assert abs(found.row - expected_row) <= 1.0, (height, found.row, expected_row)
        if height == 50:
            assert expected_row - 24 < 1
        if height == 60:
            assert expected_row + 24 > height - 1


def test_the_half_width_is_the_rows_whose_rise_is_at_least_half_its_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean step between rows 399 and 400: the smoothed rise is a Gaussian of about 12 rows about 399.5, at least
    half its peak within 14.1 rows of it, on the 28 rows 386 to 413, so half of 28. Then, with the smoothing made a
    copy (a Gaussian of 0.01 rows has a one-weight kernel) and the step measured 3 rows either side, a profile whose
    rise is 10, 20, 20 and 10 codes per row on rows 399 to 402: the rows at exactly half the peak count, so half of 4.
    Then a rise that runs to both ends of a 40-row frame, the smoothing sharp: half of 40."""
    skyline = pe.find_skyline(_step(400, noise=0.0))
    assert skyline is not None and skyline.half_width_px == 14.0, skyline
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.01)
    monkeypatch.setattr(pe, "SKYLINE_STEP_REACH", 300.0)
    profile = np.full(HEIGHT, 2.0)
    profile[400:] = (22.0, 42.0, *([62.0] * (HEIGHT - 402)))
    rows = np.repeat(profile[:, None], 40, axis=1)
    assert list(np.gradient(profile)[398:404]) == [0.0, 10.0, 20.0, 20.0, 10.0, 0.0]
    skyline = pe.find_skyline(rows)
    assert skyline is not None and skyline.half_width_px == 2.0, skyline
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.3)
    monkeypatch.setattr(pe, "SKYLINE_STEP_REACH", 2.0)
    # 16 of 100 columns ramp 6 codes a row, the rest stay at 2: the 85th percentile ramps, the sky's median
    # absolute deviation stays 0
    ramp = np.full((40, 100), 2.0)
    ramp[:, 84:] = (2.0 + 6.0 * np.arange(40.0))[:, None]
    skyline = pe.find_skyline(ramp)
    assert skyline is not None and skyline.half_width_px == 20.0, skyline


def _documented_half_width(luma: np.ndarray) -> float:
    """The half width as documented, written as runs: of the rows whose smoothed rise is at least half the peak's,
    the unbroken run that holds the peak's row, its length halved."""
    height = luma.shape[0]
    rise = np.gradient(ndimage.gaussian_filter1d(np.percentile(luma, 85.0, axis=1), 12.0))
    lo, hi = int(height * 0.38), int(height * 0.72)
    row = lo + int(np.argmax(rise[lo:hi]))
    marked = np.flatnonzero(rise >= rise[row] / 2.0)
    runs = np.split(marked, np.flatnonzero(np.diff(marked) != 1) + 1)
    (run,) = [r for r in runs if row in r]
    return len(run) / 2.0


def test_the_half_width_is_the_whole_run_of_rows_about_the_peak_on_frames_of_either_parity() -> None:
    """Clean steps at rows 400 and 401, their first bright row lit to a tenth, a quarter, a half and three quarters
    of the step: the peak's row and the run's ends fall at both parities, so a walk that steps by more than a row
    misses an end on some of them."""
    for row in (400, 401):
        for fraction in (1.0, 0.1, 0.25, 0.5, 0.75):
            luma = np.full((HEIGHT, 40), 2.0)
            luma[row:, :] = 60.0
            luma[row, :] = 2.0 + 58.0 * fraction
            skyline = pe.find_skyline(luma)
            assert skyline is not None and skyline.half_width_px == _documented_half_width(luma), (row, fraction)


def test_a_rise_flat_across_the_bands_first_row_is_placed_on_that_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The smoothing made a copy and the step measured 10 rows either side: a profile that rises 2 codes a row from
    row 260 to row 300, so its rise is the same 2 on the band's first row and both its neighbours. The parabola has no
    peak there, so the skyline is the band's first row itself, no fraction added."""
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.01)
    monkeypatch.setattr(pe, "SKYLINE_STEP_REACH", 1000.0)
    lo = int(HEIGHT * pe.SKYLINE_BAND[0])
    profile = np.clip(2.0 + 2.0 * (np.arange(HEIGHT) - 260.0), 2.0, 2.0 + 2.0 * 40.0)
    rise = np.gradient(profile)
    assert rise[lo - 1] == rise[lo] == rise[lo + 1] == 2.0
    skyline = pe.find_skyline(np.repeat(profile[:, None], 40, axis=1))
    assert skyline is not None and skyline.row == float(lo), skyline


def test_a_four_row_frame_is_placed_to_a_fraction_of_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The smallest frame the band holds a row of, with the smoothing sharp: rows 2, 2, 60, 60. The band is its row 1,
    whose rise and its neighbours' put the step halfway between rows 1 and 2."""
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.3)
    luma = np.repeat(np.array([2.0, 2.0, 60.0, 60.0])[:, None], 40, axis=1)
    assert (int(4 * pe.SKYLINE_BAND[0]), int(4 * pe.SKYLINE_BAND[1])) == (1, 2)
    skyline = pe.find_skyline(luma)
    assert skyline is not None and skyline.row == pytest.approx(1.5, abs=0.01), skyline


def test_a_band_that_does_not_rise_is_no_skyline_and_one_that_rises_at_all_is_judged_by_its_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The smoothing made a copy and the step measured 10 rows either side: a frame that steps up 5 rows above the
    band and is flat inside it has no row in the band that rises, so no skyline, though its step measured from the
    band's first row passes; the same frame rising by a tenth of a code inside the band is judged by that step."""
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.01)
    monkeypatch.setattr(pe, "SKYLINE_STEP_REACH", 1000.0)
    lo = int(HEIGHT * pe.SKYLINE_BAND[0])
    profile = np.full(HEIGHT, 2.0)
    profile[lo - 5 :] = 52.0
    assert pe.find_skyline(np.repeat(profile[:, None], 40, axis=1)) is None
    profile[lo + 1 :] = 52.1
    assert 0.0 < float(np.max(np.gradient(profile)[lo : int(HEIGHT * pe.SKYLINE_BAND[1])])) <= 0.05 + 1e-9
    skyline = pe.find_skyline(np.repeat(profile[:, None], 40, axis=1))
    assert skyline is not None and skyline.step_snr == pytest.approx(50.1, rel=1e-9), skyline


def test_the_skyline_is_placed_to_a_fraction_of_a_row() -> None:
    """A clean step whose first bright row is lit to a fraction f of the step has its edge at 400.5 - f (row
    centres): the parabola through the rise's peak and its two neighbours places it there, on either side of a row."""
    for fraction in (0.1, 0.25, 0.75, 0.9):
        luma = np.full((HEIGHT, 40), 2.0)
        luma[400:, :] = 60.0
        luma[400, :] = 2.0 + 58.0 * fraction
        skyline = pe.find_skyline(luma)
        assert skyline is not None and abs(skyline.row - (400.5 - fraction)) <= 0.01, (fraction, skyline)


def test_a_frame_too_short_for_the_band_is_no_skyline_and_a_skyline_is_frozen() -> None:
    # One row: the band from 0.38 to before 0.72 of the height holds no row
    assert pe.find_skyline(np.full((1, 40), 2.0)) is None
    skyline = pe.find_skyline(_step(400))
    assert skyline is not None
    with pytest.raises(FrozenInstanceError):
        skyline.row = 0.0  # type: ignore[misc]


def test_the_no_skyline_sentence_says_why_first_and_its_measure_second() -> None:
    # C21 (.lead/uiux/UIUX-skylinewords-READY.md, decided 2026-11-19 13:20): the reason in plain words first, which the
    # warnings list shows; the band and the step looked for in a second sentence, kept in the record (C5's rule)
    assert pe.NO_SKYLINE == (
        "The sky is not lined up over the picture: no skyline was found to line the Moon up with. A skyline here is a "
        "row 38 to 72 percent of the way down below which the picture turns brighter by at least 10 times the sky's "
        "noise."
    )


def test_a_pixel_is_turned_upright_and_back_as_the_frame_is_turned() -> None:
    """to_upright puts a stored pixel where np.rot90(stored, -turns) moves it, for any whole number of turns (a turn
    count is taken modulo 4), and to_stored brings it back."""
    for turns in range(-4, 9):
        for width, height in ((7, 5), (5, 7)):
            for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (2, 3), (width - 1, height - 1)):
                stored = np.zeros((height, width))
                stored[y, x] = 1.0
                upright = np.rot90(stored, -turns)
                ((v, u),) = np.argwhere(upright == 1.0)
                assert pe.to_upright(x, y, turns, width, height) == (u, v), (turns, width, height, x, y)
                assert pe.to_stored(u, v, turns, upright.shape[1], upright.shape[0]) == (x, y), (turns, x, y)


def test_the_field_of_view_is_the_inverse_of_the_scale() -> None:
    for fov in (20.0, 60.0, 100.0, 130.0):
        for width, height in ((WIDTH, HEIGHT), (HEIGHT, WIDTH), (4032, 3024)):
            scale = pe.scale_deg_per_px(fov, width, height)
            assert pe.fov_diagonal_deg(scale, width, height) == pytest.approx(fov, rel=1e-12), (fov, width, height)


# --- The pose ---------------------------------------------------------------------------------------------------------


def test_a_moon_over_a_skyline_is_lined_up_with_the_skyline_swept() -> None:
    camera, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult), pose
    estimate = pose.pose_estimate
    assert (estimate.method, estimate.beacon, estimate.roll_deg) == ("moon_horizon", "moon", 0.0)
    assert pe.SKYLINE_ALTS_DEG == (0.0, 1.5, 3.0)
    assert len(estimate.swept_wcs) == 9 and pose.wcs in estimate.swept_wcs
    skyline = pose.skyline
    # Each swept WCS puts its skyline altitude on its row 10 degrees either side of its centre's azimuth: 0, 1.5 or 3
    # degrees at the row less, at and plus its half width; the nominal 1.5 degrees at the row found
    rows = (skyline.row - skyline.half_width_px, skyline.row, skyline.row + skyline.half_width_px)
    expected = [(alt, row) for alt in pe.SKYLINE_ALTS_DEG for row in rows]
    matches = [[abs(_row_at(w, alt) - row) <= 0.5 for alt, row in expected] for w in estimate.swept_wcs]
    assert all(any(column) for column in zip(*matches)), matches
    assert all(any(row) for row in matches), matches
    assert abs(_row_at(pose.wcs, 1.5) - skyline.row) <= 0.5
    # The field of view is fitted, not assumed, and comes out near the truth's
    assert estimate.fov_diagonal_deg == pytest.approx(EXAMPLE_FOV, abs=6.0)
    assert estimate.fov_source.startswith("Fitted, not assumed")
    # The beacon term: the Moon's radius plus the record's stated uncertainty plus the frame term, what the WCS's one
    # rotation leaves out across the sweep's frames (each its crval and half its fitted diagonal)
    moon = _moon()
    radius = _moon_level(MOON_MOMENT, LAT, LON)[2]
    swept = estimate.swept_wcs
    term = pe.frame_term_arcsec(pose.moon, swept, [pe.fov_diagonal_deg(w.scale_deg_per_px, w.width, w.height)
                                                    for w in swept], LAT, LON, MOON_MOMENT)
    assert 0.0 < term < 60.0, term
    assert estimate.beacon_centroid_uncertainty_deg == pytest.approx(
        radius + (moon.uncertainty_arcsec + term) / 3600.0, rel=1e-9
    )
    # The centre is stated where the camera's centre is seen: the nominal crval through the pose's frame rotation; the
    # crval's own apparent place lies within the frame term of it
    frame = pe.pose_frame(pose.moon, LAT, LON, MOON_MOMENT)
    stated = catalog.icrs_to_frame_altaz(frame, pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg)
    assert (estimate.center_alt_deg, estimate.center_az_deg) == pytest.approx(stated, abs=1e-9)
    seen = catalog.icrs_to_altaz(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, LAT, LON, MOON_MOMENT)
    assert angular_separation_deg(seen[1], seen[0], stated[1], stated[0]) * 3600.0 <= term
    assert estimate.max_center_offset_deg == pytest.approx(
        max(angular_separation_deg(pose.wcs.crval_ra_deg, pose.wcs.crval_dec_deg, w.crval_ra_deg, w.crval_dec_deg)
            for w in estimate.swept_wcs), rel=1e-12
    )
    assert _assert_inside_the_bound(pose, camera, MOON_MOMENT, LAT, LON) >= 3


def test_the_horizon_pose_states_its_assumptions_with_the_numbers_it_used() -> None:
    """The skyline's row and half width as measured, the swept altitudes, the field of view range, and the beacon
    term's parts: the Moon's radius from its distance and the uncertainty the ephemeris record states, in arcsec."""
    _, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult)
    skyline, assumptions = pose.skyline, pose.pose_estimate.assumptions
    assert len(assumptions) == 7
    assert assumptions[1] == (
        f"The skyline at row {skyline.row:.0f} of the upright frame is taken to be level ground 0 to 3 degrees above "
        "the true horizon (hills, buildings and refraction lift it); the sweep takes 0, 1.5 and 3 degrees. A sea "
        "horizon seen from a height lies below the true horizon by its dip, which the sweep does not cover."
    )
    assert assumptions[2] == (
        f"The skyline's row is measured to within {skyline.half_width_px:.0f} px, the half width of the rise in "
        f"brightness there; the sweep takes the row and {skyline.half_width_px:.0f} px either side."
    )
    assert assumptions[4].endswith("a fit outside 20 to 130 degrees across the diagonal is refused.")
    radius = _moon_level(MOON_MOMENT, LAT, LON)[2]
    swept = pose.pose_estimate.swept_wcs
    term = pe.frame_term_arcsec(pose.moon, swept, [pe.fov_diagonal_deg(w.scale_deg_per_px, w.width, w.height)
                                                    for w in swept], LAT, LON, MOON_MOMENT)
    assert (
        f"the Moon's predicted radius of {radius:.2f} degrees plus the ephemeris's stated uncertainty of "
        f"{_moon().uncertainty_arcsec:,.0f} arcsec plus {term:,.0f} arcsec for drawing every position through one "
        "rotation"
    ) in assumptions[5], assumptions[5]


def _row_at(wcs: WCSModel, alt: float) -> float:
    """The row of a stored-upright frame where a WCS puts the altitude `alt` (through the pose's frame rotation), 10
    degrees either side of its centre's azimuth: the two points' mean row (a level WCS puts them on one row)."""
    frame = pe.pose_frame(pe.topocentric_moon(MOON_MOMENT, LAT, LON), LAT, LON, MOON_MOMENT)
    _, az = catalog.icrs_to_frame_altaz(frame, wcs.crval_ra_deg, wcs.crval_dec_deg)
    rows = [wcs.world_to_pixel(*catalog.frame_altaz_to_icrs(frame, alt, az + side))[1] for side in (-10.0, 10.0)]
    assert abs(rows[0] - rows[1]) <= 0.5, rows
    return (rows[0] + rows[1]) / 2.0


def _assert_inside_the_bound(pose: PoseResult, camera: _Camera, moment: str, lat: float, lon: float,
                             turns: int = 0) -> int:
    """The Moon on the spot within half a pixel under every WCS; every predicted object truly in the frame inside
    its ring (the largest distance from its nominal pixel to its swept pixels plus the beacon term in pixels at the
    nominal scale); the true centre within maxCenterOffsetDeg plus the beacon term."""
    estimate = pose.pose_estimate
    moon = _moon(moment, lat, lon)
    for wcs in (pose.wcs, *estimate.swept_wcs):
        mx, my = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
        assert math.hypot(mx - pose.blob.x_px, my - pose.blob.y_px) <= 0.5
    term_px = estimate.beacon_centroid_uncertainty_deg / pose.wcs.scale_deg_per_px
    stored_w, stored_h = pose.wcs.width, pose.wcs.height
    checked = 0
    for finding in predicted_sky_findings(moment, lat, lon, solution_id="sol", source_sha256=SHA):
        truth = camera.upright_pixel(finding.ra_deg, finding.dec_deg)
        if truth is None or not (0.0 <= truth[0] < camera.up_w and 0.0 <= truth[1] < camera.up_h):
            continue
        tx, ty = _to_stored(truth[0], truth[1], turns, camera.up_w, camera.up_h)
        nx, ny = pose.wcs.world_to_pixel(finding.ra_deg, finding.dec_deg)
        ring = max(math.hypot(x - nx, y - ny) for x, y in (w.world_to_pixel(finding.ra_deg, finding.dec_deg)
                                                           for w in estimate.swept_wcs))
        assert math.hypot(tx - nx, ty - ny) <= ring + term_px, (finding.id, (tx, ty), (nx, ny), ring, term_px)
        checked += 1
    centre = angular_separation_deg(camera.az, camera.alt, estimate.center_az_deg, estimate.center_alt_deg)
    assert centre <= estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg, (
        centre, estimate.max_center_offset_deg)
    assert (stored_w, stored_h) == ((WIDTH, HEIGHT) if turns % 2 == 0 else (HEIGHT, WIDTH))
    return checked


def test_a_light_on_the_ground_below_the_skyline_is_no_rival_and_two_spots_above_it_refuse() -> None:
    camera, base = _example()
    pose = _pose(base)
    assert isinstance(pose, PoseResult)
    radius = pose.blob.equivalent_diameter_px / 2.0
    # A Moon-sized light at 255 on the ground, below the skyline: not a candidate, and no rival
    _, ground_lit = _example(spots=((700.0, 600.0, radius, 255.0),))
    assert isinstance(_pose(ground_lit), PoseResult)
    # The same light above the skyline: two spots pass the Moon test on their own
    _, two = _example(spots=((700.0, 120.0, radius, 240.0),))
    refusal = _pose(two)
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon"
    assert "more than one spot passes the Moon test on its own" in refusal.reason


def test_a_spot_cut_by_the_skyline_is_refused() -> None:
    """A Moon touching the skyline is cut by it: its centroid moves off its centre, so it is refused, saying so."""
    camera, luma = _example()
    skyline = pe.find_skyline(luma)
    assert skyline is not None
    # A Moon-sized spot across the lower edge of the sky searched (the skyline less its half width), the Moon gone
    _, cut = _example(moon=False, spots=((480.0, skyline.row - skyline.half_width_px - 1.0, 4.0, 244.0),))
    refusal = _pose(cut)
    assert isinstance(refusal, Refusal) and refusal.kind == "not_moon", refusal
    assert "the brightest spot is cut by the skyline" in refusal.reason


def test_by_day_with_the_moon_down_or_a_mirrored_frame_there_is_no_horizon_pose() -> None:
    camera, luma = _example()
    lat, lon = _daytime_place()
    refusal = _pose(luma, lat=lat, lon=lon)
    assert isinstance(refusal, Refusal) and refusal.kind == "daytime" and refusal.reason == pe.DAYTIME
    down_moment = _moon_down_moment()
    refusal = _pose(luma, moment=down_moment)
    assert isinstance(refusal, Refusal) and refusal.reason == pe.MOON_NOT_UP
    mirrored = pe.horizon_moon_pose(luma, frame_time_sec=0.0, moment=MOON_MOMENT, lat=LAT, lon=LON,
                                    orientation=_orientation(0, mirrored=True))
    assert isinstance(mirrored, Refusal) and mirrored.reason == pe.MIRRORED
    flat = _draw(camera, MOON_MOMENT, LAT, LON, skyline_alt=-30.0)
    refusal = _pose(flat)
    assert isinstance(refusal, Refusal) and refusal.kind == "no_skyline" and refusal.reason == pe.NO_SKYLINE


def test_a_fit_whose_field_of_view_is_outside_20_to_130_degrees_or_whose_spot_is_not_the_moons_size_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert pe.HORIZON_FOV_DIAGONAL_RANGE_DEG == (20.0, 130.0)
    # The range moved to just above the field of view this scene fits to: refused for it
    camera, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult)
    fov = pose.pose_estimate.fov_diagonal_deg
    monkeypatch.setattr(pe, "HORIZON_FOV_DIAGONAL_RANGE_DEG", (fov + 10.0, 130.0))
    refusal = _pose(luma)
    assert isinstance(refusal, Refusal) and refusal.kind == "fov", refusal
    assert "is outside" in refusal.reason and TAKEN in refusal.reason
    monkeypatch.setattr(pe, "HORIZON_FOV_DIAGONAL_RANGE_DEG", (20.0, 130.0))
    # A lamp 12 times the Moon's width where the Moon is: the fit stands, its size does not
    _, lamp = _example(bloom=12.0)
    refusal = _pose(lamp)
    assert isinstance(refusal, Refusal) and refusal.kind == "size", refusal
    assert "times the Moon's predicted diameter" in refusal.reason
    # A missed fit refuses too
    monkeypatch.setattr(pe, "MAX_FIT_RESIDUAL_PX", -1.0)
    refusal = _pose(luma)
    assert isinstance(refusal, Refusal) and refusal.kind == "fit"
    assert refusal.reason.startswith(
        "The sky is not lined up over the picture: the fit found no camera that puts the Moon on the bright spot with "
        "the skyline level. The spot is at ("
    ) and refusal.reason.endswith("; the fit allows -1 px."), refusal.reason


def test_each_refused_horizon_fit_names_its_own_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three causes, three sentences: a spot that does not stand above the skyline cannot start a fit (a guard for a
    caller, since the pose searches only the sky above it); a fit that meets its constraints with the sky upside
    down; and one that finds no camera within the residual limit (the test above)."""
    _, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult)
    moon = pose.moon
    below_blob = replace(pose.blob, y_px=pose.skyline.row + 10.0)
    below = pe.fit_horizon_pose(moon, below_blob, width=WIDTH, height=HEIGHT,
                                skyline_alt_deg=1.5, skyline_row=pose.skyline.row, turns=0, moment=MOON_MOMENT, lat=LAT,
                                lon=LON, where="in the photo")
    assert isinstance(below, Refusal) and below.kind == "fit"
    assert below.reason == pe.horizon_start_reason(below_blob.x_px, below_blob.y_px, "in the photo", pose.skyline.row)
    assert "so no fit could start. " in below.reason and " and the skyline at row " in below.reason
    # The Moon below the skyline's altitude: the same refusal
    low = pe.fit_horizon_pose(replace(moon, alt_deg=1.0), pose.blob, width=WIDTH, height=HEIGHT,
                              skyline_alt_deg=1.5, skyline_row=pose.skyline.row, turns=0, moment=MOON_MOMENT, lat=LAT,
                              lon=LON)
    assert isinstance(low, Refusal) and "so no fit could start. " in low.reason
    # The fit's own camera turned half a turn about the frame centre, its constraints taken as met: upside down
    real = pe._normalized
    monkeypatch.setattr(pe, "_fit_accepted", lambda residuals: True)
    monkeypatch.setattr(pe, "_normalized", lambda wcs: real(replace(wcs, rotation_deg=wcs.rotation_deg + 180.0)))
    upside = _pose(luma)
    assert isinstance(upside, Refusal) and upside.kind == "fit"
    assert "shows the sky upside down. The spot is at " in upside.reason, upside.reason


def test_a_fit_starts_only_from_a_spot_above_the_skyline_and_a_moon_above_its_altitude() -> None:
    """The start guard's edges: a spot on the skyline's row, or a Moon at the skyline's altitude, gives no start; a spot
    a twentieth of a row above it, or a Moon a millionth of a degree above it, does (whatever the fit then finds)."""
    _, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult)
    row, moon = pose.skyline.row, pose.moon
    kwargs = dict(width=WIDTH, height=HEIGHT, skyline_alt_deg=1.5, skyline_row=row, turns=0, moment=MOON_MOMENT,
                  lat=LAT, lon=LON)

    def no_start(result: WCSModel | Refusal) -> bool:
        return isinstance(result, Refusal) and "so no fit could start. " in result.reason

    assert no_start(pe.fit_horizon_pose(moon, replace(pose.blob, y_px=row), **kwargs))
    assert not no_start(pe.fit_horizon_pose(moon, replace(pose.blob, y_px=row - 0.05), **kwargs))
    assert no_start(pe.fit_horizon_pose(replace(moon, alt_deg=1.5), pose.blob, **kwargs))
    assert not no_start(pe.fit_horizon_pose(replace(moon, alt_deg=1.5 + 1e-6), pose.blob, **kwargs))


def test_the_horizon_fit_penalises_a_scale_not_above_zero_and_refuses_fits_that_end_badly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The residuals the fit is given are 1e4 px on each of the four for a scale of 0 or less, and for a centre that
    gives no finite residual (probed through the solver's own call). A fit that ends at a negative scale is refused,
    saying no camera was found, even where it is the same projection turned half a turn (whose field of view would
    read negative). A fit whose skyline's two points lie 0.05 px apart, its constraints taken as met, is right way up
    and refused for its field of view."""
    from types import SimpleNamespace

    _, luma = _example()
    pose = _pose(luma)
    assert isinstance(pose, PoseResult)
    blob, row = pose.blob, pose.skyline.row
    kwargs = dict(width=WIDTH, height=HEIGHT, skyline_alt_deg=1.5, skyline_row=row, turns=0, moment=MOON_MOMENT,
                  lat=LAT, lon=LON)
    probes: list[np.ndarray] = []
    found: list[np.ndarray] = []
    real = pe.optimize.least_squares

    def probing(fun: Any, x0: Any, **options: Any) -> Any:
        x0 = np.asarray(x0, dtype=np.float64)
        for scale in (0.0, -0.01):
            probes.append(np.asarray(fun(np.array([x0[0], x0[1], scale, x0[3]]))))
        probes.append(np.asarray(fun(np.array([math.nan, x0[1], x0[2], x0[3]]))))
        probes.append(np.asarray(fun(np.array([(x0[0] + 180.0) % 360.0, -x0[1], x0[2], x0[3]]))))
        result = real(fun, x0, **options)
        found.append(np.array(result.x))
        return result

    monkeypatch.setattr(pe.optimize, "least_squares", probing)
    assert isinstance(pe.fit_horizon_pose(pose.moon, blob, **kwargs), WCSModel)
    assert len(probes) == 4
    for probe in probes:
        assert probe.shape == (4,) and np.all(probe == 1e4), probe
    ra, dec, scale, rotation = found[0]
    fit_refused = pe.horizon_fit_reason(blob.x_px, blob.y_px, "in the frame", row)
    for x in ((ra, dec, -0.01, rotation), (ra, dec, -scale, rotation + 180.0), (math.nan, dec, scale, rotation)):
        monkeypatch.setattr(pe.optimize, "least_squares", lambda fun, x0, x=x, **options: SimpleNamespace(x=np.array(x)))
        refused = pe.fit_horizon_pose(pose.moon, blob, **kwargs)
        assert isinstance(refused, Refusal) and (refused.kind, refused.reason) == ("fit", fit_refused), (x, refused)
    # The skyline's two points 10 degrees either side span 20 degrees of azimuth: at k times the scale, 1/k as many px
    wcs = pe._normalized(WCSModel(crval_ra_deg=ra, crval_dec_deg=dec, crpix_x=WIDTH / 2.0, crpix_y=HEIGHT / 2.0,
                                  scale_deg_per_px=scale, rotation_deg=rotation, k1=0.0, k2=0.0, parity=-1,
                                  width=WIDTH, height=HEIGHT))
    frame = pe.pose_frame(pose.moon, LAT, LON, MOON_MOMENT)
    _, az = catalog.icrs_to_frame_altaz(frame, wcs.crval_ra_deg, wcs.crval_dec_deg)
    left, right = (wcs.world_to_pixel(*catalog.frame_altaz_to_icrs(frame, 1.5, az + side))[0] for side in (-10.0, 10.0))
    k = (right - left) / 0.05
    monkeypatch.setattr(pe, "_fit_accepted", lambda residuals: True)
    monkeypatch.setattr(pe.optimize, "least_squares",
                        lambda fun, x0, **options: SimpleNamespace(x=np.array([ra, dec, scale * k, rotation])))
    narrow = pe.fit_horizon_pose(pose.moon, blob, **kwargs)
    assert isinstance(narrow, Refusal) and narrow.kind == "fov", narrow
    # A scale of 1e300 puts both of the skyline's points on the centre pixel itself, a span of exactly 0: no right way
    # up, so upside down
    monkeypatch.setattr(pe.optimize, "least_squares",
                        lambda fun, x0, **options: SimpleNamespace(x=np.array([ra, dec, 1e300, rotation])))
    flat = pe.fit_horizon_pose(pose.moon, blob, **kwargs)
    assert isinstance(flat, Refusal) and (flat.kind, flat.reason) == (
        "fit", pe.horizon_upside_down_reason(blob.x_px, blob.y_px, "in the frame", row)), flat
    # A fit that ends at a scale of exactly 0 is refused before any projection
    monkeypatch.setattr(pe.optimize, "least_squares",
                        lambda fun, x0, **options: SimpleNamespace(x=np.array([ra, dec, 0.0, rotation])))
    zero = pe.fit_horizon_pose(pose.moon, blob, **kwargs)
    assert isinstance(zero, Refusal) and (zero.kind, zero.reason) == ("fit", fit_refused), zero


def test_a_fit_that_ends_at_any_negative_scale_is_refused_even_as_the_same_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property's narrow example (the Moon about 10 degrees up, a 40 degree frame), whose fitted scale is under 0.1
    degree a pixel: the same projection at the negative of that scale, turned half a turn, meets every constraint,
    and is still refused, saying no camera was found."""
    from types import SimpleNamespace

    lat, lon = _place_with_moon_near(10.0, 90.0)
    assert _sun_alt(MOON_MOMENT, lat, lon) < pe.SUNRISE_SUNSET_ALT_DEG
    camera = _camera_for(MOON_MOMENT, lat, lon, fov=40.0, skyline_alt=3.0, row_fraction=0.45, moon_column=0.5,
                         up_w=WIDTH, up_h=HEIGHT)
    assert camera is not None
    luma = _draw(camera, MOON_MOMENT, lat, lon, skyline_alt=3.0)
    pose = _pose(luma, lat=lat, lon=lon)
    assert isinstance(pose, PoseResult), pose
    blob, row = pose.blob, pose.skyline.row
    kwargs = dict(width=WIDTH, height=HEIGHT, skyline_alt_deg=1.5, skyline_row=row, turns=0, moment=MOON_MOMENT,
                  lat=lat, lon=lon)
    found: list[np.ndarray] = []
    real = pe.optimize.least_squares

    def recording(fun: Any, x0: Any, **options: Any) -> Any:
        result = real(fun, x0, **options)
        found.append(np.array(result.x))
        return result

    monkeypatch.setattr(pe.optimize, "least_squares", recording)
    assert isinstance(pe.fit_horizon_pose(pose.moon, blob, **kwargs), WCSModel)
    ra, dec, scale, rotation = found[0]
    assert 0.0 < scale < 0.1, scale
    monkeypatch.setattr(pe.optimize, "least_squares", lambda fun, x0, **options: SimpleNamespace(
        x=np.array([ra, dec, -scale, rotation + 180.0])))
    refused = pe.fit_horizon_pose(pose.moon, blob, **kwargs)
    assert isinstance(refused, Refusal) and (refused.kind, refused.reason) == (
        "fit", pe.horizon_fit_reason(blob.x_px, blob.y_px, "in the frame", row)), refused


def test_a_brighter_band_above_the_skyline_is_no_skyline_of_its_own() -> None:
    """The skyline is where the frame turns brighter below, never darker: a bright twilight band that ends in a
    darker sky, its fall steeper than the skyline's rise, does not hide the skyline below it."""
    rng = np.random.default_rng(9)
    luma = np.full((HEIGHT, WIDTH), 100.0)
    luma[300:, :] = 2.0
    luma[450:, :] = 60.0
    luma = np.clip(luma + rng.normal(0.0, 1.0, luma.shape), 0.0, None)
    skyline = pe.find_skyline(luma)
    assert isinstance(skyline, pe.Skyline) and abs(skyline.row - 449.5) <= 1.0, skyline


def _frames_pose(frames: list[tuple[float, np.ndarray]], *, moments: dict[float, str] | None = None,
                 lat: float = LAT, lon: float = LON, is_clip: bool = True) -> PoseResult | Refusal:
    timed = [(t, (moments or {}).get(t, frame_moment(MOON_MOMENT, t)), luma) for t, luma in frames]
    return pe.horizon_pose_from_frames(timed, lat=lat, lon=lon, orientation=None, is_clip=is_clip)


def test_over_several_frames_the_horizon_pose_says_what_says_most(monkeypatch: pytest.MonkeyPatch) -> None:
    """horizon_pose_from_frames (REQ-SKY-4.1, the order of REQ-SKY-3.12): the first frame in time order that gives a
    pose is used, whatever order the frames come in; with none, a spot above a skyline that gave no pose says most
    (the first such frame in time order), then not confirmed as the Moon (counting the frames searched, or "in the
    photo"), then no skyline; with no frame read, no frames."""
    camera, posed = _example()
    _, sky_only = _example(moon=False)
    flat = _draw(camera, MOON_MOMENT, LAT, LON, skyline_alt=-30.0)
    first = _frames_pose([(3.0, posed), (0.0, flat), (1.5, posed)])
    assert isinstance(first, PoseResult) and first.frame_time_sec == 1.5
    # In the order of their time into the clip, not of their moment's text: "...:01Z" sorts after "...:01.500Z"
    assert frame_moment(MOON_MOMENT, 1.5) < frame_moment(MOON_MOMENT, 1.0)
    earlier = _frames_pose([(1.5, posed), (1.0, posed)])
    assert isinstance(earlier, PoseResult) and earlier.frame_time_sec == 1.0
    # Frames given mirrored are refused as mirrored, before any frame is judged
    mirrored = pe.horizon_pose_from_frames([(0.0, MOON_MOMENT, posed)], lat=LAT, lon=LON,
                                           orientation=_orientation(0, mirrored=True))
    assert mirrored == Refusal("mirrored", pe.MIRRORED)
    assert _frames_pose([]) == Refusal("no_frames", pe.NO_FRAMES)
    # Not confirmed, counted, over frames with no skyline; no skyline alone
    two = _frames_pose([(0.0, sky_only), (1.0, flat), (2.0, sky_only)])
    assert (two.kind, two.reason) == ("not_moon", pe.not_confirmed_above_skyline_reason("in the 2 frames searched"))
    # Frames are a clip's unless the caller says otherwise
    default = pe.horizon_pose_from_frames(
        [(t, frame_moment(MOON_MOMENT, t), luma) for t, luma in ((0.0, sky_only), (2.0, sky_only))], lat=LAT, lon=LON,
        orientation=None,
    )
    assert default.reason == two.reason
    one = _frames_pose([(1.0, flat), (0.0, sky_only)])
    assert one.reason == pe.not_confirmed_above_skyline_reason("in the frame searched")
    photo = _frames_pose([(0.0, sky_only)], is_clip=False)
    assert photo.reason == pe.not_confirmed_above_skyline_reason("in the photo")
    none = _frames_pose([(0.0, flat), (1.0, flat)])
    assert (none.kind, none.reason) == ("no_skyline", pe.NO_SKYLINE)
    # A spot that gave no pose says more than frames not confirmed, and the first in time order is named
    monkeypatch.setattr(pe, "fit_horizon_pose", lambda moon, blob, **kwargs: Refusal("fit", "refused for the test"))
    for frames in ([(0.0, sky_only), (2.0, posed), (4.0, posed), (5.0, flat)], [(4.0, posed), (5.0, flat), (2.0, posed)]):
        refused = _frames_pose(frames)
        assert refused.kind == "fit" and refused.frame_time_sec == 2.0 and refused.blob is not None, refused


def test_over_several_frames_the_moon_not_up_is_said_only_when_no_frame_has_it_up() -> None:
    """With no frame searched, the Moon not up is said when every frame has it down; frames that mix the Moon down
    and by day answer by day, in either order (REQ-SKY-3.12's rule, the horizon pose's frames)."""
    _, posed = _example()
    lat, lon = _daytime_place()
    down = next(
        moment
        for moment in (frame_moment(MOON_MOMENT, hours * 3600.0) for hours in range(1, 25))
        if _moon(moment, lat, lon).alt_deg <= pe.MIN_MOON_ALT_DEG
    )
    moments = {0.0: down, 1.0: MOON_MOMENT}
    for frames in ([(0.0, posed), (1.0, posed)], [(1.0, posed), (0.0, posed)], [(1.0, posed)]):
        refused = _frames_pose(frames, moments=moments, lat=lat, lon=lon)
        assert (refused.kind, refused.reason) == ("daytime", pe.DAYTIME), frames
    refused = _frames_pose([(0.0, posed)], moments=moments, lat=lat, lon=lon)
    assert (refused.kind, refused.reason) == ("moon_not_up", pe.MOON_NOT_UP)


def test_a_moon_near_the_frames_left_edge_is_lined_up_from_the_fits_own_start() -> None:
    """The fit starts at the azimuth the spot's column gives, the Moon's azimuth less its offset from the centre column:
    a Moon a tenth of the way in from the left edge of a 100 degree frame, the skyline 3 degrees up, is lined up and
    holds its bound (a start that adds the offset instead ends in another camera and is refused)."""
    camera, luma = _example(fov=100.0, row_fraction=0.6, moon_column=0.1, skyline_alt=3.0)
    pose = _pose(luma)
    assert isinstance(pose, PoseResult), pose
    _assert_inside_the_bound(pose, camera, MOON_MOMENT, LAT, LON)


def test_a_frame_stored_turned_is_lined_up_as_it_is_shown() -> None:
    for turns in (1, 2, 3):
        camera, luma = _example(turns=turns)
        assert luma.shape == ((HEIGHT, WIDTH) if turns % 2 == 0 else (WIDTH, HEIGHT))
        pose = _pose(luma, turns=turns)
        assert isinstance(pose, PoseResult), (turns, pose)
        assert _assert_inside_the_bound(pose, camera, MOON_MOMENT, LAT, LON, turns) >= 3


# --- P-SKY (REQ-SKY-4.2): the bound over synthetic truths --------------------------------------------------------


def _destination(lat: float, lon: float, bearing_deg: float, distance_deg: float) -> tuple[float, float]:
    p, lam = math.radians(lat), math.radians(lon)
    b, d = math.radians(bearing_deg), math.radians(distance_deg)
    p2 = math.asin(math.sin(p) * math.cos(d) + math.cos(p) * math.sin(d) * math.cos(b))
    lam2 = lam + math.atan2(math.sin(b) * math.sin(d) * math.cos(p), math.cos(d) - math.sin(p) * math.sin(p2))
    return math.degrees(p2), (math.degrees(lam2) + 180.0) % 360.0 - 180.0


def _place_with_moon_near(alt_deg: float, bearing_deg: float) -> tuple[float, float]:
    """A place about `alt_deg` from where the Moon stands overhead, along `bearing_deg`, on a sphere: the record's
    own altitude there differs by up to about a degree, and is read, not assumed."""
    (geo,) = compute_solar_system_ephemerides(MOON_MOMENT, LAT, LON, bodies=("Moon",), topocentric=False)
    under = _destination(LAT, LON, geo.az_deg, 90.0 - geo.alt_deg)
    return _destination(*under, bearing_deg, 90.0 - alt_deg)


def _sun_alt(moment: str, lat: float, lon: float) -> float:
    return compute_solar_system_ephemerides(moment, lat, lon, bodies=("Sun",))[0].alt_deg


def _daytime_place() -> tuple[float, float]:
    for bearing in range(0, 360, 10):
        lat, lon = _place_with_moon_near(15.0, float(bearing))
        if _sun_alt(MOON_MOMENT, lat, lon) > 5.0:
            return lat, lon
    raise AssertionError("no daytime place with the Moon up")


def _moon_down_moment() -> str:
    for hours in range(1, 24):
        moment = frame_moment(MOON_MOMENT, hours * 3600.0)
        if _moon(moment).alt_deg < 0.0:
            return moment
    raise AssertionError("no moment with the Moon down")


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
@given(
    moon_alt=st.floats(8.0, 60.0),
    bearing=st.floats(0.0, 360.0, exclude_max=True),
    fov=st.floats(40.0, 100.0),
    skyline_alt=st.floats(0.0, 3.0),
    row_fraction=st.floats(0.45, 0.68),
    moon_column=st.floats(0.08, 0.92),
    turns=st.sampled_from([0, 1, 2, 3]),
    bloom=st.floats(1.0, 3.0),
    blur=st.floats(0.5, 2.0),
)
@example(moon_alt=33.0, bearing=0.0, fov=60.0, skyline_alt=0.0, row_fraction=0.6, moon_column=0.5, turns=0, bloom=1.0, blur=1.5)
@example(moon_alt=33.0, bearing=180.0, fov=100.0, skyline_alt=3.0, row_fraction=0.68, moon_column=0.92, turns=1, bloom=2.0, blur=1.0)
@example(moon_alt=10.0, bearing=90.0, fov=40.0, skyline_alt=3.0, row_fraction=0.45, moon_column=0.08, turns=3, bloom=1.0, blur=2.0)
@example(moon_alt=55.0, bearing=270.0, fov=100.0, skyline_alt=0.0, row_fraction=0.68, moon_column=0.5, turns=2, bloom=3.0, blur=0.5)
def test_pbt_whenever_the_horizon_pose_is_approximate_the_truth_lies_inside_its_bound(
    moon_alt: float, bearing: float, fov: float, skyline_alt: float, row_fraction: float, moon_column: float,
    turns: int, bloom: float, blur: float,
) -> None:
    lat, lon = _place_with_moon_near(moon_alt, bearing)
    assume(_sun_alt(MOON_MOMENT, lat, lon) < pe.SUNRISE_SUNSET_ALT_DEG)
    camera = _camera_for(MOON_MOMENT, lat, lon, fov=fov, skyline_alt=skyline_alt, row_fraction=row_fraction,
                         moon_column=moon_column, up_w=WIDTH, up_h=HEIGHT)
    assume(camera is not None)
    m = _moon(MOON_MOMENT, lat, lon)
    spot = camera.upright_pixel(m.ra_deg, m.dec_deg)
    radius = _moon_level(MOON_MOMENT, lat, lon)[2] / math.degrees(camera.scale) * bloom
    # The Moon whole in the frame, above the skyline's band by its own size
    assume(spot is not None and radius + 6.0 < spot[0] < WIDTH - radius - 6.0 and radius + 6.0 < spot[1])
    assume(spot[1] + radius + 4.0 * blur + 2.0 * pe.SKYLINE_SMOOTHING_ROWS < row_fraction * (HEIGHT - 1))
    luma = _draw(camera, MOON_MOMENT, lat, lon, skyline_alt=skyline_alt, turns=turns, bloom=bloom, blur=blur)
    pose = _pose(luma, lat=lat, lon=lon, turns=turns)
    event(f"outcome: {pose.kind if isinstance(pose, Refusal) else 'approximate'}")
    if isinstance(pose, Refusal):
        assert pose.kind in {"not_moon", "no_skyline", "fit", "fov", "size"}, pose
        return
    _assert_inside_the_bound(pose, camera, MOON_MOMENT, lat, lon, turns)


# --- The approximate solution and its words -------------------------------------------------------------------------


def _solver_solution(**overrides: Any) -> ObservationSolution:
    return ObservationSolution(
        id="sol-synthetic-horizon",
        source_asset_id="asset-synthetic-horizon",
        source_sha256=SHA,
        frame_time_sec=0.0,
        revision=1,
        status="unresolved",
        wcs=None,
        quality=SolutionQuality(
            detected_candidate_count=3, matched_inlier_count=0, held_out_pair_count=0, held_out_inliers=0,
            median_residual_px=None, p95_residual_px=None, median_residual_arcsec=None,
            passed_independent_verification=False,
        ),
        solver=SolverIdentity(name="SyntheticSolver", version="0", catalog_release="none"),
        findings=[],
        warnings=["Too few stars for a plate solve."],
        **overrides,
    )


def _asset(*, kind: str = "image", model: str | None = "Synthetic Phone", turns: int = 0) -> dict[str, Any]:
    asset: dict[str, Any] = {
        "id": "asset-synthetic-horizon",
        "mediaKind": kind,
        "width": WIDTH if turns % 2 == 0 else HEIGHT,
        "height": HEIGHT if turns % 2 == 0 else WIDTH,
        "capturedAtUTC": MOON_MOMENT,
        "observerLocation": {"latDeg": LAT, "lonDeg": LON},
        "displayOrientation": _orientation(turns),
    }
    if model is not None:
        asset["cameraMetadata"] = {"model": model}
    return asset


def _texts(solution: ObservationSolution) -> list[str]:
    texts = list(solution.warnings)
    for finding in solution.findings:
        texts += [finding.title, finding.subtitle, *finding.limitations, finding.verification_method or ""]
    if solution.pose_estimate is not None:
        texts += [solution.pose_estimate.fov_source, *solution.pose_estimate.assumptions]
    return texts


def test_a_photo_from_a_camera_without_a_head_worn_profile_is_lined_up_from_the_moon_and_the_skyline() -> None:
    camera, luma = _example()
    calls: list[int] = []

    def frames() -> list[tuple[float, np.ndarray]]:
        calls.append(1)
        return [(0.0, luma)]

    solver = _solver_solution()
    out = complete_sky(solver, _asset(), frames=frames)
    assert out.status == "approximate", out.warnings
    assert calls == [1]
    estimate = out.pose_estimate
    assert estimate.method == "moon_horizon" and out.solver.name == "AlbedianHorizonMoonPose"
    q = out.quality
    assert (q.detected_candidate_count, q.matched_inlier_count, q.held_out_pair_count, q.held_out_inliers) == (3, 0, 0, 0)
    assert q.passed_independent_verification is False
    (spot,) = [f for f in out.findings if f.evidence_class == "observed"]
    assert spot.id == "finding-observed-moon" and spot.title == "Bright Spot (Taken To Be The Moon)"
    assert spot.verified_by_holdout is False and spot.ra_deg is None
    assert "no held-out check was made" in spot.verification_method
    assert spot.limitations[0].startswith("Taken to be the Moon, not shown to be")
    assert not any(f.evidence_class == "catalog_matched" for f in out.findings)
    within = math.ceil(estimate.max_center_offset_deg + estimate.beacon_centroid_uncertainty_deg)
    lined = out.warnings[-2]
    assert lined.startswith(f"Lined up by taking the bright spot at ({spot.source_detection.x_px:.0f}, "
                            f"{spot.source_detection.y_px:.0f}) px in the photo to be the Moon and the skyline")
    assert "to be level ground 0 to 3 degrees above the true horizon" in lined
    assert f"within about {within} degrees" in lined
    assert lined.endswith("not checked against stars.")
    # The predicted sky is the predicted sky's own, for the frame's moment
    predicted = {f.id for f in out.findings if f.evidence_class == "predicted"}
    assert predicted == {f.id for f in predicted_sky_findings(MOON_MOMENT, LAT, LON, solution_id=out.id, source_sha256=SHA)}
    # Predicted star subtitles hold their own pc distance and sky Alt/Az (where a Sagittarius star sits at 40.0 pc),
    # while warnings, pose assumptions, titles, limitations and observed subtitles hold none of LEAKS
    own_texts = [
        *out.warnings,
        out.pose_estimate.fov_source,
        *out.pose_estimate.assumptions,
        *(
            t
            for f in out.findings
            for t in (f.title, *f.limitations, f.verification_method or "", *(() if f.evidence_class == "predicted" else (f.subtitle,)))
        ),
    ]
    for text in own_texts:
        for value in LEAKS:
            assert value not in text, (value, text)
    for text in _texts(out):
        for value in (MOON_MOMENT, MOON_MOMENT[:10], MOON_MOMENT[11:16], "-40.0", "-40.00", "30.00"):
            assert value not in text, (value, text)
        for phrase in SAYS_IT_IS_THE_MOON:
            assert phrase not in text, (phrase, text)
    (inside,) = {text for f in out.findings for text in f.limitations if "approximate pose" in text}
    assert "taking the frame's bright spot to be the Moon and its skyline to be level ground" in inside


def test_a_clips_detections_in_other_frames_are_kept_with_a_sentence_that_takes_the_spot_to_be_the_moon() -> None:
    """A clip from a camera with no head-worn profile, lined up from the Moon and the skyline in its frame 2 s in,
    whose solver kept a detection from another frame: the spot's limitations end with the sentence that says why
    that detection is kept, and it names the spot as taken to be the Moon (REQ-SKY-3.11's rule, the camera's own
    sentence: the camera may have moved, not the head)."""
    from services.science.catalog import get_catalog_stars
    from services.science.predicted_sky import OTHER_FRAMES_KEPT_CAMERA
    from services.science.solver import _build_unresolved_solution, triangle_solver_identity
    from services.science.wcs_models import SourceDetection

    _, luma = _example()
    other = SourceDetection(900.0, 100.0, 70.0, 10.0, 2.0, 0.2, asset_id="asset-synthetic-horizon", frame_time_sec=0.0)
    solver = _build_unresolved_solution(
        [other], "sol-synthetic-horizon", "asset-synthetic-horizon", SHA, 0.0, MOON_MOMENT, LAT, LON,
        "Too few stars for a plate solve.", "Pixel position only: no verified WCS places it on the sky.",
        triangle_solver_identity(get_catalog_stars()),
    )
    out = complete_sky(solver, _asset(kind="video"), frames=lambda: [(2.0, luma)])
    assert out.status == "approximate" and out.pose_estimate.method == "moon_horizon", out.warnings
    (spot,) = [f for f in out.findings if f.id == "finding-observed-moon"]
    assert spot.limitations[-1] == OTHER_FRAMES_KEPT_CAMERA
    assert "this frame's bright spot, taken to be the Moon," in OTHER_FRAMES_KEPT_CAMERA
    assert "Moon pixel" not in OTHER_FRAMES_KEPT_CAMERA


def test_a_head_worn_camera_whose_own_pose_is_refused_is_lined_up_from_the_skyline(monkeypatch: pytest.MonkeyPatch) -> None:
    camera, luma = _example()
    monkeypatch.setattr(pe, "fit_level_pose", lambda *args, **kwargs: Refusal("fit", "refused for the test"))
    from services.science.camera_profiles import META_RAY_BAN_DISPLAY_MODEL

    calls: list[int] = []

    def frames() -> list[tuple[float, np.ndarray]]:
        calls.append(1)
        return [(0.0, luma)]

    out = complete_sky(_solver_solution(), _asset(model=META_RAY_BAN_DISPLAY_MODEL), frames=frames)
    assert out.status == "approximate" and out.pose_estimate.method == "moon_horizon", out.warnings
    # Both poses searched the same frames, read once
    assert calls == [1]


def test_by_day_or_with_the_moon_down_a_photos_frames_are_not_read() -> None:
    """The horizon pose's Moon and Sun are checked at the solution's moment before any frame is read, as the
    head-worn pose's are; each frame is checked again at its own moment."""
    _, luma = _example()
    calls: list[int] = []

    def frames() -> list[tuple[float, np.ndarray]]:
        calls.append(1)
        return [(0.0, luma)]

    lat, lon = _daytime_place()
    day = complete_sky(_solver_solution(), {**_asset(), "observerLocation": {"latDeg": lat, "lonDeg": lon}},
                       frames=frames)
    assert day.status == "predicted_only" and day.warnings[-2] == pe.DAYTIME
    down = complete_sky(_solver_solution(), {**_asset(), "capturedAtUTC": _moon_down_moment()}, frames=frames)
    assert down.status == "predicted_only" and down.warnings[-2] == pe.MOON_NOT_UP
    assert calls == []


def test_a_mirrored_frame_from_a_camera_without_a_head_worn_profile_is_refused_before_its_frames_are_read() -> None:
    _, luma = _example()
    calls: list[int] = []

    def frames() -> list[tuple[float, np.ndarray]]:
        calls.append(1)
        return [(0.0, luma)]

    asset = {**_asset(), "displayOrientation": _orientation(0, mirrored=True)}
    out = complete_sky(_solver_solution(), asset, frames=frames)
    assert out.status == "predicted_only" and out.warnings[-2] == pe.MIRRORED, out.warnings
    assert calls == []


def test_a_clips_frames_not_confirmed_are_counted_as_a_clips_and_a_photos_named_as_a_photo() -> None:
    """Through complete_sky, a camera without a head-worn profile: the frames of a clip whose sky above the skyline
    holds no Moon are counted ("in the frame searched"); the same frame from a photo is "in the photo"."""
    _, sky_only = _example(moon=False)
    for kind, where in (("video", "in the frame searched"), ("image", "in the photo")):
        out = complete_sky(_solver_solution(), _asset(kind=kind), frames=lambda: [(0.0, sky_only)])
        assert out.status == "predicted_only" and out.warnings[-2] == pe.not_confirmed_above_skyline_reason(where), (
            kind, out.warnings)


def test_without_a_skyline_a_photo_says_why_and_keeps_the_predicted_sky() -> None:
    camera, _ = _example()
    sky_only = _draw(camera, MOON_MOMENT, LAT, LON, skyline_alt=-30.0)
    solver = _solver_solution()
    out = complete_sky(solver, _asset(), frames=lambda: [(0.0, sky_only)])
    assert out.status == "predicted_only" and out.wcs is None and out.pose_estimate is None
    assert out.warnings[:-1] == [*solver.warnings, pe.NO_SKYLINE]


def test_the_old_entry_point_returns_the_same_pose_estimate_fields() -> None:
    """REQ-SKY-4.4: clips found on the laptop go through solve_horizon_and_beacon_pose, which now gives the same
    approximate solution with its pose estimate; a clip's frame is named by its time."""
    camera, luma = _example()
    out = solve_horizon_and_beacon_pose(luma, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                        source_sha256=SHA, frame_time_sec=2.5, media_kind="video")
    assert out.status == "approximate" and out.pose_estimate is not None
    assert out.pose_estimate.method == "moon_horizon" and len(out.pose_estimate.swept_wcs) == 9
    assert out.frame_time_sec == 2.5 and out.quality.matched_inlier_count == 0
    assert "in the frame 2.50 s into the clip to be the Moon" in out.warnings[-2]
    out.to_contract_dict()
    # A frame decoded to [0, 1] (discovery's) is read on the 8-bit scale the Moon test counts when its white is
    # stated as 1.0; read as 8-bit codes it has no spot near 200 and no skyline
    normalized = luma / 255.0
    same = solve_horizon_and_beacon_pose(normalized, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                         source_sha256=SHA, full_scale=1.0)
    coded = solve_horizon_and_beacon_pose(luma, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                          source_sha256=SHA).wcs
    assert same.status == "approximate"
    for name in ("crval_ra_deg", "crval_dec_deg", "scale_deg_per_px", "rotation_deg"):
        assert getattr(same.wcs, name) == pytest.approx(getattr(coded, name), abs=1e-6), name
    unstated = solve_horizon_and_beacon_pose(normalized, MOON_MOMENT, LAT, LON,
                                             source_asset_id="asset-synthetic-horizon", source_sha256=SHA)
    assert unstated.status == "predicted_only"
    # Any positive white is a scale, however small
    small = solve_horizon_and_beacon_pose(luma * (0.05 / 255.0), MOON_MOMENT, LAT, LON,
                                          source_asset_id="asset-synthetic-horizon", source_sha256=SHA, full_scale=0.05)
    assert small.status == "approximate"
    for wrong in (0.0, -0.0, -1.0, math.inf, math.nan, True, "255"):
        with pytest.raises(ValueError, match="full_scale"):
            solve_horizon_and_beacon_pose(luma, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                          source_sha256=SHA, full_scale=wrong)
    refused = solve_horizon_and_beacon_pose(np.full((HEIGHT, WIDTH), 2.0), MOON_MOMENT, LAT, LON,
                                            source_asset_id="asset-synthetic-horizon", source_sha256=SHA)
    assert refused.status == "predicted_only" and refused.wcs is None and refused.pose_estimate is None
    assert refused.warnings[0] == pe.NO_SKYLINE
    # No spot, no candidate; the solution's first revision
    assert refused.quality.detected_candidate_count == 0 and refused.revision == 1 and out.revision == 1
    # A spot whose fit is refused (a lamp 12 times the Moon's width) counts one candidate, and its reason names the
    # frame by its time into a clip, or the photo
    _, lamp = _example(bloom=12.0)
    for kind, where in (("video", "in the frame 2.50 s into the clip"), ("image", "in the photo")):
        sized = solve_horizon_and_beacon_pose(lamp, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                              source_sha256=SHA, frame_time_sec=2.5, media_kind=kind)
        assert sized.status == "predicted_only" and sized.quality.detected_candidate_count == 1, sized.warnings
        assert where in sized.warnings[0] and "times the Moon's predicted diameter" in sized.warnings[0], sized.warnings
    # The frame is read on the 8-bit scale unless told otherwise: a spot at 210 of 255 is lined up, one at 190 is not
    for peak, status in ((210.0, "approximate"), (190.0, "predicted_only")):
        _, frame = _example(peak=peak)
        read = solve_horizon_and_beacon_pose(frame, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                             source_sha256=SHA)
        assert read.status == status, (peak, read.warnings)


def test_the_old_entry_points_predicted_findings_are_the_predicted_skys_own() -> None:
    """Characterization: the horizon solver's predicted findings, by id, with their positions and uncertainties, are
    those of predicted_sky_findings at the same moment and place, for a frame with a pose and one without."""
    camera, posed = _example()
    for luma in (posed, np.full((HEIGHT, WIDTH), 2.0)):
        out = solve_horizon_and_beacon_pose(luma, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                            source_sha256=SHA)
        own = {f.id: f for f in out.findings if f.evidence_class == "predicted"}
        expected = {f.id: f for f in predicted_sky_findings(MOON_MOMENT, LAT, LON, solution_id=out.id, source_sha256=SHA)}
        assert set(own) == set(expected)
        for key, finding in own.items():
            other = expected[key]
            for name in ("ra_deg", "dec_deg", "alt_deg", "az_deg", "uncertainty_arcsec"):
                assert getattr(finding, name) == pytest.approx(getattr(other, name), abs=1e-9), (key, name)


def test_the_horizon_solvers_predicted_findings_list_what_lies_inside_the_frame_first() -> None:
    """The horizon solver's own order, kept for its callers: the predicted findings inside the frame first, then the
    rest above the horizon, each part in the predicted sky's own order."""
    from services.science.predicted_sky import OUTSIDE_THE_FRAME

    _, posed = _example()
    out = solve_horizon_and_beacon_pose(posed, MOON_MOMENT, LAT, LON, source_asset_id="asset-synthetic-horizon",
                                        source_sha256=SHA)
    predicted = [f for f in out.findings if f.evidence_class == "predicted"]
    outside = [f.subtitle.endswith(OUTSIDE_THE_FRAME) for f in predicted]
    assert 0 < outside.count(False) and 0 < outside.count(True) and outside == sorted(outside), outside
    order = [f.id for f in predicted_sky_findings(MOON_MOMENT, LAT, LON, solution_id=out.id, source_sha256=SHA)]
    for part in (False, True):
        ids = [f.id for f, out_ in zip(predicted, outside) if out_ is part]
        assert ids == [i for i in order if i in ids], part


def test_an_uploaded_photo_of_the_moon_over_a_skyline_is_lined_up_through_the_job(tmp_path: Any) -> None:
    """The real upload path: a PNG with an EXIF capture time and place at the fixtures' moment and open-ocean place
    and no camera model, whose stars do not solve, ends approximate from the Moon and the skyline, with no failure
    warning; the stages are the solver's own (the pose adds none)."""
    import io

    from fastapi.testclient import TestClient
    from PIL import Image

    from services.api import app
    from services.ingest import router as ingest_router
    from services.ingest.store import IngestStore

    _, luma = _example()
    exif = Image.Exif()
    sub = exif.get_ifd(0x8769)
    sub[0x9003], sub[0x9011] = "2031:08:30 03:01:00", "+00:00"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (30.0, 0.0, 0.0), "W", (40.0, 0.0, 0.0)
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(luma), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG", exif=exif.tobytes(), compress_level=1
    )
    body = buf.getvalue()
    store = IngestStore(tmp_path / "store", max_bytes=20_000_000)
    app.dependency_overrides[ingest_router.get_ingest_store] = lambda: store
    try:
        client = TestClient(app)
        resp = client.post("/api/ingest", content=body,
                           headers={"content-type": "image/png", "x-albedian-filename": "synthetic-horizon.png"})
        assert resp.status_code == 201, resp.text
        sha = hashlib.sha256(body).hexdigest()
        job = _solved(client, sha)[1]
        assert job["state"] == "succeeded", job
        assert [stage["stage"] for stage in job["stages"]][:4] == ["store", "decode", "detect", "solve"]
        solution = client.get(f"/api/ingest/{sha}/solution").json()
    finally:
        app.dependency_overrides.pop(ingest_router.get_ingest_store, None)
    assert solution["status"] == "approximate", solution["warnings"]
    assert solution["poseEstimate"]["method"] == "moon_horizon" and solution["frameTimeSec"] == 0.0
    assert solution["quality"]["matchedInlierCount"] == 0
    assert not any("could not be computed" in w for w in solution["warnings"])


# --- Edges, from one table ------------------------------------------------------------------------------------------
# Every bound sky-4 defines, sampled at each inside end and one step outside: the bound is set to the quantity a fixed
# scene measures (inside, it holds) and moved one float past it (outside, it refuses). math.nextafter is the step, so
# an inclusive bound made exclusive, or one moved by any amount, fails here.


def _measured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the example scene measures against each bound: the skyline's rise in noise units, the sky rows searched,
    and per fit its field of view, the spot's size ratio and its largest residual."""
    _, luma = _example()
    fits: list[tuple[float, float, float]] = []
    real = pe.fit_horizon_pose

    def recording(moon: Any, blob: Any, **kwargs: Any) -> WCSModel | Refusal:
        wcs = real(moon, blob, **kwargs)
        if isinstance(wcs, WCSModel):
            ratio = blob.equivalent_diameter_px / (2.0 * moon.radius_deg / wcs.scale_deg_per_px)
            mx, my = wcs.world_to_pixel(moon.ra_deg, moon.dec_deg)
            fits.append((pe.fov_diagonal_deg(wcs.scale_deg_per_px, WIDTH, HEIGHT), ratio, math.hypot(mx - blob.x_px, my - blob.y_px)))
        return wcs

    monkeypatch.setattr(pe, "fit_horizon_pose", recording)
    pose = _pose(luma)
    monkeypatch.setattr(pe, "fit_horizon_pose", real)
    assert isinstance(pose, PoseResult) and len(fits) == 9
    skyline = pose.skyline
    return {
        "luma": luma,
        "snr": skyline.step_snr,
        "top": int(math.floor(skyline.row - skyline.half_width_px)),
        "fov": (min(f[0] for f in fits), max(f[0] for f in fits)),
        "ratio": (min(f[1] for f in fits), max(f[1] for f in fits)),
    }


def _up(x: float) -> float:
    return math.nextafter(x, math.inf)


def _down(x: float) -> float:
    return math.nextafter(x, -math.inf)


# (bound, the module value it sets from what the scene measures, inside or outside, the refusal kind outside)
EDGES: list[tuple[str, Any, Any, str]] = [
    ("skyline rise at least 10 noise units", lambda m: ("SKYLINE_MIN_STEP_SNR", m["snr"]),
     lambda m: ("SKYLINE_MIN_STEP_SNR", _up(m["snr"])), "no_skyline"),
    ("sky searched at least 16 rows", lambda m: ("MIN_SKY_ROWS", m["top"]), lambda m: ("MIN_SKY_ROWS", m["top"] + 1),
     "not_moon"),
    ("field of view at least 20 degrees", lambda m: ("HORIZON_FOV_DIAGONAL_RANGE_DEG", (m["fov"][0], 130.0)),
     lambda m: ("HORIZON_FOV_DIAGONAL_RANGE_DEG", (_up(m["fov"][0]), 130.0)), "fov"),
    ("field of view at most 130 degrees", lambda m: ("HORIZON_FOV_DIAGONAL_RANGE_DEG", (20.0, m["fov"][1])),
     lambda m: ("HORIZON_FOV_DIAGONAL_RANGE_DEG", (20.0, _down(m["fov"][1]))), "fov"),
    ("spot at least 0.8 times the Moon", lambda m: ("MIN_DIAMETER_FACTOR", m["ratio"][0]),
     lambda m: ("MIN_DIAMETER_FACTOR", _up(m["ratio"][0])), "size"),
    ("spot at most 8 times the Moon", lambda m: ("MAX_DIAMETER_FACTOR", m["ratio"][1]),
     lambda m: ("MAX_DIAMETER_FACTOR", _down(m["ratio"][1])), "size"),
]


@pytest.mark.parametrize("side", ["inside", "outside"])
@pytest.mark.parametrize("bound, inside, outside, kind", EDGES, ids=[edge[0] for edge in EDGES])
def test_each_bound_holds_at_its_inside_end_and_refuses_one_step_outside(
    monkeypatch: pytest.MonkeyPatch, bound: str, inside: Any, outside: Any, kind: str, side: str
) -> None:
    measured = _measured(monkeypatch)
    name, value = (inside if side == "inside" else outside)(measured)
    monkeypatch.setattr(pe, name, value)
    result = _pose(measured["luma"])
    if side == "inside":
        assert isinstance(result, PoseResult), (bound, result)
    else:
        assert isinstance(result, Refusal) and result.kind == kind, (bound, result)


def test_the_sky_searched_above_the_skyline_is_at_least_16_rows() -> None:
    """Small frames whose skyline less its half width leaves 15 and 16 rows of sky: with 15 the pose refuses before
    any Moon test, saying what it looked for; with 16 the Moon test runs on them and refuses for its own cause (a sky
    of noise has no spot near 200)."""
    assert pe.MIN_SKY_ROWS == 16
    tops: dict[int, np.ndarray] = {}
    for row in range(26, 52):
        luma = _step(row, height=72, width=60)
        skyline = pe.find_skyline(luma)
        if skyline is not None:
            tops.setdefault(int(math.floor(skyline.row - skyline.half_width_px)), luma)
    assert {15, 16} <= set(tops), sorted(tops)
    for top, before_the_moon_test in ((15, True), (16, False)):
        result = _pose(tops[top])
        assert isinstance(result, Refusal) and result.kind == "not_moon", (top, result)
        assert (result.reason == pe.not_confirmed_above_skyline_reason("in the frame")) is before_the_moon_test, (
            top, result.reason)


def test_the_skyline_band_takes_its_first_row_and_not_its_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """The band's ends, with the smoothing made sharp so the rise sits on the step's two rows (a central difference
    spreads a step over the row before it and its own): a band that starts on the first of them finds it, one that
    starts past both does not; one that ends a row after the first finds it, one that ends before it does not (the
    end is exclusive)."""
    monkeypatch.setattr(pe, "SKYLINE_SMOOTHING_ROWS", 0.3)
    luma = _step(400, noise=0.0)
    rise = np.gradient(ndimage.gaussian_filter1d(np.percentile(luma, 85.0, axis=1), 0.3))
    row = int(np.argmax(rise))
    assert row in (399, 400)
    for first, found in ((row, True), (row + 2, False)):
        monkeypatch.setattr(pe, "SKYLINE_BAND", ((first + 0.5) / HEIGHT, 0.72))
        assert (pe.find_skyline(luma) is not None) is found, (first, found)
    for end, found in ((row + 1, True), (row - 1, False)):
        monkeypatch.setattr(pe, "SKYLINE_BAND", (0.38, (end + 0.5) / HEIGHT))
        assert (pe.find_skyline(luma) is not None) is found, (end, found)


PLACE_STEP = 1e-9
PLACE_EDGES = [
    (-90.0, 0.0, False), (90.0, 0.0, False), (0.0, -180.0, False), (0.0, 180.0, False),
    (-90.0 - PLACE_STEP, 0.0, True), (90.0 + PLACE_STEP, 0.0, True),
    (0.0, -180.0 - PLACE_STEP, True), (0.0, 180.0 + PLACE_STEP, True),
    (math.nan, 0.0, True), (math.inf, 0.0, True), (-math.inf, 0.0, True),
    (0.0, math.nan, True), (0.0, math.inf, True), (0.0, -math.inf, True),
]
_DOOR_MOON = pe.topocentric_moon(MOON_MOMENT, LAT, LON)
_DOOR_BLOB = MoonBlob(
    x_px=100.0, y_px=80.0, flux=1000.0, snr=50.0, fwhm_px=6.0, centroid_uncertainty_px=0.1,
    peak_to_total_ratio=0.05, area_px=28, equivalent_diameter_px=6.0, radius_px=5.0,
)
_DOOR_LUMA = np.zeros((64, 48))
HORIZON_DOORS = {
    "pose_estimate.fit_horizon_pose": lambda lat, lon: pe.fit_horizon_pose(
        _DOOR_MOON, _DOOR_BLOB, width=48, height=64, skyline_alt_deg=1.5, skyline_row=40.0, turns=0,
        moment=MOON_MOMENT, lat=lat, lon=lon,
    ),
    "pose_estimate.horizon_moon_pose": lambda lat, lon: pe.horizon_moon_pose(
        _DOOR_LUMA, frame_time_sec=0.0, moment=MOON_MOMENT, lat=lat, lon=lon, orientation={"mirrored": True},
    ),
    "pose_estimate.horizon_pose_from_frames": lambda lat, lon: pe.horizon_pose_from_frames(
        [], lat=lat, lon=lon, orientation=None,
    ),
    "predicted_sky.horizon_pose_solution": lambda lat, lon: horizon_pose_solution(
        _solver_solution(), _DOOR_LUMA, moment=MOON_MOMENT, lat=lat, lon=lon,
    ),
    "horizon_solver.solve_horizon_and_beacon_pose": lambda lat, lon: solve_horizon_and_beacon_pose(
        _DOOR_LUMA, MOON_MOMENT, lat, lon, source_asset_id="synthetic-door", source_sha256=SHA,
    ),
}


@pytest.mark.parametrize("door", sorted(HORIZON_DOORS))
@pytest.mark.parametrize(("lat", "lon", "refused"), PLACE_EDGES)
def test_every_horizon_door_checks_its_place_at_each_edge(door: str, lat: float, lon: float, refused: bool) -> None:
    """Every public horizon-pose function that takes a place checks it at its own door before mirrored, empty-frame or
    start guards, refusing one step outside [-90, 90] or [-180, 180] and non-finite angles with PlaceOutOfRange."""
    call = HORIZON_DOORS[door]
    if refused:
        with pytest.raises(PlaceOutOfRange) as caught:
            call(lat, lon)
        names = [entry.name for entry in caught.traceback]
        assert names[names.index("require_place") - 1] == door.split(".")[1], names
        return
    call(lat, lon)


def _solved(client: TestClient, sha: str) -> tuple[int, dict]:
    """Starts the solve of `sha` as the web does and follows its job route until the job ends (the start no longer
    waits, upload jobs REQ-JOB-7.5): the start's status and the job as it ended."""
    started = client.post(f"/api/ingest/{sha}/solve")
    job = started.json()
    deadline = time.monotonic() + 300.0
    while isinstance(job, dict) and job.get("state") in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.02)
        job = client.get(f"/api/ingest/jobs/{job['id']}").json()
    return started.status_code, job
