"""Low-precision analytical ephemeris for the Moon and the planets, with its error bounds stated.

The planets come from the approximate Keplerian elements of Standish and Williams (1992), as JPL's Solar System
Dynamics group publishes them for 1800 to 2050 ("Approximate Positions of the Planets"), seen from the Earth-Moon
barycentre and without light-time correction. The Moon comes from Meeus (1998) chapter 47 cut to its 6 largest
longitude, 4 latitude and 4 distance terms, seen from the Earth's centre. No JPL Horizons request and no DE440
file is involved; magnitudes are not computed. Every finding is `predicted`: computed from time and place, never
measured in an image.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from services.science.catalog import (
    _iso_to_julian_date,
    icrs_to_altaz,
    mean_obliquity_iau1980_deg,
    precession_matrix_iau1976,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.wcs_models import require_source_sha256

AU_PER_PARSEC = 206264.80624709636
AU_KM = 149597870.7
C_AU_PER_DAY = 173.1446326846693
EARTH_EQUATORIAL_RADIUS_KM = 6378.137
ARCSEC_PER_RAD = 180.0 * 3600.0 / math.pi

PLANET_METHOD = (
    "Standish and Williams (1992) approximate Keplerian elements (JPL Solar System Dynamics, valid 1800 to 2050), "
    "seen from the Earth-Moon barycentre without light-time correction"
)
MOON_METHOD = "Meeus (1998) chapter 47, its 6 largest longitude, 4 latitude and 4 distance terms, geocentric"
# Nominal heliocentric errors of those elements for 1800 to 2050, as published with them: longitude (arcsec),
# latitude (arcsec), distance (1000 km). "Earth" is the Earth-Moon barycentre row.
PUBLISHED_ERRORS_1800_2050: dict[str, tuple[float, float, float]] = {
    "Earth": (20.0, 8.0, 6.0),
    "Venus": (20.0, 1.0, 4.0),
    "Mars": (40.0, 2.0, 25.0),
    "Jupiter": (400.0, 10.0, 600.0),
    "Saturn": (600.0, 25.0, 1500.0),
    "Uranus": (50.0, 2.0, 1000.0),
}
# The Earth's centre sits up to the Moon's greatest distance (406,700 km) over 82.3 (1 + the Earth/Moon mass
# ratio 81.3) from the barycentre the planets are seen from
BARYCENTRE_OFFSET_KM = 5000.0
# The Moon against the same chapter's 20 largest terms (within 0.02 deg of ERFA's Moon): at most 1,216 arcsec
# and 1,129 km, measured over 200,000 instants from 2017 to 2050; tests/integration/test_sky_transforms.py and
# test_ephemeris_labels.py check both bounds
MOON_UNCERTAINTY_ARCSEC = 1300.0
MOON_DISTANCE_UNCERTAINTY_KM = 1200.0


@dataclass(frozen=True)
class OrbitalElementsJ2000:
    """Mean Keplerian elements at J2000.0 and secular rates per Julian century (Standish / JPL)."""

    name: str
    object_type: str
    a0_au: float
    a_dot_au: float
    e0: float
    e_dot: float
    i0_deg: float
    i_dot_deg: float
    l0_deg: float
    l_dot_deg: float
    long_peri0_deg: float
    long_peri_dot_deg: float
    long_node0_deg: float
    long_node_dot_deg: float


PLANET_ELEMENTS: dict[str, OrbitalElementsJ2000] = {
    "Earth": OrbitalElementsJ2000(
        name="Earth",
        object_type="planet",
        a0_au=1.00000261,
        a_dot_au=0.00000562,
        e0=0.01671123,
        e_dot=-0.00004392,
        i0_deg=-0.00001531,
        i_dot_deg=-0.01294668,
        l0_deg=100.46457166,
        l_dot_deg=35999.37244981,
        long_peri0_deg=102.93768193,
        long_peri_dot_deg=0.32327364,
        long_node0_deg=0.0,
        long_node_dot_deg=0.0,
    ),
    "Venus": OrbitalElementsJ2000(
        name="Venus",
        object_type="planet",
        a0_au=0.72333566,
        a_dot_au=0.00000390,
        e0=0.00677672,
        e_dot=-0.00004107,
        i0_deg=3.39467605,
        i_dot_deg=-0.00078890,
        l0_deg=181.97909950,
        l_dot_deg=58517.81538729,
        long_peri0_deg=131.60246718,
        long_peri_dot_deg=0.00268329,
        long_node0_deg=76.67984255,
        long_node_dot_deg=-0.27769418,
    ),
    "Mars": OrbitalElementsJ2000(
        name="Mars",
        object_type="planet",
        a0_au=1.52371034,
        a_dot_au=0.00001847,
        e0=0.09339410,
        e_dot=0.00007882,
        i0_deg=1.84969142,
        i_dot_deg=-0.00813131,
        l0_deg=-4.55343205,
        l_dot_deg=19140.30268499,
        long_peri0_deg=-23.94362959,
        long_peri_dot_deg=0.44441088,
        long_node0_deg=49.55953891,
        long_node_dot_deg=-0.29257343,
    ),
    "Jupiter": OrbitalElementsJ2000(
        name="Jupiter",
        object_type="planet",
        a0_au=5.20288700,
        a_dot_au=-0.00011607,
        e0=0.04838624,
        e_dot=-0.00013253,
        i0_deg=1.30439695,
        i_dot_deg=-0.00183714,
        l0_deg=34.39644051,
        l_dot_deg=3034.74612775,
        long_peri0_deg=14.72847983,
        long_peri_dot_deg=0.21252668,
        long_node0_deg=100.47390909,
        long_node_dot_deg=0.20469106,
    ),
    "Saturn": OrbitalElementsJ2000(
        name="Saturn",
        object_type="planet",
        a0_au=9.53667594,
        a_dot_au=-0.00125060,
        e0=0.05386179,
        e_dot=-0.00050991,
        i0_deg=2.48599187,
        i_dot_deg=0.00193609,
        l0_deg=49.95424423,
        l_dot_deg=1222.49362201,
        long_peri0_deg=92.59887831,
        long_peri_dot_deg=-0.41897216,
        long_node0_deg=113.66242448,
        long_node_dot_deg=-0.28867794,
    ),
    "Uranus": OrbitalElementsJ2000(
        name="Uranus",
        object_type="planet",
        a0_au=19.18916464,
        a_dot_au=-0.00196176,
        e0=0.04725744,
        e_dot=-0.00004397,
        i0_deg=0.77263783,
        i_dot_deg=-0.00242939,
        l0_deg=313.23810451,
        l_dot_deg=428.48202785,
        long_peri0_deg=170.95427630,
        long_peri_dot_deg=0.40805281,
        long_node0_deg=74.01692503,
        long_node_dot_deg=0.04240589,
    ),
}


@dataclass(frozen=True)
class EphemerisFinding:
    """A predicted Solar System body: where the model puts it at the moment, with the model's error bounds.

    `uncertainty_arcsec` and the distance bounds come from the model's published or measured errors (see the
    module constants), computed for this moment; `method` names the model and `limitations` what it leaves out.
    """

    id: str
    observation_solution_id: str
    name: str
    object_type: str
    evidence_class: str
    title: str
    subtitle: str
    ra_deg: float
    dec_deg: float
    alt_deg: float
    az_deg: float
    distance_au: float
    distance_lower_au: float
    distance_upper_au: float
    distance_pc: float
    uncertainty_arcsec: float
    cartesian_parsec_icrs: tuple[float, float, float]
    obs_utc_iso: str
    method: str
    limitations: tuple[str, ...]

    def to_contract_dict(self, *, source_sha256: str | None) -> dict:
        """Format as @albedian/contracts CelestialFinding dictionary. No catalog reference: no catalog
        stands behind a position computed from a model.

        `source_sha256` is the SHA-256 of the recording the prediction was made for, or None when it was made
        for a time and place alone; there is no default, and None leaves the key out rather than invent one.
        """
        evidence_source = {} if source_sha256 is None else {"sourceAssetSha256": require_source_sha256(source_sha256)}
        return {
            "id": self.id,
            "observationSolutionId": self.observation_solution_id,
            "evidenceClass": "predicted",
            "title": self.title,
            "subtitle": self.subtitle,
            "skyPosition": {
                "raDeg": float(self.ra_deg),
                "decDeg": float(self.dec_deg),
                "uncertaintyArcsec": float(self.uncertainty_arcsec),
                "altDeg": float(self.alt_deg),
                "azDeg": float(self.az_deg),
            },
            "inferredDistancePc": {
                "lower": float(self.distance_lower_au / AU_PER_PARSEC),
                "median": float(self.distance_pc),
                "upper": float(self.distance_upper_au / AU_PER_PARSEC),
                "method": "ephemeris_au",
            },
            "cartesianParsecICRS": [
                float(self.cartesian_parsec_icrs[0]),
                float(self.cartesian_parsec_icrs[1]),
                float(self.cartesian_parsec_icrs[2]),
            ],
            "evidence": {
                "id": f"ev-ephem-{self.name.lower()}",
                "evidenceClass": "predicted",
                "verificationMethod": self.method,
                **evidence_source,
                "distanceUncertaintyNote": (
                    f"Geocentric distance {self.distance_au:.6f} AU, between {self.distance_lower_au:.6f} and "
                    f"{self.distance_upper_au:.6f} AU from the model's error bounds"
                ),
                "limitations": list(self.limitations),
                "verifiedByIndependentHoldout": False,
            },
        }


def _solve_kepler_rad(mean_anomaly_rad: float, ecc: float) -> float:
    """Solve Kepler's equation E - e*sin(E) = M using Newton-Raphson."""
    e_anom = mean_anomaly_rad + ecc * math.sin(mean_anomaly_rad)
    for _ in range(10):
        delta = (e_anom - ecc * math.sin(e_anom) - mean_anomaly_rad) / (
            1.0 - ecc * math.cos(e_anom)
        )
        e_anom -= delta
        if abs(delta) < 1e-13:
            break
    return e_anom


