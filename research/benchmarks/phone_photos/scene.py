"""The scene of one simulated frame: the sky's light, the Sun and Moon, the ground, and nuisances.

Light is kept as V-band flux relative to a star of magnitude 0: a star of magnitude m has flux
10^(-0.4 m), and a sky of surface brightness mu mag/arcsec^2 has flux 10^(-0.4 mu) per arcsec^2.
The renderer turns that into electrons with each camera's pupil and sensor.

Sources and what is modelled:

- Moon: Meeus (1998) chapter 47, truncated to its 20 largest longitude and distance terms and 14
  largest latitude terms, with the additive terms. Checked against ERFA's eraMoon98 test value and
  Meeus's worked example 47.a to 0.02 degree. Topocentric: the observer's offset from the Earth's
  centre is applied (up to about 1 degree of parallax), on a spherical Earth. Annual aberration is
  applied as for a star, which is wrong for the Moon by up to 20 arcsec, far inside the truncation
  error [inference].
- Sun: Meeus chapter 25, low precision (0.01 degree), used for night selection and lunar phase.
- Moon magnitude and moonlit sky: Krisciunas and Schaefer (1991, PASP 103, 1033), equations 15 to
  21; natural sky brightness with their airmass and extinction law (their equations 2 and 3).
- Artificial light: a parametric glow, uniform over the sky with the same airmass growth, plus
  domes toward towns that fade with altitude [inference: the shape follows the qualitative
  behaviour of Garstang's (1986, PASP 98, 364) model, not its physics].
- Ground, trees, buildings, lamps, clouds, aircraft and screens are procedural, seeded shapes
  [inference]; they exist to block, light and confuse, not to look like any real place.

Not modelled: the diffuse Milky Way and integrated light of stars fainter than the catalog's G 12,
zodiacal light, airglow structure, twilight (frames are dark-sky only: the Sun below -18 degrees).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from research.benchmarks.phone_photos import astrometry as am

AU_KM = 149597870.7
EARTH_RADIUS_KM = 6378.137
MOON_RADIUS_KM = 1737.4
NANOLAMBERT_PER_UNIT_FLUX = 34.08 * math.exp(20.7233)  # KS91 eq. 1: B(nL) for mu = 0 mag/arcsec^2

# =============================================================================================
# Moon and Sun
# =============================================================================================

# Meeus table 47.A, the 20 largest terms: multiples of D, M, M', F; longitude (1e-6 deg); distance (1e-3 km)
_MOON_LON_DIST = (
    (0, 0, 1, 0, 6288774, -20905355),
    (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888),
    (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158),
    (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620),
    (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755),
    (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782),
    (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636),
    (2, 1, -1, 0, -7888, 24208),
)
# Meeus table 47.B, the 14 largest terms: multiples of D, M, M', F; latitude (1e-6 deg)
_MOON_LAT = (
    (0, 0, 0, 1, 5128122),
    (0, 0, 1, 1, 280602),
    (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413),
    (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573),
    (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822),
    (2, -1, 0, -1, 8216),
    (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
    (2, 1, 0, -1, -3359),
)


def moon_ecliptic_of_date(jd_tt: float) -> tuple[float, float, float]:
    """Geocentric ecliptic longitude and latitude (degrees, mean equinox of date) and distance (km)."""
    t = (jd_tt - am.JD_J2000) / am.DAYS_PER_JULIAN_CENTURY
    lp = 218.3164477 + 481267.88123421 * t - 0.0015786 * t**2 + t**3 / 538841.0 - t**4 / 65194000.0
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t**2 + t**3 / 545868.0 - t**4 / 113065000.0
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t**2 + t**3 / 24490000.0
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t**2 + t**3 / 69699.0 - t**4 / 14712000.0
    f = 93.2720950 + 483202.0175233 * t - 0.0036539 * t**2 - t**3 / 3526000.0 + t**4 / 863310000.0
    a1 = 119.75 + 131.849 * t
    a2 = 53.09 + 479264.290 * t
    a3 = 313.45 + 481266.484 * t
    ecc = 1.0 - 0.002516 * t - 0.0000074 * t**2

    def argument(kd: int, km: int, kmp: int, kf: int) -> float:
        return math.radians(kd * d + km * m + kmp * mp + kf * f)

    sum_l = sum_r = sum_b = 0.0
    for kd, km, kmp, kf, sl, sr in _MOON_LON_DIST:
        scale = ecc ** abs(km)
        arg = argument(kd, km, kmp, kf)
        sum_l += sl * scale * math.sin(arg)
        sum_r += sr * scale * math.cos(arg)
    for kd, km, kmp, kf, sb in _MOON_LAT:
        sum_b += sb * ecc ** abs(km) * math.sin(argument(kd, km, kmp, kf))
    r = math.radians
    sum_l += 3958.0 * math.sin(r(a1)) + 1962.0 * math.sin(r(lp - f)) + 318.0 * math.sin(r(a2))
    sum_b += (
        -2235.0 * math.sin(r(lp)) + 382.0 * math.sin(r(a3)) + 175.0 * math.sin(r(a1 - f))
        + 175.0 * math.sin(r(a1 + f)) + 127.0 * math.sin(r(lp - mp)) - 115.0 * math.sin(r(lp + mp))
    )
    return (lp + sum_l / 1e6) % 360.0, sum_b / 1e6, 385000.56 + sum_r / 1000.0


def _ecliptic_of_date_to_icrs(lon_deg: float, lat_deg: float, jd_tt: float) -> np.ndarray:
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    ecl = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    eps = am.mean_obliquity_iau1980(jd_tt)
    ce, se = math.cos(eps), math.sin(eps)
    equatorial_of_date = np.array([ecl[0], ce * ecl[1] - se * ecl[2], se * ecl[1] + ce * ecl[2]])
    return am.precession_matrix_iau1976(jd_tt).T @ equatorial_of_date


def moon_geocentric_icrs_km(jd_tt: float) -> np.ndarray:
    """Geocentric position of the Moon in ICRS (GCRS) axes, km."""
    lon, lat, distance = moon_ecliptic_of_date(jd_tt)
    return _ecliptic_of_date_to_icrs(lon, lat, jd_tt) * distance


def sun_geocentric_icrs_km(jd_tt: float) -> np.ndarray:
    """Geocentric position of the Sun in ICRS axes, km (Meeus chapter 25, low precision)."""
    t = (jd_tt - am.JD_J2000) / am.DAYS_PER_JULIAN_CENTURY
    mean_lon = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    anomaly = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    a = math.radians(anomaly)
    centre = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(a)
        + (0.019993 - 0.000101 * t) * math.sin(2 * a)
        + 0.000289 * math.sin(3 * a)
    )
    ecc = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t
    distance_au = 1.000001018 * (1.0 - ecc * ecc) / (1.0 + ecc * math.cos(math.radians(anomaly + centre)))
    return _ecliptic_of_date_to_icrs(mean_lon + centre, 0.0, jd_tt) * distance_au * AU_KM


def _observed_enu(frame: am.ObservationFrame, geocentric_icrs_km: np.ndarray) -> tuple[np.ndarray, float]:
    """Observed (refracted, topocentric) direction and distance of a solar-system body."""
    distance = float(np.linalg.norm(geocentric_icrs_km))
    ra, dec = am.vec_to_radec(geocentric_icrs_km / distance)
    enu = frame.icrs_to_enu(ra, dec)[0]
    alt_observed = math.degrees(math.atan2(enu[2], math.hypot(enu[0], enu[1])))
    alt_true = float(am.true_altitude_deg(alt_observed, frame.pressure_hpa, frame.temperature_c)[0])
    horizontal = math.hypot(enu[0], enu[1])
    if horizontal > 0.0:
        geometric = np.array([enu[0] / horizontal * math.cos(math.radians(alt_true)),
                              enu[1] / horizontal * math.cos(math.radians(alt_true)),
                              math.sin(math.radians(alt_true))])
    else:
        geometric = enu.copy()
    topocentric = geometric * distance - np.array([0.0, 0.0, EARTH_RADIUS_KM])
    topo_distance = float(np.linalg.norm(topocentric))
    unit = topocentric / topo_distance
    alt = math.degrees(math.asin(np.clip(unit[2], -1.0, 1.0)))
    alt_app = float(am.apparent_altitude_deg(alt, frame.pressure_hpa, frame.temperature_c)[0])
    horizontal = math.hypot(unit[0], unit[1])
    if horizontal > 0.0:
        unit = np.array([unit[0] / horizontal * math.cos(math.radians(alt_app)),
                         unit[1] / horizontal * math.cos(math.radians(alt_app)),
                         math.sin(math.radians(alt_app))])
    return unit, topo_distance


@dataclass(frozen=True)
class Body:
    """A solar-system body as the observer sees it."""

    enu: np.ndarray
    distance_km: float
    alt_deg: float
    az_deg: float


def body_from_icrs(frame: am.ObservationFrame, geocentric_icrs_km: np.ndarray) -> Body:
    enu, distance = _observed_enu(frame, geocentric_icrs_km)
    return Body(
        enu=enu,
        distance_km=distance,
        alt_deg=math.degrees(math.asin(float(np.clip(enu[2], -1.0, 1.0)))),
        az_deg=math.degrees(math.atan2(enu[0], enu[1])) % 360.0,
    )


@dataclass(frozen=True)
class MoonState:
    body: Body
    phase_angle_deg: float
    illuminated_fraction: float
    v_mag: float
    angular_radius_deg: float
    sun_enu: np.ndarray


def moon_state(frame: am.ObservationFrame) -> MoonState:
    """Where the Moon stands for the observer, how much of it is lit, and how bright it is."""
    moon_geo = moon_geocentric_icrs_km(frame.jd_tt)
    sun_geo = sun_geocentric_icrs_km(frame.jd_tt)
    body = body_from_icrs(frame, moon_geo)
    elongation = math.acos(float(np.clip(moon_geo @ sun_geo / (np.linalg.norm(moon_geo) * np.linalg.norm(sun_geo)), -1.0, 1.0)))
    sun_distance = float(np.linalg.norm(sun_geo))
    phase = math.degrees(math.atan2(sun_distance * math.sin(elongation), np.linalg.norm(moon_geo) - sun_distance * math.cos(elongation)))
    v_mag = -12.73 + 0.026 * abs(phase) + 4e-9 * phase**4  # KS91 eq. 20
    return MoonState(
        body=body,
        phase_angle_deg=phase,
        illuminated_fraction=(1.0 + math.cos(math.radians(phase))) / 2.0,
        v_mag=v_mag,
        angular_radius_deg=math.degrees(math.asin(MOON_RADIUS_KM / body.distance_km)),
        sun_enu=body_from_icrs(frame, sun_geo).enu,
    )


def sun_altitude_deg(frame: am.ObservationFrame) -> float:
    return body_from_icrs(frame, sun_geocentric_icrs_km(frame.jd_tt)).alt_deg


# =============================================================================================
# Sky glow
# =============================================================================================


def ks_airmass(alt_deg: np.ndarray) -> np.ndarray:
    """KS91 eq. 3, X = (1 - 0.96 sin^2 z)^(-1/2), with the altitude held at 0 below the horizon."""
    z = np.radians(90.0 - np.clip(alt_deg, 0.0, 90.0))
    return 1.0 / np.sqrt(1.0 - 0.96 * np.sin(z) ** 2)


@dataclass(frozen=True)
class SkyGlow:
    """Brightness of the sky itself, natural and artificial."""

    natural_zenith_mag: float  # V mag/arcsec^2 of the natural sky at the zenith
    extinction: float  # V magnitudes per airmass
    artificial_zenith_mag: float | None  # V mag/arcsec^2 of artificial light at the zenith, None for none
    domes: tuple[tuple[float, float, float, float], ...] = ()  # (azimuth, half-width deg, boost factor, scale height deg)
    artificial_temperature_k: float = 2700.0

    def natural_flux(self, alt_deg: np.ndarray) -> np.ndarray:
        x = ks_airmass(alt_deg)
        return 10.0 ** (-0.4 * self.natural_zenith_mag) * 10.0 ** (-0.4 * self.extinction * (x - 1.0)) * x

    def artificial_flux(self, alt_deg: np.ndarray, az_deg: np.ndarray) -> np.ndarray:
        if self.artificial_zenith_mag is None:
            return np.zeros(np.shape(alt_deg))
        zenith = 10.0 ** (-0.4 * self.artificial_zenith_mag)
        flux = zenith * ks_airmass(alt_deg)
        alt = np.clip(alt_deg, 0.0, 90.0)
        for azimuth, half_width, boost, height in self.domes:
            delta = (np.asarray(az_deg) - azimuth + 180.0) % 360.0 - 180.0
            flux = flux + zenith * boost * np.exp(-((delta / half_width) ** 2)) * np.exp(-alt / height)
        return flux

    def moon_flux(self, alt_deg: np.ndarray, separation_deg: np.ndarray, moon: MoonState | None) -> np.ndarray:
        """Moonlight scattered by the atmosphere, KS91 eq. 15 to 21, as flux per arcsec^2."""
        if moon is None or moon.body.alt_deg <= 0.0:
            return np.zeros(np.shape(alt_deg))
        rho = np.clip(separation_deg, 0.5, 180.0)
        scatter = 10.0**5.36 * (1.06 + np.cos(np.radians(rho)) ** 2) + np.where(
            rho >= 10.0, 10.0 ** (6.15 - rho / 40.0), 6.2e7 / rho**2
        )
        alpha = abs(moon.phase_angle_deg)
        illuminance = 10.0 ** (-0.4 * (3.84 + 0.026 * alpha + 4e-9 * alpha**4))
        x_moon = float(ks_airmass(np.array(moon.body.alt_deg)))
        nanolamberts = scatter * illuminance * 10.0 ** (-0.4 * self.extinction * x_moon) * (
            1.0 - 10.0 ** (-0.4 * self.extinction * ks_airmass(alt_deg))
        )
        return nanolamberts / NANOLAMBERT_PER_UNIT_FLUX


# =============================================================================================
# Ground and its lights
# =============================================================================================


@dataclass(frozen=True)
class PointLight:
    az_deg: float
    alt_deg: float
    v_mag: float
    temperature_k: float


@dataclass(frozen=True)
class Skyline:
    """What blocks the sky: terrain, trees or buildings, as shapes in azimuth and altitude."""

    kind: str  # "open", "hills", "trees" or "buildings"
    seed: int
    base_alt_deg: float = 0.0
    amplitude_deg: float = 0.0

    def _rng(self, salt: int) -> np.random.Generator:
        return np.random.default_rng([self.seed, salt])

    def terrain_alt(self, az_deg: np.ndarray) -> np.ndarray:
        if self.kind == "open" or self.amplitude_deg == 0.0:
            return np.full(np.shape(az_deg), self.base_alt_deg)
        rng = self._rng(1)
        az = np.radians(np.asarray(az_deg, dtype=np.float64))
        profile = np.zeros(az.shape)
        for k in range(1, 13):
            profile += rng.normal(0.0, 1.0) / k**1.5 * np.sin(k * az + rng.uniform(0.0, 2 * math.pi))
        return self.base_alt_deg + self.amplitude_deg * (profile / 2.0 + 0.5)

    def _objects(self) -> tuple[list[tuple[float, float, float, float]], list[PointLight]]:
        """Trees as (azimuth, crown altitude, half-width, crown half-height) and building lights."""
        rng = self._rng(2)
        trees: list[tuple[float, float, float, float]] = []
        lights: list[PointLight] = []
        if self.kind == "trees":
            for _ in range(int(rng.integers(4, 12))):
                az = float(rng.uniform(0.0, 360.0))
                trees.append((az, float(rng.uniform(4.0, 25.0)), float(rng.uniform(1.5, 6.0)), float(rng.uniform(2.0, 7.0))))
        if self.kind == "buildings":
            for _ in range(int(rng.integers(3, 8))):
                az0 = float(rng.uniform(0.0, 360.0))
                width = float(rng.uniform(3.0, 15.0))
                top = float(self.base_alt_deg + rng.uniform(2.0, 14.0))
                trees.append((az0, top, -width, 0.0))  # negative half-width marks a block, not a crown
                for _ in range(int(rng.integers(0, 12))):
                    lights.append(PointLight(az0 + float(rng.uniform(0.1, 0.9)) * width, float(rng.uniform(self.base_alt_deg + 0.3, top - 0.3)),
                                             float(rng.uniform(1.0, 5.0)), float(rng.choice([2700.0, 4000.0, 6500.0]))))
            for _ in range(int(rng.integers(0, 4))):
                lights.append(PointLight(float(rng.uniform(0.0, 360.0)), float(self.base_alt_deg + rng.uniform(0.2, 2.0)),
                                         float(rng.uniform(-2.0, 2.0)), float(rng.choice([2000.0, 3000.0, 4000.0]))))
        return trees, lights

    def ground(self, az_deg: np.ndarray, alt_deg: np.ndarray) -> np.ndarray:
        """True where the ground or something on it hides the sky."""
        az = np.asarray(az_deg, dtype=np.float64)
        alt = np.asarray(alt_deg, dtype=np.float64)
        hidden = alt < self.terrain_alt(az)
        objects, _ = self._objects()
        for centre_az, top, half_width, half_height in objects:
            delta = (az - centre_az + 180.0) % 360.0 - 180.0
            if half_width < 0.0:  # a building block from the ground up to `top`
                hidden |= (delta >= 0.0) & (delta <= -half_width) & (alt <= top)
            else:  # a tree crown on a trunk
                scale = np.maximum(np.cos(np.radians(np.clip(alt, -89.0, 89.0))), 0.05)
                crown = ((delta * scale / half_width) ** 2 + ((alt - top) / half_height) ** 2) <= 1.0
                trunk = (np.abs(delta * scale) <= 0.08 * half_width + 0.15) & (alt <= top)
                hidden |= crown | trunk
        return hidden

    def lights(self) -> list[PointLight]:
        return self._objects()[1]


# =============================================================================================
# Image-space nuisances
# =============================================================================================


def fractal_noise(shape: tuple[int, int], seed: int, *, smoothness: float = 3.2) -> np.ndarray:
    """Seeded fractal (power-law spectrum) noise in [0, 1] over an image."""
    rng = np.random.default_rng([seed, 7])
    h, w = shape
    white = rng.normal(size=(h, w))
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    k = np.hypot(fx, fy)
    k[0, 0] = 1.0
    field = np.fft.irfft2(np.fft.rfft2(white) / k ** (smoothness / 2.0), s=(h, w))
    field -= field.min()
    return field / max(float(field.max()), 1e-12)


def cloud_cover(shape: tuple[int, int], seed: int, fraction: float) -> np.ndarray:
    """Optical depth map: zero in clear sky, rising inside clouds covering about `fraction` of the frame."""
    if fraction <= 0.0:
        return np.zeros(shape)
    noise = fractal_noise(shape, seed)
    if fraction >= 1.0:
        return 3.0 + 2.0 * noise
    threshold = float(np.quantile(noise, 1.0 - fraction))
    return np.clip((noise - threshold) / max(1.0 - threshold, 1e-6), 0.0, 1.0) * 4.0


@dataclass(frozen=True)
class MovingLight:
    """A light crossing the frame during the exposure, in pixels; flux is V-band relative to mag 0."""

    x0: float
    y0: float
    x1: float
    y1: float
    v_mag: float
    temperature_k: float
    flash_hz: float  # 0 for a steady light
    duty: float


def aircraft_lights(seed: int, width: int, height: int) -> list[MovingLight]:
    """One aircraft: two steady navigation lights, a white strobe and a red beacon on one track."""
    rng = np.random.default_rng([seed, 11])
    x0, y0 = float(rng.uniform(0, width)), float(rng.uniform(0, height))
    angle = float(rng.uniform(0.0, 2 * math.pi))
    length = float(rng.uniform(0.3, 1.2)) * math.hypot(width, height)
    x1, y1 = x0 + length * math.cos(angle), y0 + length * math.sin(angle)
    offset = float(rng.uniform(1.0, 3.0))
    return [
        MovingLight(x0, y0, x1, y1, float(rng.uniform(1.0, 3.0)), 1800.0, 0.0, 1.0),
        MovingLight(x0 + offset, y0, x1 + offset, y1, float(rng.uniform(1.0, 3.0)), 6000.0, 0.0, 1.0),
        MovingLight(x0, y0 + offset, x1, y1 + offset, float(rng.uniform(-3.0, -1.0)), 7000.0, float(rng.uniform(0.8, 1.5)), 0.08),
        MovingLight(x0, y0, x1, y1, float(rng.uniform(-1.0, 1.0)), 1800.0, float(rng.uniform(0.7, 1.2)), 0.15),
    ]


def random_dot_field(seed: int, width: int, height: int, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Star-like points at random places with a catalog-like brightness spread: no real sky behind them."""
    rng = np.random.default_rng([seed, 13])
    x = rng.uniform(4.0, width - 5.0, count)
    y = rng.uniform(4.0, height - 5.0, count)
    # Cumulative counts grow about 10^(0.45 m) for bright stars [inference]; draw magnitudes accordingly
    u = rng.uniform(0.0, 1.0, count)
    v_mag = 1.0 + np.log10(1.0 + u * (10.0 ** (0.45 * 8.0) - 1.0)) / 0.45
    return x, y, v_mag


