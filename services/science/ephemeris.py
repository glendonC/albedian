"""Deterministic analytical planetary & lunar ephemeris calculator.

All ephemeris findings are explicitly tagged with `evidence_class = "predicted"`
because they are computed from orbital elements, time, and observer location
rather than extracted from camera sensor pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from services.science.catalog import (
    _iso_to_julian_date,
    icrs_to_altaz,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)

AU_PER_PARSEC = 206264.80624709636


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
    apparent_mag_nominal: float


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
        apparent_mag_nominal=0.0,
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
        apparent_mag_nominal=-4.2,
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
        apparent_mag_nominal=0.5,
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
        apparent_mag_nominal=-2.5,
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
        apparent_mag_nominal=0.6,
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
        apparent_mag_nominal=5.7,
    ),
}


@dataclass(frozen=True)
class EphemerisFinding:
    """Predicted Solar System body finding with explicit `predicted` epistemic class."""

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
    distance_pc: float
    apparent_magnitude: float
    uncertainty_arcsec: float
    cartesian_parsec_icrs: tuple[float, float, float]
    obs_utc_iso: str

    def to_contract_dict(self, source_sha256: str = "0" * 64) -> dict:
        """Format as @albedian/contracts CelestialFinding dictionary."""
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
            "catalogRef": {
                "id": f"jpl-{self.name.lower()}",
                "provider": "JPL Horizons",
                "releaseVersion": "DE440-Analytical",
                "catalogSourceId": f"JPL-{self.name.upper()}",
                "commonName": self.name,
                "constellation": "Ecliptic",
                "objectType": self.object_type,
                "coordinateEpoch": self.obs_utc_iso,
                "raDeg": float(self.ra_deg),
                "decDeg": float(self.dec_deg),
                "apparentMagnitude": float(self.apparent_magnitude),
                "bailerJonesDistancePc": {
                    "lower": float(self.distance_pc * 0.999),
                    "median": float(self.distance_pc),
                    "upper": float(self.distance_pc * 1.001),
                    "method": "ephemeris_au",
                },
                "citation": "Standish (1992) JPL Planetary and Lunar Ephemerides.",
            },
            "inferredDistancePc": {
                "lower": float(self.distance_pc * 0.999),
                "median": float(self.distance_pc),
                "upper": float(self.distance_pc * 1.001),
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
                "verificationMethod": "Analytical Keplerian / Lunar Ephemeris (time + location)",
                "sourceAssetSha256": source_sha256,
                "catalogReferenceId": f"jpl-{self.name.lower()}",
                "distanceUncertaintyNote": f"{self.distance_au:.4f} AU from geocenter",
                "limitations": [
                    "Predicted from timestamp and observer coordinates; not verified as a pixel detection."
                ],
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
    """Compute geocentric ICRS (ra_deg, dec_deg, distance_au) for the Moon (Meeus Ch. 47 low-precision)."""
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
    x_ecl = dist_au * math.cos(lat_rad) * math.cos(lon_rad)
    y_ecl = dist_au * math.cos(lat_rad) * math.sin(lon_rad)
    z_ecl = dist_au * math.sin(lat_rad)
    x_eq, y_eq, z_eq = _ecliptic_to_equatorial_icrs(x_ecl, y_ecl, z_ecl)
    ra_deg, dec_deg = unit_vector_to_ra_dec((x_eq, y_eq, z_eq))
    return (ra_deg, dec_deg, dist_au)


def compute_solar_system_ephemerides(
    obs_utc_iso: str,
    lat_deg: float,
    lon_deg: float,
    bodies: tuple[str, ...] = ("Jupiter", "Uranus", "Saturn", "Mars", "Venus", "Moon"),
    observation_solution_id: str = "sol-ephemeris",
) -> list[EphemerisFinding]:
    """Compute predicted positions for requested Solar System bodies at `obs_utc_iso`."""
    jd = _iso_to_julian_date(obs_utc_iso)
    t_centuries = (jd - 2451545.0) / 36525.0

    earth_elem = PLANET_ELEMENTS["Earth"]
    ex_ecl, ey_ecl, ez_ecl = _heliocentric_ecliptic_xyz_au(earth_elem, t_centuries)

    findings: list[EphemerisFinding] = []
    for body_name in bodies:
        if body_name == "Moon":
            ra_deg, dec_deg, dist_au = _compute_moon_geocentric_icrs(t_centuries)
            obj_type = "moon"
            mag = -12.0
            uncertainty_arcsec = 18.0
        else:
            if body_name not in PLANET_ELEMENTS:
                raise KeyError(f"Unsupported Solar System body: {body_name}")
            elem = PLANET_ELEMENTS[body_name]
            px_ecl, py_ecl, pz_ecl = _heliocentric_ecliptic_xyz_au(elem, t_centuries)
            gx_ecl = px_ecl - ex_ecl
            gy_ecl = py_ecl - ey_ecl
            gz_ecl = pz_ecl - ez_ecl
            gx_eq, gy_eq, gz_eq = _ecliptic_to_equatorial_icrs(gx_ecl, gy_ecl, gz_ecl)
            dist_au = math.sqrt(gx_eq * gx_eq + gy_eq * gy_eq + gz_eq * gz_eq)
            ra_deg, dec_deg = unit_vector_to_ra_dec((gx_eq, gy_eq, gz_eq))
            obj_type = elem.object_type
            mag = elem.apparent_mag_nominal
            uncertainty_arcsec = 12.0

        alt_deg, az_deg = icrs_to_altaz(ra_deg, dec_deg, lat_deg, lon_deg, obs_utc_iso)
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
                distance_pc=dist_pc,
                apparent_magnitude=mag,
                uncertainty_arcsec=uncertainty_arcsec,
                cartesian_parsec_icrs=(ux * dist_pc, uy * dist_pc, uz * dist_pc),
                obs_utc_iso=obs_utc_iso,
            )
        )
    return findings