def _heliocentric_ecliptic_xyz_au(
    elem: OrbitalElementsJ2000, t_centuries: float
) -> tuple[float, float, float]:
    """Compute heliocentric J2000 ecliptic coordinates (x, y, z) in AU."""
    a = elem.a0_au + elem.a_dot_au * t_centuries
    e = elem.e0 + elem.e_dot * t_centuries
    inc_rad = math.radians(elem.i0_deg + elem.i_dot_deg * t_centuries)
    l_deg = elem.l0_deg + elem.l_dot_deg * t_centuries
    w_bar_deg = elem.long_peri0_deg + elem.long_peri_dot_deg * t_centuries
    node_deg = elem.long_node0_deg + elem.long_node_dot_deg * t_centuries

    arg_peri_rad = math.radians((w_bar_deg - node_deg) % 360.0)
    node_rad = math.radians(node_deg % 360.0)
    m_deg = ((l_deg - w_bar_deg + 180.0) % 360.0) - 180.0
    m_rad = math.radians(m_deg)

    e_anom = _solve_kepler_rad(m_rad, e)
    x_orb = a * (math.cos(e_anom) - e)
    y_orb = a * math.sqrt(max(0.0, 1.0 - e * e)) * math.sin(e_anom)

    cos_w = math.cos(arg_peri_rad)
    sin_w = math.sin(arg_peri_rad)
    cos_n = math.cos(node_rad)
    sin_n = math.sin(node_rad)
    cos_i = math.cos(inc_rad)
    sin_i = math.sin(inc_rad)

    x_ecl = (cos_w * cos_n - sin_w * sin_n * cos_i) * x_orb + (
        -sin_w * cos_n - cos_w * sin_n * cos_i
    ) * y_orb
    y_ecl = (cos_w * sin_n + sin_w * cos_n * cos_i) * x_orb + (
        -sin_w * sin_n + cos_w * cos_n * cos_i
    ) * y_orb
    z_ecl = (sin_w * sin_i) * x_orb + (cos_w * sin_i) * y_orb
    return (x_ecl, y_ecl, z_ecl)