def screen_radiance(shape: tuple[int, int], seed: int, *, kind: str) -> np.ndarray:
    """A photo of a screen in a dark room, as V-band flux per pixel (before the camera's pupil).

    `kind` is "dots" (a starfield screensaver of random points) or "interface" (dark interface
    with rows of text-like marks and a few icons). The panel is seen at an angle, with its pixel
    grid as a fine periodic pattern the camera can alias.
    """
    rng = np.random.default_rng([seed, 17])
    h, w = shape
    corners = np.array([[0.15, 0.2], [0.85, 0.15], [0.9, 0.85], [0.1, 0.8]]) + rng.uniform(-0.06, 0.06, (4, 2))
    corners *= np.array([w, h])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    # Bilinear inverse of the quad by Newton iteration gives each pixel's panel coordinates (u, v)
    u = np.full(xx.shape, 0.5)
    v = np.full(xx.shape, 0.5)
    p00, p10, p11, p01 = corners
    for _ in range(12):
        px = (1 - u) * (1 - v) * p00[0] + u * (1 - v) * p10[0] + u * v * p11[0] + (1 - u) * v * p01[0]
        py = (1 - u) * (1 - v) * p00[1] + u * (1 - v) * p10[1] + u * v * p11[1] + (1 - u) * v * p01[1]
        dxu = -(1 - v) * p00[0] + (1 - v) * p10[0] + v * p11[0] - v * p01[0]
        dxv = -(1 - u) * p00[0] - u * p10[0] + u * p11[0] + (1 - u) * p01[0]
        dyu = -(1 - v) * p00[1] + (1 - v) * p10[1] + v * p11[1] - v * p01[1]
        dyv = -(1 - u) * p00[1] - u * p10[1] + u * p11[1] + (1 - u) * p01[1]
        det = dxu * dyv - dxv * dyu
        ex, ey = px - xx, py - yy
        u = u - (dyv * ex - dxv * ey) / det
        v = v - (-dyu * ex + dxu * ey) / det
    on_panel = (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)
    base = 10.0 ** (-0.4 * rng.uniform(8.0, 10.0))  # a dim backlit panel, per pixel
    radiance = np.where(on_panel, base, 0.0)
    if kind == "dots":
        n = int(rng.integers(150, 600))
        cu, cv = rng.uniform(0.02, 0.98, n), rng.uniform(0.02, 0.98, n)
        mags = rng.uniform(0.0, 4.0, n)
        size = rng.uniform(0.002, 0.006, n)
        for i in range(n):
            spot = ((u - cu[i]) ** 2 + (v - cv[i]) ** 2) < size[i] ** 2
            radiance = radiance + spot * on_panel * 10.0 ** (-0.4 * (mags[i] + 6.0))
    else:
        rows = int(rng.integers(12, 30))
        for r in range(rows):
            line_v = 0.05 + 0.9 * r / rows
            marks = (np.abs(v - line_v) < 0.006) & (np.sin(u * rng.uniform(150, 260)) > 0.2) & (u > 0.06) & (u < rng.uniform(0.4, 0.95))
            radiance = radiance + marks * on_panel * 10.0 ** (-0.4 * 6.5)
        for _ in range(int(rng.integers(3, 9))):
            iu, iv = rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95)
            icon = (np.abs(u - iu) < 0.012) & (np.abs(v - iv) < 0.016)
            radiance = radiance + icon * on_panel * 10.0 ** (-0.4 * 5.0)
    pitch = rng.uniform(250.0, 450.0)  # panel pixels across the panel
    grid = 0.75 + 0.25 * np.cos(2 * math.pi * u * pitch) * np.cos(2 * math.pi * v * pitch * 0.75)
    return radiance * np.where(on_panel, grid, 1.0)