def _ecliptic_to_equatorial_icrs(
    x_ecl: float, y_ecl: float, z_ecl: float
) -> tuple[float, float, float]:
    """Rotate J2000 ecliptic Cartesian coordinates to equatorial ICRS."""
    eps_rad = math.radians(23.43929111)
    cos_e = math.cos(eps_rad)
    sin_e = math.sin(eps_rad)
    x_eq = x_ecl
    y_eq = y_ecl * cos_e - z_ecl * sin_e
    z_eq = y_ecl * sin_e + z_ecl * cos_e
    return (x_eq, y_eq, z_eq)


def _compute_moon_geocentric_icrs(t_centuries: float) -> tuple[float, float, float]:
    """Compute geocentric ICRS (ra_deg, dec_deg, distance_au) for the Moon (Meeus Ch. 47 low-precision).

    Meeus's series give ecliptic coordinates on the mean equinox of date; they are turned to the mean equator
    of date with the obliquity of date and then precessed back to J2000 (ICRS) like every other direction here.
    """
    l_prime = (218.3164477 + 481267.88123421 * t_centuries) % 360.0
    d_elong = (297.8501921 + 445267.1114034 * t_centuries) % 360.0
    m_sun = (357.5291092 + 35999.0502909 * t_centuries) % 360.0
    m_moon = (134.9633964 + 477198.8675055 * t_centuries) % 360.0
    f_arg = (93.2720950 + 483202.0175233 * t_centuries) % 360.0

    d_rad = math.radians(d_elong)
    m_sun_rad = math.radians(m_sun)
    m_moon_rad = math.radians(m_moon)
    f_rad = math.radians(f_arg)

    lon_ecl_deg = (
        l_prime
        + 6.288774 * math.sin(m_moon_rad)
        + 1.274027 * math.sin(2.0 * d_rad - m_moon_rad)
        + 0.658314 * math.sin(2.0 * d_rad)
        + 0.213618 * math.sin(2.0 * m_moon_rad)
        - 0.185116 * math.sin(m_sun_rad)
        - 0.114332 * math.sin(2.0 * f_rad)
    ) % 360.0
    lat_ecl_deg = (
        5.128122 * math.sin(f_rad)
        + 0.280602 * math.sin(m_moon_rad + f_rad)
        + 0.277693 * math.sin(m_moon_rad - f_rad)
        + 0.173237 * math.sin(2.0 * d_rad - f_rad)
    )
    dist_km = (
        385000.56
        - 20905.355 * math.cos(m_moon_rad)
        - 3699.111 * math.cos(2.0 * d_rad - m_moon_rad)
        - 2955.968 * math.cos(2.0 * d_rad)
        - 569.925 * math.cos(2.0 * m_moon_rad)
    )
    dist_au = dist_km / 149597870.7

    lon_rad = math.radians(lon_ecl_deg)
    lat_rad = math.radians(lat_ecl_deg)
    jd = 2451545.0 + 36525.0 * t_centuries
    eps_rad = math.radians(mean_obliquity_iau1980_deg(jd))
    ecl = np.array(
        [math.cos(lat_rad) * math.cos(lon_rad), math.cos(lat_rad) * math.sin(lon_rad), math.sin(lat_rad)]
    )
    equator_of_date = np.array(
        [
            ecl[0],
            math.cos(eps_rad) * ecl[1] - math.sin(eps_rad) * ecl[2],
            math.sin(eps_rad) * ecl[1] + math.cos(eps_rad) * ecl[2],
        ]
    )
    ra_deg, dec_deg = unit_vector_to_ra_dec(precession_matrix_iau1976(jd).T @ equator_of_date)
    return (ra_deg, dec_deg, dist_au)


def moon_parallax_shift_arcsec(distance_au: float, alt_deg: float) -> tuple[float, float]:
    """The Moon's horizontal parallax at `distance_au`, and how far below its geocentric position an observer sees
    it at geocentric altitude `alt_deg` (the parallax times the cosine of the altitude), both in arcsec."""
    parallax_arcsec = math.degrees(math.asin(EARTH_EQUATORIAL_RADIUS_KM / (distance_au * AU_KM))) * 3600.0
    return parallax_arcsec, parallax_arcsec * math.cos(math.radians(alt_deg))


def _planet_error_bound_au(
    body: str, planet_au: np.ndarray, earth_au: np.ndarray, geocentric_au: np.ndarray, t_centuries: float
) -> float:
    """The most the planet's geocentric position can be off, in AU: the published heliocentric errors of the planet
    and of the Earth-Moon barycentre (angular errors as displacements at their distances, plus the distance
    errors), the barycentre's offset from the Earth's centre, and the light time the model leaves out."""
    lon_p, lat_p, dist_p = PUBLISHED_ERRORS_1800_2050[body]
    lon_e, lat_e, dist_e = PUBLISHED_ERRORS_1800_2050["Earth"]
    planet = float(np.linalg.norm(planet_au)) * math.hypot(lon_p, lat_p) / ARCSEC_PER_RAD + dist_p * 1000.0 / AU_KM
    earth = float(np.linalg.norm(earth_au)) * math.hypot(lon_e, lat_e) / ARCSEC_PER_RAD + dist_e * 1000.0 / AU_KM
    tau_centuries = float(np.linalg.norm(geocentric_au)) / C_AU_PER_DAY / 36525.0
    earlier = np.array(_heliocentric_ecliptic_xyz_au(PLANET_ELEMENTS[body], t_centuries - tau_centuries))
    light_time = float(np.linalg.norm(planet_au - earlier))
    return planet + earth + BARYCENTRE_OFFSET_KM / AU_KM + light_time


def compute_solar_system_ephemerides(
    obs_utc_iso: str,
    lat_deg: float,
    lon_deg: float,
    bodies: tuple[str, ...] = ("Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"),
    observation_solution_id: str = "sol-ephemeris",
) -> list[EphemerisFinding]:
    """Predicted positions of the requested bodies at `obs_utc_iso`, with each one's error bounds for the moment."""
    jd = _iso_to_julian_date(obs_utc_iso)
    t_centuries = (jd - 2451545.0) / 36525.0

    earth_au = np.array(_heliocentric_ecliptic_xyz_au(PLANET_ELEMENTS["Earth"], t_centuries))
    common = "Predicted from the given time and place with a low-precision model; not measured in any image."

    findings: list[EphemerisFinding] = []
    for body_name in bodies:
        if body_name == "Moon":
            ra_deg, dec_deg, dist_au = _compute_moon_geocentric_icrs(t_centuries)
            obj_type = "moon"
            uncertainty_arcsec = MOON_UNCERTAINTY_ARCSEC
            bound_au = MOON_DISTANCE_UNCERTAINTY_KM / AU_KM
            method = MOON_METHOD
        else:
            if body_name not in PLANET_ELEMENTS or body_name == "Earth":
                raise KeyError(f"Unsupported Solar System body: {body_name}")
            planet_au = np.array(_heliocentric_ecliptic_xyz_au(PLANET_ELEMENTS[body_name], t_centuries))
            geocentric_au = planet_au - earth_au
            gx_eq, gy_eq, gz_eq = _ecliptic_to_equatorial_icrs(*geocentric_au)
            dist_au = math.sqrt(gx_eq * gx_eq + gy_eq * gy_eq + gz_eq * gz_eq)
            ra_deg, dec_deg = unit_vector_to_ra_dec((gx_eq, gy_eq, gz_eq))
            obj_type = PLANET_ELEMENTS[body_name].object_type
            bound_au = _planet_error_bound_au(body_name, planet_au, earth_au, geocentric_au, t_centuries)
            # Displacements all across the line of sight would turn the whole bound into direction
            uncertainty_arcsec = bound_au / dist_au * ARCSEC_PER_RAD
            method = PLANET_METHOD

        alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, obs_utc_iso)
        limitations: tuple[str, ...] = (common,)
        if body_name == "Moon":
            parallax_arcsec, shift_arcsec = moon_parallax_shift_arcsec(dist_au, alt_deg)
            limitations += (
                f"Geocentric: from this place the Moon appears about {shift_arcsec:,.0f} arcsec lower than this "
                f"position (its horizontal parallax, {parallax_arcsec:,.0f} arcsec, times the cosine of its altitude).",
            )
        dist_pc = max(1e-9, dist_au / AU_PER_PARSEC)
        ux, uy, uz = ra_dec_to_unit_vector(ra_deg, dec_deg)

        findings.append(
            EphemerisFinding(
                id=f"finding-predicted-{body_name.lower()}",
                observation_solution_id=observation_solution_id,
                name=body_name,
                object_type=obj_type,
                evidence_class="predicted",
                title=f"{body_name} (Predicted Ephemeris)",
                subtitle=f"Predicted {obj_type.capitalize()} · {dist_au:.3f} AU · Alt {alt_deg:+.1f}°",
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                alt_deg=alt_deg,
                az_deg=az_deg,
                distance_au=dist_au,
                distance_lower_au=max(1e-12, dist_au - bound_au),
                distance_upper_au=dist_au + bound_au,
                distance_pc=dist_pc,
                uncertainty_arcsec=uncertainty_arcsec,
                cartesian_parsec_icrs=(ux * dist_pc, uy * dist_pc, uz * dist_pc),
                obs_utc_iso=obs_utc_iso,
                method=method,
                limitations=limitations,
            )
        )
    return findings
